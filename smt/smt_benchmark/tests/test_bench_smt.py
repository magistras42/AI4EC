#!/usr/bin/env python3
"""
Tests for bench_smt.py — every assumption the benchmark relies on, asserted.

Two tiers:
  * UNIT tests use a FAKE easycrypt binary (a shell script that records its
    argv) — deterministic, no EasyCrypt needed.
  * INTEGRATION tests use the real easycrypt binary — skipped (with a notice)
    when `easycrypt` is not on PATH. Run `source ~/ec-env.sh` first.

Run standalone (no pytest needed):   python3 tests/test_bench_smt.py
Or with pytest:                      pytest tests/test_bench_smt.py -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_smt  # noqa: E402
from bench_smt import (  # noqa: E402
    BASELINE, OWN_PRAGMA_RE, SMT_RE, CsvStream, collect_files, cmd_retime,
    compute_stats, deps_ok_map, file_meta, local_requires, measured_run,
    run_easycrypt, validate_args, warmup_run,
)

HAVE_EC = shutil.which("easycrypt") is not None


# ---------------------------------------------------------------- helpers

def make_fake_ec(tmpdir: Path, body: str) -> Path:
    """Create a fake `easycrypt` executable whose behavior we control.
    It always appends its argv to $ECBENCH_LOG (one line per invocation)."""
    fake = tmpdir / "fake_ec"
    fake.write_text("#!/bin/bash\n"
                    'echo "$@" >> "$ECBENCH_LOG"\n' + body)
    fake.chmod(0o755)
    return fake


def with_fake_ec(body, fn):
    """Run fn(fake_binary_path, log_path) with a scoped ECBENCH_LOG."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        fake = make_fake_ec(tmpdir, body)
        log = tmpdir / "argv.log"
        log.touch()
        old = os.environ.get("ECBENCH_LOG")
        os.environ["ECBENCH_LOG"] = str(log)
        try:
            return fn(fake, log, tmpdir)
        finally:
            if old is None:
                os.environ.pop("ECBENCH_LOG", None)
            else:
                os.environ["ECBENCH_LOG"] = old


# ---------------------------------------------------------------- unit: argv

def test_measured_run_argv_solver():
    """measured_run must pass: compile, -no-eco, -p SOLVER, -timeout N,
    -I <orig parent>, and a target path living in a TEMP dir (not the
    original dir) — this is the mechanism that defeats the .eco cache."""
    def check(fake, log, tmpdir):
        target = tmpdir / "corpus" / "file_a.ec"
        target.parent.mkdir()
        target.write_text("(* dummy *)\n")
        ok, _, _ = measured_run(str(fake), target, "Z3", 10, 60, [])
        assert ok, "fake binary exits 0, run must be ok"
        argv = log.read_text().strip().splitlines()
        assert len(argv) == 1, f"exactly one invocation expected: {argv}"
        args = argv[0].split()
        assert args[0] == "compile", args
        assert "-no-eco" in args, "measured runs must not WRITE .eco"
        assert args[args.index("-p") + 1] == "Z3", args
        assert args[args.index("-timeout") + 1] == "10", args
        assert args[args.index("-I") + 1] == str(target.parent), \
            "deps must resolve via -I to the ORIGINAL (warmed) dir"
        run_path = Path(args[-1])
        assert run_path.name == "file_a.ec", "file name must be preserved"
        assert run_path.parent != target.parent, \
            "target must run from a TEMP copy, never in-tree (eco isolation)"
        assert not run_path.exists(), "temp dir must be cleaned up after run"
    with_fake_ec("exit 0\n", check)


def test_measured_run_argv_baseline():
    """Baseline config must NOT restrict provers (no -p flag)."""
    def check(fake, log, tmpdir):
        target = tmpdir / "b.ec"
        target.write_text("")
        measured_run(str(fake), target, None, 10, 60, [])
        args = log.read_text().split()
        assert "-p" not in args, "baseline must use the default portfolio"
        assert "-timeout" in args
    with_fake_ec("exit 0\n", check)


def test_smt_timeout_zero_omitted():
    """--smt-timeout 0 must omit the -timeout flag entirely."""
    def check(fake, log, tmpdir):
        target = tmpdir / "c.ec"
        target.write_text("")
        measured_run(str(fake), target, "Z3", 0, 60, [])
        assert "-timeout" not in log.read_text().split()
    with_fake_ec("exit 0\n", check)


def test_extra_idirs_passed():
    def check(fake, log, tmpdir):
        target = tmpdir / "d.ec"
        target.write_text("")
        measured_run(str(fake), target, "Z3", 10, 60, ["/extra/inc"])
        args = log.read_text().split()
        idxs = [i for i, a in enumerate(args) if a == "-I"]
        got = {args[i + 1] for i in idxs}
        assert str(tmpdir) in got and "/extra/inc" in got, args
    with_fake_ec("exit 0\n", check)


def test_warmup_run_writes_eco_in_tree():
    """Warmup must run IN-TREE (real path) with .eco writing ENABLED."""
    def check(fake, log, tmpdir):
        target = tmpdir / "w.ec"
        target.write_text("")
        warmup_run(str(fake), target, 10, 60)
        args = log.read_text().split()
        assert "-no-eco" not in args, "warmup must WRITE .eco (dep caches)"
        assert args[-1] == str(target), "warmup must run on the ORIGINAL path"
        assert "-p" not in args, "warmup uses the default portfolio"
    with_fake_ec("exit 0\n", check)


# ---------------------------------------------------------------- unit: exit

def test_exit_code_mapping_and_error_tail():
    def check(fake, log, tmpdir):
        target = tmpdir / "e.ec"
        target.write_text("")
        ok, _, tail = measured_run(str(fake), target, "Z3", 10, 60, [])
        assert not ok
        assert "boom goal unproved" in tail, f"stderr must be captured: {tail}"
    with_fake_ec('echo "boom goal unproved" >&2\nexit 1\n', check)


def test_hard_timeout_kills_process_group():
    """On hard timeout the WHOLE process group (easycrypt + solver children)
    must be killed — orphaned z3 processes on a shared server are not ok."""
    def check(fake, log, tmpdir):
        pidfile = tmpdir / "child.pid"
        # fake spawns a long-lived child, writes its pid, then sleeps
        fake.write_text(textwrap.dedent(f"""\
            #!/bin/bash
            echo "$@" >> "$ECBENCH_LOG"
            sleep 300 &
            echo $! > {pidfile}
            wait
        """))
        fake.chmod(0o755)
        target = tmpdir / "t.ec"
        target.write_text("")
        t0 = time.monotonic()
        ok, dt, tail = measured_run(str(fake), target, "Z3", 10,
                                    2, [])  # hard timeout: 2s
        elapsed = time.monotonic() - t0
        assert not ok and "HARD_TIMEOUT" in tail
        assert elapsed < 10, f"must return promptly, took {elapsed:.1f}s"
        child = int(pidfile.read_text().strip())
        time.sleep(0.2)
        try:
            os.kill(child, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive, f"child pid {child} survived the group kill"
    with_fake_ec("", check)


# ---------------------------------------------------------------- unit: meta

def test_file_meta_pragma_and_smt_count():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.ec"
        f.write_text('prover [""].\n'
                     "lemma a : true by smt.\n"
                     "lemma b : true by smt. (* smt *)\n")
        meta = file_meta(f)
        assert meta["has_own_pragma"] == 1, "prover pragma must be flagged"
        assert meta["n_smt_tokens"] == 3, "raw \\bsmt\\b count (comments too)"

        g = Path(td) / "n.ec"
        g.write_text("lemma c : true by auto.\n"
                     "(* the prover is great, smtish, bsmtb *)\n")
        meta = file_meta(g)
        assert meta["has_own_pragma"] == 0, \
            "mid-line 'prover' must NOT be flagged (line-anchored regex)"
        assert meta["n_smt_tokens"] == 0, "smtish/bsmtb must not match"


def test_own_pragma_timeout_directive():
    assert OWN_PRAGMA_RE.search("timeout 5.\n")
    assert OWN_PRAGMA_RE.search('  prover ["Z3"].\n')
    assert not OWN_PRAGMA_RE.search("(* timeout is important *) lemma x.\n")


def test_collect_files_sorted_and_limit():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name in ["z.ec", "a.ec", "m/k.ec"]:
            p = root / name
            p.parent.mkdir(exist_ok=True)
            p.write_text("")
        files = collect_files(root, 0)
        assert [f.name for f in files] == ["a.ec", "k.ec", "z.ec"], \
            "deterministic sorted order"
        assert len(collect_files(root, 2)) == 2, "--limit must truncate"


# ---------------------------------------------------------------- unit: stats

def synthetic_rows():
    """fileA: only Z3 solves (baseline misses it too).
       fileB: everyone solves; Z3 is fastest (1s vs baseline 10s).
       fileC: nobody solves.
       fileD: Z3+CVC5 solve, Alt-Ergo fails; baseline solves."""
    S = ["Alt-Ergo", "Z3", "CVC5"]
    ok = {
        "fileA": {BASELINE: 0, "Alt-Ergo": 0, "Z3": 1, "CVC5": 0},
        "fileB": {BASELINE: 1, "Alt-Ergo": 1, "Z3": 1, "CVC5": 1},
        "fileC": {BASELINE: 0, "Alt-Ergo": 0, "Z3": 0, "CVC5": 0},
        "fileD": {BASELINE: 1, "Alt-Ergo": 0, "Z3": 1, "CVC5": 1},
    }
    secs = {
        ("fileB", BASELINE): 10.0, ("fileB", "Z3"): 1.0,
        ("fileB", "Alt-Ergo"): 5.0, ("fileB", "CVC5"): 2.0,
        ("fileD", BASELINE): 4.0, ("fileD", "Z3"): 3.0,
        ("fileD", "CVC5"): 2.0,
    }
    rows = []
    for f, cfg in ok.items():
        for s, o in cfg.items():
            rows.append({"file": f, "solver": s, "ok": o,
                         "seconds": secs.get((f, s), 9.9)})
    return rows, S


def test_compute_stats_h1_h2():
    rows, solvers = synthetic_rows()
    st = compute_stats(rows, solvers)
    assert st["total"] == 4
    assert st["solved"][BASELINE] == ["fileB", "fileD"]
    assert st["solved"]["Z3"] == ["fileA", "fileB", "fileD"]
    # H1: A (only Z3) and D (Alt-Ergo fails) disagree; B unanimous; C n/a
    assert st["union"] == ["fileA", "fileB", "fileD"]
    assert st["disagree"] == ["fileA", "fileD"]
    assert st["exactly_one"] == {"fileA": "Z3"}
    # H2: on files baseline AND vbs solve (B, D):
    #   baseline 10+4=14, VBS min(5,1,2)+min(3,2)=1+2=3 -> 78.57% savings
    assert st["both_count"] == 2
    assert abs(st["baseline_total_time"] - 14.0) < 1e-9
    assert abs(st["vbs_total_time"] - 3.0) < 1e-9
    assert abs(st["vbs_savings_pct"] - 100 * 11 / 14) < 1e-6
    # VBS solves fileA which baseline misses
    assert st["base_missed"] == {"fileA": ["Z3"]}


def test_compute_stats_empty_baseline_overlap():
    """No file solved by both baseline and VBS -> savings undefined, no crash."""
    rows = [
        {"file": "f", "solver": BASELINE, "ok": 0, "seconds": 1.0},
        {"file": "f", "solver": "Z3", "ok": 1, "seconds": 1.0},
    ]
    st = compute_stats(rows, ["Z3"])
    assert st["vbs_savings_pct"] is None
    assert st["base_missed"] == {"f": ["Z3"]}


# ------------------------------------------------------- unit: new behaviors

def test_compute_stats_excludes_dep_contaminated_files():
    """Files with deps_warmup_ok=0 must not pollute H1/H2 statistics."""
    rows, solvers = synthetic_rows()
    for r in rows:
        if r["file"] == "fileA":          # fileA becomes contaminated
            r["deps_warmup_ok"] = 0
    st = compute_stats(rows, solvers)
    assert st["excluded_dep_contaminated"] == ["fileA"]
    assert st["total"] == 3, "fileA must vanish from the denominator"
    assert st["exactly_one"] == {}, "fileA was the only exactly-one file"
    assert st["disagree"] == ["fileD"]
    assert st["base_missed"] == {}


def test_local_requires_transitive_and_deps_ok():
    """A -> B -> C chain: A's flag must reflect C's warmup failure.
    Case-insensitive first letter (file dep.ec provides theory Dep)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        a = td / "A.ec"
        b = td / "B.ec"
        c = td / "dep.ec"
        other = td / "Other.ec"
        a.write_text("require B.\nlemma x : true by auto.\n")
        b.write_text("require import Dep.\n(* require Other. is a comment *)\n")
        c.write_text("lemma y : true by auto.\n")
        other.write_text("")
        files = [a, b, c, other]
        clo = local_requires(files)
        assert clo[a] == {b, c}, f"transitive closure broken: {clo[a]}"
        assert clo[b] == {c}
        assert clo[other] == set(), "commented require must be ignored"
        deps = deps_ok_map(files, {a: 1, b: 1, c: 0, other: 1})
        assert deps[a] == 0 and deps[b] == 0, \
            "warmup-failed dep must contaminate all transitive dependents"
        assert deps[c] == 1 and deps[other] == 1, \
            "a file's OWN warmup failure is not dep contamination"


def test_validate_args_out_collision_and_defaults():
    import argparse
    ap = argparse.ArgumentParser()

    def ns(**kw):
        d = {"out": None, "retime": None}
        d.update(kw)
        return argparse.Namespace(**d)

    assert validate_args(ns(), ap).out == "results/sweep.csv"
    assert validate_args(ns(retime="s.csv"), ap).out == "results/timing.csv"
    try:
        validate_args(ns(retime="results/sweep.csv",
                         out="results/sweep.csv"), ap)
        raise AssertionError("collision --out == --retime must be refused")
    except SystemExit:
        pass  # argparse .error() exits — expected


def test_csv_stream_rows_durable_immediately():
    """Every row must be on disk the moment write() returns (crash safety)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        st = CsvStream(path, ["a", "b"])
        st.write({"a": 1, "b": 2})
        on_disk = path.read_text()   # NOT closed yet — must still be there
        assert "a,b" in on_disk and "1,2" in on_disk, \
            f"row not flushed before close: {on_disk!r}"
        st.close()


def test_reap_kills_registered_children():
    """_reap must SIGKILL every live registered process group; the registry
    must drain once runs finish (no leak)."""
    import threading as th
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        slow = tmpdir / "slow_ec"
        slow.write_text("#!/bin/bash\nsleep 300\n")
        slow.chmod(0o755)
        target = tmpdir / "x.ec"
        target.write_text("")
        result = {}

        def run():
            result["r"] = run_easycrypt(str(slow), target, [], None, 0, 60)

        t = th.Thread(target=run)
        t.start()
        deadline = time.monotonic() + 10
        while not bench_smt._PGIDS and time.monotonic() < deadline:
            time.sleep(0.05)
        assert bench_smt._PGIDS, "child pgid was never registered"
        bench_smt._reap(None)          # signum=None: kill but don't exit
        t.join(timeout=15)
        assert not t.is_alive(), "worker still blocked after group kill"
        assert not bench_smt._PGIDS, "registry must drain after run ends"
        ok, _, _ = result["r"]
        assert not ok, "killed run must be reported as failure"


def test_retime_full_grid_and_failure_confirmation():
    """Retime must re-run EVERY sweep pair: successes --reps times, failures
    once; output rows carry sweep_ok and rebuild the full grid so H1 stats
    are not structurally degenerate."""
    import argparse
    import csv as csvmod

    def check(fake, log, tmpdir):
        corpus = tmpdir / "corpus"
        corpus.mkdir()
        (corpus / "f1.ec").write_text("")
        (corpus / "f2.ec").write_text("")
        sweep = tmpdir / "sweep.csv"
        with open(sweep, "w", newline="") as fh:
            w = csvmod.DictWriter(fh, fieldnames=["file", "solver", "ok",
                                                  "seconds"])
            w.writeheader()
            for f in ["f1.ec", "f2.ec"]:
                for s, ok in [(BASELINE, 1), ("Z3", 1), ("CVC5", 0)]:
                    w.writerow({"file": f, "solver": s, "ok": ok,
                                "seconds": 1.0})
        out = tmpdir / "timing.csv"
        args = argparse.Namespace(
            ec=str(fake), solvers=["Z3", "CVC5"], smt_timeout=10,
            hard_timeout=60, idir=[], jobs=2, limit=0,
            out=str(out), retime=str(sweep), reps=3, no_preflight=True)
        files = [corpus / "f1.ec", corpus / "f2.ec"]
        cmd_retime(args, corpus, files)

        rows = list(csvmod.DictReader(open(out)))
        # full grid: 2 files x 3 configs
        assert len(rows) == 6, f"expected full grid, got {len(rows)} rows"
        by = {(r["file"], r["solver"]): r for r in rows}
        for f in ["f1.ec", "f2.ec"]:
            ok_pair = by[(f, "Z3")]
            fail_pair = by[(f, "CVC5")]
            assert ok_pair["sweep_ok"] == "1"
            assert len(ok_pair["reps"].split(";")) == 3, \
                "sweep successes must get --reps repetitions"
            assert fail_pair["sweep_ok"] == "0"
            assert len(fail_pair["reps"].split(";")) == 1, \
                "sweep failures must get exactly 1 confirmation run"
            # fake binary exits 0 -> sweep failure FLIPS to ok serially
            assert fail_pair["ok"] == "1", \
                "serial confirmation result must override sweep verdict"
    with_fake_ec("exit 0\n", check)


# ---------------------------------------------------------- integration (ec)

def integration_corpus(td: Path):
    """Mini corpus: dep.ec (library), uses_dep.ec (requires dep),
    good.ec (standalone true lemma), bad.ec (false lemma)."""
    (td / "Dep.ec").write_text(
        "require import AllCore.\n"
        "lemma dep_fact (x : int) : x + 0 = x by smt.\n")
    (td / "uses_dep.ec").write_text(
        "require import AllCore.\n"
        "require Dep.\n"
        "lemma use (x : int) : x + 0 = x by apply Dep.dep_fact.\n")
    (td / "good.ec").write_text(
        "require import AllCore.\n"
        "lemma g (x y : int) : x + y = y + x by smt.\n")
    (td / "bad.ec").write_text(
        "require import AllCore.\n"
        "lemma b (x : int) : x < x by smt.\n")


def test_integration_canary_all_solvers():
    """True lemma passes under every solver; false lemma rejected."""
    if not HAVE_EC:
        return "SKIP (easycrypt not on PATH)"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        integration_corpus(td)
        for solver in [None, "Z3", "CVC5", "Alt-Ergo"]:
            ok, dt, tail = measured_run("easycrypt", td / "good.ec",
                                        solver, 10, 120, [])
            assert ok, f"good.ec failed under {solver}: {tail[-300:]}"
        ok, _, _ = measured_run("easycrypt", td / "bad.ec", None, 10, 120, [])
        assert not ok, "bad.ec (false lemma) must be rejected"


def test_integration_dep_require_via_idir():
    """After in-tree warmup, a file requiring a sibling must verify from the
    isolated temp copy (deps resolved via -I to the warmed original dir)."""
    if not HAVE_EC:
        return "SKIP (easycrypt not on PATH)"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        integration_corpus(td)
        ok, _, tail = warmup_run("easycrypt", td / "Dep.ec", 10, 120)
        assert ok, f"warmup of Dep.ec failed: {tail[-300:]}"
        assert (td / "Dep.eco").exists(), \
            "warmup must produce Dep.eco (the dep cache)"
        ok, _, tail = measured_run("easycrypt", td / "uses_dep.ec",
                                   "Z3", 10, 120, [])
        assert ok, f"uses_dep.ec failed from temp copy: {tail[-300:]}"


def test_integration_eco_isolation():
    """A valid in-tree .eco must NOT let measured runs skip verification:
    in-tree reruns are cache hits (fast), measured_run must stay honest.
    Detection: make the cached file FALSE after warmup — a cache hit would
    return ok, real verification must fail."""
    if not HAVE_EC:
        return "SKIP (easycrypt not on PATH)"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        f = td / "flip.ec"
        f.write_text("require import AllCore.\n"
                     "lemma flip (x y : int) : x + y = y + x by smt.\n")
        ok, _, _ = warmup_run("easycrypt", f, 10, 120)
        assert ok and (td / "flip.eco").exists()
        # flip the file to a FALSE lemma; keep the (now stale) .eco around
        f.write_text("require import AllCore.\n"
                     "lemma flip (x : int) : x < x by smt.\n")
        ok, _, _ = measured_run("easycrypt", f, None, 10, 120, [])
        assert not ok, ("measured_run returned ok for a FALSE lemma — "
                        ".eco isolation is broken, all timings would be fake")


def test_integration_preflight_passes():
    """preflight() itself must pass in the real environment — it guards the
    full run, so the suite must exercise it (provers line is on stderr)."""
    if not HAVE_EC:
        return "SKIP (easycrypt not on PATH)"
    import argparse
    args = argparse.Namespace(ec="easycrypt",
                              solvers=["Alt-Ergo", "Z3", "CVC5"],
                              smt_timeout=10)
    bench_smt.preflight(args)  # raises AssertionError on any violation


def test_integration_measured_run_leaves_tree_untouched():
    """measured_run must not create or modify .eco in the corpus tree."""
    if not HAVE_EC:
        return "SKIP (easycrypt not on PATH)"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        integration_corpus(td)
        before = set(td.rglob("*.eco"))
        ok, _, _ = measured_run("easycrypt", td / "good.ec", "Z3", 10, 120, [])
        assert ok
        after = set(td.rglob("*.eco"))
        assert before == after, \
            f"measured_run touched the tree: {after - before}"


# ---------------------------------------------------------------- runner

def main():
    tests = [(n, fn) for n, fn in sorted(globals().items())
             if n.startswith("test_") and callable(fn)]
    failed, skipped = [], []
    for name, fn in tests:
        try:
            result = fn()
            if isinstance(result, str) and result.startswith("SKIP"):
                skipped.append((name, result))
                print(f"  SKIP {name}  {result}")
            else:
                print(f"  ok   {name}")
        except AssertionError as e:
            failed.append((name, e))
            print(f"  FAIL {name}\n       {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  ERROR {name}\n       {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed) - len(skipped)} passed, "
          f"{len(failed)} failed, {len(skipped)} skipped")
    if not HAVE_EC:
        print("note: integration tests skipped — run `source ~/ec-env.sh`")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

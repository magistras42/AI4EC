#!/usr/bin/env python3
"""
bench_smt.py — Per-solver benchmark for EasyCrypt proof scripts.

For every .ec file under a directory, checks the file once per SMT solver
(via EasyCrypt CLI flags: `easycrypt compile -p <Solver> -timeout <N>`) and
once with EasyCrypt's default solver portfolio (baseline). Records
success/failure and wall-clock time, writes a CSV, and prints a summary:

  * per-solver solve rate and mean time
  * files where solvers DISAGREE (some succeed, some fail)  <- H1 evidence
  * virtual best solver (VBS) vs. baseline                  <- H2: upper bound
                                                               for LLM-based
                                                               selection (RQ3)

Design notes (measurement validity):
  * EasyCrypt caches verification results in `.eco` files and SKIPS re-checking
    when a valid .eco exists (`-no-eco` prevents writing but NOT reading).
    Protocol: a warmup pass compiles the whole corpus in-tree (building .eco
    for all dependencies), then every measured run copies the target file
    alone into a fresh temp dir and runs with `-I <original dir>` — deps hit
    the warmed cache, the target itself is always re-verified, and parallel
    runs never race on shared .eco files.
  * Warmup-failed dependencies would be re-verified inline in every dependent
    measured run (cost/failure misattributed to the target), so each file's
    transitive local requires are checked against warmup results and flagged
    in a `deps_warmup_ok` CSV column; flagged files are excluded from the
    headline H1/H2 statistics.
  * Phase 1 (parallel sweep) success/fail can be perturbed by CPU contention;
    phase 2 (--retime) therefore re-runs EVERY pair serially — successes get
    --reps repetitions (median), sweep failures get one serial confirmation —
    so final H1/H2 numbers come from uncontended serial runs.
  * Results stream to the CSV as they complete (a crash or Ctrl+C keeps all
    finished rows), and SIGINT/SIGTERM/SIGHUP kill every live easycrypt
    process group before exiting (no orphaned solvers on a shared server).
  * Granularity is per-FILE, not per-smt-call (honest proxy for day 1).

Usage:
  # phase 1: parallel success/fail sweep
  python3 bench_smt.py corpus --jobs 8 --smt-timeout 10 --hard-timeout 300 \
      --out results/sweep.csv
  # phase 2: serial precise re-timing of ALL pairs from phase 1
  python3 bench_smt.py corpus --retime results/sweep.csv --reps 3 \
      --out results/timing.csv
"""

import argparse
import csv
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASELINE = "default(all)"
SMT_RE = re.compile(r"\bsmt\b")
OWN_PRAGMA_RE = re.compile(r"^\s*(prover\b|timeout\b)", re.MULTILINE)
REQUIRE_RE = re.compile(
    r"^\s*(?:from\s+\w+\s+)?require(?:\s+(?:import|export))?\s+([^.]*)\.",
    re.MULTILINE)

SWEEP_FIELDS = ["file", "solver", "ok", "seconds", "n_smt_tokens",
                "has_own_pragma", "warmup_ok", "deps_warmup_ok", "error_tail"]
RETIME_FIELDS = ["file", "solver", "ok", "seconds", "reps", "flaky",
                 "sweep_ok", "deps_warmup_ok"]

# ---- live process-group registry: on SIGINT/SIGTERM/SIGHUP we must kill
# every detached easycrypt session (each spawns SMT solver children) before
# dying, or they would keep running unbounded on the shared server.
_PGIDS = set()
_PGLOCK = threading.Lock()


def _reap(signum=None, _frame=None):
    with _PGLOCK:
        pgids = list(_PGIDS)
    for pg in pgids:
        try:
            os.killpg(pg, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if signum is not None:
        # skip ThreadPoolExecutor's shutdown(wait=True) drain — exiting NOW
        # is the point (leftover temp dirs are acceptable on abort)
        os._exit(128 + signum)


def install_signal_handlers():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _reap)


def run_easycrypt(ec_bin, path, idirs, solver, smt_timeout, hard_timeout,
                  no_eco=True):
    """Run one easycrypt compile; returns (ok, seconds, error_tail)."""
    cmd = [ec_bin, "compile"]
    if no_eco:
        cmd.append("-no-eco")
    if solver is not None:
        cmd += ["-p", solver]
    if smt_timeout > 0:
        cmd += ["-timeout", str(smt_timeout)]
    for d in idirs:
        cmd += ["-I", str(d)]
    cmd.append(str(path))

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                errors="replace", start_new_session=True)
    except FileNotFoundError:
        print(f"error: EasyCrypt binary not found: '{ec_bin}'",
              file=sys.stderr)
        sys.exit(1)
    with _PGLOCK:
        _PGIDS.add(proc.pid)  # session leader => pgid == pid
    try:
        try:
            out, err = proc.communicate(timeout=hard_timeout)
            dt = time.monotonic() - t0
            ok = proc.returncode == 0
            tail = "" if ok else (out + err).strip()[-1500:]
            return ok, dt, tail
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.communicate()
            return (False, float(hard_timeout),
                    "HARD_TIMEOUT (process group killed)")
    finally:
        with _PGLOCK:
            _PGIDS.discard(proc.pid)


def measured_run(ec_bin, orig_file: Path, solver, smt_timeout, hard_timeout,
                 extra_idirs):
    """Isolated run: copy the target file into a fresh temp dir so its own
    .eco can never be read/written; deps resolve via -I to the warmed tree."""
    with tempfile.TemporaryDirectory(prefix="ecbench_") as td:
        tmp = Path(td) / orig_file.name
        shutil.copyfile(orig_file, tmp)
        idirs = [orig_file.parent] + list(extra_idirs)
        return run_easycrypt(ec_bin, tmp, idirs, solver, smt_timeout,
                             hard_timeout)


def warmup_run(ec_bin, orig_file: Path, smt_timeout, hard_timeout):
    """In-tree baseline compile, .eco writing ENABLED (builds dep caches)."""
    return run_easycrypt(ec_bin, orig_file, [orig_file.parent], None,
                         smt_timeout, hard_timeout, no_eco=False)


def warm_corpus(args, root, files):
    """Build .eco dep caches in-tree. Sequential per directory (files in one
    dir may require each other), directories in parallel.
    Returns {file: ok} — used both as data and for dependency flags."""
    print(f"warmup: baseline compile of {len(files)} file(s) in-tree "
          f"(builds .eco dep caches)")
    by_dir = {}
    for f in files:
        by_dir.setdefault(f.parent, []).append(f)

    warmup_ok = {}

    def warm_dir(dir_files):
        for f in dir_files:
            ok, dt, _ = warmup_run(args.ec, f, args.smt_timeout,
                                   args.hard_timeout)
            warmup_ok[f] = int(ok)
            print(f"  [warmup] {f.relative_to(root)!s:<60} "
                  f"{'OK  ' if ok else 'FAIL'} {dt:7.1f}s")

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(warm_dir, by_dir.values()))
    n_fail = sum(1 for v in warmup_ok.values() if not v)
    print(f"warmup done ({n_fail} baseline failures — kept, they are data)\n")
    return warmup_ok


# ---------------- local dependency analysis ----------------

def local_requires(files):
    """Map each file to the set of SIBLING corpus files it requires,
    directly or transitively (same-directory only; stdlib names that match
    no sibling are ignored). Theory name == filename stem (EasyCrypt allows
    the first letter's case to differ)."""
    by_dir = {}
    for f in files:
        by_dir.setdefault(f.parent, {})[f.stem] = f

    direct = {}
    for f in files:
        sibs = by_dir[f.parent]
        deps = set()
        src = f.read_text(encoding="utf-8", errors="replace")
        src = re.sub(r"\(\*.*?\*\)", " ", src, flags=re.DOTALL)
        for m in REQUIRE_RE.finditer(src):
            for tok in m.group(1).split():
                if tok in ("import", "export", "as"):
                    continue
                for cand in (tok, tok[:1].lower() + tok[1:],
                             tok[:1].upper() + tok[1:]):
                    if cand in sibs and sibs[cand] != f:
                        deps.add(sibs[cand])
                        break
        direct[f] = deps

    closure = {}

    def close(f, seen):
        if f in closure:
            return closure[f]
        if f in seen:            # require cycle — treat as no extra deps
            return set()
        seen.add(f)
        acc = set(direct[f])
        for d in direct[f]:
            acc |= close(d, seen)
        closure[f] = acc
        return acc

    for f in files:
        close(f, set())
    return closure


def deps_ok_map(files, warmup_ok):
    """deps_warmup_ok flag per file: 1 iff every transitive local dep passed
    warmup (its .eco exists), so measured runs never re-verify dep proofs."""
    closure = local_requires(files)
    return {f: int(all(warmup_ok.get(d, 0) for d in closure[f]))
            for f in files}


# ---------------- CSV streaming ----------------

class CsvStream:
    """Header-first streaming writer: every completed row is durable
    immediately, so a crash or Ctrl+C never loses finished work."""

    def __init__(self, path, fields):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fh = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.fh, fieldnames=fields)
        self.w.writeheader()
        self.fh.flush()
        self.lock = threading.Lock()

    def write(self, row):
        with self.lock:
            self.w.writerow(row)
            self.fh.flush()

    def close(self):
        self.fh.close()
        print(f"\nwrote {self.path}")


# ---------------- sweep (phase 1) ----------------

def cmd_sweep(args, root, files):
    solvers = args.solvers
    meta = {f: file_meta(f) for f in files}
    warmup_ok = warm_corpus(args, root, files)
    deps_ok = deps_ok_map(files, warmup_ok)
    n_contam = sum(1 for v in deps_ok.values() if not v)
    if n_contam:
        print(f"note: {n_contam} file(s) have warmup-failed local deps — "
              f"flagged deps_warmup_ok=0, excluded from headline stats\n")

    configs = [(BASELINE, None)] + [(s, s) for s in solvers]
    jobs = [(f, label, solver) for f in files for label, solver in configs]
    print(f"sweep: {len(files)} file(s) x {len(configs)} config(s) "
          f"= {len(jobs)} runs, jobs={args.jobs}")

    out = CsvStream(args.out, SWEEP_FIELDS)

    def one(job):
        f, label, solver = job
        try:
            ok, dt, tail = measured_run(args.ec, f, solver, args.smt_timeout,
                                        args.hard_timeout, args.idir)
        except Exception as e:  # one bad job must not abort the sweep
            ok, dt, tail = False, 0.0, f"HARNESS_ERROR: {e!r}"
        print(f"  {str(f.relative_to(root)):<60} {label:<14} "
              f"{'OK  ' if ok else 'FAIL'} {dt:7.1f}s")
        row = {
            "file": str(f.relative_to(root)),
            "solver": label,
            "ok": int(ok),
            "seconds": round(dt, 3),
            **meta[f],
            "warmup_ok": warmup_ok[f],
            "deps_warmup_ok": deps_ok[f],
            "error_tail": tail.replace("\n", " | ")[:500],
        }
        out.write(row)
        return row

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        rows = list(ex.map(one, jobs))
    out.close()

    print("\nNOTE: sweep timings ran under parallel load — treat as noisy; "
          "final H1/H2 numbers come from the serial --retime phase.")
    summarize(rows, solvers)


# ---------------- retime (phase 2) ----------------

def cmd_retime(args, root, files):
    with open(args.retime, newline="", encoding="utf-8") as fh:
        sweep = list(csv.DictReader(fh))
    if not sweep:
        print(f"error: {args.retime} is empty", file=sys.stderr)
        sys.exit(1)

    # solver set comes from the sweep (the ground-truth grid), not from
    # whatever happens to succeed here
    solvers = sorted({r["solver"] for r in sweep if r["solver"] != BASELINE})

    # retime re-runs EVERY pair serially: successes get --reps repetitions
    # (median), sweep failures get one confirmation run — contention flips
    # from the parallel sweep are caught instead of frozen into the data.
    pairs = sorted((r["file"], r["solver"], r["ok"] == "1") for r in sweep)
    n_ok = sum(1 for _, _, o in pairs if o)
    print(f"retime: {len(pairs)} pair(s) from sweep ({n_ok} ok x {args.reps} "
          f"reps, {len(pairs) - n_ok} failed x 1 confirmation), serial")

    # rebuild dep caches — retime must not depend on leftovers of a previous
    # invocation (and measures under the same cache conditions as the sweep)
    warmup_ok = warm_corpus(args, root, files)
    deps_ok = deps_ok_map(files, warmup_ok)

    out = CsvStream(args.out, RETIME_FIELDS)
    rows = []
    for rel, label, sweep_ok in pairs:
        f = root / rel
        solver = None if label == BASELINE else label
        reps = args.reps if sweep_ok else 1
        times, oks = [], []
        for _ in range(reps):
            ok, dt, _ = measured_run(args.ec, f, solver, args.smt_timeout,
                                     args.hard_timeout, args.idir)
            oks.append(ok)
            times.append(dt)
        med = statistics.median(times)
        stable = all(oks)
        row = {
            "file": rel,
            "solver": label,
            "ok": int(stable),
            "seconds": round(med, 3),
            "reps": ";".join(f"{t:.3f}" for t in times),
            "flaky": int(any(oks) and not stable),
            "sweep_ok": int(sweep_ok),
            "deps_warmup_ok": deps_ok.get(f, 0),
        }
        out.write(row)
        rows.append(row)
        flip = "" if stable == sweep_ok else "  <- FLIPPED vs sweep"
        print(f"  {rel:<60} {label:<14} "
              f"{'OK  ' if stable else 'FAIL'} median {med:7.2f}s{flip}")
    out.close()
    summarize(rows, solvers)


# ---------------- summary ----------------

def file_meta(f: Path):
    src = f.read_text(encoding="utf-8", errors="replace")
    return {
        "n_smt_tokens": len(SMT_RE.findall(src)),
        "has_own_pragma": int(bool(OWN_PRAGMA_RE.search(src))),
    }


def compute_stats(rows, solvers):
    """Pure computation over result rows — returns a stats dict (testable).
    Files whose local deps failed warmup (deps_warmup_ok=0) are excluded:
    their measurements include dependency re-verification, so success/time
    would be misattributed."""
    contaminated = {r["file"] for r in rows
                    if not int(r.get("deps_warmup_ok", 1) or 0)}
    by_file = {}
    for r in rows:
        if r["file"] not in contaminated:
            by_file.setdefault(r["file"], {})[r["solver"]] = r

    def ok(f, s):
        return f in by_file and s in by_file[f] and int(by_file[f][s]["ok"])

    def sec(f, s):
        return float(by_file[f][s]["seconds"])

    solved = {s: sorted(f for f in by_file if ok(f, s))
              for s in [BASELINE] + list(solvers)}
    covered = [f for f in by_file if all(s in by_file[f] for s in solvers)]
    union = sorted(f for f in covered if any(ok(f, s) for s in solvers))
    disagree = sorted(f for f in union if not all(ok(f, s) for s in solvers))
    exactly_one = {
        f: next(s for s in solvers if ok(f, s))
        for f in covered if sum(ok(f, s) for s in solvers) == 1
    }
    both = [f for f in union if BASELINE in by_file[f] and ok(f, BASELINE)]
    base_t = sum(sec(f, BASELINE) for f in both)
    vbs_t = sum(min(sec(f, s) for s in solvers if ok(f, s)) for f in both)
    base_missed = {
        f: [s for s in solvers if ok(f, s)]
        for f in union
        if BASELINE in by_file[f] and not ok(f, BASELINE)
    }
    mean_time = {
        s: (statistics.mean([sec(f, s) for f in solved[s]])
            if solved[s] else None)
        for s in solved
    }
    return {
        "total": len(by_file),
        "excluded_dep_contaminated": sorted(contaminated),
        "solved": solved,
        "mean_time": mean_time,
        "union": union,
        "disagree": disagree,
        "exactly_one": exactly_one,
        "both_count": len(both),
        "baseline_total_time": base_t,
        "vbs_total_time": vbs_t,
        "vbs_savings_pct": (100 * (base_t - vbs_t) / base_t
                            if base_t > 0 else None),
        "base_missed": base_missed,
    }


def summarize(rows, solvers):
    st = compute_stats(rows, solvers)
    total = st["total"]
    print("\n================ SUMMARY ================")
    if st["excluded_dep_contaminated"]:
        print(f"excluded (warmup-failed local deps)   : "
              f"{len(st['excluded_dep_contaminated'])} file(s)")
    for s in [BASELINE] + list(solvers):
        n = len(st["solved"][s])
        mt = st["mean_time"][s]
        mean_t = f"{mt:.2f}s" if mt is not None else "-"
        print(f"{s:<14} solved {n:>3}/{total}   mean time (solved): {mean_t}")

    print(f"\nunion of single solvers (VBS) solves : "
          f"{len(st['union'])}/{total}")
    print(f"files where solvers DISAGREE          : {len(st['disagree'])}"
          f"  <- solver choice matters here (H1)")
    print(f"files solved by EXACTLY ONE solver    : {len(st['exactly_one'])}")
    for f, winner in list(st["exactly_one"].items())[:15]:
        print(f"    {f}  (only {winner})")

    if st["both_count"]:
        print(f"\non files solved by both baseline & VBS ({st['both_count']}):")
        print(f"    baseline total time : {st['baseline_total_time']:8.2f}s")
        print(f"    VBS total time      : {st['vbs_total_time']:8.2f}s")
        if st["vbs_savings_pct"] is not None:
            print(f"    -> perfect per-file solver selection saves "
                  f"{st['vbs_savings_pct']:.1f}% "
                  f"(upper bound for LLM-based selection, H2)")
    if st["base_missed"]:
        print(f"\nfiles VBS solves but baseline does NOT "
              f"({len(st['base_missed'])}):")
        for f, winners in list(st["base_missed"].items())[:10]:
            print(f"    {f}  ({', '.join(winners)})")
    return st


# ---------------- preflight ----------------

CANARY_TRUE = "lemma bench_canary (x y : int) : x + y = y + x by smt.\n"
CANARY_FALSE = "lemma bench_canary_bad (x : int) : x < x by smt.\n"


def preflight(args):
    """Assert every assumption the benchmark relies on, before spending
    hours on a full run. Aborts loudly on any violation."""
    print("preflight: verifying environment assumptions...")

    # 1. binary exists and is runnable
    ec = shutil.which(args.ec)
    assert ec, (f"easycrypt binary '{args.ec}' not on PATH "
                f"(did you `source ~/ec-env.sh`?)")

    # 2. every requested solver is registered in why3.conf
    #    (the provers line is printed on stderr)
    p = subprocess.run([args.ec, "config"], capture_output=True, text=True,
                       timeout=60)
    cfg = p.stdout + p.stderr
    m = re.search(r"known provers:\s*(.+)", cfg)
    assert m, "could not find 'known provers:' in `easycrypt config` output"
    known = m.group(1)
    for s in args.solvers:
        assert re.search(rf"(^|,)\s*{re.escape(s)}@", known), \
            f"solver '{s}' not registered with why3 (known: {known.strip()})"

    # 3. a true lemma passes under EVERY solver individually,
    #    through the same isolated-run path used for measurements
    with tempfile.TemporaryDirectory(prefix="ecpreflight_") as td:
        good = Path(td) / "canary_good.ec"
        good.write_text("require import AllCore.\n" + CANARY_TRUE)
        for s in [None] + list(args.solvers):
            ok, dt, tail = measured_run(args.ec, good, s, args.smt_timeout,
                                        max(60, args.smt_timeout * 3), [])
            label = s or BASELINE
            assert ok, (f"canary TRUE lemma failed under '{label}' "
                        f"({dt:.1f}s): {tail[-300:]}")

        # 4. a false lemma is rejected (failure detection works)
        bad = Path(td) / "canary_bad.ec"
        bad.write_text("require import AllCore.\n" + CANARY_FALSE)
        ok, _, _ = measured_run(args.ec, bad, None, args.smt_timeout,
                                max(60, args.smt_timeout * 3), [])
        assert not ok, ("canary FALSE lemma PASSED — failure detection "
                        "is broken; all benchmark data would be garbage")

    print(f"preflight OK: {args.ec} + solvers {args.solvers} "
          f"all verified end-to-end\n")


# ---------------- entry ----------------

def collect_files(root: Path, limit: int):
    files = sorted(p for p in root.rglob("*.ec"))
    if limit > 0:
        files = files[:limit]
    return files


def validate_args(args, parser):
    if args.out is None:
        args.out = "results/timing.csv" if args.retime else "results/sweep.csv"
    if args.retime and Path(args.out).resolve() == Path(args.retime).resolve():
        parser.error("--out would overwrite the sweep CSV passed to --retime; "
                     "choose a different --out (e.g. results/timing.csv)")
    return args


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="corpus root (searched recursively for .ec)")
    ap.add_argument("--solvers", nargs="+",
                    default=["Alt-Ergo", "Z3", "CVC5"],
                    help="solver names as in why3.conf")
    ap.add_argument("--smt-timeout", type=int, default=10,
                    help="SMT timeout passed via -timeout (0 = omit)")
    ap.add_argument("--hard-timeout", type=int, default=300,
                    help="hard wall-clock limit per run, seconds")
    ap.add_argument("--ec", default="easycrypt", help="easycrypt binary")
    ap.add_argument("-I", "--idir", action="append", default=[],
                    help="extra include dir(s)")
    ap.add_argument("--jobs", type=int, default=8,
                    help="parallel workers for sweep/warmup (measured retime "
                         "runs are always serial)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only first N files (0 = all)")
    ap.add_argument("--out", default=None,
                    help="CSV output (default: results/sweep.csv, or "
                         "results/timing.csv in --retime mode)")
    ap.add_argument("--retime", metavar="SWEEP_CSV",
                    help="re-run all pairs from a sweep CSV serially "
                         "(--reps repetitions for sweep successes, 1 "
                         "confirmation for sweep failures)")
    ap.add_argument("--reps", type=int, default=3,
                    help="repetitions per successful pair in --retime mode")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip environment assumption checks (tests only)")
    args = validate_args(ap.parse_args(), ap)

    install_signal_handlers()

    root = Path(args.dir).resolve()
    files = collect_files(root, args.limit)
    if not files:
        print(f"no .ec files under {root}", file=sys.stderr)
        sys.exit(1)

    if args.no_preflight:
        print("preflight SKIPPED (--no-preflight)")
    else:
        preflight(args)

    if args.retime:
        cmd_retime(args, root, files)
    else:
        cmd_sweep(args, root, files)


if __name__ == "__main__":
    main()

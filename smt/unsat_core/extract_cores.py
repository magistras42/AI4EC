#!/usr/bin/env python3
"""Extract unsat cores for every smt call in the benchmark corpus (stdlib only).

Requires the patched EasyCrypt (EC_SMT_DEBUG dumps numbered <Prover>.NNNN.smt
per call instead of overwriting a single file).

Per (file, solver in {Z3, CVC5}):
  1. Temp-copy the file, run with cwd=tempdir:
       EC_SMT_DEBUG=1 easycrypt compile -no-eco -p <S> -timeout <T> -I <orig parent>
     -> one dump per executed smt call, in execution (= file) order.
  2. Per dump: name every assert (smtcore.name_asserts), run the solver with
     core production, parse the core, classify members (goal / Top_* env
     lemma / other), record the goal text.

Outputs under --out:
  cores.jsonl   one record per (file, solver, call_idx)
  runs.csv      one row per (file, solver): ec_ok, seconds, n_dumps
  SUMMARY.md    headline statistics

Usage:
  source ~/ec-env.sh
  python3 extract_cores.py --warmup            # rebuild .eco dep caches once
  python3 extract_cores.py                     # full extraction
  python3 extract_cores.py --files examples/ehoare/adversary.ec   # subset
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smtcore
from smtcore import extract_goal

ROOT = HERE.parent / "smt_benchmark"
CORPUS = ROOT / "corpus"
TIMING = ROOT / "results" / "timing.csv"

SOLVERS = ["Z3", "CVC5"]          # Alt-Ergo 2.4.3 cores are unusable
SOLVER_CMD = {
    "Z3":   lambda f: ["z3", "unsat_core=true", str(f)],
    "CVC5": lambda f: ["cvc5", "--lang", "smt2", "--produce-unsat-cores", str(f)],
}
DUMP_RE = {s: re.compile(re.escape(s) + r"\.(\d{4,})\.smt$") for s in SOLVERS}


def corpus_files():
    """The 48 benchmark files: distinct files in timing.csv with clean deps."""
    files, seen = [], set()
    with open(TIMING) as fh:
        for row in csv.DictReader(fh):
            f = row["file"]
            if f in seen:
                continue
            seen.add(f)
            if row["deps_warmup_ok"] == "1":
                files.append(f)
    return files


def run_cmd(cmd, cwd, timeout, env=None):
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode(errors="replace"), time.monotonic() - t0
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace")
        return -9, out + "\n[hard timeout]", time.monotonic() - t0


def solver_status(out):
    if re.search(r"^unsat\s*$", out, re.M):
        return "unsat"
    if re.search(r"^sat\s*$", out, re.M):
        return "sat"
    if re.search(r"^unknown\s*$", out, re.M):
        return "unknown"
    if "[hard timeout]" in out:
        return "timeout"
    return "error"


def process_dump(dump_path, solver, core_timeout):
    text = dump_path.read_text(errors="replace")
    named = smtcore.name_asserts(text)
    named_path = dump_path.with_suffix(".named.smt2")
    named_path.write_text(named)

    rc, out, secs = run_cmd(SOLVER_CMD[solver](named_path),
                            cwd=dump_path.parent, timeout=core_timeout)
    status = solver_status(out)
    core = []
    if status == "unsat":
        try:
            core = smtcore.parse_core(out)
        except SystemExit:
            status = "core-parse-error"

    env = sorted(n for n in core if n.startswith("Top_"))
    other = sorted(n for n in core if not n.startswith("Top_") and n != "goal")

    # what was SENT: unique base names of all named asserts; Top_* = environment
    # axioms/lemmas (the LLM's candidate pool), rest = goal/hyps/builtins.
    # First-occurrence order is preserved: the dump lists axioms in task
    # insertion order, which reflects the relevancy filter's admission rounds.
    names = [re.sub(r"__\d+$", "", n.strip("|")) for n in
             re.findall(r":named\s+(\|[^|]+\||[^\s()]+)", named)]
    sent_env = list(dict.fromkeys(n for n in names if n.startswith("Top_")))

    return {
        "n_asserts": named.count("(assert (!"),
        "n_sent_env": len(sent_env),
        "sent_env": sent_env,
        "status": status,
        "solver_ms": int(secs * 1000),
        "core_env": env,
        "core_other": other,
        "goal_in_core": "goal" in core,
        "goal": extract_goal(text),
    }


def run_pair(relfile, solver, args):
    """Run one (file, solver) extraction; returns (run_row, [call_records])."""
    orig = CORPUS / relfile
    with tempfile.TemporaryDirectory(prefix="eccore_") as td:
        td = Path(td)
        tmp = td / orig.name
        shutil.copyfile(orig, tmp)
        env = dict(os.environ, EC_SMT_DEBUG="1")
        cmd = ["easycrypt", "compile", "-no-eco", "-p", solver,
               "-timeout", str(args.smt_timeout), "-I", str(orig.parent), str(tmp)]
        rc, out, secs = run_cmd(cmd, cwd=td, timeout=args.hard_timeout, env=env)

        dumps = sorted((p for p in td.iterdir() if DUMP_RE[solver].search(p.name)),
                       key=lambda p: int(DUMP_RE[solver].search(p.name).group(1)))
        records = []
        for p in dumps:
            call_idx = int(DUMP_RE[solver].search(p.name).group(1))
            rec = {"file": relfile, "solver": solver, "call": call_idx,
                   "ec_ok": int(rc == 0)}
            try:
                rec.update(process_dump(p, solver, args.core_timeout))
            except Exception as e:                      # keep the sweep alive
                rec.update({"status": "pipeline-error", "error": str(e)[:300]})
            records.append(rec)

        run_row = {"file": relfile, "solver": solver, "ec_ok": int(rc == 0),
                   "seconds": round(secs, 3), "n_dumps": len(dumps),
                   "tail": " ".join(out[-300:].split()) if rc != 0 else ""}
        return run_row, records


def warmup(files, args):
    """In-tree compile with .eco writing enabled (rebuilds dep caches for the
    current easycrypt binary). Sequential within a directory, dirs parallel."""
    by_dir = {}
    for rel in files:
        by_dir.setdefault((CORPUS / rel).parent, []).append(rel)

    def warm_dir(rels):
        for rel in rels:
            orig = CORPUS / rel
            cmd = ["easycrypt", "compile", "-timeout", str(args.smt_timeout),
                   "-I", str(orig.parent), str(orig)]
            rc, _, secs = run_cmd(cmd, cwd=orig.parent, timeout=args.hard_timeout)
            print(f"  [warmup] {rel:<60} {'OK  ' if rc == 0 else 'FAIL'} {secs:6.1f}s",
                  flush=True)

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(warm_dir, by_dir.values()))


def summarize(all_runs, all_recs, outdir):
    ok_runs = {s: sum(1 for r in all_runs if r["solver"] == s and r["ec_ok"])
               for s in SOLVERS}
    unsat = [r for r in all_recs if r.get("status") == "unsat"]
    by_status = {}
    for r in all_recs:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1

    def stats(vals):
        if not vals:
            return "n/a"
        vals = sorted(vals)
        return (f"mean {sum(vals)/len(vals):.1f}, median {vals[len(vals)//2]}, "
                f"min {vals[0]}, max {vals[-1]}")

    lines = ["# Unsat core extraction — corpus summary", ""]
    lines.append(f"- (file, solver) runs: {len(all_runs)}; whole-file ok: "
                 + ", ".join(f"{s} {ok_runs[s]}" for s in SOLVERS))
    lines.append(f"- dumps (= executed smt calls observed): {len(all_recs)}")
    lines.append(f"- per-dump status: " + ", ".join(
        f"{k} {v}" for k, v in sorted(by_status.items())))
    lines.append("")
    lines.append("## Sent vs used (unsat dumps only)")
    lines.append(f"- asserts sent per call: {stats([r['n_asserts'] for r in unsat])}")
    lines.append(f"- env lemmas SENT per call: "
                 + stats([r.get('n_sent_env', 0) for r in unsat]))
    lines.append(f"- env lemmas (Top_*) USED (in core): "
                 + stats([len(r['core_env']) for r in unsat]))
    lines.append(f"- calls whose core has 0 env lemmas: "
                 + str(sum(1 for r in unsat if not r['core_env'])))
    with_env = [r for r in unsat if r.get('n_sent_env', 0) > 0]
    lines.append(f"- calls that SENT >0 env lemmas: {len(with_env)}; among them "
                 f"sent {stats([r['n_sent_env'] for r in with_env])} vs used "
                 + stats([len(r['core_env']) for r in with_env]))
    lines.append("")

    # cross-solver core agreement on calls both solvers proved
    bykey = {}
    for r in unsat:
        bykey.setdefault((r["file"], r["call"]), {})[r["solver"]] = r
    both = {k: v for k, v in bykey.items() if len(v) == 2}
    same = sum(1 for v in both.values()
               if set(v["Z3"]["core_env"]) == set(v["CVC5"]["core_env"]))
    lines.append("## Cross-solver comparison (calls unsat under both)")
    lines.append(f"- joint calls: {len(both)}; identical env-lemma cores: {same}; "
                 f"differing: {len(both) - same}")
    (outdir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--smt-timeout", type=int, default=10)
    ap.add_argument("--hard-timeout", type=int, default=300)
    ap.add_argument("--core-timeout", type=int, default=30)
    ap.add_argument("--warmup", action="store_true",
                    help="only rebuild in-tree .eco caches, then exit")
    ap.add_argument("--files", nargs="*", default=None,
                    help="subset of corpus-relative files (default: all 48)")
    args = ap.parse_args()

    files = args.files if args.files else corpus_files()
    print(f"{len(files)} corpus files", flush=True)

    if args.warmup:
        warmup(files, args)
        return

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    pairs = [(f, s) for f in files for s in SOLVERS]

    all_runs, all_recs = [], []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_pair, f, s, args): (f, s) for f, s in pairs}
        done = 0
        for fut in as_completed(futs):
            f, s = futs[fut]
            run_row, recs = fut.result()
            all_runs.append(run_row)
            all_recs.extend(recs)
            done += 1
            n_unsat = sum(1 for r in recs if r.get("status") == "unsat")
            print(f"[{done:>3}/{len(pairs)}] {s:<5} {f:<58} "
                  f"ec_ok={run_row['ec_ok']} dumps={run_row['n_dumps']} "
                  f"unsat={n_unsat} ({run_row['seconds']}s)", flush=True)

    write_outputs(all_runs, all_recs, outdir)


def write_outputs(all_runs, all_recs, outdir):
    all_runs.sort(key=lambda r: (r["file"], r["solver"]))
    all_recs.sort(key=lambda r: (r["file"], r["solver"], r["call"]))

    with open(outdir / "runs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_runs[0].keys()))
        w.writeheader()
        w.writerows(all_runs)
    with open(outdir / "cores.jsonl", "w") as fh:
        for r in all_recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    summarize(all_runs, all_recs, outdir)


if __name__ == "__main__":
    main()

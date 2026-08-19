#!/usr/bin/env python3
"""Dump-replay validation: does a lemma ranking actually carry the proof?

Replays the per-call SMT-LIB dumps directly on the solvers, with the env
lemma (Top_*) asserts restricted to a chosen set. Dropping asserts can only
remove axioms, so an `unsat` on a restricted variant means the kept lemmas
really suffice. This mirrors smt(hints) restrict semantics on the solver
input; the true EasyCrypt path is ec_path_eval.py.

Variant field names in the output jsonl are historical: `llm8` = the
top-k restricted arm (whatever preds produced it), `full` = as sent,
`oracle` = unsat-core lemmas only.

Subcommands:
  dumps      regenerate + keep per-call dumps for the eval files (patched EC)
  run        replay full / top-k / oracle for every eval.jsonl goal
  kablation  top-k sweep (k in 0..32) on the strip-hints failing goals
  portfolio  goal-level portfolio table: EC default vs +embed arms vs oracle

Usage:
  source ~/ec-env.sh
  python3 replay_eval.py dumps --dumpdir DUMPS
  python3 replay_eval.py run --dumpdir DUMPS --preds eval/preds_X.json --topk 8
  python3 replay_eval.py kablation --workdir stripped_workdir \
      --preds eval/preds_embed_top32_qe4.json
  python3 replay_eval.py portfolio
"""
import argparse
import json
import os
import re
import shutil
import statistics as st
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smtcore
from extract_cores import CORPUS, DUMP_RE, SOLVERS, run_cmd
from smtcore import extract_goal

EVAL = HERE / "eval"

REPLAY_CMD = {
    "Z3":   lambda f: ["z3", str(f)],
    "CVC5": lambda f: ["cvc5", "--lang", "smt2", str(f)],
}

NAMED_RE = re.compile(r":named\s+(\|[^|]+\||[^\s()]+)")

K_DEFAULT = (0, 1, 2, 4, 8, 16, 32)


def slug(relfile):
    return relfile.replace("/", "__")


# ------------------------------------------------------------------ dumps

def regen_pair(relfile, solver, dumpdir, smt_timeout, hard_timeout):
    orig = CORPUS / relfile
    dest = dumpdir / solver / slug(relfile)
    with tempfile.TemporaryDirectory(prefix="ecdump_") as td:
        td = Path(td)
        shutil.copyfile(orig, td / orig.name)
        env = dict(os.environ, EC_SMT_DEBUG="1")
        cmd = ["easycrypt", "compile", "-no-eco", "-p", solver,
               "-timeout", str(smt_timeout), "-I", str(orig.parent),
               str(td / orig.name)]
        rc, _, secs = run_cmd(cmd, cwd=td, timeout=hard_timeout, env=env)
        dumps = [p for p in td.iterdir() if DUMP_RE[solver].search(p.name)]
        dest.mkdir(parents=True, exist_ok=True)
        for p in dumps:
            shutil.copyfile(p, dest / p.name)
    return relfile, solver, rc == 0, len(dumps), secs


def cmd_dumps(args):
    ev = [json.loads(l) for l in open(EVAL / "eval.jsonl")]
    files = sorted({r["file"] for r in ev})
    dumpdir = Path(args.dumpdir)
    print(f"{len(files)} eval files -> {dumpdir}", flush=True)
    pairs = [(f, s) for f in files for s in SOLVERS]
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(regen_pair, f, s, dumpdir,
                          args.smt_timeout, args.hard_timeout)
                for f, s in pairs]
        for fut in as_completed(futs):
            f, s, ok, n, secs = fut.result()
            print(f"  {s:<5} {f:<58} ec_ok={int(ok)} dumps={n} "
                  f"({secs:.1f}s)", flush=True)


# --------------------------------------------------- shared replay helpers

def load_dump_index(dumpdir):
    """{(file, solver): [(call_idx, goal_text, path), ...]}"""
    idx = {}
    for solver in SOLVERS:
        sdir = Path(dumpdir) / solver
        if not sdir.is_dir():
            continue
        for fdir in sdir.iterdir():
            relfile = fdir.name.replace("__", "/")
            entries = []
            for p in sorted(fdir.iterdir()):
                m = DUMP_RE[solver].search(p.name)
                if not m:
                    continue
                text = p.read_text(errors="replace")
                entries.append((int(m.group(1)), extract_goal(text), p))
            idx[(relfile, solver)] = entries
    return idx


def find_dump(idx, relfile, solver, call, goal):
    """Prefer the same call number when its goal text matches; else fall back
    to goal-text search (parallel-load noise can shift call numbering).
    eval.jsonl goals had 'Top_' stripped at build time -> normalize both."""
    entries = idx.get((relfile, solver), [])
    for c, g, p in entries:
        if c == call and g.replace("Top_", "") == goal:
            return p
    for c, g, p in entries:
        if g.replace("Top_", "") == goal:
            return p
    return None


def build_variant(named_text, keep_env):
    """Drop Top_*-named asserts whose base is not in keep_env (None = keep all).
    Also drop the (get-unsat-core) line (we replay without core production)."""
    out = []
    i, n = 0, len(named_text)
    while i < n:
        if named_text[i] == "(":
            j = smtcore._scan_form(named_text, i)
            form = named_text[i:j]
            head = form[1:].lstrip()
            if head.startswith("get-unsat-core"):
                i = j
                continue
            if head.startswith("assert"):
                m = NAMED_RE.search(form)
                if m:
                    base = re.sub(r"__\d+$", "", m.group(1).strip("|"))
                    if (base.startswith("Top_") and keep_env is not None
                            and base not in keep_env):
                        i = j
                        continue
            out.append(form)
            i = j
        else:
            out.append(named_text[i])
            i += 1
    return "".join(out)


def replay(solver, text, workdir, timeout):
    path = Path(workdir) / f"replay_{os.getpid()}_{time.monotonic_ns()}.smt2"
    path.write_text(text)
    rc, out, secs = run_cmd(REPLAY_CMD[solver](path), cwd=workdir,
                            timeout=timeout)
    path.unlink(missing_ok=True)
    if re.search(r"^unsat\s*$", out, re.M):
        status = "unsat"
    elif re.search(r"^sat\s*$", out, re.M):
        status = "sat"
    elif re.search(r"^unknown\s*$", out, re.M):
        status = "unknown"
    elif "[hard timeout]" in out:
        status = "timeout"
    else:
        status = "error"
    return status, int(secs * 1000)


# -------------------------------------------------------------------- run

def replay_job(rec, solver, dump_path, picks, workdir, timeout):
    """One (goal, solver): name asserts once, replay the three variants."""
    text = dump_path.read_text(errors="replace")
    named = smtcore.name_asserts(text)
    sent = {re.sub(r"__\d+$", "", n.strip("|"))
            for n in NAMED_RE.findall(named)}
    sent_env = {n for n in sent if n.startswith("Top_")}

    keep_topk = {"Top_" + p for p in picks}
    keep_oracle = {"Top_" + a for a in rec["answer"]}

    row = {"id": rec["id"], "file": rec["file"], "call": rec["call"],
           "solver": solver, "n_sent_env": len(sent_env),
           "n_llm_kept": len(keep_topk & sent_env),
           "offline_hit": int(keep_oracle <= keep_topk)}
    for label, keep in (("full", None), ("llm8", keep_topk),
                        ("oracle", keep_oracle)):
        status, ms = replay(solver, build_variant(named, keep),
                            workdir, timeout)
        row[label] = status
        row[label + "_ms"] = ms
    return row


def cmd_run(args):
    ev = [json.loads(l) for l in open(EVAL / "eval.jsonl")]
    preds = json.load(open(args.preds))
    idx = load_dump_index(args.dumpdir)

    jobs, missing = [], []
    for rec in ev:
        picks = preds.get(rec["id"], [])[:args.topk]
        for solver in SOLVERS:
            p = find_dump(idx, rec["file"], solver, rec["call"], rec["goal"])
            if p is None:
                missing.append((rec["id"], solver))
                continue
            jobs.append((rec, solver, p, picks))
    print(f"{len(ev)} goals -> {len(jobs)} (goal, solver) replays; "
          f"{len(missing)} dumps not matched", flush=True)

    rows = []
    with tempfile.TemporaryDirectory(prefix="ecreplay_") as td, \
            ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(replay_job, r, s, p, k, td, args.timeout)
                for r, s, p, k in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            if i % 40 == 0 or i == len(futs):
                print(f"  [{i}/{len(futs)}]", flush=True)

    rows.sort(key=lambda r: (r["id"], r["solver"]))
    out = Path(args.out) if args.out else EVAL / "online_replay.jsonl"
    with open(out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    if missing:
        (EVAL / "online_replay_missing.json").write_text(
            json.dumps(missing, indent=1))
    print(f"wrote {out} ({len(rows)} rows)")


# -------------------------------------------------------------- kablation

def kabl_job(rec, picks, td, ks):
    """Replay one failing goal keeping only the top-k ranked env lemmas.
    k=0 removes every Top_* assert (incl. translation 'defs) — harsher
    than smt(). Nested curve: one ranked-32 prediction, truncated."""
    named = smtcore.name_asserts(Path(rec["dump"]).read_text(errors="replace"))
    sent = {re.sub(r"__\d+$", "", n.strip("|"))
            for n in NAMED_RE.findall(named)}
    pool = sorted(n for n in sent if n.startswith("Top_"))
    row = {"id": rec["id"], "solver": rec["solver"], "pool": len(pool)}
    for k in ks:
        keep = {"Top_" + p for p in picks[:k]}
        stt, ms = replay(rec["solver"], build_variant(named, keep), td, 10)
        row[f"k{k}"] = stt
        row[f"k{k}_ms"] = ms
    return row


def cmd_kablation(args):
    ks = tuple(int(x) for x in args.ks.split(","))
    preds = json.load(open(args.preds))
    ids = json.load(open(Path(args.workdir) / "failing_goals.json"))
    rows = []
    with tempfile.TemporaryDirectory(prefix="eckabl_") as td, \
            ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(kabl_job, r, preds.get(r["id"], []), td, ks)
                for r in ids]
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            print(f"[{i}/{len(futs)}]", flush=True)

    out = Path(args.out) if args.out else Path(args.workdir) / "k_ablation.json"
    json.dump(rows, open(out, "w"), indent=1)

    pools = [r["pool"] for r in rows]
    print(f"\nfailing goals n={len(rows)}; candidate pool sizes: "
          f"mean {st.mean(pools):.1f}, median {st.median(pools)}, "
          f"min {min(pools)}, max {max(pools)}")
    print(f"{'k':>4} {'recovered':>10} {'median_ms(ok)':>14}")
    for k in ks:
        ok = [r for r in rows if r[f"k{k}"] == "unsat"]
        med = st.median([r[f"k{k}_ms"] for r in ok]) if ok else "-"
        print(f"{k:>4} {len(ok):>6}/{len(rows)} {str(med):>14}")


# -------------------------------------------------------------- portfolio

def load_replay(fn):
    return {(r["id"], r["solver"]): r
            for r in (json.loads(l) for l in open(EVAL / fn))}


def print_per_solver(label, data, field):
    for sv in SOLVERS:
        ks = [k for k in data if k[1] == sv]
        oks = sum(data[k][field] == "unsat" for k in ks)
        tot = sum(data[k][field + "_ms"] for k in ks) / 1000
        print(f"  {label:16s} {sv:5s} ok={oks}/{len(ks)} "
              f"({100 * oks / len(ks):.1f}%) total={tot:.1f}s")


def print_portfolio(label, arms, ids):
    """A goal succeeds if ANY arm is unsat; per-goal time = fastest
    successful arm, else the slowest failing arm's cost."""
    oks, times = 0, []
    for gid in ids:
        rs = []
        for data, field in arms:
            for sv in SOLVERS:
                r = data.get((gid, sv))
                if r:
                    rs.append((r[field], r[field + "_ms"]))
        ok = any(s == "unsat" for s, _ in rs)
        oks += ok
        times.append(min(ms for s, ms in rs if s == "unsat") if ok
                     else max(ms for _, ms in rs))
    print(f"  {label:28s} ok={oks}/{len(times)} "
          f"({100 * oks / len(times):.1f}%) "
          f"total={sum(times) / 1000:7.1f}s "
          f"median={st.median(times):.0f}ms")


def cmd_portfolio(args):
    e8 = load_replay(args.embed8)
    e16 = load_replay(args.embed16)
    ids = sorted({k[0] for k in e8})
    print(f"matched (goal,solver): {len(e8)} | goals: {len(ids)}")

    print_per_solver("full", e8, "full")
    print_per_solver("embed8", e8, "llm8")
    print_per_solver("embed16", e16, "llm8")
    print_per_solver("oracle", e8, "oracle")

    print("\n## portfolio (goal-level)")
    print_portfolio("EC default (full only)", [(e8, "full")], ids)
    print_portfolio("embed8 restrict alone", [(e8, "llm8")], ids)
    print_portfolio("embed16 restrict alone", [(e16, "llm8")], ids)
    print_portfolio("full + embed8 arm", [(e8, "full"), (e8, "llm8")], ids)
    print_portfolio("full + embed16 arm", [(e8, "full"), (e16, "llm8")], ids)
    print_portfolio("full + embed8+16 arms",
                    [(e8, "full"), (e8, "llm8"), (e16, "llm8")], ids)
    print_portfolio("oracle", [(e8, "oracle")], ids)


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dumps")
    p.add_argument("--dumpdir", required=True)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--smt-timeout", type=int, default=10)
    p.add_argument("--hard-timeout", type=int, default=300)
    p.set_defaults(fn=cmd_dumps)

    p = sub.add_parser("run")
    p.add_argument("--dumpdir", required=True)
    p.add_argument("--preds", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--topk", type=int, default=8)
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--timeout", type=int, default=10)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("kablation")
    p.add_argument("--workdir", required=True)
    p.add_argument("--preds", required=True)
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--ks", default=",".join(map(str, K_DEFAULT)))
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_kablation)

    p = sub.add_parser("portfolio")
    p.add_argument("--embed8", default="online_replay_embed8_qe4.jsonl")
    p.add_argument("--embed16", default="online_replay_embed16_qe4.jsonl")
    p.set_defaults(fn=cmd_portfolio)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

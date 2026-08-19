#!/usr/bin/env python3
"""Evaluation datasets + offline recall scoring (stdlib only).

Two labeled benchmarks, both grounded in the extracted unsat cores:

  266-goal set   build: results/cores.jsonl -> eval/eval.jsonl. One record
                 per unique goal whose core contains >=1 env lemma;
                 candidates = lemmas EC actually sent, answer = core.
  33-goal set    strip: rewrite every hinted smt(...) call to bare smt,
                 re-run EC's relevance filter (patched EC dumps), replay
                 every dump as sent; the failing replays become
                 <workdir>/failing_goals.json — the recovery benchmark.

Plus:
  baselines      random (seeded shuffle) and lexical (name-token overlap
                 with the goal text) rankings over the FULL bare-smt env
                 (~1.5k pool, same pools as embed_rank recall266) →
                 eval/fullenv_{random,lexical}.json, full-recall@k printed
  score          recall@k / full-recall@k table for any predictions file
                 ({goal_id: [candidate display names, ranked]})

Usage:
  python3 build_datasets.py build
  python3 build_datasets.py baselines
  python3 build_datasets.py strip --workdir stripped_workdir      # needs EC
  python3 build_datasets.py score eval/preds_armpicks_qe4.json --label arm
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smtcore
from extract_cores import CORPUS, DUMP_RE, SOLVERS, run_cmd
from replay_eval import NAMED_RE, build_variant, replay
from smtcore import extract_goal

RESULTS = HERE / "results"
EVAL = HERE / "eval"
TIMING = HERE.parent / "smt_benchmark" / "results" / "timing.csv"

SCORE_KS = (1, 2, 4, 8, 16)


def display(name):
    return name[4:] if name.startswith("Top_") else name


def load_eval():
    return [json.loads(l) for l in open(EVAL / "eval.jsonl")]


# ------------------------------------------------------------------ build

def cmd_build(_args):
    recs = [json.loads(l) for l in open(RESULTS / "cores.jsonl")]
    recs = [r for r in recs if r["status"] == "unsat"
            and r.get("n_sent_env", 0) > 0 and r["core_env"]]
    # dedupe by goal, prefer Z3's record
    bykey = {}
    for r in recs:
        k = (r["file"], r["call"])
        if k not in bykey or r["solver"] == "Z3":
            bykey[k] = r

    EVAL.mkdir(exist_ok=True)
    out = []
    for (f, call), r in sorted(bykey.items()):
        out.append({"id": f"{f}::{call}", "file": f, "call": call,
                    "solver": r["solver"],
                    "goal": r["goal"].replace("Top_", ""),
                    "candidates": [display(n) for n in r["sent_env"]],
                    "answer": [display(n) for n in r["core_env"]]})
    with open(EVAL / "eval.jsonl", "w") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_c = [len(r["candidates"]) for r in out]
    n_a = [len(r["answer"]) for r in out]
    print(f"eval set: {len(out)} goals from {len({r['file'] for r in out})} files")
    print(f"candidates/goal: mean {sum(n_c)/len(n_c):.1f}, median "
          f"{sorted(n_c)[len(n_c)//2]}, max {max(n_c)}")
    print(f"answer size: mean {sum(n_a)/len(n_a):.2f}, max {max(n_a)}")


# ------------------------------------------------------------------ baselines

TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")

FULLENV_KS = (1, 2, 4, 8, 16, 32)


def tokens(s):
    return {t.lower() for t in TOKEN_RE.findall(s)}


def rank_lexical(names, goal_text):
    """Rank env lemma names by token overlap with the goal text
    (display-name tokens; same formula as the historical lexical baseline)."""
    gtok = tokens(goal_text)
    scored = []
    for n in names:
        ct = tokens(n[4:] if n.startswith("Top_") else n)
        hit = len(ct & gtok)
        scored.append((-(hit / len(ct)) if ct else 0.0, -hit, n))
    scored.sort()
    return [n for _, _, n in scored]


def fullenv_rows(ev, idx, rank_fn):
    """{goal_id: full@k row} ranking each goal's FULL bare-env pool."""
    from replay_eval import find_dump
    rows, n_nodump = {}, 0
    for rec in ev:
        p = find_dump(idx, rec["file"], rec["solver"], rec["call"],
                      rec["goal"])
        if p is None:
            for s in SOLVERS:
                p = find_dump(idx, rec["file"], s, rec["call"], rec["goal"])
                if p is not None:
                    break
        if p is None:
            n_nodump += 1
            continue
        env = smtcore.collect_env_asserts(p.read_text(errors="replace"))
        if not env:
            n_nodump += 1
            continue
        names = sorted(env)
        ranked = rank_fn(names, rec)
        ans = {"Top_" + a for a in rec["answer"]}
        row = {"id": rec["id"], "pool": len(names),
               "ans_in_env": int(ans <= set(names))}
        for k in FULLENV_KS:
            row[f"full@{k}"] = int(ans <= set(ranked[:k]))
        rows[rec["id"]] = row
    return rows, n_nodump


def report_fullenv(tag, ev, rows, n_nodump):
    n = len(rows)
    pools = sorted(r["pool"] for r in rows.values())
    print(f"\n{tag}: scored {n}/{len(ev)} goals (no dump {n_nodump})")
    print(f"pool median {pools[n // 2]}, max {pools[-1]}; answer-in-env "
          f"{sum(r['ans_in_env'] for r in rows.values())}/{n}")
    print("full-recall@k: " + "  ".join(
        f"@{k}={sum(r[f'full@{k}'] for r in rows.values()) / n:.3f}"
        for k in FULLENV_KS))


def cmd_baselines(args):
    from replay_eval import load_dump_index
    ev = load_eval()
    idx = load_dump_index(args.dumpdir)
    rng = random.Random(42)

    def rand_fn(names, _rec):
        shuf = names[:]
        rng.shuffle(shuf)
        return shuf

    def lex_fn(names, rec):
        return rank_lexical(names, rec["goal"])

    for tag, fn in (("random", rand_fn), ("lexical", lex_fn)):
        rows, n_nodump = fullenv_rows(ev, idx, fn)
        json.dump(rows, open(EVAL / f"fullenv_{tag}.json", "w"), indent=1)
        report_fullenv(tag, ev, rows, n_nodump)


# ------------------------------------------------------------------ strip

def hinted_files():
    """Corpus files (clean deps) containing >=1 hinted smt(...) call."""
    files, seen = [], set()
    with open(TIMING) as fh:
        for row in csv.DictReader(fh):
            f = row["file"]
            if f in seen or row["deps_warmup_ok"] != "1":
                continue
            seen.add(f)
            text = (CORPUS / f).read_text(errors="replace")
            masked = smtcore.mask_ec_comments(text)
            if any(smtcore.call_kind(m) == "PAREN"
                   for m in smtcore._SMT_CALL_RE.finditer(masked)):
                files.append(f)
    return files


def strip_text(text):
    """Rewrite every PAREN smt call to bare smt; count rewrites."""
    masked = smtcore.mask_ec_comments(text)
    out, last, n = [], 0, 0
    for m in smtcore._SMT_CALL_RE.finditer(masked):
        if smtcore.call_kind(m) != "PAREN":
            continue
        out.append(text[last:m.start()])
        out.append("smt")
        last = m.end()
        n += 1
    out.append(text[last:])
    return "".join(out), n


def strip_pair(relfile, solver, workdir, args):
    """Strip one (file, solver), compile with dumps, replay each dump."""
    orig = CORPUS / relfile
    stripped, n_stripped = strip_text(orig.read_text(errors="replace"))
    dest = workdir / "dumps" / solver / relfile.replace("/", "__")
    with tempfile.TemporaryDirectory(prefix="ecstrip_") as td:
        td = Path(td)
        (td / orig.name).write_text(stripped)
        env = dict(os.environ, EC_SMT_DEBUG="1")
        cmd = ["easycrypt", "compile", "-no-eco", "-p", solver,
               "-timeout", "10", "-I", str(orig.parent), str(td / orig.name)]
        rc, _, secs = run_cmd(cmd, cwd=td, timeout=args.hard_timeout, env=env)
        dest.mkdir(parents=True, exist_ok=True)
        dumps = [p for p in td.iterdir() if DUMP_RE[solver].search(p.name)]
        for p in dumps:
            shutil.copyfile(p, dest / p.name)

    rows = []
    with tempfile.TemporaryDirectory(prefix="ecstriprep_") as td:
        for p in sorted(dest.iterdir(),
                        key=lambda p: int(DUMP_RE[solver].search(p.name)
                                          .group(1))):
            text = p.read_text(errors="replace")
            named = smtcore.name_asserts(text)
            sent = {re.sub(r"__\d+$", "", n.strip("|"))
                    for n in NAMED_RE.findall(named)}
            stt, ms = replay(solver, build_variant(named, None), td, 10)
            rows.append({
                "file": relfile, "solver": solver,
                "call": int(DUMP_RE[solver].search(p.name).group(1)),
                "dump": str(dest / p.name),
                "n_sent_env": sum(1 for n in sent if n.startswith("Top_")),
                "full": stt, "full_ms": ms, "goal": extract_goal(text)})
    return {"file": relfile, "solver": solver, "ec_ok": int(rc == 0),
            "seconds": round(secs, 1), "n_stripped": n_stripped,
            "n_dumps": len(rows)}, rows


def write_failing_goals(workdir, rows):
    """Dedupe failing replays by (file, goal) -> failing_goals.json."""
    seen = {}
    for r in rows:
        if r["full"] == "unsat" or not r["goal"]:
            continue
        seen.setdefault((r["file"], r["goal"]), r)
    ids = []
    for (f, g), r in sorted(seen.items()):
        ids.append({"id": f"{f}::{r['solver']}:{r['call']}", "file": f,
                    "solver": r["solver"], "call": r["call"],
                    "dump": r["dump"], "goal": g})
    json.dump(ids, open(workdir / "failing_goals.json", "w"), indent=1)
    return ids


def cmd_strip(args):
    workdir = Path(args.workdir)
    files = hinted_files()
    print(f"{len(files)} hinted files", flush=True)
    pairs = [(f, s) for f in files for s in SOLVERS]
    runs, rows = [], []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(strip_pair, f, s, workdir, args): (f, s)
                for f, s in pairs}
        for i, fut in enumerate(as_completed(futs), 1):
            run_row, rs = fut.result()
            runs.append(run_row)
            rows.extend(rs)
            n_fail = sum(1 for r in rs if r["full"] != "unsat")
            print(f"[{i}/{len(pairs)}] {run_row['solver']:<5} "
                  f"{run_row['file']:<52} ec_ok={run_row['ec_ok']} "
                  f"dumps={run_row['n_dumps']} replay_fail={n_fail}",
                  flush=True)

    json.dump(runs, open(workdir / "stripped_runs.json", "w"), indent=1)
    with open(workdir / "stripped_replays.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    ids = write_failing_goals(workdir, rows)
    n_fail = sum(1 for r in rows if r["full"] != "unsat")
    print(f"\nreplays: {len(rows)} total, {n_fail} failing; "
          f"{len(ids)} unique failing goals -> failing_goals.json")


# ------------------------------------------------------------------ report

REPORT_ORDER = ("random", "lexical", "minilm", "bgem3",
                "qe06", "qe4", "qe4eos", "qe8")


def cmd_report(_args):
    """Combined full-recall table from every eval/fullenv_*.json."""
    tags = [t for t in REPORT_ORDER
            if (EVAL / f"fullenv_{t}.json").exists()]
    tags += sorted(t.stem[8:] for t in EVAL.glob("fullenv_*.json")
                   if t.stem[8:] not in tags)
    print("| method | " + " | ".join(f"@{k}" for k in FULLENV_KS) + " | n |")
    print("|---|" + "---|" * (len(FULLENV_KS) + 1))
    for t in tags:
        rows = list(json.load(open(EVAL / f"fullenv_{t}.json")).values())
        cells = " | ".join(
            f"{sum(r[f'full@{k}'] for r in rows) / len(rows):.3f}"
            for k in FULLENV_KS)
        print(f"| {t} | {cells} | {len(rows)} |")


# ------------------------------------------------------------------ score

def cmd_score(args):
    ev = {r["id"]: r for r in load_eval()}
    preds = json.load(open(args.predictions))

    n_scored, invalid = 0, 0
    rec_at = {k: [] for k in SCORE_KS}
    full_at = {k: [] for k in SCORE_KS}
    for gid, r in ev.items():
        picks = preds.get(gid)
        if picks is None:
            continue
        n_scored += 1
        cset = set(r["candidates"])
        clean = [p for p in picks if p in cset]
        invalid += len(picks) - len(clean)
        ans = set(r["answer"])
        for k in SCORE_KS:
            top = set(clean[:k])
            rec_at[k].append(len(ans & top) / len(ans))
            full_at[k].append(1.0 if ans <= top else 0.0)

    label = args.label or Path(args.predictions).stem
    cols = " | ".join(
        f"{sum(rec_at[k])/n_scored:.3f} / {sum(full_at[k])/n_scored:.3f}"
        for k in SCORE_KS)
    print(f"scored {n_scored}/{len(ev)} goals, invalid picks {invalid}")
    print(f"| {label} | {cols} |")
    print("(cells: mean recall@k / full-recall rate@k for k = "
          + ", ".join(map(str, SCORE_KS)) + ")")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    p = sub.add_parser("baselines")
    p.add_argument("--dumpdir",
                   default=str(HERE / "stripped_workdir" / "dumps"))
    p.set_defaults(fn=cmd_baselines)
    p = sub.add_parser("strip")
    p.add_argument("--workdir", required=True)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--hard-timeout", type=int, default=600)
    p.set_defaults(fn=cmd_strip)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    p = sub.add_parser("score")
    p.add_argument("predictions")
    p.add_argument("--label")
    p.set_defaults(fn=cmd_score)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

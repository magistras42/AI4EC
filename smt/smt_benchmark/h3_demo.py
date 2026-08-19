#!/usr/bin/env python3
"""
h3_demo.py — H3 pilot: can an LLM pick the winning SMT solver from an
EasyCrypt file's *content alone*?

RQ3's conditional hypothesis H3: on files where solver choice decides
success/failure, an LLM that only sees the proof script should be able to
predict which solver wins. This is a pilot — it builds the dataset from the
benchmark's clean serial results and provides blind prompts + a scorer. The
prediction step itself is done by an external LLM (blind to the labels);
this script never reveals the answer inside a prompt.

Dataset (from timing.csv, the uncontended serial grid):
  * single_winner : exactly one of Z3/CVC5/Alt-Ergo solves  -> clean 3-way label
  * disagree      : some solvers solve, some fail (broader, multi-label)

Usage:
  python3 h3_demo.py build [--timing results/timing.csv] [--out results/h3]
      -> writes results/h3/dataset.json  and  results/h3/prompts/<id>.txt
  python3 h3_demo.py score --preds results/h3/predictions.json
      -> accuracy vs the 1/3 random baseline, plus a confusion table

predictions.json format (produced by the blind LLM):
  { "<file>": "Z3" | "CVC5" | "Alt-Ergo", ... }
"""

import argparse
import json
import re
from pathlib import Path

from bench_smt import compute_stats

SOLVERS = ["Alt-Ergo", "Z3", "CVC5"]
CORPUS = Path("corpus")

PROMPT_TEMPLATE = """\
You are choosing an SMT solver for an EasyCrypt proof script.

Exactly one of these three solvers discharges every `smt`/`auto` goal in the
file below within a 10-second per-call timeout; the other two FAIL on at least
one goal. The candidates are:
  - Z3
  - CVC5
  - Alt-Ergo

Using ONLY the content of the file (its lemmas, operators, types, and the
shape of the goals), predict which single solver succeeds.

Answer with a JSON object on the last line, exactly:
  {{"solver": "Z3"}}  (or "CVC5" or "Alt-Ergo")
Give one or two sentences of reasoning first, then the JSON line.

--- FILE: {file} ---
{content}
"""


def load_rows(path):
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build(timing, outdir):
    rows = load_rows(timing)
    st = compute_stats(rows, SOLVERS)
    # n_smt tokens live in sweep.csv; best-effort join if present
    n_smt = {}
    sweep = Path("results/sweep.csv")
    if sweep.exists():
        for r in load_rows(sweep):
            n_smt.setdefault(r["file"], r.get("n_smt_tokens", "?"))

    single = [{"file": f, "winner": w,
               "n_smt": n_smt.get(f, "?")}
              for f, w in st["exactly_one"].items()]
    # disagree but not single-winner: >=2 solve or >=2 fail (multi-label)
    single_files = {d["file"] for d in single}
    by = {}
    for r in rows:
        by.setdefault(r["file"], {})[r["solver"]] = r

    def ok(f, s):
        return f in by and s in by[f] and by[f][s]["ok"] == "1"

    disagree_multi = [
        {"file": f,
         "winners": [s for s in SOLVERS if ok(f, s)],
         "losers": [s for s in SOLVERS if s in by.get(f, {}) and not ok(f, s)],
         "n_smt": n_smt.get(f, "?")}
        for f in st["disagree"] if f not in single_files]

    out = Path(outdir)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    dataset = {"single_winner": single, "disagree_multi": disagree_multi}
    (out / "dataset.json").write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8")

    # write one blind prompt per single-winner case (the clean 3-way task)
    for d in single:
        content = (CORPUS / d["file"]).read_text(encoding="utf-8",
                                                 errors="replace")
        pid = re.sub(r"[^A-Za-z0-9]+", "_", d["file"]).strip("_")
        (out / "prompts" / f"{pid}.txt").write_text(
            PROMPT_TEMPLATE.format(file=d["file"], content=content),
            encoding="utf-8")

    print(f"single-winner (clean 3-way H3 cases): {len(single)}")
    for d in single:
        print(f"  {d['file']}  -> {d['winner']}  (smt tokens: {d['n_smt']})")
    print(f"disagree-but-not-single (multi-label): {len(disagree_multi)}")
    print(f"\nwrote {out/'dataset.json'} and {len(single)} blind prompt(s) "
          f"to {out/'prompts'}/")
    if len(single) < 5:
        print("\nNOTE: pilot sample is small — treat accuracy as a "
              "feasibility signal, not a statistic.")


def score(preds_path, outdir):
    dataset = json.loads((Path(outdir) / "dataset.json").read_text())
    preds = json.loads(Path(preds_path).read_text())
    single = dataset["single_winner"]
    n = len(single)
    correct = 0
    print("=== H3 pilot — single-winner prediction ===")
    print(f"{'file':<52} {'true':<10} {'pred':<10} hit")
    conf = {}
    for d in single:
        f, true = d["file"], d["winner"]
        p = preds.get(f, "(none)")
        hit = (p == true)
        correct += hit
        conf.setdefault((true, p), 0)
        conf[(true, p)] += 1
        print(f"{f:<52} {true:<10} {p:<10} {'Y' if hit else 'n'}")
    acc = correct / n if n else 0.0
    print(f"\naccuracy: {correct}/{n} = {acc:.0%}   "
          f"(random 3-way baseline = 33%)")
    print("verdict:", "above chance — worth scaling H3"
          if acc > 1 / 3 else "at/below chance on this tiny sample")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--timing", default="results/timing.csv")
    b.add_argument("--out", default="results/h3")
    s = sub.add_parser("score")
    s.add_argument("--preds", default="results/h3/predictions.json")
    s.add_argument("--out", default="results/h3")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.timing, args.out)
    else:
        score(args.preds, args.out)


if __name__ == "__main__":
    main()

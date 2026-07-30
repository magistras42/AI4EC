#!/usr/bin/env python3
"""
estimate_repair_difficulty.py

Per-LEMMA (not per-repo) repair difficulty estimate, meant to be called at
actual repair time -- once you have a specific broken lemma in hand -- not
as part of the repo-level benchmark ladder (compute_exposure_score.py).
Different granularity, different invocation point: this is a repair-time
tool, that one is a benchmark-construction tool.

Combines three signals per lemma:

1. depth: approximate tactic-invocation count (see count_tactics in
   compute_exposure_score.py -- imported here rather than duplicated).
2. external_dependency_fan_out: count of DISTINCT qualified identifiers
   (Theory.lemma_name-style references) the proof relies on. A shallow
   proof touching five different external theories is a harder repair
   target than a long, entirely self-contained one, since an LLM repairing
   it needs all five theories' current state in context -- this is a
   different axis from depth, not a substitute for it.
3. automation_ratio: same fragile-tactic-reliance signal as
   compute_exposure_score.py, at lemma granularity instead of repo
   granularity.

Usage:
    python estimate_repair_difficulty.py --file broken_proof.ec
    python estimate_repair_difficulty.py --file broken_proof.ec --lemma some_lemma_name
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Reuse the tactic-counting and proof-block regexes from the repo-level
# scorer rather than duplicating them -- keeps the two tools' notion of
# "depth" consistent.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compute_exposure_score import (  # noqa: E402
    PROOF_BLOCK_RE, INLINE_BY_RE, count_tactics, FRAGILE_TACTICS, TACTIC_TOKEN_RE,
    strip_comments,
)

# Module.identifier -- a qualified reference to something defined in
# another theory. Capitalized module name is EasyCrypt convention (theory/
# module names are capitalized; local identifiers are not), so this
# shouldn't false-positive on ordinary local tactic arguments.
QUALIFIED_REF_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\.([a-z_][A-Za-z0-9_']*)\b")

# depth/fan-out weights for the combined difficulty score. Fan-out is
# weighted higher per unit than depth: an external dependency means the
# repair may require pulling in another theory's CURRENT state (which
# could itself have changed), not just re-deriving a tactic sequence
# locally -- a qualitatively different, usually larger, repair cost.
DEPTH_WEIGHT = 1.0
FAN_OUT_WEIGHT = 2.0
AUTOMATION_MULTIPLIER = 1.0


def find_lemma_blocks(text: str) -> list[dict]:
    """Extract each lemma's name (best-effort), proof body text, and span,
    covering both explicit proof...qed/abort/admit blocks and the inline
    'lemma ... by tactic.' shorthand.

    Callers should pass comment-stripped text (see strip_comments, imported
    above) -- otherwise a commented-out `(* lemma foo. proof. ... qed. *)`
    block false-positive-matches here the same way it does in
    compute_exposure_score.py's extract_lemma_depths."""
    blocks = []

    proof_spans = []
    for m in PROOF_BLOCK_RE.finditer(text):
        proof_spans.append(m.span())
        name = _nearest_lemma_name(text, m.start())
        blocks.append({"name": name, "body": m.group(1), "extra": 0, "span": m.span()})

    for m in INLINE_BY_RE.finditer(text):
        if any(start <= m.start() < end for start, end in proof_spans):
            continue
        name = _nearest_lemma_name(text, m.start())
        blocks.append({"name": name, "body": m.group(1), "extra": 1, "span": m.span()})

    return blocks


def _nearest_lemma_name(text: str, pos: int) -> str | None:
    """Best-effort: find the 'lemma <name>' immediately preceding pos."""
    preceding = text[:pos]
    m = None
    # (?:nosmt\s+)? -- without it, `lemma nosmt foo` extracts "nosmt" as
    # the lemma name and --lemma foo can never match.
    for m in re.finditer(r"\blemma\s+(?:nosmt\s+)?([A-Za-z_][A-Za-z0-9_']*)", preceding):
        pass  # keep the LAST match before pos
    return m.group(1) if m else None


def compute_fan_out(body: str) -> dict:
    refs = QUALIFIED_REF_RE.findall(body)
    distinct_theories = sorted({theory for theory, _ in refs})
    distinct_refs = sorted({f"{theory}.{ident}" for theory, ident in refs})
    return {
        "distinct_theories": distinct_theories,
        "distinct_qualified_refs": distinct_refs,
        "fan_out_count": len(distinct_theories),
    }


def compute_lemma_automation(body: str) -> dict:
    total = 0
    fragile = 0
    for m in TACTIC_TOKEN_RE.finditer(body):
        total += 1
        if m.group(1) in FRAGILE_TACTICS:
            fragile += 1
    ratio = (fragile / total) if total else 0.0
    return {"fragile_tactic_count": fragile, "total_tactic_count": total, "ratio": ratio}


def score_lemma(block: dict) -> dict:
    depth = count_tactics(block["body"], extra=block["extra"])
    fan_out = compute_fan_out(block["body"])
    automation = compute_lemma_automation(block["body"])

    automation_factor = 1.0 + automation["ratio"] * AUTOMATION_MULTIPLIER
    difficulty = (DEPTH_WEIGHT * depth + FAN_OUT_WEIGHT * fan_out["fan_out_count"]) * automation_factor

    return {
        "lemma": block["name"],
        "depth": depth,
        "fan_out": fan_out,
        "automation_reliance": automation,
        "difficulty_score": round(difficulty, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="path to the .ec file containing the lemma")
    ap.add_argument("--lemma", default=None, help="only report this specific lemma name (default: all in file)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        sys.exit(1)
    text = strip_comments(path.read_text(errors="ignore"))

    blocks = find_lemma_blocks(text)
    if args.lemma:
        blocks = [b for b in blocks if b["name"] == args.lemma]
        if not blocks:
            print(f"error: lemma '{args.lemma}' not found in {path} (or its proof form wasn't recognized)",
                  file=sys.stderr)
            sys.exit(1)

    results = [score_lemma(b) for b in blocks]
    out = json.dumps(results if len(results) != 1 else results[0], indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()

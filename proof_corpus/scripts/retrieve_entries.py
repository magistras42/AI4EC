#!/usr/bin/env python3
"""
retrieve_relevant_entries.py

Given the structured changelog produced by process_changelog.py, a source
and target EasyCrypt version, and a (broken) proof script, return the small
set of changelog entries most likely to explain the break -- ready to be
injected into a proof-repair prompt.

Filtering strategy (see design discussion): 
  Tier A (always kept): high-relevance mechanism_change entries in range --
    these are structural changes (cloning, imports, module system) that can
    break a proof without the proof textually referencing anything that
    changed, so identifier overlap can't be relied on to surface them.
  Tier B (kept on match): entries whose identifiers/tactic/theory fields
    overlap with tokens found in the proof script (exact token match, not
    substring).
  Tier C (dropped): everything else, especially kind in
    {internal, documentation} or relevance == "low".

Results are ranked: Tier A first, then Tier B by (# overlapping
identifiers, proximity to target version), and truncated to --top-n.

Usage:
    python retrieve_relevant_entries.py \
        --changelog changelog.yaml \
        --proof broken_proof.ec \
        --source-version r2025.02 \
        --target-version r2025.10 \
        --top-n 12
"""

import argparse
import json
import re
import sys

import yaml

# Tokens that will appear in almost every proof script and carry no
# discriminative signal for matching against changelog identifiers.
GENERIC_TOKENS = {
    "proof", "qed", "lemma", "have", "move", "apply", "by", "smt", "auto",
    "case", "if", "then", "else", "let", "in", "with", "end", "module",
    "op", "theory", "import", "require", "export", "type", "axiom",
}


def load_changelog(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def tokenize_proof(text: str) -> set[str]:
    """Extract candidate identifier tokens from a proof script. Exact-token
    matching only (case-sensitive), since EasyCrypt identifiers are exact
    tokens -- we deliberately do NOT do substring/fuzzy matching here to
    avoid false positives like 'map' matching 'map1'."""
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_']*\b", text)
    return {t for t in tokens if t.lower() not in GENERIC_TOKENS and len(t) > 1}


def releases_in_range(releases: list[dict], source_version: str, target_version: str) -> list[dict]:
    """Order releases chronologically by published_at and return the slice
    strictly after source_version up to and including target_version.
    Falls back to returning everything (with a warning) if either tag isn't
    found, rather than silently returning nothing."""
    ordered = sorted(releases, key=lambda r: r.get("published_at") or "")
    versions = [r["version"] for r in ordered]

    if source_version not in versions or target_version not in versions:
        missing = [v for v in (source_version, target_version) if v not in versions]
        print(f"warning: version(s) not found in changelog: {missing}. "
              f"Using full changelog range instead.", file=sys.stderr)
        return ordered

    i_src = versions.index(source_version)
    i_tgt = versions.index(target_version)
    lo, hi = sorted((i_src, i_tgt))
    # entries strictly after the source version, up to and including target
    return ordered[lo + 1: hi + 1]


def score_entries(in_range: list[dict], proof_tokens: set[str], top_n: int) -> list[dict]:
    tier_a = []
    tier_b = []

    n_releases = len(in_range)
    for pos, rel in enumerate(in_range):
        # distance from target release (0 = the target release itself)
        proximity = n_releases - 1 - pos
        for entry in rel.get("entries", []):
            kind = entry.get("kind")
            relevance = entry.get("relevance")

            if kind == "mechanism_change" and relevance == "high":
                tier_a.append({**entry, "version": rel["version"],
                               "reason": "structural change in range",
                               "overlap": [], "proximity": proximity})
                continue

            if relevance == "low" or kind in ("internal", "documentation"):
                continue

            entry_ids = {str(i) for i in (entry.get("identifiers") or [])}
            overlap = sorted(entry_ids & proof_tokens)
            if overlap:
                tier_b.append({**entry, "version": rel["version"],
                               "reason": "identifier match",
                               "overlap": overlap, "proximity": proximity})

    tier_b.sort(key=lambda e: (-len(e["overlap"]), -e["proximity"]))

    combined = tier_a + tier_b
    return combined[:top_n]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--changelog", required=True, help="path to changelog.yaml from process_changelog.py")
    ap.add_argument("--proof", required=True, help="path to the broken .ec proof script")
    ap.add_argument("--source-version", required=True, help="tag the proof was written against")
    ap.add_argument("--target-version", required=True, help="tag the proof needs to work on")
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--out", default=None, help="write JSON result here (default: stdout)")
    args = ap.parse_args()

    changelog = load_changelog(args.changelog)
    with open(args.proof, encoding="utf-8") as f:
        proof_text = f.read()

    proof_tokens = tokenize_proof(proof_text)
    in_range = releases_in_range(changelog["releases"], args.source_version, args.target_version)
    results = score_entries(in_range, proof_tokens, args.top_n)

    # Compact prompt-ready form: drop bookkeeping fields the repair LLM
    # doesn't need (title, summary stay; internal 'needs_llm' etc already gone).
    prompt_ready = [
        {
            "version": r["version"],
            "id": r.get("id"),
            "kind": r.get("kind"),
            "repair_hint": r.get("repair_hint"),
            "matched_identifiers": r.get("overlap"),
            "reason": r.get("reason"),
        }
        for r in results
    ]

    out = json.dumps(prompt_ready, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {len(prompt_ready)} entries to {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
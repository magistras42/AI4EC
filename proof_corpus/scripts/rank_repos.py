#!/usr/bin/env python3
"""
rank_repos.py

Read the JSON produced by compute_exposure_score.py (either a single repo
object, from --repo-path/--repo-url, or an array, from --csv) and produce a
ranked markdown "proof repair ladder": easiest (lowest exposure score) to
hardest, with the key contributing factors shown per repo so the ranking
isn't a black box.

Repos that failed to clone (an "error" key, no score) are listed separately
at the bottom rather than silently dropped, so a CSV batch's failures stay
visible.

Usage:
    python rank_repos.py --in exposure_results.json --out ladder.md
"""

import argparse
import json
import sys
from pathlib import Path


def load_results(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def fmt_version(detected: dict) -> str:
    version = detected.get("version") or "?"
    confidence = detected.get("confidence", "?")
    return f"{version} ({confidence})"


def fmt_solver_info(hits: list[dict]) -> str:
    if not hits:
        return "-"
    return ", ".join(f"{h['solver']} {h['version']}" for h in hits)


def build_markdown(results: list[dict], target_version: str | None) -> str:
    scored = [r for r in results if "total_exposure_score" in r]
    failed = [r for r in results if "total_exposure_score" not in r]
    # Plain score sort: predates_changelog's contribution to the score is
    # already scaled by measured proof complexity (see
    # compute_exposure_score.py's score_repo), so a shallow old repo can
    # legitimately rank easier than a deep, automation-heavy modern one.
    scored.sort(key=lambda r: r["total_exposure_score"])

    lines = []
    lines.append("# Proof Repair Difficulty Ladder")
    lines.append("")
    if target_version:
        lines.append(f"Target EasyCrypt version: `{target_version}`")
        lines.append("")
    lines.append(f"{len(scored)} repositor{'y' if len(scored) == 1 else 'ies'} ranked, "
                 f"easiest to hardest.{' ' + str(len(failed)) + ' failed to score (see below).' if failed else ''}")
    lines.append("")

    lines.append("| Rank | Repository | Exposure Score | Detected Source Version | "
                 "Releases Crossed | Breaking-Change Score | Automation Reliance | Proof Complexity | "
                 "SMT Solvers Pinned | Flag |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for rank, r in enumerate(scored, 1):
        name = r.get("name") or Path(r["repo_path"]).name
        url = r.get("url")
        name_cell = f"[{name}]({url})" if url else name

        score = r["total_exposure_score"]
        version_cell = fmt_version(r.get("detected_source_version", {}))
        crossed = ", ".join(r.get("releases_crossed", [])) or "-"
        breaking = r.get("breaking_change_score", {}).get("total", 0)
        automation = r.get("automation_reliance", {})
        automation_cell = f"{automation.get('ratio', 0):.2f} " \
                          f"({automation.get('fragile_tactic_count', 0)}/{automation.get('total_tactic_count', 0)})"
        complexity = r.get("proof_complexity", {})
        complexity_cell = f"{complexity.get('complexity_score', 0):.1f} " \
                          f"({complexity.get('lemma_count', 0)} lemmas, {complexity.get('long_lemma_count', 0)} long)"
        solver_cell = fmt_solver_info(r.get("smt_solver_info", []))
        flag_cell = "⚠️ predates changelog" if r.get("predates_changelog") else \
                   ("❓ version unknown" if r.get("version_unknown") else "-")

        lines.append(f"| {rank} | {name_cell} | {score} | {version_cell} | {crossed} | "
                     f"{breaking} | {automation_cell} | {complexity_cell} | {solver_cell} | {flag_cell} |")

    if failed:
        lines.append("")
        lines.append("## Not scored (clone or setup failed)")
        lines.append("")
        lines.append("| Repository | Error |")
        lines.append("|---|---|")
        for r in failed:
            name = r.get("name") or Path(r.get("repo_path", "?")).name
            lines.append(f"| {name} | {r.get('error', 'unknown error')} |")

    lines.append("")
    lines.append("_Lower exposure score = easier repair target. Score combines weighted "
                 "changelog-breaking-change count amplified by automation-reliance, plus a "
                 "proof-complexity-scaled penalty for repos whose version predates our changelog's "
                 "earliest cataloged release (r2022.04) -- see compute_exposure_score.py for the "
                 "formula. \"Predates changelog\" is informational, not an automatic override: a "
                 "shallow old repo can rank easier than a deep, automation-heavy modern one._")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="infile", required=True, help="JSON output from compute_exposure_score.py")
    ap.add_argument("--out", required=True, help="path to write the markdown ladder")
    args = ap.parse_args()

    results = load_results(args.infile)
    if not results:
        print("no results found in input file", file=sys.stderr)
        sys.exit(1)

    target_version = next((r.get("target_version") for r in results if r.get("target_version")), None)
    markdown = build_markdown(results, target_version)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
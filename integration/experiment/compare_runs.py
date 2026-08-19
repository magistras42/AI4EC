"""Score a paired A/B across several seeds per arm.

`repair_metrics.hint_uptake` asks whether an identifier a hint named turns up
in a tactic EasyCrypt accepted. That is a proxy and the metric's own docstring
says so: the model might have reached that name anyway. Showing the knowledge
base *helps* needs the counterfactual -- the same corpus, the same seeds,
without it -- which `--no-changelog-hints` now produces.

The reason this is a module and not a one-line `jq` is the variance. Run-to-run
spread under **identical** configuration reached 11-vs-1 accepted tactics on
this corpus (ELGAMAL_E2E_RESULTS.md §10.1), which is larger than any between-arm
difference a single pair could show. Two runs cannot answer the question no
matter how they are compared, so this tool refuses to pretend otherwise: it
reports the per-seed spread next to the between-arm gap and says plainly when
the second swamps the first.

No EasyCrypt, no LLM, no network -- it only reads `summary.json` files, so it
can score a finished experiment at any time.

CLI::

    python3 -m integration.experiment.compare_runs \\
        --arm hints-on  runs/on-seed1  runs/on-seed2  runs/on-seed3 \\
        --arm hints-off runs/off-seed1 runs/off-seed2 runs/off-seed3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: What gets compared. Each is (label, how to read it out of a summary).
METRICS: dict[str, Any] = {
    "success_rate": lambda s: (
        s["successes"] / s["trials_run"] if s.get("trials_run") else None
    ),
    "successes": lambda s: s.get("successes"),
    "tactics_accepted": lambda s: (
        (s.get("repair_metrics") or {}).get("replay", {}).get("total_tactics_accepted")
    ),
    "fully_replayed_rate": lambda s: (
        (s.get("repair_metrics") or {}).get("replay", {}).get("fully_replayed_rate")
    ),
    "import_repair_resolved_rate": lambda s: (
        (s.get("repair_metrics") or {}).get("import_repair", {}).get("resolved_rate")
    ),
    "hint_uptake_rate": lambda s: (
        (s.get("repair_metrics") or {}).get("hint_uptake", {}).get("rate_among_scorable")
    ),
    "cost_usd": lambda s: (s.get("estimated_cost") or {}).get("usd"),
}


@dataclass
class Arm:
    label: str
    runs: list[dict[str, Any]] = field(default_factory=list)
    sources: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def values(self, metric: str) -> list[float]:
        read = METRICS[metric]
        out = []
        for summary in self.runs:
            try:
                value = read(summary)
            except (KeyError, TypeError, ZeroDivisionError):
                value = None
            if value is not None:
                out.append(float(value))
        return out


def load_summary(path: Path) -> dict[str, Any] | None:
    """Read one run's summary, whether given the directory or the file."""
    candidate = path / "summary.json" if path.is_dir() else path
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else None,
    }


def compare(arms: list[Arm]) -> dict[str, Any]:
    """Per-metric spread within each arm, and the gap between arms.

    ``conclusive`` is the whole point. It is True only when the difference
    between the arm means exceeds the widest within-arm range -- i.e. when the
    effect is bigger than the noise the same configuration produces on its
    own. With one run per arm it is never True, because a single run has no
    measurable spread to compare against.
    """
    report: dict[str, Any] = {
        "arms": [
            {"label": a.label, "runs": len(a.runs),
             "sources": [str(p) for p in a.sources], "notes": a.notes}
            for a in arms
        ],
        "metrics": {},
    }

    for metric in METRICS:
        per_arm = {a.label: _spread(a.values(metric)) for a in arms}
        entry: dict[str, Any] = {"by_arm": per_arm}

        means = [(label, s["mean"]) for label, s in per_arm.items() if s.get("n")]
        if len(means) == 2:
            (label_a, mean_a), (label_b, mean_b) = means
            gap = abs(mean_a - mean_b)
            widest = max(
                (s["max"] - s["min"]) for s in per_arm.values() if s.get("n")
            )
            entry["difference"] = round(mean_b - mean_a, 4)
            entry["direction"] = f"{label_b} - {label_a}"
            entry["widest_within_arm_range"] = round(widest, 4)
            singleton = any(s.get("n", 0) < 2 for s in per_arm.values())
            entry["conclusive"] = bool(gap > widest) and not singleton
            if singleton:
                entry["caveat"] = (
                    "at least one arm has a single run: no within-arm spread "
                    "to compare the difference against"
                )
            elif not entry["conclusive"]:
                entry["caveat"] = (
                    f"the {gap:.4g} gap is inside the {widest:.4g} spread the "
                    "same configuration produces on its own"
                )
        report["metrics"][metric] = entry

    report["conclusive_metrics"] = sorted(
        name for name, entry in report["metrics"].items()
        if entry.get("conclusive")
    )
    return report


def check_pairing(arms: list[Arm]) -> list[str]:
    """Warn about anything that makes the arms not actually comparable."""
    warnings: list[str] = []
    seeds = {a.label: sorted(
        {r.get("arm", {}).get("seed") for r in a.runs} - {None}
    ) for a in arms}
    distinct = {tuple(v) for v in seeds.values() if v}
    if len(distinct) > 1:
        warnings.append(f"arms did not use the same seeds: {seeds}")

    for arm in arms:
        models = {r.get("arm", {}).get("model") for r in arm.runs} - {None}
        if len(models) > 1:
            warnings.append(f"{arm.label}: mixed models {sorted(models)}")
        specs = {r.get("spec_name") for r in arm.runs} - {None}
        if len(specs) > 1:
            warnings.append(f"{arm.label}: mixed specs {sorted(specs)}")
        if any(r.get("budget_stopped") for r in arm.runs):
            warnings.append(
                f"{arm.label}: at least one run hit its spend cap and stopped "
                "early -- its success rate is not comparable"
            )

    settings = {
        arm.label: {r.get("arm", {}).get("changelog_hints") for r in arm.runs}
        for arm in arms
    }
    flat = {label: values for label, values in settings.items() if values - {None}}
    if len(arms) == 2 and len({frozenset(v) for v in flat.values()}) == 1 and flat:
        warnings.append(
            "both arms ran with the same changelog_hints setting -- this is "
            "not an A/B, it is the same configuration twice"
        )
    return warnings


def render(report: dict[str, Any], warnings: list[str]) -> str:
    lines: list[str] = []
    for arm in report["arms"]:
        lines.append(f"{arm['label']}: {arm['runs']} run(s)")
        for source in arm["sources"]:
            lines.append(f"    {source}")
    lines.append("")

    width = max(len(name) for name in report["metrics"])
    for name, entry in report["metrics"].items():
        parts = []
        for label, spread in entry["by_arm"].items():
            if not spread.get("n"):
                parts.append(f"{label}=n/a")
                continue
            parts.append(
                f"{label}={spread['mean']:g} "
                f"[{spread['min']:g}..{spread['max']:g}]"
            )
        verdict = ""
        if "conclusive" in entry:
            verdict = "  CONCLUSIVE" if entry["conclusive"] else "  (within noise)"
        lines.append(f"  {name:<{width}}  {'  '.join(parts)}{verdict}")

    lines.append("")
    if report["conclusive_metrics"]:
        lines.append(
            "Differences larger than the within-arm spread: "
            + ", ".join(report["conclusive_metrics"])
        )
    else:
        lines.append(
            "No metric separated the arms by more than the spread the same "
            "configuration produces on its own. That is a statement about "
            "power, not about the knowledge base: add seeds per arm."
        )
    for warning in warnings:
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare paired experiment arms across several seeds",
    )
    parser.add_argument(
        "--arm", action="append", nargs="+", metavar=("LABEL", "RUN_DIR"),
        required=True,
        help="arm label followed by one or more run directories; repeatable",
    )
    parser.add_argument("--json", type=Path, default=None,
                        help="also write the full report here")
    args = parser.parse_args(argv)

    arms: list[Arm] = []
    for entry in args.arm:
        label, *paths = entry
        arm = Arm(label=label)
        for raw in paths:
            path = Path(raw)
            summary = load_summary(path)
            if summary is None:
                arm.notes.append(f"unreadable: {path}")
                print(f"warning: no readable summary.json at {path}",
                      file=sys.stderr)
                continue
            arm.runs.append(summary)
            arm.sources.append(path)
        arms.append(arm)

    if not any(a.runs for a in arms):
        print("error: no readable runs", file=sys.stderr)
        return 1

    report = compare(arms)
    warnings = check_pairing(arms)
    report["warnings"] = warnings
    print(render(report, warnings))

    if args.json:
        args.json.write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

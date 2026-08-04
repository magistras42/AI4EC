"""Aggregate per-trial repair artifacts into run-level metrics.

Roadmap item W8 in [`docs/PROOF_REPAIR_HANDOFF.md`](../../docs/PROOF_REPAIR_HANDOFF.md).
The replay-bootstrap mode already wrote rich per-trial evidence --
``bootstrap_result.json``, ``import_repair.json``, ``repair_hints_hop.json``,
``ec_versions.json`` -- and nothing ever rolled it up, so attempt/success
rates were recorded but never reported. Without this, W2-W5 cannot be shown
to help or not help: the knowledge base's effect was unmeasurable even when
it was working.

Everything here is derived by reading files the trials already wrote. No
EasyCrypt calls, no LLM calls, no network -- so it is safe to re-run over an
old output directory to score a completed experiment after the fact.

Missing or malformed artifacts are skipped rather than raising: a mutation-mode
run has none of these files, and a crashed trial may have written only some.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _name_pattern(name: str) -> re.Pattern[str]:
    """Whole-identifier match, so `map` cannot match inside `map1`."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


def _hinted_identifiers(hints_text: str) -> set[str]:
    """Identifiers a rendered hint block actually named.

    Deliberately crude: the rendered block is prose plus identifier lists, so
    this looks for EasyCrypt-shaped names (CamelCase theories, qualified
    ``Theory.name`` paths) and ignores ordinary English words by requiring
    either a dot or an internal capital.
    """
    candidates = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b", hints_text))
    candidates |= {
        token
        for token in re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", hints_text)
        if any(c.islower() for c in token)
    }
    return candidates


def _accepted_tactics(agent_log: dict[str, Any]) -> list[str]:
    """Tactics EasyCrypt accepted during the solver loop.

    ``AgentRunLog`` writes ``{"source", "work_copy", "events": [...]}`` where
    each per-step record is ``{"event": "iteration", "action": ..., "outcome":
    ...}``. An earlier version of this function read a top-level
    ``"iterations"`` key that does not exist, so it silently returned [] for
    every real run and ``hint_uptake`` was structurally pinned at 0.0 --
    measuring nothing while looking like a finding. ``iterations`` is still
    accepted as a fallback for hand-built fixtures.
    """
    entries = agent_log.get("events")
    if entries is None:
        entries = agent_log.get("iterations") or []
    tactics: list[str] = []
    for entry in entries:
        if entry.get("event") not in (None, "iteration"):
            continue
        if entry.get("action") == "tactic" and entry.get("outcome") in {
            "accepted",
            "complete",
        }:
            tactic = entry.get("tactic")
            if tactic:
                tactics.append(str(tactic))
    return tactics


def collect_trial_repair_metrics(trial_dir: Path) -> dict[str, Any] | None:
    """Read one trial's repair artifacts. None when the trial wrote none."""
    bootstrap = _read_json(trial_dir / "bootstrap_result.json")
    import_repair = _read_json(trial_dir / "import_repair.json")
    hop = _read_json(trial_dir / "repair_hints_hop.json")
    versions = _read_json(trial_dir / "ec_versions.json")
    hints_path = trial_dir / "changelog_hints.txt"
    # A rendered hint block is itself a repair artifact: a trial that was
    # given changelog evidence is a repair trial even if every other file is
    # missing, and its hint uptake is exactly what W8 exists to measure.
    if not any((bootstrap, import_repair, hop, versions, hints_path.is_file())):
        return None

    metrics: dict[str, Any] = {"trial_dir": trial_dir.name}

    if bootstrap:
        accepted = int(bootstrap.get("accepted_count", 0) or 0)
        total = int(bootstrap.get("total_count", 0) or 0)
        metrics["replay"] = {
            "accepted_count": accepted,
            "total_count": total,
            # How much of the OLD proof still holds against the current build.
            # The honest headline number for "how bad is version drift here".
            "replayed_fraction": round(accepted / total, 4) if total else None,
            "fully_replayed": bool(bootstrap.get("fully_replayed")),
            "failed_tactic": bootstrap.get("failed_tactic", ""),
        }

    if import_repair:
        kept = [a for a in import_repair.get("applied", []) if a.get("kept")]
        metrics["import_repair"] = {
            "attempted": True,
            "changed": bool(import_repair.get("changed")),
            "improved": bool(import_repair.get("improved")),
            "loads_after": bool(import_repair.get("loads_after")),
            "considered_count": len(import_repair.get("considered", []) or []),
            "kept_count": len(kept),
            "kept_ids": [a.get("id") for a in kept],
            "error_line_before": import_repair.get("error_line_before", -1),
            "error_line_after": import_repair.get("error_line_after", -1),
        }

    if hop:
        metrics["changelog_hop"] = hop.get("changelog_hop_matched_version")

    if versions:
        metrics["ec_versions"] = {
            "source": (versions.get("source") or {}).get("version"),
            "source_method": (versions.get("source") or {}).get("method"),
            "target": (versions.get("target") or {}).get("version"),
            "target_method": (versions.get("target") or {}).get("method"),
        }

    # Did the model's accepted repair actually use what the hint pointed at?
    # This is the closest available proxy for "the knowledge base helped",
    # short of a counterfactual run with hints disabled.
    agent_log = _read_json(trial_dir / "agent_log.json")
    hints_text = ""
    if hints_path.is_file():
        try:
            hints_text = hints_path.read_text(encoding="utf-8")
        except OSError:
            hints_text = ""
    if agent_log and hints_text:
        hinted = _hinted_identifiers(hints_text)
        accepted = _accepted_tactics(agent_log)
        accepted_text = "\n".join(accepted)
        used = sorted(
            name for name in hinted if _name_pattern(name).search(accepted_text)
        )
        metrics["hint_uptake"] = {
            "hinted_identifier_count": len(hinted),
            "used_in_accepted_tactics": used,
            "any_used": bool(used),
            # Load-bearing denominator. With zero accepted tactics, `any_used`
            # is False by construction and says nothing about whether the hints
            # were useful -- the model simply never landed anything for a hint
            # to appear in. Reported so a 0 rate can be read correctly instead
            # of as evidence the knowledge base was ignored.
            "accepted_tactic_count": len(accepted),
            "scorable": bool(accepted),
        }

    return metrics


def aggregate_repair_metrics(output_dir: Path) -> dict[str, Any]:
    """Roll every trial's repair artifacts into run-level metrics.

    Returns ``{}`` when no trial wrote any repair artifact, so mutation and
    informal runs keep a clean ``summary.json`` instead of a block of nulls.
    """
    trials_root = Path(output_dir) / "trials"
    if not trials_root.is_dir():
        return {}

    per_trial: list[dict[str, Any]] = []
    for trial_dir in sorted(trials_root.iterdir()):
        if not trial_dir.is_dir():
            continue
        metrics = collect_trial_repair_metrics(trial_dir)
        if metrics is not None:
            per_trial.append(metrics)

    if not per_trial:
        return {}

    replays = [m["replay"] for m in per_trial if "replay" in m]
    fractions = [
        r["replayed_fraction"] for r in replays if r["replayed_fraction"] is not None
    ]
    imports = [m["import_repair"] for m in per_trial if "import_repair" in m]
    uptakes = [m["hint_uptake"] for m in per_trial if "hint_uptake" in m]

    hops: dict[str, int] = {}
    for m in per_trial:
        version = m.get("changelog_hop")
        key = version if version else "(no match)"
        hops[key] = hops.get(key, 0) + 1

    summary: dict[str, Any] = {"trials_with_repair_artifacts": len(per_trial)}

    if replays:
        fully = sum(1 for r in replays if r["fully_replayed"])
        summary["replay"] = {
            "trials": len(replays),
            # The zero-LLM cheap win: the old proof still compiles verbatim.
            "fully_replayed": fully,
            "fully_replayed_rate": round(fully / len(replays), 4),
            "mean_replayed_fraction": _mean(fractions),
            "min_replayed_fraction": min(fractions) if fractions else None,
            "max_replayed_fraction": max(fractions) if fractions else None,
            "total_tactics_accepted": sum(r["accepted_count"] for r in replays),
            "total_tactics": sum(r["total_count"] for r in replays),
        }

    if imports:
        improved = sum(1 for i in imports if i["improved"])
        loaded = sum(1 for i in imports if i["loads_after"])
        advances = [
            i["error_line_after"] - i["error_line_before"]
            for i in imports
            if i["error_line_before"] >= 0 and i["error_line_after"] >= 0
        ]
        summary["import_repair"] = {
            "attempted": len(imports),
            "improved": improved,
            "improved_rate": round(improved / len(imports), 4),
            "made_file_load": loaded,
            "mean_migrations_kept": _mean([float(i["kept_count"]) for i in imports]),
            # Positive = EasyCrypt now gets further into the file before
            # complaining. The honest partial-progress measure (4.5): it does
            # not claim the proof works, only that loading advanced.
            "mean_first_error_line_advance": _mean([float(a) for a in advances]),
        }

    if uptakes:
        any_used = sum(1 for u in uptakes if u["any_used"])
        # Only trials that actually landed a tactic can evidence uptake.
        scorable = [u for u in uptakes if u.get("scorable")]
        summary["hint_uptake"] = {
            "trials_scored": len(uptakes),
            "trials_using_a_hinted_identifier": any_used,
            "rate": round(any_used / len(uptakes), 4),
            # The honest rate: denominator restricted to trials with at least
            # one accepted tactic. None when no trial qualifies, which means
            # "not measurable in this run" rather than "hints were unused".
            "trials_with_accepted_tactics": len(scorable),
            "rate_among_scorable": (
                round(
                    sum(1 for u in scorable if u["any_used"]) / len(scorable), 4
                )
                if scorable
                else None
            ),
        }

    summary["changelog_hops"] = hops
    summary["per_trial"] = per_trial
    return summary

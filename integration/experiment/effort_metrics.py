"""Re-derive §5.6/§5.7 of the research report from run artifacts.

Every number in those sections -- completion and repair rates, runtime, steps
to a valid proof, wasted edits, useless context, prompt size, and the
shannon-prover failure-mode shares -- comes out of this module. The report has
already had to retract figures twice (§6.2, §6.3), so the tables are computed
from the artifacts on every read rather than typed in by hand.

What it reads, in order of preference:

* ``events.jsonl`` -- the ``trial_finish`` records. Present even for runs that
  were killed before ``summary.json`` was written, which is 7 of 17 runs, so
  this is the primary source and ``summary.json`` is only a cross-check.
* ``trials/trial_NNN/agent_log.json`` -- the per-step iteration stream, which
  is the only place tactic, outcome, error and ranked premises are recorded.
* ``trials/trial_NNN/bootstrap_result.json`` -- replayed-prefix accounting.

Two definitions are load-bearing and easy to get wrong:

**A completion is not a repair.** ``reason == COMPLETE`` with ``steps == 0``
means the original proof replayed; the model was never called. Only
``steps > 0`` is a proof the model closed. Conflating the two is the central
measurement error of this project, so ``completion_rate`` and
``repair_success_rate`` are reported separately and never summed.

**A wasted edit is not the same as a rejected tactic.** Every tactic
submission writes the working file. It can then be rejected by EasyCrypt, be
accepted but inert (the harness removes it -- ``no_op``), or be accepted and
later undone by the agent. Only the last two are invisible to a naive
"acceptance rate", and together they are 27% of all submissions.

No EasyCrypt, no LLM, no network: it only reads finished artifacts, so it can
score a run at any time, including one that is still going.

CLI::

    python3 -m integration.experiment.effort_metrics integration/output/experiments
    python3 -m integration.experiment.effort_metrics <dir> --run run-20260810T053405Z
    python3 -m integration.experiment.effort_metrics <dir> --json metrics.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

#: Tactics that descend a proof layer or discharge locally. The split is the
#: one shannon-prover's "unconscious lowering" is about: these expose concrete
#: program structure or hand the goal to a solver, and they are cheap to write.
LOWERING_HEADS = frozenset(
    {"wp", "auto", "sp", "inline", "smt", "progress", "trivial",
     "simplify", "skip", "by"}
)

#: EasyCrypt's way of saying "that tactic does not belong at this goal". These
#: are the wrong-layer / wrong-position rejections, not argument errors.
_LAYER_ERROR_RE = re.compile(
    r"expecting a goal of the form"
    r"|conclusion is not a hoare or an equiv"
    r"|left instruction list is not empty"
    r"|invalid (?:first|last) instruction",
    re.IGNORECASE,
)

#: A tactic that names a specific proof object -- the "high-level route" whose
#: abandonment is agent flinch.
_NAMED_ROUTE_RE = re.compile(
    r"\b(?:apply|exact|rewrite|byequiv|conseq)\b[^.]*?"
    r"\b([A-Z][A-Za-z0-9_]{2,}|[a-z]+_[a-z_]{2,})"
)

_HEAD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)")

FAILED_OUTCOMES = frozenset({"failed", "rejected"})
KEPT_OUTCOMES = frozenset({"accepted", "complete"})


def tactic_head(tactic: str | None) -> str:
    match = _HEAD_RE.match(tactic or "")
    return match.group(1) if match else "?"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def iterations(trial_dir: Path) -> list[dict[str, Any]]:
    """The agent's per-step stream, in step order.

    Empty for a trial the agent never entered -- a full replay, or a case that
    failed to load. That is the common case, not an error.
    """
    log = _read_json(trial_dir / "agent_log.json")
    steps = [e for e in log.get("events", []) if e.get("event") == "iteration"]
    steps.sort(key=lambda e: e.get("step") or 0)
    return steps


def edit_disposition(steps: Iterable[dict[str, Any]]) -> Counter:
    """Where each file edit ended up: kept, reverted, inert, or undone.

    An edit counts as undone when a later ``undo`` reaches back far enough to
    cross it. Undo depth is in steps, so the comparison is positional.
    """
    steps = list(steps)
    out: Counter = Counter()
    for i, event in enumerate(steps):
        if event.get("action") != "tactic":
            continue
        outcome = event.get("outcome")
        if outcome in FAILED_OUTCOMES:
            out["reverted"] += 1
        elif outcome == "no_op":
            out["inert"] += 1
        elif outcome in KEPT_OUTCOMES:
            crossed = any(
                later.get("action") == "undo"
                and (later.get("undo_count") or 0) >= (j - i)
                for j, later in enumerate(steps)
                if j > i
            )
            out["undone" if crossed else "kept"] += 1
        else:
            out["other"] += 1
    return out


def trial_row(run_dir: Path, finish: dict[str, Any]) -> dict[str, Any]:
    """One trial: outcome, effort, and the edit disposition counters."""
    trial_dir = run_dir / "trials" / f"trial_{finish['trial_id']:03d}"
    steps = iterations(trial_dir)
    tactics = [e for e in steps if e.get("action") == "tactic"]
    outcomes = Counter(e.get("outcome") for e in tactics)
    undos = [e for e in steps if e.get("action") == "undo"]
    bootstrap = _read_json(trial_dir / "bootstrap_result.json")
    usage = finish.get("token_usage") or {}
    calls = usage.get("calls", 0)
    disposition = edit_disposition(steps)
    return {
        "run": run_dir.name,
        "trial_id": finish["trial_id"],
        "lemma": finish.get("name"),
        "reason": finish.get("reason"),
        "steps": finish.get("steps", 0),
        "duration_s": finish.get("duration_s", 0.0),
        "closed_by_model": finish.get("reason") == "COMPLETE"
        and finish.get("steps", 0) > 0,
        "agent_invoked": bool(calls) or bool(steps),
        "original_tactics": bootstrap.get("total_count"),
        "replayed_tactics": bootstrap.get("accepted_count"),
        "fully_replayed": bootstrap.get("fully_replayed"),
        "calls": calls,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "reasoning_tokens": usage.get("reasoning_tokens", 0),
        "cached_prompt_tokens": usage.get("cached_prompt_tokens", 0),
        "accepted": outcomes["accepted"],
        "failed": outcomes["failed"] + outcomes["rejected"],
        "no_op": outcomes["no_op"],
        "undo_actions": len(undos),
        "tactics_undone": sum(e.get("undone") or 0 for e in undos),
        "lookups": sum(
            1 for e in steps
            if e.get("action") in ("search_lemmas", "lookup_lemma")
        ),
        "edits": dict(disposition),
    }


def run_row(run_dir: Path) -> dict[str, Any] | None:
    """One run, or None when it never finished a trial."""
    events = _read_jsonl(run_dir / "events.jsonl")
    finishes = [e for e in events if e.get("event") == "trial_finish"]
    if not finishes:
        return None
    start = next((e for e in events if e.get("event") == "experiment_start"), {})
    first = datetime.fromisoformat(events[0]["time"])
    last = datetime.fromisoformat(events[-1]["time"])
    trials = [trial_row(run_dir, f) for f in finishes]
    durations = [t["duration_s"] for t in trials]
    calls = sum(t["calls"] for t in trials)
    return {
        "run": run_dir.name,
        "spec": start.get("spec", "?"),
        "completed_normally": (run_dir / "summary.json").exists(),
        "trials": len(trials),
        "closed": sum(1 for t in trials if t["reason"] == "COMPLETE"),
        "closed_by_model": sum(1 for t in trials if t["closed_by_model"]),
        "closed_on_replay": sum(
            1 for t in trials if t["reason"] == "COMPLETE" and t["steps"] == 0
        ),
        "stuck": sum(1 for t in trials if t["reason"] == "STUCK"),
        "max_steps": sum(1 for t in trials if t["reason"] == "MAX_STEPS"),
        "wall_s": (last - first).total_seconds(),
        "trial_time_s": sum(durations),
        "median_trial_s": statistics.median(durations),
        "max_trial_s": max(durations),
        "calls": calls,
        "prompt_tokens": sum(t["prompt_tokens"] for t in trials),
        "reasoning_tokens": sum(t["reasoning_tokens"] for t in trials),
        "cached_prompt_tokens": sum(t["cached_prompt_tokens"] for t in trials),
        "prompt_tokens_per_call": (
            sum(t["prompt_tokens"] for t in trials) / calls if calls else 0.0
        ),
        "trial_rows": trials,
    }


def context_metrics(steps: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """How much of the prompt the model acted on.

    Uptake is decidable for the two blocks that name things: the ranked
    premises and the lookup/search results. It is not decidable for the rules
    or the few-shot block, so those are not counted here -- their size is
    reported instead and the residual is left explicit in the report.
    """
    steps = list(steps)
    tactics = [e for e in steps if e.get("action") == "tactic"]
    with_premises = [e for e in tactics if e.get("top_premises")]
    block_sizes = [
        sum(len(name) + len(text) + 4 for name, text in e["top_premises"].items())
        for e in with_premises
    ]
    used = used_accepted = 0
    for event in with_premises:
        names = [n.split(".")[-1] for n in event["top_premises"]]
        tactic = event.get("tactic") or ""
        if any(
            len(name) > 2 and re.search(rf"\b{re.escape(name)}\b", tactic)
            for name in names
        ):
            used += 1
            if event.get("outcome") in KEPT_OUTCOMES:
                used_accepted += 1
    lookups = [
        e for e in steps
        if e.get("action") in ("search_lemmas", "lookup_lemma")
    ]
    empty = sum(
        1 for e in lookups
        if re.search(
            r"not found|no match",
            e.get("search_result") or e.get("lookup_result") or "",
            re.IGNORECASE,
        )
    )
    productive = 0
    for a, b in zip(steps, steps[1:]):
        if (
            a.get("action") in ("search_lemmas", "lookup_lemma")
            and b.get("action") == "tactic"
            and b.get("outcome") in KEPT_OUTCOMES
        ):
            productive += 1
    goals = [len(e.get("goal") or "") for e in steps if e.get("goal")]
    return {
        "premise_turns": len(with_premises),
        "premise_block_chars_mean": (
            statistics.mean(block_sizes) if block_sizes else 0.0
        ),
        "premise_used_turns": used,
        "premise_used_accepted": used_accepted,
        "lookups": len(lookups),
        "lookups_empty": empty,
        "lookups_followed_by_accept": productive,
        "goal_chars_median": statistics.median(goals) if goals else 0.0,
    }


def failure_modes(steps: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The three shannon-prover modes, as far as artifacts can decide them.

    Flinch is reported only in its strict form -- a named route that fails and
    is never re-attempted while the reasoning keeps naming it. A looser
    text-matching variant was tried and rejected: hand-checking showed most of
    its hits are state confusion, not abandonment.
    """
    steps = list(steps)
    per_trial: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for event in steps:
        key = (event.get("_run", ""), event.get("_trial", 0))
        per_trial.setdefault(key, []).append(event)

    tactics = [e for e in steps if e.get("action") == "tactic"]
    failures = [e for e in tactics if e.get("outcome") in FAILED_OUTCOMES]
    layer = [e for e in failures if _LAYER_ERROR_RE.search(e.get("error") or "")]

    lowering_accepted = lowering_rolled_back = 0
    route_attempts = route_retried = route_dropped = 0
    for events in per_trial.values():
        for i, event in enumerate(events):
            if event.get("action") != "tactic":
                continue
            if (
                event.get("outcome") in KEPT_OUTCOMES
                and tactic_head(event.get("tactic")) in LOWERING_HEADS
            ):
                lowering_accepted += 1
                if any(
                    later.get("action") == "undo"
                    and (later.get("undo_count") or 0) >= (j - i)
                    for j, later in enumerate(events)
                    if j > i
                ):
                    lowering_rolled_back += 1
            if event.get("outcome") not in FAILED_OUTCOMES:
                continue
            match = _NAMED_ROUTE_RE.search(event.get("tactic") or "")
            if not match:
                continue
            name = match.group(1)
            route_attempts += 1
            rest = events[i + 1:]
            if any(
                name in (e.get("tactic") or "")
                for e in rest if e.get("action") == "tactic"
            ):
                route_retried += 1
            elif any(name in (e.get("thought") or "") for e in rest):
                route_dropped += 1

    return {
        "tactic_attempts": len(tactics),
        "failures": len(failures),
        "wrong_layer_failures": len(layer),
        "wrong_layer_share_of_failures": (
            len(layer) / len(failures) if failures else 0.0
        ),
        "lowering_attempts": sum(
            1 for e in tactics if tactic_head(e.get("tactic")) in LOWERING_HEADS
        ),
        "no_op": sum(1 for e in tactics if e.get("outcome") == "no_op"),
        "accepted": sum(1 for e in tactics if e.get("outcome") == "accepted"),
        "lowering_accepted": lowering_accepted,
        "lowering_rolled_back": lowering_rolled_back,
        "named_route_failures": route_attempts,
        "named_route_retried": route_retried,
        "named_route_dropped": route_dropped,
    }


_TRIAL_DIR_RE = re.compile(r"^trial_(\d+)$")


def collect(root: Path, only: list[str] | None = None) -> dict[str, Any]:
    runs = []
    for run_dir in sorted(root.glob("run-*")):
        if not run_dir.is_dir():
            continue
        if only and run_dir.name not in only:
            continue
        row = run_row(run_dir)
        if row:
            runs.append(row)

    # Run and trial rows come from `trial_finish`, because a trial without one
    # has no outcome to report. The step stream must NOT be limited that way:
    # a trial killed mid-flight still ran, and its edits and failures are as
    # real as any other. Seven of seventeen runs were killed, and dropping
    # their in-flight trials loses ~10% of all iterations.
    steps: list[dict[str, Any]] = []
    for run in runs:
        run_dir = root / run["run"]
        for trial_dir in sorted((run_dir / "trials").glob("trial_*")):
            match = _TRIAL_DIR_RE.match(trial_dir.name)
            if not match:
                continue
            trial_id = int(match.group(1))
            for event in iterations(trial_dir):
                event = dict(event)
                event["_run"] = run["run"]
                event["_trial"] = trial_id
                steps.append(event)

    by_trial: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for event in steps:
        by_trial.setdefault((event["_run"], event["_trial"]), []).append(event)
    edits: Counter = Counter()
    for events in by_trial.values():
        edits.update(edit_disposition(events))
    total_edits = sum(edits.values())

    trials = [t for run in runs for t in run["trial_rows"]]
    invoked = [t for t in trials if t["agent_invoked"]]
    return {
        "runs": runs,
        "totals": {
            "runs": len(runs),
            "trials": len(trials),
            "calls": sum(r["calls"] for r in runs),
            "iterations": len(steps),
            "completion_rate": (
                sum(1 for t in trials if t["reason"] == "COMPLETE") / len(trials)
                if trials else 0.0
            ),
            "model_completion_rate": (
                sum(1 for t in trials if t["closed_by_model"]) / len(trials)
                if trials else 0.0
            ),
            "repair_success_rate": (
                sum(1 for t in invoked if t["reason"] == "COMPLETE") / len(invoked)
                if invoked else 0.0
            ),
            "agent_invoked_trials": len(invoked),
            "steps_to_close": sorted(
                t["steps"] for t in trials if t["closed_by_model"]
            ),
            "wasted_edit_share": (
                (total_edits - edits["kept"]) / total_edits if total_edits else 0.0
            ),
            "edits": dict(edits),
        },
        "context": context_metrics(steps),
        "failure_modes": failure_modes(steps),
    }


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    head = (
        f"{'run':<22}{'spec':<26}{'n':>4}{'closed':>7}{'model':>6}"
        f"{'stuck':>6}{'max':>5}{'wall_h':>8}{'med_s':>7}{'calls':>7}{'ptok/call':>10}"
    )
    lines += ["## Per run", "", head, "-" * len(head)]
    for run in report["runs"]:
        flag = "" if run["completed_normally"] else " *"
        lines.append(
            f"{run['run'] + flag:<22}{run['spec']:<26}{run['trials']:>4}"
            f"{run['closed']:>7}{run['closed_by_model']:>6}{run['stuck']:>6}"
            f"{run['max_steps']:>5}{run['wall_s'] / 3600:>8.1f}"
            f"{run['median_trial_s']:>7.0f}{run['calls']:>7}"
            f"{run['prompt_tokens_per_call']:>10.0f}"
        )
    lines += ["", "* = killed before summary.json was written", ""]

    totals = report["totals"]
    lines += ["## Totals", ""]
    lines += [
        f"  runs {totals['runs']}, trials {totals['trials']}, "
        f"model calls {totals['calls']}, agent iterations {totals['iterations']}",
        f"  completion rate            {totals['completion_rate']:.0%}",
        f"  closed by the model        {totals['model_completion_rate']:.0%}",
        f"  repair success rate        {totals['repair_success_rate']:.0%}"
        f"  (agent invoked on {totals['agent_invoked_trials']} trials)",
        f"  steps to a valid proof     {totals['steps_to_close'] or '-'}",
        f"  wasted edits               {totals['wasted_edit_share']:.0%}"
        f"  {totals['edits']}",
        "",
    ]

    ctx = report["context"]
    lines += ["## Context uptake", ""]
    premise_share = (
        ctx["premise_used_turns"] / ctx["premise_turns"]
        if ctx["premise_turns"] else 0.0
    )
    lines += [
        f"  premises shown on {ctx['premise_turns']} turns, "
        f"~{ctx['premise_block_chars_mean'] / 4:.0f} tokens/turn",
        f"  a shown premise appears in the tactic on "
        f"{ctx['premise_used_turns']} turns ({premise_share:.1%}); "
        f"{ctx['premise_used_accepted']} were accepted",
        f"  lookups {ctx['lookups']}, empty {ctx['lookups_empty']}, "
        f"followed by an accepted tactic {ctx['lookups_followed_by_accept']}",
        f"  goal text median {ctx['goal_chars_median']:.0f} chars "
        f"(~{ctx['goal_chars_median'] / 4:.0f} tokens)",
        "",
    ]

    fm = report["failure_modes"]
    lines += ["## Failure modes (shannon-prover taxonomy)", ""]
    lines += [
        f"  wrong-layer rejections     {fm['wrong_layer_failures']}/{fm['failures']}"
        f" ({fm['wrong_layer_share_of_failures']:.0%} of failures)",
        f"  lowering-class attempts    {fm['lowering_attempts']}/{fm['tactic_attempts']}",
        f"  inert (no_op) moves        {fm['no_op']} of "
        f"{fm['no_op'] + fm['accepted']} EasyCrypt-accepted",
        f"  accepted lowering rolled back "
        f"{fm['lowering_rolled_back']}/{fm['lowering_accepted']}",
        f"  named routes: {fm['named_route_failures']} failed, "
        f"{fm['named_route_retried']} retried, "
        f"{fm['named_route_dropped']} dropped while still being reasoned about",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-derive report §5.6/§5.7 from experiment artifacts."
    )
    parser.add_argument(
        "root", type=Path,
        help="experiments directory holding run-*/ subdirectories",
    )
    parser.add_argument(
        "--run", action="append", dest="runs",
        help="restrict to this run directory name (repeatable)",
    )
    parser.add_argument(
        "--json", type=Path, help="also write the full report as JSON",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 1

    report = collect(args.root, args.runs)
    if not report["runs"]:
        print("error: no runs with finished trials", file=sys.stderr)
        return 1

    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

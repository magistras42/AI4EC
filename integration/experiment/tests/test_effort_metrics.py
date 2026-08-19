"""Tests for the report's effort/waste accounting.

Two definitions carry the section and both have a wrong-but-plausible reading
the rest of this project has already been burned by: a replayed proof is not a
repair, and an accepted tactic is not a kept tactic.
"""

from __future__ import annotations

import json
from pathlib import Path

from integration.experiment.effort_metrics import (
    collect,
    edit_disposition,
    failure_modes,
    tactic_head,
)


def _write_run(
    root: Path,
    name: str,
    finishes: list[dict],
    logs: dict[int, list[dict]] | None = None,
) -> Path:
    run = root / name
    (run / "trials").mkdir(parents=True)
    events = [{"time": "2026-08-10T00:00:00+00:00", "event": "experiment_start",
               "spec": "demo", "mode": "replay_bootstrap", "trials": len(finishes)}]
    for finish in finishes:
        events.append({"time": "2026-08-10T01:00:00+00:00",
                       "event": "trial_finish", **finish})
    (run / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    for trial_id, steps in (logs or {}).items():
        trial = run / "trials" / f"trial_{trial_id:03d}"
        trial.mkdir(parents=True, exist_ok=True)
        (trial / "agent_log.json").write_text(
            json.dumps({"source": "x", "work_copy": "y", "events": steps}),
            encoding="utf-8",
        )
    return run


def _finish(trial_id: int, name: str, reason: str, steps: int, calls: int = 0):
    return {
        "trial_id": trial_id, "name": name, "reason": reason, "steps": steps,
        "duration_s": 10.0,
        "token_usage": {"calls": calls, "prompt_tokens": 100 * calls,
                        "completion_tokens": 0, "reasoning_tokens": 0,
                        "cached_prompt_tokens": 0},
    }


def _tactic(step: int, tactic: str, outcome: str, **extra):
    return {"event": "iteration", "step": step, "action": "tactic",
            "tactic": tactic, "outcome": outcome, "error": extra.pop("error", None),
            **extra}


def test_replayed_proof_is_not_a_repair(tmp_path: Path) -> None:
    """COMPLETE at steps=0 closed on replay; it must not count as a repair."""
    _write_run(
        tmp_path, "run-A",
        [_finish(0, "replayed", "COMPLETE", steps=0),
         _finish(1, "repaired", "COMPLETE", steps=1, calls=1),
         _finish(2, "unsolved", "STUCK", steps=9, calls=9)],
        logs={
            1: [_tactic(1, "apply X.", "complete")],
            2: [_tactic(i, "wp.", "failed") for i in range(1, 10)],
        },
    )
    totals = collect(tmp_path)["totals"]

    assert totals["completion_rate"] == 2 / 3
    assert totals["model_completion_rate"] == 1 / 3
    # Only the two trials the agent actually entered are in the denominator.
    assert totals["agent_invoked_trials"] == 2
    assert totals["repair_success_rate"] == 1 / 2
    assert totals["steps_to_close"] == [1]


def test_accepted_is_not_kept() -> None:
    """Inert and later-undone edits are waste even though EasyCrypt took them."""
    steps = [
        _tactic(1, "seq 1 1 : (x).", "accepted"),   # undone by step 4
        _tactic(2, "wp.", "no_op"),                 # accepted, changed nothing
        _tactic(3, "smt().", "failed"),             # rejected outright
        {"event": "iteration", "step": 4, "action": "undo",
         "undo_count": 3, "undone": 1, "outcome": "undone"},
        _tactic(5, "auto.", "accepted"),            # survives
    ]
    disposition = edit_disposition(steps)

    assert disposition == {"undone": 1, "inert": 1, "reverted": 1, "kept": 1}
    assert sum(disposition.values()) - disposition["kept"] == 3


def test_undo_shallower_than_the_edit_does_not_revert_it() -> None:
    """Undo depth is positional: a 1-step undo cannot reach a 3-step-old edit."""
    steps = [
        _tactic(1, "proc.", "accepted"),
        _tactic(2, "wp.", "accepted"),
        _tactic(3, "auto.", "accepted"),
        {"event": "iteration", "step": 4, "action": "undo",
         "undo_count": 1, "undone": 1, "outcome": "undone"},
    ]
    assert edit_disposition(steps) == {"kept": 2, "undone": 1}


def test_wrong_layer_failures_are_separated_from_argument_errors() -> None:
    steps = [
        _tactic(1, "skip.", "failed",
                error="[critical] expecting a goal of the form: hoare[S]",
                _run="r", _trial=0),
        _tactic(2, "rnd.", "failed",
                error="[critical] invalid arguments", _run="r", _trial=0),
        _tactic(3, "wp.", "failed",
                error="[critical] left instruction list is not empty",
                _run="r", _trial=0),
    ]
    modes = failure_modes(steps)

    assert modes["failures"] == 3
    assert modes["wrong_layer_failures"] == 2


def test_flinch_counts_only_routes_never_re_attempted() -> None:
    """A route that fails and is tried again is not a flinch."""
    steps = [
        _tactic(1, "apply (RO_LCDHAdv q1 q2).", "failed", _run="r", _trial=0),
        _tactic(2, "auto.", "accepted", _run="r", _trial=0,
                thought="the RO_LCDHAdv bridge is still the route"),
        _tactic(3, "apply (Other_lemma x).", "failed", _run="r", _trial=1),
        _tactic(4, "apply (Other_lemma y).", "accepted", _run="r", _trial=1),
    ]
    modes = failure_modes(steps)

    assert modes["named_route_failures"] == 2
    assert modes["named_route_retried"] == 1   # Other_lemma
    assert modes["named_route_dropped"] == 1   # RO_LCDHAdv


def test_in_flight_trials_still_contribute_their_steps(tmp_path: Path) -> None:
    """A killed trial has no outcome but its edits happened; keep them."""
    _write_run(
        tmp_path, "run-B",
        [_finish(0, "finished", "COMPLETE", steps=0)],
        logs={
            0: [],
            1: [_tactic(1, "wp.", "failed"), _tactic(2, "auto.", "accepted")],
        },
    )
    report = collect(tmp_path)

    assert report["totals"]["trials"] == 1          # only the finished one
    assert report["totals"]["iterations"] == 2      # but both steps counted
    assert report["totals"]["edits"] == {"reverted": 1, "kept": 1}


def test_tactic_head_ignores_leading_space_and_arguments() -> None:
    assert tactic_head("  seq 1 1 : (x = y).") == "seq"
    assert tactic_head("smt(a b).") == "smt"
    assert tactic_head(None) == "?"

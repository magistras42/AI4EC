"""Aggregation of per-trial repair artifacts into run-level metrics (W8)."""

from __future__ import annotations

import json

from integration.experiment.repair_metrics import (
    aggregate_repair_metrics,
    collect_trial_repair_metrics,
)


def _write(trial_dir, name, payload):
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def _bootstrap(accepted, total, failed="rewrite foo.", fully=False):
    return {
        "accepted_count": accepted,
        "total_count": total,
        "failed_tactic": failed,
        "fully_replayed": fully,
    }


def _import_repair(*, improved=True, loads_after=False, kept=("rule-a",), before=108, after=453):
    return {
        "changed": True,
        "improved": improved,
        "loads_after": loads_after,
        "considered": ["rule-a", "rule-b", "rule-c", "rule-d"],
        "applied": [{"id": rule, "kept": True} for rule in kept]
        + [{"id": "rejected-rule", "kept": False}],
        "error_line_before": before,
        "error_line_after": after,
    }


def test_returns_empty_for_a_run_with_no_repair_artifacts(tmp_path):
    """Mutation/informal runs must keep a clean summary.json."""
    (tmp_path / "trials" / "trial_000").mkdir(parents=True)
    assert aggregate_repair_metrics(tmp_path) == {}
    assert aggregate_repair_metrics(tmp_path / "nonexistent") == {}


def test_collects_replay_fraction_for_one_trial(tmp_path):
    trial = tmp_path / "trials" / "trial_000"
    _write(trial, "bootstrap_result.json", _bootstrap(7, 10))
    metrics = collect_trial_repair_metrics(trial)
    assert metrics["replay"]["accepted_count"] == 7
    assert metrics["replay"]["replayed_fraction"] == 0.7
    assert metrics["replay"]["fully_replayed"] is False


def test_zero_total_tactics_does_not_divide_by_zero(tmp_path):
    trial = tmp_path / "trials" / "trial_000"
    _write(trial, "bootstrap_result.json", _bootstrap(0, 0))
    assert collect_trial_repair_metrics(trial)["replay"]["replayed_fraction"] is None


def test_aggregates_replay_and_fully_replayed_rate(tmp_path):
    trials = tmp_path / "trials"
    _write(trials / "trial_000", "bootstrap_result.json", _bootstrap(10, 10, "", True))
    _write(trials / "trial_001", "bootstrap_result.json", _bootstrap(4, 8))
    _write(trials / "trial_002", "bootstrap_result.json", _bootstrap(2, 8))

    summary = aggregate_repair_metrics(tmp_path)["replay"]
    assert summary["trials"] == 3
    assert summary["fully_replayed"] == 1
    assert summary["fully_replayed_rate"] == round(1 / 3, 4)
    assert summary["mean_replayed_fraction"] == round((1.0 + 0.5 + 0.25) / 3, 4)
    assert summary["min_replayed_fraction"] == 0.25
    assert summary["total_tactics_accepted"] == 16
    assert summary["total_tactics"] == 26


def test_aggregates_import_repair_attempts_and_line_advance(tmp_path):
    trials = tmp_path / "trials"
    _write(trials / "trial_000", "import_repair.json",
           _import_repair(before=108, after=453, kept=("a", "b")))
    _write(trials / "trial_001", "import_repair.json",
           _import_repair(improved=False, before=108, after=108, kept=()))

    summary = aggregate_repair_metrics(tmp_path)["import_repair"]
    assert summary["attempted"] == 2
    assert summary["improved"] == 1
    assert summary["improved_rate"] == 0.5
    # (345 + 0) / 2
    assert summary["mean_first_error_line_advance"] == 172.5
    assert summary["mean_migrations_kept"] == 1.0


def test_counts_changelog_hops_including_misses(tmp_path):
    trials = tmp_path / "trials"
    _write(trials / "trial_000", "repair_hints_hop.json",
           {"changelog_hop_matched_version": "r2025.02"})
    _write(trials / "trial_001", "repair_hints_hop.json",
           {"changelog_hop_matched_version": "r2025.02"})
    _write(trials / "trial_002", "repair_hints_hop.json",
           {"changelog_hop_matched_version": None})

    hops = aggregate_repair_metrics(tmp_path)["changelog_hops"]
    assert hops["r2025.02"] == 2
    assert hops["(no match)"] == 1


def test_records_version_provenance_per_trial(tmp_path):
    trial = tmp_path / "trials" / "trial_000"
    _write(trial, "ec_versions.json", {
        "source": {"version": None, "method": "predates_catalog", "confidence": "medium"},
        "target": {"version": "r2026.06", "method": "git_describe", "confidence": "high"},
    })
    versions = collect_trial_repair_metrics(trial)["ec_versions"]
    assert versions["target"] == "r2026.06"
    assert versions["target_method"] == "git_describe"
    assert versions["source"] is None


def test_hint_uptake_detects_a_hinted_identifier_in_an_accepted_tactic(tmp_path):
    trial = tmp_path / "trials" / "trial_000"
    trial.mkdir(parents=True)
    (trial / "changelog_hints.txt").write_text(
        "r2025.02: SmtMap was split; finite maps now live in FMap.\n"
        "Affected: FMap.get_setE\n",
        encoding="utf-8",
    )
    _write(trial, "agent_log.json", {"iterations": [
        {"action": "tactic", "outcome": "failed", "tactic": "by rewrite get_set."},
        {"action": "tactic", "outcome": "accepted", "tactic": "by rewrite FMap.get_setE."},
    ]})

    uptake = collect_trial_repair_metrics(trial)["hint_uptake"]
    assert uptake["any_used"] is True
    assert "FMap.get_setE" in uptake["used_in_accepted_tactics"]


def test_hint_uptake_is_false_when_the_model_ignored_the_hint(tmp_path):
    trial = tmp_path / "trials" / "trial_000"
    trial.mkdir(parents=True)
    (trial / "changelog_hints.txt").write_text(
        "r2025.02: finite maps moved to FMap.get_setE\n", encoding="utf-8"
    )
    _write(trial, "agent_log.json", {"iterations": [
        {"action": "tactic", "outcome": "accepted", "tactic": "by smt()."},
    ]})
    assert collect_trial_repair_metrics(trial)["hint_uptake"]["any_used"] is False


def test_hint_uptake_requires_whole_identifier_matches(tmp_path):
    """A hinted name must not be credited for matching inside a longer one.

    Same rule the changelog retriever uses (no substring matching, so `map`
    never matches `map1`). Here the hint names `FMap.getE` and the accepted
    tactic uses `FMap.getEX`, which is a different lemma.
    """
    trial = tmp_path / "trials" / "trial_000"
    trial.mkdir(parents=True)
    (trial / "changelog_hints.txt").write_text("Renamed FMap.getE\n", encoding="utf-8")
    _write(trial, "agent_log.json", {"iterations": [
        {"action": "tactic", "outcome": "accepted", "tactic": "by rewrite Other.getEX."},
    ]})
    uptake = collect_trial_repair_metrics(trial)["hint_uptake"]
    assert "FMap.getE" not in uptake["used_in_accepted_tactics"]
    assert uptake["any_used"] is False


def test_hint_uptake_credits_a_hinted_theory_used_on_its_own(tmp_path):
    """Qualifying a tactic with a hinted theory counts as uptake."""
    trial = tmp_path / "trials" / "trial_000"
    trial.mkdir(parents=True)
    (trial / "changelog_hints.txt").write_text(
        "SmtMap split; finite maps now live in FMap.\n", encoding="utf-8"
    )
    _write(trial, "agent_log.json", {"iterations": [
        {"action": "tactic", "outcome": "accepted", "tactic": "by rewrite FMap.get_setE."},
    ]})
    assert collect_trial_repair_metrics(trial)["hint_uptake"]["any_used"] is True


def test_malformed_artifacts_are_skipped_not_fatal(tmp_path):
    trials = tmp_path / "trials"
    good = trials / "trial_000"
    _write(good, "bootstrap_result.json", _bootstrap(3, 6))
    bad = trials / "trial_001"
    bad.mkdir(parents=True)
    (bad / "bootstrap_result.json").write_text("{not json", encoding="utf-8")

    summary = aggregate_repair_metrics(tmp_path)
    # The good trial still counts; the malformed one contributes nothing.
    assert summary["replay"]["trials"] == 1
    assert summary["trials_with_repair_artifacts"] == 1


def test_per_trial_detail_is_retained_for_auditing(tmp_path):
    trials = tmp_path / "trials"
    _write(trials / "trial_000", "bootstrap_result.json", _bootstrap(5, 5, "", True))
    summary = aggregate_repair_metrics(tmp_path)
    assert summary["per_trial"][0]["trial_dir"] == "trial_000"
    assert summary["per_trial"][0]["replay"]["fully_replayed"] is True


def test_hint_uptake_reads_the_real_agent_log_event_shape(tmp_path):
    """AgentRunLog writes {"events": [{"event": "iteration", ...}]}.

    An earlier version read a top-level "iterations" key that never exists in
    a real run, so hint_uptake was structurally pinned at 0.0 -- reporting a
    finding where it had measured nothing.
    """
    trial = tmp_path / "trials" / "trial_000"
    trial.mkdir(parents=True)
    (trial / "changelog_hints.txt").write_text(
        "finite maps moved to FMap.get_setE\n", encoding="utf-8"
    )
    _write(trial, "agent_log.json", {
        "source": "x.ec",
        "work_copy": "x.agent.ec",
        "events": [
            {"event": "startup", "goal": "g"},
            {"event": "iteration", "action": "tactic", "outcome": "failed",
             "tactic": "by smt()."},
            {"event": "iteration", "action": "tactic", "outcome": "accepted",
             "tactic": "by rewrite FMap.get_setE."},
            {"event": "finish", "reason": "COMPLETE"},
        ],
    })
    uptake = collect_trial_repair_metrics(trial)["hint_uptake"]
    assert uptake["accepted_tactic_count"] == 1
    assert uptake["scorable"] is True
    assert uptake["any_used"] is True


def test_zero_accepted_tactics_is_reported_as_unscorable(tmp_path):
    """A 0 rate with 0 accepted tactics means 'not measurable', not 'ignored'."""
    trial = tmp_path / "trials" / "trial_000"
    trial.mkdir(parents=True)
    (trial / "changelog_hints.txt").write_text("moved to FMap.getE\n", encoding="utf-8")
    _write(trial, "agent_log.json", {
        "events": [
            {"event": "iteration", "action": "tactic", "outcome": "failed",
             "tactic": "skip."},
            {"event": "iteration", "action": "tactic", "outcome": "rejected",
             "tactic": "skip."},
        ],
    })
    uptake = collect_trial_repair_metrics(trial)["hint_uptake"]
    assert uptake["accepted_tactic_count"] == 0
    assert uptake["scorable"] is False
    assert uptake["any_used"] is False

    summary = aggregate_repair_metrics(tmp_path)["hint_uptake"]
    assert summary["trials_with_accepted_tactics"] == 0
    assert summary["rate_among_scorable"] is None, "must not imply hints were ignored"

"""Paired A/B scoring across seeds (the hint-uptake counterfactual).

The point of this tool is refusing to overclaim. Run-to-run spread under
identical configuration reached 11-vs-1 accepted tactics on this corpus, which
is larger than any between-arm difference a single pair could show -- so most
of these tests are about what it declines to conclude.
"""
from __future__ import annotations

import json

import pytest

from integration.experiment.compare_runs import (
    Arm,
    check_pairing,
    compare,
    load_summary,
    main,
)


def _summary(
    *, successes=8, trials=10, accepted=40, hints=True, seed=1,
    model="claude-opus-5", spec="elgamal-changelog-repair", budget_stopped=False,
):
    return {
        "spec_name": spec,
        "successes": successes,
        "trials_run": trials,
        "budget_stopped": budget_stopped,
        "estimated_cost": {"usd": 1.5},
        "arm": {
            "spec": spec, "seed": seed, "model": model,
            "provider": "anthropic", "changelog_hints": hints,
        },
        "repair_metrics": {
            "replay": {
                "total_tactics_accepted": accepted, "fully_replayed_rate": 0.5,
            },
            "import_repair": {"resolved_rate": 1.0},
            "hint_uptake": {"rate_among_scorable": 0.4 if hints else 0.0},
        },
    }


def _arm(label, summaries) -> Arm:
    return Arm(label=label, runs=list(summaries))


def _write_run(tmp_path, name, payload):
    run = tmp_path / name
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(payload), encoding="utf-8")
    return run


# --- loading ----------------------------------------------------------------


def test_a_run_directory_or_the_summary_file_both_load(tmp_path):
    run = _write_run(tmp_path, "on-1", _summary())
    assert load_summary(run)["successes"] == 8
    assert load_summary(run / "summary.json")["successes"] == 8


def test_an_unreadable_run_is_skipped_not_fatal(tmp_path, capsys):
    good = _write_run(tmp_path, "on-1", _summary())
    assert main(["--arm", "on", str(good), str(tmp_path / "nope")]) == 0
    assert "no readable summary.json" in capsys.readouterr().err


def test_no_readable_runs_at_all_is_an_error(tmp_path):
    assert main(["--arm", "on", str(tmp_path / "nope")]) == 1


# --- the refusal to overclaim ----------------------------------------------


def test_one_run_per_arm_is_never_conclusive():
    """A single run has no measurable spread, so there is nothing to judge a
    difference against however large it looks."""
    report = compare([
        _arm("on", [_summary(successes=10)]),
        _arm("off", [_summary(successes=0, hints=False)]),
    ])
    entry = report["metrics"]["success_rate"]
    assert entry["difference"] == -1.0        # as big a gap as exists
    assert entry["conclusive"] is False
    assert "single run" in entry["caveat"]
    assert report["conclusive_metrics"] == []


def test_a_difference_inside_the_within_arm_spread_is_not_conclusive():
    """The measured failure mode: 11-vs-1 accepted tactics under identical
    configuration swamps any between-arm effect."""
    report = compare([
        _arm("on", [_summary(accepted=11), _summary(accepted=1, seed=2)]),
        _arm("off", [_summary(accepted=9, hints=False),
                     _summary(accepted=2, hints=False, seed=2)]),
    ])
    entry = report["metrics"]["tactics_accepted"]
    assert entry["conclusive"] is False
    assert entry["widest_within_arm_range"] == 10.0
    assert "inside the" in entry["caveat"]


def test_a_difference_larger_than_the_spread_is_conclusive():
    report = compare([
        _arm("on", [_summary(accepted=40), _summary(accepted=42, seed=2)]),
        _arm("off", [_summary(accepted=10, hints=False),
                     _summary(accepted=12, hints=False, seed=2)]),
    ])
    entry = report["metrics"]["tactics_accepted"]
    assert entry["conclusive"] is True
    assert entry["difference"] == -30.0
    assert "tactics_accepted" in report["conclusive_metrics"]


def test_the_reported_direction_names_which_arm_is_which():
    """A signed difference with no stated direction is unreadable."""
    report = compare([
        _arm("hints-on", [_summary(accepted=40), _summary(accepted=41, seed=2)]),
        _arm("hints-off", [_summary(accepted=10, hints=False),
                           _summary(accepted=11, hints=False, seed=2)]),
    ])
    assert report["metrics"]["tactics_accepted"]["direction"] == (
        "hints-off - hints-on"
    )


def test_a_missing_metric_leaves_the_arm_empty_rather_than_zero():
    """Absent is not zero. A run that recorded no hint_uptake block must not
    read as an arm that scored 0."""
    bare = _summary()
    bare["repair_metrics"] = {}
    report = compare([_arm("on", [bare]), _arm("off", [_summary(hints=False)])])
    assert report["metrics"]["hint_uptake_rate"]["by_arm"]["on"] == {"n": 0}
    assert "difference" not in report["metrics"]["hint_uptake_rate"]


# --- pairing hygiene --------------------------------------------------------


def test_mismatched_seeds_are_flagged():
    warnings = check_pairing([
        _arm("on", [_summary(seed=1), _summary(seed=2)]),
        _arm("off", [_summary(seed=3, hints=False), _summary(seed=4, hints=False)]),
    ])
    assert any("same seeds" in w for w in warnings)


def test_two_arms_with_the_same_setting_are_flagged_as_not_an_ab():
    """The easiest mistake to make and the hardest to notice from the output:
    forgetting --no-changelog-hints on the second arm."""
    warnings = check_pairing([
        _arm("on", [_summary()]), _arm("off", [_summary()]),
    ])
    assert any("not an A/B" in w for w in warnings)


def test_a_budget_stopped_run_is_flagged_as_incomparable():
    """A run that stopped early on its spend cap ran fewer trials than it was
    asked to; its success rate is not the same measurement."""
    warnings = check_pairing([
        _arm("on", [_summary(budget_stopped=True)]),
        _arm("off", [_summary(hints=False)]),
    ])
    assert any("spend cap" in w for w in warnings)


def test_mixed_models_within_one_arm_are_flagged():
    warnings = check_pairing([
        _arm("on", [_summary(model="claude-opus-5"),
                    _summary(model="deepseek-v4-flash", seed=1)]),
        _arm("off", [_summary(hints=False)]),
    ])
    assert any("mixed models" in w for w in warnings)


def test_a_clean_pairing_produces_no_warnings():
    assert check_pairing([
        _arm("on", [_summary(seed=1), _summary(seed=2)]),
        _arm("off", [_summary(seed=1, hints=False), _summary(seed=2, hints=False)]),
    ]) == []


# --- output -----------------------------------------------------------------


def test_the_report_says_plainly_when_nothing_separated_the_arms(tmp_path, capsys):
    on = _write_run(tmp_path, "on-1", _summary(accepted=11))
    off = _write_run(tmp_path, "off-1", _summary(accepted=9, hints=False))
    main(["--arm", "hints-on", str(on), "--arm", "hints-off", str(off)])
    out = capsys.readouterr().out
    assert "No metric separated the arms" in out
    assert "about power, not about the knowledge base" in out


def test_the_json_report_can_be_written_for_later(tmp_path):
    on = _write_run(tmp_path, "on-1", _summary())
    off = _write_run(tmp_path, "off-1", _summary(hints=False))
    out = tmp_path / "report.json"
    main(["--arm", "on", str(on), "--arm", "off", str(off), "--json", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["metrics"]["success_rate"]["by_arm"]["on"]["n"] == 1
    assert "warnings" in report


# --- the arm the comparison needs -------------------------------------------
# Until --no-changelog-hints existed there was no way to produce a hints-off
# run: changelog_hints was populated unconditionally, so the counterfactual
# could not be measured at any price.


def test_the_hints_off_arm_exists_and_defaults_to_on():
    from integration.experiment.specs import SPECS

    for name in SPECS.names():
        spec = SPECS.get(name)
        if spec.replay_bootstrap is None:
            continue
        assert spec.replay_bootstrap.changelog_hints is True, name


def test_the_cli_flag_produces_the_hints_off_arm():
    import integration.experiment.__main__ as cli

    captured = {}

    def fake_run(spec, config):
        captured["spec"] = spec
        raise SystemExit(0)

    original = cli.run_experiment
    cli.run_experiment = fake_run
    try:
        with pytest.raises(SystemExit):
            cli.main(["run", "--spec", "elgamal-changelog-repair",
                      "--no-changelog-hints", "--trials", "1"])
    finally:
        cli.run_experiment = original

    assert captured["spec"].replay_bootstrap.changelog_hints is False


def test_turning_hints_off_also_turns_off_the_per_failure_refresh():
    """A run whose bootstrap hints were suppressed but which then refetched
    them on the next failure is not a hints-off run."""
    import inspect

    from integration.experiment import repair_bootstrap

    source = inspect.getsource(repair_bootstrap.run_replay_bootstrap_trial)
    assert "live_changelog_hints=replay_config.changelog_hints" in source


def test_the_summary_records_which_arm_ran():
    """Without this a hints-on and a hints-off summary.json are
    indistinguishable and pairing relies on remembering which was which."""
    from dataclasses import replace as dc_replace

    from integration.experiment.config import ExperimentConfig
    from integration.experiment.runner import _arm_of
    from integration.experiment.specs import SPECS

    spec = SPECS.get("elgamal-changelog-repair")
    off = dc_replace(
        spec, replay_bootstrap=dc_replace(spec.replay_bootstrap,
                                          changelog_hints=False)
    )
    config = ExperimentConfig(trials=1, seed=7)
    assert _arm_of(spec, config)["changelog_hints"] is True
    assert _arm_of(off, config)["changelog_hints"] is False
    assert _arm_of(spec, config)["seed"] == 7

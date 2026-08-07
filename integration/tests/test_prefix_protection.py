"""The replayed prefix is verified work, and the agent was destroying it.

The bootstrap replays the original proof one tactic at a time and keeps the
ones that COMPILE against the current EasyCrypt build. Those are not guesses --
the tactic that broke is the next one. Nothing distinguished them from the
model's own speculative lines, and `undo_last_tactic` walked straight through
them.

Measured on run 20260807T031032Z, with nothing protecting them:

    G2_G3          10 undo actions removed 37 tactics; ended 13 -> 12
    INDCPA_HEG_G1  13 undo actions removed 53 tactics; ended 21 ->  9
    G1_G2_eq       ONE undo of count 12 removed 12 of 18 at step 2, after a
                   single failed `smt(mem_rng_empty)`

Two of the three finished holding FEWER tactics than the bootstrap handed them,
and no conventional counter showed it -- accepted/failed/no-op all looked
ordinary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integration.agent.proof_file import ProofFile

SCRIPT = """\
lemma l : true.
proof.
  byequiv => //.
  proc.
  seq 5 5 : (={x}).
  auto.
  wp.
  skip.
qed.
"""


@pytest.fixture
def proof(tmp_path) -> ProofFile:
    path = tmp_path / "p.ec"
    path.write_text(SCRIPT, encoding="utf-8")
    return ProofFile(path)


def test_tactic_count_sees_the_script(proof):
    assert proof.tactic_count() == 6


# --- the clamp --------------------------------------------------------------


def test_a_bulk_undo_stops_at_the_prefix(proof):
    """The G1_G2_eq case: one big count must not erase verified work."""
    proof.protected_prefix = 4
    assert proof.undo_last_tactic(12) == 2, "should stop at the prefix, not wipe"
    assert proof.tactic_count() == 4
    text = proof.path.read_text(encoding="utf-8")
    assert "byequiv => //." in text and "seq 5 5 : (={x})." in text


def test_crossing_the_prefix_is_still_possible_one_step_at_a_time(proof):
    """NOT a prohibition. The bootstrap stops at the FIRST failure, and an
    earlier tactic can be the real cause -- a `seq` whose invariant is too weak
    compiles and strands the proof later -- so reaching back must stay
    possible. It just has to be a decision rather than a reflex."""
    proof.protected_prefix = 4
    proof.undo_last_tactic(12)
    assert proof.tactic_count() == 4
    for expected in (3, 2, 1, 0):
        assert proof.undo_last_tactic(1) == 1
        assert proof.tactic_count() == expected


def test_a_bulk_undo_at_the_boundary_still_makes_progress(proof):
    """Already down to the prefix: a multi-undo must remove one rather than
    silently doing nothing, or the model can spin issuing undos forever."""
    proof.protected_prefix = 6
    assert proof.undo_last_tactic(5) == 1
    assert proof.tactic_count() == 5


def test_no_prefix_means_the_old_unrestricted_behaviour(proof):
    """A plain agent run has no bootstrap prefix and must be unaffected."""
    assert proof.protected_prefix == 0
    assert proof.undo_last_tactic(12) == 6
    assert proof.tactic_count() == 0


def test_undo_never_touches_the_proof_line(proof):
    proof.undo_last_tactic(99)
    text = proof.path.read_text(encoding="utf-8")
    assert "proof." in text and "lemma l : true." in text


# --- what the model is told -------------------------------------------------


def test_the_prompt_names_the_prefix_as_verified():
    from integration.agent.prompt import format_replayed_prefix_note

    text = format_replayed_prefix_note(18)
    assert "18 tactic(s)" in text
    assert "compiled" in text
    assert "the tactic that broke is the NEXT one" in text
    # It must say the harness will stop a bulk undo, or the clamp is a silent
    # surprise the model cannot reason about.
    assert "stop a multi-step undo" in text
    # And it must NOT read as a prohibition.
    assert "one step at a time" in text


def test_no_note_without_a_prefix():
    from integration.agent.prompt import format_replayed_prefix_note

    assert format_replayed_prefix_note(0) == ""


def test_the_note_is_not_first_step_only():
    """`broken_tactic` is shown on step 1 alone; this must not be. The bulk
    undos that destroyed the prefix happened at steps 2, 20 and beyond."""
    import inspect

    from integration.agent import loop as loop_mod

    src = inspect.getsource(loop_mod.run_agent)
    assert "replayed_prefix=config.replayed_prefix," in src
    assert "config.replayed_prefix if step == 1" not in src


def test_the_note_reaches_the_prompt():
    from integration.agent.prompt import build_prompt

    text = build_prompt(
        goal="G", top_premises={}, failed_tactics=[], proof_tail="",
        replayed_prefix=18,
    )
    assert "already verified" in text
    text_none = build_prompt(
        goal="G", top_premises={}, failed_tactics=[], proof_tail="",
    )
    assert "already verified" not in text_none


# --- the metric -------------------------------------------------------------


def test_the_run_log_records_net_tactics(tmp_path):
    """The only measure that caught the destruction. Every conventional
    counter looked ordinary while the proof shrank."""
    import json

    from integration.agent.run_log import AgentRunLog

    log = tmp_path / "log.json"
    run_log = AgentRunLog(source=tmp_path / "s.ec", work_copy=tmp_path / "w.ec",
                          path=log)
    run_log.finish(reason="STUCK", message="m", steps=114,
                   tactics_retained=9, replayed_prefix=21)
    ev = json.loads(log.read_text(encoding="utf-8"))["events"][-1]
    assert ev["tactics_retained"] == 9
    assert ev["replayed_prefix"] == 21
    assert ev["net_tactics_vs_bootstrap"] == -12


def test_net_is_absent_rather_than_wrong_when_unknown(tmp_path):
    import json

    from integration.agent.run_log import AgentRunLog

    log = tmp_path / "log.json"
    run_log = AgentRunLog(source=tmp_path / "s.ec", work_copy=tmp_path / "w.ec",
                          path=log)
    run_log.finish(reason="COMPLETE", message="m", steps=3)
    ev = json.loads(log.read_text(encoding="utf-8"))["events"][-1]
    assert ev["net_tactics_vs_bootstrap"] is None


# --- how smt is actually used (measured across every run) -------------------
# 124 smt-invoking tactics: 76% aimed at PROGRAM-LOGIC goals, and that is where
# all 20 successes come from -- compounds that reduce the judgment first, then
# call smt. Bare `smt()` at an ambient goal is 0 of 27.


def test_the_ambient_advice_records_that_bare_smt_never_works_here():
    from integration.agent.prompt import format_active_goal_shape_hints

    text = format_active_goal_shape_hints(
        "Current goal\n\nx: int\n----------------\n"
        "forall (y : int), 0 <= x => x + y = y + x"
    )
    assert "AMBIENT-LOGIC goal" in text
    assert "0 of 27" in text
    assert "cannot prove goal (strict)" in text
    # It must point at what DOES work rather than only forbidding.
    assert "smt(Lemma1, Lemma2)" in text
    assert "have h : P by smt()" in text


def test_the_compound_rung_says_where_compounds_actually_die():
    """40 of 55 failed smt-compounds died in their FIRST segment, so the error
    the model is reading is not about smt at all."""
    from integration.agent.prompt import format_broken_tactic_repair

    text = format_broken_tactic_repair("rnd; wp; skip; smt().", None)
    assert "40 died in the FIRST segment" in text
    assert "invalid last instruction" in text


def test_no_ambient_note_on_a_program_logic_goal():
    from integration.agent.prompt import format_active_goal_shape_hints

    goal = ("Current goal\n\npre = x = 1\n\nq <$ dexp   ( 1)  q <$ dexp\n\n"
            "post = x = 2")
    assert "0 of 27" not in format_active_goal_shape_hints(goal)


# --- the notebook support module --------------------------------------------
# Three notebooks now drive the same harness. The traps in the handoff's §7 are
# mostly NOTEBOOK traps (stale kernel, reused output_dir, a preflight that
# passes while the run cannot start), so the parts that must not drift live in
# one module rather than being copied per notebook.


def test_the_staleness_check_passes_on_the_working_tree():
    from integration.experiment.notebook_support import verify_working_tree_is_live

    assert verify_working_tree_is_live() == []


def test_the_staleness_check_names_what_is_stale(monkeypatch):
    """It must FAIL on stale code rather than pass quietly -- that is its whole
    reason to exist.

    Simulated by reverting one shipped behaviour (the prefix note) to what the
    pre-change code did, which is exactly what a stale kernel would serve.
    Patching a dataclass default would not work: defaults are baked into
    `__init__` when the class is created, so the constructor ignores it.
    """
    import integration.agent.prompt as prompt_mod
    from integration.experiment.notebook_support import verify_working_tree_is_live

    monkeypatch.setattr(prompt_mod, "format_replayed_prefix_note", lambda n: "")
    assert "prefix note present" in verify_working_tree_is_live()


def test_each_run_gets_a_fresh_output_dir(tmp_path):
    """Reusing a stale output_dir overwrites a previous run in place; it has
    already mixed two runs' events into one file."""
    import time

    from integration.experiment.notebook_support import build_config

    kw = dict(
        provider="lm_studio", model="m", thinking="adaptive", reasoning_effort=None,
        embed_model="e", trials=1, adaptive_multiplier=2.5, min_steps=10,
        stuck_limit=20, top_k=10, llm_max_tokens=1024, llm_timeout_s=180,
        cost_limit_usd=5.0, data_dir=tmp_path,
    )
    a = build_config(spec_name="joy-tactic-repair", **kw)
    time.sleep(1.1)
    b = build_config(spec_name="joy-tactic-repair", **kw)
    assert a.output_dir != b.output_dir


def test_a_local_provider_gets_no_cost_cap(tmp_path):
    """A cap is meaningless for a free local model."""
    from integration.experiment.notebook_support import build_config

    exp = build_config(
        spec_name="joy-tactic-repair", provider="lm_studio", model="m",
        thinking="adaptive", reasoning_effort=None, embed_model="e", trials=1,
        adaptive_multiplier=2.5, min_steps=10, stuck_limit=20, top_k=10,
        llm_max_tokens=1024, llm_timeout_s=180, cost_limit_usd=5.0,
        data_dir=tmp_path,
    )
    assert exp.agent.spend_budget is None


def test_the_timeout_reaches_the_agent_config(tmp_path):
    from integration.experiment.notebook_support import build_config

    exp = build_config(
        spec_name="joy-tactic-repair", provider="lm_studio", model="m",
        thinking="adaptive", reasoning_effort=None, embed_model="e", trials=1,
        adaptive_multiplier=2.5, min_steps=10, stuck_limit=20, top_k=10,
        llm_max_tokens=1024, llm_timeout_s=180, cost_limit_usd=None,
        data_dir=tmp_path,
    )
    assert exp.agent.lm_studio_timeout == 180


@pytest.mark.parametrize("spec", ["joy-tactic-repair", "lq1-broken-repair"])
def test_both_new_specs_build_and_load_cases(spec, tmp_path):
    from integration.experiment.notebook_support import build_config, build_spec

    exp = build_config(
        spec_name=spec, provider="lm_studio", model="m", thinking="adaptive",
        reasoning_effort=None, embed_model="e", trials=1,
        adaptive_multiplier=2.5, min_steps=10, stuck_limit=20, top_k=10,
        llm_max_tokens=1024, llm_timeout_s=180, cost_limit_usd=None,
        data_dir=Path("data"),
    )
    built = build_spec(exp, Path("data"))
    assert built.name == spec
    assert list(built.corpus.load_cases()), "corpus loaded no cases"


def test_neither_new_spec_uses_replay_bootstrap():
    """Pins the scope decision: the prefix clamp and net_tactics metric are
    replay-only, so these two specs must not silently acquire a prefix."""
    from integration.experiment.specs import SPECS, register_default_specs

    register_default_specs("data")
    for name in ("joy-tactic-repair", "lq1-broken-repair"):
        assert SPECS.get(name).replay_bootstrap is None


# --- replay-bootstrap specs for the other two corpora -----------------------


def test_the_new_changelog_specs_use_replay_bootstrap():
    """Only replay_bootstrap produces a verified prefix, so only it can
    exercise the clamp. broken_formal and mutations strip every tactic first."""
    from integration.experiment.specs import SPECS, register_default_specs

    register_default_specs("data")
    for name in ("lq1-changelog-repair", "joy-changelog-repair",
                 "elgamal-changelog-repair"):
        assert SPECS.get(name).replay_bootstrap is not None, name
    for name in ("lq1-broken-repair", "joy-tactic-repair"):
        assert SPECS.get(name).replay_bootstrap is None, name


@pytest.mark.parametrize("spec", ["lq1-changelog-repair", "joy-changelog-repair"])
def test_the_new_specs_load_cases(spec):
    from integration.experiment.specs import SPECS, register_default_specs

    register_default_specs("data")
    assert list(SPECS.get(spec).corpus.load_cases())


def test_a_parsing_replay_is_not_a_complete_proof(tmp_path, easycrypt_bin):
    """The bug that made a replay spec on LQ1 look pointless.

    `validate_file` runs `llm -lastgoals`, whose exit 0 means the tactics
    PARSED. `repair_bootstrap` treated "every tactic applied" as fully
    replayed, so LQ1's `sampling_bound` -- 5 tactics that all apply while the
    goal stays open -- was reported COMPLETE with steps=0 and the agent was
    never invoked.
    """
    from integration.agent.config import AgentConfig
    from integration.agent.easycrypt import validate_file
    from integration.agent.proof_file import ProofFile, create_working_copy
    from integration.experiment.verify import is_proof_complete

    src = Path(__file__).resolve().parent / "fixtures" / "hoare_after_proof.ec"
    cfg = AgentConfig(easycrypt_bin=easycrypt_bin)
    work = tmp_path / "w.ec"
    create_working_copy(src, work_copy=work)
    proof = ProofFile(work)
    proof.append_tactic("proc.")

    # Parses fine ...
    assert validate_file(work, cfg).returncode == 0
    # ... but the proof is nowhere near closed.
    assert is_proof_complete(work, cfg) is False


def test_fully_replayed_requires_the_proof_to_close():
    """Pins the fix so a returncode-only check cannot come back."""
    import inspect

    from integration.experiment import repair_bootstrap as rb

    src = inspect.getsource(rb.run_replay_bootstrap_trial)
    assert "is_proof_complete(" in src, "fully_replayed must check completion"
    assert "fully_replayed = accepted_count == len(tactics) and is_proof_complete" in src

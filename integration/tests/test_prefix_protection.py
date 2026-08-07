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
    assert "smt(Lemma1 Lemma2)" in text   # space-separated; comma is a parse error
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


# --- comments are not tactics ----------------------------------------------
# `_original_tactics` split the tactic block on `.<whitespace>` WITHOUT
# stripping comments first. Comment prose contains sentence dots, so a comment
# was shredded into fragments and replayed as if it were a script.
#
# Joy's `games_quadruple` ends with
#     (* auto can replace wp. simplify. trivial  *)
# which turned 5 real tactics into 8: the replay "broke" on
# `(* auto can replace wp.`, truncating the verified prefix at 5/8 and handing
# the model comment text as the tactic to repair.
#
# Corpus-wide, before the fix: 19 of 33 Joy cases carried fake tactics
# (`two_to_ten` 60 -> 27, `ten_to_two` 31 -> 11, `triple1` 18 -> 5) and so did
# `INDCPA_HEG_G1` on ElGamal (52 -> 51), so this inflated the denominator of
# every "replayed N/M" figure that corpus produced.


def test_a_trailing_comment_is_not_three_tactics():
    from integration.experiment.repair_bootstrap import _strip_ec_comments

    block = "trivial.\n(* auto can replace wp. simplify. trivial  *)"
    assert "auto can replace" not in _strip_ec_comments(block)
    assert "trivial." in _strip_ec_comments(block)


def test_a_comment_before_a_tactic_keeps_the_tactic():
    """`(* note *) trivial.` IS a tactic -- dropping whole comment-bearing
    lines is the mistake the handoff records the tactic counter making."""
    from integration.experiment.repair_bootstrap import _strip_ec_comments

    assert _strip_ec_comments("(* note *) trivial.").strip() == "trivial."


def test_nested_comments_are_removed_whole():
    """EasyCrypt comments nest, so a regex stopping at the first `*)` would
    leave ` c *)` behind as tactic text."""
    from integration.experiment.repair_bootstrap import _strip_ec_comments

    assert _strip_ec_comments("(* a (* b *) c *) wp.").strip() == "wp."


def test_an_unterminated_comment_runs_to_the_end():
    from integration.experiment.repair_bootstrap import _strip_ec_comments

    assert _strip_ec_comments("wp. (* oops").strip() == "wp."


def test_qualified_names_survive_comment_stripping():
    from integration.experiment.repair_bootstrap import _strip_ec_comments

    assert _strip_ec_comments("smt(G1.bad).") == "smt(G1.bad)."


def test_the_real_joy_case_yields_five_tactics_not_eight():
    from integration.experiment.corpora.joy import JoyCorpus
    from integration.experiment.repair_bootstrap import _original_tactics

    case = next(c for c in JoyCorpus(data_dir=Path("data")).load_cases()
                if c.name == "games_quadruple")
    tactics = _original_tactics(case, case.file.read_text().splitlines())
    assert tactics == ["proc.", "inline *.", "wp.", "simplify.", "trivial."]


def test_no_corpus_case_replays_a_comment_as_a_tactic():
    """The invariant, across every corpus: nothing handed to append_tactic may
    contain comment syntax."""
    from integration.experiment.corpora.elgamal import ElGamalCorpus
    from integration.experiment.corpora.joy import JoyCorpus
    from integration.experiment.corpora.lq1 import LQ1Corpus
    from integration.experiment.repair_bootstrap import _original_tactics

    offenders = []
    for cls in (JoyCorpus, LQ1Corpus, ElGamalCorpus):
        for case in cls(data_dir=Path("data")).load_cases():
            for tactic in _original_tactics(case, case.file.read_text().splitlines()):
                if "(*" in tactic or "*)" in tactic:
                    offenders.append((case.name, tactic[:50]))
    assert not offenders, f"comment text replayed as a tactic: {offenders[:5]}"


# --- smt argument syntax ----------------------------------------------------
# `smt(a, b)` is a PARSE ERROR in EasyCrypt; the separator is a space.
# Verified against the real binary: comma -> "parse error", space -> the
# lemma lookup proceeds. Observed on G2_bad_ub, where 2 of 5 failures were the
# model writing a comma -- and the prompt was teaching it to.


def test_the_ambient_advice_uses_space_separated_smt_args():
    from integration.agent.prompt import format_active_goal_shape_hints

    text = format_active_goal_shape_hints(
        "Current goal\n\nx: int\n----------------\nforall (y : int), x + y = y + x"
    )
    assert "smt(Lemma1 Lemma2)" in text
    assert "smt(Lemma1, Lemma2)" not in text, "comma form is a parse error"
    assert "comma is a parse error" in text


def test_the_smt_failure_hint_warns_about_the_comma():
    """A parse error is not a failed proof, and the model retried the same
    comma form twice before finding out."""
    from integration.agent.loop import _enrich_error

    text = _enrich_error("cannot prove goal (strict)", "smt().", "goal")
    assert "SPACE-separated" in text
    assert "parse error, not a failed proof" in text


# --- names in scope, and what kind they are ---------------------------------
# 16 of 426 measured failures are name/scope errors, and 10 of those are
# `an hypothesis or variable named 'X' already exists` -- the model
# re-introducing `&1`/`&2` or a hypothesis the goal's own context block already
# lists. Nothing is fetched here; every fact is already on screen.

CTX_GOAL = (
    "Current goal (remaining: 2)\n\n"
    "Type variables: <none>\n\n"
    "&m: {}\n"
    "q1_L: exp\n"
    "q2_L: exp\n"
    "&1: {choice : bool, q1 : exp}\n"
    "&2: {choice : bool, grp1 : group}\n"
    "hpre: q2_L = q2{1} /\\\n"
    "      q1_L = q1{1}\n"
    "------------------------------------------------------------------------\n"
    "post = q1{1} = q1{2}"
)


def test_memories_with_digits_are_parsed():
    """`&1` / `&2` are the two names most often re-introduced by mistake. An
    `&?[A-Za-z_]...` pattern misses them and silently folds them into the
    PREVIOUS entry's statement."""
    from integration.agent.ec_context import MEMORY, parse_context

    entries = {e.name: e for e in parse_context(CTX_GOAL)}
    assert set(entries) == {"&m", "q1_L", "q2_L", "&1", "&2", "hpre"}
    assert entries["&1"].kind == MEMORY and entries["&2"].kind == MEMORY


def test_kinds_are_separated():
    from integration.agent.ec_context import HYPOTHESIS, VARIABLE, parse_context

    entries = {e.name: e.kind for e in parse_context(CTX_GOAL)}
    assert entries["q1_L"] == VARIABLE
    assert entries["hpre"] == HYPOTHESIS


def test_a_wrapped_hypothesis_is_one_entry():
    from integration.agent.ec_context import parse_context

    hpre = next(e for e in parse_context(CTX_GOAL) if e.name == "hpre")
    assert "q1_L = q1{1}" in hpre.statement, "continuation line was dropped"


def test_the_note_names_the_move_that_would_fail():
    """7 of the 16 name failures are exactly `move => &1 &2`."""
    from integration.agent.ec_context import format_context_note

    note = format_context_note(CTX_GOAL)
    assert "move => &1 &2" in note
    assert "already" in note


def test_the_note_states_the_smt_namespace_rule():
    from integration.agent.ec_context import format_context_note

    note = format_context_note(CTX_GOAL)
    assert "LIBRARY lemma names" in note
    assert "bare `smt()`" in note


def test_no_note_when_there_is_no_context():
    from integration.agent.ec_context import format_context_note

    assert format_context_note("Current goal\n\n-------\npost = true") == ""
    assert format_context_note("") == ""


def test_an_in_scope_name_gets_a_namespace_explanation():
    """`cannot find lemma 'hpre'` when hpre IS in scope needs the opposite
    response from a genuinely missing name."""
    from integration.agent.loop import _scope_hint_for_error

    hint = _scope_hint_for_error("cannot find lemma `hpre'", CTX_GOAL)
    assert "IS in scope" in hint and "local hypothesis" in hint
    assert "bare `smt()`" in hint


def test_a_genuinely_missing_name_falls_through_to_lookup():
    """Must stay silent so the existing search/lookup advice still applies."""
    from integration.agent.loop import _scope_hint_for_error

    assert _scope_hint_for_error("unknown lemma `andP'", CTX_GOAL) == ""


def test_the_context_section_reaches_the_prompt():
    from integration.agent.prompt import build_prompt

    text = build_prompt(goal=CTX_GOAL, top_premises={}, failed_tactics=[],
                        proof_tail="")
    assert "## Names already in scope" in text
    plain = build_prompt(goal="Current goal\n\n----\npost = true",
                         top_premises={}, failed_tactics=[], proof_tail="")
    assert "## Names already in scope" not in plain


# --- name resolution advice -------------------------------------------------
# The model invents lemma names (`andP`, `hemma`) and confuses namespaces
# (`smt(hpre)` where hpre is a local hypothesis). EasyCrypt reports both as
# `cannot find lemma 'X'`, which does not distinguish them.
#
# This NEVER blocks a tactic, and that is measured rather than cautious: the
# Ax.all catalog has false negatives. `rpow_hmono` is absent from it yet is
# part of G2_bad_ub's own original proof and replays successfully, so a
# rejecting pre-check would have blocked a working tactic.

CATALOG = {
    "Logic.andrP": "a", "Logic.andlP": "b", "Logic.andaP": "c",
    "Ring.IntID.addr0": "d", "RealExp.rpowe_hmono": "e",
}


def test_only_lemma_position_names_are_extracted():
    """A tactic is full of variables and binders; checking every token would
    produce noise about names never meant to resolve to lemmas."""
    from integration.agent.ec_names import referenced_lemma_names

    assert referenced_lemma_names("smt(Top.grexpA Top.inj).") == [
        "Top.grexpA", "Top.inj"]
    assert referenced_lemma_names("apply/andP; split.") == ["andP"]
    assert referenced_lemma_names("by rewrite addr0.") == ["addr0"]
    # No lemma position at all.
    assert referenced_lemma_names("wp; skip; smt().") == []
    assert referenced_lemma_names("move => &1 &2 hpre.") == []


def test_a_bare_basename_counts_as_resolved():
    """The catalog is keyed by qualified path while the model writes bare
    names, so `addr0` must not be reported missing."""
    from integration.agent.ec_names import resolves

    assert resolves("addr0", CATALOG)
    assert resolves("Ring.IntID.addr0", CATALOG)
    assert not resolves("nonesuch", CATALOG)


def test_an_in_scope_hypothesis_is_explained_not_searched():
    from integration.agent.ec_names import name_advice

    text = name_advice("smt(hpre).", CTX_GOAL, CATALOG, "cannot find lemma `hpre'")
    assert "local hypothesis already in scope" in text
    assert "bare `smt()`" in text
    assert "Closest catalog entries" not in text, "should not search for it"


def test_an_invented_name_gets_the_nearest_catalog_entries():
    from integration.agent.ec_names import name_advice

    text = name_advice("apply/andP.", CTX_GOAL, CATALOG, "unknown lemma `andP'")
    assert "not in the lemma catalog" in text
    assert "Logic.and" in text, "should suggest the near misses"


def test_a_name_with_no_near_match_says_so():
    from integration.agent.ec_names import name_advice

    text = name_advice("exact hemma.", CTX_GOAL, CATALOG, "unknown lemma `hemma'")
    assert "nothing similar" in text
    assert "search_lemmas" in text


def test_a_working_tactic_is_never_second_guessed():
    """THE false-positive guard. `rpow_hmono` is absent from the catalog and
    works; without gating on a name error, the model would be told its
    successful tactic used a bad name."""
    from integration.agent.ec_names import name_advice

    assert name_advice("apply rpow_hmono.", CTX_GOAL, CATALOG, "") == ""
    assert name_advice(
        "apply rpow_hmono.", CTX_GOAL, CATALOG, "cannot prove goal (strict)"
    ) == ""
    # But a genuine name error still reports it.
    assert name_advice(
        "apply rpow_hmono.", CTX_GOAL, CATALOG, "unknown lemma `rpow_hmono'"
    ) != ""


def test_the_in_scope_explanation_does_not_need_a_name_error():
    """It reads the goal, not the catalog, so it is always safe."""
    from integration.agent.ec_names import name_advice

    assert "local hypothesis" in name_advice("smt(hpre).", CTX_GOAL, CATALOG, "")


def test_no_catalog_means_no_catalog_advice():
    from integration.agent.ec_names import name_advice

    assert name_advice("apply/andP.", CTX_GOAL, None, "unknown lemma `andP'") == ""


# --- resolve_goal is lossy, and a comment is not a mechanism ----------------
# `resolve_goal` returns "" after a goal-unchanged tactic (it walks back past
# each unchanged cursor and gives up). Callers that read "" as "the proof is
# discharged" are wrong, and this has caused THREE separate bugs:
#
#   * confirm_noop, before it was given `_current_goal`
#   * _probe_compound_subgoal, which reported "Step 1/4 `rnd.` OK -- no open
#     goal" while the real error was `left instruction list is not empty`
#   * repair_bootstrap's fully_replayed, which called a proof that does not
#     close COMPLETE with steps=0
#
# The docs said "any new caller must use _current_goal". The third bug landed
# after that was written, so this pins the call sites instead.

#: (module, enclosing function) pairs allowed to call `resolve_goal` directly,
#: each because it does NOT treat an empty result as a discharged proof.
_ALLOWED_LOSSY_CALLERS = {
    # Falls through to _probe_qed_discharge rather than trusting "".
    ("integration/agent/easycrypt.py", "is_proof_complete_at_cursor"),
    # Startup logging only; the value is cosmetic.
    ("integration/agent/loop.py", "run_agent"),
    # THE correct accessor -- this is the fallback everyone else should use.
    ("integration/agent/loop.py", "_current_goal"),
    ("integration/agent/loop.py", "_startup"),
}


def test_no_new_lossy_goal_callers():
    import ast

    root = Path(__file__).resolve().parents[2]
    found = set()
    for rel in ("integration/agent/easycrypt.py", "integration/agent/loop.py",
                "integration/agent/prompt.py", "integration/agent/proof_file.py",
                "integration/experiment/repair_bootstrap.py",
                "integration/experiment/runner.py"):
        path = root / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "resolve_goal"):
                    found.add((rel, node.name))

    new = found - _ALLOWED_LOSSY_CALLERS
    assert not new, (
        "New direct `resolve_goal` call(s): "
        + ", ".join(f"{m}::{f}" for m, f in sorted(new))
        + ". It returns '' after a goal-unchanged tactic, which is NOT the "
        "same as a discharged proof -- three bugs have come from that. Use "
        "`loop._current_goal`, which falls through to the raw `llm -upto` "
        "output. If this caller genuinely tolerates the empty return, add it "
        "to _ALLOWED_LOSSY_CALLERS with the reason."
    )


# --- 8c.2: EasyCrypt's own seq limit beats our computed counts --------------


def test_the_split_index_limit_is_parsed():
    from integration.agent.ec_program import split_index_limit

    assert split_index_limit("[critical] invalid split index: ^<5") == 5
    assert split_index_limit("invalid split index: ^<4") == 4
    assert split_index_limit("cannot prove goal (strict)") is None
    assert split_index_limit("") is None


def test_a_known_limit_supersedes_the_computed_ceiling():
    """11 of 12 failed `seq` attempts were INSIDE the counts; twice EasyCrypt
    named a far smaller K (`^<5` against a count of 13). Restating the ceiling
    the model has already been rejected inside of is useless."""
    from integration.agent.ec_program import parse_program_block
    from integration.agent.prompt import _seq_position_bullets

    block = (Path(__file__).resolve().parent / 'fixtures'
             / 'elgamal_equiv_block.txt').read_text(encoding='utf-8')
    pair = parse_program_block(block)
    text = " ".join(_seq_position_bullets(pair, split_limit=5))
    assert "strictly less than 5" in text
    assert "at most 4" in text
    # And it must contrast with the ceiling rather than repeat it.
    assert "not 13" in text


def test_without_a_known_limit_it_says_how_to_get_one():
    from integration.agent.ec_program import parse_program_block
    from integration.agent.prompt import _seq_position_bullets

    block = (Path(__file__).resolve().parent / 'fixtures'
             / 'elgamal_equiv_block.txt').read_text(encoding='utf-8')
    text = " ".join(_seq_position_bullets(parse_program_block(block)))
    assert "^<K" in text
    assert "strictly less than 5" not in text


def test_the_loop_records_and_replays_the_limit():
    import inspect

    from integration.agent import loop as loop_mod

    src = inspect.getsource(loop_mod.run_agent)
    assert "split_limit_by_goal[_goal_hash(goal)] = limit" in src, "not recorded"
    assert "split_limit=split_limit_by_goal.get(_goal_hash(goal))" in src, "not replayed"


# --- 8c.3: ask EasyCrypt for the logic class instead of guessing ------------


@pytest.mark.integration
def test_the_probe_is_decisive_where_text_classification_is_not(
    tmp_path, easycrypt_bin
):
    """`goal_looks_program_logic` calls 91% of discharged judgments
    program-logic, because the printed form genuinely does not distinguish
    them. The probe does, at ~1.5s against a ~170s model call."""
    from integration.agent.config import AgentConfig
    from integration.agent.easycrypt import probe_is_program_logic
    from integration.agent.proof_file import ProofFile, create_working_copy

    src = Path(__file__).resolve().parent / "fixtures" / "hoare_after_proof.ec"
    cfg = AgentConfig(easycrypt_bin=easycrypt_bin)
    work = tmp_path / "w.ec"
    create_working_copy(src, work_copy=work)
    proof = ProofFile(work)

    proof.append_tactic("proc.")
    assert probe_is_program_logic(proof, cfg) is True

    # `skip.` discharges the judgment; the goal still prints pre/post-ish text
    # but no program-logic tactic applies any more.
    proof.append_tactic("skip.")
    assert probe_is_program_logic(proof, cfg) is False


@pytest.mark.integration
def test_the_probe_leaves_the_file_byte_identical(tmp_path, easycrypt_bin):
    from integration.agent.config import AgentConfig
    from integration.agent.easycrypt import probe_is_program_logic
    from integration.agent.proof_file import ProofFile, create_working_copy

    src = Path(__file__).resolve().parent / "fixtures" / "hoare_after_proof.ec"
    work = tmp_path / "w.ec"
    create_working_copy(src, work_copy=work)
    proof = ProofFile(work)
    proof.append_tactic("proc.")
    before = work.read_text(encoding="utf-8")
    probe_is_program_logic(proof, AgentConfig(easycrypt_bin=easycrypt_bin))
    assert work.read_text(encoding="utf-8") == before


def test_an_inconclusive_probe_returns_none_not_a_guess():
    """`wp.` fails on plenty of LIVE program-logic goals. Only the specific
    wrong-class error means ambient; anything else must be `None` so callers
    fall back rather than assuming."""
    import inspect

    from integration.agent import easycrypt as ec

    src = inspect.getsource(ec.probe_is_program_logic)
    assert "return None" in src
    assert "expecting a goal of the form" in inspect.getsource(ec)


# --- 8c.4: version hopping never needed the fork's goal printer -------------
# `llm` was added to the fork in da4935c9 (2026-04-11), so 11 of the 14
# buildable release tags do not have it and `validate_file` cannot run against
# them at all. That was read as "version hopping is blocked".
#
# It asks one question -- does this file still check out at release R -- and
# upstream `compile` answers it at every release. Verified against both
# binaries: `llm -lastgoals` and `compile` return the same code on a passing
# and a failing proof, and `compile` works on r2025.02 where `llm` is an
# unknown option.


def test_the_hop_probe_uses_compile_not_the_fork_command():
    import inspect

    from integration.experiment import version_hop

    src = inspect.getsource(version_hop.probe_version)
    assert "check_file_compat" in src
    assert "validate_file" not in src, "llm -lastgoals is absent from 11 of 14 tags"


def test_check_file_compat_invokes_compile(monkeypatch):
    from integration.agent import easycrypt as ec
    from integration.agent.config import AgentConfig

    seen = {}

    def fake_run(argv, config):
        seen["argv"] = argv
        return ec.LlmResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ec, "run_llm", fake_run)
    ec.check_file_compat(Path("/tmp/x.ec"), AgentConfig())
    assert seen["argv"][0] == "compile", seen["argv"]
    assert "-lastgoals" not in seen["argv"]


@pytest.mark.integration
def test_compile_agrees_with_lastgoals_on_the_modern_binary(tmp_path, easycrypt_bin):
    """If they disagreed, swapping them would change what hopping concludes."""
    from integration.agent.config import AgentConfig
    from integration.agent.easycrypt import check_file_compat, validate_file

    cfg = AgentConfig(easycrypt_bin=easycrypt_bin)
    good = tmp_path / "good.ec"
    good.write_text(
        "require import AllCore.\n\nlemma t (x : int) : x = x.\nproof.\n"
        "trivial.\nqed.\n", encoding="utf-8")
    bad = tmp_path / "bad.ec"
    bad.write_text(
        "require import AllCore.\n\nlemma t (x : int) : x * x = 7.\nproof.\n"
        "smt().\nqed.\n", encoding="utf-8")

    for path in (good, bad):
        a = validate_file(path, cfg).returncode == 0
        b = check_file_compat(path, cfg).returncode == 0
        assert a == b, f"{path.name}: lastgoals={a} compile={b}"

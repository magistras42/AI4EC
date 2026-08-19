"""Inert REPEATS of a tactic (narrowed from the original 60% finding).

The wider rule -- "goal text unchanged means the tactic did nothing" -- is
UNSOUND, and the fixture in this repo disproves it: on
`fixtures/hoare_after_proof.ec`, `skip.` leaves `resolve_goal` byte-identical
(166 chars both sides) and is load-bearing; delete it and the proof no longer
closes. The goal display is a lossy view of the proof state.

Measured across three ElGamal runs, 60-63% of ACCEPTED tactics left the goal
byte-identical, and both STUCK outcomes in run E were a run of them -- one
lemma ended with 45 of its 58 lines a bare `wp.`, all 39 trailing ones
provably removable.

The fix is not to give up but to use TWO views. `resolve_goal` is lossy for
`skip.`; the raw `llm -upto` cursor is lossy for `proc.`. Each mis-classifies
a tactic the other gets right, so both must agree nothing moved -- and then
removal has to confirm it.
"""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from integration.agent import loop as loop_mod
from integration.agent.config import AgentConfig
from integration.agent.easycrypt import LlmResult
from integration.agent.proof_file import ProofFile
from integration.agent.prompt import build_prompt

SCRIPT = """lemma l : true.
proof.
  move => H.
  wp.
qed.
"""


@pytest.fixture
def proof(tmp_path) -> ProofFile:
    path = tmp_path / "p.ec"
    path.write_text(SCRIPT, encoding="utf-8")
    return ProofFile(path)


class FakeGoals:
    """Maps a cursor to the goal text EasyCrypt would print there."""

    def __init__(self, by_cursor, on_missing=""):
        self.by_cursor = by_cursor
        self.on_missing = on_missing
        self.calls: list[int] = []

    def __call__(self, path, cursor, config):
        self.calls.append(cursor)
        return LlmResult(
            returncode=0,
            stdout=self.by_cursor.get(cursor, self.on_missing),
            stderr="",
        )


def _config() -> AgentConfig:
    return AgentConfig(easycrypt_bin=Path("/nonexistent/ec.exe"))


# --- detection: two observables must agree, then removal proves it ---------


def _goals(resolve, raw_at, raw_prev):
    """Patch both views independently."""
    def fake_resolve(p, c):
        return resolve() if callable(resolve) else resolve
    def fake_fetch(path, cursor, config):
        text = raw_at if cursor == 4 else raw_prev
        return LlmResult(returncode=0, stdout=text, stderr="")
    return fake_resolve, fake_fetch


def _patch(monkeypatch, resolve, raw_at, raw_prev):
    r, f = _goals(resolve, raw_at, raw_prev)
    monkeypatch.setattr(loop_mod, "resolve_goal", r)
    monkeypatch.setattr(loop_mod, "fetch_goal", f)


def test_both_views_unchanged_and_removal_confirms_is_a_no_op(proof, monkeypatch):
    _patch(monkeypatch, "GOAL A", "RAW", "RAW")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is True
    assert "wp." not in proof.path.read_text(encoding="utf-8")


def test_resolve_alone_is_not_enough_the_skip_case(proof, monkeypatch):
    """`skip.` renders byte-identical through resolve_goal and is
    load-bearing. The raw view sees it, so the conjunction keeps it."""
    _patch(monkeypatch, "GOAL A", "RAW AFTER", "RAW BEFORE")
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is False
    assert proof.path.read_text(encoding="utf-8") == before


def test_raw_alone_is_not_enough_the_proc_case(proof, monkeypatch):
    """`llm -upto N` is not a faithful state-after for `proc.`; the raw view
    calls it inert. resolve_goal sees it, so the conjunction keeps it."""
    _patch(monkeypatch, "GOAL B", "RAW", "RAW")
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is False
    assert proof.path.read_text(encoding="utf-8") == before


def test_removal_that_changes_the_goal_puts_the_tactic_back(proof, monkeypatch):
    """Stage 3. Both views looked inert, but the state moves once it is gone,
    so it was load-bearing and is restored untouched."""
    original = proof.path.read_text(encoding="utf-8")
    def resolve(p, c):
        return "GOAL A" if "wp." in Path(p.path).read_text(encoding="utf-8") else "OTHER"
    monkeypatch.setattr(loop_mod, "resolve_goal", resolve)
    monkeypatch.setattr(loop_mod, "fetch_goal",
                        lambda path, cursor, config: LlmResult(0, "RAW", ""))
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is False
    assert proof.path.read_text(encoding="utf-8") == original


def test_an_empty_goal_is_never_treated_as_a_no_op(proof, monkeypatch):
    _patch(monkeypatch, "", "RAW", "RAW")
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is False
    assert proof.path.read_text(encoding="utf-8") == before


def test_an_empty_raw_goal_is_never_a_no_op(proof, monkeypatch):
    _patch(monkeypatch, "GOAL A", "", "")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is False


def test_the_first_proof_line_is_never_probed(proof, monkeypatch):
    called = []
    monkeypatch.setattr(loop_mod, "resolve_goal",
                        lambda p, c: called.append(1) or "GOAL A")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 1) is False
    assert called == []


def test_an_unreadable_file_fails_safe(proof, monkeypatch):
    def boom(p, c):
        raise OSError("disk gone")
    monkeypatch.setattr(loop_mod, "resolve_goal", boom)
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4) is False
    assert proof.path.read_text(encoding="utf-8") == before


# --- the ban is scoped to the goal ------------------------------------------


def test_the_ban_is_keyed_to_the_goal_not_the_tactic():
    """`wp.` doing nothing in one state says nothing about the next. A global
    ban would be exactly the overreach this design avoids."""
    a, b = loop_mod._goal_hash("GOAL A"), loop_mod._goal_hash("GOAL B")
    assert a != b
    bans = {a: {"wp."}}
    assert "wp." in bans.get(a, set())
    assert "wp." not in bans.get(b, set())


def test_the_same_goal_hashes_the_same_after_whitespace_noise():
    assert loop_mod._goal_hash(" GOAL A\n") == loop_mod._goal_hash("GOAL A")


# --- what the model is told -------------------------------------------------


def test_the_prompt_distinguishes_a_no_op_from_a_failure():
    """Opposite failure modes. A model that reads "accepted" tries the same
    thing again -- that is how one lemma ended with 45 bare `wp.` lines."""
    text = build_prompt(
        goal="GOAL A", top_premises={}, failed_tactics=[], proof_tail="",
        noop_tactics=["wp.", "auto."],
    )
    assert "compiled but did NOTHING here" in text
    assert "`wp.`" in text and "`auto.`" in text
    assert "byte-identical" in text
    # It must say the bar lifts, or the model will avoid them forever.
    assert "available again as soon as the goal changes" in text


def test_no_section_when_nothing_was_inert():
    text = build_prompt(
        goal="G", top_premises={}, failed_tactics=[], proof_tail="",
        noop_tactics=[],
    )
    assert "compiled but did NOTHING" not in text


# --- the unsoundness this design is narrowed around -------------------------


def test_goal_text_equality_is_not_proof_of_inertness(tmp_path, easycrypt_bin):
    """The experiment that forced the narrowing, pinned as a test.

    On this fixture `skip.` leaves `resolve_goal` byte-identical AND is
    load-bearing: without it the proof does not close. Any future widening of
    `confirm_noop` to first applications has to survive this."""
    from integration.agent.easycrypt import resolve_goal, validate_file
    from integration.agent.proof_file import create_working_copy

    src = Path(__file__).resolve().parent / "fixtures" / "hoare_after_proof.ec"
    cfg = AgentConfig(easycrypt_bin=easycrypt_bin)

    # `skip.` does not move the displayed goal ...
    w = tmp_path / "a.ec"
    create_working_copy(src, w)
    p = ProofFile(w)
    p.append_tactic("proc.")
    before = resolve_goal(p, cfg).strip()
    p.append_tactic("skip.")
    assert resolve_goal(p, cfg).strip() == before, "fixture changed"

    # ... and yet the proof does not close without it.
    full = ["proc.", "skip.", "move => &m H1.", "subst.", "trivial."]
    w2 = tmp_path / "b.ec"
    create_working_copy(src, w2)
    p2 = ProofFile(w2)
    for tac in full:
        p2.append_tactic(tac)
    assert validate_file(w2, cfg).returncode == 0

    w3 = tmp_path / "c.ec"
    create_working_copy(src, w3)
    p3 = ProofFile(w3)
    for tac in [t for t in full if t != "skip."]:
        p3.append_tactic(tac)
    assert validate_file(w3, cfg).returncode != 0, (
        "skip. must be load-bearing here; if this fails the fixture changed "
        "and the narrowing argument needs rechecking"
    )


@pytest.mark.integration
def test_subgoal_count_does_not_rescue_the_skip_case(tmp_path, easycrypt_bin):
    """Why `confirm_noop` still uses two goal-text views and not a count.

    The handoff `docs/PROOF_REPAIR_NEXT_HANDOFF.md` §2 proposed replacing the
    primary signal with the subgoal count from shannon-prover's
    `ec_state_diff.py`, on the reading that its `PROGRESS_DECOMPOSITION` arm
    "is exactly the `skip.` case". It is not. `skip.` converts a Hoare
    judgment into an ambient goal WITHOUT splitting it, so the count is 1 on
    both sides -- in both views -- and a count-primary detector calls it inert
    and deletes it, which the test above shows breaks the proof.

    Running their `compute_state_diff` on this transition returns
    NEUTRAL_OR_NO_CHANGE, not PROGRESS_DECOMPOSITION, for both views.
    """
    from integration.agent.easycrypt import fetch_goal, resolve_goal
    from integration.agent.goal_diff import subgoal_count
    from integration.agent.proof_file import create_working_copy

    src = Path(__file__).resolve().parent / "fixtures" / "hoare_after_proof.ec"
    cfg = AgentConfig(easycrypt_bin=easycrypt_bin)

    w = tmp_path / "a.ec"
    create_working_copy(src, w)
    p = ProofFile(w)
    p.append_tactic("proc.")
    resolve_before = resolve_goal(p, cfg).strip()
    raw_before = fetch_goal(w, p.last_tactic_line(), cfg).stdout.strip()

    p.append_tactic("skip.")
    resolve_after = resolve_goal(p, cfg).strip()
    raw_after = fetch_goal(w, p.last_tactic_line(), cfg).stdout.strip()

    # The count is blind to it in BOTH views ...
    assert subgoal_count(resolve_before) == subgoal_count(resolve_after) == 1
    assert subgoal_count(raw_before) == subgoal_count(raw_after) == 1
    # ... and the raw view is the only observable that moves at all, which is
    # precisely the conjunction `confirm_noop` relies on.
    assert resolve_before == resolve_after
    assert raw_before != raw_after


def test_the_goal_view_matches_what_the_loop_shows_the_model(proof, monkeypatch):
    """After a run of unchanged tactics `resolve_goal` returns "" -- it walks
    back past each unchanged cursor and gives up -- and the loop falls through
    to the raw `llm -upto` output. Checking `resolve_goal` alone saw the empty
    string and refused to act, so the real 39-`wp.` run went undetected."""
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda p, c: "")
    monkeypatch.setattr(loop_mod, "resolve_goal_cursor", lambda p, c: 4)
    monkeypatch.setattr(loop_mod, "fetch_goal",
                        lambda path, cursor, config: LlmResult(0, "RAW GOAL", ""))
    assert loop_mod._current_goal(proof, _config()) == "RAW GOAL"
    assert loop_mod.confirm_noop(proof, _config(), "RAW GOAL", 4) is True


# --- enforcement, not advice ------------------------------------------------
# Telling the model in the prompt was not enough. On run G an already-banned
# no-op was re-sent 7 times on G2_G3 (`rnd; skip; smt().`) and 7 times on
# INDCPA_HEG_G1 (`auto.`) -- 13 wasted calls, each also driving the stuck
# counter. Failed duplicates have always been hard-rejected; inert ones are
# now too.


def test_a_banned_no_op_is_refused_before_it_reaches_easycrypt(
    tmp_path, easycrypt_bin, monkeypatch
):
    """End-to-end through run_agent: the model keeps proposing a tactic that
    does nothing. The first is removed; every repeat must be rejected without
    an EasyCrypt call, not silently re-applied."""
    from integration.agent import run_agent
    from integration.agent.proof_file import create_working_copy

    source = Path(__file__).resolve().parent / "fixtures" / "hoare_after_proof.ec"
    work = tmp_path / "w.agent.ec"
    create_working_copy(source, work)

    log_file = tmp_path / "agent_log.json"
    config = AgentConfig(
        easycrypt_bin=easycrypt_bin, top_k=2, max_steps=8, max_premises=10,
        llm_model="mock", embed_model="mock-embed", stuck_limit=99,
        log_file=log_file,
    )

    # `proc.` opens the bodies; then the model insists on a tactic that will
    # compile and change nothing.
    script = ["proc."] + ["wp."] * 7
    idx = {"i": 0}

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction
            i = min(idx["i"], len(script) - 1)
            idx["i"] += 1
            return LlmDecision(action=TacticAction(tactic=script[i]))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"
        def embed(self, _text):
            import numpy as np
            return np.array([1.0, 0.0])
        def build_index(self, premises):
            import numpy as np
            return {n: np.array([1.0, 0.0]) for n in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _c: FakeLlm())
    monkeypatch.setattr("integration.agent.loop.EmbeddingClient", lambda _c: FakeEmbedder())

    run_agent(source, config, work_copy=work)

    events = json.loads(log_file.read_text(encoding="utf-8"))["events"]
    steps = [e for e in events if e.get("event") == "iteration"]
    outcomes = [e.get("outcome") for e in steps]

    # The first inert `wp.` is detected and removed ...
    assert "no_op" in outcomes, f"no_op never fired: {outcomes}"
    # ... and every later attempt at it is REJECTED before EasyCrypt sees it.
    rejected = [e for e in steps
                if e.get("outcome") == "rejected"
                and "left it completely unchanged" in (e.get("error") or "")]
    assert rejected, (
        f"repeats were not hard-rejected, only advised against: {outcomes}"
    )
    # And the proof never accumulates the inert tactic.
    assert work.read_text(encoding="utf-8").count("wp.") <= 1


# --- repair the broken tactic first -----------------------------------------
# The harness always showed the original script, but as reference headed
# "adapt rather than paste" -- and the model treated it that way. On run G's
# G2_G3: 8 of 40 attempts reused an original tactic verbatim, and 19 of 40
# were `rnd` variants although the original uses `rnd` exactly once. It even
# tried the right tactic and compounded three original lines into one.


def test_the_broken_tactic_is_stated_as_the_task_not_as_reference():
    from integration.agent.prompt import format_broken_tactic_repair
    text = format_broken_tactic_repair("rnd(fun x => t{1} +^ x).", None)
    assert "Repair THIS tactic first" in text
    assert "rnd(fun x => t{1} +^ x)." in text
    assert "not to invent a different approach" in text


def test_the_ladder_is_keyed_to_what_easycrypt_actually_said():
    """~45% of failures are position errors inside the RIGHT tactic class, so
    the advice has to name the edit that error calls for -- and opposite ends
    of the program share one `ec_errors` kind."""
    from integration.agent.prompt import format_broken_tactic_repair

    last = format_broken_tactic_repair("rnd.", "[critical] invalid last instruction")
    first = format_broken_tactic_repair("if.", "[critical] invalid first instruction")
    assert "consume from the END" in last
    assert "address the FIRST instruction" in first
    assert last != first


def test_a_class_mismatch_says_to_abandon_the_tactic():
    """The one rung where keeping the tactic is precisely the mistake."""
    from integration.agent.prompt import format_broken_tactic_repair
    text = format_broken_tactic_repair(
        "wp.", "[critical] expecting a goal of the form: hoare[S], equiv[S]")
    assert "WRONG LOGIC CLASS" in text
    assert "no program-logic tactic will apply" in text


def test_compounds_are_called_out_because_that_was_the_observed_miss():
    from integration.agent.prompt import format_broken_tactic_repair
    text = format_broken_tactic_repair("rnd.", None)
    assert "Split a compound" in text
    assert "fails whole even when its first component was right" in text


def test_an_unrecognised_error_still_gives_the_ordered_procedure():
    from integration.agent.prompt import format_broken_tactic_repair
    text = format_broken_tactic_repair("rnd.", "something nobody has seen")
    assert "Order of attack" in text
    assert "What that error calls for" not in text   # no invented advice


def test_no_section_without_a_broken_tactic():
    from integration.agent.prompt import format_broken_tactic_repair
    assert format_broken_tactic_repair("", "err") == ""


def test_the_section_only_appears_on_the_first_step():
    """Once the model has moved the proof on, the original break is no longer
    the tactic to repair and pinning attention to it would mislead."""
    import inspect
    from integration.agent import loop as loop_mod
    src = inspect.getsource(loop_mod.run_agent)
    assert "config.broken_tactic if step == 1 else None" in src


def test_a_no_op_neither_resets_nor_increments_the_stuck_counter():
    """Two claims were bundled and the data separated them: "this step was
    wasted" is not "this trial is going nowhere". Incrementing made G2_G3 die
    progressively earlier (75 -> 44 -> 27 steps) while the share of steps
    that changed nothing stayed flat at ~50% -- the counter was shortening the
    runway, not reducing the waste."""
    import inspect
    from integration.agent import loop as loop_mod

    src = inspect.getsource(loop_mod.run_agent)
    block = src[src.index("if confirm_noop("):src.index("state_hash = _proof_state_hash")]
    assert "_increment_stuck" not in block, (
        "a confirmed no-op must not drive the stuck counter"
    )
    assert "stuck_counter = 0" not in block, (
        "nor reset it -- a no-op is not progress either"
    )
    # It must still be able to end a trial that is genuinely stuck.
    assert "_check_stuck" in block


# --- the stuck budget, behaviourally (§5) -----------------------------------
# `docs/PROOF_REPAIR_NEXT_HANDOFF.md` §5 records that no-ops were removed from
# the stuck counter after run H and that NO run has executed with the change.
# The end-to-end ElGamal measurement it asks for needs a solver LLM and is not
# runnable here (see the doc). What IS testable is the mechanism the concern is
# about: whether a run of inert steps eats the runway. The test above pins the
# accounting by reading the source; this one pins the consequence.


def _scripted_run(monkeypatch, tmp_path, tactics, *, inert, stuck_limit, max_steps):
    """Drive `run_agent` with EasyCrypt and the model both stubbed."""
    from types import SimpleNamespace

    from integration.agent import loop as loop_mod
    from integration.agent.llm import LlmDecision, TacticAction

    proof = tmp_path / "p.ec"
    proof.write_text("lemma x : true.\nproof.\n", encoding="utf-8")
    config = AgentConfig(
        easycrypt_bin=Path("/nonexistent/ec.exe"),
        max_steps=max_steps, stuck_limit=stuck_limit, output_dir=tmp_path,
    )

    seen = {"calls": 0}

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            i = seen["calls"]
            seen["calls"] += 1
            return LlmDecision(action=TacticAction(tactic=tactics[i % len(tactics)]))

    monkeypatch.setattr(loop_mod, "LlmClient", lambda _c: FakeLlm())
    monkeypatch.setattr(loop_mod, "_startup", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "_build_premise_index", lambda *a, **k: ({}, {}, {}))
    monkeypatch.setattr(loop_mod, "EmbeddingClient",
                        lambda *a, **k: SimpleNamespace(embed=lambda t: None))
    monkeypatch.setattr(loop_mod, "rank_by_cosine", lambda *a, **k: [])
    monkeypatch.setattr(loop_mod, "top_premises", lambda *a, **k: {})
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda *a, **k: "goal: x = x")
    monkeypatch.setattr(loop_mod, "resolve_goal_cursor", lambda *a, **k: 1)
    monkeypatch.setattr(loop_mod, "fetch_goal", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="goal", stderr=""))
    monkeypatch.setattr(loop_mod, "validate_file", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(loop_mod, "is_proof_discharged", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "tactic_discharged_proof", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "is_no_active_proof", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "is_proof_complete_at_cursor", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "confirm_noop", lambda *a, **k: inert)

    result = loop_mod.run_agent(proof, config, work_copy=tmp_path / "w.agent.ec")
    return result, loop_mod, seen["calls"]


def test_a_run_of_no_ops_keeps_the_full_runway(monkeypatch, tmp_path):
    """Eight consecutive inert steps against a stuck limit of 3.

    This is the change run H has never exercised. Coupling "this step was
    wasted" to "this trial is going nowhere" made G2_G3 die progressively
    earlier -- 75 steps, then 44, then 27 -- while the share of steps that
    changed nothing stayed flat near 50%, so the counter was shortening the
    runway rather than reducing the waste.
    """
    # Eight DISTINCT tactics: each is a fresh no-op at this goal, never a
    # repeat of a banned one. Repeats are a different charge, and the test
    # below is the one that pins them.
    result, loop_mod, calls = _scripted_run(
        monkeypatch, tmp_path,
        ["wp.", "auto.", "simplify.", "trivial.", "progress.", "sp.",
         "subst.", "smt()."],
        inert=True, stuck_limit=3, max_steps=8,
    )
    assert result.reason is loop_mod.ExitReason.MAX_STEPS, (
        f"a run of fresh no-ops ended the trial early as {result.reason}"
    )
    assert calls == 8, "the trial must keep its whole step budget"


def test_repeating_one_banned_no_op_still_ends_the_trial(monkeypatch, tmp_path):
    """The other half, and the reason the counter can be spared no-ops safely.

    A no-op is removed and barred at its goal, so proposing it again is a hard
    rejection and DOES drive the counter. Sparing the first occurrence costs
    nothing, because a model that cannot move on is still caught here -- which
    matters, since the proof-state hash cannot catch it: a no-op appends a line
    before it is removed, so the tail hash is new every time.
    """
    result, loop_mod, _ = _scripted_run(
        monkeypatch, tmp_path, ["wp."], inert=True, stuck_limit=3, max_steps=20,
    )
    assert result.reason is loop_mod.ExitReason.STUCK

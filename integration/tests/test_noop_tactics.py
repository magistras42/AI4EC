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

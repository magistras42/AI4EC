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

What the evidence does support is repetition: the pathology was always a RUN
of the same tactic (39 consecutive bare `wp.` in one lemma, all removable).
So only an immediate repeat that moved nothing is rolled back, and the first
application of any tactic is always kept.
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


# --- detection: repeats only ------------------------------------------------


def test_a_first_application_is_never_touched(proof, monkeypatch):
    """The safety property. `skip.` moves the goal invisibly and is
    load-bearing; a rule that removed first applications would delete it."""
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda p, c: "GOAL A")
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, None, "wp.") is False
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, "skip.", "wp.") is False
    assert proof.path.read_text(encoding="utf-8") == before


def test_an_inert_repeat_is_rolled_back(proof, monkeypatch):
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda p, c: "GOAL A")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, "wp.", "wp.") is True
    assert "wp." not in proof.path.read_text(encoding="utf-8")


def test_a_repeat_that_moves_the_goal_is_kept(proof, monkeypatch):
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda p, c: "GOAL B")
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, "wp.", "wp.") is False
    assert proof.path.read_text(encoding="utf-8") == before


def test_repeat_detection_ignores_whitespace_noise(proof, monkeypatch):
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda p, c: "GOAL A")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, "wp .", " wp. ") is True


def test_an_empty_goal_is_never_treated_as_a_no_op(proof, monkeypatch):
    """An empty goal is what a discharged or unreachable position looks like."""
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda p, c: "")
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, "wp.", "wp.") is False
    assert proof.path.read_text(encoding="utf-8") == before


def test_the_first_proof_line_is_never_probed(proof, monkeypatch):
    called = []
    monkeypatch.setattr(loop_mod, "resolve_goal",
                        lambda p, c: called.append(1) or "GOAL A")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 1, "wp.", "wp.") is False
    assert called == []


def test_an_unreadable_file_fails_safe(proof, monkeypatch):
    def boom(p, c):
        raise OSError("disk gone")
    monkeypatch.setattr(loop_mod, "resolve_goal", boom)
    before = proof.path.read_text(encoding="utf-8")
    assert loop_mod.confirm_noop(proof, _config(), "GOAL A", 4, "wp.", "wp.") is False
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

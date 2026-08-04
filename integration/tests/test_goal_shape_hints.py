"""Goal-shape hints must not misread EasyCrypt's program-logic dumps.

Grounded in `fixtures/goals/elgamal_goals.json` -- 390 real goal/outcome
records captured from live DeepSeek runs. Before this, the prompt advised
`skip.` on 121 of them and the chosen tactic then failed on 53, because three
parsing defects made non-empty programs look empty:

  1. only ONE line after `pre =` was dropped, so a multi-line precondition
     leaked in and was mistaken for statements;
  2. `<$` (random sampling) matched no statement pattern at all, so a goal
     whose remaining code was a sampling read as "no code";
  3. `[programs are in sync]` -- which means both sides hold IDENTICAL
     remaining code -- was read as an absent statement list, i.e. emptiness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integration.agent.prompt import (
    _last_statement_kind,
    _program_statement_block,
    _programs_in_sync,
    _statement_lines,
    format_active_goal_shape_hints,
)

FIXTURE = Path(__file__).parent / "fixtures" / "goals" / "elgamal_goals.json"

IN_SYNC_GOAL = """Current goal (remaining: 2)

&1 (left ) : {choice, guess : bool, q1, q2 : exp} [programs are in sync]
&2 (right) : {choice, guess : bool, q1, q2 : exp}

pre =
  q1{1} = q1{2} /\\
  q2{1} = q2{2}

post =
  true
"""

SAMPLING_GOAL = """Current goal

&1 (left ) : {q : exp}
&2 (right) : {q1 : exp}

pre =
  RO.mp{1} = empty /\\
  (glob Adv){2} = (glob Adv){m} /\\
  (glob Adv){1} = (glob Adv){m}

q <$ dexp                  (1)  q1 <$ dexp

post =
  true
"""


@pytest.fixture(scope="module")
def records():
    if not FIXTURE.is_file():
        pytest.skip("captured goal fixture not present")
    return json.loads(FIXTURE.read_text())


# --- the three parsing defects ------------------------------------------------


def test_multiline_precondition_does_not_leak_into_statements():
    block = _program_statement_block(SAMPLING_GOAL)
    assert "glob Adv" not in block, "precondition text leaked in as code"
    assert "empty" not in block
    assert "<$" in block, "the real statement was dropped"


def test_random_sampling_counts_as_code():
    """`rnd` was 31 of 134 failures; `<$` matched no pattern at all."""
    assert _statement_lines("q <$ dexp                  (1)  q1 <$ dexp")
    assert _statement_lines("  q1{1} = q1{2} /\\") == [], "formula is not code"


def test_programs_in_sync_is_detected():
    assert _programs_in_sync(IN_SYNC_GOAL)
    assert not _programs_in_sync(SAMPLING_GOAL)


# --- the advice ----------------------------------------------------------------


def test_in_sync_goal_is_never_told_to_skip():
    hints = format_active_goal_shape_hints(IN_SYNC_GOAL)
    assert "Apply `skip.`" not in hints
    assert "programs are in sync" in hints
    assert "do not apply `skip.`" in hints.lower()


def test_sampling_goal_is_pointed_at_rnd_not_skip():
    hints = format_active_goal_shape_hints(SAMPLING_GOAL)
    assert "Apply `skip.`" not in hints
    assert "rnd" in hints


def test_instruction_counts_are_reported_for_seq():
    """`seq N M` failed 13x with ``invalid `position' parameter``."""
    hints = format_active_goal_shape_hints(SAMPLING_GOAL)
    assert "Instruction counts" in hints
    assert "seq N M" in hints


def test_last_instruction_kind_drives_rnd_selection():
    assert "rnd" in (_last_statement_kind("q <$ dexp") or "")
    assert "wp" in (_last_statement_kind("x <- 1") or "")
    assert "call" in (_last_statement_kind("r <@ M.f()") or "")
    assert _last_statement_kind("") is None
    assert _last_statement_kind("q1{1} = q1{2}") is None


def test_a_genuinely_empty_program_still_gets_skip_advice():
    """The fix must not simply delete the advice; empty programs need it."""
    goal = """Current goal

&1 (left ) : {x : int}
&2 (right) : {x : int}

pre =
  x{1} = x{2}

post =
  true
"""
    hints = format_active_goal_shape_hints(goal)
    assert "Apply `skip.`" in hints
    # ...and it must warn that an unseen statement list is possible.
    assert "not shown" in hints


# --- regression over the real corpus -------------------------------------------


def test_no_captured_goal_is_told_to_skip_while_code_remains(records):
    """The headline regression: 121 bogus `skip.` recommendations -> 0."""
    bogus = [
        r for r in records
        if "Apply `skip.`" in format_active_goal_shape_hints(r["goal"])
        and _programs_in_sync(r["goal"])
    ]
    assert not bogus, f"{len(bogus)} in-sync goals still advised to skip"


def test_skip_advice_never_precedes_a_left_instruction_list_error(records):
    """EasyCrypt itself contradicted the advice on these goals."""
    contradicted = [
        r for r in records
        if "left instruction list is not empty" in (r.get("critical") or "")
        and "Apply `skip.`" in format_active_goal_shape_hints(r["goal"])
    ]
    assert not contradicted, (
        f"{len(contradicted)} goals advised `skip.` where EasyCrypt reported "
        "the instruction list was not empty"
    )


def test_hints_are_still_produced_for_most_goals(records):
    """A fix that silences the hints entirely would also pass the tests above."""
    distinct = {r["goal_id"]: r for r in records}.values()
    informative = sum(
        1 for r in distinct
        if "Instruction counts" in format_active_goal_shape_hints(r["goal"])
    )
    assert informative >= len(list(distinct)) // 2, (
        "the fix removed bad advice without adding the structured facts"
    )


def test_hint_generation_never_raises_on_real_goals(records):
    for r in records:
        format_active_goal_shape_hints(r["goal"])

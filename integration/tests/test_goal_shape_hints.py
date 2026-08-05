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


# --- multi-goal dumps --------------------------------------------------------
#
# EasyCrypt prints every open goal, active one first, the rest under `Goal #N`.
# A tactic applies to the active goal alone. Measured over one run: 90% of
# prompts carried more than one subgoal and 70% of the goal text was inactive.

MULTI_GOAL = """\
Current goal (remaining: 2)

Type variables: <none>

&m: {}
------------------------------------------------------------------------
forall (x : int), 0 <= x => x + 1 <> 0


  Goal #2
  ------------------------------------------------------------------------
  &1 (left ) : {a : int}
&2 (right) : {a : int}

pre = ={a}

    (1)  x <- a          (1)  x <- a
    (2)  y <$ dt         (2)  y <$ dt

post = ={x, y}
"""


def test_active_goal_is_extracted_without_the_inactive_ones():
    from integration.agent.prompt import active_goal_text

    active = active_goal_text(MULTI_GOAL)
    assert "forall (x : int)" in active
    assert "Goal #2" not in active
    assert "y <$ dt" not in active


def test_subgoal_count_comes_from_the_remaining_header():
    from integration.agent.prompt import count_subgoals

    assert count_subgoals(MULTI_GOAL) == 2
    assert count_subgoals("Current goal\n\nfoo") == 1
    assert count_subgoals("") == 0


def test_inactive_program_does_not_make_an_ambient_goal_look_program_logic():
    """The regression that mattered: goal #2 has a program, the active one does not."""
    hints = format_active_goal_shape_hints(MULTI_GOAL)
    assert "AMBIENT-LOGIC" in hints
    assert "PROGRAM-LOGIC" not in hints


def test_instruction_counts_are_never_taken_from_an_inactive_goal():
    """`seq N M` uses these numbers directly; fabricated ones are worse than none."""
    hints = format_active_goal_shape_hints(MULTI_GOAL)
    assert "Instruction counts" not in hints


def test_the_model_is_told_which_goal_is_active():
    hints = format_active_goal_shape_hints(MULTI_GOAL)
    assert "2 open goals" in hints
    assert "ACTIVE" in hints


def test_single_goal_dumps_are_unchanged_and_say_nothing_about_subgoals():
    single = """\
Current goal

&m: {}
------------------------------------------------------------------------
pre = ={a}

    (1)  x <- a          (1)  x <- a

post = ={x}
"""
    hints = format_active_goal_shape_hints(single)
    assert "PROGRAM-LOGIC" in hints
    assert "open goals" not in hints
    assert "Instruction counts" in hints


def test_goal_section_labels_and_separates_the_dumps():
    from integration.agent.prompt import _goal_section

    rendered = "\n".join(_goal_section(MULTI_GOAL))
    assert "ACTIVE (1 of 2 open)" in rendered
    assert "Other open goals (1)" in rendered
    # Context is kept, not discarded -- it just stops being mistaken for active.
    assert "y <$ dt" in rendered


def test_goal_section_leaves_a_single_goal_alone():
    from integration.agent.prompt import _goal_section

    rendered = _goal_section("Current goal\n\npre = ={a}\n\npost = ={x}")
    assert rendered[0] == "## Current goal"
    assert "ACTIVE" not in "\n".join(rendered)


# --- both ends of the program (2026-08-05 run) ------------------------------
# EasyCrypt's tactics divide on which end they consume: `rnd` and `wp` work
# backwards from the LAST instruction, `if` / `rcondt` / `rcondf` forwards from
# the FIRST. The block named only the last, so every `if` was guesswork --
# `invalid first instruction` was 13 of 78 failures, all of them `if`.

_ASYMMETRIC_EQUIV = """Current goal

&1 (left ) : {x : int}
&2 (right) : {x : int}

pre = ={x}

    if (x < 0) {                    (1)  x <- x + 1
      x <- 0;                       (2)  y <$ dbool
    }
    y <$ dbool

post = ={x}
"""


def test_the_first_instruction_is_named_for_each_side():
    hints = format_active_goal_shape_hints(_ASYMMETRIC_EQUIV)
    assert "First instruction on the left" in hints
    assert "First instruction on the right" in hints


def test_first_and_last_can_disagree_and_both_are_reported():
    """The measured failure: the model tried `if{2}` when the RIGHT side's
    first instruction was an assignment. Naming only the last instruction gave
    it nothing to rule that out with."""
    hints = format_active_goal_shape_hints(_ASYMMETRIC_EQUIV)
    first = [l for l in hints.splitlines() if "First instruction" in l]
    last = [l for l in hints.splitlines() if "Last instruction" in l]
    assert first and last
    assert first != last, "first and last must be derived separately"


def test_a_leading_conditional_points_at_the_if_family():
    hints = format_active_goal_shape_hints(_ASYMMETRIC_EQUIV)
    left_first = next(l for l in hints.splitlines()
                      if "First instruction on the left" in l)
    assert "`if`" in left_first or "rcond" in left_first


def test_wp_is_not_advised_at_the_head_of_a_program():
    """`wp` consumes from the end. Saying "assignment -> wp" about the FIRST
    instruction would send the model at the wrong end of the program."""
    hints = format_active_goal_shape_hints(_ASYMMETRIC_EQUIV)
    right_first = next(l for l in hints.splitlines()
                       if "First instruction on the right" in l)
    assert "works from the END" in right_first


def test_a_sync_goal_names_its_own_recovery():
    """Measured: of 101 `[programs are in sync]` goals, 89 accepted a
    program-logic tactic and 12 answered "expecting a goal of the form ...".
    No feature of the printed goal separates them, so the advice cannot be
    made conditional -- naming the recovery is what is left."""
    goal = """Current goal

&1 (left ) : {x : int} [programs are in sync]
&2 (right) : {x : int}

pre = ={x}

post = ={x}
"""
    hints = format_active_goal_shape_hints(goal)
    assert "programs are in sync" in hints
    assert "expecting a goal of the form" in hints
    assert "is NOT a program-logic goal" in hints
    # And it must point somewhere useful, not just say "you are stuck".
    assert "smt" in hints and "progress" in hints

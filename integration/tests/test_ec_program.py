"""Positions in EasyCrypt's two-column program dump.

Position errors -- right tactic, wrong target -- are ~45% of measured tactic
failures, and `seq N M` with an out-of-range index is the largest slice the
harness can address directly. The prompt used to derive N and M by counting
instruction-shaped LINES, which counts a statement's nested body as more
statements; on 503 of 654 real indexed goals that overstated the maximum, and
it overstated it every single time it differed.
"""

from __future__ import annotations

from pathlib import Path

from integration.agent.ec_program import (
    ASSIGN,
    CALL,
    IF,
    SAMPLE,
    WHILE,
    _callee,
    _classify,
    common_prefix_length,
    parse_program_block,
    seq_candidates,
)

# A real ElGamal goal, copied verbatim out of
# `run-20260803T175504Z/trials/trial_000` rather than retyped -- the column
# offsets ARE the data here, and a hand-transcribed copy silently came out
# 12/11 instead of 13/12.
#
# The left program has 13 top-level statements and the right has 12; the old
# line count called both 15. Positions 9 (right) and 10 (left) open blocks
# whose bodies print as `( N.k)` rows, and position 5 on the left is truncated
# by the column width -- all three are what made the count wrong.
REAL_BLOCK = (
    Path(__file__).resolve().parent / "fixtures" / "elgamal_equiv_block.txt"
).read_text(encoding="utf-8").rstrip("\n")

FLAT_BLOCK = """\
q1 <$ dexp                          ( 1)  q1 <$ dexp
q2 <$ dexp                          ( 2)  q2 <$ dexp
(x1, x2) <@ G2.A.choose(g ^ q1)     ( 3)  (x1, x2) <@ G3.A.choose(g ^ q1)
t <- if choice then x1 else x2      ( 4)  y <$ dtext"""


# --- counting ---------------------------------------------------------------


def test_nested_bodies_are_not_top_level_positions():
    """The bug this module exists for. `seq` addresses positions 1..13 and
    1..12 here; the line count said 15 and 15, above a sentence telling the
    model not to exceed them."""
    pair = parse_program_block(REAL_BLOCK)
    assert (len(pair.left), len(pair.right)) == (13, 12)
    assert [s.position for s in pair.left] == list(range(1, 14))
    assert [s.position for s in pair.right] == list(range(1, 13))


def test_the_flat_index_form_is_read_too():
    """EasyCrypt prints `( N)` when no statement has a body and `( N--)` when
    one does. Both open a top-level position."""
    pair = parse_program_block(FLAT_BLOCK)
    assert (len(pair.left), len(pair.right)) == (4, 4)


def test_a_single_row_block_still_has_a_position():
    """138 of 683 real blocks are one statement wide. A lone marker cannot be
    corroborated by a second row, so it is trusted only when there is no
    second row to disagree."""
    pair = parse_program_block("q <$ dexp                  (1)  q1 <$ dexp")
    assert (len(pair.left), len(pair.right)) == (1, 1)
    assert pair.left[0].kind == SAMPLE


def test_a_block_with_no_index_column_reports_no_positions():
    """A proc header before `proc.` opens the bodies. Guessing a count here is
    exactly the failure being fixed, so the parser declines instead."""
    pair = parse_program_block("RO.f ~ RO_track.f")
    assert pair.indexed is False
    assert pair.left == () and pair.right == ()


def test_parenthesised_digits_in_program_text_do_not_pose_as_indices():
    """The index column is found by agreement across rows, because program
    text carries its own parens -- `(pubk, privk)`, `(g ^ q, q)`."""
    pair = parse_program_block(REAL_BLOCK)
    assert pair.left[3].text.startswith("(pubk, privk) <-")
    assert pair.left[3].position == 4


def test_the_sides_are_not_transposed():
    """A `seq N M` with N and M swapped is precisely the position error this
    is meant to prevent, so which column is which is pinned."""
    pair = parse_program_block(REAL_BLOCK)
    assert "RO.mp" in pair.left[0].text and "RO_track" not in pair.left[0].text
    assert "RO_track.mp" in pair.right[0].text
    assert "INDCPA(HEG, Adv)" in pair.left[-1].text
    assert "G1.A.guess" in pair.right[-1].text


# --- classification ---------------------------------------------------------


def test_an_assignment_whose_value_contains_if_is_not_a_conditional():
    """`t <- if choice then x1 else x2` would otherwise send the model looking
    for an `rcondt` position that does not exist."""
    assert _classify("t <- if choice then x1 else x2") == ASSIGN


def test_the_statement_kinds():
    assert _classify("q <$ dexp") == SAMPLE
    assert _classify("(x1, x2) <@ G1.A.choose(g ^ q1)") == CALL
    assert _classify("x <- pubk0 ^ r") == ASSIGN
    assert _classify("if (x \\notin RO.mp) {") == IF
    assert _classify("while (i < n) {") == WHILE


def test_the_callee_is_the_base_name_past_any_functor_arguments():
    assert _callee("guess <@ INDCPA(HEG, Adv).A.guess(c)") == "guess"
    assert _callee("(x1, x2) <@ G1.A.choose(g ^ q1)") == "choose"


def test_a_truncated_call_yields_no_callee_rather_than_a_wrong_one():
    """EasyCrypt clips the left column to its width; `(x1, x2) <@` with
    nothing after it is common and is not a parse failure."""
    assert _callee("(x1, x2) <@") is None
    pair = parse_program_block(REAL_BLOCK)
    assert pair.left[4].kind == CALL
    assert pair.left[4].procedure is None


# --- cut points -------------------------------------------------------------


def test_a_shared_call_is_proposed_as_the_cut():
    """Ported from `_compute_seq_suggestions`. Matching the `guess` call at
    left 13 to the same call at right 12 is what the model cannot read off the
    dump, and it is where `call`/`auto` can discharge both prefixes."""
    candidates = seq_candidates(parse_program_block(REAL_BLOCK))
    assert [(c.left_pos, c.right_pos, c.procedure) for c in candidates] == [
        (13, 12, "guess")
    ]
    assert candidates[0].tactic == "seq 13 12 : (<invariant>)."


def test_a_truncated_left_call_cannot_be_matched():
    """Left position 5 is the `choose` call, but its callee was clipped, so it
    must not be paired with the right-hand `choose` on a guess."""
    candidates = seq_candidates(parse_program_block(REAL_BLOCK))
    assert all(c.procedure != "choose" for c in candidates)


def test_each_right_hand_call_is_consumed_once():
    block = """\
a <@ M.f()      ( 1--)  a <@ N.f()
b <@ M.f()      ( 2--)  b <@ N.g()"""
    candidates = seq_candidates(parse_program_block(block))
    assert [(c.left_pos, c.right_pos) for c in candidates] == [(1, 1)]


def test_the_common_prefix_ends_where_the_programs_diverge():
    pair = parse_program_block(REAL_BLOCK)
    # 1 ASSIGN, 2 SAMPLE, 3 SAMPLE, 4 ASSIGN agree; position 5 is a CALL on
    # the left and an ASSIGN on the right.
    assert common_prefix_length(pair) == 4


def test_identical_programs_have_a_full_common_prefix():
    block = """\
q1 <$ dexp      ( 1)  q1 <$ dexp
q2 <$ dexp      ( 2)  q2 <$ dexp"""
    pair = parse_program_block(block)
    assert common_prefix_length(pair) == 2


# --- what the model is told -------------------------------------------------


def test_the_prompt_states_the_exact_seq_bounds():
    from integration.agent.prompt import _seq_position_bullets

    text = " ".join(_seq_position_bullets(parse_program_block(REAL_BLOCK)))
    assert "left: 13, right: 12" in text
    assert "N must be 0..13 and M must be 0..12" in text
    assert "15" not in text


def test_the_prompt_offers_the_divergence_and_the_matched_call():
    from integration.agent.prompt import _seq_position_bullets

    text = " ".join(_seq_position_bullets(parse_program_block(REAL_BLOCK)))
    assert "agree for their first 4 instructions" in text
    assert "seq 4 4" in text
    assert "Both sides call `guess`" in text
    assert "seq 13 12 : (<invariant>)." in text


def test_no_count_is_claimed_when_no_index_column_was_printed():
    """Silence beats a number the model is told is a hard bound."""
    from integration.agent.prompt import format_active_goal_shape_hints

    goal = (
        "Current goal\n\npre = true\n\nRO.f ~ RO_track.f\n\npost = ={res}"
    )
    text = format_active_goal_shape_hints(goal)
    assert "Instruction counts" not in text


def test_the_first_row_keeps_its_column_alignment():
    """`_program_statement_block` used to `.strip()` the joined block, which
    deletes the leading spaces that ARE the empty left column on row one and
    shifts that row's index marker out of line with every other row. 25 of 683
    real blocks parsed as unindexed for that alone."""
    from integration.agent.prompt import _program_statement_block

    goal = (
        "pre = true\n\n"
        "                           ( 1--)  if (x = b) {\n"
        "                           ( 1.1)    y <- true\n"
        "z <- 1                     ( 2--)  w <- 2\n\n"
        "post = true"
    )
    block = _program_statement_block(goal)
    assert block.startswith(" "), "leading column must survive"
    pair = parse_program_block(block)
    assert pair.indexed is True
    assert len(pair.left) == 1 and len(pair.right) == 2


# --- the wrong-logic-class hedge (§4) ---------------------------------------


def test_a_discharged_judgment_hint_fires_on_the_measured_conjunction():
    """24% of failures are a program-logic tactic on a goal whose judgment is
    already discharged. Subgoal count alone does not find them -- 21.6%
    (accepted) vs 50.7% (wrong) at two open goals. With "no instruction index
    column" it reaches 58% precision at 45% recall over 568 labelled steps,
    against an 11.8% base rate.
    """
    from integration.agent.prompt import format_active_goal_shape_hints

    goal = (
        "Current goal (remaining: 2)\n\n"
        "pre = true\n\n"
        "&1 (left ) : {c : cipher} [programs are in sync]\n\n"
        "post = ={res}"
    )
    text = format_active_goal_shape_hints(goal)
    assert "no instruction indices" in text
    assert "about\nhalf the time" in text or "half the time" in text
    # It must stay a hedge: two in five of these really are program-logic.
    assert "expecting a goal of the form" in text


def test_the_hint_stays_silent_when_positions_were_printed():
    """The signal is the ABSENCE of an index column. A goal with one is a live
    program-logic goal 83% of the time and must not be second-guessed."""
    from integration.agent.prompt import format_active_goal_shape_hints

    goal = (
        "Current goal (remaining: 2)\n\n"
        "pre = true\n\n" + REAL_BLOCK + "\n\npost = ={res}"
    )
    assert "no instruction indices" not in format_active_goal_shape_hints(goal)


def test_the_hint_stays_silent_at_a_single_goal():
    from integration.agent.prompt import format_active_goal_shape_hints

    goal = (
        "Current goal\n\npre = true\n\n"
        "&1 (left ) : {c : cipher} [programs are in sync]\n\npost = ={res}"
    )
    assert "no instruction indices" not in format_active_goal_shape_hints(goal)

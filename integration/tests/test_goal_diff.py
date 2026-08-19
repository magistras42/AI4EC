"""Structural diff of a goal across one tactic.

Two jobs here. The first is the ordinary one: the metrics and the verdict do
what they say. The second is to pin *why* this module is not wired into
`confirm_noop`, because the handoff that commissioned it proposed exactly that
and the measurements refute it -- see `test_noop_tactics.py` for the fixture
counterexample and the docstring of `goal_diff` for the corpus numbers.
"""

from __future__ import annotations

from integration.agent import goal_diff
from integration.agent.goal_diff import (
    NEUTRAL_OR_NO_CHANGE,
    PROGRESS,
    PROGRESS_DECOMPOSITION,
    REGRESSION,
    UNCLASSIFIED,
    compute_state_diff,
    format_state_diff,
    metrics,
    subgoal_count,
)


def _goal(remaining: int | None, body: str = "pre = x = 1\n\npost = x = 2") -> str:
    header = (
        "Current goal" if remaining is None
        else f"Current goal (remaining: {remaining})"
    )
    return f"{header}\n\nType variables: <none>\n\n{'-' * 20}\n{body}"


# --- subgoal count ----------------------------------------------------------


def test_the_remaining_header_is_the_count():
    assert subgoal_count(_goal(3)) == 3


def test_a_bare_current_goal_is_one_goal():
    """16% of real goals print the bare form; reading it as "unknown" would
    blind the metric on a sixth of the corpus."""
    assert subgoal_count(_goal(None)) == 1


def test_no_more_goals_is_zero_not_one():
    """The one unambiguous PROGRESS signal. `prompt.count_subgoals` reports 1
    for any non-empty text, which is right for display and wrong here."""
    assert subgoal_count("No more goals") == 0


def test_empty_text_has_no_goals():
    assert subgoal_count("") == 0
    assert subgoal_count("   \n ") == 0


def test_the_last_marker_wins_over_replay_chatter():
    """A dump can carry earlier markers from the replayed prefix. The state is
    whatever the final one says."""
    text = "Current goal (remaining: 7)\n\nstuff\n\nCurrent goal (remaining: 2)\n\nmore"
    assert subgoal_count(text) == 2


# --- body metrics -----------------------------------------------------------


def test_metrics_are_scoped_to_the_active_subgoal():
    """EasyCrypt prints every open goal. On our runs 90% of prompts carry more
    than one and 70% of the text is inactive, so whole-dump metrics would move
    whenever ANY goal moved and blame the tactic for it."""
    active = _goal(2, "post = a = b")
    with_inactive = active + "\n\nGoal #2\n\npost = forall x, forall y, P x y"
    assert metrics(active).quantifiers_count == 0
    assert metrics(with_inactive).quantifiers_count == 0


def test_nested_pr_terms_count_once():
    body = "post = Pr[A.main() @ &m : Pr[B.main() @ &m : res] = 1%r] <= 1%r"
    assert metrics(_goal(1, body)).pr_terms_count == 1


def test_a_quantifier_inside_a_pr_event_is_not_top_level():
    """It is part of the probability expression, not structure `move=>` can
    attack."""
    assert metrics(_goal(1, "post = Pr[A.main() @ &m : forall x, P x] = 0%r")
                   ).quantifiers_count == 0
    assert metrics(_goal(1, "post = forall x, P x")).quantifiers_count == 1


def test_a_quantifier_inside_a_lambda_is_not_top_level():
    assert metrics(_goal(1, "post = f (fun y => forall x, P x)")
                   ).quantifiers_count == 0


def test_functor_chains_are_measured_by_leading_argument():
    # Depth counts the `Name(` openings on the leading edge, so the innermost
    # bare name does not add a level: `A(B(C(D)))` is 3, not 4.
    assert metrics(_goal(1, "post = A(B(C(D))) = x")).module_depth_max == 3
    assert metrics(_goal(1, "post = A(B) = x")).module_depth_max == 1
    # An ordinary application is not a chain.
    assert metrics(_goal(1, "post = f(x, y) = z")).module_depth_max == 1


def test_arrow_connectives_are_not_double_counted_as_equals():
    """`=>` and `<=` must be consumed whole or each also scores a bare `=`."""
    assert metrics(_goal(1, "post = a => b")).top_connectives_count == 2
    assert metrics(_goal(1, "post = a <= b")).top_connectives_count == 2


def test_connectives_inside_parens_do_not_count():
    assert metrics(_goal(1, "post = f (a /\\ b)")).top_connectives_count == 1


# --- verdicts ---------------------------------------------------------------


def test_closing_the_proof_is_progress():
    diff = compute_state_diff(_goal(2), "No more goals", "smt().")
    assert diff.verdict == PROGRESS


def test_a_byte_identical_goal_is_no_change():
    goal = _goal(2)
    assert compute_state_diff(goal, goal, "wp.").verdict == NEUTRAL_OR_NO_CHANGE


def test_whitespace_alone_is_not_a_change():
    diff = compute_state_diff(_goal(2), _goal(2) + "\n\n   \n", "wp.")
    assert diff.verdict == NEUTRAL_OR_NO_CHANGE


def test_a_split_by_a_decomposing_tactic_is_progress_not_regression():
    """The signal this module exists for. A `seq` that triples the subgoal
    count reads to a model exactly like something going wrong."""
    diff = compute_state_diff(
        _goal(1), _goal(3, "post = a = b"), "seq 2 2 : (={x})."
    )
    assert diff.verdict == PROGRESS_DECOMPOSITION


def test_the_same_split_by_a_non_decomposing_tactic_is_a_regression():
    """`apply` with loose unification spawns speculative obligations, and the
    only difference from a real decomposition is which tactic did it."""
    diff = compute_state_diff(_goal(1), _goal(3, "post = a = b"), "apply lem.")
    assert diff.verdict == REGRESSION


def test_the_decomposer_check_reads_the_head_of_a_compound():
    assert goal_diff._is_decomposer("if{2}.")
    assert goal_diff._is_decomposer("seq 2 2 : (={x}).")
    assert goal_diff._is_decomposer("call (_: Inv).")
    assert not goal_diff._is_decomposer("apply lem.")


def test_fewer_subgoals_is_progress():
    assert compute_state_diff(_goal(3), _goal(1), "smt().").verdict == PROGRESS


def test_a_moved_goal_with_flat_metrics_is_not_called_inert():
    """The measurement that decides this module cannot be an inertness oracle.

    113 of 525 real accepted transitions changed the goal text with every
    structural metric flat. Upstream returns NEUTRAL_OR_NO_CHANGE there, which
    is safe when the verdict only decides whether to print a hint and unsafe
    when it decides whether to DELETE a line. We say UNCLASSIFIED and print
    nothing.
    """
    pre = _goal(1, "post = x = 1")
    post = _goal(1, "post = y = 2")
    diff = compute_state_diff(pre, post, "rewrite foo.")
    assert diff.deltas == {k: 0 for k in diff.deltas}, "metrics must be flat"
    assert diff.text_unchanged is False
    assert diff.verdict == UNCLASSIFIED
    assert diff.verdict != NEUTRAL_OR_NO_CHANGE


def test_subgoal_count_never_moves_when_the_text_is_identical():
    """The other half of the same argument: on 525 real transitions the count
    that byte-identical text hides is 0. Subgoal count is strictly implied by
    text equality, so it can never rescue a tactic text comparison condemns --
    which is why it is not worth adding as a veto in `confirm_noop` either."""
    goal = _goal(4, "post = a = b")
    assert subgoal_count(goal) == subgoal_count(goal)
    diff = compute_state_diff(goal, goal, "wp.")
    assert diff.deltas["subgoals_count"] == 0


# --- cosmetic noise ---------------------------------------------------------
# Ported because the handoff asked for it. Recorded here because it fired on 0
# of 987 real goals in the current (ElGamal-only) corpus: these tests are
# synthetic, and the feature is unvalidated against our own runs.


def test_a_newly_applied_lambda_is_reported_as_cosmetic():
    pre = _goal(1, "post = g x = 1")
    post = _goal(1, "post = (fun y => y + 1) x = 1")
    assert "beta_redex" in compute_state_diff(pre, post, "rewrite f.").cosmetic_noise


def test_a_lambda_with_parens_in_its_body_is_still_seen():
    post = _goal(1, "post = (fun y => f (y + 1)) x = 1")
    diff = compute_state_diff(_goal(1, "post = g x = 1"), post, "rewrite f.")
    assert "beta_redex" in diff.cosmetic_noise


def test_pre_existing_noise_is_not_blamed_on_this_tactic():
    body = "post = (fun y => y + 1) x = {}"
    pre = _goal(2, body.format(1))
    post = _goal(1, body.format(2))
    assert compute_state_diff(pre, post, "smt().").cosmetic_noise == ()


def test_an_eta_wrapper_is_noise_but_self_application_is_not():
    assert goal_diff._detect_eta_expansion("fun x => f x")
    assert not goal_diff._detect_eta_expansion("fun x => x x")


def test_a_deep_glob_chain_is_noise_and_a_shallow_one_is_not():
    assert goal_diff._detect_unreduced_glob("(glob A(B(C(D))))")
    assert not goal_diff._detect_unreduced_glob("(glob A)")


# --- what reaches the prompt ------------------------------------------------


def test_nothing_is_said_when_no_metric_can_name_the_move():
    """Silence is the default. A block on every step would be noise on the
    majority of them -- it renders on 32% of real accepted steps."""
    assert format_state_diff(
        _goal(1, "post = x = 1"), _goal(1, "post = y = 2"), "rewrite foo."
    ) == ""


def test_nothing_is_said_about_a_no_op_because_the_harness_already_does():
    goal = _goal(2)
    assert format_state_diff(goal, goal, "wp.") == ""


def test_a_split_is_explained_as_the_tactic_working():
    text = format_state_diff(_goal(1), _goal(3, "post = a = b"), "seq 2 2 : (={x}).")
    assert PROGRESS_DECOMPOSITION in text
    assert "subgoals 1->3" in text
    assert "not a regression" in text
    # It must say which subgoal to work, or the split is not actionable.
    assert "active (first) subgoal" in text


def test_a_regression_says_to_check_the_new_subgoals():
    text = format_state_diff(_goal(1), _goal(3, "post = a = b"), "apply lem.")
    assert REGRESSION in text
    assert "speculative" in text
    assert "undo" in text


def test_progress_names_the_metric_that_moved():
    text = format_state_diff(_goal(3), _goal(1), "smt().")
    assert PROGRESS in text
    assert "subgoals 3->1" in text


def test_a_missing_side_or_tactic_says_nothing():
    assert format_state_diff("", _goal(1), "wp.") == ""
    assert format_state_diff(_goal(1), "", "wp.") == ""
    assert format_state_diff(_goal(1), _goal(3), "  ") == ""


def test_the_block_reaches_the_prompt_under_its_own_heading():
    from integration.agent.prompt import build_prompt

    diff = format_state_diff(_goal(1), _goal(3, "post = a = b"), "seq 2 2 : (={x}).")
    text = build_prompt(
        goal=_goal(3, "post = a = b"), top_premises={}, failed_tactics=[],
        proof_tail="", state_diff=diff,
    )
    assert "## What your last tactic did to the goal" in text
    assert PROGRESS_DECOMPOSITION in text


def test_no_heading_when_there_is_nothing_to_report():
    from integration.agent.prompt import build_prompt

    text = build_prompt(
        goal=_goal(1), top_premises={}, failed_tactics=[], proof_tail="",
        state_diff="",
    )
    assert "What your last tactic did" not in text

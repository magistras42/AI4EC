"""EasyCrypt error classification (roadmap W4 step 1).

Message strings below are the shapes EasyCrypt actually emits, including the
two different location formats that caused "the worst bug of the sprint"
(W4 step 4): a classifier that understands only one of them silently reports
no line at all.
"""

from __future__ import annotations

import pytest

from integration.agent.ec_errors import (
    KIND_PARSE_ERROR,
    KIND_PROOF_INCOMPLETE,
    KIND_TACTIC_ERROR,
    KIND_TYPE_ERROR,
    KIND_UNKNOWN,
    KIND_UNKNOWN_SYMBOL,
    KIND_UNKNOWN_THEORY,
    classify_error,
    extract_identifiers,
    first_error_line,
    is_load_failure,
    strip_warning_lines,
)

BRACKET_FORM = "[critical] [/x/hashedelgamal.ec: line 108 (8)] cannot find theory: `SmtMap'"
COLON_FORM = "/x/hashedelgamal.ec:108: cannot find theory: `SmtMap'"


@pytest.mark.parametrize("text", [BRACKET_FORM, COLON_FORM])
def test_both_easycrypt_location_formats_yield_the_line(text):
    assert first_error_line(text) == 108


def test_missing_location_reports_minus_one_not_zero():
    assert first_error_line("something went wrong") == -1
    assert first_error_line("") == -1


@pytest.mark.parametrize(
    "text,expected",
    [
        (BRACKET_FORM, KIND_UNKNOWN_THEORY),
        ("[critical] [/x/f.ec: line 5 (1)] unknown operator `dmap'", KIND_UNKNOWN_SYMBOL),
        ("/x/f.ec:12: parse error", KIND_PARSE_ERROR),
        ("[critical] [/x/f.ec: line 9 (2)] type error: cannot unify", KIND_TYPE_ERROR),
        (
            "[critical] [/x/f.ec: line 453 (2)] invalid 'position' parameter",
            KIND_TACTIC_ERROR,
        ),
        (
            "[critical] [/x/f.ec: line 77 (4)] cannot prove goal (strict)",
            KIND_PROOF_INCOMPLETE,
        ),
        ("mysterious internal failure", KIND_UNKNOWN),
    ],
)
def test_classification_kinds(text, expected):
    assert classify_error(text).kind == expected


def test_pre_proof_and_in_proof_are_disjoint_and_meaningful():
    """The boundary this whole subsystem is organized around."""
    load_failure = classify_error(BRACKET_FORM)
    assert load_failure.is_pre_proof and not load_failure.is_in_proof

    tactic_failure = classify_error(
        "[critical] [/x/f.ec: line 453 (2)] invalid 'position' parameter"
    )
    assert tactic_failure.is_in_proof and not tactic_failure.is_pre_proof


def test_the_elgamal_boundary_is_classified_correctly():
    """108 is import repair's job; 453 is the solver's. See handoff 4.4."""
    before = classify_error(BRACKET_FORM)
    after = classify_error(
        "[critical] [/x/hashedelgamal.ec: line 453 (2)] invalid 'position' parameter"
    )
    assert before.is_pre_proof and before.line == 108
    assert after.is_in_proof and after.line == 453


def test_identifiers_are_extracted_from_quoted_names():
    assert extract_identifiers(BRACKET_FORM) == ("SmtMap",)
    assert extract_identifiers('unknown operator `dmap\' and "FMap"') == ("dmap", "FMap")
    assert extract_identifiers("no names here") == ()


def test_identifiers_are_deduplicated_preserving_order():
    text = "cannot find `A' ... also `B' ... again `A'"
    assert extract_identifiers(text) == ("A", "B")


def test_unknown_output_counts_as_a_load_failure():
    """Fail open: an unrecognized error must not skip a repair attempt."""
    assert is_load_failure("mysterious internal failure")
    assert is_load_failure(BRACKET_FORM)
    assert not is_load_failure(
        "[critical] [/x/f.ec: line 453 (2)] invalid 'position' parameter"
    )


def test_as_dict_is_json_ready_and_truncates_long_messages():
    payload = classify_error(BRACKET_FORM + "\n" + "x" * 5000).as_dict()
    assert payload["kind"] == KIND_UNKNOWN_THEORY
    assert payload["line"] == 108
    assert payload["is_pre_proof"] is True
    assert payload["identifiers"] == ["SmtMap"]
    assert len(payload["message"]) <= 500


def test_classifier_never_raises_on_empty_or_none():
    assert classify_error("").kind == KIND_UNKNOWN
    assert classify_error(None).kind == KIND_UNKNOWN


# --- warning noise -----------------------------------------------------------
#
# EasyCrypt reprints every file-level warning on every invocation, so the same
# notices ride along with each tactic failure. In the captured runs these were
# 106 of 193 error lines, and always the same two.

REAL_RUN_ERROR = """\
[warning] [/tmp/agent_work.agent.ec:362] global axiom Adv_choose_ll in section
[warning] [/tmp/agent_work.agent.ec:363] global axiom Adv_guess_ll in section
[critical] [/tmp/agent_work.agent.ec: line 453 (2)] invalid last instruction\
"""


def test_warnings_are_stripped_leaving_the_real_failure():
    cleaned = strip_warning_lines(REAL_RUN_ERROR)
    assert "warning" not in cleaned
    assert "invalid last instruction" in cleaned
    assert len(cleaned.splitlines()) == 1


def test_first_line_becomes_the_critical_line():
    # loop.py takes line 0 as "the reason" for compound tactics; before the
    # filter that picked a warning.
    assert strip_warning_lines(REAL_RUN_ERROR).splitlines()[0].startswith("[critical]")


def test_warning_only_output_is_returned_unchanged():
    """Something beats nothing -- never hand back an empty error."""
    warnings_only = "\n".join(REAL_RUN_ERROR.splitlines()[:2])
    assert strip_warning_lines(warnings_only) == warnings_only


def test_output_without_warnings_is_untouched():
    text = "[critical] [/x/f.ec: line 12 (2)] invalid arguments"
    assert strip_warning_lines(text) == text


def test_the_word_warning_inside_a_message_is_not_a_warning_line():
    text = "[critical] [/x/f.ec: line 12 (2)] warning: unused hypothesis"
    assert strip_warning_lines(text) == text


def test_indented_and_uppercase_warnings_are_recognised():
    text = "  [Warning] noise\n[critical] [/x/f.ec: line 1 (2)] parse error"
    assert strip_warning_lines(text) == "[critical] [/x/f.ec: line 1 (2)] parse error"


def test_empty_and_none_are_safe():
    assert strip_warning_lines("") == ""
    assert strip_warning_lines(None) == ""


def test_classification_is_unaffected_by_stripping():
    """The filter must not change what the error *is*."""
    assert classify_error(strip_warning_lines(REAL_RUN_ERROR)).line == 453

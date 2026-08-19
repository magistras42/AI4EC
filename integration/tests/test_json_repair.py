"""Parsing model replies that carry EasyCrypt syntax.

EasyCrypt tactics are unusually hostile to JSON: ``/\\`` and ``\\/`` are
everyday operators, and multi-line tactics are natural to write. Each of the
cases below was reachable with the previous parser.
"""

from __future__ import annotations

import json

import pytest

from integration.agent.llm import (
    _find_json_object,
    _loads_json_object,
    _repair_invalid_json_escapes,
)

BACKSLASH = "\\"


def _parse(raw: str) -> dict:
    candidate = _find_json_object(raw)
    assert candidate is not None, f"reply was rejected outright: {raw!r}"
    return _loads_json_object(candidate, original=raw)


class TestSilentCorruption:
    """``\\/`` is *legal* JSON, so these never raised -- they returned garbage."""

    def test_disjunction_survives_instead_of_becoming_a_slash(self):
        # The model writes EasyCrypt's \/ . json.loads happily decodes \/ to /,
        # yielding smt(a / b). -- a different tactic, with nothing raised.
        raw = '{"action":"tactic","tactic":"smt(a ' + BACKSLASH + '/ b)."}'
        assert _parse(raw)["tactic"] == "smt(a " + BACKSLASH + "/ b)."

    def test_mixed_conjunction_and_disjunction(self):
        raw = (
            '{"action":"tactic","tactic":"case (a '
            + BACKSLASH
            + "/ b) => //= /"
            + BACKSLASH
            + ' c."}'
        )
        assert _parse(raw)["tactic"] == (
            "case (a " + BACKSLASH + "/ b) => //= /" + BACKSLASH + " c."
        )

    def test_already_correctly_escaped_disjunction_is_unchanged(self):
        # A model that escapes properly must not be punished for it.
        raw = '{"action":"tactic","tactic":"smt(a ' + BACKSLASH * 2 + '/ b)."}'
        assert _parse(raw)["tactic"] == "smt(a " + BACKSLASH + "/ b)."


class TestRejectedRepliesThatShouldParse:
    """Raw control characters inside a JSON string are illegal but natural."""

    def test_multi_line_tactic(self):
        raw = '{"action":"tactic","tactic":"proc.\nwp.\nskip."}'
        assert _parse(raw)["tactic"] == "proc.\nwp.\nskip."

    def test_tab_inside_tactic(self):
        raw = '{"action":"tactic","tactic":"wp;\tskip."}'
        assert _parse(raw)["tactic"] == "wp;\tskip."

    def test_carriage_return(self):
        raw = '{"action":"tactic","tactic":"a.\r\nb."}'
        assert _parse(raw)["tactic"] == "a.\r\nb."


class TestStillWorks:
    """Regression guard: the ordinary paths must be untouched by the repair."""

    def test_bare_conjunction_backslash(self):
        raw = '{"action":"tactic","tactic":"seq 1 1 : (={x} /' + BACKSLASH + ' y)."}'
        assert _parse(raw)["tactic"] == "seq 1 1 : (={x} /" + BACKSLASH + " y)."

    def test_properly_escaped_conjunction(self):
        raw = '{"action":"tactic","tactic":"seq 1 1 : (={x} /' + BACKSLASH * 2 + ' y)."}'
        assert _parse(raw)["tactic"] == "seq 1 1 : (={x} /" + BACKSLASH + " y)."

    def test_prose_and_fenced_block(self):
        raw = 'Sure:\n```json\n{"action":"tactic","tactic":"rnd."}\n```\nHope that helps.'
        assert _parse(raw)["tactic"] == "rnd."

    def test_unicode_escapes_are_preserved(self):
        raw = '{"action":"tactic","tactic":"a ' + BACKSLASH + 'u0041 b"}'
        assert _parse(raw)["tactic"] == "a A b"

    def test_quotes_inside_a_tactic(self):
        raw = (
            '{"action":"tactic","tactic":"smt('
            + BACKSLASH
            + '"x'
            + BACKSLASH
            + '")."}'
        )
        assert _parse(raw)["tactic"] == 'smt("x").'


class TestRepairProperties:
    @pytest.mark.parametrize(
        "text",
        [
            '{"a":"b"}',
            '{"a":"/' + BACKSLASH + ' c"}',
            '{"a":"x' + BACKSLASH + '/ y"}',
            '{"a":"line\nbreak"}',
            '{"a":"' + BACKSLASH + 'u0041"}',
            "not json at all",
            "",
        ],
    )
    def test_idempotent(self, text):
        once = _repair_invalid_json_escapes(text)
        assert _repair_invalid_json_escapes(once) == once

    def test_valid_json_round_trips_unchanged(self):
        payload = {"action": "tactic", "tactic": "seq 1 1 : (={x} /" + BACKSLASH + " y)."}
        encoded = json.dumps(payload)
        assert json.loads(_repair_invalid_json_escapes(encoded)) == payload

    def test_text_outside_strings_is_untouched(self):
        # Backslashes outside string literals are not ours to rewrite.
        text = '{"a": 1} trailing ' + BACKSLASH + " text"
        assert _repair_invalid_json_escapes(text) == text

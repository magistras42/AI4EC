"""Tests for tactic mutation operators."""

from __future__ import annotations

import random

from integration.experiment.mutations.tactics import (
    TacticMutationSet,
    remove_middle,
    remove_suffix,
    shuffle_window,
)


def _sample_lines_and_tactics():
    lines = [
        "lemma foo: true.",
        "proof.",
        "  tactic_a.",
        "  tactic_b.",
        "  tactic_c.",
        "qed.",
    ]
    tactic_lines = [3, 4, 5]
    return lines, tactic_lines


def test_remove_suffix_drops_tail():
    lines, tactic_lines = _sample_lines_and_tactics()
    rng = random.Random(0)
    new_lines, new_tactics = remove_suffix(lines, tactic_lines, rng)
    assert len(new_tactics) < len(tactic_lines)
    assert new_tactics == tactic_lines[: len(new_tactics)]
    assert "tactic_c." not in "\n".join(new_lines)


def test_remove_middle_drops_interior():
    lines, tactic_lines = _sample_lines_and_tactics()
    rng = random.Random(1)
    new_lines, new_tactics = remove_middle(lines, tactic_lines, rng)
    assert len(new_tactics) < len(tactic_lines)


def test_shuffle_window_changes_order():
    lines, tactic_lines = _sample_lines_and_tactics()
    rng = random.Random(2)
    new_lines, new_tactics = shuffle_window(lines, tactic_lines, rng)
    assert new_tactics == tactic_lines
    block = [new_lines[2], new_lines[3], new_lines[4]]
    assert block != ["  tactic_a.", "  tactic_b.", "  tactic_c."]
    assert sorted(block) == sorted(["  tactic_a.", "  tactic_b.", "  tactic_c."])


def test_mutation_set_applies_at_least_one_operator():
    lines, tactic_lines = _sample_lines_and_tactics()
    rng = random.Random(3)
    result = TacticMutationSet().apply(lines, tactic_lines, rng)
    assert result.operators_applied
    assert result.lines != lines or result.tactic_lines != tactic_lines

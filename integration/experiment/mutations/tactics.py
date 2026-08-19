"""Tactic-line mutation operators."""

from __future__ import annotations

import random
from typing import Callable

from integration.experiment.protocols import MutationResult

Operator = Callable[[list[str], list[int], random.Random], tuple[list[str], list[int]]]

OPERATOR_NAMES = ("remove_suffix", "remove_middle", "shuffle_window")


def _remove_lines(
    lines: list[str], tactic_lines: list[int], to_remove: list[int]
) -> tuple[list[str], list[int]]:
    drop = set(to_remove)
    new_lines = [line for i, line in enumerate(lines, start=1) if i not in drop]
    offset = 0
    remapped: list[int] = []
    for i, line in enumerate(lines, start=1):
        if i in drop:
            offset += 1
            continue
        if i in tactic_lines:
            remapped.append(i - offset)
    return new_lines, remapped


def remove_suffix(
    lines: list[str], tactic_lines: list[int], rng: random.Random
) -> tuple[list[str], list[int]]:
    if not tactic_lines:
        return lines, tactic_lines
    count = rng.randint(1, min(3, len(tactic_lines)))
    to_remove = tactic_lines[-count:]
    return _remove_lines(lines, tactic_lines, to_remove)


def remove_middle(
    lines: list[str], tactic_lines: list[int], rng: random.Random
) -> tuple[list[str], list[int]]:
    if len(tactic_lines) < 2:
        if tactic_lines:
            return _remove_lines(lines, tactic_lines, [tactic_lines[0]])
        return lines, tactic_lines
    max_count = min(2, len(tactic_lines) - 1)
    count = rng.randint(1, max_count)
    max_start = len(tactic_lines) - count - 1
    if max_start < 0:
        max_start = 0
    start_idx = rng.randint(0, max_start)
    to_remove = tactic_lines[start_idx : start_idx + count]
    return _remove_lines(lines, tactic_lines, to_remove)


def shuffle_window(
    lines: list[str], tactic_lines: list[int], rng: random.Random
) -> tuple[list[str], list[int]]:
    if len(tactic_lines) < 2:
        return lines, tactic_lines
    window = rng.randint(2, len(tactic_lines))
    max_start = len(tactic_lines) - window
    start_idx = rng.randint(0, max_start)
    window_lines = tactic_lines[start_idx : start_idx + window]

    contents = [lines[t - 1] for t in window_lines]
    shuffled = contents[:]
    for _ in range(10):
        rng.shuffle(shuffled)
        if shuffled != contents:
            break
    if shuffled == contents:
        shuffled = contents[1:] + contents[:1]

    new_lines = lines[:]
    for line_no, content in zip(window_lines, shuffled):
        new_lines[line_no - 1] = content
    return new_lines, tactic_lines


OPERATORS: dict[str, Operator] = {
    "remove_suffix": remove_suffix,
    "remove_middle": remove_middle,
    "shuffle_window": shuffle_window,
}


class TacticMutationSet:
    """Apply one or more randomly chosen tactic mutations per trial."""

    def apply(
        self,
        lines: list[str],
        tactic_lines: list[int],
        rng: random.Random,
    ) -> MutationResult:
        if not tactic_lines:
            return MutationResult(lines=lines, tactic_lines=tactic_lines)

        k = rng.randint(1, len(OPERATOR_NAMES))
        chosen = rng.sample(list(OPERATOR_NAMES), k)

        current_lines = list(lines)
        current_tactics = list(tactic_lines)
        for name in chosen:
            op = OPERATORS[name]
            current_lines, current_tactics = op(current_lines, current_tactics, rng)

        return MutationResult(
            lines=current_lines,
            tactic_lines=current_tactics,
            operators_applied=chosen,
        )

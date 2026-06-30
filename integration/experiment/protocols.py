"""Protocols and shared datatypes for mutation repair experiments."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol


@dataclass(frozen=True)
class IndexEntry:
    repo_slug: str
    file: str
    line: int
    kind: str
    name: str
    signature: str


@dataclass
class ProofCase:
    """A single provable lemma prepared for mutation/repair."""

    name: str
    file: Path
    lemma_line: int
    proof_start_line: int
    qed_line: int
    tactic_lines: list[int]
    index_entry: IndexEntry


@dataclass
class MutationResult:
    lines: list[str]
    tactic_lines: list[int]
    operators_applied: list[str] = field(default_factory=list)


class CorpusProvider(Protocol):
    def load_cases(self) -> list[ProofCase]:
        """Return all eligible proof cases for this corpus."""

    def lemma_lookup_index(self) -> dict[str, str]:
        """Map lemma name to signature for the lookup tool."""

    def sample_cases(self, count: int, rng: random.Random) -> list[ProofCase]:
        """Sample up to `count` cases (with replacement if pool is smaller)."""


class MutationStrategy(Protocol):
    def apply(
        self,
        lines: list[str],
        tactic_lines: list[int],
        rng: random.Random,
    ) -> MutationResult:
        """Return mutated file lines and updated tactic line numbers."""


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    corpus: CorpusProvider
    mutations: MutationStrategy


class ExperimentSpecRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ExperimentSpec] = {}

    def register(self, spec: ExperimentSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ExperimentSpec:
        if name not in self._specs:
            known = ", ".join(sorted(self._specs)) or "(none)"
            raise KeyError(f"Unknown experiment spec {name!r}. Known: {known}")
        return self._specs[name]

    def names(self) -> Iterator[str]:
        return iter(sorted(self._specs))

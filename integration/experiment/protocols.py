"""Protocols and shared datatypes for mutation repair experiments."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Protocol

if TYPE_CHECKING:
    from integration.experiment.informal import InformalConfig


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
class BrokenFormalConfig:
    """Marker config for specs where the "informal proof" sketch shown to
    the solver is the corpus's own genuinely broken formal tactic script,
    rather than a writer-LLM natural-language paraphrase (see
    :mod:`integration.experiment.corpora.elgamal`). Deliberately has no
    red-herring/writer knobs: this mode never curates a lemma manifest, so
    the solver ranks premises against the full ambient catalog, same as
    `joy-tactic-repair`.

    When ``source_version`` is set, the runner activates **compat migration
    mode**: it filters the structured EasyCrypt changelog for breaking
    changes relevant to the proof and injects them into the solver prompt as
    additional guidance.
    """

    # The EasyCrypt release the proof was last known to compile against.
    # Use "pre-r2022.04" for repos that predate the first formal release.
    # When None, compat migration hints are not injected.
    source_version: str | None = None

    # Target EasyCrypt version (default: latest known in changelog).
    target_version: str = "r2026.07"

    # Max changelog entries to inject into the prompt.
    migration_max_entries: int = 30


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    corpus: CorpusProvider
    mutations: MutationStrategy | None = None
    informal: InformalConfig | None = None
    broken_formal: BrokenFormalConfig | None = None


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

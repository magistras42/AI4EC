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
    `joy-tactic-repair`."""


@dataclass(frozen=True)
class ReplayBootstrapConfig:
    """Marker config for specs that replay the corpus's own ORIGINAL tactic
    script one tactic at a time (via ProofFile.append_tactic + validate_file,
    see integration.experiment.repair_bootstrap) instead of admitting
    everything and asking the solver to reconstruct the proof from scratch
    (contrast with BrokenFormalConfig above). Preserves whatever prefix of
    the original script still applies against the current EasyCrypt build;
    the solver only picks up at the first tactic that no longer does.

    source_ec_version/target_ec_version are caller-supplied (no
    auto-detection, matching the shannon-prover repair-hints design this
    mode ports from) -- used to scope proof_corpus/output/changelog.yaml
    matching once a failure is hit.
    """

    source_ec_version: str
    target_ec_version: str


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    corpus: CorpusProvider
    mutations: MutationStrategy | None = None
    informal: InformalConfig | None = None
    broken_formal: BrokenFormalConfig | None = None
    replay_bootstrap: ReplayBootstrapConfig | None = None


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

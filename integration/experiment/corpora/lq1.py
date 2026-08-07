"""LQ-1 controlled synthetic proof-repair corpus."""

from __future__ import annotations

import random
from pathlib import Path

from integration.experiment.proof_extract import build_sandbox
from integration.experiment.protocols import CorpusProvider, IndexEntry, ProofCase


LQ1_SLUG = "LQ-1"

TARGET_FILE = (
    "proof_corpus/benchmark_candidates/lq1_easy/"
    "sampling_bound_broken.ec"
)

LEMMA_LINE = 8
LEMMA_NAME = "sampling_bound"


class LQ1Corpus(CorpusProvider):
    """Corpus containing the controlled LQ-1 incomplete-proof candidate."""

    def __init__(
        self,
        data_dir: Path,
        sandbox_dir: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.sandbox_dir = sandbox_dir
        self._cached_cases: list[ProofCase] | None = None

    def load_cases(self) -> list[ProofCase]:
        if self._cached_cases is not None:
            return self._cached_cases

        project_root = Path(__file__).resolve().parents[3]

        entry = IndexEntry(
            repo_slug=LQ1_SLUG,
            file=TARGET_FILE,
            line=LEMMA_LINE,
            kind="lemma",
            name=LEMMA_NAME,
            signature=(
                "lemma sampling_bound "
                "(k : int) (forged_frac : real)"
            ),
        )

        base = self.sandbox_dir or (
            project_root
            / "data"
            / ".experiment-sandboxes"
            / LQ1_SLUG
        )
        base.mkdir(parents=True, exist_ok=True)

        destination = base / "sampling_bound.ec"
        case = build_sandbox(entry, project_root, destination)

        self._cached_cases = [case]
        return self._cached_cases

    def sample_cases(
        self,
        count: int,
        rng: random.Random,
    ) -> list[ProofCase]:
        pool = self.load_cases()

        if not pool:
            return []

        if len(pool) >= count:
            return rng.sample(pool, count)

        return [rng.choice(pool) for _ in range(count)]
"""The Joy of EasyCrypt evaluation corpus."""

from __future__ import annotations

import json
import random
from pathlib import Path

from integration.experiment.proof_extract import build_sandbox
from integration.experiment.protocols import CorpusProvider, IndexEntry, ProofCase

JOY_SLUG = "tejasanilshah-the-joy-of-easycrypt"
MIN_TACTICS = 2


def load_index_entries(
    proofs_index: Path, repo_slug: str = JOY_SLUG
) -> list[IndexEntry]:
    payload = json.loads(proofs_index.read_text(encoding="utf-8"))
    entries: list[IndexEntry] = []
    for row in payload.get("proofs", []):
        if row.get("repo_slug") != repo_slug:
            continue
        if row.get("kind") != "lemma":
            continue
        entries.append(
            IndexEntry(
                repo_slug=row["repo_slug"],
                file=row["file"],
                line=row["line"],
                kind=row["kind"],
                name=row["name"],
                signature=row["signature"],
            )
        )
    return entries


class JoyCorpus(CorpusProvider):
    def __init__(
        self,
        data_dir: Path,
        proofs_index: Path | None = None,
        min_tactics: int = MIN_TACTICS,
        sandbox_dir: Path | None = None,
    ) -> None:
        self.data_dir = data_dir
        if proofs_index is None:
            proofs_index = data_dir / "proofs_index.json"
        self.proofs_index = proofs_index
        self.min_tactics = min_tactics
        self.sandbox_dir = sandbox_dir
        self._cached_cases: list[ProofCase] | None = None

    def lemma_lookup_index(self) -> dict[str, str]:
        entries = load_index_entries(self.proofs_index)
        return {e.name: e.signature for e in entries}

    def load_cases(self) -> list[ProofCase]:
        if self._cached_cases is not None:
            return self._cached_cases

        entries = load_index_entries(self.proofs_index)
        cases: list[ProofCase] = []
        base = self.sandbox_dir or (self.data_dir / ".experiment-sandboxes" / JOY_SLUG)
        base.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            # Destination must be unique per lemma, not just per source file:
            # multiple lemmas commonly share one chapter file, and each one's
            # sandbox is truncated differently (right after its own `qed.`).
            # Keying only on the source file would let later entries silently
            # overwrite earlier ones' sandbox content on disk while earlier
            # `ProofCase`s still believe their `file` points at their own
            # (now-corrupted) truncated proof.
            safe_file = entry.file.replace("/", "__")
            dest = base / f"{safe_file}__L{entry.line}_{entry.name}.ec"
            try:
                case = build_sandbox(entry, self.data_dir, dest)
            except (FileNotFoundError, ValueError):
                continue
            if len(case.tactic_lines) < self.min_tactics:
                continue
            cases.append(case)
        self._cached_cases = cases
        return cases

    def sample_cases(self, count: int, rng: random.Random) -> list[ProofCase]:
        pool = self.load_cases()
        if not pool:
            return []
        if len(pool) >= count:
            return rng.sample(pool, count)
        return [rng.choice(pool) for _ in range(count)]

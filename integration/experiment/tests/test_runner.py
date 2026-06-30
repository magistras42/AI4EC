"""Tests for experiment runner orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from integration.agent.config import AgentConfig
from integration.agent.loop import AgentResult, ExitReason
from integration.experiment.config import ExperimentConfig
from integration.experiment.corpora.joy import JoyCorpus, load_index_entries
from integration.experiment.mutations.tactics import TacticMutationSet
from integration.experiment.protocols import ExperimentSpec, IndexEntry, ProofCase
from integration.experiment.runner import run_experiment

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class StaticCorpus:
    def __init__(self, cases: list[ProofCase], lookup: dict[str, str]) -> None:
        self._cases = cases
        self._lookup = lookup

    def load_cases(self) -> list[ProofCase]:
        return self._cases

    def lemma_lookup_index(self) -> dict[str, str]:
        return self._lookup

    def sample_cases(self, count: int, rng) -> list[ProofCase]:
        return self._cases[:count]


def _make_case(tmp_path: Path) -> ProofCase:
    source_lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()[:12]
    sandbox = tmp_path / "sandbox.ec"
    sandbox.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    entry = IndexEntry(
        repo_slug="tejasanilshah-the-joy-of-easycrypt",
        file="chapter/multi_lemma.ec",
        line=8,
        kind="lemma",
        name="target",
        signature="lemma target (n : int) : 0 + n = n.",
    )
    return ProofCase(
        name="target",
        file=sandbox,
        lemma_line=8,
        proof_start_line=9,
        qed_line=12,
        tactic_lines=[10, 11],
        index_entry=entry,
    )


def test_load_index_entries_from_fixture():
    entries = load_index_entries(FIXTURES / "mini_index.json")
    assert len(entries) == 1
    assert entries[0].name == "target"


def test_joy_corpus_builds_case_from_fixture_tree(tmp_path):
    data_dir = tmp_path / "data"
    corpus_dir = data_dir / "chapter"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "multi_lemma.ec").write_text(
        (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8")
    )
    (data_dir / "proofs_index.json").write_text(
        (FIXTURES / "mini_index.json").read_text(encoding="utf-8")
    )
    corpus = JoyCorpus(data_dir=data_dir, sandbox_dir=tmp_path / "sandboxes")
    cases = corpus.load_cases()
    assert len(cases) == 1
    assert cases[0].tactic_lines == [10, 11]


@patch("integration.experiment.runner.is_proof_incomplete", return_value=True)
@patch("integration.experiment.runner.is_proof_complete", return_value=True)
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_records_success(mock_run, mock_complete, mock_incomplete, tmp_path):
    mock_run.return_value = AgentResult(
        reason=ExitReason.COMPLETE,
        message="Proof complete",
        steps=3,
    )
    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-spec",
        corpus=StaticCorpus([case], {"target": case.index_entry.signature}),
        mutations=TacticMutationSet(),
    )
    out = tmp_path / "out"
    config = ExperimentConfig(
        trials=1,
        output_dir=out,
        seed=0,
        agent=AgentConfig(max_steps=5),
    )
    result = run_experiment(spec, config)
    assert result.trials_run == 1
    assert result.successes == 1
    assert (out / "summary.json").exists()
    assert (out / "events.jsonl").exists()
    mock_run.assert_called_once()

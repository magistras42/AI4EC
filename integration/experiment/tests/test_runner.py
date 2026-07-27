"""Tests for experiment runner orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from integration.agent.config import AgentConfig
from integration.agent.easycrypt import LlmResult
from integration.agent.loop import AgentResult, ExitReason
from integration.experiment.config import ExperimentConfig
from integration.experiment.corpora.joy import JoyCorpus, load_index_entries
from integration.experiment.informal import InformalConfig, InformalWriterError
from integration.experiment.mutations.tactics import TacticMutationSet
from integration.experiment.protocols import (
    BrokenFormalConfig,
    ExperimentSpec,
    IndexEntry,
    ProofCase,
)
from integration.experiment.runner import run_experiment

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class StaticCorpus:
    def __init__(self, cases: list[ProofCase]) -> None:
        self._cases = cases

    def load_cases(self) -> list[ProofCase]:
        return self._cases

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


def test_joy_corpus_does_not_clobber_sandboxes_for_lemmas_in_same_file(tmp_path):
    """Regression test: multiple lemmas from one chapter file must each get
    their own sandbox path. Previously every lemma in the same source file
    shared one destination path, so whichever entry was processed last
    silently overwrote every earlier lemma's truncated sandbox on disk."""
    data_dir = tmp_path / "data"
    corpus_dir = data_dir / "chapter"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "multi_lemma.ec").write_text(
        (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8")
    )
    index = {
        "proofs": [
            {
                "repo_slug": "tejasanilshah-the-joy-of-easycrypt",
                "file": "chapter/multi_lemma.ec",
                "line": 3,
                "kind": "lemma",
                "name": "first",
                "signature": "lemma first (n : int) : n + 0 = n.",
            },
            {
                "repo_slug": "tejasanilshah-the-joy-of-easycrypt",
                "file": "chapter/multi_lemma.ec",
                "line": 8,
                "kind": "lemma",
                "name": "target",
                "signature": "lemma target (n : int) : 0 + n = n.",
            },
            {
                "repo_slug": "tejasanilshah-the-joy-of-easycrypt",
                "file": "chapter/multi_lemma.ec",
                "line": 14,
                "kind": "lemma",
                "name": "after",
                "signature": "lemma after (n : int) : n = n.",
            },
        ]
    }
    (data_dir / "proofs_index.json").write_text(json.dumps(index), encoding="utf-8")

    corpus = JoyCorpus(data_dir=data_dir, sandbox_dir=tmp_path / "sandboxes", min_tactics=1)
    cases = {case.name: case for case in corpus.load_cases()}

    assert set(cases) == {"first", "target", "after"}
    # Each case must point at a distinct sandbox file...
    assert len({case.file for case in cases.values()}) == 3
    # ...whose on-disk content actually ends at *that* lemma's own qed,
    # not some other lemma's that happened to share the source file.
    assert cases["first"].file.read_text().strip().splitlines()[-1] == "qed."
    assert "lemma first" in cases["first"].file.read_text()
    assert "lemma target" not in cases["first"].file.read_text()

    assert "lemma target" in cases["target"].file.read_text()
    assert "lemma after" not in cases["target"].file.read_text()

    assert "lemma after" in cases["after"].file.read_text()


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
        corpus=StaticCorpus([case]),
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
    assert result.mode == "mutation"
    assert (out / "summary.json").exists()
    assert (out / "events.jsonl").exists()
    mock_run.assert_called_once()
    trial_agent_config = mock_run.call_args.args[1]
    assert "by rewrite /addC." in trial_agent_config.right_fix
    assert trial_agent_config.retrospective_file == (
        out / "trials" / "trial_000" / "timeout_retrospective.json"
    )


@patch("integration.experiment.runner.is_proof_incomplete", return_value=True)
@patch("integration.experiment.runner.is_proof_complete", return_value=True)
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_reports_token_usage_and_per_trial_average(
    mock_run, mock_complete, mock_incomplete, tmp_path
):
    def fake_agent(source, agent_config, work_copy=None):
        agent_config.usage_tracker.record(
            _usage_response(prompt=300, completion=50, cache_hit=200, cache_miss=100)
        )
        return AgentResult(reason=ExitReason.COMPLETE, message="Proof complete", steps=1)

    mock_run.side_effect = fake_agent
    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-spec",
        corpus=StaticCorpus([case, case]),
        mutations=TacticMutationSet(),
    )
    out = tmp_path / "out-usage"
    from integration.agent.config import apply_deepseek_provider

    agent = apply_deepseek_provider(AgentConfig(max_steps=5), model="deepseek-v4-flash")
    config = ExperimentConfig(
        trials=2,
        output_dir=out,
        seed=0,
        agent=agent,
    )

    result = run_experiment(spec, config)

    assert result.trials_run == 2
    assert result.token_usage.calls == 2
    assert result.token_usage.prompt_tokens == 600
    assert result.token_usage.completion_tokens == 100
    assert result.token_usage.cached_prompt_tokens == 400
    assert result.token_usage.cache_miss_prompt_tokens == 200
    assert result.token_usage_per_trial["prompt_tokens_per_trial"] == 300.0
    assert result.token_usage_per_trial["completion_tokens_per_trial"] == 50.0
    assert result.estimated_cost is not None
    assert result.estimated_cost["model"] == "deepseek-v4-flash"
    # 400 hit * 0.0028/1M + 200 miss * 0.14/1M + 100 out * 0.28/1M
    expected = (400 * 0.0028 + 200 * 0.14 + 100 * 0.28) / 1_000_000
    assert abs(result.estimated_cost["usd"] - expected) < 1e-12
    assert result.token_usage_per_trial["estimated_cost_usd_per_trial"] == round(
        expected / 2, 8
    )

    summary = json.loads((out / "summary.json").read_text())
    assert summary["token_usage"]["total_tokens"] == 700
    assert summary["token_usage"]["cache_miss_prompt_tokens"] == 200
    assert summary["token_usage_per_trial"]["total_tokens_per_trial"] == 350.0
    assert summary["trial_results"][0]["token_usage"]["prompt_tokens"] == 300
    assert summary["trial_results"][0]["estimated_cost"]["usd"] == round(
        expected / 2, 8
    )


def _usage_response(
    *,
    prompt: int,
    completion: int,
    cache_hit: int = 0,
    cache_miss: int | None = None,
):
    class _Usage:
        prompt_tokens = prompt
        completion_tokens = completion
        total_tokens = prompt + completion
        prompt_cache_hit_tokens = cache_hit
        prompt_cache_miss_tokens = (
            cache_miss if cache_miss is not None else max(0, prompt - cache_hit)
        )

    class _Response:
        usage = _Usage()

    return _Response()


@patch("integration.experiment.runner.is_proof_complete", return_value=True)
@patch("integration.experiment.runner.fetch_premises_at_cursor")
@patch("integration.experiment.runner.write_informal_proof")
@patch("integration.experiment.runner.select_red_herrings")
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_informal_mode(
    mock_run, mock_herrings, mock_writer, mock_catalog, mock_complete, tmp_path
):
    # The fixture's tactic script (multi_lemma.ec lines 10-11) literally
    # contains "addC" (via `/addC`) and "trivial", so name-matching against
    # this catalog will find "addC" as a used lemma.
    mock_catalog.return_value = {
        "addC": "lemma addC : forall x y, x + y = y + x.",
        "unrelated_lemma": "lemma unrelated_lemma : true.",
    }
    mock_writer.return_value = (
        "We reason by commutativity of addition to conclude the equality."
    )
    mock_herrings.return_value = {"unrelated_lemma": "lemma unrelated_lemma : true."}
    mock_run.return_value = AgentResult(
        reason=ExitReason.COMPLETE,
        message="Proof complete",
        steps=4,
    )

    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-informal-spec",
        corpus=StaticCorpus([case]),
        mutations=None,
        informal=InformalConfig(),
    )
    out = tmp_path / "out-informal"
    config = ExperimentConfig(
        trials=1,
        output_dir=out,
        seed=0,
        agent=AgentConfig(max_steps=5),
    )

    result = run_experiment(spec, config)
    trial_dir = out / "trials" / "trial_000"

    assert result.mode == "informal"
    assert result.trials_run == 1
    assert result.successes == 1
    assert result.trial_results[0].mode == "informal"

    mock_run.assert_called_once()
    trial_agent_config = mock_run.call_args.args[1]
    assert trial_agent_config.repair_hint is None
    assert trial_agent_config.informal_proof == mock_writer.return_value
    assert trial_agent_config.premises_override == {
        "addC": "lemma addC : forall x y, x + y = y + x.",
        "unrelated_lemma": "lemma unrelated_lemma : true.",
    }
    assert "by rewrite /addC." in trial_agent_config.right_fix
    assert trial_agent_config.retrospective_file == (
        trial_dir / "timeout_retrospective.json"
    )

    assert (trial_dir / "original.ec").exists()
    assert (trial_dir / "agent_start.ec").exists()
    assert not (trial_dir / "mutated.ec").exists()

    manifest = json.loads((trial_dir / "lemma_manifest.json").read_text())
    assert list(manifest.keys()) == sorted(manifest.keys())

    labeled = json.loads((trial_dir / "lemma_manifest_labeled.json").read_text())
    assert labeled["addC"]["is_real"] is True
    assert labeled["unrelated_lemma"]["is_real"] is False


@patch("integration.experiment.runner.is_proof_complete", return_value=True)
@patch("integration.experiment.runner.fetch_premises_at_cursor", return_value={})
@patch("integration.experiment.runner.write_informal_proof", return_value="by rewrite addrC.")
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_informal_mode_skips_on_contamination(
    mock_run, mock_writer, mock_catalog, mock_complete, tmp_path
):
    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-informal-spec",
        corpus=StaticCorpus([case]),
        mutations=None,
        informal=InformalConfig(),
    )
    out = tmp_path / "out-informal-contam"
    config = ExperimentConfig(
        trials=1,
        output_dir=out,
        seed=0,
        agent=AgentConfig(max_steps=5),
    )

    result = run_experiment(spec, config)
    assert result.trials_skipped == 1
    assert result.trial_results[0].skip_reason == "writer_leaked_code"
    mock_run.assert_not_called()


@patch("integration.experiment.runner.is_proof_complete", return_value=True)
@patch("integration.experiment.runner.fetch_premises_at_cursor", return_value={})
@patch("integration.experiment.runner.write_informal_proof")
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_informal_mode_skips_on_truncated_writer(
    mock_run, mock_writer, mock_catalog, mock_complete, tmp_path
):
    """Regression: a writer LLM that never manages to produce a complete,
    non-truncated informal proof (e.g. a local "thinking" model that spends
    its whole token budget on hidden reasoning) must produce a clean skip,
    not crash the whole experiment run."""
    mock_writer.side_effect = InformalWriterError("writer truncated after retries")
    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-informal-spec",
        corpus=StaticCorpus([case]),
        mutations=None,
        informal=InformalConfig(),
    )
    out = tmp_path / "out-informal-truncated"
    config = ExperimentConfig(
        trials=1,
        output_dir=out,
        seed=0,
        agent=AgentConfig(max_steps=5),
    )

    result = run_experiment(spec, config)
    assert result.trials_skipped == 1
    assert result.trial_results[0].skip_reason == "writer_truncated"
    mock_run.assert_not_called()


_OPEN_GOAL_STDOUT = (
    "Current goal\n\n"
    "Type variables: <none>\n\n"
    "------------------------------------------------------------------------\n"
    "0 + n = n\n"
)


@patch("integration.experiment.runner.fetch_goal_and_premises")
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_broken_formal_mode(mock_run, mock_fetch, tmp_path):
    """`elgamal-broken-repair`-style trial: no writer call, no red herrings/
    manifest, the corpus's own (broken) tactic text is passed through as
    `informal_proof` with `informal_proof_is_formal=True`, and premises are
    left to the full ambient catalog (`premises_override=None`)."""
    mock_fetch.return_value = LlmResult(returncode=0, stdout=_OPEN_GOAL_STDOUT, stderr="")
    mock_run.return_value = AgentResult(
        reason=ExitReason.COMPLETE,
        message="Proof complete",
        steps=5,
    )

    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-broken-formal-spec",
        corpus=StaticCorpus([case]),
        mutations=None,
        broken_formal=BrokenFormalConfig(),
    )
    out = tmp_path / "out-broken-formal"
    config = ExperimentConfig(
        trials=1,
        output_dir=out,
        seed=0,
        agent=AgentConfig(max_steps=5),
    )

    result = run_experiment(spec, config)
    trial_dir = out / "trials" / "trial_000"

    assert result.mode == "broken_formal"
    assert result.trials_run == 1
    assert result.successes == 1
    assert result.trial_results[0].mode == "broken_formal"

    mock_run.assert_called_once()
    trial_agent_config = mock_run.call_args.args[1]
    assert trial_agent_config.repair_hint is None
    assert trial_agent_config.premises_override is None
    assert trial_agent_config.informal_proof_is_formal is True
    assert "by rewrite /addC." in trial_agent_config.informal_proof
    assert "trivial." in trial_agent_config.informal_proof
    assert "by rewrite /addC." in trial_agent_config.right_fix

    assert (trial_dir / "original.ec").exists()
    assert (trial_dir / "agent_start.ec").exists()
    assert not (trial_dir / "lemma_manifest.json").exists()
    assert not (trial_dir / "lemma_manifest_labeled.json").exists()

    informal_proof_text = (trial_dir / "informal_proof.md").read_text(encoding="utf-8")
    assert "by rewrite /addC." in informal_proof_text


@patch("integration.experiment.runner.fetch_goal_and_premises")
@patch("integration.experiment.runner.run_agent")
def test_run_experiment_broken_formal_mode_skips_unreachable_goal(
    mock_run, mock_fetch, tmp_path
):
    """If admitting every prior lemma still doesn't make the target's goal
    reachable (e.g. a corpus-porting gap), the trial must skip cleanly
    rather than hand the solver a bogus/empty goal."""
    mock_fetch.return_value = LlmResult(returncode=1, stdout="", stderr="parse error")

    case = _make_case(tmp_path)
    spec = ExperimentSpec(
        name="test-broken-formal-spec",
        corpus=StaticCorpus([case]),
        mutations=None,
        broken_formal=BrokenFormalConfig(),
    )
    out = tmp_path / "out-broken-formal-unreachable"
    config = ExperimentConfig(
        trials=1,
        output_dir=out,
        seed=0,
        agent=AgentConfig(max_steps=5),
    )

    result = run_experiment(spec, config)
    assert result.trials_skipped == 1
    assert result.trial_results[0].skip_reason == "goal_unreachable"
    mock_run.assert_not_called()

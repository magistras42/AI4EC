"""Tests for EasyCrypt goal resolution and proof completeness detection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from integration.agent.config import NO_MORE_GOALS, AgentConfig
from integration.agent.easycrypt import (
    is_proof_complete_at_cursor,
    is_proof_discharged,
    resolve_goal,
    resolve_goal_cursor,
    tactic_discharged_proof,
    validate_file,
)
from integration.agent.loop import ExitReason, run_agent
from integration.agent.proof_file import ProofFile, create_working_copy

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.integration
def test_validate_file_success_does_not_imply_complete(easycrypt_bin, tmp_path):
    """Regression: `llm -lastgoals` exit 0 with empty stdout only means the
    tactics parsed; it must not be treated as proof completion."""
    source = FIXTURES / "hoare_after_proc.ec"
    work_copy = tmp_path / "hoare_after_proc.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    validation = validate_file(work_copy, config)
    proof = ProofFile(work_copy)

    assert validation.returncode == 0
    assert validation.stdout.strip() == ""
    assert not is_proof_complete_at_cursor(proof, config)


@pytest.mark.integration
def test_resolve_goal_after_proc_shows_ambient_subgoal(easycrypt_bin, tmp_path):
    source = FIXTURES / "hoare_after_proc.ec"
    work_copy = tmp_path / "hoare_after_proc.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    proof = ProofFile(work_copy)
    goal = resolve_goal(proof, config)

    assert "Current goal" in goal
    assert "pre = x = 1" in goal
    assert "post = x + 1 = 2" in goal
    assert "Func1.add_1" not in goal


@pytest.mark.integration
def test_resolve_goal_after_proc_not_stale_hoare_goal(easycrypt_bin, tmp_path):
    source = FIXTURES / "hoare_after_proc.ec"
    work_copy = tmp_path / "hoare_after_proc.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    proof = ProofFile(work_copy)
    goal = resolve_goal(proof, config)

    assert "Func1.add_1" not in goal
    assert "pre = arg = 1" not in goal


@pytest.mark.integration
def test_resolve_goal_cursor_after_proc_prefers_skip_probe_line(
    easycrypt_bin, tmp_path
):
    source = FIXTURES / "hoare_after_proc.ec"
    work_copy = tmp_path / "hoare_after_proc.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    proof = ProofFile(work_copy)
    bounds = proof.bounds()

    cursor = resolve_goal_cursor(proof, config)
    assert cursor != bounds.cursor_upto
    assert "x = 1" in resolve_goal(proof, config)


@pytest.mark.integration
def test_is_proof_complete_false_for_proc_only(easycrypt_bin, tmp_path):
    source = FIXTURES / "hoare_after_proc.ec"
    work_copy = tmp_path / "hoare_after_proc.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    assert not is_proof_complete_at_cursor(ProofFile(work_copy), config)


@pytest.mark.integration
def test_is_proof_complete_true_for_hoare_with_qed(easycrypt_bin, tmp_path):
    source = FIXTURES / "hoare_complete.ec"
    work_copy = tmp_path / "hoare_complete.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    assert is_proof_complete_at_cursor(ProofFile(work_copy), config)


@pytest.mark.integration
def test_is_proof_complete_true_for_ambient_with_qed(easycrypt_bin, tmp_path):
    source = FIXTURES / "ambient_complete.ec"
    work_copy = tmp_path / "ambient_complete.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    assert is_proof_complete_at_cursor(ProofFile(work_copy), config)


@pytest.mark.integration
def test_tactic_discharged_proof_after_ambient_tactic_without_qed(
    easycrypt_bin, tmp_path
):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    proof = ProofFile(work_copy)
    proof.append_tactic("by rewrite addr0.")

    resolved = resolve_goal(proof, config)
    assert resolved == ""
    assert tactic_discharged_proof(proof, resolved, config)
    assert is_proof_complete_at_cursor(proof, config)


@pytest.mark.integration
def test_tactic_discharged_proof_false_after_proc(easycrypt_bin, tmp_path):
    source = FIXTURES / "hoare_after_proc.ec"
    work_copy = tmp_path / "hoare_after_proc.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    proof = ProofFile(work_copy)
    resolved = resolve_goal(proof, config)

    assert resolved != ""
    assert not tactic_discharged_proof(proof, resolved, config)
    assert not is_proof_complete_at_cursor(proof, config)


@pytest.mark.integration
def test_is_proof_discharged_recognizes_no_more_goals():
    assert is_proof_discharged(NO_MORE_GOALS)
    assert is_proof_discharged("No active proof.")


@pytest.mark.integration
def test_experiment_is_proof_complete_rejects_proc_only(easycrypt_bin):
    from integration.experiment.verify import is_proof_complete, is_proof_incomplete

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    path = FIXTURES / "hoare_after_proc.ec"
    assert not is_proof_complete(path, config)
    assert is_proof_incomplete(path, config)


@pytest.mark.integration
def test_experiment_is_proof_complete_accepts_finished_hoare(easycrypt_bin):
    from integration.experiment.verify import is_proof_complete, is_proof_incomplete

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    path = FIXTURES / "hoare_complete.ec"
    assert is_proof_complete(path, config)
    assert not is_proof_incomplete(path, config)


@pytest.mark.integration
def test_experiment_is_proof_complete_accepts_finished_ambient(easycrypt_bin):
    from integration.experiment.verify import is_proof_complete, is_proof_incomplete

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    path = FIXTURES / "ambient_complete.ec"
    assert is_proof_complete(path, config)
    assert not is_proof_incomplete(path, config)


@pytest.mark.integration
def test_experiment_is_proof_incomplete_for_open_ambient_proof(easycrypt_bin):
    from integration.experiment.verify import is_proof_complete, is_proof_incomplete

    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    path = FIXTURES / "incomplete_proof.ec"
    assert not is_proof_complete(path, config)
    assert is_proof_incomplete(path, config)


@pytest.mark.integration
def test_agent_does_not_complete_after_proc_alone(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression for trial_002: `proc.` must not end the run while skip and
    ambient steps remain."""
    source = FIXTURES / "hoare_after_proof.ec"
    work_copy = tmp_path / "hoare_after_proof.agent.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=10,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=5,
    )

    prompts: list[str] = []
    calls = {"n": 0}

    class FakeLlm:
        def decide(self, prompt):
            from integration.agent.llm import LlmDecision, TacticAction

            prompts.append(prompt)
            calls["n"] += 1
            if calls["n"] == 1:
                return LlmDecision(action=TacticAction(tactic="proc."))
            return LlmDecision(action=TacticAction(tactic="skip."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)

    assert result.reason != ExitReason.COMPLETE
    assert calls["n"] >= 2
    text = work_copy.read_text(encoding="utf-8")
    assert "proc." in text
    assert "skip." in text
    goal_section = prompts[1].split("## Current goal", 1)[1].split("\n##", 1)[0]
    assert "pre = x = 1" in goal_section
    assert "post = x + 1 = 2" in goal_section
    assert "Func1.add_1" not in goal_section


@pytest.mark.integration
def test_agent_completes_full_hoare_proof(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "hoare_after_proof.ec"
    work_copy = tmp_path / "hoare_after_proof.agent.ec"
    create_working_copy(source, work_copy)

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=20,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
    )

    tactics = [
        "proc.",
        "skip.",
        "move => &m H1.",
        "subst.",
        "trivial.",
    ]
    tactic_index = {"i": 0}

    class FakeLlm:
        def decide(self, _prompt):
            from integration.agent.llm import LlmDecision, TacticAction

            idx = tactic_index["i"]
            tactic_index["i"] += 1
            return LlmDecision(action=TacticAction(tactic=tactics[idx]))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.COMPLETE
    text = work_copy.read_text(encoding="utf-8")
    for tactic in ("proc.", "skip.", "move => &m H1.", "subst.", "trivial."):
        assert tactic in text

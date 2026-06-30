"""Unit tests for the EasyCrypt agent package."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from integration.agent.embeddings import rank_by_cosine
from integration.agent.error_history import ErrorHistory, normalize_goal
from integration.agent.llm import parse_action
from integration.agent.premises import (
    load_cached_embeddings,
    parse_premises,
    save_cached_embeddings,
)
from integration.agent.proof_file import ProofFile, create_working_copy
from integration.agent.prompt import build_prompt

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SAMPLE_PREMISES = """
========== Top ==========

lemma myfirstlemma: forall (n : int), n + 0 = n.
lemma mysecondlemma: forall (n : int), 0 + n = n.

========== Core ==========

axiom addr0: forall (x : domain), add x rzero = x.
lemma bij_inj ['a, 'b]: forall (f : 'b -> 'a), bijective f => injective f.
"""


def test_parse_premises_extracts_names_and_theory():
    premises = parse_premises(SAMPLE_PREMISES)
    assert set(premises) == {
        "myfirstlemma",
        "mysecondlemma",
        "addr0",
        "bij_inj",
    }
    assert "[Top]" in premises["myfirstlemma"]
    assert "[Core]" in premises["addr0"]


def test_proof_file_append_and_undo(tmp_path):
    source = FIXTURES / "incomplete_proof.ec"
    copy = create_working_copy(source, tmp_path / "work.ec")
    proof = ProofFile(copy)

    bounds = proof.bounds()
    assert bounds.proof_start_line == 4
    assert bounds.qed_line is None
    assert bounds.cursor_upto == 4

    line_no = proof.append_tactic("by rewrite addr0.")
    lines = proof.read_lines()
    assert "by rewrite addr0." in lines[line_no - 1]

    assert proof.undo_last_tactic()
    assert "by rewrite addr0." not in "\n".join(proof.read_lines())

    assert not proof.undo_last_tactic()


def test_rank_by_cosine_orders_by_similarity():
    index = {
        "a": np.array([1.0, 0.0]),
        "b": np.array([0.9, 0.1]),
        "c": np.array([0.0, 1.0]),
    }
    goal = np.array([1.0, 0.0])
    ranked = rank_by_cosine(index, goal, k=2)
    assert ranked[0][0] == "a"
    assert ranked[1][0] == "b"


def test_parse_action_tactic_and_undo():
    tactic = parse_action('{"action": "tactic", "tactic": "by ring."}')
    assert tactic.kind == "tactic"
    assert tactic.tactic == "by ring."

    undo = parse_action('{"action": "undo"}')
    assert undo.kind == "undo"


def test_parse_action_lookup_lemma():
    lookup = parse_action('{"action": "lookup_lemma", "name": "addr0"}')
    assert lookup.kind == "lookup_lemma"
    assert lookup.name == "addr0"


def test_build_prompt_includes_repair_hint_and_lookup_tool():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        repair_hint="  by broken.",
        lookup_notes=["addr0: axiom addr0: ..."],
        enable_lemma_lookup=True,
    )
    assert "Repair hint" in prompt
    assert "by broken." in prompt
    assert "lookup_lemma" in prompt
    assert "Lemma lookup results" in prompt


@pytest.mark.integration
def test_agent_stuck_on_repeated_failed_tactics(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=50,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=3,
    )

    class FakeLlm:
        def decide(self, _prompt):
            from integration.agent.llm import TacticAction

            return TacticAction(tactic="by obviously_invalid_tactic.")

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
    assert result.reason == ExitReason.STUCK
    assert result.steps == 3


def test_error_history_normalizes_and_persists(tmp_path):
    path = tmp_path / "history.json"
    history = ErrorHistory(path)
    goal = "Current goal\n\nn: int\n--------\nn + 0 = n"
    history.add(goal, "error message", "by foo.")
    reloaded = ErrorHistory(path)
    assert reloaded.get(goal)[0] == ("error message", "by foo.")
    assert normalize_goal(goal) == normalize_goal("Current goal\n\nn: int\n--------\nn + 0 = n")


def test_premises_cache_roundtrip(tmp_path):
    work_copy = tmp_path / "proof.agent.ec"
    work_copy.write_text("lemma x.", encoding="utf-8")
    premises = {"foo": "lemma foo: true."}
    embeddings = {"foo": [1.0, 0.0, 0.0]}
    save_cached_embeddings(work_copy, 10, "embed-model", 1, premises, embeddings)
    loaded = load_cached_embeddings(work_copy, 10, "embed-model", 1)
    assert loaded == embeddings


def test_build_prompt_includes_goal_and_failures():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={"addr0": "axiom addr0: ..."},
        failed_tactics=[("bad tactic", "by foo.")],
        proof_tail="proof.",
        fewshot="example",
    )
    assert "n + 0 = n" in prompt
    assert "addr0" in prompt
    assert "by foo." in prompt
    assert "example" in prompt


@pytest.mark.integration
def test_agent_loop_with_mock_llm(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=3,
        max_steps=5,
        max_premises=20,
        llm_model="mock",
        embed_model="mock-embed",
    )

    class FakeLlm:
        def decide(self, _prompt):
            from integration.agent.llm import TacticAction

            return TacticAction(tactic="by rewrite addr0.")

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
    assert result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE)
    text = work_copy.read_text(encoding="utf-8")
    assert "by rewrite addr0." in text


def test_agent_run_log_written(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    log_path = tmp_path / "run.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=5,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        log_file=log_path,
    )

    class FakeLlm:
        def decide(self, _prompt):
            from integration.agent.llm import TacticAction

            return TacticAction(tactic="by rewrite addr0.")

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
    assert result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE)
    assert log_path.exists()

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    events = [event["event"] for event in payload["events"]]
    assert "startup" in events
    assert "iteration" in events
    assert "finish" in events
    assert payload["events"][-1]["reason"] == result.reason.name

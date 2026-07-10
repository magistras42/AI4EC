"""Unit tests for the EasyCrypt agent package."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from integration.agent.embeddings import rank_by_cosine
from integration.agent.error_history import ErrorHistory, normalize_goal
from integration.agent.llm import (
    LlmDecision,
    _action_text_from_message,
    _find_json_object,
    _response_content,
    _response_thought,
    parse_action,
)
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


def test_proof_file_append_tactic_collapses_embedded_newlines(tmp_path):
    """Regression: a degenerate LLM generation containing raw newlines must
    never be written as multiple physical lines. Otherwise, a rollback via
    `remove_lines(inserted_line)` (which only deletes 1 line) leaves the
    rest behind, permanently corrupting the proof file."""
    source = FIXTURES / "incomplete_proof.ec"
    copy = create_working_copy(source, tmp_path / "work.ec")
    proof = ProofFile(copy)

    before_line_count = len(proof.read_lines())
    garbage = "smt()." + ("\n" * 50) + "garbage\x08tail"
    line_no = proof.append_tactic(garbage)
    after_line_count = len(proof.read_lines())
    assert after_line_count == before_line_count + 1

    proof.remove_lines(line_no)
    assert len(proof.read_lines()) == before_line_count


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
    tactic = parse_action(
        '{"action": "tactic", "tactic": "by ring.", "name": ""}'
    )
    assert tactic.kind == "tactic"
    assert tactic.tactic == "by ring."

    undo = parse_action('{"action": "undo", "tactic": "", "name": ""}')
    assert undo.kind == "undo"


def test_parse_action_lookup_lemma():
    lookup = parse_action(
        '{"action": "lookup_lemma", "tactic": "", "name": "addr0"}'
    )
    assert lookup.kind == "lookup_lemma"
    assert lookup.name == "addr0"


def test_parse_action_rejects_prose_reasoning():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_action("Let's see if there's a better way.")


def test_parse_action_rejects_empty_content():
    with pytest.raises(ValueError, match="Empty LLM content"):
        parse_action("")


def test_parse_action_accepts_fenced_json_block():
    action = parse_action(
        'Preamble text\n```json\n{"action": "tactic", "tactic": "by ring.", "name": ""}\n```\n'
    )
    assert action.kind == "tactic"
    assert action.tactic == "by ring."


def test_find_json_object_extracts_embedded_action():
    text = (
        "Some analysis...\n"
        '{"action": "tactic", "tactic": "skip.", "name": ""}\n'
        "Trailing note."
    )
    assert _find_json_object(text) == '{"action": "tactic", "tactic": "skip.", "name": ""}'


def test_action_text_from_message_falls_back_to_reasoning_json():
    message = type(
        "Message",
        (),
        {
            "content": "",
            "reasoning_content": (
                "The goal is arithmetic.\n"
                "```json\n"
                '{"action": "tactic", "tactic": "by smt().", "name": ""}\n'
                "```"
            ),
        },
    )()
    assert _action_text_from_message(message) == (
        '{"action": "tactic", "tactic": "by smt().", "name": ""}'
    )


def test_response_content_never_uses_reasoning():
    message = type(
        "Message",
        (),
        {
            "content": "",
            "reasoning_content": (
                "Let's see if there's a better way.\n"
                '{"action": "tactic", "tactic": "trivial.", "name": ""}'
            ),
        },
    )()
    assert _response_content(message) == ""
    with pytest.raises(ValueError, match="Empty LLM content"):
        parse_action(_response_content(message))


def test_response_thought_extracts_reasoning_content():
    message = type(
        "Message",
        (),
        {"content": '{"action": "tactic", "tactic": "trivial."}', "reasoning_content": "Try trivial first."},
    )()
    assert _response_thought(message) == "Try trivial first."


def test_response_thought_extracts_reasoning_field():
    message = type(
        "Message",
        (),
        {
            "content": '{"action": "tactic", "tactic": "by ring.", "name": ""}',
            "reasoning_content": "",
            "reasoning": "The goal is an equality; ring should close it.",
        },
    )()
    assert _response_thought(message) == "The goal is an equality; ring should close it."


def test_response_thought_prefers_reasoning_content_over_reasoning():
    message = type(
        "Message",
        (),
        {
            "content": '{"action": "tactic", "tactic": "trivial.", "name": ""}',
            "reasoning_content": "Primary channel.",
            "reasoning": "Secondary channel.",
        },
    )()
    assert _response_thought(message) == "Primary channel."


def test_response_thought_extracts_nested_reasoning_dict():
    message = type(
        "Message",
        (),
        {
            "content": '{"action": "tactic", "tactic": "trivial.", "name": ""}',
            "reasoning": {"content": "Nested provider reasoning."},
        },
    )()
    assert _response_thought(message) == "Nested provider reasoning."


def test_response_thought_returns_none_when_absent():
    message = type("Message", (), {"content": "hello", "reasoning_content": ""})()
    assert _response_thought(message) is None


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
    assert "Required response shape" in prompt


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
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="by obviously_invalid_tactic."))

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


@pytest.mark.integration
def test_agent_rejects_admit_tactic_without_ever_writing_it(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression: EasyCrypt's `admit` tactic marks a goal as assumed and is
    reported as a fully discharged proof (return code 0, no open goals) even
    for false statements. The agent must reject it outright rather than
    treat it as a real success."""
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
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="admit."))

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
    assert "admit" not in work_copy.read_text(encoding="utf-8")


@pytest.mark.integration
def test_agent_rejects_degenerate_multiline_tactic_without_corrupting_file(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression: a runaway/degenerate LLM generation (raw newlines mixed
    into the "tactic" text) must be rejected before ever being written to
    the proof file, since a naive write+rollback would otherwise leave
    orphaned garbage lines that permanently break every later goal fetch."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)
    original_line_count = len(work_copy.read_text(encoding="utf-8").splitlines())

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=2,
    )

    class FakeLlm:
        def decide(self, _prompt):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(
                action=TacticAction(tactic="smt()." + ("\n" * 50) + "garbage\x08tail")
            )

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
    final_line_count = len(work_copy.read_text(encoding="utf-8").splitlines())
    assert final_line_count == original_line_count


@pytest.mark.integration
def test_agent_prompt_accumulates_all_past_failures_for_same_goal(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression/wiring check: at each step, the prompt handed to the LLM
    must include every previously failed tactic (and its error) for the
    *current* goal, not just the latest one, and not silently drop earlier
    ones. This is what should stop a weak model from cycling through the
    same failed tactic forever."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=3,
    )

    seen_prompts: list[str] = []

    class FakeLlm:
        def decide(self, prompt):
            from integration.agent.llm import LlmDecision, TacticAction

            seen_prompts.append(prompt)
            return LlmDecision(action=TacticAction(tactic="by obviously_invalid_tactic."))

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

    run_agent(source, config, work_copy=work_copy)

    assert len(seen_prompts) == 3
    # Step 1: no prior failures yet.
    assert "(none)" in seen_prompts[0]
    assert "do NOT repeat" not in seen_prompts[0]
    # Steps 2 and 3: every previously failed attempt at this same goal must
    # still be visible, and the anti-repetition warning must be present.
    for prompt in seen_prompts[1:]:
        assert "by obviously_invalid_tactic." in prompt
        assert "do NOT repeat" in prompt
    # The failure count strictly grows: step 3 sees at least as many
    # occurrences recorded as step 2 (it must not have been reset/dropped).
    assert seen_prompts[2].count("by obviously_invalid_tactic.") >= seen_prompts[
        1
    ].count("by obviously_invalid_tactic.")


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


def test_build_prompt_includes_all_past_failures_and_warns_against_repeats():
    """All recorded failures for the current goal must appear (not just the
    most recent one), and the prompt must explicitly warn against repeating
    them -- small/weak models have been observed to repeat a failed tactic
    verbatim otherwise."""
    failed_tactics = [
        ("cannot close goals", "by rewrite addz_gt0."),
        ("nothing to introduce", "move => h."),
        ("parse error", "smt(addz_gt0)."),
    ]
    prompt = build_prompt(
        goal="0 < x => 0 < x + 1",
        top_premises={},
        failed_tactics=failed_tactics,
        proof_tail="proof.",
    )
    for error, tactic in failed_tactics:
        assert tactic in prompt
        assert error in prompt
    assert "do NOT repeat" in prompt


def test_build_prompt_omits_repeat_warning_when_no_failures_yet():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
    )
    assert "(none)" in prompt
    assert "do NOT repeat" not in prompt


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
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="by rewrite addr0."))

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
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="by rewrite addr0."))

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


@pytest.mark.integration
def test_agent_run_log_includes_llm_thought(tmp_path, easycrypt_bin, monkeypatch):
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
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(
                action=TacticAction(tactic="by rewrite addr0."),
                thought="The goal is n + 0 = n; addr0 should rewrite it.",
                content='{"action": "tactic", "tactic": "by rewrite addr0.", "name": ""}',
            )

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

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    iterations = [e for e in payload["events"] if e["event"] == "iteration"]
    assert iterations
    assert iterations[0]["thought"] == (
        "The goal is n + 0 = n; addr0 should rewrite it."
    )
    assert iterations[0]["content"] == (
        '{"action": "tactic", "tactic": "by rewrite addr0.", "name": ""}'
    )


@pytest.mark.integration
def test_llm_client_default_mode_returns_reasoning_from_lm_studio():
    """Regression: strict response_format suppresses Gemma reasoning_content.

    Default agent mode should keep the two-channel design: JSON in content,
    reasoning in a separate provider field.
    """
    from openai import OpenAI

    from integration.agent.config import AgentConfig
    from integration.agent.llm import LlmClient
    from integration.agent.prompt import build_prompt

    client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
    try:
        models = client.models.list().data
    except Exception as exc:
        pytest.skip(f"LM Studio unavailable: {exc}")

    model_ids = [m.id for m in models]
    model = next((m for m in model_ids if "gemma" in m.lower()), None)
    if model is None:
        pytest.skip("No Gemma model loaded in LM Studio")

    prompt = build_prompt(
        goal="n: int\n--------\nn + 0 = n",
        top_premises={"addr0": "axiom addr0: forall (x : domain), add x rzero = x."},
        failed_tactics=[],
        proof_tail="proof.",
    )
    config = AgentConfig(llm_model=model, llm_json_mode=False, llm_max_tokens=1024)
    decision = LlmClient(config).decide(prompt)

    assert decision.action.kind == "tactic"
    assert decision.content.strip().startswith("{")
    assert decision.thought
    assert len(decision.thought) > 20
    assert "{" not in decision.thought[:20]

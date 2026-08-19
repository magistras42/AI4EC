"""Run-level USD spend cap (`--max-spend-usd`).

The cap must actually stop work, and must refuse to exist when it cannot be
enforced. A budget that silently does nothing is worse than no budget: the
user believes the run is bounded and it is not.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from integration.agent.budget import BudgetUnavailable, SpendBudget
from integration.agent.config import AgentConfig, apply_anthropic_provider
from integration.agent.llm import ChatReply, LlmClient, _record_usage
from integration.agent.usage import TokenUsage


def _openai_response(prompt: int, completion: int):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=prompt,
            completion_tokens_details=None,
        )
    )


def _anthropic_message(uncached: int, output: int, read: int = 0, write: int = 0):
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=uncached,
            output_tokens=output,
            cache_read_input_tokens=read,
            cache_creation_input_tokens=write,
        )
    )


def _budget(limit=1.0, provider="deepseek", model="deepseek-v4-flash"):
    return SpendBudget(limit_usd=limit, provider=provider, model=model)


# --- construction guards ----------------------------------------------------


def test_refuses_a_model_with_no_published_rates():
    with pytest.raises(BudgetUnavailable, match="no published rates"):
        SpendBudget(limit_usd=1.0, provider="deepseek", model="mystery-model")


def test_refuses_a_nonpositive_limit():
    with pytest.raises(BudgetUnavailable, match="must be positive"):
        SpendBudget(limit_usd=0.0, provider="deepseek", model="deepseek-v4-flash")
    with pytest.raises(BudgetUnavailable):
        SpendBudget(limit_usd=-1.0, provider="deepseek", model="deepseek-v4-flash")


def test_accepts_known_anthropic_and_deepseek_models():
    assert _budget(provider="anthropic", model="claude-opus-5").limit_usd == 1.0
    assert _budget(provider="deepseek", model="deepseek-v4-pro").limit_usd == 1.0


# --- accounting -------------------------------------------------------------


def test_accumulates_across_calls_and_flags_exhaustion():
    budget = _budget(limit=0.10)
    assert not budget.exhausted
    for _ in range(3):
        budget.record_openai(_openai_response(200_000, 20_000))
    # 3 x (200k input @ $0.14/M + 20k output @ $0.28/M) = $0.1008
    assert budget.spent_usd == pytest.approx(0.1008, rel=1e-6)
    assert budget.exhausted
    assert budget.remaining_usd == 0.0
    assert budget.exhausted_at_usd == pytest.approx(0.1008, rel=1e-6)


def test_exhausted_at_usd_records_only_the_first_crossing():
    budget = _budget(limit=0.01)
    budget.record_openai(_openai_response(100_000, 10_000))
    first = budget.exhausted_at_usd
    budget.record_openai(_openai_response(100_000, 10_000))
    assert budget.exhausted_at_usd == first, "must not be overwritten by later calls"


def test_anthropic_usage_is_priced_with_the_cache_split():
    budget = _budget(limit=100.0, provider="anthropic", model="claude-opus-5")
    budget.record_anthropic(_anthropic_message(uncached=100_000, output=10_000,
                                               read=800_000, write=50_000))
    # 100k @ $5/M + 800k @ $0.50/M + 50k @ $6.25/M + 10k @ $25/M
    expected = 0.5 + 0.4 + 0.3125 + 0.25
    assert budget.spent_usd == pytest.approx(expected, rel=1e-6)


def test_as_dict_is_json_ready():
    budget = _budget(limit=0.5)
    budget.record_openai(_openai_response(1000, 100))
    payload = budget.as_dict()
    assert payload["limit_usd"] == 0.5
    assert payload["calls"] == 1
    assert payload["exhausted"] is False
    assert payload["provider"] == "deepseek"


# --- wiring into the LLM path -----------------------------------------------


def test_record_usage_updates_both_tracker_and_budget():
    """A backend must not be able to report tokens while escaping the cap."""
    tracker = TokenUsage()
    budget = _budget(limit=1.0)
    config = AgentConfig(llm_provider="deepseek", llm_model="deepseek-v4-flash")
    config.usage_tracker = tracker
    config.spend_budget = budget

    _record_usage(config, _openai_response(1000, 100), anthropic=False)

    assert tracker.calls == 1 and tracker.prompt_tokens == 1000
    assert budget.usage.calls == 1 and budget.usage.prompt_tokens == 1000


def test_record_usage_handles_anthropic_shape():
    tracker = TokenUsage()
    budget = _budget(limit=1.0, provider="anthropic", model="claude-opus-5")
    config = apply_anthropic_provider(AgentConfig())
    config.usage_tracker = tracker
    config.spend_budget = budget

    _record_usage(config, _anthropic_message(100, 20, read=50), anthropic=True)

    assert tracker.prompt_tokens == 150  # uncached + read
    assert budget.usage.completion_tokens == 20


def test_budget_is_optional_everywhere():
    """No budget configured must remain the uncapped historical behaviour."""
    tracker = TokenUsage()
    config = AgentConfig()
    config.usage_tracker = tracker
    config.spend_budget = None
    _record_usage(config, _openai_response(10, 5), anthropic=False)
    assert tracker.calls == 1


# --- the loop actually stops ------------------------------------------------


def test_agent_loop_exits_when_the_cap_is_reached(tmp_path, monkeypatch):
    """End-to-end through run_agent: an exhausted budget stops before calling."""
    from integration.agent import loop as loop_mod

    budget = _budget(limit=0.01)
    # Pre-exhaust it, as a prior trial in the same run would have.
    budget.record_openai(_openai_response(1_000_000, 100_000))
    assert budget.exhausted

    proof = tmp_path / "p.ec"
    proof.write_text("lemma x : true.\nproof.\n", encoding="utf-8")

    config = AgentConfig(llm_provider="deepseek", llm_model="deepseek-v4-flash")
    config.spend_budget = budget
    config.max_steps = 5
    config.output_dir = tmp_path

    called = {"n": 0}

    class _NoCallClient:
        def __init__(self, *a, **k):
            pass

        def decide(self, *a, **k):
            called["n"] += 1
            raise AssertionError("LLM was called after the budget was exhausted")

    # Neutralize everything before the budget check so the test isolates it.
    monkeypatch.setattr(loop_mod, "LlmClient", _NoCallClient)
    monkeypatch.setattr(loop_mod, "_startup", lambda *a, **k: None)
    monkeypatch.setattr(
        loop_mod, "_build_premise_index", lambda *a, **k: ({}, {}, {})
    )
    monkeypatch.setattr(
        loop_mod, "EmbeddingClient", lambda *a, **k: SimpleNamespace(embed=lambda t: None)
    )
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda *a, **k: "goal: x = x")
    monkeypatch.setattr(loop_mod, "resolve_goal_cursor", lambda *a, **k: 1)
    monkeypatch.setattr(loop_mod, "is_proof_discharged", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "tactic_discharged_proof", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "is_no_active_proof", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "rank_by_cosine", lambda *a, **k: [])
    monkeypatch.setattr(loop_mod, "top_premises", lambda *a, **k: {})
    monkeypatch.setattr(
        loop_mod,
        "fetch_goal",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="goal", stderr=""),
    )

    result = loop_mod.run_agent(proof, config, work_copy=tmp_path / "w.agent.ec")

    assert result.reason is loop_mod.ExitReason.BUDGET_EXHAUSTED
    assert called["n"] == 0, "no paid call may be made once the cap is reached"
    assert "spend cap" in result.message.lower()


def test_llm_client_still_works_without_a_budget():
    class FakeBackend:
        def resolve_model(self):
            return "fake"

        def complete(self, **kwargs):
            return ChatReply(text='{"action": "tactic", "tactic": "trivial."}')

    decision = LlmClient(AgentConfig(), backend=FakeBackend()).decide("g")
    assert decision.action.kind == "tactic"

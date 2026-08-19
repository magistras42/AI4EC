"""Tests for DeepSeek pricing / cost estimates."""

from __future__ import annotations

from integration.agent.config import LLM_PROVIDER_DEEPSEEK, LLM_PROVIDER_LM_STUDIO
from integration.agent.pricing import estimate_usage_cost, lookup_model_rates
from integration.agent.usage import TokenUsage


def test_lookup_flash_and_pro_rates():
    flash = lookup_model_rates("deepseek-v4-flash")
    assert flash is not None
    assert flash.input_cache_hit == 0.0028
    assert flash.input_cache_miss == 0.14
    assert flash.output == 0.28

    pro = lookup_model_rates("deepseek-v4-pro")
    assert pro is not None
    assert pro.input_cache_miss == 0.435
    assert pro.output == 0.87


def test_deprecated_aliases_use_flash_rates():
    flash = lookup_model_rates("deepseek-v4-flash")
    chat = lookup_model_rates("deepseek-chat")
    reasoner = lookup_model_rates("deepseek-reasoner")
    assert flash is not None and chat is not None and reasoner is not None
    assert (chat.input_cache_hit, chat.input_cache_miss, chat.output) == (
        flash.input_cache_hit,
        flash.input_cache_miss,
        flash.output,
    )
    assert (reasoner.input_cache_hit, reasoner.input_cache_miss, reasoner.output) == (
        flash.input_cache_hit,
        flash.input_cache_miss,
        flash.output,
    )


def test_estimate_usage_cost_splits_hit_miss_output():
    usage = TokenUsage(
        calls=1,
        prompt_tokens=1000,
        completion_tokens=100,
        total_tokens=1100,
        cached_prompt_tokens=800,
        cache_miss_prompt_tokens=200,
    )
    cost = estimate_usage_cost(
        usage, provider=LLM_PROVIDER_DEEPSEEK, model="deepseek-v4-flash"
    )
    assert cost is not None
    expected = (800 * 0.0028 + 200 * 0.14 + 100 * 0.28) / 1_000_000
    assert abs(cost.usd - expected) < 1e-12
    assert cost.input_cache_hit_usd == round(800 * 0.0028 / 1_000_000, 8)
    assert cost.input_cache_miss_usd == round(200 * 0.14 / 1_000_000, 8)
    assert cost.output_usd == round(100 * 0.28 / 1_000_000, 8)


def test_estimate_usage_cost_none_for_local_or_unknown():
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert (
        estimate_usage_cost(
            usage, provider=LLM_PROVIDER_LM_STUDIO, model="gemma"
        )
        is None
    )
    assert (
        estimate_usage_cost(
            usage, provider=LLM_PROVIDER_DEEPSEEK, model="not-a-real-model"
        )
        is None
    )


def test_token_usage_records_cache_miss_from_response():
    usage = TokenUsage()

    class _U:
        prompt_tokens = 50
        completion_tokens = 10
        total_tokens = 60
        prompt_cache_hit_tokens = 30
        prompt_cache_miss_tokens = 20

    class _R:
        usage = _U()

    usage.record(_R())
    assert usage.cached_prompt_tokens == 30
    assert usage.cache_miss_prompt_tokens == 20

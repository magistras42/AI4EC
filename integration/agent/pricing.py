"""Paid-provider pricing used for experiment cost estimates.

Rates are USD per 1M tokens, from each provider's official pricing page:

* DeepSeek  -- https://api-docs.deepseek.com/quick_start/pricing/
* Anthropic -- https://platform.claude.com/docs/en/about-claude/models/overview

Product prices may change; ``PRICING_AS_OF`` records which snapshot we used.
Local providers (LM Studio) have no rates and estimate to ``None`` rather
than to zero, so "free" and "unknown" stay distinguishable in results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import LLM_PROVIDER_ANTHROPIC, LLM_PROVIDER_DEEPSEEK
from .usage import TokenUsage

PRICING_AS_OF = "2026-08-03"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
ANTHROPIC_PRICING_SOURCE = (
    "https://platform.claude.com/docs/en/about-claude/models/overview"
)

# Official table: cache-hit input / cache-miss input / output, USD per 1M tokens.
_DEEPSEEK_RATES_PER_MILLION: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.0028, 0.14, 0.28),
    "deepseek-v4-pro": (0.003625, 0.435, 0.87),
    # Deprecated aliases map onto flash (non-thinking / thinking).
    "deepseek-chat": (0.0028, 0.14, 0.28),
    "deepseek-reasoner": (0.0028, 0.14, 0.28),
}

# Anthropic publishes base input/output rates; the cache rates are the
# documented multipliers on base input -- 0.1x for a cache READ, 1.25x for a
# 5-minute-TTL cache WRITE. Writes are billed above a plain miss, which is why
# TokenUsage tracks them separately instead of folding them into misses.
_ANTHROPIC_CACHE_READ_MULTIPLIER = 0.1
_ANTHROPIC_CACHE_WRITE_MULTIPLIER = 1.25

# base input / output, USD per 1M tokens.
_ANTHROPIC_BASE_RATES_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}


@dataclass(frozen=True)
class ModelRates:
    """USD per 1M tokens for one chat model."""

    model: str
    input_cache_hit: float
    input_cache_miss: float
    output: float
    currency: str = "USD"
    as_of: str = PRICING_AS_OF
    source: str = PRICING_SOURCE
    # Rate for prompt tokens WRITTEN to the provider's cache. None when the
    # provider does not bill writes differently from ordinary misses
    # (DeepSeek), in which case cache writes are never reported either.
    cache_write: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CostEstimate:
    """Estimated API spend for a TokenUsage under known model rates."""

    usd: float
    model: str
    currency: str
    input_cache_hit_usd: float
    input_cache_miss_usd: float
    output_usd: float
    rates: ModelRates
    cache_write_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "usd": self.usd,
            "model": self.model,
            "currency": self.currency,
            "input_cache_hit_usd": self.input_cache_hit_usd,
            "input_cache_miss_usd": self.input_cache_miss_usd,
            "cache_write_usd": self.cache_write_usd,
            "output_usd": self.output_usd,
            "rates": self.rates.as_dict(),
        }


def lookup_model_rates(model: str | None) -> ModelRates | None:
    """Rates for a model id, searching every paid provider's table.

    Model ids are globally unambiguous between providers (``deepseek-*`` vs
    ``claude-*``), so a single lookup by id is safe and keeps callers that
    only have a model name working.
    """
    if not model:
        return None
    key = model.strip().lower()
    rates = _DEEPSEEK_RATES_PER_MILLION.get(key)
    if rates is not None:
        hit, miss, output = rates
        return ModelRates(
            model=key,
            input_cache_hit=hit,
            input_cache_miss=miss,
            output=output,
        )
    return lookup_anthropic_rates(key)


def lookup_anthropic_rates(model: str | None) -> ModelRates | None:
    """Rates for a Claude model, deriving cache rates from the base input rate."""
    if not model:
        return None
    key = model.strip().lower()
    base = _ANTHROPIC_BASE_RATES_PER_MILLION.get(key)
    if base is None:
        return None
    base_input, output = base
    return ModelRates(
        model=key,
        input_cache_hit=round(base_input * _ANTHROPIC_CACHE_READ_MULTIPLIER, 8),
        input_cache_miss=base_input,
        output=output,
        cache_write=round(base_input * _ANTHROPIC_CACHE_WRITE_MULTIPLIER, 8),
        source=ANTHROPIC_PRICING_SOURCE,
    )


def estimate_usage_cost(
    usage: TokenUsage,
    *,
    provider: str,
    model: str | None,
) -> CostEstimate | None:
    """Estimate USD spend. Returns None when the provider/model has no known rates."""
    if provider == LLM_PROVIDER_ANTHROPIC:
        rates = lookup_anthropic_rates(model)
    elif provider == LLM_PROVIDER_DEEPSEEK:
        rates = lookup_model_rates(model)
    else:
        # Local provider: free, but report unknown rather than $0.00 so a
        # results table cannot read a local run as a priced one.
        return None
    if rates is None:
        return None

    hit = usage.cached_prompt_tokens
    write = usage.cache_write_prompt_tokens
    miss = usage.cache_miss_prompt_tokens
    # Prefer explicit miss counts; otherwise the uncached remainder of the
    # prompt. Cache writes are already counted separately and must not be
    # double-billed into the miss bucket.
    if miss == 0 and usage.prompt_tokens > hit + write:
        miss = usage.prompt_tokens - hit - write

    hit_usd = _usd_for_tokens(hit, rates.input_cache_hit)
    miss_usd = _usd_for_tokens(miss, rates.input_cache_miss)
    write_usd = _usd_for_tokens(
        write, rates.cache_write if rates.cache_write is not None else rates.input_cache_miss
    )
    out_usd = _usd_for_tokens(usage.completion_tokens, rates.output)
    total = round(hit_usd + miss_usd + write_usd + out_usd, 8)
    return CostEstimate(
        usd=total,
        model=rates.model,
        currency=rates.currency,
        input_cache_hit_usd=hit_usd,
        input_cache_miss_usd=miss_usd,
        cache_write_usd=write_usd,
        output_usd=out_usd,
        rates=rates,
    )


def _usd_for_tokens(tokens: int, usd_per_million: float) -> float:
    return round((tokens / 1_000_000.0) * usd_per_million, 8)

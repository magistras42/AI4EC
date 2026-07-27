"""DeepSeek API pricing used for experiment cost estimates.

Rates are USD per 1M tokens from the official Models & Pricing page:
https://api-docs.deepseek.com/quick_start/pricing/

Product prices may change; ``PRICING_AS_OF`` records which snapshot we used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import LLM_PROVIDER_DEEPSEEK
from .usage import TokenUsage

PRICING_AS_OF = "2026-07-24"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"

# Official table: cache-hit input / cache-miss input / output, USD per 1M tokens.
_DEEPSEEK_RATES_PER_MILLION: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.0028, 0.14, 0.28),
    "deepseek-v4-pro": (0.003625, 0.435, 0.87),
    # Deprecated aliases map onto flash (non-thinking / thinking).
    "deepseek-chat": (0.0028, 0.14, 0.28),
    "deepseek-reasoner": (0.0028, 0.14, 0.28),
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "usd": self.usd,
            "model": self.model,
            "currency": self.currency,
            "input_cache_hit_usd": self.input_cache_hit_usd,
            "input_cache_miss_usd": self.input_cache_miss_usd,
            "output_usd": self.output_usd,
            "rates": self.rates.as_dict(),
        }


def lookup_model_rates(model: str | None) -> ModelRates | None:
    if not model:
        return None
    key = model.strip().lower()
    rates = _DEEPSEEK_RATES_PER_MILLION.get(key)
    if rates is None:
        return None
    hit, miss, output = rates
    return ModelRates(
        model=key,
        input_cache_hit=hit,
        input_cache_miss=miss,
        output=output,
    )


def estimate_usage_cost(
    usage: TokenUsage,
    *,
    provider: str,
    model: str | None,
) -> CostEstimate | None:
    """Estimate USD spend. Returns None when the provider/model has no known rates."""
    if provider != LLM_PROVIDER_DEEPSEEK:
        return None
    rates = lookup_model_rates(model)
    if rates is None:
        return None

    hit = usage.cached_prompt_tokens
    miss = usage.cache_miss_prompt_tokens
    # Prefer explicit miss counts; otherwise the uncached remainder of the prompt.
    if miss == 0 and usage.prompt_tokens > hit:
        miss = usage.prompt_tokens - hit

    hit_usd = _usd_for_tokens(hit, rates.input_cache_hit)
    miss_usd = _usd_for_tokens(miss, rates.input_cache_miss)
    out_usd = _usd_for_tokens(usage.completion_tokens, rates.output)
    total = round(hit_usd + miss_usd + out_usd, 8)
    return CostEstimate(
        usd=total,
        model=rates.model,
        currency=rates.currency,
        input_cache_hit_usd=hit_usd,
        input_cache_miss_usd=miss_usd,
        output_usd=out_usd,
        rates=rates,
    )


def _usd_for_tokens(tokens: int, usd_per_million: float) -> float:
    return round((tokens / 1_000_000.0) * usd_per_million, 8)

"""Token accounting for chat completions (cost tracking)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TokenUsage:
    """Cumulative prompt/completion tokens over one or more chat calls."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Subset of prompt_tokens served from the provider's context cache, when
    # reported. DeepSeek bills those at a lower rate.
    cached_prompt_tokens: int = 0
    # Subset of prompt_tokens that missed the context cache (full input rate).
    cache_miss_prompt_tokens: int = 0
    # Reasoning tokens billed as output by thinking models, when reported.
    reasoning_tokens: int = 0

    def record(self, response) -> None:
        """Accumulate an OpenAI-compatible response's ``usage`` block."""
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt = _as_int(getattr(usage, "prompt_tokens", 0))
        completion = _as_int(getattr(usage, "completion_tokens", 0))
        total = _as_int(getattr(usage, "total_tokens", 0)) or (prompt + completion)
        hit = _as_int(getattr(usage, "prompt_cache_hit_tokens", 0))
        miss = _as_int(getattr(usage, "prompt_cache_miss_tokens", 0))
        # DeepSeek reports both; if only hits are present, treat the rest as miss.
        if miss == 0 and prompt > hit:
            miss = prompt - hit
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.cached_prompt_tokens += hit
        self.cache_miss_prompt_tokens += miss
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            self.reasoning_tokens += _as_int(getattr(details, "reasoning_tokens", 0))

    def merge(self, other: "TokenUsage") -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cached_prompt_tokens += other.cached_prompt_tokens
        self.cache_miss_prompt_tokens += other.cache_miss_prompt_tokens
        self.reasoning_tokens += other.reasoning_tokens

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def average_usage(
    total: TokenUsage,
    trials: int,
    *,
    cost_usd: float | None = None,
) -> dict[str, Any]:
    """Per-trial averages, for comparing the economy of different runs."""
    if trials <= 0:
        return {
            "calls_per_trial": 0.0,
            "prompt_tokens_per_trial": 0.0,
            "completion_tokens_per_trial": 0.0,
            "total_tokens_per_trial": 0.0,
            "cached_prompt_tokens_per_trial": 0.0,
            "cache_miss_prompt_tokens_per_trial": 0.0,
            "estimated_cost_usd_per_trial": None if cost_usd is None else 0.0,
        }
    avg: dict[str, Any] = {
        "calls_per_trial": round(total.calls / trials, 2),
        "prompt_tokens_per_trial": round(total.prompt_tokens / trials, 2),
        "completion_tokens_per_trial": round(total.completion_tokens / trials, 2),
        "total_tokens_per_trial": round(total.total_tokens / trials, 2),
        "cached_prompt_tokens_per_trial": round(total.cached_prompt_tokens / trials, 2),
        "cache_miss_prompt_tokens_per_trial": round(
            total.cache_miss_prompt_tokens / trials, 2
        ),
    }
    if cost_usd is not None:
        avg["estimated_cost_usd_per_trial"] = round(cost_usd / trials, 8)
    else:
        avg["estimated_cost_usd_per_trial"] = None
    return avg


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

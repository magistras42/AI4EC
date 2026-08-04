"""Run-level spend cap for paid chat providers.

The harness could previously bound a run only *indirectly* -- `--trials` x
`--max-steps` gives an upper bound on call count, and `--llm-max-tokens` caps
each call's output -- but nothing bounded the actual dollars, and the cost
estimate was computed only after the run finished. That makes "let me try this
for about a dollar" impossible to express.

:class:`SpendBudget` is a single mutable object shared by every trial in a run
(unlike `TokenUsage`, which the experiment runner creates fresh per trial so it
can report per-trial figures). It accumulates usage across the whole run and
prices it with the same `pricing.py` tables that produce `estimated_cost`, so
the number it enforces against is the number the summary reports.

Two honest limitations, both deliberate:

* **The cap is checked after a call returns**, because token usage is not known
  until then. Actual spend can therefore overshoot by at most one call. With
  the default 16k output cap that is fractions of a cent on DeepSeek and a few
  cents on Opus -- but it is an overshoot, not a hard ceiling, and callers are
  told so rather than left to assume otherwise.
* **A cap is refused outright when the model's rates are unknown**, rather than
  silently not enforcing. A budget that quietly does nothing is worse than no
  budget at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .pricing import estimate_usage_cost, lookup_anthropic_rates, lookup_model_rates
from .usage import TokenUsage


class BudgetUnavailable(RuntimeError):
    """A spend cap was requested for a provider/model with no known rates."""


@dataclass
class SpendBudget:
    """A USD ceiling on one experiment run, enforced across all trials."""

    limit_usd: float
    provider: str
    model: str | None
    # Cumulative across the ENTIRE run, unlike the per-trial TokenUsage the
    # runner builds for reporting. This is the whole point of the class.
    usage: TokenUsage = field(default_factory=TokenUsage)
    # Set once the ceiling is first crossed, so callers can distinguish
    # "stopped because of budget" from "stopped for any other reason".
    exhausted_at_usd: float | None = None

    def __post_init__(self) -> None:
        if self.limit_usd <= 0:
            raise BudgetUnavailable(
                f"spend limit must be positive, got {self.limit_usd}"
            )
        if not self._rates_known():
            raise BudgetUnavailable(
                f"no published rates for provider {self.provider!r} model "
                f"{self.model!r}, so a spend cap cannot be enforced. Remove "
                "--max-spend-usd, or use a model with known pricing."
            )

    def _rates_known(self) -> bool:
        rates = (
            lookup_anthropic_rates(self.model)
            if self.provider == "anthropic"
            else lookup_model_rates(self.model)
        )
        return rates is not None

    @property
    def spent_usd(self) -> float:
        estimate = estimate_usage_cost(
            self.usage, provider=self.provider, model=self.model
        )
        return estimate.usd if estimate is not None else 0.0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.limit_usd

    def record_openai(self, response) -> None:
        """Accumulate an OpenAI-compatible response (LM Studio / DeepSeek)."""
        self.usage.record(response)
        self._note_exhaustion()

    def record_anthropic(self, message) -> None:
        """Accumulate an Anthropic Messages API response."""
        self.usage.record_anthropic(message)
        self._note_exhaustion()

    def _note_exhaustion(self) -> None:
        if self.exhausted_at_usd is None and self.exhausted:
            self.exhausted_at_usd = self.spent_usd

    def status(self) -> str:
        """One-line human summary, for logs and exit messages."""
        return (
            f"${self.spent_usd:.4f} of ${self.limit_usd:.2f} spent "
            f"({self.usage.calls} calls, ${self.remaining_usd:.4f} remaining)"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "limit_usd": self.limit_usd,
            "spent_usd": round(self.spent_usd, 8),
            "remaining_usd": round(self.remaining_usd, 8),
            "exhausted": self.exhausted,
            "exhausted_at_usd": self.exhausted_at_usd,
            "provider": self.provider,
            "model": self.model,
            "calls": self.usage.calls,
        }

"""Configuration for mutation repair experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from integration.agent.config import DEFAULT_OUTPUT_DIR, AgentConfig


def _default_experiment_output() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_DIR / "experiments" / f"run-{stamp}"


@dataclass
class ExperimentConfig:
    spec_name: str = "joy-tactic-repair"
    trials: int = 10
    stuck_limit: int = 20
    seed: int | None = None
    data_dir: Path = field(default_factory=lambda: Path("data"))
    output_dir: Path = field(default_factory=_default_experiment_output)
    mutation_retries: int = 5
    agent: AgentConfig = field(default_factory=AgentConfig)
    # Attempt the shortest proofs first instead of sampling at random. Makes a
    # bounded run spend its budget on the cases most likely to be completable,
    # and makes runs comparable to each other (deterministic order, no seed).
    sort_by_difficulty: bool = False
    # Per-trial step budget as a multiple of the proof's own tactic count.
    # A 3-line proof and a 104-line proof should not get the same allowance:
    # a flat --max-steps either starves the long ones or wastes calls on the
    # short ones. None keeps the flat `agent.max_steps`.
    adaptive_steps_multiplier: float | None = None
    min_adaptive_steps: int = 10
    # USD ceiling for the whole run. Constructed into an
    # integration.agent.budget.SpendBudget by `with_agent_defaults`, which is
    # what actually enforces it. Equivalent to the --max-spend-usd CLI flag.
    cost_limit_usd: float | None = None

    def steps_for_case(self, tactic_line_count: int) -> int:
        """Step budget for one case under the adaptive policy."""
        if self.adaptive_steps_multiplier is None:
            return self.agent.max_steps
        scaled = int(self.adaptive_steps_multiplier * max(0, tactic_line_count))
        return max(self.min_adaptive_steps, scaled)

    def with_agent_defaults(self) -> ExperimentConfig:
        """Apply experiment-specific agent defaults."""
        self.agent.stuck_limit = self.stuck_limit
        if self.cost_limit_usd is not None and self.agent.spend_budget is None:
            # Imported here so the module stays importable without the pricing
            # tables loaded for callers that never set a cap.
            from integration.agent.budget import SpendBudget

            self.agent.spend_budget = SpendBudget(
                limit_usd=self.cost_limit_usd,
                provider=self.agent.llm_provider,
                model=self.agent.llm_model,
            )
        return self

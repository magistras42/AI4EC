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
    # When True, trials are ordered shortest-proof-first (by tactic line count)
    # instead of randomly sampled. Gives a clearer sense of capability gradient.
    sort_by_difficulty: bool = False
    # When set, per-trial max_steps = max(min_adaptive_steps, int(multiplier * proof_lines)).
    # Overrides the global agent.max_steps for broken-formal trials.
    adaptive_steps_multiplier: float | None = None
    min_adaptive_steps: int = 10
    # Stop the experiment after cumulative API spend reaches this amount.
    # None disables the cost limit (runs all trials regardless of spend).
    cost_limit_usd: float | None = None

    def with_agent_defaults(self) -> ExperimentConfig:
        """Apply experiment-specific agent defaults."""
        self.agent.stuck_limit = self.stuck_limit
        return self

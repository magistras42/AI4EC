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

    def with_agent_defaults(self) -> ExperimentConfig:
        """Apply experiment-specific agent defaults."""
        self.agent.stuck_limit = self.stuck_limit
        return self

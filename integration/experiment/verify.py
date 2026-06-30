"""EasyCrypt proof completeness checks for experiments."""

from __future__ import annotations

from pathlib import Path

from integration.agent.config import AgentConfig
from integration.agent.easycrypt import has_open_goals, validate_file


def is_proof_complete(path: Path, config: AgentConfig) -> bool:
    result = validate_file(path, config)
    return result.returncode == 0 and not has_open_goals(result.stdout)


def is_proof_incomplete(path: Path, config: AgentConfig) -> bool:
    result = validate_file(path, config)
    return result.returncode == 0 and has_open_goals(result.stdout)

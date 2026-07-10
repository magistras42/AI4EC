"""EasyCrypt proof completeness checks for experiments."""

from __future__ import annotations

from pathlib import Path

from integration.agent.config import AgentConfig
from integration.agent.easycrypt import is_proof_complete_at_cursor
from integration.agent.proof_file import ProofFile


def is_proof_complete(path: Path, config: AgentConfig) -> bool:
    return is_proof_complete_at_cursor(ProofFile(path), config)


def is_proof_incomplete(path: Path, config: AgentConfig) -> bool:
    proof = ProofFile(path)
    bounds = proof.bounds()
    if bounds.proof_start_line == 0:
        return False
    return not is_proof_complete(path, config)

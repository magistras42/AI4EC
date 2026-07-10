"""Configuration for the EasyCrypt agent loop."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "integration" / "output"
DEFAULT_EASYCRYPT_BIN = (
    REPO_ROOT
    / "integration"
    / "extern"
    / "easycrypt"
    / "_build"
    / "default"
    / "src"
    / "ec.exe"
)

PREMISES_SEPARATOR = "(* --- premises --- *)"
NO_ACTIVE_PROOF = "No active proof."
NO_MORE_GOALS = "No more goals"


@dataclass
class AgentConfig:
    easycrypt_bin: Path = field(default_factory=lambda: _resolve_easycrypt_bin())
    lm_studio_base_url: str = field(
        default_factory=lambda: os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    )
    llm_model: str | None = field(
        default_factory=lambda: os.environ.get("LM_STUDIO_LLM_MODEL")
    )
    embed_model: str | None = field(
        default_factory=lambda: os.environ.get("LM_STUDIO_EMBED_MODEL")
    )
    top_k: int = 10
    max_steps: int = 200
    max_premises: int | None = None
    easycrypt_timeout: int = field(
        default_factory=lambda: int(os.environ.get("EASYCRYPT_TIMEOUT", "120"))
    )
    lm_studio_timeout: int = field(
        default_factory=lambda: int(os.environ.get("LM_STUDIO_TIMEOUT", "600"))
    )
    embed_batch_size: int = 64
    llm_temperature: float = 0.2
    llm_json_mode: bool = False
    llm_tactic_only: bool = False
    llm_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("LM_STUDIO_LLM_MAX_TOKENS", "8192"))
    )
    work_copy_suffix: str = ".agent.ec"
    output_dir: Path = field(default_factory=lambda: DEFAULT_OUTPUT_DIR)
    promote_on_success: bool = False
    proof_tail_lines: int = 20
    log_file: Path | None = None
    repair_hint: str | None = None
    informal_proof: str | None = None
    premises_override: dict[str, str] | None = None
    lemma_lookup_index: dict[str, str] | None = None
    stuck_limit: int | None = None


def _resolve_easycrypt_bin() -> Path:
    env_path = os.environ.get("EASYCRYPT")
    if env_path:
        return Path(env_path)
    return DEFAULT_EASYCRYPT_BIN

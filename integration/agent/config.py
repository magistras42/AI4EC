"""Configuration for the EasyCrypt agent loop."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .usage import TokenUsage


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

LLM_PROVIDER_LM_STUDIO = "lm_studio"
LLM_PROVIDER_DEEPSEEK = "deepseek"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_THINKING = "disabled"
DEFAULT_THINKING_FAILURE_WINDOW = 5
# Trajectory outcomes that count as "recent failure" for adaptive thinking.
THINKING_FAILURE_OUTCOMES = frozenset(
    {"failed", "rejected", "search_limited", "format_error"}
)
DEFAULT_LLM_MAX_TOKENS = 16384


@dataclass
class AgentConfig:
    easycrypt_bin: Path = field(default_factory=lambda: _resolve_easycrypt_bin())
    llm_provider: str = LLM_PROVIDER_LM_STUDIO
    lm_studio_base_url: str = field(
        default_factory=lambda: os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    )
    llm_api_key: str | None = None
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
    # None/False = no response_format; True forces provider JSON mode.
    # DeepSeek's json_object does not enforce our action schema, so default off.
    llm_json_mode: bool | None = None
    llm_tactic_only: bool = False
    llm_max_tokens: int = field(
        default_factory=lambda: int(
            os.environ.get("LM_STUDIO_LLM_MAX_TOKENS", str(DEFAULT_LLM_MAX_TOKENS))
        )
    )
    # DeepSeek V4 thinking controls (ignored for LM Studio). Official API:
    # thinking enabled|disabled; reasoning_effort high|max when thinking is on.
    # ``adaptive`` enables thinking only after recent failure-like steps
    # (see thinking_failure_window).
    llm_thinking: str | None = None
    llm_reasoning_effort: str | None = None
    thinking_failure_window: int = DEFAULT_THINKING_FAILURE_WINDOW
    work_copy_suffix: str = ".agent.ec"
    output_dir: Path = field(default_factory=lambda: DEFAULT_OUTPUT_DIR)
    promote_on_success: bool = False
    proof_tail_lines: int = 20
    log_file: Path | None = None
    repair_hint: str | None = None
    informal_proof: str | None = None
    # When True, `informal_proof` holds a genuinely broken *formal* EasyCrypt
    # tactic script (e.g. the elgamal-broken-repair spec), not a writer-LLM
    # natural-language paraphrase. Only changes the prompt section heading.
    informal_proof_is_formal: bool = False
    premises_override: dict[str, str] | None = None
    lemma_search_top_k: int = 5
    # Cap consecutive lookup/search actions; warn on the penultimate call.
    # None disables the cap. Default: warn on 4th, reject from 6th onward.
    max_continuous_searches: int | None = 5
    stuck_limit: int | None = None
    # Exit STUCK once the same normalized tactic fails this many times at one
    # goal (including hard-rejected repeats). None disables the early abort.
    identical_fail_limit: int | None = 3
    # Extra stuck weight applied when the model re-proposes a previously
    # failed tactic (hard-rejected without calling EasyCrypt).
    repeat_stuck_weight: int = 2
    # Hard cap on consecutive undo requests that remove zero tactics (i.e.
    # there is nothing left to undo). The model can always specify a larger
    # `count`, so repeating a no-op undo indicates it is stuck rather than
    # making progress; exceeding this triggers an early STUCK exit.
    max_consecutive_noop_undos: int | None = 3
    history_steps: int = 20
    right_fix: str | None = None
    retrospective_file: Path | None = None
    # Shared, mutable: every chat completion made with this config adds to it.
    usage_tracker: TokenUsage | None = None


def _resolve_easycrypt_bin() -> Path:
    env_path = os.environ.get("EASYCRYPT")
    if env_path:
        return Path(env_path)
    return DEFAULT_EASYCRYPT_BIN


def deepseek_base_url() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")


def deepseek_api_key(config: AgentConfig | None = None) -> str | None:
    if config is not None and config.llm_api_key:
        return config.llm_api_key
    return os.environ.get("DEEPSEEK_API_KEY")


def apply_deepseek_provider(
    config: AgentConfig,
    *,
    model: str | None = None,
    thinking: str | None = None,
    reasoning_effort: str | None = None,
) -> AgentConfig:
    """Point chat completions at DeepSeek. Embeddings stay on LM Studio.

    Defaults to thinking disabled: V4 thinking defaults to high effort and is
    what burns the output budget on simple tactic-selection steps.
    """
    config.llm_provider = LLM_PROVIDER_DEEPSEEK
    config.llm_model = model or DEFAULT_DEEPSEEK_MODEL
    if thinking is not None:
        config.llm_thinking = thinking
    elif config.llm_thinking is None:
        config.llm_thinking = DEFAULT_DEEPSEEK_THINKING
    if reasoning_effort is not None:
        config.llm_reasoning_effort = reasoning_effort
    return config


def chat_client_kwargs(config: AgentConfig) -> dict[str, Any]:
    """OpenAI-SDK kwargs for solver/writer chat completions."""
    if config.llm_provider == LLM_PROVIDER_DEEPSEEK:
        api_key = deepseek_api_key(config)
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Export it before using --deepseek."
            )
        return {
            "base_url": deepseek_base_url(),
            "api_key": api_key,
            "timeout": config.lm_studio_timeout,
        }
    return {
        "base_url": config.lm_studio_base_url,
        "api_key": config.llm_api_key or "lm-studio",
        "timeout": config.lm_studio_timeout,
    }


def deepseek_thinking(config: AgentConfig) -> str:
    """Configured thinking mode: ``enabled``, ``disabled``, or ``adaptive``."""
    return config.llm_thinking or DEFAULT_DEEPSEEK_THINKING


def resolve_thinking_for_step(
    config: AgentConfig,
    trajectory: list[dict[str, Any]] | None = None,
) -> str:
    """Resolve per-call thinking to ``enabled`` or ``disabled``.

    With ``llm_thinking='adaptive'``, enable thinking when any of the last
    ``thinking_failure_window`` trajectory steps has a failure-like outcome.
    """
    mode = deepseek_thinking(config)
    if mode in {"enabled", "disabled"}:
        return mode
    if mode != "adaptive":
        raise ValueError(
            "llm_thinking must be 'enabled', 'disabled', or 'adaptive', "
            f"got {mode!r}"
        )
    window = max(0, int(config.thinking_failure_window))
    if window <= 0 or not trajectory:
        return "disabled"
    recent = trajectory[-window:]
    if any(step.get("outcome") in THINKING_FAILURE_OUTCOMES for step in recent):
        return "enabled"
    return "disabled"


def action_response_format_mode(config: AgentConfig) -> str | None:
    """Which ``response_format`` flavour to request for action selection.

    Opt-in only via ``llm_json_mode=True`` / ``--llm-json-mode``. DeepSeek's
    ``json_object`` mode does not enforce our action schema; LM Studio's
    ``json_schema`` form is also opt-in because small local models often reject
    it.
    """
    if not config.llm_json_mode:
        return None
    if config.llm_provider == LLM_PROVIDER_DEEPSEEK:
        return "json_object"
    return "json_schema"


def chat_completion_kwargs(
    config: AgentConfig,
    *,
    thinking: str | None = None,
) -> dict[str, Any]:
    """Provider-specific extras for ``chat.completions.create``.

    Pass *thinking* as ``enabled``/``disabled`` to override the configured mode
    for a single call (used by adaptive thinking). ``adaptive`` is not valid
    here — resolve it with :func:`resolve_thinking_for_step` first.
    """
    if config.llm_provider != LLM_PROVIDER_DEEPSEEK:
        return {}

    resolved = thinking if thinking is not None else deepseek_thinking(config)
    if resolved == "adaptive":
        resolved = "disabled"
    if resolved not in {"enabled", "disabled"}:
        raise ValueError(
            f"llm_thinking must be 'enabled' or 'disabled', got {resolved!r}"
        )

    kwargs: dict[str, Any] = {
        "extra_body": {"thinking": {"type": resolved}},
    }
    # Official DeepSeek V4 effort values when thinking is on: high | max.
    # Omit when thinking is disabled (API ignores / may reject null effort).
    if resolved == "enabled" and config.llm_reasoning_effort:
        effort = config.llm_reasoning_effort
        if effort not in {"high", "max"}:
            raise ValueError(
                f"llm_reasoning_effort must be 'high' or 'max', got {effort!r}"
            )
        kwargs["reasoning_effort"] = effort
    return kwargs

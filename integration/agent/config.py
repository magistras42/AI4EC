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
LLM_PROVIDER_ANTHROPIC = "anthropic"

# Every provider the solver/writer chat path can be pointed at. LM Studio is
# the local default (Gemma et al. via an OpenAI-compatible server); DeepSeek
# and Anthropic are hosted and paid. Embeddings ALWAYS stay on LM Studio
# regardless of this choice -- neither hosted provider is used for them.
LLM_PROVIDERS = (
    LLM_PROVIDER_LM_STUDIO,
    LLM_PROVIDER_DEEPSEEK,
    LLM_PROVIDER_ANTHROPIC,
)

# Providers that spend real money, and therefore require the interactive
# human confirmation in integration/experiment/paid_confirm.py. See AGENTS.md:
# agents must never answer that prompt on the user's behalf.
PAID_LLM_PROVIDERS = frozenset({LLM_PROVIDER_DEEPSEEK, LLM_PROVIDER_ANTHROPIC})

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_THINKING = "disabled"

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
# Claude Opus 5 thinks by default; `adaptive` is the explicit spelling of that
# default and lets Claude choose depth per request. Unlike DeepSeek (where
# thinking defaults off because V4 burns the output budget on trivial
# tactic-selection steps), adaptive thinking is the recommended mode here and
# is what the effort knob is designed to modulate.
DEFAULT_ANTHROPIC_THINKING = "adaptive"
DEFAULT_ANTHROPIC_EFFORT = "high"

# Effort levels each provider accepts. DeepSeek V4 documents only high/max;
# Anthropic's effort ladder is the full five. Sending an out-of-range value is
# a 400 on both, so the CLI validates against these rather than a shared list.
REASONING_EFFORTS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    LLM_PROVIDER_DEEPSEEK: ("high", "max"),
    LLM_PROVIDER_ANTHROPIC: ("low", "medium", "high", "xhigh", "max"),
}
# Claude rejects `thinking: {"type": "disabled"}` above this effort level.
_ANTHROPIC_EFFORTS_REQUIRING_THINKING = frozenset({"xhigh", "max"})

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
    # The ORIGINAL tactic that stopped working, and EasyCrypt's complaint about
    # it, set by repair_bootstrap. Passed separately from the remaining script
    # because it is not reference material -- it is the thing to repair, and
    # measurement says the model was not treating it that way: on run G's
    # `G2_G3` it reused an original tactic verbatim in 8 of 40 attempts and
    # spent 19 attempts on `rnd` variants where the original script uses `rnd`
    # exactly once.
    broken_tactic: str | None = None
    broken_tactic_error: str | None = None
    # Changelog/repair_doc facts for the specific tactic that broke, set by
    # integration/experiment/repair_bootstrap.py when a version-drift-shaped
    # failure occurs during replay. Distinct from repair_hint (the mutated
    # broken tactic script shown as a reference) -- this is dated,
    # sourced library-change evidence, not a reference proof.
    changelog_hints: str | None = None
    informal_proof: str | None = None
    # When True, `informal_proof` holds a genuinely broken *formal* EasyCrypt
    # tactic script (e.g. the elgamal-broken-repair spec), not a writer-LLM
    # natural-language paraphrase. Only changes the prompt section heading.
    informal_proof_is_formal: bool = False
    # Overrides the section heading for `informal_proof`. Replay-bootstrap
    # uses it to label the REMAINING original tactics accurately: only the
    # first is known-broken, the rest were never reached.
    informal_proof_heading: str | None = None
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
    # Abort after this many consecutive replies in which the provider
    # produced nothing usable (no choices, or the output budget was spent
    # reasoning). Bounded separately from the stuck counter because it is
    # an infrastructure signal, not a proof-progress one. None disables.
    max_consecutive_provider_failures: int | None = 5
    history_steps: int = 20
    right_fix: str | None = None
    retrospective_file: Path | None = None
    # Attempt a verified, line-preserving import/syntax repair when the file
    # will not load at startup (integration/agent/import_repair.py). Off by
    # default so a plain agent run never rewrites the user's file implicitly;
    # the experiment's replay-bootstrap mode drives its own repair separately.
    import_repair: bool = False
    # EasyCrypt version endpoints scoping which migration rules and changelog
    # releases apply. None means "detect" -- see
    # integration/agent/ec_version.py. Explicit values always win over
    # detection, since a corpus's authoring-time version is often knowable
    # from outside the file.
    source_ec_version: str | None = None
    target_ec_version: str | None = None
    # Re-fetch changelog/repair_doc hints on every tactic failure, hopping to
    # the next unconsumed release each time (roadmap W3), instead of freezing
    # the block fetched at bootstrap for the whole run. Opt-in: each refresh
    # costs a retrieval pass, and only version-drift experiments benefit --
    # a mutation trial's failures are synthetic and map to no real release.
    live_changelog_hints: bool = False
    # Shared, mutable: every chat completion made with this config adds to it.
    usage_tracker: TokenUsage | None = None
    # Run-level USD ceiling (integration/agent/budget.py). Unlike
    # usage_tracker, which the experiment runner rebuilds per trial for
    # per-trial reporting, this object is shared by EVERY trial in a run so the
    # cap applies to cumulative spend rather than resetting each trial.
    # None = uncapped (the historical behaviour).
    spend_budget: Any = None


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


def anthropic_api_key(config: AgentConfig | None = None) -> str | None:
    """Explicit Anthropic key, if one was supplied.

    ``None`` is NOT an error: the Anthropic SDK also resolves
    ``ANTHROPIC_AUTH_TOKEN`` and `ant auth login` profiles on its own, so a
    zero-arg client can be perfectly well authenticated with no key in the
    environment. Callers should pass whatever this returns straight through
    and let the SDK do its own resolution rather than refusing to start.
    """
    if config is not None and config.llm_api_key:
        return config.llm_api_key
    return os.environ.get("ANTHROPIC_API_KEY")


def apply_anthropic_provider(
    config: AgentConfig,
    *,
    model: str | None = None,
    thinking: str | None = None,
    reasoning_effort: str | None = None,
) -> AgentConfig:
    """Point chat completions at the Anthropic API. Embeddings stay on LM Studio.

    Defaults to adaptive thinking at `high` effort, which is the recommended
    configuration for Claude Opus 5 -- the opposite of the DeepSeek default
    (thinking off), because Claude's adaptive mode already scales thinking
    depth to the difficulty of the step instead of spending a fixed budget on
    every one.
    """
    config.llm_provider = LLM_PROVIDER_ANTHROPIC
    config.llm_model = model or DEFAULT_ANTHROPIC_MODEL
    if thinking is not None:
        config.llm_thinking = thinking
    elif config.llm_thinking is None:
        config.llm_thinking = DEFAULT_ANTHROPIC_THINKING
    if reasoning_effort is not None:
        config.llm_reasoning_effort = reasoning_effort
    elif config.llm_reasoning_effort is None:
        config.llm_reasoning_effort = DEFAULT_ANTHROPIC_EFFORT
    return config


def validate_reasoning_effort(provider: str, effort: str | None) -> None:
    """Raise if `effort` is not one this provider accepts."""
    if effort is None:
        return
    allowed = REASONING_EFFORTS_BY_PROVIDER.get(provider)
    if allowed is None:
        raise ValueError(f"provider {provider!r} does not support reasoning effort")
    if effort not in allowed:
        raise ValueError(
            f"llm_reasoning_effort must be one of {', '.join(allowed)} for "
            f"provider {provider!r}, got {effort!r}"
        )


def validate_anthropic_thinking_effort(thinking: str, effort: str | None) -> None:
    """Reject the one Claude thinking/effort combination the API 400s on.

    Claude Opus 5 accepts explicitly-disabled thinking only at effort `high`
    or below. Catching it here turns a mid-run HTTP 400 (which would abort a
    trial after the harness has already spent EasyCrypt time) into an
    argument error at startup.
    """
    if thinking == "disabled" and effort in _ANTHROPIC_EFFORTS_REQUIRING_THINKING:
        raise ValueError(
            "Claude rejects thinking='disabled' at reasoning effort "
            f"{effort!r}; use effort high or below, or leave thinking adaptive"
        )


def is_paid_provider(config: AgentConfig) -> bool:
    return config.llm_provider in PAID_LLM_PROVIDERS


def chat_client_kwargs(config: AgentConfig) -> dict[str, Any]:
    """OpenAI-SDK kwargs for solver/writer chat completions.

    Only meaningful for the OpenAI-compatible providers (LM Studio, DeepSeek).
    Anthropic does not use the OpenAI SDK at all -- see
    ``integration/agent/llm.py::AnthropicBackend``.
    """
    if config.llm_provider == LLM_PROVIDER_ANTHROPIC:
        raise ValueError(
            "the Anthropic provider does not use the OpenAI SDK; "
            "AnthropicBackend builds its own client"
        )
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


def default_thinking_for_provider(provider: str) -> str:
    """Thinking mode a provider falls back to when none was configured."""
    if provider == LLM_PROVIDER_ANTHROPIC:
        return DEFAULT_ANTHROPIC_THINKING
    return DEFAULT_DEEPSEEK_THINKING


def configured_thinking(config: AgentConfig) -> str:
    """Configured thinking mode: ``enabled``, ``disabled``, or ``adaptive``."""
    return config.llm_thinking or default_thinking_for_provider(config.llm_provider)


def deepseek_thinking(config: AgentConfig) -> str:
    """Backwards-compatible alias for :func:`configured_thinking`."""
    return configured_thinking(config)


def resolve_thinking_for_step(
    config: AgentConfig,
    trajectory: list[dict[str, Any]] | None = None,
) -> str:
    """Resolve per-call thinking to ``enabled`` or ``disabled``.

    With ``llm_thinking='adaptive'``, enable thinking when any of the last
    ``thinking_failure_window`` trajectory steps has a failure-like outcome.

    NOTE this is the *harness's* adaptive mode, which is not the same thing as
    Claude's own adaptive thinking. For Anthropic we deliberately do NOT
    resolve `adaptive` here: Claude scales thinking depth per request on its
    own and does it better than a trajectory-window heuristic can, so the mode
    is passed through untouched and the backend sends `{"type": "adaptive"}`.
    """
    if config.llm_provider == LLM_PROVIDER_ANTHROPIC:
        return configured_thinking(config)
    mode = configured_thinking(config)
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

    Anthropic is the one provider where this is worth turning on: its
    structured-outputs ``output_config.format`` genuinely constrains the reply
    to the action schema (rather than merely to "some JSON"), so the JSON
    repair path in llm.py becomes a fallback instead of a routine necessity.
    It is still opt-in, to keep one switch controlling the behaviour on every
    provider.
    """
    if not config.llm_json_mode:
        return None
    if config.llm_provider == LLM_PROVIDER_ANTHROPIC:
        return "anthropic_json_schema"
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

    Anthropic returns ``{}``: its per-call parameters are not OpenAI
    ``extra_body`` keys and are built by
    :func:`anthropic_request_kwargs` instead.
    """
    if config.llm_provider != LLM_PROVIDER_DEEPSEEK:
        return {}

    resolved = thinking if thinking is not None else configured_thinking(config)
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


def anthropic_client_kwargs(config: AgentConfig) -> dict[str, Any]:
    """Constructor kwargs for ``anthropic.Anthropic``.

    ``api_key`` is passed only when we actually have one. Omitting it lets the
    SDK run its own credential resolution (``ANTHROPIC_API_KEY``, then
    ``ANTHROPIC_AUTH_TOKEN``, then an ``ant auth login`` profile), so a machine
    authenticated by profile rather than env var still works.
    """
    kwargs: dict[str, Any] = {"timeout": float(config.lm_studio_timeout)}
    key = anthropic_api_key(config)
    if key:
        kwargs["api_key"] = key
    return kwargs


def anthropic_request_kwargs(
    config: AgentConfig,
    *,
    thinking: str | None = None,
) -> dict[str, Any]:
    """Per-call Anthropic Messages API parameters.

    Deliberately omits ``temperature``/``top_p``/``top_k``: Claude Opus 5
    removed them and returns a 400 if any is sent. ``AgentConfig.llm_temperature``
    therefore has no effect on this provider, which is why prompt-level
    steering is the only knob here.
    """
    resolved = thinking if thinking is not None else configured_thinking(config)
    # The harness's own trajectory-window mode maps onto Claude's native
    # adaptive thinking: both mean "decide per step", Claude just does it with
    # more information than a failure counter has.
    if resolved == "enabled":
        resolved = "adaptive"

    effort = config.llm_reasoning_effort or DEFAULT_ANTHROPIC_EFFORT
    validate_reasoning_effort(LLM_PROVIDER_ANTHROPIC, effort)
    validate_anthropic_thinking_effort(resolved, effort)

    kwargs: dict[str, Any] = {"output_config": {"effort": effort}}
    if resolved == "adaptive":
        # `display: summarized` is what makes the reasoning readable in the
        # run log; the API default ("omitted") streams empty thinking blocks,
        # which would silently hollow out AgentRunLog's `thought` field.
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
    elif resolved == "disabled":
        kwargs["thinking"] = {"type": "disabled"}
    else:
        raise ValueError(
            "llm_thinking must be 'adaptive', 'enabled', or 'disabled' for "
            f"Anthropic, got {resolved!r}"
        )
    return kwargs

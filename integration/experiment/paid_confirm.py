"""Interactive confirmation before spending money on a hosted LLM provider.

Generalizes the original DeepSeek-only gate to every paid provider (see
``integration.agent.config.PAID_LLM_PROVIDERS``). The rule in
[`AGENTS.md`](../../AGENTS.md) is provider-independent and applies here in
full: **agents must never answer this prompt on the user's behalf** -- no
piping the phrase in, no pty tricks, no background run that "handles" it.
Hand the user the command and stop.

There is deliberately no ``--yes`` / ``--force`` flag for any provider.
"""

from __future__ import annotations

import sys
from typing import TextIO

from integration.agent.config import (
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_DEEPSEEK,
    AgentConfig,
    anthropic_api_key,
    configured_thinking,
    deepseek_base_url,
)
from integration.agent.pricing import lookup_anthropic_rates, lookup_model_rates

# Printed verbatim in the confirmation banner and in AGENTS.md. Agents must
# never type the confirmation string on the user's behalf.
CONFIRMATION_PHRASE = "YES"
AGENT_NEVER_CONFIRM_NOTICE = (
    "Agents / automated tools MUST NEVER answer this prompt for the user. "
    "Only a human may confirm paid API usage."
)

_PROVIDER_LABELS = {
    LLM_PROVIDER_DEEPSEEK: "DeepSeek",
    LLM_PROVIDER_ANTHROPIC: "Anthropic (Claude)",
}


def _provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


def _endpoint(config: AgentConfig) -> str:
    if config.llm_provider == LLM_PROVIDER_DEEPSEEK:
        return deepseek_base_url()
    if config.llm_provider == LLM_PROVIDER_ANTHROPIC:
        return "https://api.anthropic.com (or the configured ANTHROPIC_BASE_URL)"
    return "(unknown endpoint)"


def _credential_line(config: AgentConfig) -> str:
    if config.llm_provider != LLM_PROVIDER_ANTHROPIC:
        return ""
    if anthropic_api_key(config):
        return "Credentials              : ANTHROPIC_API_KEY (set)\n"
    # Not an error: the SDK also resolves ANTHROPIC_AUTH_TOKEN and
    # `ant auth login` profiles. Say so rather than implying the run will fail.
    return (
        "Credentials              : no ANTHROPIC_API_KEY in the environment; "
        "the SDK will\n"
        "                           try ANTHROPIC_AUTH_TOKEN and any "
        "`ant auth login` profile\n"
    )


def _rate_line(config: AgentConfig) -> str:
    """Published per-1M-token rates, so the banner shows the actual exposure."""
    model = config.llm_model
    rates = (
        lookup_anthropic_rates(model)
        if config.llm_provider == LLM_PROVIDER_ANTHROPIC
        else lookup_model_rates(model)
    )
    if rates is None:
        return "Rates                    : unknown for this model id\n"
    write = (
        f", cache write ${rates.cache_write:.2f}"
        if rates.cache_write is not None
        else ""
    )
    return (
        f"Rates (USD / 1M tokens)  : input ${rates.input_cache_miss:.4f}, "
        f"cached input ${rates.input_cache_hit:.4f}{write}, "
        f"output ${rates.output:.4f}\n"
    )


def format_paid_provider_warning(
    *,
    config: AgentConfig,
    trials: int,
    informal: bool,
) -> str:
    """Render the pre-spend banner for whichever paid provider is configured."""
    provider = config.llm_provider
    label = _provider_label(provider)
    model = config.llm_model or "(unset)"
    upper = trials * config.max_steps
    thinking = configured_thinking(config)

    if thinking == "adaptive" and provider == LLM_PROVIDER_ANTHROPIC:
        # Claude's own adaptive thinking, not the harness's failure-window mode.
        thinking_detail = "adaptive (Claude decides depth per request)"
        effort = config.llm_reasoning_effort or "(provider default)"
    elif thinking == "adaptive":
        thinking_detail = f"adaptive (window={config.thinking_failure_window})"
        effort = (
            config.llm_reasoning_effort
            or "(unset; used when adaptive enables thinking)"
        )
    elif thinking in {"enabled"}:
        thinking_detail = thinking
        effort = config.llm_reasoning_effort or "(unset)"
    else:
        thinking_detail = thinking
        effort = "(n/a; thinking disabled)"

    writer_note = (
        f"Informal writer LLM calls: up to {trials} additional paid calls "
        "(one per trial).\n"
        if informal
        else ""
    )
    # A configured cap is the single most decision-relevant line in this
    # banner, so it goes right next to the unbounded call count it bounds.
    budget = getattr(config, "spend_budget", None)
    budget_note = (
        f"SPEND CAP                : ${budget.limit_usd:.2f} USD "
        "(run stops when reached; may overshoot by one call)\n"
        if budget is not None
        else "Spend cap                : none (--max-spend-usd not set)\n"
    )
    return (
        "========================================================================\n"
        f"WARNING: This will call the {label} API and consume paid API credits.\n"
        "========================================================================\n"
        "\n"
        f"Provider                 : {label} ({_endpoint(config)})\n"
        f"Chat model               : {model}\n"
        f"{_rate_line(config)}"
        f"{_credential_line(config)}"
        f"Thinking                 : {thinking_detail}\n"
        f"Reasoning effort         : {effort}\n"
        f"Max output tokens        : {config.llm_max_tokens}\n"
        f"Trials (iterations)      : {trials}\n"
        f"Max agent steps / trial  : {config.max_steps}\n"
        f"Upper bound solver calls : {trials} × {config.max_steps} = {upper}\n"
        f"{budget_note}"
        f"{writer_note}"
        "\n"
        f"Embeddings still use the local LM Studio endpoint (not {label}):\n"
        f"  {config.lm_studio_base_url}\n"
        "\n"
        f"{AGENT_NEVER_CONFIRM_NOTICE}\n"
        "\n"
        f'Type {CONFIRMATION_PHRASE} (all caps) to proceed, '
        "or anything else to abort.\n"
    )


def confirm_paid_provider_usage(
    *,
    config: AgentConfig,
    trials: int,
    informal: bool = False,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> bool:
    """Prompt a human for explicit confirmation. Returns True only for YES."""
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    out_stream.write(
        format_paid_provider_warning(config=config, trials=trials, informal=informal)
    )
    out_stream.flush()
    try:
        answer = in_stream.readline()
    except KeyboardInterrupt:
        out_stream.write("\nAborted.\n")
        return False
    if answer is None:
        return False
    return answer.strip() == CONFIRMATION_PHRASE

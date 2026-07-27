"""Interactive confirmation before spending DeepSeek API credits."""

from __future__ import annotations

import sys
from typing import TextIO

from integration.agent.config import AgentConfig, deepseek_base_url

# Printed verbatim in the confirmation banner and in AGENTS.md. Agents must
# never type the confirmation string on the user's behalf.
CONFIRMATION_PHRASE = "YES"
AGENT_NEVER_CONFIRM_NOTICE = (
    "Agents / automated tools MUST NEVER answer this prompt for the user. "
    "Only a human may confirm DeepSeek API usage."
)


def format_deepseek_warning(
    *,
    config: AgentConfig,
    trials: int,
    informal: bool,
) -> str:
    model = config.llm_model or "(unset)"
    upper = trials * config.max_steps
    thinking = config.llm_thinking or "disabled"
    if thinking == "adaptive":
        effort = (
            config.llm_reasoning_effort
            or "(unset; used when adaptive enables thinking)"
        )
        thinking_detail = (
            f"adaptive (window={config.thinking_failure_window})"
        )
    elif thinking == "enabled":
        effort = config.llm_reasoning_effort or "(unset)"
        thinking_detail = thinking
    else:
        effort = "(n/a; thinking disabled)"
        thinking_detail = thinking
    writer_note = (
        f"Informal writer LLM calls: up to {trials} additional paid calls "
        "(one per trial).\n"
        if informal
        else ""
    )
    return (
        "========================================================================\n"
        "WARNING: This will call the DeepSeek API and consume paid API credits.\n"
        "========================================================================\n"
        "\n"
        f"Provider                 : DeepSeek ({deepseek_base_url()})\n"
        f"Chat model               : {model}\n"
        f"Thinking                 : {thinking_detail}\n"
        f"Reasoning effort         : {effort}\n"
        f"Max output tokens        : {config.llm_max_tokens}\n"
        f"Trials (iterations)      : {trials}\n"
        f"Max agent steps / trial  : {config.max_steps}\n"
        f"Upper bound solver calls : {trials} × {config.max_steps} = {upper}\n"
        f"{writer_note}"
        "\n"
        "Embeddings still use the local LM Studio endpoint (not DeepSeek):\n"
        f"  {config.lm_studio_base_url}\n"
        "\n"
        f"{AGENT_NEVER_CONFIRM_NOTICE}\n"
        "\n"
        f'Type {CONFIRMATION_PHRASE} (all caps) to proceed, '
        "or anything else to abort.\n"
    )


def confirm_deepseek_usage(
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
        format_deepseek_warning(config=config, trials=trials, informal=informal)
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

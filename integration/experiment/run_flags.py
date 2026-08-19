"""Persist CLI flags for an experiment run folder."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from integration.agent.config import AgentConfig, action_response_format_mode

RUN_FLAGS_FILENAME = "run_flags.json"


def namespace_to_jsonable(args: Namespace) -> dict[str, Any]:
    """Convert an argparse namespace into a JSON-serializable dict."""
    payload: dict[str, Any] = {}
    for key, value in vars(args).items():
        if isinstance(value, Path):
            payload[key] = str(value)
        else:
            payload[key] = value
    return payload


def build_run_flags_payload(
    *,
    args: Namespace,
    argv: list[str] | None,
    agent: AgentConfig,
    output_dir: Path,
) -> dict[str, Any]:
    """Assemble the run_flags.json document."""
    return {
        "argv": list(argv) if argv is not None else None,
        "flags": namespace_to_jsonable(args),
        "resolved": {
            "output_dir": str(output_dir),
            "llm_provider": agent.llm_provider,
            "llm_model": agent.llm_model,
            "embed_model": agent.embed_model,
            "lm_studio_base_url": agent.lm_studio_base_url,
            "llm_json_mode": agent.llm_json_mode,
            "action_response_format": action_response_format_mode(agent),
            "llm_max_tokens": agent.llm_max_tokens,
            "llm_thinking": agent.llm_thinking,
            "llm_reasoning_effort": agent.llm_reasoning_effort,
            "thinking_failure_window": agent.thinking_failure_window,
            "top_k": agent.top_k,
            "lemma_search_top_k": agent.lemma_search_top_k,
            "max_steps": agent.max_steps,
            "max_premises": agent.max_premises,
            "easycrypt_bin": str(agent.easycrypt_bin),
        },
    }


def write_run_flags(
    output_dir: Path,
    *,
    args: Namespace,
    argv: list[str] | None,
    agent: AgentConfig,
) -> Path:
    """Write run_flags.json into the experiment output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / RUN_FLAGS_FILENAME
    payload = build_run_flags_payload(
        args=args,
        argv=argv,
        agent=agent,
        output_dir=output_dir,
    )
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path

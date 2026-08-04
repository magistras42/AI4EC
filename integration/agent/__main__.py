"""CLI entry point for the EasyCrypt agent loop."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import (
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_DEEPSEEK,
    LLM_PROVIDER_LM_STUDIO,
    PAID_LLM_PROVIDERS,
    AgentConfig,
    apply_anthropic_provider,
    apply_deepseek_provider,
)
from .loop import ExitReason, run_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EasyCrypt LLM proof agent")
    parser.add_argument("file", type=Path, help="Path to the .ec file")
    parser.add_argument("--top-k", type=int, default=10, help="Premises to include")
    parser.add_argument("--max-steps", type=int, default=200, help="Max agent iterations")
    parser.add_argument(
        "--max-premises",
        type=int,
        default=None,
        help="Cap premises for debugging",
    )
    parser.add_argument("--promote", action="store_true", help="Overwrite original on success")
    parser.add_argument("--work-copy", type=Path, default=None, help="Working copy path")
    parser.add_argument("--easycrypt", type=Path, default=None, help="Path to easycrypt binary")
    parser.add_argument(
        "--provider",
        choices=(LLM_PROVIDER_LM_STUDIO, LLM_PROVIDER_DEEPSEEK, LLM_PROVIDER_ANTHROPIC),
        default=LLM_PROVIDER_LM_STUDIO,
        help=(
            "Chat provider (default: lm_studio). deepseek and anthropic are "
            "paid hosted APIs. Embeddings always use LM Studio."
        ),
    )
    parser.add_argument(
        "--thinking",
        choices=("enabled", "disabled", "adaptive"),
        default=None,
        help="Thinking mode for hosted providers (ignored for lm_studio)",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=None,
        help=(
            "Reasoning effort for hosted providers. DeepSeek accepts high|max; "
            "Anthropic accepts the full ladder (default high)."
        ),
    )
    parser.add_argument(
        "--import-repair",
        action="store_true",
        help=(
            "Before proving, attempt a verified, line-preserving import/syntax "
            "repair when the file does not load (proof_corpus/ec_migrations.toml)"
        ),
    )
    parser.add_argument(
        "--source-ec-version",
        default=None,
        help="Corpus EasyCrypt version for --import-repair (default: auto-detect)",
    )
    parser.add_argument(
        "--target-ec-version",
        default=None,
        help="Target EasyCrypt version for --import-repair (default: auto-detect)",
    )
    parser.add_argument("--llm-model", default=None, help="Chat model id for the provider")
    parser.add_argument("--embed-model", default=None, help="LM Studio embedding model id")
    parser.add_argument(
        "--lm-studio-url",
        default=None,
        help="LM Studio base URL (default http://localhost:1234/v1)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Write a structured JSON run log to this path",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    config = AgentConfig(
        top_k=args.top_k,
        max_steps=args.max_steps,
        max_premises=args.max_premises,
        promote_on_success=args.promote,
        log_file=args.log_file,
    )
    if args.easycrypt:
        config.easycrypt_bin = args.easycrypt
    if args.embed_model:
        config.embed_model = args.embed_model
    if args.lm_studio_url:
        config.lm_studio_base_url = args.lm_studio_url

    if args.provider == LLM_PROVIDER_DEEPSEEK:
        apply_deepseek_provider(
            config,
            model=args.llm_model,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
        )
    elif args.provider == LLM_PROVIDER_ANTHROPIC:
        apply_anthropic_provider(
            config,
            model=args.llm_model,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
        )
    elif args.llm_model:
        config.llm_model = args.llm_model

    if args.provider in PAID_LLM_PROVIDERS:
        # Single-file runs are bounded by --max-steps rather than by trials,
        # but they still spend money, so the same human-only gate applies.
        # See AGENTS.md: an agent must never answer this on the user's behalf.
        from integration.experiment.paid_confirm import confirm_paid_provider_usage

        if not confirm_paid_provider_usage(config=config, trials=1):
            print(
                f"Aborted: {args.provider} API usage was not confirmed.",
                file=sys.stderr,
            )
            return 5

    config.import_repair = args.import_repair
    config.source_ec_version = args.source_ec_version
    config.target_ec_version = args.target_ec_version

    result = run_agent(args.file, config, work_copy=args.work_copy)
    print(result.message)
    if result.work_copy:
        print(f"Working copy: {result.work_copy}")
    if args.log_file:
        print(f"Run log: {args.log_file}")
    if result.steps:
        print(f"Steps: {result.steps}")

    if result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE):
        return 0
    if result.reason == ExitReason.STARTUP_ERROR:
        return 1
    if result.reason == ExitReason.MAX_STEPS:
        return 2
    if result.reason == ExitReason.STUCK:
        return 4
    return 3


if __name__ == "__main__":
    sys.exit(main())

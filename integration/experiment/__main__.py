"""CLI for mutation repair experiments."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

from integration.agent.config import (
    AgentConfig,
    apply_deepseek_provider,
    deepseek_api_key,
)
from integration.experiment.config import ExperimentConfig
from integration.experiment.corpora.elgamal import ElGamalCorpus
from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.deepseek_confirm import (
    AGENT_NEVER_CONFIRM_NOTICE,
    confirm_deepseek_usage,
)
from integration.experiment.protocols import ExperimentSpec
from integration.experiment.run_flags import write_run_flags
from integration.experiment.runner import run_experiment
from integration.experiment.specs import SPECS, register_default_specs


def _build_spec(name: str, data_dir: Path) -> ExperimentSpec:
    register_default_specs(data_dir)
    if name in list(SPECS.names()):
        spec = SPECS.get(name)
        if isinstance(spec.corpus, JoyCorpus):
            spec = ExperimentSpec(
                name=spec.name,
                corpus=JoyCorpus(data_dir=data_dir),
                mutations=spec.mutations,
                informal=spec.informal,
                broken_formal=spec.broken_formal,
            )
        elif isinstance(spec.corpus, ElGamalCorpus):
            spec = ExperimentSpec(
                name=spec.name,
                corpus=ElGamalCorpus(data_dir=data_dir),
                mutations=spec.mutations,
                informal=spec.informal,
                broken_formal=spec.broken_formal,
            )
        return spec
    raise KeyError(name)


def _with_sandbox_dir(spec: ExperimentSpec, data_dir: Path, sandbox_dir: Path) -> ExperimentSpec:
    if isinstance(spec.corpus, JoyCorpus):
        return ExperimentSpec(
            name=spec.name,
            corpus=JoyCorpus(data_dir=data_dir, sandbox_dir=sandbox_dir),
            mutations=spec.mutations,
            informal=spec.informal,
            broken_formal=spec.broken_formal,
        )
    if isinstance(spec.corpus, ElGamalCorpus):
        return ExperimentSpec(
            name=spec.name,
            corpus=ElGamalCorpus(data_dir=data_dir, sandbox_dir=sandbox_dir),
            mutations=spec.mutations,
            informal=spec.informal,
            broken_formal=spec.broken_formal,
        )
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EasyCrypt mutation repair experiment")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run an experiment spec")
    run_p.add_argument(
        "--spec",
        default="joy-tactic-repair",
        help="Experiment spec name (default: joy-tactic-repair)",
    )
    run_p.add_argument("--trials", type=int, default=10, help="Number of trials")
    run_p.add_argument(
        "--stuck-limit",
        type=int,
        default=20,
        help="Stuck iteration limit per trial",
    )
    run_p.add_argument("--max-steps", type=int, default=200, help="Max agent steps per trial")
    run_p.add_argument("--seed", type=int, default=None, help="Random seed")
    run_p.add_argument("--data-dir", type=Path, default=Path("data"), help="Corpus data dir")
    run_p.add_argument("--output-dir", type=Path, default=None, help="Experiment output dir")
    run_p.add_argument("--easycrypt", type=Path, default=None, help="Path to easycrypt binary")
    run_p.add_argument(
        "--llm-model",
        default=None,
        help="Chat model id (LM Studio id, or DeepSeek id with --deepseek)",
    )
    run_p.add_argument("--embed-model", default=None, help="LM Studio embedding model id")
    run_p.add_argument(
        "--lm-studio-url",
        default=None,
        help="LM Studio base URL (embeddings always use this endpoint)",
    )
    run_p.add_argument(
        "--deepseek",
        action="store_true",
        help=(
            "Route solver/writer chat completions through the DeepSeek API "
            "(requires DEEPSEEK_API_KEY). Prompts for human confirmation before "
            "any paid calls. "
            + AGENT_NEVER_CONFIRM_NOTICE
        ),
    )
    run_p.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        help="Max output tokens per chat completion (default: 16384)",
    )
    run_p.add_argument(
        "--thinking",
        choices=("enabled", "disabled", "adaptive"),
        default=None,
        help=(
            "DeepSeek V4 thinking mode. Default with --deepseek: disabled "
            "(cheaper; avoids burning the token budget on hidden CoT). "
            "Use enabled for harder proofs, or adaptive to enable thinking "
            "only after recent failure-like steps (see "
            "--thinking-failure-window)."
        ),
    )
    run_p.add_argument(
        "--thinking-failure-window",
        type=int,
        default=None,
        help=(
            "With --thinking adaptive: enable thinking if any of the last N "
            "trajectory steps failed/rejected/search-limited/format-errored "
            "(default: 5)"
        ),
    )
    run_p.add_argument(
        "--reasoning-effort",
        choices=("high", "max"),
        default=None,
        help=(
            "DeepSeek reasoning effort when thinking is enabled "
            "(official values: high, max). Also applies when adaptive "
            "thinking turns thinking on. Ignored when thinking stays disabled."
        ),
    )
    run_p.add_argument("--top-k", type=int, default=10, help="Premises to include")
    run_p.add_argument(
        "--lemma-search-top-k",
        type=int,
        default=5,
        help="Semantic lemma-search results to return",
    )
    run_p.add_argument(
        "--max-premises",
        type=int,
        default=None,
        help="Cap premises for debugging",
    )
    run_p.add_argument(
        "--red-herring-ratio",
        type=float,
        default=None,
        help=(
            "Fraction of used-lemma count to add as red herrings "
            "(joy-informal-repair spec only; default 0.3)"
        ),
    )
    run_p.add_argument(
        "--writer-temperature",
        type=float,
        default=None,
        help=(
            "Sampling temperature for the informal-proof writer LLM "
            "(joy-informal-repair spec only; default 0.7)"
        ),
    )
    run_p.add_argument(
        "--llm-json-mode",
        action="store_true",
        help=(
            "Opt in to structured JSON output (response_format) from the solver "
            "LLM. Off by default: DeepSeek json_object does not enforce the "
            "action schema, and many local models reject json_schema."
        ),
    )
    run_p.add_argument(
        "--no-llm-json-mode",
        action="store_true",
        help=(
            "Explicitly disable response_format (default behavior; kept for "
            "clarity in scripts / run_flags)."
        ),
    )
    run_p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.command != "run":
        return 1

    agent = AgentConfig(
        top_k=args.top_k,
        lemma_search_top_k=args.lemma_search_top_k,
        max_steps=args.max_steps,
        max_premises=args.max_premises,
    )
    if args.easycrypt:
        agent.easycrypt_bin = args.easycrypt
    if args.embed_model:
        agent.embed_model = args.embed_model
    if args.lm_studio_url:
        agent.lm_studio_base_url = args.lm_studio_url
    if args.llm_json_mode and args.no_llm_json_mode:
        print(
            "error: --llm-json-mode and --no-llm-json-mode are mutually exclusive",
            file=sys.stderr,
        )
        return 1
    if args.llm_json_mode:
        agent.llm_json_mode = True
    elif args.no_llm_json_mode:
        agent.llm_json_mode = False
    if args.llm_max_tokens is not None:
        agent.llm_max_tokens = args.llm_max_tokens

    if args.deepseek:
        if not deepseek_api_key(agent):
            print(
                "error: --deepseek requires DEEPSEEK_API_KEY in the environment",
                file=sys.stderr,
            )
            return 1
        apply_deepseek_provider(
            agent,
            model=args.llm_model,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
        )
        if args.thinking_failure_window is not None:
            agent.thinking_failure_window = args.thinking_failure_window
        if agent.llm_thinking == "disabled" and args.reasoning_effort is not None:
            print(
                "warning: --reasoning-effort is ignored when thinking is disabled",
                file=sys.stderr,
            )
    elif args.llm_model:
        agent.llm_model = args.llm_model
    elif (
        args.thinking is not None
        or args.reasoning_effort is not None
        or args.thinking_failure_window is not None
    ):
        print(
            "error: --thinking / --reasoning-effort / "
            "--thinking-failure-window require --deepseek",
            file=sys.stderr,
        )
        return 1

    exp_config = ExperimentConfig(
        spec_name=args.spec,
        trials=args.trials,
        stuck_limit=args.stuck_limit,
        seed=args.seed,
        data_dir=args.data_dir,
        agent=agent,
    )
    if args.output_dir is not None:
        exp_config.output_dir = args.output_dir

    spec = _build_spec(args.spec, args.data_dir)
    exp_config.output_dir.mkdir(parents=True, exist_ok=True)
    spec = _with_sandbox_dir(spec, args.data_dir, exp_config.output_dir / "sandboxes")

    if spec.informal is not None:
        overrides = {}
        if args.red_herring_ratio is not None:
            overrides["red_herring_ratio"] = args.red_herring_ratio
        if args.writer_temperature is not None:
            overrides["writer_temperature"] = args.writer_temperature
        if overrides:
            spec = ExperimentSpec(
                name=spec.name,
                corpus=spec.corpus,
                mutations=spec.mutations,
                informal=dc_replace(spec.informal, **overrides),
            )

    if args.deepseek:
        confirmed = confirm_deepseek_usage(
            config=agent,
            trials=args.trials,
            informal=spec.informal is not None,
        )
        if not confirmed:
            print("Aborted: DeepSeek API usage was not confirmed.", file=sys.stderr)
            return 2

    # Record the invocation after confirmation so aborted DeepSeek prompts do
    # not leave a flags file that looks like a started run.
    write_run_flags(
        exp_config.output_dir,
        args=args,
        argv=argv if argv is not None else sys.argv[1:],
        agent=agent,
    )

    result = run_experiment(spec, exp_config)

    print(f"Spec: {result.spec_name}")
    print(f"Mode: {result.mode}")
    print(f"Trials: {result.trials_run} run, {result.trials_skipped} skipped")
    print(f"Successes: {result.successes}, stuck: {result.stuck}, max_steps: {result.max_steps}")
    print(f"Errors: {result.errors}")
    usage = result.token_usage
    print(
        f"Tokens: {usage.prompt_tokens} in "
        f"({usage.cached_prompt_tokens} cache hit, "
        f"{usage.cache_miss_prompt_tokens} cache miss), "
        f"{usage.completion_tokens} out "
        f"({usage.total_tokens} total over {usage.calls} chat calls)"
    )
    per_trial = result.token_usage_per_trial
    if per_trial:
        print(
            "Per trial: "
            f"{per_trial['prompt_tokens_per_trial']} in, "
            f"{per_trial['completion_tokens_per_trial']} out, "
            f"{per_trial['calls_per_trial']} calls"
        )
    if result.estimated_cost is not None:
        cost = result.estimated_cost
        per = per_trial.get("estimated_cost_usd_per_trial") if per_trial else None
        print(
            f"Estimated cost: ${cost['usd']:.6f} USD "
            f"({cost['model']}"
            + (f"; ${per:.6f}/trial" if per is not None else "")
            + ")"
        )
    if result.output_dir:
        print(f"Output: {result.output_dir}")

    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

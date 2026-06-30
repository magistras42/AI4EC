"""CLI for mutation repair experiments."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from integration.agent.config import AgentConfig
from integration.experiment.config import ExperimentConfig
from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.mutations.tactics import TacticMutationSet
from integration.experiment.protocols import ExperimentSpec
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
            )
        return spec
    raise KeyError(name)


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
    run_p.add_argument("--llm-model", default=None, help="LM Studio LLM model id")
    run_p.add_argument("--embed-model", default=None, help="LM Studio embedding model id")
    run_p.add_argument(
        "--lm-studio-url",
        default=None,
        help="LM Studio base URL",
    )
    run_p.add_argument("--top-k", type=int, default=10, help="Premises to include")
    run_p.add_argument(
        "--max-premises",
        type=int,
        default=None,
        help="Cap premises for debugging",
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
        max_steps=args.max_steps,
        max_premises=args.max_premises,
    )
    if args.easycrypt:
        agent.easycrypt_bin = args.easycrypt
    if args.llm_model:
        agent.llm_model = args.llm_model
    if args.embed_model:
        agent.embed_model = args.embed_model
    if args.lm_studio_url:
        agent.lm_studio_base_url = args.lm_studio_url

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
    if isinstance(spec.corpus, JoyCorpus):
        spec = ExperimentSpec(
            name=spec.name,
            corpus=JoyCorpus(
                data_dir=args.data_dir,
                sandbox_dir=exp_config.output_dir / "sandboxes",
            ),
            mutations=spec.mutations,
        )
    result = run_experiment(spec, exp_config)

    print(f"Spec: {result.spec_name}")
    print(f"Trials: {result.trials_run} run, {result.trials_skipped} skipped")
    print(f"Successes: {result.successes}, stuck: {result.stuck}, max_steps: {result.max_steps}")
    print(f"Errors: {result.errors}")
    if result.output_dir:
        print(f"Output: {result.output_dir}")

    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

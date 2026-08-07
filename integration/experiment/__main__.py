"""CLI for mutation repair experiments."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

from integration.agent.budget import BudgetUnavailable, SpendBudget
from integration.agent.config import (
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_DEEPSEEK,
    LLM_PROVIDER_LM_STUDIO,
    PAID_LLM_PROVIDERS,
    REASONING_EFFORTS_BY_PROVIDER,
    AgentConfig,
    apply_anthropic_provider,
    apply_deepseek_provider,
    configured_thinking,
    deepseek_api_key,
    validate_anthropic_thinking_effort,
    validate_reasoning_effort,
)
from integration.experiment.config import ExperimentConfig
from integration.experiment.corpora.elgamal import ElGamalCorpus
from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.corpora.lq1 import LQ1Corpus
from integration.experiment.paid_confirm import (
    AGENT_NEVER_CONFIRM_NOTICE,
    confirm_paid_provider_usage,
)
from integration.experiment.protocols import ExperimentSpec
from integration.experiment.run_flags import write_run_flags
from integration.experiment.runner import run_experiment
from integration.experiment.specs import SPECS, register_default_specs


def _embeddings_endpoint_status(agent: AgentConfig) -> tuple[bool, str]:
    """Is the LM Studio embeddings endpoint reachable, and does it serve a model?

    Returns ``(ok, detail)``. Only a connectivity probe -- it deliberately does
    not embed anything, so it costs nothing and cannot be confused for real
    work. A server that answers but exposes no embedding model is reported as
    a distinct failure, because that is the confusing case: the URL is right
    and the run would still die later.
    """
    try:
        from openai import OpenAI

        client = OpenAI(
            base_url=agent.lm_studio_base_url,
            api_key=agent.llm_api_key or "lm-studio",
            timeout=10,
        )
        models = client.models.list()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"

    available = [m.id for m in (models.data or [])]
    if not available:
        return False, "endpoint responded but serves no models"
    if agent.embed_model and agent.embed_model not in available:
        return False, (
            f"requested --embed-model {agent.embed_model!r} is not loaded "
            f"(available: {', '.join(available[:5])})"
        )
    if not agent.embed_model and not any("embed" in m.lower() for m in available):
        return False, (
            "no model with 'embed' in its id is loaded, and --embed-model was "
            f"not given (available: {', '.join(available[:5])})"
        )
    return True, f"{len(available)} model(s) available"


def _rebuilt_corpus(corpus, data_dir: Path, sandbox_dir: Path | None):
    """Rebuild a corpus provider against the CLI's --data-dir / sandbox path.

    Returns the original object for corpus types that take neither, so an
    unknown provider passes through rather than being silently dropped.
    """
    kwargs = {"data_dir": data_dir}
    if sandbox_dir is not None:
        kwargs["sandbox_dir"] = sandbox_dir
    if isinstance(corpus, JoyCorpus):
        return JoyCorpus(**kwargs)
    if isinstance(corpus, LQ1Corpus):
        return LQ1Corpus(**kwargs)
    if isinstance(corpus, ElGamalCorpus):
        return ElGamalCorpus(**kwargs)
    return corpus


def _build_spec(name: str, data_dir: Path) -> ExperimentSpec:
    register_default_specs(data_dir)
    if name not in list(SPECS.names()):
        raise KeyError(name)
    spec = SPECS.get(name)
    # dataclasses.replace, NOT a field-by-field ExperimentSpec(...) rebuild.
    # The enumerated form silently dropped `replay_bootstrap` when that mode
    # was added (docs/PROOF_REPAIR_HANDOFF.md 6.2): every CLI-launched
    # `--spec elgamal-changelog-repair` run fell through to the mutation path
    # with no mode config at all, so nothing ever populated
    # AgentConfig.changelog_hints and the entire proof_corpus knowledge base
    # was unreachable from any experiment. `replace` copies every field by
    # construction, so a fifth mode cannot be lost the same way.
    return dc_replace(spec, corpus=_rebuilt_corpus(spec.corpus, data_dir, None))


def _with_sandbox_dir(spec: ExperimentSpec, data_dir: Path, sandbox_dir: Path) -> ExperimentSpec:
    # Same reasoning as _build_spec: only the corpus is rebuilt; every other
    # field rides along untouched.
    return dc_replace(
        spec, corpus=_rebuilt_corpus(spec.corpus, data_dir, sandbox_dir)
    )


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
        "--provider",
        choices=(LLM_PROVIDER_LM_STUDIO, LLM_PROVIDER_DEEPSEEK, LLM_PROVIDER_ANTHROPIC),
        default=None,
        help=(
            "Chat provider for the solver/writer LLM. "
            f"{LLM_PROVIDER_LM_STUDIO} (default) runs a local OpenAI-compatible "
            f"server such as LM Studio; {LLM_PROVIDER_DEEPSEEK} and "
            f"{LLM_PROVIDER_ANTHROPIC} are paid hosted APIs and prompt for "
            "human confirmation before any billable call. Embeddings always "
            "stay on LM Studio. " + AGENT_NEVER_CONFIRM_NOTICE
        ),
    )
    run_p.add_argument(
        "--deepseek",
        action="store_true",
        help=(
            "Deprecated alias for --provider deepseek "
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
        choices=("low", "medium", "high", "xhigh", "max"),
        default=None,
        help=(
            "Reasoning effort. Accepted values differ by provider and are "
            "validated after parsing: DeepSeek takes high|max (and only when "
            "thinking is enabled); Anthropic takes the full "
            "low|medium|high|xhigh|max ladder and applies it to adaptive "
            "thinking. Default for Anthropic: high."
        ),
    )
    run_p.add_argument(
        "--max-spend-usd",
        type=float,
        default=None,
        help=(
            "Stop the run once estimated spend reaches this many USD "
            "(paid providers only). Checked before each chat call, so actual "
            "spend can overshoot by at most one call. Refused when the model "
            "has no published rates, rather than silently not enforcing."
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
    run_p.add_argument(
        "--no-changelog-hints",
        action="store_true",
        help=(
            "replay_bootstrap only. Run WITHOUT the changelog/repair_doc "
            "knowledge base -- the hints-off arm of the paired A/B that shows "
            "whether it helps. `hint_uptake` in summary.json is only a proxy "
            "for that; this is the counterfactual. Import repair still runs "
            "(it edits the file, not the prompt). Pair with several seeds per "
            "arm: run-to-run variance under identical configuration has been "
            "measured at 11-vs-1 accepted tactics."
        ),
    )
    run_p.add_argument(
        "--version-hop",
        action="store_true",
        help=(
            "replay_bootstrap only (W7). When a tactic fails, re-check it "
            "against each release's OWN EasyCrypt binary to find which release "
            "broke it, and scope the changelog to that one transition instead "
            "of the whole source..target span. BUILDS EASYCRYPT: the first run "
            "provisions an opam switch and a full OCaml build per release "
            "probed (minutes and hundreds of MB each), cached in "
            "integration/extern/.ec_versions/ thereafter. Pre-build with "
            "`python3 -m integration.experiment.ec_versions --version rYYYY.MM`."
        ),
    )
    run_p.add_argument(
        "--version-hop-strategy",
        choices=("bisect", "linear"),
        default="bisect",
        help=(
            "bisect (default) assumes a tactic breaks once and stays broken, "
            "costing ~4 builds over the 14-release catalog; linear drops that "
            "assumption and costs up to 14."
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

    # --deepseek is the historical spelling of --provider deepseek. Honour both,
    # but refuse a request that names two different providers rather than
    # silently picking one and billing the wrong API.
    if args.deepseek and args.provider not in (None, LLM_PROVIDER_DEEPSEEK):
        print(
            f"error: --deepseek conflicts with --provider {args.provider}",
            file=sys.stderr,
        )
        return 1
    provider = args.provider or (
        LLM_PROVIDER_DEEPSEEK if args.deepseek else LLM_PROVIDER_LM_STUDIO
    )

    if provider == LLM_PROVIDER_DEEPSEEK:
        if not deepseek_api_key(agent):
            print(
                "error: --provider deepseek requires DEEPSEEK_API_KEY in the "
                "environment",
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
    elif provider == LLM_PROVIDER_ANTHROPIC:
        # No API-key precondition on purpose: the Anthropic SDK also resolves
        # ANTHROPIC_AUTH_TOKEN and `ant auth login` profiles, so demanding an
        # env var here would refuse to start on a correctly-authenticated box.
        # A genuinely missing credential surfaces as an auth error on the
        # first call, after the human has confirmed the spend.
        apply_anthropic_provider(
            agent,
            model=args.llm_model,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
        )
        if args.thinking_failure_window is not None:
            agent.thinking_failure_window = args.thinking_failure_window
    else:
        if args.llm_model:
            agent.llm_model = args.llm_model
        if (
            args.thinking is not None
            or args.reasoning_effort is not None
            or args.thinking_failure_window is not None
        ):
            print(
                "error: --thinking / --reasoning-effort / "
                "--thinking-failure-window require a hosted provider "
                "(--provider deepseek or --provider anthropic)",
                file=sys.stderr,
            )
            return 1

    if args.max_spend_usd is not None:
        if provider not in PAID_LLM_PROVIDERS:
            print(
                f"error: --max-spend-usd is meaningless for provider {provider} "
                "(local models are free); omit it",
                file=sys.stderr,
            )
            return 1
        try:
            agent.spend_budget = SpendBudget(
                limit_usd=args.max_spend_usd,
                provider=provider,
                model=agent.llm_model,
            )
        except BudgetUnavailable as exc:
            # Refusing beats pretending: a cap that cannot be priced would
            # otherwise let the run proceed uncapped while the user believed
            # it was bounded.
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # Validate provider-specific combinations up front: a bad effort level or
    # a disabled-thinking/high-effort pairing is a 400 mid-trial otherwise,
    # after EasyCrypt time has already been spent.
    if provider in PAID_LLM_PROVIDERS:
        try:
            if agent.llm_reasoning_effort is not None:
                validate_reasoning_effort(provider, agent.llm_reasoning_effort)
            if provider == LLM_PROVIDER_ANTHROPIC:
                validate_anthropic_thinking_effort(
                    configured_thinking(agent), agent.llm_reasoning_effort
                )
        except ValueError as exc:
            allowed = REASONING_EFFORTS_BY_PROVIDER.get(provider, ())
            print(
                f"error: {exc}"
                + (f" (allowed: {', '.join(allowed)})" if allowed else ""),
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
            # dc_replace for the same reason as _build_spec: the enumerated
            # form here dropped broken_formal and replay_bootstrap.
            spec = dc_replace(
                spec, informal=dc_replace(spec.informal, **overrides)
            )

    replay_overrides: dict[str, object] = {}
    if args.version_hop:
        replay_overrides["version_hop"] = True
        replay_overrides["version_hop_strategy"] = args.version_hop_strategy
    if args.no_changelog_hints:
        replay_overrides["changelog_hints"] = False
    if replay_overrides:
        if spec.replay_bootstrap is None:
            flags = ", ".join(f"--{k.replace('_', '-')}" for k in replay_overrides)
            print(
                f"error: {flags} apply to replay_bootstrap specs; "
                f"{args.spec!r} is not one",
                file=sys.stderr,
            )
            return 2
        spec = dc_replace(
            spec,
            replay_bootstrap=dc_replace(spec.replay_bootstrap, **replay_overrides),
        )

    if provider in PAID_LLM_PROVIDERS:
        # Embeddings always run on LM Studio, whatever the chat provider is.
        # Check that endpoint BEFORE asking anyone to authorize spend: an
        # unreachable embedder fails in _build_premise_index, i.e. after the
        # human has confirmed and after EasyCrypt has already done work. Being
        # asked to approve money for a run that cannot start is the worst
        # possible ordering.
        reachable, detail = _embeddings_endpoint_status(agent)
        if not reachable:
            print(
                f"error: embeddings endpoint unreachable at {agent.lm_studio_base_url}\n"
                f"       {detail}\n"
                "       Embeddings always use LM Studio, even with "
                f"--provider {provider} (Anthropic has no embeddings API).\n"
                "       Start LM Studio with an embedding model loaded, or point\n"
                "       --lm-studio-url at another OpenAI-compatible endpoint.\n"
                "       No paid call was made.",
                file=sys.stderr,
            )
            return 1

        confirmed = confirm_paid_provider_usage(
            config=agent,
            trials=args.trials,
            informal=spec.informal is not None,
        )
        if not confirmed:
            print(
                f"Aborted: {provider} API usage was not confirmed.",
                file=sys.stderr,
            )
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

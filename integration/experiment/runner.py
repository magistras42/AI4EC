"""Orchestrate mutation repair experiment trials."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from integration.agent import run_agent
from integration.agent.config import AgentConfig
from integration.agent.easycrypt import fetch_goal_and_premises, has_open_goals
from integration.agent.loop import ExitReason
from integration.agent.pricing import estimate_usage_cost
from integration.agent.usage import TokenUsage, average_usage
from integration.experiment.config import ExperimentConfig
from integration.experiment.informal import (
    InformalWriterError,
    build_labeled_manifest,
    build_lemma_manifest,
    extract_used_lemma_names,
    fetch_premises_at_cursor,
    looks_contaminated,
    name_boundary_pattern,
    select_red_herrings,
    write_informal_proof,
)
from integration.experiment.proof_extract import (
    apply_lines,
    format_hint,
    strip_tactics,
)
from integration.experiment.protocols import ExperimentSpec, ProofCase
from integration.experiment.repair_metrics import aggregate_repair_metrics
from integration.experiment.verify import is_proof_complete, is_proof_incomplete

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cost_for_usage(usage: TokenUsage, agent: AgentConfig) -> dict | None:
    estimate = estimate_usage_cost(
        usage,
        provider=agent.llm_provider,
        model=agent.llm_model,
    )
    return estimate.as_dict() if estimate is not None else None


@dataclass
class TrialResult:
    trial_id: int
    name: str
    source_file: str
    mutations_applied: list[str]
    steps: int
    reason: str
    message: str
    duration_s: float
    mode: str = "mutation"
    skipped: bool = False
    skip_reason: str | None = None
    used_lemma_count: int | None = None
    red_herring_count: int | None = None
    real_lemmas_referenced: int | None = None
    red_herrings_referenced: int | None = None
    retrospective_file: str | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    # DeepSeek USD estimate when model rates are known; None for local providers.
    estimated_cost: dict | None = None


@dataclass
class ExperimentResult:
    spec_name: str
    mode: str
    trials_requested: int
    trials_run: int
    trials_skipped: int
    successes: int
    stuck: int
    max_steps: int
    errors: int
    trial_results: list[TrialResult] = field(default_factory=list)
    output_dir: Path | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    # Totals divided by `trials_run`, for comparing run economy across models.
    token_usage_per_trial: dict[str, float] = field(default_factory=dict)
    estimated_cost: dict | None = None
    # Repair-specific outcomes aggregated from per-trial artifacts (W8).
    # Empty for modes that write none, so mutation/informal summaries stay
    # unchanged rather than gaining a block of nulls.
    repair_metrics: dict = field(default_factory=dict)
    # Spend cap state, when --max-spend-usd was given. `budget_stopped`
    # says whether the run ended early because of it, which a reader must
    # know before interpreting the success rate.
    budget: dict | None = None
    budget_stopped: bool = False


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields) -> None:
        entry = {"time": _utc_now(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_summary(path: Path, result: ExperimentResult) -> None:
    payload = asdict(result)
    payload["output_dir"] = str(result.output_dir) if result.output_dir else None
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _prepare_mutation(
    case: ProofCase,
    spec: ExperimentSpec,
    agent_config: AgentConfig,
    rng: random.Random,
    retries: int,
) -> tuple[list[str], list[str], list[str]] | None:
    """Return (mutated_lines, tactic_lines, operators) or None if preparation failed."""
    lines = case.file.read_text(encoding="utf-8").splitlines()
    if not is_proof_complete(case.file, agent_config):
        return None

    for _ in range(retries):
        mutation = spec.mutations.apply(lines, list(case.tactic_lines), rng)
        if is_proof_incomplete_from_lines(mutation.lines, case.file.parent, agent_config):
            return mutation.lines, mutation.tactic_lines, mutation.operators_applied
    return None


def is_proof_incomplete_from_lines(
    lines: list[str], parent: Path, agent_config: AgentConfig
) -> bool:
    tmp = parent / ".mutation_check.ec"
    apply_lines(tmp, lines)
    try:
        return is_proof_incomplete(tmp, agent_config)
    finally:
        if tmp.exists():
            tmp.unlink()


def _experiment_mode(spec: ExperimentSpec) -> str:
    """Mode label for summary.json / events.jsonl.

    Branch order MUST match run_trial's dispatch order, or summary.json
    mislabels the run. Every mode-marker field needs a branch here: a
    replay_bootstrap spec used to fall through to "mutation" while
    TrialResult.mode said "replay_bootstrap", so the two disagreed and any
    results table built from summary.json was silently wrong.
    """
    if spec.informal is not None:
        return "informal"
    if spec.broken_formal is not None:
        return "broken_formal"
    if spec.replay_bootstrap is not None:
        return "replay_bootstrap"
    return "mutation"


def _count_referenced_lemmas(
    work_copy: Path, used: dict[str, str], herrings: dict[str, str]
) -> tuple[int, int]:
    if not work_copy.exists():
        return 0, 0
    text = work_copy.read_text(encoding="utf-8")
    real = sum(1 for name in used if name_boundary_pattern(name).search(text))
    herring = sum(1 for name in herrings if name_boundary_pattern(name).search(text))
    return real, herring


def run_informal_trial(
    trial_id: int,
    case: ProofCase,
    spec: ExperimentSpec,
    config: ExperimentConfig,
    rng: random.Random,
    trial_dir: Path,
) -> TrialResult:
    usage = TokenUsage()
    agent_config = replace(config.agent, usage_tracker=usage)
    informal_config = spec.informal
    trial_dir.mkdir(parents=True, exist_ok=True)

    original_path = trial_dir / "original.ec"
    original_path.write_bytes(case.file.read_bytes())

    if not is_proof_complete(original_path, agent_config):
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mode="informal",
            mutations_applied=[],
            steps=0,
            reason="SKIPPED",
            message="Original proof not complete",
            duration_s=0.0,
            skipped=True,
            skip_reason="not_complete",
            token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
        )

    lines = case.file.read_text(encoding="utf-8").splitlines()
    tactic_text = "\n".join(lines[i - 1] for i in case.tactic_lines)
    catalog = fetch_premises_at_cursor(case.file, case.proof_start_line, agent_config)

    used_names = extract_used_lemma_names(tactic_text, catalog.keys())
    used = {name: catalog[name] for name in used_names}

    try:
        informal_text = write_informal_proof(
            case.index_entry.signature, tactic_text, agent_config, informal_config
        )
    except InformalWriterError as exc:
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mode="informal",
            mutations_applied=[],
            steps=0,
            reason="SKIPPED",
            message=str(exc),
            duration_s=0.0,
            skipped=True,
            skip_reason="writer_truncated",
            token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
        )
    if looks_contaminated(informal_text):
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mode="informal",
            mutations_applied=[],
            steps=0,
            reason="SKIPPED",
            message="Writer LLM leaked EasyCrypt syntax",
            duration_s=0.0,
            skipped=True,
            skip_reason="writer_leaked_code",
            token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
        )

    herrings = select_red_herrings(
        used, catalog, agent_config, informal_config.red_herring_ratio, rng
    )
    manifest = build_lemma_manifest(used, herrings)
    labeled = build_labeled_manifest(used, herrings)

    (trial_dir / "informal_proof.md").write_text(informal_text + "\n", encoding="utf-8")
    (trial_dir / "lemma_manifest.json").write_text(
        json.dumps(dict(manifest), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (trial_dir / "lemma_manifest_labeled.json").write_text(
        json.dumps(labeled, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    start_lines = strip_tactics(lines, case.tactic_lines)
    agent_start = trial_dir / "agent_start.ec"
    apply_lines(agent_start, start_lines)

    trial_agent_config = replace(
        agent_config,
        repair_hint=None,
        informal_proof=informal_text,
        premises_override=dict(manifest),
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
        right_fix=tactic_text,
        retrospective_file=trial_dir / "timeout_retrospective.json",
    )

    start = time.monotonic()
    result = run_agent(
        agent_start,
        trial_agent_config,
        work_copy=trial_dir / "agent_work.agent.ec",
    )
    duration = time.monotonic() - start

    real_referenced, herring_referenced = _count_referenced_lemmas(
        trial_dir / "agent_work.agent.ec", used, herrings
    )

    return TrialResult(
        trial_id=trial_id,
        name=case.name,
        source_file=str(case.file),
        mode="informal",
        mutations_applied=[],
        steps=result.steps,
        reason=result.reason.name,
        message=result.message,
        duration_s=round(duration, 3),
        used_lemma_count=len(used),
        red_herring_count=len(herrings),
        real_lemmas_referenced=real_referenced,
        red_herrings_referenced=herring_referenced,
        retrospective_file=(
            str(result.retrospective_file) if result.retrospective_file else None
        ),
        token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
    )


def run_broken_formal_trial(
    trial_id: int,
    case: ProofCase,
    config: ExperimentConfig,
    trial_dir: Path,
) -> TrialResult:
    """Trial for a genuinely broken formal-proof corpus (e.g. ElGamal): the
    corpus has already admitted every lemma the target depends on (see
    `integration.experiment.corpora.elgamal`), so the only thing left to
    check here is that the target's own goal is reachable. The solver is
    given the corpus's own broken tactic script as reference — not a
    writer-LLM paraphrase — and ranks premises against the full ambient
    catalog (no red herrings / curated manifest): this measures repair
    effectiveness and efficiency against a genuinely broken proof, not
    against an informal sketch plus a hand-picked lemma set.
    """
    usage = TokenUsage()
    agent_config = replace(config.agent, usage_tracker=usage)
    trial_dir.mkdir(parents=True, exist_ok=True)

    original_path = trial_dir / "original.ec"
    original_path.write_bytes(case.file.read_bytes())

    goal_result = fetch_goal_and_premises(case.file, case.proof_start_line, agent_config)
    if goal_result.returncode != 0 or not has_open_goals(goal_result.stdout):
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mode="broken_formal",
            mutations_applied=[],
            steps=0,
            reason="SKIPPED",
            message=(
                "Target goal unreachable even after admitting prior lemmas "
                f"(stderr: {goal_result.stderr.strip()[:300]})"
            ),
            duration_s=0.0,
            skipped=True,
            skip_reason="goal_unreachable",
            token_usage=usage,
            estimated_cost=_cost_for_usage(usage, agent_config),
        )

    lines = case.file.read_text(encoding="utf-8").splitlines()
    broken_proof_text = "\n".join(lines[i - 1] for i in case.tactic_lines)
    (trial_dir / "informal_proof.md").write_text(
        broken_proof_text + "\n", encoding="utf-8"
    )

    start_lines = strip_tactics(lines, case.tactic_lines)
    agent_start = trial_dir / "agent_start.ec"
    apply_lines(agent_start, start_lines)

    trial_agent_config = replace(
        agent_config,
        repair_hint=None,
        informal_proof=broken_proof_text,
        informal_proof_is_formal=True,
        premises_override=None,
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
        right_fix=broken_proof_text,
        retrospective_file=trial_dir / "timeout_retrospective.json",
    )

    start = time.monotonic()
    result = run_agent(
        agent_start,
        trial_agent_config,
        work_copy=trial_dir / "agent_work.agent.ec",
    )
    duration = time.monotonic() - start

    return TrialResult(
        trial_id=trial_id,
        name=case.name,
        source_file=str(case.file),
        mode="broken_formal",
        mutations_applied=[],
        steps=result.steps,
        reason=result.reason.name,
        message=result.message,
        duration_s=round(duration, 3),
        retrospective_file=(
            str(result.retrospective_file) if result.retrospective_file else None
        ),
        token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
    )


def run_trial(
    trial_id: int,
    case: ProofCase,
    spec: ExperimentSpec,
    config: ExperimentConfig,
    rng: random.Random,
    trial_dir: Path,
) -> TrialResult:
    if spec.informal is not None:
        return run_informal_trial(trial_id, case, spec, config, rng, trial_dir)
    if spec.broken_formal is not None:
        return run_broken_formal_trial(trial_id, case, config, trial_dir)
    if spec.replay_bootstrap is not None:
        # Deferred import: repair_bootstrap.py imports TrialResult/_cost_for_usage
        # from this module, so a top-level import here would be circular.
        from integration.experiment.repair_bootstrap import run_replay_bootstrap_trial

        return run_replay_bootstrap_trial(
            trial_id, case, config, spec.replay_bootstrap, trial_dir,
        )

    usage = TokenUsage()
    agent_config = replace(config.agent, usage_tracker=usage)
    trial_dir.mkdir(parents=True, exist_ok=True)

    original_path = trial_dir / "original.ec"
    original_path.write_bytes(case.file.read_bytes())
    original_lines = case.file.read_text(encoding="utf-8").splitlines()
    right_fix = format_hint(original_lines, case.tactic_lines)

    prep = _prepare_mutation(case, spec, agent_config, rng, config.mutation_retries)
    if prep is None:
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mutations_applied=[],
            steps=0,
            reason="SKIPPED",
            message="Could not produce incomplete mutation",
            duration_s=0.0,
            skipped=True,
            skip_reason="mutation_failed",
            token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
        )

    mutated_lines, tactic_lines, operators = prep
    mutated_path = trial_dir / "mutated.ec"
    apply_lines(mutated_path, mutated_lines)

    if not is_proof_complete(original_path, agent_config):
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mutations_applied=[],
            steps=0,
            reason="SKIPPED",
            message="Original proof not complete",
            duration_s=0.0,
            skipped=True,
            skip_reason="not_complete",
            token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
        )

    hint = format_hint(mutated_lines, tactic_lines)
    start_lines = strip_tactics(mutated_lines, tactic_lines)
    agent_start = trial_dir / "agent_start.ec"
    apply_lines(agent_start, start_lines)

    trial_agent_config = replace(
        agent_config,
        repair_hint=hint,
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
        right_fix=right_fix,
        retrospective_file=trial_dir / "timeout_retrospective.json",
    )

    start = time.monotonic()
    result = run_agent(
        agent_start,
        trial_agent_config,
        work_copy=trial_dir / "agent_work.agent.ec",
    )
    duration = time.monotonic() - start

    return TrialResult(
        trial_id=trial_id,
        name=case.name,
        source_file=str(case.file),
        mutations_applied=operators,
        steps=result.steps,
        reason=result.reason.name,
        message=result.message,
        duration_s=round(duration, 3),
        retrospective_file=(
            str(result.retrospective_file) if result.retrospective_file else None
        ),
        token_usage=usage,
        estimated_cost=_cost_for_usage(usage, agent_config),
    )


def run_experiment(spec: ExperimentSpec, config: ExperimentConfig) -> ExperimentResult:
    config = config.with_agent_defaults()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(config.seed)
    events = EventLog(config.output_dir / "events.jsonl")
    events.record(
        "experiment_start",
        spec=spec.name,
        mode=_experiment_mode(spec),
        trials=config.trials,
        stuck_limit=config.stuck_limit,
        seed=config.seed,
    )

    if config.sort_by_difficulty:
        # Deterministic shortest-first, so a bounded run spends its budget on
        # the cases most likely to be completable and two runs are comparable
        # without relying on a shared seed.
        cases = sorted(spec.corpus.load_cases(), key=lambda c: len(c.tactic_lines))
        cases = cases[: config.trials]
    else:
        cases = spec.corpus.sample_cases(config.trials, rng)
    trial_results: list[TrialResult] = []
    successes = stuck = max_steps = errors = skipped = 0
    total_usage = TokenUsage()

    budget = config.agent.spend_budget
    budget_stopped = False

    for i, case in enumerate(cases):
        # Stop launching NEW trials once the cap is reached. A trial that has
        # already begun finishes its current step and exits on its own budget
        # check, so partial work is still recorded rather than discarded.
        if budget is not None and budget.exhausted:
            budget_stopped = True
            logger.info(
                "Spend cap reached before trial %d; %d trial(s) not started (%s)",
                i,
                len(cases) - i,
                budget.status(),
            )
            events.record(
                "budget_exhausted",
                trials_not_started=len(cases) - i,
                budget=budget.as_dict(),
            )
            break

        trial_dir = config.output_dir / "trials" / f"trial_{i:03d}"
        # Scale the step allowance to the proof's own length when asked. The
        # agent config is replaced rather than mutated so the run-level config
        # (and the shared spend budget it carries) is untouched.
        trial_config = config
        if config.adaptive_steps_multiplier is not None:
            steps = config.steps_for_case(len(case.tactic_lines))
            trial_config = replace(
                config, agent=replace(config.agent, max_steps=steps)
            )
            logger.info(
                "Trial %d: %s (%d tactic lines -> %d step budget)",
                i, case.name, len(case.tactic_lines), steps,
            )
        else:
            logger.info("Trial %d: %s", i, case.name)
        trial = run_trial(i, case, spec, trial_config, rng, trial_dir)
        trial_results.append(trial)
        total_usage.merge(trial.token_usage)
        events.record("trial_finish", **asdict(trial))

        if trial.skipped:
            skipped += 1
            continue

        if trial.reason == ExitReason.COMPLETE.name:
            successes += 1
        elif trial.reason == ExitReason.STUCK.name:
            stuck += 1
        elif trial.reason == ExitReason.MAX_STEPS.name:
            max_steps += 1
        elif trial.reason == ExitReason.BUDGET_EXHAUSTED.name:
            # Not an error and not a failure to repair -- we simply stopped
            # paying. Counting it as either would corrupt the success rate.
            budget_stopped = True
        else:
            errors += 1

    trials_run = len(trial_results) - skipped
    estimated_cost = _cost_for_usage(total_usage, config.agent)
    cost_usd = estimated_cost["usd"] if estimated_cost is not None else None
    # Derived purely by reading artifacts the trials already wrote, so a
    # failure here must not lose an otherwise-complete experiment.
    try:
        repair_metrics = aggregate_repair_metrics(config.output_dir)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to aggregate repair metrics")
        repair_metrics = {}
    result = ExperimentResult(
        spec_name=spec.name,
        mode=_experiment_mode(spec),
        trials_requested=config.trials,
        trials_run=trials_run,
        trials_skipped=skipped,
        successes=successes,
        stuck=stuck,
        max_steps=max_steps,
        errors=errors,
        trial_results=trial_results,
        output_dir=config.output_dir,
        token_usage=total_usage,
        token_usage_per_trial=average_usage(
            total_usage, trials_run, cost_usd=cost_usd
        ),
        estimated_cost=estimated_cost,
        repair_metrics=repair_metrics,
        budget=budget.as_dict() if budget is not None else None,
        budget_stopped=budget_stopped,
    )
    _write_summary(config.output_dir / "summary.json", result)
    events.record(
        "experiment_finish",
        successes=successes,
        stuck=stuck,
        errors=errors,
        token_usage=total_usage.as_dict(),
        token_usage_per_trial=result.token_usage_per_trial,
        estimated_cost=estimated_cost,
    )
    return result

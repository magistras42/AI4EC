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
from integration.agent.loop import ExitReason
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
from integration.experiment.verify import is_proof_complete, is_proof_incomplete

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return "informal" if spec.informal is not None else "mutation"


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
    agent_config = config.agent
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
        lemma_lookup_index=spec.corpus.lemma_lookup_index(),
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
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

    agent_config = config.agent
    trial_dir.mkdir(parents=True, exist_ok=True)

    original_path = trial_dir / "original.ec"
    original_path.write_bytes(case.file.read_bytes())

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
        )

    hint = format_hint(mutated_lines, tactic_lines)
    start_lines = strip_tactics(mutated_lines, tactic_lines)
    agent_start = trial_dir / "agent_start.ec"
    apply_lines(agent_start, start_lines)

    trial_agent_config = replace(
        agent_config,
        repair_hint=hint,
        lemma_lookup_index=spec.corpus.lemma_lookup_index(),
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
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

    cases = spec.corpus.sample_cases(config.trials, rng)
    trial_results: list[TrialResult] = []
    successes = stuck = max_steps = errors = skipped = 0

    for i, case in enumerate(cases):
        trial_dir = config.output_dir / "trials" / f"trial_{i:03d}"
        logger.info("Trial %d: %s", i, case.name)
        trial = run_trial(i, case, spec, config, rng, trial_dir)
        trial_results.append(trial)
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
        else:
            errors += 1

    result = ExperimentResult(
        spec_name=spec.name,
        mode=_experiment_mode(spec),
        trials_requested=config.trials,
        trials_run=len(cases) - skipped,
        trials_skipped=skipped,
        successes=successes,
        stuck=stuck,
        max_steps=max_steps,
        errors=errors,
        trial_results=trial_results,
        output_dir=config.output_dir,
    )
    _write_summary(config.output_dir / "summary.json", result)
    events.record("experiment_finish", successes=successes, stuck=stuck, errors=errors)
    return result

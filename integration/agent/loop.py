"""Main agent loop orchestrating EasyCrypt, embeddings, and LLM."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np

from .config import AgentConfig
from .easycrypt import (
    fetch_goal,
    fetch_goal_and_premises,
    has_open_goals,
    is_no_active_proof,
    split_goal_and_premises,
    validate_file,
)
from .embeddings import EmbeddingClient, rank_by_cosine, top_premises
from .error_history import ErrorHistory
from .llm import LlmClient, TacticAction, UndoAction
from .premises import (
    load_cached_embeddings,
    parse_premises,
    save_cached_embeddings,
)
from .prompt import build_prompt
from .proof_file import ProofFile, create_working_copy, promote_working_copy
from .run_log import AgentRunLog

logger = logging.getLogger(__name__)


class ExitReason(Enum):
    COMPLETE = auto()
    ALREADY_COMPLETE = auto()
    STARTUP_ERROR = auto()
    MAX_STEPS = auto()
    LLM_ERROR = auto()


@dataclass
class AgentResult:
    reason: ExitReason
    message: str
    steps: int = 0
    work_copy: Path | None = None


def run_agent(
    source: Path,
    config: AgentConfig | None = None,
    work_copy: Path | None = None,
) -> AgentResult:
    config = config or AgentConfig()
    if work_copy is None:
        work_copy = create_working_copy(
            source,
            suffix=config.work_copy_suffix,
            output_dir=config.output_dir,
        )
    elif not work_copy.exists():
        create_working_copy(source, work_copy=work_copy)
    proof = ProofFile(work_copy)
    errors = ErrorHistory(
        work_copy.with_name(f".{work_copy.stem}.error_history.json")
    )
    run_log = (
        AgentRunLog(config.log_file, source, work_copy)
        if config.log_file is not None
        else None
    )

    startup = _startup(proof, config, run_log)
    if startup is not None:
        _log_finish(run_log, startup)
        return startup

    premise_index, premises_catalog = _build_premise_index(proof, config)
    llm = LlmClient(config)
    embedder = EmbeddingClient(config)

    if run_log is not None:
        bounds = proof.bounds()
        goal_result = fetch_goal(work_copy, bounds.cursor_upto, config)
        run_log.startup(
            goal=goal_result.stdout.strip(),
            premise_count=len(premises_catalog),
            cursor_upto=bounds.cursor_upto,
        )

    for step in range(1, config.max_steps + 1):
        bounds = proof.bounds()
        goal_result = fetch_goal(work_copy, bounds.cursor_upto, config)
        if goal_result.returncode != 0:
            result = AgentResult(
                reason=ExitReason.STARTUP_ERROR,
                message=goal_result.stderr.strip() or goal_result.stdout.strip(),
                steps=step - 1,
                work_copy=work_copy,
            )
            _log_finish(run_log, result)
            return result

        goal = goal_result.stdout.strip()
        if is_no_active_proof(goal):
            result = _complete(config, source, work_copy, step - 1, already=False)
            _log_finish(run_log, result)
            return result

        ranked = rank_by_cosine(
            premise_index,
            embedder.embed(goal),
            config.top_k,
        )
        top = top_premises(premises_catalog, ranked)

        prompt = build_prompt(
            goal=goal,
            top_premises=top,
            failed_tactics=errors.get(goal),
            proof_tail=proof.tail(config.proof_tail_lines),
        )

        try:
            action = llm.decide(prompt)
        except Exception as exc:
            result = AgentResult(
                reason=ExitReason.LLM_ERROR,
                message=str(exc),
                steps=step - 1,
                work_copy=work_copy,
            )
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="error",
                    outcome="llm_error",
                    error=str(exc),
                )
            _log_finish(run_log, result)
            return result

        if isinstance(action, UndoAction):
            undone = proof.undo_last_tactic()
            if not undone:
                logger.info("Undo requested but no tactic to remove")
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="undo",
                    outcome="noop" if not undone else "undone",
                )
            continue

        if isinstance(action, TacticAction):
            inserted_line = proof.append_tactic(action.tactic)
            validation = validate_file(work_copy, config)
            if validation.returncode != 0:
                proof.remove_lines(inserted_line)
                error_msg = validation.stderr.strip() or validation.stdout.strip()
                errors.add(goal, error_msg, action.tactic)
                logger.info("Tactic failed: %s", error_msg)
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="failed",
                        error=error_msg,
                    )
                continue

            if validation.returncode == 0 and not has_open_goals(validation.stdout):
                result = _complete(config, source, work_copy, step, already=False)
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="complete",
                    )
                _log_finish(run_log, result)
                return result

            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="tactic",
                    tactic=action.tactic,
                    outcome="accepted",
                )

    result = AgentResult(
        reason=ExitReason.MAX_STEPS,
        message=f"Exceeded max steps ({config.max_steps})",
        steps=config.max_steps,
        work_copy=work_copy,
    )
    _log_finish(run_log, result)
    return result


def _log_finish(run_log: AgentRunLog | None, result: AgentResult) -> None:
    if run_log is None:
        return
    run_log.finish(
        reason=result.reason.name,
        message=result.message,
        steps=result.steps,
    )


def _startup(
    proof: ProofFile,
    config: AgentConfig,
    run_log: AgentRunLog | None,
) -> AgentResult | None:
    bounds = proof.bounds()
    result = fetch_goal_and_premises(proof.path, bounds.cursor_upto, config)
    if result.returncode != 0:
        return AgentResult(
            reason=ExitReason.STARTUP_ERROR,
            message=result.stderr.strip() or result.stdout.strip(),
            work_copy=proof.path,
        )

    split = split_goal_and_premises(result.stdout)
    if is_no_active_proof(split.goal):
        startup_result = AgentResult(
            reason=ExitReason.ALREADY_COMPLETE,
            message="Proof already complete at startup",
            work_copy=proof.path,
        )
        if run_log is not None:
            run_log.startup(
                goal=split.goal,
                premise_count=len(parse_premises(split.premises)),
                cursor_upto=bounds.cursor_upto,
            )
        return startup_result
    return None


def _build_premise_index(
    proof: ProofFile, config: AgentConfig
) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    bounds = proof.bounds()
    result = fetch_goal_and_premises(proof.path, bounds.cursor_upto, config)
    split = split_goal_and_premises(result.stdout)
    premises = parse_premises(split.premises)
    if config.max_premises is not None:
        premises = dict(list(premises.items())[: config.max_premises])

    embedder = EmbeddingClient(config)
    model = embedder._resolve_model()
    decl_count = proof.count_declarations_before(bounds.last_line)

    cached = load_cached_embeddings(
        proof.path, bounds.cursor_upto, model, decl_count
    )
    if cached is not None:
        return (
            {name: np.asarray(vec) for name, vec in cached.items()},
            premises,
        )

    index = embedder.build_index(premises)
    save_cached_embeddings(
        proof.path,
        bounds.cursor_upto,
        model,
        decl_count,
        premises,
        {name: vec.tolist() for name, vec in index.items()},
    )
    return index, premises


def _complete(
    config: AgentConfig,
    source: Path,
    work_copy: Path,
    steps: int,
    already: bool,
) -> AgentResult:
    if config.promote_on_success:
        promote_working_copy(work_copy, source)
    reason = ExitReason.ALREADY_COMPLETE if already else ExitReason.COMPLETE
    message = "Proof complete"
    return AgentResult(reason=reason, message=message, steps=steps, work_copy=work_copy)

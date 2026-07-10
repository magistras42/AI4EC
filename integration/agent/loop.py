"""Main agent loop orchestrating EasyCrypt, embeddings, and LLM."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np

from .config import AgentConfig
from .easycrypt import (
    fetch_goal,
    fetch_goal_and_premises,
    is_no_active_proof,
    is_proof_complete_at_cursor,
    is_proof_discharged,
    resolve_goal,
    resolve_goal_cursor,
    split_goal_and_premises,
    tactic_discharged_proof,
    validate_file,
)
from .embeddings import EmbeddingClient, rank_by_cosine, top_premises
from .error_history import ErrorHistory
from .llm import LlmClient, LookupLemmaAction, TacticAction, UndoAction
from .premises import (
    load_cached_embeddings,
    parse_premises,
    save_cached_embeddings,
)
from .prompt import build_prompt
from .proof_file import ProofFile, create_working_copy, promote_working_copy
from .run_log import AgentRunLog

logger = logging.getLogger(__name__)

# EasyCrypt's `admit` tactic marks a goal as assumed-without-proof, and
# EasyCrypt reports this as a fully discharged goal (return code 0, no open
# goals) even when the statement is false. Left unblocked, a confused model
# could "succeed" by cheating rather than proving anything. This is checked
# before the tactic is ever written to the proof file, so it never reaches
# EasyCrypt at all.
_BANNED_TACTIC_RE = re.compile(r"\badmit\b", re.IGNORECASE)
_BANNED_TACTIC_MESSAGE = (
    "The `admit` tactic is disallowed: it marks a goal as assumed without "
    "proof, and EasyCrypt reports this as success even for false "
    "statements. Find a real proof instead."
)

# Small local models occasionally emit a degenerate/runaway generation (e.g.
# hundreds of blank lines, or stray control bytes) instead of a real tactic.
# `ProofFile.append_tactic` writes the tactic text as a single logical line,
# but embedded newlines become real physical lines on disk once written; a
# failed tactic is then rolled back with `remove_lines(..., count=1)`, which
# only deletes the first of those lines and permanently corrupts the file
# with the rest. Reject anything that isn't a single, reasonably-sized line
# before it's ever written, rather than trying to clean it up after the fact.
_MAX_TACTIC_LENGTH = 300
_DEGENERATE_TACTIC_MESSAGE = (
    "That response was not a single, well-formed tactic (it contained a "
    "newline or was implausibly long). Reply with exactly one short "
    "EasyCrypt tactic line."
)


def _is_degenerate_tactic(tactic: str) -> bool:
    return "\n" in tactic or "\r" in tactic or len(tactic) > _MAX_TACTIC_LENGTH


class ExitReason(Enum):
    COMPLETE = auto()
    ALREADY_COMPLETE = auto()
    STARTUP_ERROR = auto()
    MAX_STEPS = auto()
    LLM_ERROR = auto()
    STUCK = auto()


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
        cursor = resolve_goal_cursor(proof, config)
        goal_result = fetch_goal(work_copy, cursor, config)
        run_log.startup(
            goal=resolve_goal(proof, config),
            premise_count=len(premises_catalog),
            cursor_upto=cursor,
        )

    lookup_notes: list[str] = []
    seen_proof_states: set[str] = set()
    stuck_counter = 0
    enable_lookup = config.lemma_lookup_index is not None

    for step in range(1, config.max_steps + 1):
        bounds = proof.bounds()
        goal = resolve_goal(proof, config)
        if is_proof_discharged(goal) or tactic_discharged_proof(proof, goal, config):
            result = _complete(config, source, work_copy, step - 1, already=False)
            _log_finish(run_log, result)
            return result

        cursor = resolve_goal_cursor(proof, config)
        goal_result = fetch_goal(work_copy, cursor, config)
        if goal_result.returncode != 0:
            result = AgentResult(
                reason=ExitReason.STARTUP_ERROR,
                message=goal_result.stderr.strip() or goal_result.stdout.strip(),
                steps=step - 1,
                work_copy=work_copy,
            )
            _log_finish(run_log, result)
            return result

        if not goal:
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
            repair_hint=config.repair_hint,
            informal_proof=config.informal_proof,
            lookup_notes=lookup_notes,
            enable_lemma_lookup=enable_lookup,
        )

        try:
            decision = llm.decide(prompt)
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

        action = decision.action
        thought = decision.thought
        raw_content = decision.content

        if isinstance(action, UndoAction):
            undone = proof.undo_last_tactic()
            if not undone:
                logger.info("Undo requested but no tactic to remove")
            else:
                stuck_counter = _increment_stuck(config, stuck_counter)
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="undo",
                    outcome="noop" if not undone else "undone",
                    thought=thought,
                    content=raw_content,
                )
            if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                return _stuck_result(config, work_copy, step, run_log)
            continue

        if isinstance(action, LookupLemmaAction):
            note = _lookup_lemma(config, action.name)
            lookup_notes.append(note)
            stuck_counter = _increment_stuck(config, stuck_counter)
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="lookup_lemma",
                    outcome="lookup",
                    lookup_name=action.name,
                    lookup_result=note,
                    thought=thought,
                    content=raw_content,
                )
            if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                return _stuck_result(config, work_copy, step, run_log)
            continue

        if isinstance(action, TacticAction):
            if _is_degenerate_tactic(action.tactic):
                logged_tactic = action.tactic[:200]
                errors.add(goal, _DEGENERATE_TACTIC_MESSAGE, logged_tactic)
                logger.info(
                    "Tactic rejected as degenerate (len=%d): %s",
                    len(action.tactic),
                    logged_tactic,
                )
                stuck_counter = _increment_stuck(config, stuck_counter)
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="tactic",
                        tactic=logged_tactic,
                        outcome="rejected",
                        error=_DEGENERATE_TACTIC_MESSAGE,
                        thought=thought,
                    content=raw_content,
                    )
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(config, work_copy, step, run_log)
                continue

            if _BANNED_TACTIC_RE.search(action.tactic):
                errors.add(goal, _BANNED_TACTIC_MESSAGE, action.tactic)
                logger.info("Tactic rejected: %s", _BANNED_TACTIC_MESSAGE)
                stuck_counter = _increment_stuck(config, stuck_counter)
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="rejected",
                        error=_BANNED_TACTIC_MESSAGE,
                        thought=thought,
                    content=raw_content,
                    )
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(config, work_copy, step, run_log)
                continue

            inserted_line = proof.append_tactic(action.tactic)
            validation = validate_file(work_copy, config)
            if validation.returncode != 0:
                proof.remove_lines(inserted_line)
                error_msg = validation.stderr.strip() or validation.stdout.strip()
                errors.add(goal, error_msg, action.tactic)
                logger.info("Tactic failed: %s", error_msg)
                stuck_counter = _increment_stuck(config, stuck_counter)
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
                        thought=thought,
                    content=raw_content,
                    )
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(config, work_copy, step, run_log)
                continue

            if is_proof_complete_at_cursor(proof, config):
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
                        thought=thought,
                    content=raw_content,
                    )
                _log_finish(run_log, result)
                return result

            state_hash = _proof_state_hash(proof.tail(config.proof_tail_lines))
            if state_hash in seen_proof_states:
                stuck_counter = _increment_stuck(config, stuck_counter)
            else:
                seen_proof_states.add(state_hash)
                stuck_counter = 0

            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="tactic",
                    tactic=action.tactic,
                    outcome="accepted",
                    thought=thought,
                    content=raw_content,
                )
            if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                return _stuck_result(config, work_copy, step, run_log)

    result = AgentResult(
        reason=ExitReason.MAX_STEPS,
        message=f"Exceeded max steps ({config.max_steps})",
        steps=config.max_steps,
        work_copy=work_copy,
    )
    _log_finish(run_log, result)
    return result


def _proof_state_hash(proof_tail: str) -> str:
    return hashlib.sha256(proof_tail.encode("utf-8")).hexdigest()


def _increment_stuck(config: AgentConfig, stuck_counter: int) -> int:
    if config.stuck_limit is None:
        return stuck_counter
    return stuck_counter + 1


def _check_stuck(
    config: AgentConfig,
    stuck_counter: int,
    run_log: AgentRunLog | None,
    work_copy: Path,
    step: int,
) -> bool:
    if config.stuck_limit is None:
        return False
    return stuck_counter >= config.stuck_limit


def _stuck_result(
    config: AgentConfig,
    work_copy: Path,
    step: int,
    run_log: AgentRunLog | None,
) -> AgentResult:
    limit = config.stuck_limit or 0
    result = AgentResult(
        reason=ExitReason.STUCK,
        message=f"Agent stuck after {limit} unproductive iterations",
        steps=step,
        work_copy=work_copy,
    )
    _log_finish(run_log, result)
    return result


def _lookup_lemma(config: AgentConfig, name: str) -> str:
    index = config.lemma_lookup_index or {}
    if name in index:
        return f"{name}: {index[name]}"
    lower = name.lower()
    for key, sig in index.items():
        if key.lower() == lower:
            return f"{key}: {sig}"
    partial = [key for key in index if lower in key.lower()]
    if len(partial) == 1:
        key = partial[0]
        return f"{key}: {index[key]}"
    if partial:
        return f"{name}: ambiguous ({', '.join(sorted(partial)[:5])})"
    return f"{name}: not found"


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
    cursor = resolve_goal_cursor(proof, config)
    result = fetch_goal_and_premises(proof.path, cursor, config)
    if result.returncode != 0:
        return AgentResult(
            reason=ExitReason.STARTUP_ERROR,
            message=result.stderr.strip() or result.stdout.strip(),
            work_copy=proof.path,
        )

    goal = resolve_goal(proof, config)
    if is_no_active_proof(goal) or is_proof_discharged(goal):
        startup_result = AgentResult(
            reason=ExitReason.ALREADY_COMPLETE,
            message="Proof already complete at startup",
            work_copy=proof.path,
        )
        if run_log is not None:
            premise_count = (
                len(config.premises_override)
                if config.premises_override is not None
                else len(parse_premises(split_goal_and_premises(result.stdout).premises))
            )
            run_log.startup(
                goal=goal,
                premise_count=premise_count,
                cursor_upto=cursor,
            )
        return startup_result
    return None


def _build_premise_index(
    proof: ProofFile, config: AgentConfig
) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    bounds = proof.bounds()
    cursor = resolve_goal_cursor(proof, config)
    if config.premises_override is not None:
        premises = dict(config.premises_override)
    else:
        result = fetch_goal_and_premises(proof.path, cursor, config)
        split = split_goal_and_premises(result.stdout)
        premises = parse_premises(split.premises)
    if config.max_premises is not None:
        premises = dict(list(premises.items())[: config.max_premises])

    embedder = EmbeddingClient(config)
    model = embedder._resolve_model()
    decl_count = proof.count_declarations_before(bounds.last_line)

    cached = load_cached_embeddings(
        proof.path, cursor, model, decl_count
    )
    if cached is not None:
        return (
            {name: np.asarray(vec) for name, vec in cached.items()},
            premises,
        )

    index = embedder.build_index(premises)
    save_cached_embeddings(
        proof.path,
        cursor,
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

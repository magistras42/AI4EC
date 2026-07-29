"""Main agent loop orchestrating EasyCrypt, embeddings, and LLM."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np

from .config import AgentConfig, resolve_thinking_for_step
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
from .lemma_search import (
    filter_catalog_by_theory,
    search_lemmas as run_lemma_search,
    split_theory_filter,
)
from .llm import (
    LlmClient,
    LlmFormatError,
    LookupLemmaAction,
    SearchLemmasAction,
    TacticAction,
    UndoAction,
)
from .premises import (
    lemma_ref_from_key,
    load_cached_embeddings,
    parse_premises,
    save_cached_embeddings,
)
from .prompt import (
    build_prompt,
    goal_is_implication_before_hl,
    goal_looks_program_logic,
)
from .proof_file import ProofFile, create_working_copy, promote_working_copy
from .run_log import AgentRunLog

logger = logging.getLogger(__name__)

_LEMMA_SEARCH_INDEX_CACHE: dict[
    tuple[str, str], dict[str, np.ndarray]
] = {}

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
_MALFORMED_REPLY_LABEL = "(malformed JSON reply)"
_MALFORMED_REPLY_HINT = (
    "Previous reply was not usable JSON. Reply with exactly one JSON object "
    "and escape every backslash in tactic strings (EasyCrypt /\\ must be "
    "written as /\\\\ inside JSON)."
)
_REPEAT_TACTIC_MESSAGE = (
    "Rejected: this tactic is identical (after normalization) to one that "
    "already failed at this exact goal. Choose a genuinely different "
    "strategy — a different head tactic, a different invariant/lemma, or "
    "break a compound `t1; t2.` into separate steps. Whitespace or "
    "`&&` vs `/\\` changes do not count as different."
)
_WHILE_ONESHOT_HINT = (
    "A one-shot `while (...); auto; smt().` (or similar compound) failed. "
    "Do NOT resubmit the same compound. Instead: (1) apply `while (inv).` "
    "alone, then discharge each subgoal with `wp.` / `skip.` / `smt().` "
    "separately; (2) change the loop invariant if preservation or init/exit "
    "still fails; (3) avoid retrying whitespace/`&&`/`/\\` variants of the "
    "same compound — the harness will reject them."
)
_IMPL_BEFORE_HL_HINT = (
    "The current goal is an implication or quantified statement wrapping a "
    "Hoare/pHoare/equiv judgment (look for `=> hoare[...]` / `forall ..., "
    "hoare[...]` under the dashed separator). Introduce the outer hypotheses "
    "or binders first with `move => ...`, then apply `proc.` / `while` / `wp`. "
    "Program-logic tactics expect a bare judgment, not `P => judgment`."
)
_HL_ON_AMBIENT_HINT = (
    "This tactic (e.g. 'wp', 'sp', 'rnd', 'call', 'skip', 'proc') requires the "
    "goal to be a Hoare/pHoare/equiv program-logic judgment, but the current "
    "goal appears to be ambient logic already. "
    "Do not apply program-logic tactics after the goal has been reduced by "
    "'skip.' or 'wp.'. Use 'smt()', 'trivial', 'ring', or 'assumption' instead."
)
_HL_SHAPE_MISMATCH_HINT = (
    "EasyCrypt rejected this tactic because the current program-logic goal "
    "does not have the shape this tactic expects (even though it still shows "
    "pre/post or procedural code). The goal is NOT ambient logic — do not "
    "switch to smt()/trivial solely because of this message. "
    "Typical causes: applying `while` when one side is not a while (use "
    "`seq` / `if` / `rcondt`/`rcondf` first), applying `unroll` when the "
    "focused statement is not a loop, or applying `proc` after the bodies "
    "are already open. Inspect the instruction lists under pre/post and "
    "choose a tactic that matches the leading statements."
)
_SEARCH_LIMIT_REJECT = (
    "Search/lookup budget exhausted: too many consecutive retrieval actions "
    "without attempting a tactic. Choose a tactic (or undo) before searching "
    "again. If semantic search missed a name, try one substring/prefix/exact "
    "search after your next tactic failure — do not keep searching blindly."
)


def _is_degenerate_tactic(tactic: str) -> bool:
    return "\n" in tactic or "\r" in tactic or len(tactic) > _MAX_TACTIC_LENGTH


def _is_oneshot_while_compound(tactic: str) -> bool:
    """True for while+auto/smt compounds that should not be spam-retried."""
    compact = re.sub(r"\s+", "", tactic.lower())
    if "while" not in compact:
        return False
    return ";auto" in compact or ";smt" in compact


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
    retrospective_file: Path | None = None


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
        AgentRunLog(config.log_file, source, work_copy, usage=config.usage_tracker)
        if config.log_file is not None
        else None
    )

    startup = _startup(proof, config, run_log)
    if startup is not None:
        _log_finish(run_log, startup)
        return startup

    premise_index, premises_catalog, lookup_catalog = _build_premise_index(
        proof, config
    )
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
    lemma_search_index: dict[str, np.ndarray] | None = None
    trajectory: list[dict] = []
    seen_proof_states: set[str] = set()
    stuck_counter = 0
    continuous_searches = 0
    consecutive_noop_undos = 0
    # Lookup/search always use EasyCrypt Ax.all (lookup_catalog), never an
    # external proofs_index. Enable the tools whenever that catalog exists.
    enable_lookup = True

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

        search_warning = _search_budget_warning(config, continuous_searches)
        prompt = build_prompt(
            goal=goal,
            top_premises=top,
            failed_tactics=errors.get(goal),
            proof_tail=proof.tail(config.proof_tail_lines),
            repair_hint=config.repair_hint,
            changelog_hints=config.changelog_hints,
            informal_proof=config.informal_proof,
            informal_proof_is_formal=config.informal_proof_is_formal,
            lookup_notes=lookup_notes,
            enable_lemma_lookup=enable_lookup,
            past_steps=(
                trajectory[-config.history_steps :]
                if config.history_steps > 0
                else []
            ),
            search_warning=search_warning,
            recent_failures=errors.recent_other(goal),
        )

        try:
            thinking = resolve_thinking_for_step(config, trajectory)
            decision = llm.decide(prompt, thinking=thinking)
        except LlmFormatError as exc:
            # Treat malformed/empty replies like a failed tactic: keep going
            # with feedback instead of aborting the whole trial.
            error_msg = f"{_MALFORMED_REPLY_HINT} Parser detail: {exc}"
            errors.add(goal, error_msg, _MALFORMED_REPLY_LABEL)
            logger.info("Recoverable LLM format error: %s", exc)
            stuck_counter = _increment_stuck(config, stuck_counter)
            trajectory.append(
                _step_record(
                    step,
                    goal,
                    action="error",
                    outcome="format_error",
                    error=error_msg,
                )
            )
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="error",
                    outcome="format_error",
                    error=error_msg,
                )
            if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                return _stuck_result(
                    config, source, work_copy, step, run_log, llm, trajectory
                )
            continue
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
            continuous_searches = 0
            undone = proof.undo_last_tactic(action.count)
            if undone == 0:
                logger.info("Undo requested but no tactic to remove")
                consecutive_noop_undos += 1
                stuck_counter = _increment_stuck(config, stuck_counter)
            else:
                logger.info(
                    "Undid %d tactic(s) (requested %d)", undone, action.count
                )
                consecutive_noop_undos = 0
                stuck_counter = _increment_stuck(config, stuck_counter)
            trajectory.append(
                _step_record(
                    step,
                    goal,
                    action="undo",
                    outcome="noop" if undone == 0 else "undone",
                    undo_count=action.count,
                    undone=undone,
                    thought=thought,
                    content=raw_content,
                )
            )
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="undo",
                    outcome="noop" if undone == 0 else "undone",
                    undo_count=action.count,
                    undone=undone,
                    thought=thought,
                    content=raw_content,
                )
            noop_limit = config.max_consecutive_noop_undos
            if noop_limit is not None and consecutive_noop_undos >= noop_limit:
                return _stuck_result(
                    config,
                    source,
                    work_copy,
                    step,
                    run_log,
                    llm,
                    trajectory,
                    message=(
                        f"Agent stuck: undo removed nothing {consecutive_noop_undos} "
                        f"times in a row (max_consecutive_noop_undos={noop_limit})"
                    ),
                )
            if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                return _stuck_result(
                    config, source, work_copy, step, run_log, llm, trajectory
                )
            continue

        if isinstance(action, LookupLemmaAction):
            consecutive_noop_undos = 0
            blocked = _retrieval_blocked_note(config, continuous_searches)
            if blocked is not None:
                note = blocked
                continuous_searches += 1
                stuck_counter = _increment_stuck(config, stuck_counter)
                lookup_notes.append(note)
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="lookup_lemma",
                        outcome="search_limited",
                        lookup_name=action.name,
                        lookup_result=note,
                        thought=thought,
                        content=raw_content,
                    )
                )
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="lookup_lemma",
                        outcome="search_limited",
                        lookup_name=action.name,
                        lookup_result=note,
                        thought=thought,
                        content=raw_content,
                    )
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(
                        config, source, work_copy, step, run_log, llm, trajectory
                    )
                continue

            continuous_searches += 1
            note = _lookup_lemma(lookup_catalog, action.name)
            note = _annotate_search_note(config, continuous_searches, note)
            lookup_notes.append(note)
            stuck_counter = _increment_stuck(config, stuck_counter)
            trajectory.append(
                _step_record(
                    step,
                    goal,
                    action="lookup_lemma",
                    outcome="lookup",
                    lookup_name=action.name,
                    lookup_result=note,
                    thought=thought,
                    content=raw_content,
                )
            )
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
                return _stuck_result(
                    config, source, work_copy, step, run_log, llm, trajectory
                )
            continue

        if isinstance(action, SearchLemmasAction):
            consecutive_noop_undos = 0
            blocked = _retrieval_blocked_note(config, continuous_searches)
            if blocked is not None:
                note = blocked
                continuous_searches += 1
                stuck_counter = _increment_stuck(config, stuck_counter)
                lookup_notes.append(note)
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="search_lemmas",
                        outcome="search_limited",
                        search_query=action.query,
                        search_result=note,
                        thought=thought,
                        content=raw_content,
                    )
                )
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="search_lemmas",
                        outcome="search_limited",
                        search_query=action.query,
                        search_result=note,
                        thought=thought,
                        content=raw_content,
                    )
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(
                        config, source, work_copy, step, run_log, llm, trajectory
                    )
                continue

            continuous_searches += 1
            if action.mode == "semantic":
                if lemma_search_index is None:
                    # Reuse ranking embeddings when they already cover full Ax.all.
                    if len(lookup_catalog) == len(premise_index) and all(
                        name in premise_index for name in lookup_catalog
                    ):
                        lemma_search_index = {
                            name: premise_index[name] for name in lookup_catalog
                        }
                    else:
                        lemma_search_index = _build_lemma_search_index(
                            lookup_catalog, embedder
                        )
            note = _search_lemmas(
                lookup_catalog,
                embedder,
                lemma_search_index,
                action.query,
                mode=action.mode,
                top_k=config.lemma_search_top_k,
            )
            note = _annotate_search_note(config, continuous_searches, note)
            lookup_notes.append(note)
            stuck_counter = _increment_stuck(config, stuck_counter)
            trajectory.append(
                _step_record(
                    step,
                    goal,
                    action="search_lemmas",
                    outcome="search",
                    search_query=action.query,
                    search_result=note,
                    thought=thought,
                    content=raw_content,
                )
            )
            if run_log is not None:
                run_log.iteration(
                    step=step,
                    goal=goal,
                    top_premises=top,
                    ranked_scores=ranked,
                    action="search_lemmas",
                    outcome="search",
                    search_query=action.query,
                    search_result=note,
                    thought=thought,
                    content=raw_content,
                )
            if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                return _stuck_result(
                    config, source, work_copy, step, run_log, llm, trajectory
                )
            continue

        if isinstance(action, TacticAction):
            continuous_searches = 0
            consecutive_noop_undos = 0
            if _is_degenerate_tactic(action.tactic):
                logged_tactic = action.tactic[:200]
                errors.add(goal, _DEGENERATE_TACTIC_MESSAGE, logged_tactic)
                logger.info(
                    "Tactic rejected as degenerate (len=%d): %s",
                    len(action.tactic),
                    logged_tactic,
                )
                stuck_counter = _increment_stuck(config, stuck_counter)
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="tactic",
                        tactic=logged_tactic,
                        outcome="rejected",
                        error=_DEGENERATE_TACTIC_MESSAGE,
                        thought=thought,
                        content=raw_content,
                    )
                )
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
                    return _stuck_result(
                        config, source, work_copy, step, run_log, llm, trajectory
                    )
                continue

            if _BANNED_TACTIC_RE.search(action.tactic):
                errors.add(goal, _BANNED_TACTIC_MESSAGE, action.tactic)
                logger.info("Tactic rejected: %s", _BANNED_TACTIC_MESSAGE)
                stuck_counter = _increment_stuck(config, stuck_counter)
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="rejected",
                        error=_BANNED_TACTIC_MESSAGE,
                        thought=thought,
                        content=raw_content,
                    )
                )
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
                    return _stuck_result(
                        config, source, work_copy, step, run_log, llm, trajectory
                    )
                continue

            # Soft prompt warnings are not enough: frontier models still
            # resubmit the same failing tactic verbatim. Reject normalized
            # duplicates before spending another EasyCrypt call.
            if errors.has_failed(goal, action.tactic):
                errors.add(goal, _REPEAT_TACTIC_MESSAGE, action.tactic)
                logger.info("Tactic rejected as repeat: %s", action.tactic)
                stuck_counter = _increment_stuck(
                    config, stuck_counter, config.repeat_stuck_weight
                )
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="rejected",
                        error=_REPEAT_TACTIC_MESSAGE,
                        thought=thought,
                        content=raw_content,
                    )
                )
                if run_log is not None:
                    run_log.iteration(
                        step=step,
                        goal=goal,
                        top_premises=top,
                        ranked_scores=ranked,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="rejected",
                        error=_REPEAT_TACTIC_MESSAGE,
                        thought=thought,
                        content=raw_content,
                    )
                identical = _identical_fail_stuck_result(
                    config,
                    errors,
                    goal,
                    action.tactic,
                    source,
                    work_copy,
                    step,
                    run_log,
                    llm,
                    trajectory,
                )
                if identical is not None:
                    return identical
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(
                        config, source, work_copy, step, run_log, llm, trajectory
                    )
                continue

            inserted_line = proof.append_tactic(action.tactic)
            validation = validate_file(work_copy, config)
            if validation.returncode != 0:
                proof.remove_lines(inserted_line)
                error_msg = validation.stderr.strip() or validation.stdout.strip()
                diagnostic = _probe_prefix_subgoal(proof, action.tactic, config)
                error_msg = _enrich_error(
                    error_msg, action.tactic, goal, diagnostic=diagnostic
                )
                errors.add(goal, error_msg, action.tactic)
                logger.info("Tactic failed: %s", error_msg)
                stuck_counter = _increment_stuck(config, stuck_counter)
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="failed",
                        error=error_msg,
                        thought=thought,
                        content=raw_content,
                    )
                )
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
                identical = _identical_fail_stuck_result(
                    config,
                    errors,
                    goal,
                    action.tactic,
                    source,
                    work_copy,
                    step,
                    run_log,
                    llm,
                    trajectory,
                )
                if identical is not None:
                    return identical
                if _check_stuck(config, stuck_counter, run_log, work_copy, step):
                    return _stuck_result(
                        config, source, work_copy, step, run_log, llm, trajectory
                    )
                continue

            if is_proof_complete_at_cursor(proof, config):
                trajectory.append(
                    _step_record(
                        step,
                        goal,
                        action="tactic",
                        tactic=action.tactic,
                        outcome="complete",
                        thought=thought,
                        content=raw_content,
                    )
                )
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

            trajectory.append(
                _step_record(
                    step,
                    goal,
                    action="tactic",
                    tactic=action.tactic,
                    outcome="accepted",
                    thought=thought,
                    content=raw_content,
                )
            )
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
                return _stuck_result(
                    config, source, work_copy, step, run_log, llm, trajectory
                )

    result = AgentResult(
        reason=ExitReason.MAX_STEPS,
        message=f"Exceeded max steps ({config.max_steps})",
        steps=config.max_steps,
        work_copy=work_copy,
    )
    result.retrospective_file = _write_timeout_retrospective(
        config=config,
        source=source,
        work_copy=work_copy,
        result=result,
        llm=llm,
        trajectory=trajectory,
    )
    _log_finish(run_log, result)
    return result


def _step_record(
    step: int,
    goal: str,
    *,
    action: str,
    outcome: str,
    tactic: str | None = None,
    error: str | None = None,
    lookup_name: str | None = None,
    lookup_result: str | None = None,
    search_query: str | None = None,
    search_result: str | None = None,
    undo_count: int | None = None,
    undone: int | None = None,
    thought: str | None = None,
    content: str | None = None,
) -> dict:
    return {
        "step": step,
        "goal": goal,
        "action": action,
        "tactic": tactic,
        "lookup_name": lookup_name,
        "lookup_result": lookup_result,
        "search_query": search_query,
        "search_result": search_result,
        "undo_count": undo_count,
        "undone": undone,
        "outcome": outcome,
        "error": error,
        "thought": thought,
        "content": content,
    }


def _retrospective_context(trajectory: list[dict], limit: int) -> list[dict]:
    """Keep retrospective input useful without replaying every full response."""
    context: list[dict] = []
    recent = trajectory[-limit:] if limit > 0 else []
    for item in recent:
        compact = {
            key: value
            for key, value in item.items()
            if key not in {"content"} and value is not None
        }
        for key in (
            "goal",
            "error",
            "thought",
            "lookup_result",
            "search_result",
        ):
            value = compact.get(key)
            if isinstance(value, str) and len(value) > 4000:
                compact[key] = value[:4000] + "\n[truncated]"
        context.append(compact)
    return context


def _write_timeout_retrospective(
    *,
    config: AgentConfig,
    source: Path,
    work_copy: Path,
    result: AgentResult,
    llm: LlmClient,
    trajectory: list[dict],
) -> Path | None:
    if not config.right_fix or config.retrospective_file is None:
        return None

    payload = {
        "source": str(source),
        "work_copy": str(work_copy),
        "exit_reason": result.reason.name,
        "steps": result.steps,
        "right_fix": config.right_fix,
        "trajectory": trajectory,
        "response": None,
        "response_error": None,
    }
    try:
        payload["response"] = llm.retrospect(
            right_fix=config.right_fix,
            trajectory=_retrospective_context(trajectory, config.history_steps),
        )
    except Exception as exc:
        logger.exception("Timeout retrospective request failed")
        payload["response_error"] = str(exc)

    config.retrospective_file.parent.mkdir(parents=True, exist_ok=True)
    config.retrospective_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return config.retrospective_file


def _proof_state_hash(proof_tail: str) -> str:
    return hashlib.sha256(proof_tail.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Error enrichment
# ---------------------------------------------------------------------------

# Raw EasyCrypt error strings are often too terse for a model to self-correct
# from.  These patterns map known short messages to richer guidance that names
# the likely cause and suggests what to try next.
_ERROR_HINTS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"instruction list is not empty|left instruction list is not empty|right instruction list is not empty", re.IGNORECASE),
        (
            "The program still has statements — `skip` only applies to an empty "
            "instruction list. Continue with program-logic tactics that match the "
            "leading statements (`wp`, `while`, `unroll`, `call`, `inline`, `seq`, "
            "`if`, `rcondt`/`rcondf`). If a concrete callee is still opaque, try "
            "`inline *` before `wp`/`skip`."
        ),
    ),
    (
        re.compile(r"cannot prove goal \(strict\)", re.IGNORECASE),
        (
            "The SMT backend could not close this goal automatically. "
            "This usually means the goal is nonlinear (e.g. products, squares, "
            "exponents, or logs), involves a type the solver does not handle well, "
            "or simply needs a named lemma hint. "
            "Try: (1) supply a hint — smt(lemma_name). "
            "(2) if the goal is still in Hoare/program-logic form (you see "
            "'pre =' and 'post ='), apply 'wp.' then 'skip.' first, then retry smt(). "
            "(3) if the goal is already ambient but busy, try 'progress.' / "
            "'simplify.' once, then retry smt() or a rewrite. "
            "(4) introduce an intermediate fact with 'have h : ... by smt(). smt(h).'"
        ),
    ),
    (
        re.compile(r"InvalidGoalShape", re.IGNORECASE),
        (
            "The tactic requires a goal of a specific shape that the current goal "
            "does not match. Common causes: "
            "(1) 'algebra' and 'ring' only work on equalities, not inequalities — "
            "if your goal is an inequality use 'smt()' instead. "
            "(2) 'left'/'right' only work on disjunctions — check that the goal "
            "is actually a disjunction ('\\/'). "
            "(3) 'split' only works on conjunctions or existentials. "
            "(4) The goal may still be in Hoare/program-logic form; reduce it "
            "with 'wp.' or 'skip.' before applying ambient-logic tactics. "
            "(5) On an equiv goal, the leading statements may be asymmetric — "
            "try 'seq' / 'if' / 'rcondt'/'rcondf' rather than treating a shape "
            "error as 'the goal is ambient'."
        ),
    ),
    (
        re.compile(r"conclusion must be an equation", re.IGNORECASE),
        (
            "The 'ring' and 'field' tactics only discharge goals of the form "
            "'lhs = rhs'. Your goal is not an equality. "
            "If the goal is an inequality or implication, use 'smt()' instead. "
            "If the goal is in Hoare/program-logic form, apply 'wp.' and 'skip.' "
            "first to reduce it to an ambient-logic equality before using 'ring'."
        ),
    ),
    (
        re.compile(r"cannot apply `left` on that goal", re.IGNORECASE),
        (
            "The 'left' tactic only applies when the goal is a disjunction (A \\/ B). "
            "The current goal is not a disjunction. "
            "If you see 'pre = P' and 'post = Q' in the goal, the proof is still in "
            "Hoare/program-logic form — use 'wp.' then 'skip.' to reduce it to an "
            "ambient-logic goal before attempting propositional tactics."
        ),
    ),
    (
        re.compile(r"cannot apply `right` on that goal", re.IGNORECASE),
        (
            "The 'right' tactic only applies when the goal is a disjunction (A \\/ B). "
            "The current goal is not a disjunction. "
            "If the goal is in Hoare/program-logic form, reduce it first with "
            "'wp.' and 'skip.'."
        ),
    ),
    (
        re.compile(r"parse error|illegal character|syntax error", re.IGNORECASE),
        (
            "EasyCrypt could not parse the tactic. Common causes: "
            "(1) A bare semicolon with nothing after it — 'if;' or 'wp;' are parse "
            "errors. In EasyCrypt, ';' is a tactic combinator: 'if; auto.' means "
            "apply 'if', then 'auto' on every subgoal. The whole expression up to "
            "the final '.' is one tactic. Write 'if; auto.' not 'if;'. "
            "(2) Missing period at the end — every tactic must end with '.'. "
            "(3) Mismatched parentheses or brackets. "
            "(4) Invalid or misspelled identifiers — EasyCrypt is case-sensitive "
            "and module names must be capitalised. "
            "(5) Lean/Coq/Isabelle syntax used instead of EasyCrypt syntax."
        ),
    ),
    (
        re.compile(r"unknown lemma|not found|unbound", re.IGNORECASE),
        (
            "The lemma or identifier name was not found in the current scope. "
            "Check spelling and capitalisation — EasyCrypt is case-sensitive. "
            "Use lookup_lemma or search_lemmas with mode `substring`/`exact` "
            "(JSON name field) before applying a guessed lemma name. Semantic "
            "search alone often misses short identifiers. Prefer a short name "
            "token and optional `theory:Path` filter over repeated semantic queries."
        ),
    ),
]


def _enrich_error(
    raw_error: str,
    tactic: str,
    goal: str,
    *,
    diagnostic: str | None = None,
) -> str:
    """Augment a raw EasyCrypt error message with actionable guidance.

    Matches known terse error patterns and appends a hint explaining the
    likely cause and suggesting concrete next steps.  The original error
    text is preserved so the model still sees the precise message.
    """
    parts = [raw_error]
    hint = _hint_for_error(raw_error, tactic, goal)
    if hint:
        parts.append(f"[hint] {hint}")
    if diagnostic:
        parts.append(f"[diagnostic subgoal] {diagnostic}")
    return "\n".join(parts)


def _hint_for_error(raw_error: str, tactic: str, goal: str) -> str:
    """Return a hint string for a known error pattern, or empty string."""
    if _is_oneshot_while_compound(tactic):
        return _WHILE_ONESHOT_HINT
    if _expects_hl_judgment(raw_error):
        if goal_is_implication_before_hl(goal):
            return _IMPL_BEFORE_HL_HINT
        if goal_looks_program_logic(goal):
            return _HL_SHAPE_MISMATCH_HINT
        return _HL_ON_AMBIENT_HINT
    for pattern, hint in _ERROR_HINTS:
        if pattern.search(raw_error):
            return hint
    return ""


def _expects_hl_judgment(raw_error: str) -> bool:
    return bool(
        re.search(
            r"expecting a goal of the form:.*(?:hoare|ehoare|phoare|equiv)\s*\[",
            raw_error,
            re.IGNORECASE | re.DOTALL,
        )
    )


def _split_tactic_segments(tactic: str) -> list[str]:
    """Split a `;`-compound into atomic tactic strings (each ending with ``.``)."""
    text = tactic.strip()
    if text.endswith("."):
        text = text[:-1].rstrip()
    if ";" not in text:
        return []
    segments: list[str] = []
    for part in text.split(";"):
        piece = part.strip()
        if not piece:
            continue
        segments.append(piece if piece.endswith(".") else piece + ".")
    return segments if len(segments) >= 2 else []


def _first_tactic_prefix(tactic: str) -> str | None:
    """First segment of a `;`-compound tactic, or None if not compound."""
    segments = _split_tactic_segments(tactic)
    return segments[0] if segments else None


def _probe_prefix_subgoal(
    proof: ProofFile, tactic: str, config: AgentConfig
) -> str | None:
    """Replay a failed compound segment-by-segment and dump each open goal.

    Failed compounds are rolled back wholesale, so the model never sees
    intermediate states. Re-apply each ``;``-separated prefix on a temporary
    script extension, record the goal after every successful segment, and stop
    at the first segment that fails (including its EasyCrypt error).
    """
    segments = _split_tactic_segments(tactic)
    if not segments:
        return None

    inserted_lines: list[int] = []
    trace: list[str] = []
    try:
        for index, segment in enumerate(segments, start=1):
            inserted = proof.append_tactic(segment)
            inserted_lines.append(inserted)
            validation = validate_file(proof.path, config)
            if validation.returncode != 0:
                err = (
                    validation.stderr.strip() or validation.stdout.strip() or "failed"
                )
                # Keep the message short; the outer enrich already has the
                # full compound error.
                short = err.split("\n")[0]
                if "]" in short:
                    short = short.split("]", 1)[-1].strip()
                trace.append(
                    f"Step {index}/{len(segments)} `{segment}` FAILED: {short}"
                )
                break

            subgoal = resolve_goal(proof, config).strip()
            if not subgoal or is_proof_discharged(subgoal):
                trace.append(
                    f"Step {index}/{len(segments)} `{segment}` OK — "
                    "no open goal (discharged or empty)."
                )
                # Further segments are moot if nothing remains.
                if index < len(segments):
                    trace.append(
                        "Remaining segments were not applied (no open goal)."
                    )
                break

            trace.append(
                f"Step {index}/{len(segments)} `{segment}` OK — open subgoal:\n"
                f"{subgoal}"
            )
        else:
            # All segments validated individually, yet the compound failed —
            # often due to multi-goal combinator differences.
            trace.append(
                "All segments succeeded when applied one-by-one; the compound "
                "`;` combinator may be distributing differently across "
                "subgoals. Prefer submitting atomic tactics."
            )
    except Exception:
        logger.exception("Compound subgoal probe failed for %r", tactic)
        return None
    finally:
        # Roll back in reverse so line numbers stay valid.
        for line in reversed(inserted_lines):
            try:
                proof.remove_lines(line)
            except Exception:
                logger.exception("Failed to roll back compound probe line %s", line)

    if not trace:
        return None
    return "\n".join(trace)

def _search_budget_warning(config: AgentConfig, continuous_searches: int) -> str | None:
    limit = config.max_continuous_searches
    if limit is None:
        return None
    # Warn before the next retrieval when the upcoming call would be the
    # penultimate allowed search (4th when limit=5).
    next_count = continuous_searches + 1
    if next_count == limit - 1:
        return (
            f"WARNING: this would be consecutive retrieval #{next_count} of "
            f"{limit}. One more lookup/search is allowed after this; then you "
            "MUST attempt a tactic (or undo). Prefer substring/prefix/exact "
            "modes if semantic search is not finding the lemma name."
        )
    if next_count >= limit:
        return (
            f"WARNING: consecutive retrieval budget is {limit}. Further "
            "lookup/search will be rejected until you attempt a tactic or undo."
        )
    return None


def _retrieval_blocked_note(
    config: AgentConfig, continuous_searches: int
) -> str | None:
    limit = config.max_continuous_searches
    if limit is None:
        return None
    # Allow searches while continuous_searches < limit (0..4 => five searches).
    if continuous_searches >= limit:
        return _SEARCH_LIMIT_REJECT
    return None


def _annotate_search_note(
    config: AgentConfig, continuous_searches: int, note: str
) -> str:
    limit = config.max_continuous_searches
    if limit is None:
        return note
    if continuous_searches == limit - 1:
        return (
            f"{note}\n[search-budget] WARNING: consecutive retrieval "
            f"#{continuous_searches}/{limit}. One more lookup/search is allowed; "
            "then you must attempt a tactic or undo."
        )
    if continuous_searches >= limit:
        return (
            f"{note}\n[search-budget] Consecutive retrieval limit reached "
            f"({continuous_searches}/{limit}). Next lookup/search will be rejected "
            "until you attempt a tactic or undo."
        )
    return note


def _increment_stuck(
    config: AgentConfig, stuck_counter: int, amount: int = 1
) -> int:
    if config.stuck_limit is None:
        return stuck_counter
    return stuck_counter + amount


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


def _identical_fail_stuck_result(
    config: AgentConfig,
    errors: ErrorHistory,
    goal: str,
    tactic: str,
    source: Path,
    work_copy: Path,
    step: int,
    run_log: AgentRunLog | None,
    llm: LlmClient,
    trajectory: list[dict],
) -> AgentResult | None:
    limit = config.identical_fail_limit
    if limit is None:
        return None
    count = errors.failure_count(goal, tactic)
    if count < limit:
        return None
    return _stuck_result(
        config,
        source,
        work_copy,
        step,
        run_log,
        llm,
        trajectory,
        message=(
            f"Agent stuck: same tactic failed {count} times at this goal "
            f"(identical_fail_limit={limit})"
        ),
    )


def _stuck_result(
    config: AgentConfig,
    source: Path,
    work_copy: Path,
    step: int,
    run_log: AgentRunLog | None,
    llm: LlmClient,
    trajectory: list[dict],
    message: str | None = None,
) -> AgentResult:
    limit = config.stuck_limit or 0
    result = AgentResult(
        reason=ExitReason.STUCK,
        message=message
        or f"Agent stuck after {limit} unproductive iterations",
        steps=step,
        work_copy=work_copy,
    )
    result.retrospective_file = _write_timeout_retrospective(
        config=config,
        source=source,
        work_copy=work_copy,
        result=result,
        llm=llm,
        trajectory=trajectory,
    )
    _log_finish(run_log, result)
    return result


def _lookup_lemma(catalog: dict[str, str], name: str) -> str:
    """Resolve a lookup against EasyCrypt-qualified catalog keys.

    Accepts a fully qualified path, a bare basename, or an optional
    ``theory:...`` filter in the name field (same syntax as search).
    """
    theory, residual = split_theory_filter(name)
    scoped = filter_catalog_by_theory(catalog, theory)
    scope_note = f" [theory:{theory}]" if theory else ""
    query = residual.strip()
    if not query:
        if not theory:
            return f"{name}: not found"
        keys = sorted(scoped)[:20]
        if not keys:
            return f"{name}: no lemmas under theory filter `{theory}`"
        lines = [f"{name}: lemmas under theory `{theory}` (top {len(keys)}):"]
        for key in keys:
            lines.append(f"- {key}: {scoped[key]}")
        return "\n".join(lines)

    if query in scoped:
        return f"{query}{scope_note}: {scoped[query]}"
    lower = query.lower()
    for key, sig in scoped.items():
        if key.lower() == lower:
            return f"{key}{scope_note}: {sig}"

    basename_hits = [
        key
        for key in scoped
        if lemma_ref_from_key(key).name.lower() == lower
    ]
    if len(basename_hits) == 1:
        key = basename_hits[0]
        return f"{key}{scope_note}: {scoped[key]}"
    if basename_hits:
        shown = sorted(basename_hits)[:10]
        return (
            f"{query}{scope_note}: ambiguous basename "
            f"({', '.join(shown)}"
            f"{', ...' if len(basename_hits) > len(shown) else ''})"
        )

    partial = [key for key in scoped if lower in key.lower()]
    if len(partial) == 1:
        key = partial[0]
        return f"{key}{scope_note}: {scoped[key]}"
    if partial:
        return (
            f"{query}{scope_note}: ambiguous "
            f"({', '.join(sorted(partial)[:5])})"
        )
    return f"{query}{scope_note}: not found"


def _search_lemmas(
    catalog: dict[str, str],
    embedder: EmbeddingClient | None,
    search_index: dict[str, np.ndarray] | None,
    query: str,
    *,
    top_k: int,
    mode: str = "semantic",
) -> str:
    return run_lemma_search(
        catalog,
        embedder,
        search_index,
        query,
        mode=mode,
        top_k=top_k,
    )


def _build_lemma_search_index(
    catalog: dict[str, str],
    embedder: EmbeddingClient,
) -> dict[str, np.ndarray]:
    """Build lazily and reuse one index per (model, catalog) digest."""
    digest = hashlib.sha256()
    for name in sorted(catalog):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(catalog[name].encode("utf-8"))
        digest.update(b"\0")
    key = (embedder._resolve_model(), digest.hexdigest())
    cached = _LEMMA_SEARCH_INDEX_CACHE.get(key)
    if cached is None:
        cached = embedder.build_index(catalog)
        _LEMMA_SEARCH_INDEX_CACHE[key] = cached
    return cached


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
) -> tuple[dict[str, np.ndarray], dict[str, str], dict[str, str]]:
    """Build ranking embeddings plus the Ax.all lookup catalog.

    Returns ``(ranking_index, ranking_catalog, lookup_catalog)`` where
    ``lookup_catalog`` is always EasyCrypt ``Ax.all`` at the goal cursor
    (open/unsolved lemmas are not included), and ``ranking_catalog`` is either
    ``premises_override`` or that same Ax.all set (optionally truncated by
    ``max_premises`` for the prompt top-k section).
    """
    bounds = proof.bounds()
    cursor = resolve_goal_cursor(proof, config)
    result = fetch_goal_and_premises(proof.path, cursor, config)
    split = split_goal_and_premises(result.stdout)
    lookup_catalog = parse_premises(split.premises)

    if config.premises_override is not None:
        ranking_catalog = dict(config.premises_override)
    else:
        ranking_catalog = lookup_catalog
    if config.max_premises is not None:
        ranking_catalog = dict(list(ranking_catalog.items())[: config.max_premises])

    embedder = EmbeddingClient(config)
    model = embedder._resolve_model()
    decl_count = proof.count_declarations_before(bounds.last_line)

    cached = load_cached_embeddings(
        proof.path, cursor, model, decl_count
    )
    if cached is not None and set(cached) == set(ranking_catalog):
        return (
            {name: np.asarray(vec) for name, vec in cached.items()},
            ranking_catalog,
            lookup_catalog,
        )

    index = embedder.build_index(ranking_catalog)
    save_cached_embeddings(
        proof.path,
        cursor,
        model,
        decl_count,
        ranking_catalog,
        {name: vec.tolist() for name, vec in index.items()},
    )
    return index, ranking_catalog, lookup_catalog


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

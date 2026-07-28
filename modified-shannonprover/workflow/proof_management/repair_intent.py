"""Repair-mode session bootstrap: replay an existing proof until it breaks.

Proof-repair mode (see ``workflow/repair.py``) reuses the ordinary session
start path (``ReplSessionManager.start()`` -> ``-start -f <file> -lemma
<lemma>``, unchanged) to open the target lemma at the empty goal against the
currently-installed EasyCrypt. ``run_repair_bootstrap`` then replays the
target's ORIGINAL (outdated) tactic script one tactic at a time through the
manager's existing single-commit primitive -- the same path
``handle_intent``'s ``commit_tactic`` branch already uses -- stopping at the
first tactic that no longer applies.

``commit_tactic`` always exits 0 from the backend regardless of whether
EasyCrypt accepted the tactic (``core/easycrypt/commands/commit_commands.py
handle_next`` unconditionally ``return 0``s; failure is recorded in the
``[TACTIC-EXECUTION-RESULT]`` payload, not the process exit code). So, like
``-chain``'s own failure detection (``handle_chain``, same module), this
replay loop does not trust ``ReplBackendError``/exit codes for per-tactic
outcome -- it uses the same ground-truth signal ``-chain`` uses: whether
``history.ec``'s committed-tactic count actually grew.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.easycrypt.lemma_extract import extract_original_proof_tactics
from core.easycrypt.repair_hints import emit_repair_hints

from .protocol_repair import AgentIntent
from .repl_session import (
    ReplBackendError,
    ReplBackendTimeout,
    ReplSessionManager,
    session_dir_path,
)


def _split_tactics(tactic_text: str) -> list[str]:
    """Split a tactic script into individual tactics.

    Same rule ``handle_chain`` uses (``core/easycrypt/commands/commit_commands.py``):
    split on ``.<whitespace>`` to preserve dots inside identifiers (e.g.
    ``G1.bad``), re-appending the trailing ``.`` to each piece.
    """
    tactics: list[str] = []
    for part in re.split(r"\.\s", tactic_text.strip()):
        part = part.strip().rstrip(".")
        if part:
            tactics.append(part + ".")
    return tactics


def _last_error_summary(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return ""
    observation = actions[-1].get("agent_observation")
    if isinstance(observation, dict):
        return str(observation.get("error_summary") or "")
    return ""


def run_repair_bootstrap(
    manager: ReplSessionManager,
    *,
    original_proof_file: Path,
    source_ec_version: str,
    target_ec_version: str,
    decl_line: int | None = None,
) -> dict[str, Any]:
    """Open ``manager``'s session and chain-replay the lemma's original proof.

    ``original_proof_file`` must still have the lemma's INTACT original proof
    body (i.e. not run through ``eval_source_prep``'s admit-stripping) --
    ``manager.file_path`` itself may already be stripped by the time this
    runs, since both the eval pipeline and the normal ``-start`` path strip
    proof bodies before a session ever sees them.

    Returns ``{accepted_count, total_count, failed_tactic,
    failed_tactic_index, raw_error, fully_replayed}``. On a failure (i.e.
    ``fully_replayed`` is False), eagerly emits a ``repair_hints`` ToolView
    (source="repair_bootstrap") so the very first ``ProverWorkspaceView`` the
    agent sees already carries changelog/repair_doc facts for the failing
    step, and records repair state on ``manager`` so the on-demand
    ``repair_hints`` intent can default from it on follow-up calls.
    """
    manager.start()

    original_tactics_text = extract_original_proof_tactics(
        original_proof_file, manager.lemma_name, decl_line=decl_line,
    )
    tactics = _split_tactics(original_tactics_text)

    session_dir = session_dir_path(manager.session_dir, manager.project_root)
    accepted_count = 0
    failed_tactic = ""
    failed_tactic_index = -1
    raw_error = ""

    for index, tactic in enumerate(tactics):
        before = manager.committed_history()
        try:
            _snapshot, actions = manager.handle_intent(
                AgentIntent(intent="commit_tactic", payload={"tactic": tactic}),
            )
        except (ReplBackendError, ReplBackendTimeout) as exc:
            failed_tactic = tactic
            failed_tactic_index = index
            raw_error = str(exc)
            break

        after = manager.committed_history()
        if len(after) <= len(before):
            # Ground truth, same signal -chain uses: EasyCrypt either
            # rejected the tactic outright or accepted-then-auto-reverted it
            # as no-progress -- either way nothing new landed in history.ec.
            failed_tactic = tactic
            failed_tactic_index = index
            raw_error = _last_error_summary(actions)
            break
        accepted_count += 1

    fully_replayed = accepted_count == len(tactics)

    if not fully_replayed:
        manager.record_repair_state(
            failing_tactic=failed_tactic,
            ec_error_text=raw_error,
            source_ec_version=source_ec_version,
            target_ec_version=target_ec_version,
        )
        emit_repair_hints(
            session_dir,
            failing_tactic_text=failed_tactic,
            ec_error_text=raw_error,
            source_ec_version=source_ec_version,
            target_ec_version=target_ec_version,
            source="repair_bootstrap",
        )

    return {
        "accepted_count": accepted_count,
        "total_count": len(tactics),
        "failed_tactic": failed_tactic,
        "failed_tactic_index": failed_tactic_index,
        "raw_error": raw_error,
        "fully_replayed": fully_replayed,
    }

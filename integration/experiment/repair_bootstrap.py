"""Replay-until-failure repair trial mode.

Ported concept from shannon-prover's chain-replay bootstrap
(``workflow/proof_management/repair_intent.py``), reimplemented against this
project's own primitives instead of that project's session/EC-daemon
machinery (which isn't compatible with this project's patched EasyCrypt
fork -- see the module docstring on ``integration/agent/repair_hints.py``).

Unlike ``run_broken_formal_trial`` (``integration/experiment/runner.py``),
which admits every tactic in the target lemma and asks the solver to
reconstruct the whole proof from scratch, this mode replays the corpus's own
ORIGINAL tactic script one tactic at a time -- via
``ProofFile.append_tactic`` + ``validate_file`` (the exact primitives
``integration/agent/loop.py``'s own per-step commit loop already uses,
confirmed at ``loop.py:703-713``: ``validate_file``'s ``returncode != 0`` is
a trustworthy per-tactic failure signal here, unlike shannon-prover's own
backend, which always exits 0 regardless of EasyCrypt-level acceptance) --
preserving whatever prefix of the original proof still applies against the
current (patched-fork) EasyCrypt build. The solver only has to pick up at
the first tactic that no longer works, with changelog/repair_doc hints
(``integration/agent/repair_hints.py``) surfaced for that specific failure.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path

from integration.agent import run_agent
from integration.agent.config import AgentConfig
from integration.agent.easycrypt import fetch_goal_and_premises, has_open_goals, validate_file
from integration.agent.proof_file import ProofFile, create_working_copy
from integration.agent.repair_hints import get_repair_hints_text
from integration.experiment.config import ExperimentConfig
from integration.experiment.proof_extract import apply_lines, strip_tactics
from integration.experiment.protocols import ProofCase, ReplayBootstrapConfig
from integration.experiment.runner import TrialResult, TokenUsage, _cost_for_usage


_TACTIC_SPLIT_RE = re.compile(r"\.\s")


def _original_tactics(case: ProofCase, lines: list[str]) -> list[str]:
    """The lemma's own original tactics, `.`-terminated and re-split from
    the raw tactic-line range rather than assumed one-per-physical-line: a
    single EasyCrypt tactic can wrap across lines, or multiple tactics can
    share one line, so naive physical-line splitting would apply an
    incomplete fragment to ProofFile.append_tactic and falsely report the
    first split point as a failure even when the original proof is fine.
    Same splitting rule shannon-prover's chain-replay bootstrap uses: split
    on '.<whitespace>' (preserves dots inside identifiers like `G1.bad`),
    re-appending the trailing '.' to each piece."""
    block = "\n".join(lines[i - 1] for i in case.tactic_lines)
    tactics: list[str] = []
    for part in _TACTIC_SPLIT_RE.split(block.strip()):
        part = part.strip().rstrip(".")
        if part:
            tactics.append(part + ".")
    return tactics


def run_replay_bootstrap_trial(
    trial_id: int,
    case: ProofCase,
    config: ExperimentConfig,
    replay_config: ReplayBootstrapConfig,
    trial_dir: Path,
) -> TrialResult:
    """Replay ``case``'s original tactic script tactic-by-tactic against the
    current EasyCrypt build, stopping at the first tactic that no longer
    applies. If the whole script still replays, reports success with zero
    LLM calls (matches shannon-prover's ``fully_replayed`` cheap-win case).
    On the first failure, fetches changelog/repair_doc hints for that
    specific tactic and hands off to the existing solver loop starting from
    the partially-replayed proof state, not an admitted/empty goal.
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
            mode="replay_bootstrap",
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
    tactics = _original_tactics(case, lines)

    start_lines = strip_tactics(lines, case.tactic_lines)
    agent_start = trial_dir / "agent_start.ec"
    apply_lines(agent_start, start_lines)

    work_copy = trial_dir / "agent_work.agent.ec"
    create_working_copy(agent_start, work_copy=work_copy)
    proof = ProofFile(work_copy)

    start = time.monotonic()
    accepted_count = 0
    failed_tactic = ""
    raw_error = ""
    for tactic in tactics:
        inserted_line = proof.append_tactic(tactic)
        validation = validate_file(work_copy, agent_config)
        if validation.returncode != 0:
            proof.remove_lines(inserted_line)
            failed_tactic = tactic
            raw_error = validation.stderr.strip() or validation.stdout.strip()
            break
        accepted_count += 1

    fully_replayed = accepted_count == len(tactics)
    (trial_dir / "bootstrap_result.json").write_text(
        json.dumps(
            {
                "accepted_count": accepted_count,
                "total_count": len(tactics),
                "failed_tactic": failed_tactic,
                "fully_replayed": fully_replayed,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if fully_replayed:
        duration = time.monotonic() - start
        return TrialResult(
            trial_id=trial_id,
            name=case.name,
            source_file=str(case.file),
            mode="replay_bootstrap",
            mutations_applied=[],
            steps=0,
            reason="COMPLETE",
            message=(
                f"Original proof replayed verbatim against the current "
                f"EasyCrypt build ({accepted_count}/{len(tactics)} tactics) "
                f"-- nothing to repair."
            ),
            duration_s=round(duration, 3),
            token_usage=usage,
            estimated_cost=_cost_for_usage(usage, agent_config),
        )

    changelog_hints, hint_notes, matched_version = get_repair_hints_text(
        failing_tactic_text=failed_tactic,
        ec_error_text=raw_error,
        source_ec_version=replay_config.source_ec_version,
        target_ec_version=replay_config.target_ec_version,
    )
    if hint_notes:
        (trial_dir / "repair_hints_notes.json").write_text(
            json.dumps(hint_notes, indent=2), encoding="utf-8",
        )
    # Which release the changelog hop landed on (or null if none matched) --
    # visible for debugging without re-deriving it from the prompt text. A
    # later failure within the same trial that wants to advance past this
    # release would pass {matched_version} in already_consumed_versions on
    # its own get_repair_hints_text call.
    (trial_dir / "repair_hints_hop.json").write_text(
        json.dumps({"changelog_hop_matched_version": matched_version}, indent=2),
        encoding="utf-8",
    )

    trial_agent_config = replace(
        agent_config,
        repair_hint=None,
        changelog_hints=changelog_hints or None,
        informal_proof=None,
        premises_override=None,
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
        right_fix=None,
        retrospective_file=trial_dir / "timeout_retrospective.json",
    )

    # work_copy already exists (holds the replayed-so-far prefix), so
    # run_agent's own "elif not work_copy.exists()" branch is skipped and
    # the solver loop continues from exactly where the bootstrap left off,
    # not from agent_start's admitted/empty goal.
    result = run_agent(agent_start, trial_agent_config, work_copy=work_copy)
    duration = time.monotonic() - start

    return TrialResult(
        trial_id=trial_id,
        name=case.name,
        source_file=str(case.file),
        mode="replay_bootstrap",
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

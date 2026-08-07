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
import logging
import re
import time
from dataclasses import replace
from pathlib import Path

from integration.agent import run_agent
from integration.agent.config import AgentConfig
from integration.agent.easycrypt import fetch_goal_and_premises, has_open_goals, validate_file
from integration.agent.ec_errors import classify_error, strip_warning_lines
from integration.agent.ec_version import resolve_version_window
from integration.agent.import_repair import format_for_prompt, repair_imports
from integration.agent.proof_file import ProofFile, create_working_copy
from integration.agent.repair_hints import get_repair_hints_text
from integration.experiment.config import ExperimentConfig
from integration.experiment.proof_extract import apply_lines, strip_tactics
from integration.experiment.protocols import ProofCase, ReplayBootstrapConfig
from integration.experiment.runner import TrialResult, TokenUsage, _cost_for_usage
from integration.experiment.verify import is_proof_complete

logger = logging.getLogger(__name__)


_TACTIC_SPLIT_RE = re.compile(r"\.\s")


def _cataloged_releases() -> list[str] | None:
    """Release tags the changelog actually has entries for.

    Version detection snaps onto this list so it can never report a release
    the knowledge base cannot reason about -- the vendored fork carries tags
    (r2026.06, r2026.07, ...) that the catalog may not cover. Returns None if
    proof_corpus is unreachable, which leaves detection unsnapped rather than
    failing the trial: repair hints are supplementary context, never a
    precondition.
    """
    try:
        from integration.agent.repair_hints import (
            _load_retrieve_entries_module,
            resolve_changelog_path,
        )

        module = _load_retrieve_entries_module()
        changelog = module.load_changelog(str(resolve_changelog_path()))
        versions = [
            release.get("version")
            for release in changelog.get("releases", [])
            if release.get("version")
        ]
        return versions or None
    except Exception:
        return None


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


def _oldest_cataloged_release() -> str:
    """Oldest release the changelog covers, for an unknown source version.

    Falls back to an empty string only when proof_corpus is unreachable, in
    which case retrieve_entries fails open on its own.
    """
    releases = _cataloged_releases()
    return releases[0] if releases else ""


def _hop_releases(source: str | None, target: str | None) -> list[str]:
    """Cataloged releases in ``[source, target]``, oldest first.

    Bounded by the catalog on purpose. The fork carries tags the changelog has
    no entries for, and localizing a break to a release the knowledge base
    cannot describe buys nothing -- the whole point of narrowing is to make a
    changelog lookup more precise.
    """
    releases = _cataloged_releases() or []
    if not releases:
        return []
    low = releases.index(source) if source in releases else 0
    high = releases.index(target) if target in releases else len(releases) - 1
    if low > high:
        low, high = high, low
    return releases[low: high + 1]


def _run_version_hop(
    *,
    hop_path: Path,
    trial_dir: Path,
    agent_config: AgentConfig,
    source_version: str | None,
    target_version: str | None,
    strategy: str,
):
    """Localize the break to one release, or return None and change nothing.

    Every failure mode here -- no catalog, nothing importable, a release that
    will not compile, an unreadable registry -- degrades to "we did not learn
    anything", never to a failed trial. Version hopping is a precision
    improvement on an optional hint lookup; it has no business ending a repair
    that would otherwise have run.
    """
    releases = _hop_releases(source_version, target_version)
    if len(releases) < 2:
        return None
    try:
        from integration.experiment.ec_versions import EcVersionProvisioner
        from integration.experiment.version_hop import find_break_version
    except ImportError as exc:  # pragma: no cover - defensive
        logger.warning("version hop unavailable: %s", exc)
        return None

    try:
        result = find_break_version(
            file_path=hop_path,
            versions=releases,
            config=agent_config,
            provisioner=EcVersionProvisioner(),
            strategy=strategy,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("version hop failed: %s", exc)
        return None

    (trial_dir / "version_hop.json").write_text(
        json.dumps(result.as_dict(), indent=2), encoding="utf-8",
    )
    if result.localized:
        logger.info(
            "Version hop: tactic held at %s, broke at %s (%d build(s))",
            result.last_good, result.first_broken, result.builds,
        )
    return result


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

    # W6: the spec's version pair is a declared default, not ground truth. The
    # target is genuinely knowable (ask the installed binary's source tree) and
    # was previously hardcoded one release AHEAD of the build actually
    # installed here; the source usually is not knowable and stays fail-open.
    # Detection only fills in what the spec left unset, so an explicitly
    # narrowed spec still wins.
    source_version, target_version, version_provenance = resolve_version_window(
        corpus_path=case.file,
        easycrypt_bin=agent_config.easycrypt_bin,
        source_override=replay_config.source_ec_version,
        target_override=replay_config.target_ec_version,
        known_versions=_cataloged_releases(),
    )
    (trial_dir / "ec_versions.json").write_text(
        json.dumps(version_provenance, indent=2), encoding="utf-8",
    )

    goal_result = fetch_goal_and_premises(case.file, case.proof_start_line, agent_config)
    import_repair_summary: str | None = None
    if goal_result.returncode != 0 or not has_open_goals(goal_result.stdout):
        # An unreachable goal is very often not a proof problem at all: the
        # file failed to LOAD, because a `require import` no longer resolves or
        # the file uses syntax modern EasyCrypt no longer parses. Previously
        # every such case was discarded here as `goal_unreachable`, which threw
        # away exactly the trials the changelog knowledge base is best at.
        # Try a verified, line-preserving import repair before giving up.
        repair = repair_imports(
            case.file,
            agent_config,
            source_version=source_version,
            target_version=target_version,
            work_path=trial_dir / "import_repaired.ec",
        )
        (trial_dir / "import_repair.json").write_text(
            json.dumps(repair.to_dict(), indent=2), encoding="utf-8",
        )
        if repair.changed and repair.improved:
            # Line-preserving by construction, so case.proof_start_line and
            # every other recorded line number still point at the right thing.
            case.file.write_text(repair.text, encoding="utf-8")
            import_repair_summary = format_for_prompt(repair) or None
            goal_result = fetch_goal_and_premises(
                case.file, case.proof_start_line, agent_config
            )

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
                "and attempting import repair "
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
    # The file exactly as it stood when the tactic failed -- accepted prefix
    # plus the failing tactic. Snapshotted before the undo because that is the
    # file the version hop has to re-check: "which release did THIS tactic stop
    # working at" is unanswerable against a file the tactic has been taken out
    # of. Costs one write on the failure path and nothing on the happy one.
    hop_path: Path | None = None
    for tactic in tactics:
        inserted_line = proof.append_tactic(tactic)
        validation = validate_file(work_copy, agent_config)
        if validation.returncode != 0:
            hop_path = trial_dir / "version_hop_input.ec"
            hop_path.write_bytes(work_copy.read_bytes())
            proof.remove_lines(inserted_line)
            failed_tactic = tactic
            raw_error = validation.stderr.strip() or validation.stdout.strip()
            break
        accepted_count += 1

    # Every tactic applied AND the proof actually closes. The second half is
    # not redundant: `validate_file` runs `llm -lastgoals`, whose exit 0 means
    # the tactics PARSED, not that the goal was discharged -- the distinction
    # this repo already pins in
    # `test_goal_state.py::test_validate_file_success_does_not_imply_complete`.
    #
    # LQ1's `sampling_bound` is exactly that case: all 5 original tactics
    # replay with returncode 0 while `is_proof_complete` is False. Counting it
    # as fully replayed reported a proof that does not close as COMPLETE with
    # steps=0 and skipped the agent entirely -- a false success, and the reason
    # a replay spec on that corpus appeared to have nothing to repair.
    fully_replayed = accepted_count == len(tactics) and is_proof_complete(
        work_copy, agent_config
    )
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

    # Classify before retrieving: the bootstrap's first failure is just as
    # likely to be proof-level as import-level, and the two want different
    # evidence (integration/agent/ec_errors.py).
    failure_kind = classify_error(raw_error).kind

    # An undetected source means "consider every release". Spell that as the
    # OLDEST cataloged release rather than an empty string: both fail open to
    # the full range, but the empty string does it by tripping
    # retrieve_entries' unknown-version warning on every single lookup.
    hint_source = source_version or _oldest_cataloged_release()
    hint_target = target_version or ""

    # W7: narrow that span to the one release that actually broke the tactic,
    # verified by re-running it against each release's own binary. Opt-in, and
    # fail-open by construction -- an unlocalized hop leaves the endpoints
    # exactly as they were.
    if replay_config.version_hop and hop_path is not None:
        hop = _run_version_hop(
            hop_path=hop_path,
            trial_dir=trial_dir,
            agent_config=agent_config,
            source_version=hint_source,
            target_version=target_version,
            strategy=replay_config.version_hop_strategy,
        )
        if hop is not None and hop.changelog_range:
            hint_source, hint_target = hop.changelog_range

    if replay_config.changelog_hints:
        changelog_hints, hint_notes, matched_version = get_repair_hints_text(
            failing_tactic_text=failed_tactic,
            ec_error_text=raw_error,
            error_kind=failure_kind,
            source_ec_version=hint_source,
            target_ec_version=hint_target,
        )
    else:
        # The hints-off arm. Retrieval is skipped entirely rather than
        # retrieved-and-discarded: the point is a run the knowledge base did
        # not touch, and a retrieval pass that happens anyway would still cost
        # the time it costs and still show up in the run's timings.
        changelog_hints, hint_notes, matched_version = "", [
            "changelog hints disabled for this run (hints-off A/B arm)"
        ], None
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
        json.dumps(
            {
                "changelog_hop_matched_version": matched_version,
                "failure_kind": failure_kind,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # The solver is proving against a file this harness edited, so it has to be
    # told what changed -- otherwise a tactic referencing a renamed symbol looks
    # inexplicably wrong. Prepended to the same prompt section as the changelog
    # facts, since it is the same kind of evidence (a dated library change),
    # just one that has already been acted on.
    if import_repair_summary:
        changelog_hints = (
            f"{import_repair_summary}\n\n{changelog_hints}"
            if changelog_hints else import_repair_summary
        )

    # Persist the rendered block verbatim. It is both the audit record of what
    # the model was actually told and the input `repair_metrics.hint_uptake`
    # scores accepted tactics against -- reconstructing it later from the
    # changelog would not reproduce what this trial really saw.
    if changelog_hints:
        (trial_dir / "changelog_hints.txt").write_text(
            changelog_hints, encoding="utf-8"
        )

    # The original proof from the break onward. `tactics[accepted_count]` is the
    # step that just failed; everything after it was never reached and is
    # therefore UNTESTED against the current build, not known-broken -- the
    # heading says exactly that, because telling the model these "do not
    # compile" would invite it to discard the structure it most needs.
    remaining_original = tactics[accepted_count:]
    remaining_text: str | None = None
    if replay_config.show_remaining_original and remaining_original:
        remaining_text = "\n".join(remaining_original)
        (trial_dir / "informal_proof.md").write_text(
            remaining_text + "\n", encoding="utf-8"
        )

    trial_agent_config = replace(
        agent_config,
        repair_hint=None,
        # The tactic to repair, handed over as a task rather than as reference
        # material -- see prompt.format_broken_tactic_repair.
        broken_tactic=failed_tactic or None,
        broken_tactic_error=(strip_warning_lines(raw_error) or None)
        if raw_error else None,
        # The tactics already in `work_copy`. They compiled against the current
        # build during replay, so they are verified work; without this the
        # agent was bulk-undoing them (see ProofFile.protected_prefix).
        replayed_prefix=accepted_count,
        changelog_hints=changelog_hints or None,
        informal_proof=remaining_text,
        informal_proof_is_formal=True,
        informal_proof_heading=(
            "## Original proof from this point (reference — the FIRST line "
            "below is the tactic that just failed against the current "
            "EasyCrypt; the lines after it were never reached, so they are "
            "untested rather than known-broken. Use them for intended "
            "structure and invariants; adapt rather than paste)"
        ),
        premises_override=None,
        stuck_limit=config.stuck_limit,
        log_file=trial_dir / "agent_log.json",
        output_dir=trial_dir,
        right_fix=None,
        retrospective_file=trial_dir / "timeout_retrospective.json",
        # W3: let the loop re-aim the changelog block at each NEW failure and
        # hop past releases already shown, rather than proving the rest of the
        # script against hints fetched for the first broken tactic only. Off in
        # the hints-off arm: a run whose bootstrap hints were suppressed but
        # which then refetched them on the next failure is not a hints-off run.
        live_changelog_hints=replay_config.changelog_hints,
        source_ec_version=source_version,
        target_ec_version=target_version,
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

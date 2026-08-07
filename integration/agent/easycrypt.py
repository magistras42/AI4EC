"""Subprocess wrappers around the patched EasyCrypt `llm` subcommand."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import NO_ACTIVE_PROOF, NO_MORE_GOALS, PREMISES_SEPARATOR, AgentConfig
from .proof_file import ProofFile, _normalize_tactic_line


@dataclass(frozen=True)
class LlmResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GoalAndPremises:
    goal: str
    premises: str


def run_llm(args: list[str], config: AgentConfig) -> LlmResult:
    cmd = [str(config.easycrypt_bin), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=config.easycrypt_timeout,
    )
    return LlmResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def fetch_goal(file_path: Path, cursor_upto: int, config: AgentConfig) -> LlmResult:
    return run_llm(["llm", "-upto", str(cursor_upto), str(file_path)], config)


def fetch_goal_and_premises(
    file_path: Path, cursor_upto: int, config: AgentConfig
) -> LlmResult:
    return run_llm(
        ["llm", "-upto", str(cursor_upto), "-premises", str(file_path)],
        config,
    )


def validate_file(file_path: Path, config: AgentConfig) -> LlmResult:
    return run_llm(["llm", "-lastgoals", str(file_path)], config)


def check_file_compat(file_path: Path, config: AgentConfig) -> LlmResult:
    """Does this file check out? Works on binaries without the `llm` command.

    `llm` is fork-local, added in `da4935c9` (2026-04-11), so **11 of the 14
    buildable release tags do not have it** and `validate_file` cannot run
    against them at all. That blocked version hopping entirely.

    But hopping never needed the goal printer. `version_hop.probe_version`
    asks one question -- does this file still check out at release R -- and
    upstream `compile` answers it at every release. Verified against both
    binaries: `llm -lastgoals` and `compile` return the same code on a passing
    and on a failing proof with the modern build, and `compile` works on the
    r2025.02 build where `llm` is an unknown option.

    Used only on the hop path. The agent loop keeps `validate_file`, whose
    stdout it also reads for goal text.
    """
    return run_llm(["compile", str(file_path)], config)


def split_goal_and_premises(stdout: str) -> GoalAndPremises:
    marker = PREMISES_SEPARATOR + "\n"
    if marker in stdout:
        goal, premises = stdout.split(marker, 1)
        return GoalAndPremises(goal=goal.strip(), premises=premises)
    return GoalAndPremises(goal=stdout.strip(), premises="")


def is_no_active_proof(goal_text: str) -> bool:
    return goal_text.strip() == NO_ACTIVE_PROOF


def is_proof_discharged(goal_text: str) -> bool:
    stripped = goal_text.strip()
    return stripped in (NO_ACTIVE_PROOF, NO_MORE_GOALS)


def has_open_goals(stdout: str) -> bool:
    """True when stdout contains a printable current goal or an explicit discharge."""
    stripped = stdout.strip()
    if not stripped:
        return False
    if is_proof_discharged(stripped):
        return False
    return "Current goal" in stdout


def resolve_goal_cursor(proof: ProofFile, config: AgentConfig) -> int:
    """Return the `llm -upto` line that best reflects the current proof state."""
    bounds = proof.bounds()
    if bounds.proof_start_line == 0:
        return max(bounds.cursor_upto, 1)

    for cursor in range(bounds.cursor_upto, bounds.proof_start_line - 1, -1):
        goal = fetch_goal(proof.path, cursor, config).stdout.strip()
        if goal:
            return cursor
    return bounds.cursor_upto


def resolve_goal(proof: ProofFile, config: AgentConfig) -> str:
    """Return the current proof goal text for prompting and completion checks."""
    bounds = proof.bounds()
    if bounds.proof_start_line == 0:
        return fetch_goal(proof.path, bounds.cursor_upto, config).stdout.strip()

    at_cursor = fetch_goal(proof.path, bounds.cursor_upto, config).stdout.strip()
    if not at_cursor and not proof.is_last_tactic_proc():
        unchanged = _goal_after_unchanged_tactic(proof, config)
        if unchanged == "" and proof.last_tactic_line() is not None:
            return ""

    if proof.is_last_tactic_proc():
        if not at_cursor:
            probed = _probe_post_proc_goal(proof, config)
            if probed:
                return probed

    for cursor in range(bounds.cursor_upto, bounds.proof_start_line - 1, -1):
        goal = fetch_goal(proof.path, cursor, config).stdout.strip()
        if not goal:
            continue
        if proof.is_last_tactic_proc() and _looks_like_hoare_goal(goal):
            continue
        last_tactic = proof.last_tactic_line()
        if last_tactic is not None and cursor == last_tactic:
            prev = fetch_goal(proof.path, last_tactic - 1, config).stdout.strip()
            if goal == prev:
                continue
        return goal

    if proof.is_last_tactic_proc():
        probed = _probe_post_proc_goal(proof, config)
        if probed:
            return probed

    return _goal_after_unchanged_tactic(proof, config)


def tactic_discharged_proof(
    proof: ProofFile, resolved_goal: str, config: AgentConfig
) -> bool:
    """True when the latest tactic closed all goals but `qed.` is not written yet."""
    if is_proof_discharged(resolved_goal):
        return True
    if proof.is_last_tactic_proc():
        return False
    if resolved_goal.strip():
        return False
    return _probe_qed_discharge(proof, config)


def is_proof_complete_at_cursor(proof: ProofFile, config: AgentConfig) -> bool:
    goal = resolve_goal(proof, config)
    if is_proof_discharged(goal):
        return True
    return _probe_qed_discharge(proof, config)


def _goal_after_unchanged_tactic(proof: ProofFile, config: AgentConfig) -> str:
    """Detect a discharged proof when EasyCrypt leaves the cursor goal empty."""
    bounds = proof.bounds()
    last_tactic = proof.last_tactic_line()
    if last_tactic is None or last_tactic <= bounds.proof_start_line:
        return fetch_goal(proof.path, bounds.cursor_upto, config).stdout.strip()

    at_cursor = fetch_goal(proof.path, bounds.cursor_upto, config).stdout.strip()
    if at_cursor:
        return at_cursor

    at_last = fetch_goal(proof.path, last_tactic, config).stdout.strip()
    at_prev = fetch_goal(proof.path, last_tactic - 1, config).stdout.strip()
    if at_last and at_last == at_prev:
        return ""
    if not at_last and at_prev:
        return ""
    return at_last


def _looks_like_hoare_goal(goal: str) -> bool:
    return "pre =" in goal and "post =" in goal


def _probe_qed_discharge(proof: ProofFile, config: AgentConfig) -> bool:
    """Check whether appending `qed.` would close the active proof."""
    bounds = proof.bounds()
    if bounds.proof_start_line == 0:
        return False

    lines = list(proof.read_lines())
    insert_at = proof.insert_point()
    lines.insert(insert_at, _normalize_tactic_line("qed"))
    qed_line = insert_at + 1

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ec", delete=False, encoding="utf-8"
    ) as handle:
        temp_path = Path(handle.name)
        text = "\n".join(lines)
        if text:
            text += "\n"
        handle.write(text)

    try:
        result = fetch_goal(temp_path, qed_line, config)
        return is_proof_discharged(result.stdout.strip())
    finally:
        temp_path.unlink(missing_ok=True)


def _probe_post_proc_goal(proof: ProofFile, config: AgentConfig) -> str:
    """EasyCrypt hides the post-`proc` goal until `skip` is applied; probe with a temp copy."""
    lines = list(proof.read_lines())
    insert_at = proof.insert_point()
    lines.insert(insert_at, _normalize_tactic_line("skip"))
    skip_line = insert_at + 1

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ec", delete=False, encoding="utf-8"
    ) as handle:
        temp_path = Path(handle.name)
        text = "\n".join(lines)
        if text:
            text += "\n"
        handle.write(text)

    try:
        result = fetch_goal(temp_path, skip_line, config)
        goal = result.stdout.strip()
        if "Current goal" in goal:
            return goal
    finally:
        temp_path.unlink(missing_ok=True)
    return ""

# ---------------------------------------------------------------------------
# Logic-class probe (8c.3)
# ---------------------------------------------------------------------------

#: The one error that means "this is not a program-logic goal". Any OTHER
#: failure means the probe tactic was wrong for this goal, not that the goal
#: is ambient -- `wp.` fails on plenty of live program-logic goals.
_WRONG_CLASS_RE = re.compile(
    r"expecting a goal of the form", re.IGNORECASE
)


def probe_is_program_logic(proof: ProofFile, config: AgentConfig) -> bool | None:
    """Ask EasyCrypt whether the goal is still a program-logic judgment.

    Text classification cannot answer this. A discharged judgment still prints
    `pre`/`post`, so `prompt.goal_looks_program_logic` calls 91% of them
    program-logic and shannon-prover's `classify_goal` says pRHL on all 25 it
    was tested against. The best text discriminator found is 57.7% precision.

    So stop inferring it. Append a tactic that only a live program-logic goal
    accepts, read the error, then remove it. ~1.5 s against a ~170 s model
    call, and decisive rather than probabilistic.

    Returns True (program-logic), False (judgment already discharged), or None
    when the probe was inconclusive -- callers must treat None as "unknown"
    and fall back to the text heuristic rather than assuming either way.

    Leaves the file byte-identical.
    """
    original = proof.path.read_text(encoding="utf-8")
    try:
        proof.append_tactic("wp.")
        result = validate_file(proof.path, config)
    except OSError:
        return None
    finally:
        proof.path.write_text(original, encoding="utf-8")

    if result.returncode == 0:
        # `wp.` applied, so there was a program-logic goal to apply it to.
        return True
    combined = f"{result.stderr}\n{result.stdout}"
    if _WRONG_CLASS_RE.search(combined):
        return False
    # `wp.` failed for a reason of its own (no assignment to consume, wrong
    # shape). That says nothing about the class.
    return None

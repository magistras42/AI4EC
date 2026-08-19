"""Extract per-lemma sandbox files from indexed corpus sources."""

from __future__ import annotations

import re
from pathlib import Path

from benchmark.ec_scanner import strip_comments

from .protocols import IndexEntry, ProofCase

PROOF_LINE_RE = re.compile(r"^\s*proof\.")
QED_RE = re.compile(r"^\s*qed\.")

# Some corpus files (e.g. Joy's tutorial chapters) set `pragma Goals:
# printall.` so a human following along in Proof-General sees the full
# ambient context on every step. For the agent this is actively harmful:
# EasyCrypt then reprints every declared theory/lemma/axiom on *every*
# `-upto` goal fetch, which can balloon a single goal print to thousands of
# tokens and blow past the LLM's context window (observed as a hard
# "Context size has been exceeded" error). The pragma only affects display
# verbosity, never proof semantics, so it's safe to neutralize.
PRAGMA_GOALS_RE = re.compile(r"^\s*pragma\s+Goals\s*:", re.IGNORECASE)


def neutralize_verbose_pragmas(lines: list[str]) -> list[str]:
    """Comment out `pragma Goals: ...` lines in place (line count preserved
    so all downstream 1-based line-number bookkeeping stays valid)."""
    return [
        "(* pragma removed for agent sandbox: avoids oversized goal dumps *)"
        if PRAGMA_GOALS_RE.match(strip_comments(line))
        else line
        for line in lines
    ]


# `print ...` and `search ...` are diagnostic, human-facing REPL commands:
# they have no effect on any proof obligation, but `ec.exe llm -upto N`
# replays and captures the *entire* stdout transcript of compiling the file
# up to line N, not just the state at N. Joy's tutorial chapters sprinkle
# these liberally as teaching aids (e.g. "search (+)." to show students
# what's available), and each one can dump dozens of matching
# lemma/axiom/theory signatures. When a target lemma sits after several of
# these in the same file, every single goal fetch for that lemma silently
# carries all of that dump along with it — observed in practice to inflate
# a ~100-character goal into a >15,000-character prompt, blowing past the
# LLM's context window ("Context size has been exceeded"). Neutralizing
# them is safe: they're not tactics and dropping them cannot change whether
# any proof succeeds.
REPL_DISPLAY_RE = re.compile(r"^\s*(print|search)\b", re.IGNORECASE)


def strip_repl_display_commands(lines: list[str]) -> list[str]:
    """Comment out top-level `print`/`search` commands (line count preserved)."""
    return [
        "(* print/search removed for agent sandbox: avoids oversized goal dumps *)"
        if REPL_DISPLAY_RE.match(strip_comments(line))
        else line
        for line in lines
    ]


def find_proof_region(lines: list[str], lemma_line: int) -> tuple[int, int]:
    """Return 1-based (proof_start_line, qed_line) for lemma starting at lemma_line."""
    if lemma_line < 1 or lemma_line > len(lines):
        raise ValueError(f"lemma_line {lemma_line} out of range")

    proof_start: int | None = None
    for i in range(lemma_line - 1, len(lines)):
        stripped = strip_comments(lines[i]).strip()
        if PROOF_LINE_RE.match(stripped):
            proof_start = i + 1
            break
    if proof_start is None:
        raise ValueError(f"No proof. found for lemma at line {lemma_line}")

    qed_line: int | None = None
    for i in range(proof_start, len(lines)):
        stripped = strip_comments(lines[i]).strip()
        if QED_RE.match(stripped):
            qed_line = i + 1
            break
    if qed_line is None:
        raise ValueError(f"No qed. found after proof. at line {proof_start}")

    return proof_start, qed_line


def enumerate_tactic_lines(
    lines: list[str], proof_start_line: int, qed_line: int
) -> list[int]:
    """Return 1-based line numbers of tactic lines between proof. and qed."""
    tactics: list[int] = []
    for line_no in range(proof_start_line + 1, qed_line):
        if lines[line_no - 1].strip():
            tactics.append(line_no)
    return tactics


def truncate_at_qed(lines: list[str], qed_line: int) -> list[str]:
    return lines[:qed_line]


def strip_tactics(lines: list[str], tactic_lines: list[int]) -> list[str]:
    """Remove tactic lines; drop the target lemma's closing qed. so the agent
    starts with an open proof.

    `lines` is expected to already end at the target lemma's own `qed.` (as
    produced by `truncate_at_qed`/`build_sandbox`). Earlier lemmas in the
    same source file commonly have their own, already-discharged `qed.`
    lines, so we must find the LAST `qed.` in `lines`, not the first —
    otherwise the empty-slate start file gets truncated at some unrelated,
    earlier lemma instead of the target one.
    """
    drop = set(tactic_lines)
    qed_idx = len(lines) + 1
    for i, line in enumerate(lines, start=1):
        if QED_RE.search(strip_comments(line)):
            qed_idx = i
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        if i >= qed_idx:
            break
        if i in drop:
            continue
        out.append(line)
    return out


def format_hint(lines: list[str], tactic_lines: list[int]) -> str:
    """Format mutated tactic script for the repair-hint prompt section."""
    tactic_set = set(tactic_lines)
    parts: list[str] = []
    for i, line in enumerate(lines, start=1):
        if i in tactic_set:
            parts.append(line.rstrip())
    return "\n".join(parts)


def build_sandbox(entry: IndexEntry, data_dir: Path, dest: Path) -> ProofCase:
    """Build a sandbox .ec file ending at the target lemma's qed."""
    source = data_dir / entry.file
    if not source.exists():
        raise FileNotFoundError(f"Corpus file not found: {source}")

    lines = strip_repl_display_commands(
        neutralize_verbose_pragmas(source.read_text(encoding="utf-8").splitlines())
    )
    proof_start, qed_line = find_proof_region(lines, entry.line)
    tactic_lines = enumerate_tactic_lines(lines, proof_start, qed_line)
    sandbox_lines = truncate_at_qed(lines, qed_line)

    dest.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(sandbox_lines)
    if text:
        text += "\n"
    dest.write_text(text, encoding="utf-8")

    return ProofCase(
        name=entry.name,
        file=dest,
        lemma_line=entry.line,
        proof_start_line=proof_start,
        qed_line=qed_line,
        tactic_lines=tactic_lines,
        index_entry=entry,
    )


def apply_lines(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def admit_prior_lemmas(lines: list[str], prior_lemma_lines: list[int]) -> list[str]:
    """Replace each prior lemma's proof body with a single `admit.`, so a
    later target lemma's goal remains reachable even when earlier lemmas in
    the same file are broken (e.g. a genuinely broken corpus where we only
    care about repairing one target and must assume everything it depends on
    is proven).

    `prior_lemma_lines` are 1-based lines where a *prior* (non-target) lemma
    is declared — typically every other lemma's `IndexEntry.line` from the
    same source file. The line count of `lines` is preserved (tactic lines
    are blanked rather than removed) so any other 1-based line-number
    bookkeeping computed against the original file — including the target
    lemma's own `lemma_line`/`proof_start_line`/`qed_line` and later prior
    entries processed in the same pass — stays valid.
    """
    out = list(lines)
    for lemma_line in sorted(set(prior_lemma_lines)):
        try:
            proof_start, qed_line = find_proof_region(out, lemma_line)
        except ValueError:
            continue
        tactic_lines = enumerate_tactic_lines(out, proof_start, qed_line)
        if not tactic_lines:
            continue
        first, *rest = tactic_lines
        out[first - 1] = "  admit."
        for line_no in rest:
            out[line_no - 1] = ""
    return out

"""Extract per-lemma sandbox files from indexed corpus sources."""

from __future__ import annotations

import re
from pathlib import Path

from benchmark.ec_scanner import strip_comments

from .protocols import IndexEntry, ProofCase

PROOF_LINE_RE = re.compile(r"^\s*proof\.")
QED_RE = re.compile(r"^\s*qed\.")


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
    """Remove tactic lines; drop qed. so the agent starts with an open proof."""
    drop = set(tactic_lines)
    qed_idx: int | None = None
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        if i in drop:
            continue
        if QED_RE.search(strip_comments(line)):
            qed_idx = i
            break
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

    lines = source.read_text(encoding="utf-8").splitlines()
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

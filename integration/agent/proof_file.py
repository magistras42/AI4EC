"""Working-copy management: cursor, proof bounds, append/undo tactics."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


PROOF_RE = re.compile(r"\bproof\.")
QED_RE = re.compile(r"\bqed\.")
PROC_TACTIC_RE = re.compile(r"\bproc\.\s*$", re.IGNORECASE)


@dataclass
class ProofBounds:
    proof_start_line: int
    qed_line: int | None
    last_line: int
    cursor_upto: int


@dataclass
class ProofFile:
    path: Path

    def read_lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()

    def write_lines(self, lines: list[str]) -> None:
        text = "\n".join(lines)
        if text:
            text += "\n"
        self.path.write_text(text, encoding="utf-8")

    def bounds(self) -> ProofBounds:
        lines = self.read_lines()
        last_line = _last_nonempty_line(lines)
        proof_start_line = _find_proof_start(lines, last_line)
        qed_line = _find_qed_after(lines, proof_start_line)
        cursor_upto = _cursor_upto(lines, last_line, proof_start_line)
        return ProofBounds(
            proof_start_line=proof_start_line,
            qed_line=qed_line,
            last_line=last_line,
            cursor_upto=cursor_upto,
        )

    def insert_point(self) -> int:
        """0-based index where the next tactic line should be inserted."""
        bounds = self.bounds()
        lines = self.read_lines()
        if bounds.qed_line is not None:
            return bounds.qed_line - 1
        return bounds.last_line

    def append_tactic(self, tactic: str) -> int:
        lines = self.read_lines()
        insert_at = self.insert_point()
        tactic_line = _normalize_tactic_line(tactic)
        lines.insert(insert_at, tactic_line)
        self.write_lines(lines)
        return insert_at + 1

    def remove_lines(self, start_line: int, count: int = 1) -> None:
        """Remove `count` lines starting at 1-based `start_line`."""
        lines = self.read_lines()
        start_idx = start_line - 1
        del lines[start_idx : start_idx + count]
        self.write_lines(lines)

    def undo_last_tactic(self, count: int = 1) -> int:
        """Remove up to ``count`` trailing tactics. Returns how many were undone.

        Never removes the lemma signature or the ``proof.`` line. If fewer than
        ``count`` tactics remain, undoes as many as possible.
        """
        if count < 1:
            return 0
        undone = 0
        for _ in range(count):
            bounds = self.bounds()
            lines = self.read_lines()
            tactic_line = _find_last_tactic_line(lines, bounds)
            if tactic_line is None:
                break
            del lines[tactic_line - 1]
            self.write_lines(lines)
            undone += 1
        return undone

    def tail(self, num_lines: int = 20) -> str:
        lines = self.read_lines()
        return "\n".join(lines[-num_lines:])

    def last_tactic_line(self) -> int | None:
        return _find_last_tactic_line(self.read_lines(), self.bounds())

    def is_last_tactic_proc(self) -> bool:
        bounds = self.bounds()
        line_no = _find_last_tactic_line(self.read_lines(), bounds)
        if line_no is None:
            return False
        return PROC_TACTIC_RE.search(self.read_lines()[line_no - 1]) is not None

    def count_declarations_before(self, line_limit: int) -> int:
        count = 0
        for i, line in enumerate(self.read_lines(), start=1):
            if i > line_limit:
                break
            stripped = line.strip()
            if stripped.startswith("lemma ") or stripped.startswith("axiom "):
                count += 1
        return count


def create_working_copy(
    source: Path,
    work_copy: Path | None = None,
    suffix: str = ".agent.ec",
    output_dir: Path | None = None,
) -> Path:
    if work_copy is None:
        if source.suffix == ".ec":
            name = source.stem + suffix
        else:
            name = source.name + suffix
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            work_copy = output_dir / name
        elif source.suffix == ".ec":
            work_copy = source.with_name(source.stem + suffix)
        else:
            work_copy = source.with_name(source.name + suffix)
    shutil.copy2(source, work_copy)
    return work_copy


def promote_working_copy(work_copy: Path, original: Path) -> None:
    shutil.copy2(work_copy, original)


def _cursor_upto(lines: list[str], last_line: int, proof_start_line: int) -> int:
    """Map the file end to an `llm -upto` line that yields the current goal."""
    if last_line == 0:
        return 1
    # After a bare `proof.` line, EasyCrypt returns an empty goal for -upto last_line+1.
    if proof_start_line == last_line and PROOF_RE.search(lines[last_line - 1]):
        return last_line
    return last_line + 1


def _last_nonempty_line(lines: list[str]) -> int:
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 0


def _find_proof_start(lines: list[str], from_line: int) -> int:
    for i in range(from_line, 0, -1):
        if PROOF_RE.search(lines[i - 1]):
            return i
    return 0


def _find_qed_after(lines: list[str], proof_start_line: int) -> int | None:
    """Return the 1-based line number of the ``qed.`` that closes the proof
    opened at *proof_start_line*, or ``None`` if no such line exists.

    We verify ownership by scanning *backwards* from each candidate ``qed.``
    line to find the nearest preceding ``proof.``.  A candidate is accepted
    only when that nearest ``proof.`` is exactly ``proof_start_line``; this
    prevents picking up ``qed.`` lines that belong to earlier, already-closed
    lemmas in the same file (the root cause of the false-completion bug).
    """
    if proof_start_line == 0:
        return None
    for i in range(proof_start_line, len(lines) + 1):
        if not QED_RE.search(lines[i - 1]):
            continue
        # Verify this qed. belongs to the proof opened at proof_start_line.
        for j in range(i - 1, 0, -1):
            if PROOF_RE.search(lines[j - 1]):
                if j == proof_start_line:
                    return i   # confirmed: nearest proof. is our target
                break          # nearest proof. is a different lemma – skip
    return None


def _find_last_tactic_line(lines: list[str], bounds: ProofBounds) -> int | None:
    if bounds.proof_start_line == 0:
        return None
    end = (bounds.qed_line - 1) if bounds.qed_line else len(lines)
    for i in range(end, bounds.proof_start_line, -1):
        line = lines[i - 1].strip()
        if not line:
            continue
        if i == bounds.proof_start_line and PROOF_RE.search(line):
            return None
        if QED_RE.search(line):
            continue
        return i
    return None


def _normalize_tactic_line(tactic: str) -> str:
    tactic = tactic.strip()
    if tactic.startswith("```"):
        tactic = tactic.strip("`").strip()
    # Safety net: `append_tactic` assumes one call inserts exactly one
    # physical line. If a caller ever passes text containing raw newlines
    # or control characters (e.g. a degenerate LLM generation that wasn't
    # caught upstream), collapse it to a single printable line so a later
    # `remove_lines(..., count=1)` rollback can never leave orphaned
    # garbage lines behind in the proof file.
    tactic = "".join(ch if ch.isprintable() else " " for ch in tactic)
    tactic = re.sub(r"\s+", " ", tactic).strip()
    if not tactic.endswith("."):
        tactic += "."
    if not tactic.startswith("  "):
        tactic = "  " + tactic
    return tactic

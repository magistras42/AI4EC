"""Tests for proof sandbox extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integration.experiment.proof_extract import (
    admit_prior_lemmas,
    build_sandbox,
    enumerate_tactic_lines,
    find_proof_region,
    format_hint,
    neutralize_verbose_pragmas,
    strip_repl_display_commands,
    strip_tactics,
)
from integration.experiment.protocols import IndexEntry

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_find_proof_region_target_lemma():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()
    proof_start, qed_line = find_proof_region(lines, lemma_line=8)
    assert proof_start == 9
    assert qed_line == 12


def test_enumerate_tactic_lines():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()
    tactics = enumerate_tactic_lines(lines, proof_start_line=9, qed_line=12)
    assert tactics == [10, 11]


def test_build_sandbox_truncates_after_target_qed(tmp_path):
    data_dir = tmp_path / "data"
    corpus_file = data_dir / "chapter"
    corpus_file.mkdir(parents=True)
    source = FIXTURES / "multi_lemma.ec"
    (corpus_file / "multi_lemma.ec").write_text(source.read_text(encoding="utf-8"))

    entry = IndexEntry(
        repo_slug="tejasanilshah-the-joy-of-easycrypt",
        file="chapter/multi_lemma.ec",
        line=8,
        kind="lemma",
        name="target",
        signature="lemma target (n : int) : 0 + n = n.",
    )
    dest = tmp_path / "sandbox.ec"
    case = build_sandbox(entry, data_dir, dest)

    out_lines = dest.read_text(encoding="utf-8").splitlines()
    assert "lemma after" not in "\n".join(out_lines)
    assert case.name == "target"
    assert case.tactic_lines == [10, 11]


def test_strip_tactics_removes_body_and_qed():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()[:12]
    stripped = strip_tactics(lines, tactic_lines=[10, 11])
    text = "\n".join(stripped)
    assert "/addC" not in text
    assert "trivial." not in text
    assert "lemma target" in text
    assert stripped[-1].strip() == "proof."


def test_strip_tactics_stops_at_target_qed_not_an_earlier_lemmas():
    """Regression: `multi_lemma.ec`'s "first" lemma (lines 1-6) is fully
    proved and has its own `qed.` before "target" even starts. strip_tactics
    must strip only "target"'s own (final) qed., leaving "first"'s intact
    proof — including its qed. — untouched. Previously it stopped scanning
    at the FIRST qed. found in the whole slice (line 6, "first"'s), so the
    "target" lemma's signature/proof-start never even made it into the
    empty-slate start file.
    """
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()[:12]
    stripped = strip_tactics(lines, tactic_lines=[10, 11])
    text = "\n".join(stripped)
    assert "lemma first" in text
    assert "by rewrite addr0." in text
    assert text.count("qed.") == 1
    assert "lemma target" in text
    assert stripped[-1].strip() == "proof."


def test_neutralize_verbose_pragmas_preserves_line_count():
    lines = (FIXTURES / "pragma_goals.ec").read_text(encoding="utf-8").splitlines()
    cleaned = neutralize_verbose_pragmas(lines)
    assert len(cleaned) == len(lines)
    assert not any("pragma" in line.lower() and "goals" in line.lower() for line in cleaned)
    assert cleaned[2] == "(* pragma removed for agent sandbox: avoids oversized goal dumps *)"


def test_strip_repl_display_commands_preserves_line_count():
    lines = (FIXTURES / "pragma_goals.ec").read_text(encoding="utf-8").splitlines()
    cleaned = strip_repl_display_commands(lines)
    assert len(cleaned) == len(lines)
    assert not any(line.strip().lower().startswith(("print", "search")) for line in cleaned)


def test_build_sandbox_strips_pragma_and_repl_display_commands(tmp_path):
    """Regression: `pragma Goals: printall.` and top-level `print`/`search`
    commands are diagnostic, human-facing REPL aids used throughout the Joy
    tutorial chapters. `ec.exe llm -upto N` captures the *entire* stdout
    transcript of compiling up to line N, so any `print`/`search` before
    the target lemma gets replayed and its output (which can be enormous —
    every matching lemma/axiom/theory signature) piggybacks on every future
    goal fetch. In practice this inflated a ~100-character goal into a
    >15,000-character prompt and blew past the LLM's context window
    ("Context size has been exceeded"). `build_sandbox` must neutralize
    both so the agent only ever sees the current goal."""
    data_dir = tmp_path / "data"
    corpus_file = data_dir / "chapter"
    corpus_file.mkdir(parents=True)
    source = FIXTURES / "pragma_goals.ec"
    (corpus_file / "pragma_goals.ec").write_text(source.read_text(encoding="utf-8"))

    entry = IndexEntry(
        repo_slug="tejasanilshah-the-joy-of-easycrypt",
        file="chapter/pragma_goals.ec",
        line=8,
        kind="lemma",
        name="target",
        signature="lemma target (n : int) : 0 + n = n.",
    )
    dest = tmp_path / "sandbox.ec"
    case = build_sandbox(entry, data_dir, dest)

    text = dest.read_text(encoding="utf-8")
    assert "pragma Goals" not in text
    assert "print addC" not in text
    assert "search (+)" not in text
    assert case.tactic_lines == [10, 11]


def test_admit_prior_lemmas_stubs_only_prior_lemmas():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()
    # "target" (line 8) is the trial's target; "first" (line 3) is a prior
    # lemma that must be admitted so target's goal stays reachable. "after"
    # (line 14) is neither prior to nor the target — it must be untouched.
    admitted = admit_prior_lemmas(lines, prior_lemma_lines=[3])

    assert len(admitted) == len(lines)
    text = "\n".join(admitted)
    assert "by rewrite addr0." not in text
    assert "  admit." in admitted[4]  # line 5 (1-based) -> index 4
    assert "lemma first" in text
    assert "qed." in admitted[5]

    # target and after are untouched.
    assert "by rewrite /addC." in text
    assert "trivial." in text
    assert admitted[13] == "lemma after (n : int) : n = n."


def test_admit_prior_lemmas_ignores_out_of_range_lines():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()
    # An out-of-range "prior lemma" line (e.g. from stale bookkeeping) must
    # be skipped without raising; the rest of the file is unaffected.
    admitted = admit_prior_lemmas(lines, prior_lemma_lines=[len(lines) + 5])
    assert admitted == lines


def test_admit_prior_lemmas_handles_multiple_priors_and_keeps_line_numbers_stable():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()
    # Both "first" (line 3) and "target" (line 8) are treated as priors of
    # some later, hypothetical target; "after" (line 14) stays untouched.
    # Line numbers for later lemmas must remain valid after earlier ones are
    # stubbed, since the stubbing is done in a single pass over `lines`.
    admitted = admit_prior_lemmas(lines, prior_lemma_lines=[3, 8])

    assert len(admitted) == len(lines)
    text = "\n".join(admitted)
    assert "by rewrite addr0." not in text
    assert "by rewrite /addC." not in text
    assert text.count("admit.") == 2
    assert admitted[13] == "lemma after (n : int) : n = n."
    # "after"'s own proof region is still found correctly at its original line.
    proof_start, qed_line = find_proof_region(admitted, lemma_line=14)
    assert proof_start == 15
    assert qed_line == 17


def test_format_hint_includes_tactics_only():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()[:12]
    hint = format_hint(lines, tactic_lines=[10, 11])
    assert "by rewrite" in hint
    assert "trivial." in hint
    assert "lemma target" not in hint

"""Tests for proof sandbox extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integration.experiment.proof_extract import (
    build_sandbox,
    enumerate_tactic_lines,
    find_proof_region,
    format_hint,
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
    stripped = strip_tactics(lines, tactic_lines=[11, 12])
    text = "\n".join(stripped)
    assert "/addC" not in text
    assert "qed." not in text
    assert "proof." in text


def test_format_hint_includes_tactics_only():
    lines = (FIXTURES / "multi_lemma.ec").read_text(encoding="utf-8").splitlines()[:12]
    hint = format_hint(lines, tactic_lines=[10, 11])
    assert "by rewrite" in hint
    assert "trivial." in hint
    assert "lemma target" not in hint

"""Tests for the derens99/ElGamal-proof broken-proof corpus."""

from __future__ import annotations

import json
from pathlib import Path

from integration.experiment.corpora.elgamal import (
    ELGAMAL_SLUG,
    TARGET_FILE,
    ElGamalCorpus,
    load_index_entries,
    port_legacy_easycrypt_syntax,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_port_legacy_easycrypt_syntax_preserves_line_count():
    text = (FIXTURES / "hashedelgamal_mini.ec").read_text(encoding="utf-8")
    ported = port_legacy_easycrypt_syntax(text)
    assert len(ported.split("\n")) == len(text.split("\n"))


def test_port_legacy_easycrypt_syntax_fixes_known_breakage():
    text = (FIXTURES / "hashedelgamal_mini.ec").read_text(encoding="utf-8")
    ported_lines = port_legacy_easycrypt_syntax(text).split("\n")

    assert ported_lines[0] == (
        "pragma +old_mem_restr. require import AllCore Distr FMap DBool FSet."
    )
    assert "proc *" not in "\n".join(ported_lines)
    assert ported_lines[3] == "  proc init() : unit"
    assert ported_lines[9] == "  proc choose() : int"
    assert ported_lines[14] == "declare module Adv <: ADV{RO}."


def _write_fixture_corpus(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    corpus_dir = data_dir / ELGAMAL_SLUG
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "hashedelgamal.ec").write_text(
        (FIXTURES / "hashedelgamal_mini.ec").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    index = {
        "generated_at": "2026-01-01T00:00:00Z",
        "count": 2,
        "proofs": [
            {
                "repo_slug": ELGAMAL_SLUG,
                "file": TARGET_FILE,
                "line": 17,
                "kind": "lemma",
                "name": "first",
                "signature": "lemma first : true.",
            },
            {
                "repo_slug": ELGAMAL_SLUG,
                "file": TARGET_FILE,
                "line": 22,
                "kind": "lemma",
                "name": "target",
                "signature": "lemma target : true.",
            },
            # Should be filtered out: wrong repo slug.
            {
                "repo_slug": "some-other-repo",
                "file": "some-other-repo/foo.ec",
                "line": 1,
                "kind": "lemma",
                "name": "unrelated",
                "signature": "lemma unrelated : true.",
            },
        ],
    }
    index_path = data_dir / "proofs_index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return data_dir


def test_load_index_entries_filters_by_slug_file_and_kind(tmp_path):
    data_dir = _write_fixture_corpus(tmp_path)
    entries = load_index_entries(data_dir / "proofs_index.json")
    assert [e.name for e in entries] == ["first", "target"]


def test_elgamal_corpus_load_cases_admits_priors(tmp_path):
    """Prior-lemma admitting, under the DEFAULT (raw, unported) sandboxes."""
    data_dir = _write_fixture_corpus(tmp_path)
    corpus = ElGamalCorpus(data_dir=data_dir, sandbox_dir=tmp_path / "sandboxes")

    cases = corpus.load_cases()
    assert {c.name for c in cases} == {"first", "target"}

    target = next(c for c in cases if c.name == "target")
    text = target.file.read_text(encoding="utf-8")

    # Default is now port_legacy_syntax=False: the 2020 syntax is handed to the
    # harness untouched so integration/agent/import_repair.py does the porting
    # from ec_migrations.toml, verified against EasyCrypt. See the corpus
    # docstring for the measurement behind this default.
    assert "proc *" in text, "default must NOT pre-port; import_repair does it"

    # "first" (the only prior) is admitted; "target"'s own tactic is intact.
    lines = text.splitlines()
    assert lines[18].strip() == "admit."  # "first"'s tactic, 1-based line 19
    assert lines[23].strip() == "trivial."  # target's own tactic, untouched

    # "first" itself has no priors: its own case is left alone.
    first = next(c for c in cases if c.name == "first")
    first_text = first.file.read_text(encoding="utf-8")
    assert "trivial." in first_text
    assert "admit." not in first_text


def test_elgamal_corpus_can_still_pre_port_on_request(tmp_path):
    """`port_legacy_syntax=True` restores the offline port.

    Retained so a run can isolate tactic-level repair from import-level
    repair, and so the behaviour every prior experiment used stays
    reproducible.
    """
    data_dir = _write_fixture_corpus(tmp_path)
    corpus = ElGamalCorpus(
        data_dir=data_dir,
        sandbox_dir=tmp_path / "sandboxes",
        port_legacy_syntax=True,
    )
    target = next(c for c in corpus.load_cases() if c.name == "target")
    text = target.file.read_text(encoding="utf-8")

    assert "proc *" not in text
    assert "declare module Adv <: ADV{RO}." in text
    assert "old_mem_restr" in text


def test_pre_porting_preserves_line_numbers(tmp_path):
    """Both modes must agree on line count -- ProofCase records absolute lines."""
    data_dir = _write_fixture_corpus(tmp_path)
    raw = ElGamalCorpus(data_dir=data_dir, sandbox_dir=tmp_path / "raw")
    ported = ElGamalCorpus(
        data_dir=data_dir, sandbox_dir=tmp_path / "ported", port_legacy_syntax=True
    )
    raw_case = next(c for c in raw.load_cases() if c.name == "target")
    ported_case = next(c for c in ported.load_cases() if c.name == "target")

    assert raw_case.proof_start_line == ported_case.proof_start_line
    assert raw_case.tactic_lines == ported_case.tactic_lines
    assert len(raw_case.file.read_text().splitlines()) == len(
        ported_case.file.read_text().splitlines()
    )


def test_elgamal_corpus_sample_cases_with_replacement(tmp_path):
    import random

    data_dir = _write_fixture_corpus(tmp_path)
    corpus = ElGamalCorpus(data_dir=data_dir, sandbox_dir=tmp_path / "sandboxes")
    sampled = corpus.sample_cases(5, random.Random(0))
    assert len(sampled) == 5
    assert {c.name for c in sampled} <= {"first", "target"}

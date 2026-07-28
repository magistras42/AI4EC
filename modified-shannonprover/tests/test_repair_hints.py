"""Unit tests for core.easycrypt.repair_hints.

Uses local fixtures under tests/fixtures/repair_hints/ (a trimmed copy of
retrieve_entries.py + a small synthetic changelog.yaml + two repair_doc
entries) rather than the live proof_corpus/ sibling repo, so this suite has
no external-repo dependency at CI time.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import _pathsetup  # noqa: F401  (repo root on sys.path)

import core.easycrypt.repair_hints as repair_hints
from core.easycrypt.session_tool_view import validate_tool_view


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "repair_hints"


@pytest.fixture(autouse=True)
def _isolated_proof_corpus(monkeypatch):
    """Point repair_hints at the local fixture tree and reset its module
    cache so tests never see another test's (or the real sibling repo's)
    dynamically-loaded retrieve_entries.py."""
    monkeypatch.setenv("SHANNON_PROOF_CORPUS_DIR", str(FIXTURE_ROOT))
    repair_hints._retrieve_entries_module_cache = None
    yield
    repair_hints._retrieve_entries_module_cache = None


def test_resolve_proof_corpus_root_uses_env_override():
    assert repair_hints.resolve_proof_corpus_root() == FIXTURE_ROOT


def test_resolve_proof_corpus_root_missing_raises(monkeypatch):
    monkeypatch.setenv("SHANNON_PROOF_CORPUS_DIR", "/nonexistent/path/for/sure")
    with pytest.raises(repair_hints.RepairHintsUnavailable):
        repair_hints.resolve_proof_corpus_root()


def test_get_changelog_repair_hints_tier_a_and_b_kept_tier_c_dropped():
    results = repair_hints.get_changelog_repair_hints(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
    )
    ids = {r["id"] for r in results}
    # Tier A: mechanism_change + relevance=high is always kept, even though
    # nothing in the failing text mentions SmtMap/FinMap by name.
    assert "605" in ids
    # Tier B: identifier overlap ("frng") pulls in the FMap rename entry.
    assert "690" in ids
    # Tier C: documentation/internal/low-relevance entries are always dropped.
    assert "700" not in ids
    assert "900" not in ids
    # Strictly outside the (source, target] range.
    assert "1" not in ids


def test_get_changelog_repair_hints_empty_when_no_overlap_or_mechanism_change():
    results = repair_hints.get_changelog_repair_hints(
        failing_tactic_text="apply totally_unrelated_lemma.",
        ec_error_text="unknown lemma totally_unrelated_lemma",
        source_ec_version="r2025.02",
        target_ec_version="r2025.02",
    )
    # r2025.02 itself is excluded (strictly after source), so nothing to match.
    assert results == []


def test_get_repair_doc_snippets_matches_by_identifier():
    results = repair_hints.get_repair_doc_snippets(identifiers={"frng"})
    files = {r["_file"] for r in results}
    assert "fmap_lib.json" in files
    assert "unrelated_lib.json" not in files


def test_get_repair_doc_snippets_matches_by_path_substring():
    results = repair_hints.get_repair_doc_snippets(identifiers={"FMap"})
    files = {r["_file"] for r in results}
    assert "fmap_lib.json" in files


def test_get_repair_doc_snippets_no_match_returns_empty():
    results = repair_hints.get_repair_doc_snippets(identifiers={"ZzzNoSuchIdentifier"})
    assert results == []


def test_build_repair_hints_tool_view_is_valid_and_populated():
    view = repair_hints.build_repair_hints_tool_view(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
    )
    validation = validate_tool_view(view)
    assert validation.ok, validation.errors
    assert view["ok"] is True
    assert view["tool"] == "repair_hints"
    recs = view["guidance"]["recommendations"]
    assert recs, "expected at least one recommendation"
    producers = {r["producer"] for r in recs}
    assert "repair_hints.changelog" in producers
    assert "repair_hints.repair_doc" in producers
    assert view["evidence"]["kb"]
    assert view["evidence"]["retrieval"]


def test_build_repair_hints_tool_view_degrades_gracefully_when_unavailable(monkeypatch):
    monkeypatch.setenv("SHANNON_PROOF_CORPUS_DIR", "/nonexistent/path/for/sure")
    view = repair_hints.build_repair_hints_tool_view(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
    )
    validation = validate_tool_view(view)
    assert validation.ok, validation.errors
    # Optional supplementary context: unavailability is never a hard error.
    assert view["ok"] is True
    assert view["guidance"]["recommendations"] == []
    assert view["notes"], "expected an explanatory note about unavailability"


def test_emit_repair_hints_writes_artifact_and_event(tmp_path):
    session_dir = tmp_path / ".ec_session_test"
    event_payload = repair_hints.emit_repair_hints(
        session_dir,
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
        source="test",
    )
    assert event_payload["tool"] == "repair_hints"
    assert event_payload["ok"] is True
    assert Path(event_payload["artifact"]).is_file()

    events_path = session_dir / "events.jsonl"
    assert events_path.is_file()
    last_line = events_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert '"tool.view.produced"' in last_line
    assert '"source": "test"' in last_line

"""Unit tests for integration.agent.repair_hints.

Ported from shannon-prover's equivalent test suite -- same fixtures (a
trimmed retrieve_entries.py copy + a small synthetic changelog.yaml + two
repair_doc entries) under integration/tests/fixtures/repair_hints/, so this
suite has no external-repo dependency at CI time (proof_corpus/ is a
sibling directory, not a vendored package).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from integration.agent import repair_hints


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


def test_format_repair_hints_for_prompt_includes_both_sections():
    changelog_entries = [
        {"version": "r2025.02", "title": "Example", "repair_hint": "Do X instead."},
    ]
    repair_doc_entries = [
        {"path": "theories/datatypes/FMap.ec", "current_content_summary": "FMap summary."},
    ]
    text = repair_hints.format_repair_hints_for_prompt(changelog_entries, repair_doc_entries)
    assert "Known EasyCrypt changelog entries in range:" in text
    assert "Do X instead." in text
    assert "Known library reference notes:" in text
    assert "FMap summary." in text


def test_format_repair_hints_for_prompt_empty_when_nothing_found():
    assert repair_hints.format_repair_hints_for_prompt([], []) == ""


def test_get_repair_hints_text_populated():
    text, notes, matched_version = repair_hints.get_repair_hints_text(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
    )
    assert notes == []
    assert matched_version == "r2025.02"
    assert "Known EasyCrypt changelog entries in range:" in text
    assert "Known library reference notes:" in text


def test_get_repair_hints_text_degrades_gracefully_when_unavailable(monkeypatch):
    monkeypatch.setenv("SHANNON_PROOF_CORPUS_DIR", "/nonexistent/path/for/sure")
    text, notes, matched_version = repair_hints.get_repair_hints_text(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
    )
    # Optional supplementary context: unavailability is never a hard error.
    assert text == ""
    assert matched_version is None
    assert len(notes) == 2  # both changelog and repair_doc unavailable


# ── Release-order hopping (get_changelog_repair_hints_by_release) ──────────


def test_hop_returns_earliest_applicable_release_first():
    hits, matched_version = repair_hints.get_changelog_repair_hints_by_release(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
    )
    # r2025.02 and r2025.10 BOTH have a "frng"-matching entry; the hop must
    # stop at the earliest (r2025.02), not pool both releases together.
    assert matched_version == "r2025.02"
    ids = {h["id"] for h in hits}
    assert "605" in ids  # Tier A, same-release mechanism_change+high
    assert "690" in ids  # Tier B, same-release identifier match
    assert "800" not in ids  # belongs to r2025.10, a LATER release


def test_hop_advances_past_consumed_release():
    hits, matched_version = repair_hints.get_changelog_repair_hints_by_release(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
        already_consumed_versions={"r2025.02"},
    )
    assert matched_version == "r2025.10"
    ids = {h["id"] for h in hits}
    assert ids == {"800"}


def test_hop_returns_none_when_all_applicable_releases_consumed():
    hits, matched_version = repair_hints.get_changelog_repair_hints_by_release(
        failing_tactic_text="rewrite frng.",
        ec_error_text="unknown lemma frng",
        source_ec_version="r2022.04",
        target_ec_version="r2026.07",
        already_consumed_versions={"r2025.02", "r2025.10"},
    )
    # r2026.07's only entry (id 900) is kind=internal -- always dropped, so
    # there's nothing left to hop to.
    assert matched_version is None
    assert hits == []


def test_hop_returns_none_when_no_release_in_range_matches():
    hits, matched_version = repair_hints.get_changelog_repair_hints_by_release(
        failing_tactic_text="apply totally_unrelated_lemma.",
        ec_error_text="unknown lemma totally_unrelated_lemma",
        source_ec_version="r2025.02",
        target_ec_version="r2025.02",
    )
    assert matched_version is None
    assert hits == []


# --- document-frequency guard on tactic-keyed retrieval ---------------------
# `by_tactic["smt"]` holds 28 of the catalog's 913 entries (3.1%), and matching
# on it retrieved four unrelated r2023.09 chore commits. A term that tags 3% of
# everything ever written does not select the entries relevant to one failing
# step.


def test_a_ubiquitous_tactic_name_is_not_used_for_retrieval():
    from integration.agent.repair_hints import _discriminative_tactics

    changelog = {
        "releases": [{"entries": [{}] * 100}],
        "indexes": {"by_tactic": {"smt": [{}] * 3, "rnd": [{}] * 1}},
    }
    assert _discriminative_tactics(changelog, ["smt", "rnd"]) == ["rnd"]


def test_a_name_exactly_at_the_threshold_is_kept():
    from integration.agent.repair_hints import _discriminative_tactics

    changelog = {
        "releases": [{"entries": [{}] * 100}],
        "indexes": {"by_tactic": {"seq": [{}] * 2}},
    }
    assert _discriminative_tactics(changelog, ["seq"]) == ["seq"]


def test_an_empty_catalog_prunes_nothing():
    """No denominator means no evidence of ubiquity; do not silently drop the
    whole query."""
    from integration.agent.repair_hints import _discriminative_tactics

    assert _discriminative_tactics({}, ["smt"]) == ["smt"]


def test_the_guard_prunes_the_real_catalogs_generic_names(monkeypatch):
    """Against the shipped changelog, not a fixture.

    The threshold was chosen from the real catalog's shape -- `smt` 3.07%,
    `rewrite` 2.96%, `simplify` 2.63%, `proc` 2.52%, then a gap down to `rnd`
    at 1.31% -- so a fixture proving 2% works on invented numbers would prove
    nothing. This is the one test here that opts out of the isolation fixture.
    """
    from integration.agent.repair_hints import (
        _discriminative_tactics,
        _load_retrieve_entries_module,
        resolve_changelog_path,
    )

    monkeypatch.delenv("SHANNON_PROOF_CORPUS_DIR", raising=False)
    repair_hints._retrieve_entries_module_cache = None
    module = _load_retrieve_entries_module()
    changelog = module.load_changelog(str(resolve_changelog_path()))
    names = ["smt", "rewrite", "simplify", "proc", "rnd", "while", "seq"]
    kept = _discriminative_tactics(changelog, names)
    assert "smt" not in kept and "rewrite" not in kept
    assert "simplify" not in kept and "proc" not in kept
    assert "rnd" in kept and "while" in kept


def test_a_step_naming_only_generic_tactics_retrieves_nothing(monkeypatch):
    """The guard has to be wired into the entry point, not just available.

    Against the real catalog, so that "no hits" cannot pass because the
    fixture corpus happens to be empty: a `rnd` step still retrieves, and the
    `smt` step that used to return four unrelated r2023.09 chore commits now
    returns nothing.
    """
    from integration.agent.repair_hints import get_tactic_change_hints_by_release

    monkeypatch.delenv("SHANNON_PROOF_CORPUS_DIR", raising=False)
    repair_hints._retrieve_entries_module_cache = None

    generic, version = get_tactic_change_hints_by_release(
        failing_tactic_text="smt().",
        source_ec_version="r2022.04",
        target_ec_version="r2026.06",
    )
    assert generic == [] and version is None

    specific, _ = get_tactic_change_hints_by_release(
        failing_tactic_text="rnd (fun x => t{1} +^ x).",
        source_ec_version="r2022.04",
        target_ec_version="r2026.06",
    )
    assert specific, "a discriminative tactic name must still retrieve"

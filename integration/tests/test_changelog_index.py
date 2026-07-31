"""Tests for the indexed changelog format and its retrieval path.

Covers `proof_corpus/scripts/build_changelog_index.py` (the derivation) and
`proof_corpus/scripts/retrieve_entries.py` (the two-format loader + scoring),
plus the fields `integration/agent/repair_hints.py` renders from them.

Both scripts live outside any importable package -- they are loaded by path,
the same way `repair_hints._load_retrieve_entries_module` loads the retriever
at runtime -- so these tests exercise the real dynamic-load contract rather
than a copy.

Fixtures are built in a tmp_path from the checked-in legacy fixture under
`integration/tests/fixtures/repair_hints/`, so nothing here depends on the
sibling `proof_corpus/` directory being present or re-indexed.
"""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest
import yaml

from integration.agent import repair_hints


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "proof_corpus" / "scripts"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "repair_hints"


def _load_script(name: str) -> types.ModuleType:
    path = SCRIPTS / name
    if not path.is_file():
        pytest.skip(f"{path} not present")
    spec = importlib.util.spec_from_file_location(f"_test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder() -> types.ModuleType:
    return _load_script("build_changelog_index.py")


@pytest.fixture(scope="module")
def retriever() -> types.ModuleType:
    return _load_script("retrieve_entries.py")


@pytest.fixture
def legacy_changelog() -> dict:
    return yaml.safe_load(
        (FIXTURE_ROOT / "output" / "changelog.yaml").read_text(encoding="utf-8")
    )


@pytest.fixture
def built_index(builder, legacy_changelog) -> dict:
    """An index derived from the legacy fixture, with a small synthetic
    vocabulary standing in for the EasyCrypt theory tree."""
    return builder.build_index(
        legacy_changelog,
        raw={
            "repo": "EasyCrypt/easycrypt",
            "releases": [
                {
                    "tag_name": "r2025.02",
                    "body": "- x by @a in #605\n" * 3,
                    "pr_details": {
                        "605": {
                            "labels": ["breaking change"],
                            "changed_files": [
                                "theories/datatypes/SmtMap.ec",
                                "theories/datatypes/FMap.ec",
                                "src/ecEnv.ml",
                            ],
                            "body": "For most, the fix will be s/SmtMap/FMap/.",
                        },
                        "690": {
                            "labels": [],
                            "changed_files": ["theories/datatypes/FMap.ec"],
                            "body": "",
                        },
                    },
                }
            ],
        },
        symbol_owners={
            "SmtMap": ["SmtMap"],
            "FMap": ["FMap"],
            "FinMap": ["FMap"],
            "frng": ["FMap"],
            "map": ["CoreMap", "FMap", "List"],
        },
        theory_paths={
            "SmtMap": "theories/datatypes/SmtMap.ec",
            "FMap": "theories/datatypes/FMap.ec",
        },
        tactic_vocab={"rewrite", "apply", "smt"},
        sources={"changelog": "fixture"},
    )


# --- derivation -------------------------------------------------------------


def test_index_is_tagged_with_its_schema(built_index, retriever):
    assert built_index["schema"] == retriever.INDEX_SCHEMA
    assert retriever.is_index(built_index)


def test_entries_keep_every_authored_field(built_index):
    entry = next(e for e in built_index["entries"] if e["id"] == "605")
    # The authored/LLM-produced fields must survive verbatim -- the index is
    # additive, never a rewrite of what a human or the classifier wrote.
    assert entry["title"] == "SmtMap: split into SmtMap and FinMap"
    assert entry["kind"] == "mechanism_change"
    assert entry["relevance"] == "high"
    assert entry["repair_hint"].startswith("Import the specific split theory")
    assert entry["identifiers"] == ["SmtMap", "FinMap"]


def test_legacy_id_is_preserved_for_existing_consumers(built_index):
    # repair_hints.py (both copies) reads entry["id"]; dropping it in favor of
    # "pr" alone would break them silently.
    assert all("id" in entry for entry in built_index["entries"])
    entry = next(e for e in built_index["entries"] if e["id"] == "605")
    assert entry["pr"] == entry["id"]


def test_identifiers_are_resolved_into_typed_buckets(built_index):
    entry = next(e for e in built_index["entries"] if e["id"] == "605")
    assert "SmtMap" in entry["symbols"]
    assert "FMap" in entry["symbols"] or "FMap" in entry["theories_touched"]
    assert entry["tactics"] == []


def test_theory_scope_comes_from_changed_files(built_index):
    entry = next(e for e in built_index["entries"] if e["id"] == "605")
    assert entry["theories_touched"] == ["FMap", "SmtMap"]
    assert entry["areas"] == ["engine", "library"]
    assert entry["touches"]["engine"] == ["src/ecEnv.ml"]


def test_english_words_are_not_promoted_to_symbols(builder):
    """A bare title word that happens to collide with a declared name must not
    become a symbol -- this is the defect the whole format change exists to
    fix (measured: 85% of legacy `identifiers` slots were English prose)."""
    entry = builder.enrich_entry(
        {"id": "1", "title": "use the new list type", "kind": "tactic_change",
         "relevance": "medium", "identifiers": ["use", "type", "list"]},
        version="r2025.02", ordinal=0, repo="EasyCrypt/easycrypt",
        pr_details={},
        symbol_owners={"use": ["Somewhere"], "type": ["Somewhere"], "list": ["List"]},
        theory_paths={}, tactic_vocab=set(), body_excerpt_chars=0,
    )
    assert entry["symbols"] == []


def test_backticked_names_are_accepted_even_when_ambiguous(builder):
    """Backticks are the authors' own marker for code, so they override the
    ambiguity filter that rejects bare English words."""
    entry = builder.enrich_entry(
        {"id": "2", "title": "fix `list` handling", "kind": "tactic_change",
         "relevance": "medium", "identifiers": []},
        version="r2025.02", ordinal=0, repo="EasyCrypt/easycrypt",
        pr_details={},
        symbol_owners={"list": ["List"]}, theory_paths={},
        tactic_vocab=set(), body_excerpt_chars=0,
    )
    assert entry["symbols"] == ["list"]


def test_tactic_names_win_over_symbol_names(builder):
    """`rewrite` is both a tactic and a declared name; classifying it as a
    symbol overstates the match strength and hides what the entry is about."""
    entry = builder.enrich_entry(
        {"id": "3", "title": "change `rewrite` behaviour", "kind": "tactic_change",
         "relevance": "high", "identifiers": []},
        version="r2025.02", ordinal=0, repo="EasyCrypt/easycrypt",
        pr_details={},
        symbol_owners={"rewrite": ["Logic"]}, theory_paths={},
        tactic_vocab={"rewrite"}, body_excerpt_chars=0,
    )
    assert entry["tactics"] == ["rewrite"]
    assert entry["symbols"] == []


def _enrich(builder, entry, **kwargs):
    defaults = dict(
        version="r2025.02", ordinal=0, repo="EasyCrypt/easycrypt", pr_details={},
        symbol_owners={}, theory_paths={}, tactic_vocab=set(), body_excerpt_chars=0,
    )
    defaults.update(kwargs)
    return builder.enrich_entry(entry, **defaults)


def test_import_relevant_requires_import_prose_not_just_touched_theories(builder):
    """Touching a file under theories/ is a library change, not an import
    change. Regression for #724 ('remove alt-ergo from EasyCrypt TCB'), which
    edited 21 theory files to adjust `smt` calls and was wrongly flagged."""
    entry = _enrich(
        builder,
        {"id": "724", "title": "remove alt-ergo from EasyCrypt TCB",
         "kind": "mechanism_change", "relevance": "high", "identifiers": [],
         "summary": "Alt-ergo was removed from the TCB, requiring proofs "
                    "previously relying on it to use other solvers."},
        pr_details={"724": {"changed_files": ["theories/datatypes/List.ec"]}},
    )
    assert entry["theories_touched"] == ["List"]
    assert entry["import_relevant"] is False


def test_import_relevant_fires_on_real_import_prose(builder):
    entry = _enrich(
        builder,
        {"id": "605", "title": "split SmtMap into SMT Array and finite map",
         "kind": "mechanism_change", "relevance": "high", "identifiers": [],
         "repair_hint": "If proofs importing SmtMap break, replace SmtMap "
                        "imports with FMap."},
        pr_details={"605": {"changed_files": ["theories/datatypes/SmtMap.ec"]}},
    )
    assert entry["import_relevant"] is True


def test_import_relevant_ignores_the_split_tactic(builder):
    """`split` is also a tactic; the weak 'theory reorganization' vocabulary
    must not fire without a theory actually being involved."""
    entry = _enrich(
        builder,
        {"id": "675", "title": "Tactic `split` with break position",
         "kind": "tactic_change", "relevance": "high", "identifiers": []},
    )
    assert entry["import_relevant"] is False


def test_breaking_weight_is_materialized(built_index):
    entry = next(e for e in built_index["entries"] if e["id"] == "605")
    # mechanism_change (5.0) x high (1.0)
    assert entry["breaking_weight"] == pytest.approx(5.0)


def test_coverage_records_releases_with_no_entries(built_index):
    coverage = built_index["coverage"]
    assert coverage["starts_at"] is not None
    # The fixture's r2022.04 has one entry, so nothing is missing here; the
    # field must still be present and well-formed for consumers to rely on.
    assert isinstance(coverage["releases_without_entries"], list)


def test_inverted_indexes_answer_lookups_directly(built_index):
    by_theory = built_index["indexes"]["by_theory"]
    assert "r2025.02#605" in by_theory["SmtMap"]
    assert built_index["indexes"]["by_kind"]["mechanism_change"]


# --- retrieval --------------------------------------------------------------


def test_loader_accepts_both_formats(retriever, tmp_path, built_index):
    legacy = FIXTURE_ROOT / "output" / "changelog.yaml"
    loaded_legacy = retriever.load_changelog(str(legacy))
    assert not retriever.is_index(loaded_legacy)
    assert loaded_legacy["releases"]

    index_path = tmp_path / "changelog_index.json"
    index_path.write_text(json.dumps(built_index), encoding="utf-8")
    loaded_index = retriever.load_changelog(str(index_path))
    assert retriever.is_index(loaded_index)
    # Releases must expose the nested `entries` view every existing consumer
    # expects, materialized from the flat entry list.
    assert loaded_index["releases"][-1]["entries"]


def test_load_index_rejects_the_legacy_format(retriever):
    with pytest.raises(ValueError, match="legacy changelog"):
        retriever.load_index(str(FIXTURE_ROOT / "output" / "changelog.yaml"))


def test_legacy_scoring_contract_is_unchanged(retriever):
    """The exact assertions the pre-existing repair-hints suite makes."""
    changelog = retriever.load_changelog(str(FIXTURE_ROOT / "output" / "changelog.yaml"))
    in_range = retriever.releases_in_range(
        changelog["releases"], "r2022.04", "r2026.07"
    )
    results = retriever.score_entries(
        in_range, retriever.tokenize_proof("rewrite frng.\nunknown lemma frng"), 12
    )
    ids = {r["id"] for r in results}
    assert "605" in ids           # Tier A: mechanism_change + high
    assert "690" in ids           # Tier B: identifier overlap on `frng`
    assert "700" not in ids       # Tier C: documentation
    assert "900" not in ids       # Tier C: low relevance
    assert "1" not in ids         # strictly before the source version


def test_tier_a_is_capped_so_matches_always_get_room(retriever):
    """Unmatched structural entries used to fill every slot; a name-matched
    entry must always be reachable."""
    releases = [
        {
            "version": "r2025.02",
            "ordinal": 0,
            "entries": [
                {"id": f"a{i}", "kind": "mechanism_change", "relevance": "high",
                 "identifiers": []}
                for i in range(10)
            ] + [
                {"id": "match", "kind": "tactic_change", "relevance": "high",
                 "identifiers": ["frng"]},
            ],
        }
    ]
    results = retriever.score_entries(releases, {"frng"}, 4)
    assert "match" in {r["id"] for r in results}


def test_structural_entry_keeps_its_name_match(retriever):
    """A mechanism_change/high entry that ALSO matches by name is the
    strongest signal available; it used to be filed as a generic Tier A entry
    and lose its overlap entirely."""
    releases = [
        {
            "version": "r2025.02",
            "ordinal": 0,
            "entries": [
                {"id": "605", "kind": "mechanism_change", "relevance": "high",
                 "symbols": ["SmtMap"], "identifiers": ["SmtMap"]},
            ],
        }
    ]
    results = retriever.score_entries(releases, {"SmtMap"}, 5)
    assert results[0]["overlap"] == ["SmtMap"]
    assert "structural change matching" in results[0]["reason"]


def test_symbol_match_outranks_bare_identifier_match(retriever):
    releases = [
        {
            "version": "r2025.02",
            "ordinal": 0,
            "entries": [
                {"id": "weak", "kind": "tactic_change", "relevance": "high",
                 "identifiers": ["frng"]},
                {"id": "strong", "kind": "tactic_change", "relevance": "high",
                 "symbols": ["frng"]},
            ],
        }
    ]
    results = retriever.score_entries(releases, {"frng"}, 5)
    assert [r["id"] for r in results] == ["strong", "weak"]


def test_index_lookups(retriever, tmp_path, built_index):
    index_path = tmp_path / "changelog_index.json"
    index_path.write_text(json.dumps(built_index), encoding="utf-8")
    index = retriever.load_changelog(str(index_path))

    assert [e["id"] for e in retriever.entries_for_theories(index, ["SmtMap"])] == ["605"]
    assert retriever.entries_for_symbols(index, ["FMap"])
    assert retriever.resolve_version(index, "r2025.02") is not None
    assert retriever.import_relevant_entries(index, ["r2025.02"])


def test_releases_between_strict_raises_on_unknown_version(retriever):
    changelog = retriever.load_changelog(str(FIXTURE_ROOT / "output" / "changelog.yaml"))
    # Default (fail-open) behavior is load-bearing for pre-r2022.04 corpora.
    assert retriever.releases_between(changelog, "r1999.01", "r2026.07")
    with pytest.raises(KeyError):
        retriever.releases_between(changelog, "r1999.01", "r2026.07", strict=True)


def test_coverage_gap_reports_uncataloged_releases(retriever):
    changelog = {
        "releases": [
            {"version": "r2022.04", "ordinal": 0, "published_at": "2022", "entries": []},
            {"version": "r2024.01", "ordinal": 1, "published_at": "2024", "entries": []},
            {"version": "r2025.02", "ordinal": 2, "published_at": "2025",
             "entries": [{"id": "1", "kind": "tactic_change", "relevance": "high"}]},
        ]
    }
    assert retriever.coverage_gap(changelog, "r2022.04", "r2025.02") == ["r2024.01"]


# --- prompt rendering -------------------------------------------------------


def test_prompt_renders_match_provenance_and_theory_scope():
    text = repair_hints.format_repair_hints_for_prompt(
        [
            {
                "version": "r2025.02", "id": "605", "kind": "mechanism_change",
                "title": "split SmtMap", "repair_hint": "replace SmtMap with FMap",
                "overlap": ["FMap", "SmtMap"], "reason": "structural change matching symbol",
                "theories_touched": ["FMap", "SmtMap"],
                "url": "https://github.com/EasyCrypt/easycrypt/pull/605",
            }
        ],
        [],
    )
    assert "(mechanism_change)" in text
    assert "matched FMap, SmtMap" in text
    assert "changed theories: FMap, SmtMap" in text
    assert "pull/605" in text


def test_prompt_renders_import_repair_note_untruncated():
    note = "SmtMap no longer has finite-map operations; switch them to FMap. " * 8
    text = repair_hints.format_repair_hints_for_prompt(
        [],
        [{"path": "theories/datatypes/SmtMap.ec",
          "current_content_summary": "total maps only",
          "import_repair_note": note,
          "requires": "AllCore, CoreMap."}],
        summary_chars=40,
    )
    assert "IMPORT REPAIR:" in text
    assert note.strip() in text          # never clipped -- it is the actionable part
    assert "this theory requires: AllCore, CoreMap." in text


def test_prompt_tolerates_string_valued_version_diffs():
    """13 of the 18 repair_doc files store `version_diffs_found` as a sentence
    rather than a list; iterating the string yielded one character per line."""
    text = repair_hints.format_repair_hints_for_prompt(
        [],
        [{"path": "theories/core/CoreMap.ec",
          "current_content_summary": "abstract total maps",
          "version_diffs_found": "None found by name in the scanned changelog window."}],
    )
    assert "version note: N\n" not in text
    assert "version note:" not in text   # a "none found" sentence is not a note


def test_prompt_still_renders_legacy_entries_without_new_fields():
    text = repair_hints.format_repair_hints_for_prompt(
        [{"version": "r2025.02", "title": "split SmtMap", "repair_hint": "use FMap"}],
        [{"path": "theories/datatypes/FMap.ec", "current_content_summary": "maps"}],
    )
    assert "[r2025.02] split SmtMap: use FMap" in text
    assert "theories/datatypes/FMap.ec: maps" in text

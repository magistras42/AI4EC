"""Tests for the repair-doc reprocessor and its retrieval path.

Covers `proof_corpus/scripts/build_repair_docs.py` (parsing real EasyCrypt
sources, condensing authored prose, deriving import notes, building the
symbol index) and the `integration/agent/repair_hints.py` code that consumes
`output/repair_docs_index.json`.

The script is loaded by path, like the retriever, so these tests exercise the
real module rather than a copy. Theory sources are synthesized in tmp_path so
nothing depends on the vendored EasyCrypt checkout being present.
"""
from __future__ import annotations

import importlib.util
import re
import json
import types
from pathlib import Path

import pytest

from integration.agent import repair_hints


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "proof_corpus" / "scripts"


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
    return _load_script("build_repair_docs.py")


@pytest.fixture
def theory_tree(tmp_path: Path) -> Path:
    """A miniature EasyCrypt theory tree exercising each syntax variant the
    parser has to survive: multi-name requires, the `(*--*)` alignment
    comment, a bare `import`, `require export`, `clone import`, and
    `rename "x" as "y"` inside a clone."""
    theories = tmp_path / "theories"
    (theories / "datatypes").mkdir(parents=True)
    (theories / "distributions").mkdir(parents=True)

    (theories / "datatypes" / "SmtMap.ec").write_text(
        "require import AllCore CoreMap.\n"
        "\n"
        "op cst ['a 'b] : 'b -> ('a,'b) map.\n"
        "op eq_except : bool.\n"
        "lemma merge_id : true.\n",
        encoding="utf-8",
    )
    (theories / "datatypes" / "FMap.ec").write_text(
        "require import AllCore SmtMap Finite List.\n"
        "(*---*) import IntID IntOrder.\n"
        "require (*--*) Ring.\n"
        "require export StdOrder.\n"
        "\n"
        "import CoreMap.\n"
        "clone import Quotient as Q.\n"
        "\n"
        "type ('a, 'b) fmap.\n"
        "op dom ['a 'b] (m : ('a,'b) fmap) = true.\n"
        "op frng : bool.\n"
        "op eq_except : bool.\n"
        "lemma fmap_eqP : true.\n"
        "  lemma indented_is_local : true.\n",
        encoding="utf-8",
    )
    (theories / "distributions" / "DBool.ec").write_text(
        'require import AllCore Distr.\n'
        "\n"
        "clone MFinite as MUniform with\n"
        '  rename "dunifin" as "dbool"\n'
        '  rename "dunifinE" as "dboolE_count".\n"'
        "\n"
        "lemma dboolE : true.\n",
        encoding="utf-8",
    )
    return theories


@pytest.fixture
def repair_doc_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repair_doc"
    d.mkdir()
    (d / "smtmap_lib.json").write_text(json.dumps({
        "path": "theories/datatypes/SmtMap.ec",
        "current_content_summary": "Total maps. " + ("Filler sentence. " * 60),
        "import_repair_note": "MAJOR STRUCTURAL CHANGE, r2025.02: finite-map "
                              "operations moved to FMap.",
        "requires": "AllCore, CoreMap.",
        "version_diffs_found": ["r2025.02: MAJOR -- split (#605)."],
    }), encoding="utf-8")
    (d / "fmap_lib.json").write_text(json.dumps({
        "path": "theories/datatypes/FMap.ec",
        "current_content_summary": "Finite maps built on SmtMap.",
        "requires": "AllCore, SmtMap, Finite, List, Ring; imports CoreMap.",
        # The string-valued form 13 of the 18 real docs use.
        "version_diffs_found": "None found by name in the scanned changelog window.",
    }), encoding="utf-8")
    return d


@pytest.fixture
def built(builder, theory_tree, repair_doc_dir) -> dict:
    theories, symbol_index = builder.scan_theories([theory_tree])
    return builder.build(
        repair_doc_dir=repair_doc_dir,
        theories=theories,
        symbol_index=symbol_index,
        changelog_index={
            "schema": "ai4ec.changelog-index/1",
            "entries": [{
                "key": "r2025.02#605", "id": "605", "version": "r2025.02",
                "kind": "mechanism_change", "title": "split SmtMap",
                "repair_hint": "use FMap", "import_relevant": True,
                "url": "https://example/605",
            }],
            "indexes": {"by_theory": {"SmtMap": ["r2025.02#605"],
                                      "FMap": ["r2025.02#605"]}},
        },
        summary_chars=200,
        max_exports=0,
        include_symbol_index=True,
    )


# --- source parsing ---------------------------------------------------------


def test_parses_multi_name_and_comment_marked_requires(builder, theory_tree):
    facts = builder.parse_theory_source(theory_tree / "datatypes" / "FMap.ec")
    assert facts["requires"] == ["AllCore", "SmtMap", "Finite", "List", "Ring"]
    # `require export` is tracked separately: it re-exports names to whoever
    # requires *this* theory, which is a different repair fact.
    assert facts["require_exports"] == ["StdOrder"]


def test_bare_import_is_distinguished_from_require(builder, theory_tree):
    """`import X` needs X already required; `require import X` does both.
    Conflating them is itself a common import-repair mistake."""
    facts = builder.parse_theory_source(theory_tree / "datatypes" / "FMap.ec")
    assert "CoreMap" in facts["imports"]
    assert "CoreMap" not in facts["requires"]
    assert "IntID" in facts["imports"]


def test_clones_are_captured(builder, theory_tree):
    facts = builder.parse_theory_source(theory_tree / "datatypes" / "FMap.ec")
    assert "Quotient" in facts["clones"]


def test_indented_declarations_are_captured(builder, theory_tree):
    """Indentation must NOT disqualify a declaration: a `theory Foo. ... end
    Foo.` block indents its contents and those names are exported as
    `Foo.name`. Proof-internal bindings use different keywords (`have`,
    `pose`) and so never match the declaration pattern anyway."""
    facts = builder.parse_theory_source(theory_tree / "datatypes" / "FMap.ec")
    names = [n for names in facts["declarations"].values() for n in names]
    assert "fmap_eqP" in names
    assert "indented_is_local" in names


def test_clone_rename_introduces_exported_names(builder, theory_tree):
    """`rename "dunifin" as "dbool"` exports `dbool` even though nothing
    declares it with op/lemma. DBool.ec is exactly this case and it is one of
    the four libraries with a hand-written import note."""
    facts = builder.parse_theory_source(theory_tree / "distributions" / "DBool.ec")
    assert facts["declarations"].get("clone_rename") == ["dbool", "dboolE_count"]


# --- symbol index -----------------------------------------------------------


def test_symbol_index_maps_names_to_declaring_theories(builder, theory_tree):
    _theories, symbol_index = builder.scan_theories([theory_tree])
    assert symbol_index["frng"] == ["FMap"]
    assert symbol_index["dbool"] == ["DBool"]
    # A theory's own name resolves to itself, so `require import FMap.` is
    # reachable from a bare mention of FMap.
    assert symbol_index["FMap"] == ["FMap"]


def test_symbol_index_keeps_every_owner_of_an_ambiguous_name(builder, theory_tree):
    """`eq_except` is declared by both sides of the r2025.02 split. Silently
    picking one is exactly the guess a repair agent must not make."""
    _theories, symbol_index = builder.scan_theories([theory_tree])
    assert symbol_index["eq_except"] == ["FMap", "SmtMap"]


# --- derivation -------------------------------------------------------------


def test_authored_import_note_is_preserved_verbatim(built):
    lib = next(l for l in built["libraries"] if l["theory"] == "SmtMap")
    assert lib["import_repair_note_source"] == "authored"
    assert lib["import_repair_note"].startswith("MAJOR STRUCTURAL CHANGE")


def test_import_note_is_derived_when_none_was_authored(built):
    lib = next(l for l in built["libraries"] if l["theory"] == "FMap")
    assert lib["import_repair_note_source"] == "derived"
    note = lib["import_repair_note"]
    assert "`require import FMap.` needs" in note
    assert "SmtMap" in note
    assert "declares" in note


def test_summary_is_condensed(built):
    lib = next(l for l in built["libraries"] if l["theory"] == "SmtMap")
    assert lib["summary_full_chars"] > 500
    assert len(lib["summary"]) <= 220
    # Condensing prefers a sentence boundary over a mid-word cut.
    assert lib["summary"].endswith((".", "..."))


def test_requires_is_the_parsed_list_not_the_prose(built):
    lib = next(l for l in built["libraries"] if l["theory"] == "FMap")
    assert lib["requires"] == ["AllCore", "SmtMap", "Finite", "List", "Ring"]
    assert lib["requires_prose"].startswith("AllCore, SmtMap")


def test_none_found_version_diffs_are_dropped(built):
    """13 of the 18 real docs record "no diffs found" as a sentence where a
    list belongs; that is not a version note."""
    lib = next(l for l in built["libraries"] if l["theory"] == "FMap")
    assert lib["version_notes"] == []
    smtmap = next(l for l in built["libraries"] if l["theory"] == "SmtMap")
    assert smtmap["version_notes"] == ["r2025.02: MAJOR -- split (#605)."]


def test_changelog_entries_are_attached_per_theory(built):
    lib = next(l for l in built["libraries"] if l["theory"] == "SmtMap")
    assert [e["id"] for e in lib["changelog"]] == ["605"]
    assert lib["changed_in"] == ["r2025.02"]
    assert lib["import_relevant_changes"]


def test_requires_mismatch_ignores_trailing_punctuation_and_imports(builder):
    """Regression: the source-name regex allowed dots, so a sentence-final
    "... FinType." never matched "FinType" and every doc looked mismatched.
    Prose that describes `imports` must not read as a bogus require claim."""
    assert builder.cross_check_requires(
        "AllCore, SmtMap, Finite; imports CoreMap.",
        ["AllCore", "SmtMap", "Finite"],
        also_declared=["CoreMap"],
    ) is None


def test_requires_mismatch_reports_a_real_disagreement(builder):
    result = builder.cross_check_requires(
        "AllCore, List.", ["AllCore", "List", "Discrete"],
    )
    assert result["in_source_not_in_prose"] == ["Discrete"]


def test_as_list_normalizes_string_and_list_forms(builder):
    assert builder.as_list(["a", "b"]) == ["a", "b"]
    assert builder.as_list("one sentence") == ["one sentence"]
    assert builder.as_list(None) == []


# --- retrieval integration --------------------------------------------------


@pytest.fixture
def corpus_with_index(tmp_path, built, monkeypatch) -> Path:
    root = tmp_path / "corpus"
    (root / "output").mkdir(parents=True)
    (root / "repair_doc").mkdir()
    (root / "scripts").mkdir()
    (root / "output" / "repair_docs_index.json").write_text(
        json.dumps(built), encoding="utf-8"
    )
    monkeypatch.setenv("SHANNON_PROOF_CORPUS_DIR", str(root))
    return root


def test_index_is_loaded_when_present(corpus_with_index):
    index = repair_hints.load_repair_docs_index()
    assert index is not None
    assert index["schema"].startswith("ai4ec.repair-docs-index/")


def test_load_index_returns_none_without_the_file(tmp_path, monkeypatch):
    root = tmp_path / "bare"
    (root / "output").mkdir(parents=True)
    monkeypatch.setenv("SHANNON_PROOF_CORPUS_DIR", str(root))
    assert repair_hints.load_repair_docs_index() is None


def test_resolve_symbol_theories_answers_the_import_question(corpus_with_index):
    resolved = repair_hints.resolve_symbol_theories({"frng", "eq_except", "nosuch"})
    assert resolved["frng"] == ["FMap"]
    assert resolved["eq_except"] == ["FMap", "SmtMap"]
    assert "nosuch" not in resolved


def test_snippets_rank_the_theory_that_declares_the_symbol_first(corpus_with_index):
    snippets = repair_hints.get_repair_doc_snippets(identifiers={"frng"}, max_docs=2)
    assert snippets[0]["theory"] == "FMap"
    assert snippets[0]["_declares_matched_symbol"] is True
    # The renderer's expected keys are populated from the condensed record.
    assert snippets[0]["current_content_summary"]
    assert snippets[0]["requires"].startswith("AllCore, SmtMap")


def test_prompt_leads_with_symbol_resolution():
    text = repair_hints.format_repair_hints_for_prompt(
        [], [], symbol_theories={"frng": ["FMap"], "eq_except": ["FMap", "SmtMap"]},
    )
    assert text.startswith("Where the names in this step are declared")
    assert "`require import FMap.`" in text
    assert "declared in 2 theories" in text


def test_prompt_omits_symbol_block_when_nothing_resolves():
    text = repair_hints.format_repair_hints_for_prompt(
        [{"version": "r2025.02", "title": "t", "repair_hint": "h"}], [],
        symbol_theories={},
    )
    assert not text.startswith("Where the names")


# --- the real corpus (W5) ---------------------------------------------------
# A derived note states verified facts -- what a theory requires, what it
# declares -- but cannot say WHY something broke or what to do instead, and
# "why" is the part that changes what the model tries. These check the real
# checked-in notes, not the synthetic fixture above.

REPO_ROOT = Path(__file__).resolve().parents[2]
REPAIR_DOC = REPO_ROOT / "proof_corpus" / "repair_doc"
REAL_INDEX = REPO_ROOT / "proof_corpus" / "output" / "repair_docs_index.json"

#: Something a reader can go and check: a release tag, a commit SHA, or a file.
_EVIDENCE = re.compile(r"r20\d\d\.\d\d|\b[0-9a-f]{8,9}\b|\.(?:ec|eca|ml|mll)\b")


def _real_docs() -> list[tuple[str, dict]]:
    if not REPAIR_DOC.is_dir():
        pytest.skip("repair_doc/ not present")
    return [
        (path.name, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(REPAIR_DOC.glob("*_lib.json"))
    ]


def test_authored_notes_now_outnumber_derived_ones():
    """The ratio started inverted: 4 authored, 14 derived."""
    authored = [name for name, doc in _real_docs() if doc.get("import_repair_note")]
    assert len(authored) >= 14, f"only {len(authored)} authored: {authored}"


def test_every_authored_note_cites_something_checkable():
    """An authored note's value is that it explains a change. An explanation
    nobody can verify is indistinguishable from a guess -- and the surrounding
    prose in these files carries a `caveat` saying exactly that ("No true
    git-diff was possible")."""
    for name, doc in _real_docs():
        note = doc.get("import_repair_note")
        if not note:
            continue
        assert _EVIDENCE.search(note), f"{name} cites no release, commit or source"


def test_the_cost_logic_removal_is_explained_where_it_bit():
    """r2024.09 deleted the complexity subsystem from the whole standard
    library AND removed `cost`/`schema` from the lexer, so a pre-r2024.09
    proof carrying annotations fails with a PARSE error rather than an unknown
    symbol. Every library that lost names to it must say so: hints are
    retrieved per theory, so a proof failing on DInterval never sees
    AllCore's note."""
    affected = {
        "allcore_lib.json", "bool_lib.json", "dbool_lib.json",
        "dinverval_lib.json", "smtmap_lib.json",
    }
    for name, doc in _real_docs():
        if name not in affected:
            continue
        note = doc.get("import_repair_note") or ""
        assert "cost" in note.lower(), f"{name} does not mention the cost removal"
        assert "41c2667f" in note, f"{name} does not cite the removing commit"


def test_no_authored_note_tells_a_proof_to_require_an_engine_theory():
    """`Pervasive` and `Logic` are exported into every file by the engine, so
    "add a require" is wrong advice however it is phrased. The generated rules
    are guarded by ENGINE_PRELOADED; the prose needs the same guard."""
    bad = re.compile(r"require\s+(?:import\s+)?(?:\w+\s+)*(Pervasive|Logic)\b")
    # The negation can sit on either side of the phrase -- "NEVER add `require
    # import Pervasive.`" puts it before, "'you must require Pervasive' is
    # actively wrong advice" puts it after -- so read the whole sentence.
    negations = ("never", "not ", "wrong", "no generated rule", "cannot")
    for name, doc in _real_docs():
        note = doc.get("import_repair_note") or ""
        for match in bad.finditer(note):
            start = note.rfind(".", 0, match.start()) + 1
            end = note.find(".", match.end())
            sentence = note[start: end if end != -1 else len(note)]
            assert any(word in sentence.lower() for word in negations), (
                f"{name} appears to advise requiring {match.group(1)}: "
                f"...{sentence.strip()}"
            )


def test_the_built_index_agrees_with_the_authored_files():
    if not REAL_INDEX.is_file():
        pytest.skip("repair_docs_index.json not generated")
    index = json.loads(REAL_INDEX.read_text(encoding="utf-8"))
    authored = [
        lib for lib in index["libraries"]
        if lib.get("import_repair_note_source") == "authored"
    ]
    assert len(authored) >= 14
    # Regenerating must not have dropped a note on the floor.
    on_disk = {name for name, doc in _real_docs() if doc.get("import_repair_note")}
    assert len(authored) == len(on_disk)

"""Tests for the pre-proof import repairer and the migration manifest.

`integration/agent/import_repair.py` reads `proof_corpus/ec_migrations.toml`
and rewrites a `.ec` file's requires/syntax so EasyCrypt can load it at all.
These tests cover manifest parsing, version selection, matching, each action's
line-preserving edit, and the verification loop -- with EasyCrypt stubbed, so
the suite runs without the built binary.

A separate test parses the real checked-in manifest, since a manifest that
does not load is a silent failure at repair time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from integration.agent import import_repair
from integration.agent.config import AgentConfig
from integration.agent.easycrypt import LlmResult


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST = REPO_ROOT / "proof_corpus" / "ec_migrations.toml"


MANIFEST = """
schema = "ai4ec.ec-migrations/1"

[meta]
known_versions = ["r2022.04", "r2023.09", "r2024.09", "r2025.02", "r2026.07"]

[[migration]]
id = "smtmap-split"
kind = "theory_split"
breaks_at = "r2025.02"
confidence = "high"
summary = "SmtMap split; finite-map ops moved to FMap."
  [migration.match]
  requires_theory = ["SmtMap"]
  uses_symbols = ["fdom", "frng"]
  [[migration.action]]
  op = "add_require"
  theory = "FMap"
  after = "SmtMap"

[[migration]]
id = "proc-star"
kind = "syntax_change"
breaks_at = "r2023.09"
confidence = "high"
summary = "`proc *` is no longer parsed."
  [migration.match]
  matches_regex = '\\bproc\\s+\\*\\s'
  [[migration.action]]
  op = "replace_regex"
  pattern = '\\bproc\\s+\\*\\s'
  replacement = "proc "

[[migration]]
id = "always-on"
kind = "require_semantics"
confidence = "low"
summary = "No breaks_at: applies whenever source < target."
  [migration.match]
  requires_theory = ["AllCore"]
  missing_require = ["List"]
  uses_symbols = ["nth"]
  [[migration.action]]
  op = "add_require"
  theory = "List"
  after = "AllCore"
"""

SOURCE = """require import AllCore SmtMap.
require import StdOrder.

module type Oracle = {
  proc * init() : unit
  proc get(x : int) : int
}.

lemma l : fdom m = frng m.
proof. admit. qed.
"""


@pytest.fixture
def manifest_path(tmp_path) -> Path:
    path = tmp_path / "ec_migrations.toml"
    path.write_text(MANIFEST, encoding="utf-8")
    return path


@pytest.fixture
def manifest(manifest_path) -> dict:
    return import_repair.load_manifest(manifest_path)


@pytest.fixture
def migrations(manifest) -> list[import_repair.Migration]:
    return import_repair.parse_migrations(manifest)


class FakeEasyCrypt:
    """Stands in for `validate_file`.

    `script` maps a predicate over the file text to (returncode, stderr), so a
    test can say "this file fails at line 108 until `proc *` is gone".
    """

    def __init__(self, script):
        self.script = script
        self.calls = 0

    def __call__(self, path: Path, config: AgentConfig) -> LlmResult:
        self.calls += 1
        text = Path(path).read_text(encoding="utf-8")
        for predicate, (code, err) in self.script:
            if predicate(text):
                return LlmResult(returncode=code, stdout="", stderr=err)
        return LlmResult(returncode=0, stdout="", stderr="")


def _err(line: int) -> str:
    return f"[critical] [/tmp/x.ec: line {line} (8)] parse error"


# --- manifest ---------------------------------------------------------------


def test_real_manifest_parses_and_has_the_expected_shape():
    """The checked-in manifest must load: a broken one fails at repair time,
    long after anyone would notice."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    migrations = import_repair.parse_migrations(manifest)
    assert migrations
    ids = {m.id for m in migrations}
    # Curated engine rules (parser/module-system changes, not derivable from
    # theory-file history).
    assert {"proc-star-removed", "declare-module-ascription"} <= ids
    # Every action must be one the applier implements, and line-preserving.
    for migration in migrations:
        for action in migration.actions:
            assert action["op"] in import_repair.LINE_PRESERVING_OPS
    assert manifest["meta"]["known_versions"]
    assert manifest["meta"]["tracked_libraries"]


def test_real_manifest_derives_the_smtmap_split_from_git_history():
    """The r2025.02 SmtMap -> FMap split must come out of the commit history,
    not a hand-written symbol list: 125 declarations leave SmtMap and arrive in
    FMap in that release."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    moves = [
        m for m in manifest["migration"]
        if m["kind"] == "symbol_moved" and m.get("breaks_at") == "r2025.02"
    ]
    assert moves, "expected a derived SmtMap -> FMap symbol move at r2025.02"
    move = moves[0]
    assert move["match"]["requires_theory"] == ["SmtMap"]
    assert move["match"]["missing_require"] == ["FMap"]
    assert move["action"][0] == {"op": "add_require", "theory": "FMap", "after": "SmtMap"}
    provenance = move["provenance"]
    assert provenance["derived_from"] == "library_history.json"
    assert provenance["moved_symbol_count"] >= 100
    # Names the hand-written note called out by name must be in the match set.
    assert {"dom", "empty"} <= set(move["match"]["uses_symbols"])


def test_real_manifest_never_requires_an_engine_preloaded_theory():
    """`Pervasive` and `Logic` are exported into every file by the engine
    (ecCommands.ml), so a rule telling a proof to require them is wrong."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    for migration in manifest["migration"]:
        for action in migration.get("action") or []:
            if action.get("op") == "add_require":
                assert action["theory"] not in {"Pervasive", "Logic"}


def test_real_manifest_has_no_rule_keyed_to_the_earliest_tag():
    """Every commit reachable from the oldest tag is attributed to it,
    including a decade of pre-tag history, so "changed in <earliest>" cannot be
    distinguished from "existed before our window"."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    earliest = manifest["meta"].get("earliest_tag")
    if not earliest:
        pytest.skip("manifest records no earliest tag")
    assert all(m.get("breaks_at") != earliest for m in manifest["migration"])


def test_load_manifest_rejects_a_foreign_schema(tmp_path):
    path = tmp_path / "x.toml"
    path.write_text('schema = "something/else"\n', encoding="utf-8")
    with pytest.raises(import_repair.ImportRepairUnavailable, match="schema"):
        import_repair.load_manifest(path)


def test_load_manifest_missing_file_is_unavailable_not_a_crash(tmp_path):
    with pytest.raises(import_repair.ImportRepairUnavailable, match="not found"):
        import_repair.load_manifest(tmp_path / "nope.toml")


# --- version selection ------------------------------------------------------


def test_selects_only_migrations_the_version_window_crosses(migrations, manifest):
    known = manifest["meta"]["known_versions"]
    selected = import_repair.select_by_version(
        migrations, "r2024.09", "r2026.07", known
    )
    ids = {m.id for m in selected}
    assert "smtmap-split" in ids       # breaks at r2025.02, inside the window
    assert "proc-star" not in ids      # breaks at r2023.09, before the source
    assert "always-on" in ids          # no breaks_at


def test_unpinned_migrations_always_apply(migrations, manifest):
    selected = import_repair.select_by_version(
        migrations, "r2025.02", "r2026.07", manifest["meta"]["known_versions"]
    )
    assert "always-on" in {m.id for m in selected}


def test_unknown_endpoint_considers_everything(migrations, manifest):
    """EasyCrypt's releases only reach back to r2022.04, so a 2020-era proof
    has no source tag. Fail-open matches retrieve_entries.releases_in_range."""
    selected = import_repair.select_by_version(
        migrations, "r2020.01", "r2026.07", manifest["meta"]["known_versions"]
    )
    assert len(selected) == len(migrations)


# --- matching ---------------------------------------------------------------


def test_required_theories_parses_every_require_form():
    text = (
        "require import AllCore SmtMap Finite.\n"
        "require (*--*) Ring.\n"
        "require export StdOrder.\n"
        "from Top require import Extra.\n"
    )
    assert import_repair.required_theories(text) >= {
        "AllCore", "SmtMap", "Finite", "Ring", "StdOrder", "Extra"
    }


def test_match_requires_all_present_conditions(migrations):
    by_id = {m.id: m for m in migrations}
    tokens = import_repair.file_tokens(SOURCE)
    requires = import_repair.required_theories(SOURCE)
    assert import_repair.matches(by_id["smtmap-split"], SOURCE, tokens, requires)

    # Same symbols, but SmtMap is not required -> no match.
    other = SOURCE.replace("SmtMap", "CoreMap")
    assert not import_repair.matches(
        by_id["smtmap-split"], other,
        import_repair.file_tokens(other), import_repair.required_theories(other),
    )


def test_missing_require_condition_is_negative(migrations):
    by_id = {m.id: m for m in migrations}
    text = "require import AllCore.\nlemma l : nth 0 s = 1.\n"
    assert import_repair.matches(
        by_id["always-on"], text,
        import_repair.file_tokens(text), import_repair.required_theories(text),
    )
    with_list = "require import AllCore List.\nlemma l : nth 0 s = 1.\n"
    assert not import_repair.matches(
        by_id["always-on"], with_list,
        import_repair.file_tokens(with_list),
        import_repair.required_theories(with_list),
    )


# --- actions ----------------------------------------------------------------


def test_add_require_extends_an_existing_line_preserving_line_count():
    text, applied, _ = import_repair.apply_actions(
        SOURCE, [{"op": "add_require", "theory": "FMap", "after": "SmtMap"}]
    )
    assert "require import AllCore SmtMap FMap." in text
    assert text.count("\n") == SOURCE.count("\n")
    assert applied == ["add_require FMap"]


def test_add_require_is_a_noop_when_already_required():
    text, applied, skipped = import_repair.apply_actions(
        SOURCE, [{"op": "add_require", "theory": "SmtMap"}]
    )
    assert text == SOURCE
    assert not applied
    assert skipped


def test_replace_require_swaps_only_inside_require_lines():
    text, applied, _ = import_repair.apply_actions(
        SOURCE, [{"op": "replace_require", "theory": "SmtMap", "with_theory": "FMap"}]
    )
    assert "require import AllCore FMap." in text
    assert applied


def test_remove_require_blanks_rather_than_deleting_the_line():
    """Deleting the line would shift every later line number and invalidate
    the lemma positions ProofCase records."""
    text = "require import Foo.\nlemma l : true.\n"
    out, applied, _ = import_repair.apply_actions(
        text, [{"op": "remove_require", "theory": "Foo"}]
    )
    assert out.count("\n") == text.count("\n")
    assert "Foo" not in out
    assert applied


def test_add_pragma_folds_onto_line_one():
    out, applied, _ = import_repair.apply_actions(
        SOURCE, [{"op": "add_pragma", "pragma": "+old_mem_restr"}]
    )
    assert out.splitlines()[0].startswith("pragma +old_mem_restr. require import")
    assert out.count("\n") == SOURCE.count("\n")
    assert applied


def test_replace_regex_removes_the_proc_star_marker():
    out, applied, _ = import_repair.apply_actions(
        SOURCE, [{"op": "replace_regex", "pattern": r"\bproc\s+\*\s", "replacement": "proc "}]
    )
    assert "proc init() : unit" in out
    assert "proc *" not in out
    assert out.count("\n") == SOURCE.count("\n")


def test_rename_symbol_is_whole_token_only():
    text = "lemma l : dunifin = dunifinX.\n"
    out, _applied, _ = import_repair.apply_actions(
        text, [{"op": "rename_symbol", "old": "dunifin", "new": "dbool"}]
    )
    assert "dbool = dunifinX" in out


def test_unknown_op_is_skipped_not_guessed():
    out, applied, skipped = import_repair.apply_actions(
        SOURCE, [{"op": "teleport", "theory": "X"}]
    )
    assert out == SOURCE
    assert not applied
    assert any("unknown op" in s for s in skipped)


def test_line_count_change_is_an_assertion_error(monkeypatch):
    """The applier's own guard against an action that would break ProofCase."""
    monkeypatch.setattr(
        import_repair, "_add_pragma", lambda text, pragma: ("extra\n" + text, True)
    )
    with pytest.raises(AssertionError, match="line count"):
        import_repair.apply_actions(SOURCE, [{"op": "add_pragma", "pragma": "x"}])


# --- error-line parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "output,expected",
    [
        ("[critical] [/tmp/x.ec: line 108 (8)] parse error", 108),
        ("/tmp/x.eca:120:4-9: something", 120),
        ("no location here", -1),
    ],
)
def test_first_error_line_reads_both_easycrypt_formats(output, expected):
    """Regression: only the second format was matched, so every probe returned
    -1, no migration could show progress, and all of them were rolled back."""
    assert import_repair._first_error_line(output) == expected


# --- verification loop ------------------------------------------------------


def _config() -> AgentConfig:
    return AgentConfig(easycrypt_bin=Path("/nonexistent/ec.exe"))


def test_file_that_already_loads_is_left_alone(tmp_path, monkeypatch, manifest_path):
    monkeypatch.setattr(import_repair, "validate_file", FakeEasyCrypt([]))
    source = tmp_path / "ok.ec"
    source.write_text(SOURCE, encoding="utf-8")
    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path,
    )
    assert not result.changed
    assert result.loads_before and result.loads_after
    assert source.read_text(encoding="utf-8") == SOURCE


def test_bulk_apply_short_circuits_when_it_makes_the_file_load(
    tmp_path, monkeypatch, manifest_path
):
    fake = FakeEasyCrypt([(lambda t: "proc *" in t, (1, _err(5)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert result.loads_after and result.improved
    assert "proc *" not in result.text
    assert all(a.kept for a in result.applied)
    # Two probes: the baseline and the bulk attempt. No incremental pass.
    assert fake.calls == 2


def test_incremental_pass_keeps_non_regressing_migrations(
    tmp_path, monkeypatch, manifest_path
):
    """When bulk apply does not fully fix the file, each rule is retried alone
    and kept unless it makes EasyCrypt stop earlier."""
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t, (1, _err(5))),    # before the fix
        (lambda t: True, (1, _err(400))),           # after: further in, still broken
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)

    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")
    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert not result.loads_after
    # The file never loads, but the first error moved 5 -> 400.
    assert result.improved
    assert result.error_line_before == 5
    assert result.error_line_after == 400
    assert "proc-star" in {a.migration_id for a in result.applied if a.kept}


def test_regressing_migration_is_rolled_back(tmp_path, monkeypatch, manifest_path):
    """A rule that makes EasyCrypt stop EARLIER is discarded."""
    fake = FakeEasyCrypt([
        (lambda t: "FMap" in t, (1, _err(2))),   # adding FMap made things worse
        (lambda t: True, (1, _err(50))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)

    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")
    result = import_repair.repair_imports(
        source, _config(), source_version="r2024.09", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="high",
    )
    smtmap = [a for a in result.applied if a.migration_id == "smtmap-split"]
    assert smtmap and not smtmap[0].kept
    assert "rolled back" in smtmap[0].reason
    assert "FMap" not in result.text


def test_missing_manifest_degrades_gracefully(tmp_path, monkeypatch):
    fake = FakeEasyCrypt([(lambda t: True, (1, _err(9)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")
    result = import_repair.repair_imports(
        source, _config(), manifest_path=tmp_path / "absent.toml",
    )
    assert not result.changed
    assert any("unavailable" in n for n in result.notes)


def test_source_file_is_never_modified(tmp_path, monkeypatch, manifest_path):
    fake = FakeEasyCrypt([(lambda t: "proc *" in t, (1, _err(5)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")
    import_repair.repair_imports(
        source, _config(), manifest_path=manifest_path, min_confidence="low",
    )
    assert source.read_text(encoding="utf-8") == SOURCE


def test_min_confidence_filters_low_rules(tmp_path, monkeypatch, manifest_path):
    fake = FakeEasyCrypt([(lambda t: True, (1, _err(9)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(
        "require import AllCore.\nlemma l : nth 0 s = 1.\n", encoding="utf-8"
    )
    high = import_repair.repair_imports(
        source, _config(), manifest_path=manifest_path, min_confidence="high",
    )
    assert "always-on" not in high.considered
    low = import_repair.repair_imports(
        source, _config(), manifest_path=manifest_path, min_confidence="low",
    )
    assert "always-on" in low.considered


# --- prompt rendering -------------------------------------------------------


def test_prompt_summary_lists_what_was_changed():
    result = import_repair.ImportRepairResult(
        changed=True, text="", considered=["proc-star"],
        applied=[import_repair.AppliedMigration(
            "proc-star", "syntax_change", "high", "`proc *` is gone.",
            ["replace_regex (2)"], True, "file loads",
        )],
        loads_before=False, loads_after=True, error_before="x", error_after="",
    )
    text = import_repair.format_for_prompt(result)
    assert "proc-star" in text
    assert "syntax_change" in text
    assert "replace_regex (2)" in text


def test_prompt_summary_is_empty_when_nothing_was_kept():
    result = import_repair.ImportRepairResult(
        changed=False, text="", applied=[], considered=[],
        loads_before=False, loads_after=False, error_before="", error_after="",
    )
    assert import_repair.format_for_prompt(result) == ""

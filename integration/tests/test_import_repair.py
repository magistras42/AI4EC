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

from integration.agent import ec_errors, import_repair
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
        self.texts: list[str] = []

    def __call__(self, path: Path, config: AgentConfig) -> LlmResult:
        self.calls += 1
        text = Path(path).read_text(encoding="utf-8")
        self.texts.append(text)
        for predicate, (code, err) in self.script:
            if predicate(text):
                return LlmResult(returncode=code, stdout="", stderr=err)
        return LlmResult(returncode=0, stdout="", stderr="")


def _err(line: int, message: str = "parse error") -> str:
    return f"[critical] [/tmp/x.ec: line {line} (8)] {message}"


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


def test_real_manifest_has_symbol_moves_as_a_category_not_an_anecdote():
    """`symbol_moved` had exactly one instance, because the history miner
    tracked 16 hand-picked theories and a move is only visible when BOTH ends
    are tracked. Mining every theory in the tree makes the category real."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    moves = [m for m in manifest["migration"] if m["kind"] == "symbol_moved"]
    assert len(moves) >= 4, f"only {len(moves)} symbol moves"
    # Each is a distinct source theory losing names to a distinct destination.
    assert len({(m["match"]["requires_theory"][0],
                 m["match"]["missing_require"][0]) for m in moves}) == len(moves)


def test_symbol_move_rules_carry_their_absorption_evidence():
    """A move is only a move if the destination absorbed a real share of the
    source. Co-occurrence alone put OldFMap -> PolyReduce in the manifest at
    3%, because PolyReduce arrived from Kyber that release and defines
    `reduce`."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    for migration in manifest["migration"]:
        if migration["kind"] != "symbol_moved":
            continue
        provenance = migration["provenance"]
        assert provenance["absorption_fraction"] >= 0.10, migration["id"]
        assert provenance["distinctive_symbol_count"] >= 3, migration["id"]


def test_no_derived_rule_matches_on_a_name_common_to_many_theories():
    """These rules fire on a token appearing anywhere in a file and then
    rewrite its imports. `add`, `mul`, `opp` live in 5-8 theories each because
    every algebraic structure declares them -- keying a rewrite on one is how
    BitWord got a rule saying its operators had moved to Ring."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    docs_index = REPO_ROOT / "proof_corpus" / "output" / "repair_docs_index.json"
    if not docs_index.is_file():
        pytest.skip("repair_docs_index.json not generated")
    index = json.loads(docs_index.read_text(encoding="utf-8"))["symbol_index"]

    manifest = import_repair.load_manifest(REAL_MANIFEST)
    offenders = []
    for migration in manifest["migration"]:
        if not (migration.get("provenance") or {}).get("derived_from"):
            continue  # curated engine rules match on regexes, not names
        for name in migration["match"].get("uses_symbols") or []:
            if len(index.get(name) or []) > 4:
                offenders.append((migration["id"], name, len(index[name])))
    assert not offenders, f"non-distinctive match symbols: {offenders[:10]}"


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


# --- error-directed ordering ------------------------------------------------
# `matches()` asks "could this rule apply to this file?"; relevance asks "does
# it address the error EasyCrypt actually reported?". Ordering by the second
# without ever excluding on it is what makes the likely fix cheap while keeping
# every fix reachable.


def _classify(message: str, line: int = 5):
    return ec_errors.classify_error(_err(line, message))


def test_migration_targets_collects_names_from_both_match_and_actions(migrations):
    by_id = {m.id: m for m in migrations}
    targets = import_repair.migration_targets(by_id["smtmap-split"])
    assert "SmtMap" in targets      # matched on
    assert "fdom" in targets        # matched on
    assert "FMap" in targets        # produced by the action


def test_relevance_scores_kind_and_named_identifier_separately(migrations):
    by_id = {m.id: m for m in migrations}
    split = by_id["smtmap-split"]

    # Kind alone: theory_split can fix an unknown theory, but no name matches.
    kind_only = _classify("cannot find theory: `Nowhere'")
    assert import_repair.relevance(split, kind_only) == (
        import_repair.RELEVANCE_MATCHING_KIND
    )

    # Kind and the blamed name: the strongest signal available.
    both = _classify("cannot find theory: `FMap'")
    assert import_repair.relevance(split, both) == (
        import_repair.RELEVANCE_MATCHING_KIND + import_repair.RELEVANCE_NAMED_IDENTIFIER
    )

    # A parse error is the engine's grammar; requiring theories cannot fix it.
    assert import_repair.relevance(split, _classify("parse error")) == 0


def test_relevance_is_zero_for_an_unclassifiable_error(migrations):
    """`unknown` must not pretend to rank: it means the heuristic did not
    recognise the message, so every rule stays equally plausible."""
    unknown = ec_errors.classify_error("something nobody has seen before")
    assert unknown.kind == ec_errors.KIND_UNKNOWN
    assert all(import_repair.relevance(m, unknown) == 0 for m in migrations)
    assert all(import_repair.relevance(m, None) == 0 for m in migrations)


def test_order_by_relevance_puts_the_addressing_rule_first_and_keeps_the_rest(
    migrations,
):
    """Ordering, not filtering: the irrelevant rules stay in the list, in
    manifest order, behind the relevant one."""
    ordered = import_repair.order_by_relevance(migrations, _classify("parse error"))
    assert ordered[0].id == "proc-star"           # the syntax_change rule
    assert len(ordered) == len(migrations)        # nothing dropped
    assert [m.id for m in ordered[1:]] == [
        m.id for m in migrations if m.id != "proc-star"
    ]                                             # ties keep manifest order


def test_order_by_relevance_is_identity_without_a_classification(migrations):
    for error in (None, ec_errors.classify_error("no idea")):
        assert [m.id for m in import_repair.order_by_relevance(migrations, error)] == [
            m.id for m in migrations
        ]


def test_incremental_pass_tries_the_rule_that_addresses_the_error_first(
    tmp_path, monkeypatch, manifest_path
):
    """The parse error is what EasyCrypt reported, so `proc-star` is probed
    before `smtmap-split` even though the manifest lists it second."""
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t, (1, _err(5, "parse error"))),
        (lambda t: True, (1, _err(400, "parse error"))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    # texts[0] baseline, texts[1] bulk, texts[2] the first incremental trial.
    first_trial = fake.texts[2]
    assert "proc *" not in first_trial      # proc-star was applied ...
    assert "FMap" not in first_trial        # ... and smtmap-split was not


def test_targeting_readvances_as_the_error_changes(
    tmp_path, monkeypatch, manifest_path
):
    """The classification is re-read after every accepted rule, so the ordering
    follows the file: a parse error pulls the syntax rule forward, and the
    missing theory it uncovers pulls the theory rule forward next."""
    # The file never fully loads, so the bulk pass cannot short-circuit and the
    # incremental pass runs to the end.
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t, (1, _err(5, "parse error"))),
        (lambda t: True, (1, _err(108, "cannot find theory: `FMap'"))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    # Over every record, kept or later minimised away: what is under test is
    # which error each rule was *selected against*, not whether it survived.
    picked = {a.migration_id: a for a in result.applied}
    # Each rule was chosen against the error that was live at the time.
    assert picked["proc-star"].selected_for == ec_errors.KIND_PARSE_ERROR
    assert picked["smtmap-split"].selected_for == ec_errors.KIND_UNKNOWN_THEORY
    # ... and the second was chosen on the strongest evidence: right kind, and
    # the rule names the theory EasyCrypt blamed.
    assert picked["smtmap-split"].relevance == (
        import_repair.RELEVANCE_MATCHING_KIND + import_repair.RELEVANCE_NAMED_IDENTIFIER
    )


def test_an_unaddressing_rule_is_still_tried(tmp_path, monkeypatch, manifest_path):
    """Fail-open. A rule scoring zero against the current error goes last, not
    away -- the file's *next* error may be exactly what it fixes."""
    fake = FakeEasyCrypt([
        # Only adding FMap fixes this, but the error is a parse error, which
        # smtmap-split scores zero against.
        (lambda t: "FMap" not in t, (1, _err(5, "parse error"))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert result.loads_after
    kept = {a.migration_id for a in result.applied if a.kept}
    assert "smtmap-split" in kept


def test_a_changed_error_kind_at_the_same_line_counts_as_progress(
    tmp_path, monkeypatch, manifest_path
):
    """`parse error` becoming `cannot find theory` at the same position means
    the syntax is now accepted. The line number cannot show that; the kind can,
    and without it the rule would read as "no regression" instead of a fix."""
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t, (1, _err(5, "parse error"))),
        (lambda t: True, (1, _err(5, "cannot find theory: `Nowhere'"))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    proc_star = next(a for a in result.applied if a.migration_id == "proc-star")
    assert proc_star.kept
    assert "error kind changed" in proc_star.reason
    assert "parse_error -> unknown_theory" in proc_star.reason


def test_result_records_what_broke_before_and_after(
    tmp_path, monkeypatch, manifest_path
):
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t, (1, _err(5, "parse error"))),
        (lambda t: True, (1, _err(400, "cannot find theory: `Nowhere'"))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert result.error_kind_before == ec_errors.KIND_PARSE_ERROR
    assert result.error_kind_after == ec_errors.KIND_UNKNOWN_THEORY
    payload = result.to_dict()
    assert payload["error_kind_before"] == ec_errors.KIND_PARSE_ERROR
    assert payload["error_kind_after"] == ec_errors.KIND_UNKNOWN_THEORY
    assert all("selected_for" in entry for entry in payload["applied"])


def test_a_loading_file_reports_no_remaining_error_kind(
    tmp_path, monkeypatch, manifest_path
):
    """Empty, not `unknown`: there is no error left to classify, and `unknown`
    would read as "something broke that we could not name"."""
    fake = FakeEasyCrypt([(lambda t: "proc *" in t, (1, _err(5)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), manifest_path=manifest_path, min_confidence="low",
    )
    assert result.loads_after
    assert result.error_kind_after == ""


def test_every_manifest_kind_is_reachable_from_some_error_kind():
    """A `kind =` value no error routes to is a rule that targeting can only
    ever deprioritise -- either the table or the manifest is wrong."""
    if not REAL_MANIFEST.is_file():
        pytest.skip("ec_migrations.toml not generated")
    manifest = import_repair.load_manifest(REAL_MANIFEST)
    used = {m["kind"] for m in manifest["migration"]}
    routed = set().union(*import_repair.MIGRATION_KINDS_BY_ERROR.values())
    assert used <= routed, f"unrouted migration kinds: {sorted(used - routed)}"


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
    # Baseline, bulk, then one minimisation probe. No incremental pass.
    assert fake.calls == 3
    # Only `proc *` was ever broken, so adding FMap was along for the ride and
    # the minimisation pass takes it back out.
    assert {a.migration_id for a in result.applied} == {"proc-star"}
    assert "FMap" not in result.text


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


# --- minimisation -----------------------------------------------------------
# "Did not hurt" is why a rule gets kept. It is not a good enough reason to
# tell the model the rule was part of the repair -- with 116 manifest rules
# that list fills up with unrelated theories.


def test_a_rule_the_loading_file_does_not_need_is_taken_back_out(
    tmp_path, monkeypatch, manifest_path
):
    """Only `proc *` is broken here. `smtmap-split` matches the file and does
    no harm, so both the bulk and the incremental pass keep it -- and the
    repaired file would tell the model it was missing an FMap import it never
    needed."""
    fake = FakeEasyCrypt([(lambda t: "proc *" in t, (1, _err(5)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert result.loads_after
    assert {a.migration_id for a in result.applied if a.kept} == {"proc-star"}
    dropped = [a for a in result.applied if not a.kept]
    assert not dropped or all("loads without it" in a.reason for a in dropped)
    assert any("dropped as unnecessary" in n for n in result.notes)


def test_minimisation_never_removes_the_rule_that_did_the_work(
    tmp_path, monkeypatch, manifest_path
):
    """Both rules are load-bearing, so nothing may be dropped however the
    relevance ordering ranks them."""
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t or "FMap" not in t, (1, _err(5))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert result.loads_after
    assert {a.migration_id for a in result.applied if a.kept} == {
        "proc-star", "smtmap-split"
    }


def test_minimisation_measures_against_the_graded_rank_not_just_loading(
    tmp_path, monkeypatch, manifest_path
):
    """Most repaired files never fully load -- they reach `reached_proof` and
    stop. Testing necessity with "does it still load" would skip exactly those
    and leave the noise in. The test is the rank the full set achieved."""
    fake = FakeEasyCrypt([
        (lambda t: "proc *" in t, (1, _err(5, "parse error"))),
        # Once the syntax is fixed the only complaint is a tactic: the file got
        # past loading, which is `reached_proof`. Adding FMap changes nothing.
        (lambda t: True, (1, _err(453, "invalid `position' parameter"))),
    ])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert not result.loads_after
    assert result.outcome == import_repair.PROGRESS_REACHED_PROOF
    assert {a.migration_id for a in result.applied if a.kept} == {"proc-star"}
    assert any("dropped as unnecessary" in n for n in result.notes)


def test_minimisation_does_not_run_when_nothing_was_achieved(
    tmp_path, monkeypatch, manifest_path
):
    """No progress means no target to preserve, and dropping on a guess would
    discard rules the incremental pass verified as non-regressing."""
    fake = FakeEasyCrypt([(lambda t: True, (1, _err(108)))])
    monkeypatch.setattr(import_repair, "validate_file", fake)
    source = tmp_path / "broken.ec"
    source.write_text(SOURCE, encoding="utf-8")

    result = import_repair.repair_imports(
        source, _config(), source_version="r2022.04", target_version="r2026.07",
        manifest_path=manifest_path, min_confidence="low",
    )
    assert result.outcome == import_repair.PROGRESS_NONE
    assert not any("dropped as unnecessary" in n for n in result.notes)


# --- graded progress (W4.5) -------------------------------------------------
# "The first error moved later" is true of almost any edit, so it could not
# tell "the imports are fixed" from "one import error was traded for the next".
# The boundary that decides it is the pre-proof/in-proof one.


def _result(**kwargs) -> import_repair.ImportRepairResult:
    base = dict(
        changed=True, text="", applied=[], considered=[],
        loads_before=False, loads_after=False,
        error_before="x", error_after="y",
        error_line_before=108, error_line_after=108,
        error_kind_before=ec_errors.KIND_UNKNOWN_THEORY,
        error_kind_after=ec_errors.KIND_UNKNOWN_THEORY,
    )
    base.update(kwargs)
    return import_repair.ImportRepairResult(**base)


def test_a_remaining_tactic_error_is_this_module_succeeding():
    """The file still exits nonzero, but the only complaint left is a bad
    tactic -- the solver's problem. Import repair got the file past loading,
    which is the whole job, and `loads_after` alone would score it a failure."""
    result = _result(error_kind_after=ec_errors.KIND_TACTIC_ERROR, error_line_after=453)
    assert result.outcome == import_repair.PROGRESS_REACHED_PROOF
    assert result.resolved
    assert result.improved


def test_a_later_error_of_the_same_kind_is_advanced_not_resolved():
    """The case that made `improved_rate` read 1.0: one missing theory traded
    for another, further down. Real movement, but not the job done."""
    result = _result(error_line_after=453)
    assert result.outcome == import_repair.PROGRESS_ADVANCED
    assert not result.resolved
    assert result.improved            # still worth keeping over the original


def test_a_loading_file_outranks_everything():
    result = _result(loads_after=True, error_kind_after="", error_line_after=-1)
    assert result.outcome == import_repair.PROGRESS_LOADS
    assert result.resolved
    assert result.progress_rank > _result(
        error_kind_after=ec_errors.KIND_TACTIC_ERROR
    ).progress_rank


def test_falling_back_out_of_the_proof_is_a_regression_however_far_in():
    """A rule that turns a tactic error at line 453 into a missing theory at
    line 500 has undone the module's own work. The line number says progress;
    the kind says the opposite, and the kind is right."""
    result = _result(
        error_kind_before=ec_errors.KIND_TACTIC_ERROR, error_line_before=453,
        error_kind_after=ec_errors.KIND_UNKNOWN_THEORY, error_line_after=500,
    )
    assert result.outcome == import_repair.PROGRESS_REGRESSED
    assert not result.improved


def test_an_earlier_error_is_a_regression():
    result = _result(error_line_before=453, error_line_after=108)
    assert result.outcome == import_repair.PROGRESS_REGRESSED
    assert result.progress_rank < 0


def test_a_different_error_at_the_same_line_is_advancement():
    result = _result(error_kind_after=ec_errors.KIND_PARSE_ERROR)
    assert result.outcome == import_repair.PROGRESS_ADVANCED


def test_nothing_changing_is_not_progress():
    assert _result().outcome == import_repair.PROGRESS_NONE
    assert not _result().improved


def test_a_file_that_already_loaded_reports_no_progress():
    """`loads_after and loads_before` is "there was nothing to do", not a win;
    counting it as one would inflate every rate by the healthy files."""
    result = _result(loads_before=True, loads_after=True)
    assert result.outcome == import_repair.PROGRESS_NONE
    assert not result.resolved


def test_progress_rank_orders_every_outcome():
    ranks = [
        import_repair.PROGRESS_RANK[name] for name in (
            import_repair.PROGRESS_REGRESSED, import_repair.PROGRESS_NONE,
            import_repair.PROGRESS_ADVANCED, import_repair.PROGRESS_REACHED_PROOF,
            import_repair.PROGRESS_LOADS,
        )
    ]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


def test_to_dict_publishes_the_graded_outcome():
    payload = _result(
        error_kind_after=ec_errors.KIND_TACTIC_ERROR, error_line_after=453
    ).to_dict()
    assert payload["outcome"] == import_repair.PROGRESS_REACHED_PROOF
    assert payload["resolved"] is True
    assert payload["progress_rank"] == 2


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

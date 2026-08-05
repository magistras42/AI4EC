"""Pre-proof import repair: fix a `.ec` file's requires/syntax before any lemma.

Import breakage is *pre-proof*. When a `require import` no longer resolves, or
the file uses syntax a modern EasyCrypt no longer parses, EasyCrypt cannot load
the file at all -- so ``llm -upto`` returns nonzero,
:func:`integration.experiment.repair_bootstrap.run_replay_bootstrap_trial`
records ``skip_reason="goal_unreachable"``, and the trial ends before a single
tactic is tried. The changelog and repair-doc evidence never gets a chance to
help, because reaching it requires a loadable file.

This module closes that gap. It reads ``proof_corpus/ec_migrations.toml`` -- a
hand-extendable manifest of per-version rewrite rules (see
``proof_corpus/scripts/build_ec_migrations.py``) -- selects the rules whose
version window the file crosses, applies the matching ones, and **verifies
every change against EasyCrypt itself** via
:func:`integration.agent.easycrypt.validate_file`. Nothing is accepted on
faith: an edit that does not measurably improve how far EasyCrypt gets through
the file is rolled back.

It generalizes ``integration/experiment/corpora/elgamal.py::port_legacy_easycrypt_syntax``,
which hardcodes four fixes for one file. The same four are now manifest rules
with evidence-based version pins, and they apply to any corpus.

Line numbers are load-bearing: ``integration.experiment.protocols.ProofCase``
records absolute lemma line numbers, so every action here is line-preserving
(requires are extended in place, pragmas fold onto line 1, the rest are
in-place substitutions). :func:`apply_actions` asserts this.

CLI::

    python3 -m integration.agent.import_repair FILE.ec \\
        --source-version r2022.04 --target-version r2026.07 [--write]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .easycrypt import validate_file
from .ec_errors import (
    IN_PROOF_KINDS,
    KIND_PARSE_ERROR,
    KIND_TYPE_ERROR,
    KIND_UNKNOWN,
    KIND_UNKNOWN_SYMBOL,
    KIND_UNKNOWN_THEORY,
    ClassifiedError,
    classify_error,
    first_error_line,
    strip_warning_lines,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "proof_corpus" / "ec_migrations.toml"

SCHEMA_PREFIX = "ai4ec.ec-migrations/"

# `require [import|export] A B C.` -- possibly spanning lines, possibly with
# the `(*--*)` alignment comment EasyCrypt developers use.
_REQUIRE_LINE_RE = re.compile(
    r"^(?P<head>\s*(?:from\s+\w+\s+)?require\s+(?:import|export)?\s*)"
    r"(?P<names>[^.]*)(?P<tail>\.)",
    re.MULTILINE,
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")

LINE_PRESERVING_OPS = frozenset(
    {"add_require", "replace_require", "remove_require", "rename_symbol",
     "replace_regex", "add_pragma"}
)


class ImportRepairUnavailable(Exception):
    """The migration manifest could not be loaded. Import repair is optional
    supplementary machinery: callers degrade to the previous behaviour
    (attempt the file as-is) rather than failing the run."""


@dataclass(frozen=True)
class Migration:
    id: str
    kind: str
    summary: str
    confidence: str
    breaks_at: str | None
    match: dict[str, Any]
    actions: list[dict[str, Any]]
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppliedMigration:
    migration_id: str
    kind: str
    confidence: str
    summary: str
    actions: list[str]
    kept: bool
    reason: str
    # The `ec_errors` kind this rule was picked against, and how strongly it
    # matched it. Recorded so a finished run can be asked whether targeting
    # chose well, rather than only whether the file ended up loading.
    selected_for: str = ""
    relevance: int = 0


# --- progress (W4.5) --------------------------------------------------------
# "Did the first error move later in the file?" is too weak to be the measure
# of record. It is true of almost any edit that changes anything, so it cannot
# distinguish "the imports are fixed" from "one import error was traded for the
# next one" -- on run C it reported 12 of 12 attempts improved while only 7 of
# 12 made the file load. What it was missing is not precision about *where* the
# error is, but the classification of *what* it is.
#
# The boundary that actually decides whether this module succeeded is the one
# `ec_errors` exists to draw. Import repair's job is to get a file past
# LOADING. A file whose remaining complaint is a bad tactic argument has been
# handed off to the solver -- that is this module finishing, even though
# EasyCrypt still exits nonzero. A file whose remaining complaint is another
# missing theory has not.

PROGRESS_LOADS = "loads"                    # compiles clean
PROGRESS_REACHED_PROOF = "reached_proof"    # load errors gone; a tactic is now at fault
PROGRESS_ADVANCED = "advanced"              # still a load error, but a later/different one
PROGRESS_NONE = "none"                      # nothing measurable changed
PROGRESS_REGRESSED = "regressed"            # EasyCrypt stops earlier, or back before the proof

#: Ordered so two attempts can be compared, which a set of booleans cannot be.
PROGRESS_RANK: dict[str, int] = {
    PROGRESS_REGRESSED: -1,
    PROGRESS_NONE: 0,
    PROGRESS_ADVANCED: 1,
    PROGRESS_REACHED_PROOF: 2,
    PROGRESS_LOADS: 3,
}


@dataclass
class ImportRepairResult:
    changed: bool
    text: str
    applied: list[AppliedMigration]
    considered: list[str]
    loads_before: bool
    loads_after: bool
    error_before: str
    error_after: str
    # Line of EasyCrypt's first complaint, before and after. -1 when none was
    # reported. This is the honest progress measure for a partial repair: a
    # file whose first error moves from line 108 (a parse error in a module
    # declaration) to line 453 (a bad tactic argument) is now loadable far
    # enough to OPEN the lemmas before 453, even though `-lastgoals` still
    # exits nonzero because the proofs themselves are broken -- which is the
    # solver's job, not this module's.
    error_line_before: int = -1
    error_line_after: int = -1
    # What broke, not just where. `error_kind_after` is how a caller tells
    # "still the same import problem" from "the imports are fixed and the
    # proof is now what fails" -- the pre-proof/in-proof boundary the whole
    # module is scoped to.
    error_kind_before: str = KIND_UNKNOWN
    error_kind_after: str = KIND_UNKNOWN
    notes: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        """Graded progress: one of the ``PROGRESS_*`` constants.

        Read top to bottom, most decisive first. The two kind-based clauses are
        what the line number alone could never express: crossing into an
        in-proof error is this module succeeding, and falling back out of one
        is a regression no matter how far into the file it happened.
        """
        if self.loads_after and not self.loads_before:
            return PROGRESS_LOADS
        if self.loads_before:
            return PROGRESS_NONE  # nothing was wrong; no repair was attempted

        before_in_proof = self.error_kind_before in IN_PROOF_KINDS
        after_in_proof = self.error_kind_after in IN_PROOF_KINDS
        if after_in_proof and not before_in_proof:
            return PROGRESS_REACHED_PROOF
        if before_in_proof and not after_in_proof:
            return PROGRESS_REGRESSED

        if self.error_line_before >= 0 and self.error_line_after >= 0:
            if self.error_line_after < self.error_line_before:
                return PROGRESS_REGRESSED
            if self.error_line_after > self.error_line_before:
                return PROGRESS_ADVANCED
        if (
            self.error_kind_before
            and self.error_kind_after
            and self.error_kind_after != self.error_kind_before
        ):
            # Same position, different complaint: the old error is genuinely
            # gone and a different one is underneath it.
            return PROGRESS_ADVANCED
        return PROGRESS_NONE

    @property
    def progress_rank(self) -> int:
        return PROGRESS_RANK.get(self.outcome, 0)

    @property
    def resolved(self) -> bool:
        """True when import repair did the job it exists to do.

        The headline measure. `improved` answers a different, weaker question
        -- see there -- and reporting only that is what let run C claim a 100%
        improvement rate on a run where 5 of 12 files still would not load.
        """
        return self.progress_rank >= PROGRESS_RANK[PROGRESS_REACHED_PROOF]

    @property
    def improved(self) -> bool:
        """True when the repaired text is worth keeping over the original.

        Deliberately a *low* bar, because that is what its callers need: this
        gates whether the repaired file is promoted, and by the time a result
        exists the incremental pass has already rolled back everything that
        made EasyCrypt stop earlier. The question left is "did this do anything
        at all", not "did this finish the job" -- for the latter use
        :attr:`resolved`, and for the full picture :attr:`outcome`.
        """
        return self.progress_rank >= PROGRESS_RANK[PROGRESS_ADVANCED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "loads_before": self.loads_before,
            "loads_after": self.loads_after,
            "outcome": self.outcome,
            "progress_rank": self.progress_rank,
            "resolved": self.resolved,
            "improved": self.improved,
            "error_line_before": self.error_line_before,
            "error_line_after": self.error_line_after,
            "error_kind_before": self.error_kind_before,
            "error_kind_after": self.error_kind_after,
            "considered": self.considered,
            "applied": [
                {
                    "id": a.migration_id, "kind": a.kind,
                    "confidence": a.confidence, "actions": a.actions,
                    "kept": a.kept, "reason": a.reason, "summary": a.summary,
                    "selected_for": a.selected_for, "relevance": a.relevance,
                }
                for a in self.applied
            ],
            "error_before": self.error_before[:2000],
            "error_after": self.error_after[:2000],
            "notes": self.notes,
        }


# --- manifest ---------------------------------------------------------------


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_MANIFEST
    if not path.is_file():
        raise ImportRepairUnavailable(f"migration manifest not found at {path}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ImportRepairUnavailable(f"{path} is not valid TOML: {exc}") from exc
    schema = str(data.get("schema") or "")
    if not schema.startswith(SCHEMA_PREFIX):
        raise ImportRepairUnavailable(
            f"{path} has schema {schema!r}, expected {SCHEMA_PREFIX}*"
        )
    return data


def parse_migrations(manifest: dict[str, Any]) -> list[Migration]:
    out = []
    for row in manifest.get("migration") or []:
        out.append(Migration(
            id=str(row.get("id") or ""),
            kind=str(row.get("kind") or "unknown"),
            summary=str(row.get("summary") or "").strip(),
            confidence=str(row.get("confidence") or "medium"),
            breaks_at=(str(row["breaks_at"]) if row.get("breaks_at") else None),
            match=dict(row.get("match") or {}),
            actions=[dict(a) for a in (row.get("action") or [])],
            provenance=dict(row.get("provenance") or {}),
        ))
    return out


def select_by_version(
    migrations: list[Migration],
    source_version: str | None,
    target_version: str | None,
    known_versions: list[str],
) -> list[Migration]:
    """Keep migrations whose break point lies in ``(source, target]``.

    A rule with no ``breaks_at`` (the release could not be pinned) always
    applies -- its match conditions are the only gate. When either endpoint is
    unknown to the manifest the version filter is skipped entirely and every
    rule is considered: EasyCrypt's releases only go back to r2022.04, so a
    2020-era proof legitimately has no source tag, and "consider everything" is
    the correct maximally-exposed answer (the same fail-open convention
    ``retrieve_entries.releases_in_range`` uses).
    """
    order = {version: index for index, version in enumerate(known_versions)}
    src = order.get(str(source_version)) if source_version else None
    tgt = order.get(str(target_version)) if target_version else None
    if src is None or tgt is None:
        return list(migrations)
    if src > tgt:
        src, tgt = tgt, src

    kept = []
    for migration in migrations:
        if migration.breaks_at is None:
            kept.append(migration)
            continue
        position = order.get(migration.breaks_at)
        if position is None or src < position <= tgt:
            kept.append(migration)
    return kept


# --- matching ---------------------------------------------------------------


def required_theories(text: str) -> set[str]:
    """Every theory name the file requires (any `require` form)."""
    names: set[str] = set()
    for match in _REQUIRE_LINE_RE.finditer(text):
        names.update(_TOKEN_RE.findall(match.group("names")))
    return names


def file_tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text))


def matches(migration: Migration, text: str, tokens: set[str], requires: set[str]) -> bool:
    """True when every condition present on the migration holds (AND)."""
    condition = migration.match

    wanted = condition.get("requires_theory") or []
    if wanted and not any(str(name) in requires for name in wanted):
        return False

    absent = condition.get("missing_require") or []
    if absent and any(str(name) in requires for name in absent):
        return False

    used = condition.get("uses_symbols") or []
    if used and not any(str(name) in tokens for name in used):
        return False

    forbidden = condition.get("not_uses_symbols") or []
    if forbidden and any(str(name) in tokens for name in forbidden):
        return False

    pattern = condition.get("matches_regex")
    if pattern and not re.search(str(pattern), text):
        return False

    return bool(wanted or absent or used or forbidden or pattern)


# --- error-directed ordering ------------------------------------------------
# `matches()` answers "could this rule apply to this FILE?". It says nothing
# about the error EasyCrypt actually reported, so a file with a parse error at
# line 5 would have ten require-semantics rules tried against it before the one
# syntax rule that fixes the parse error. Each of those is a full EasyCrypt
# invocation in the incremental pass. The classification from `ec_errors` is
# what closes that gap.

#: Which migration kinds can plausibly fix which classified error. Keyed by
#: `ec_errors` kind; the values are `kind =` fields from ec_migrations.toml.
#: An error kind absent from this table (or `unknown`) puts every rule on equal
#: footing, which is the pre-existing behaviour.
MIGRATION_KINDS_BY_ERROR: dict[str, frozenset[str]] = {
    # "cannot find theory: `SmtMap'" -- the theory is gone, renamed, split, or
    # was never required. Symbol-level rules cannot fix a missing theory.
    KIND_UNKNOWN_THEORY: frozenset(
        {"theory_added", "theory_removed", "theory_renamed", "theory_split"}
    ),
    # "unknown operator `fdom'" -- the theory resolved but the name did not.
    # This is the symbol-move/rename family, plus splits (the name is in the
    # other half) and require_semantics (a theory stopped re-exporting it).
    KIND_UNKNOWN_SYMBOL: frozenset(
        {"symbol_moved", "symbol_renamed", "theory_split", "require_semantics"}
    ),
    # A parse error is the engine's grammar, not the library's contents. No
    # amount of `require` rewriting fixes syntax the parser no longer accepts.
    KIND_PARSE_ERROR: frozenset({"syntax_change"}),
    # Type errors sit between the two: a renamed symbol with a new signature,
    # or an ambient theory that no longer supplies a coercion.
    KIND_TYPE_ERROR: frozenset(
        {"symbol_renamed", "symbol_moved", "require_semantics", "syntax_change"}
    ),
}

# Scores, not booleans, because two different signals are in play: the rule's
# *kind* matching the error's kind is weak evidence (a whole family), while the
# rule naming the exact identifier EasyCrypt blamed is strong evidence (this
# rule, this symbol).
RELEVANCE_NAMED_IDENTIFIER = 4
RELEVANCE_MATCHING_KIND = 2


def migration_targets(migration: Migration) -> set[str]:
    """Every theory or symbol name a migration mentions, on either side.

    Both halves count. A rule that *matches* on `SmtMap` and a rule that
    *produces* `FMap` are each relevant when EasyCrypt blames one of those
    names -- the first because the file still uses the old name, the second
    because the replacement is what is missing.
    """
    names: set[str] = set()
    condition = migration.match
    for key in ("requires_theory", "missing_require", "uses_symbols", "not_uses_symbols"):
        names.update(str(name) for name in (condition.get(key) or []))
    for action in migration.actions:
        for key in ("theory", "with_theory", "old", "new"):
            value = action.get(key)
            if value:
                names.add(str(value))
    return names


def relevance(migration: Migration, error: ClassifiedError | None) -> int:
    """How well `migration` matches the failure EasyCrypt actually reported.

    Zero means "nothing links this rule to this error" -- which is a reason to
    try it *later*, never a reason to drop it. Two facts make exclusion the
    wrong move here. A file usually has more than one thing wrong with it, so
    the rule that is irrelevant to the error at line 5 may be exactly the rule
    the error at line 300 needs once line 5 is fixed; and `KIND_UNKNOWN` exists
    precisely because this is a heuristic layer over human-readable compiler
    output. Ordering is the safe form of this optimisation: it makes the
    likely fix cheap without making any fix unreachable.
    """
    if error is None or error.kind == KIND_UNKNOWN:
        return 0

    score = 0
    blamed = {name for name in error.identifiers}
    if blamed and blamed & migration_targets(migration):
        score += RELEVANCE_NAMED_IDENTIFIER
    if migration.kind in MIGRATION_KINDS_BY_ERROR.get(error.kind, frozenset()):
        score += RELEVANCE_MATCHING_KIND
    return score


def order_by_relevance(
    migrations: list[Migration], error: ClassifiedError | None
) -> list[Migration]:
    """Most-relevant rules first; ties keep manifest order.

    The sort is stable, so with no classification (or an unrecognised one) this
    returns the input unchanged and the caller behaves exactly as it did before
    error-directed selection existed.
    """
    return sorted(migrations, key=lambda m: -relevance(m, error))


# --- actions ----------------------------------------------------------------


def _add_to_require_line(text: str, theory: str, after: str | None) -> tuple[str, bool]:
    """Add `theory` to an existing require line, preferring the line that
    already mentions `after`.

    Extending an existing line rather than inserting a new one is what keeps
    every subsequent line number stable.
    """
    target_index = None
    matches_ = list(_REQUIRE_LINE_RE.finditer(text))
    if not matches_:
        return text, False

    for index, match in enumerate(matches_):
        names = _TOKEN_RE.findall(match.group("names"))
        if theory in names:
            return text, False  # already required
        if after and after in names and target_index is None:
            target_index = index
    if target_index is None:
        target_index = 0

    match = matches_[target_index]
    names = match.group("names").rstrip()
    replacement = f"{match.group('head')}{names} {theory}{match.group('tail')}"
    return text[: match.start()] + replacement + text[match.end():], True


def _replace_require(text: str, theory: str, with_theory: str) -> tuple[str, bool]:
    changed = False

    def swap(match: re.Match[str]) -> str:
        nonlocal changed
        names = match.group("names")
        new_names, count = re.subn(
            rf"\b{re.escape(theory)}\b", with_theory, names
        )
        if count:
            changed = True
        return f"{match.group('head')}{new_names}{match.group('tail')}"

    return _REQUIRE_LINE_RE.sub(swap, text), changed


def _remove_require(text: str, theory: str) -> tuple[str, bool]:
    changed = False

    def drop(match: re.Match[str]) -> str:
        nonlocal changed
        names = _TOKEN_RE.findall(match.group("names"))
        if theory not in names:
            return match.group(0)
        remaining = [n for n in names if n != theory]
        changed = True
        if not remaining:
            # Keep the line (blanked) rather than deleting it, so line numbers
            # do not shift.
            return " " * (len(match.group(0)) - 1) + " "
        return f"{match.group('head')}{' '.join(remaining)}{match.group('tail')}"

    return _REQUIRE_LINE_RE.sub(drop, text), changed


def _add_pragma(text: str, pragma: str) -> tuple[str, bool]:
    if f"pragma {pragma}" in text:
        return text, False
    lines = text.split("\n")
    if not lines:
        return text, False
    # Folded onto line 1 rather than inserted above it: inserting would shift
    # every recorded lemma line number by one.
    lines[0] = f"pragma {pragma}. " + lines[0]
    return "\n".join(lines), True


def apply_actions(text: str, actions: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """Apply a migration's actions in order.

    Returns ``(new_text, applied_descriptions, skipped_reasons)``. Every action
    is line-preserving; an unknown op is skipped rather than guessed at.
    """
    applied: list[str] = []
    skipped: list[str] = []
    before_lines = text.count("\n")

    for action in actions:
        op = str(action.get("op") or "")
        if op not in LINE_PRESERVING_OPS:
            skipped.append(f"unknown op {op!r}")
            continue

        changed = False
        if op == "add_require":
            text, changed = _add_to_require_line(
                text, str(action.get("theory")), action.get("after")
            )
            description = f"add_require {action.get('theory')}"
        elif op == "replace_require":
            text, changed = _replace_require(
                text, str(action.get("theory")), str(action.get("with_theory"))
            )
            description = f"replace_require {action.get('theory')}->{action.get('with_theory')}"
        elif op == "remove_require":
            text, changed = _remove_require(text, str(action.get("theory")))
            description = f"remove_require {action.get('theory')}"
        elif op == "rename_symbol":
            old, new = str(action.get("old")), str(action.get("new"))
            text, count = re.subn(rf"\b{re.escape(old)}\b", new, text)
            changed = bool(count)
            description = f"rename_symbol {old}->{new} ({count})"
        elif op == "replace_regex":
            text, count = re.subn(
                str(action.get("pattern")), str(action.get("replacement")), text
            )
            changed = bool(count)
            description = f"replace_regex {action.get('pattern')!r} ({count})"
        else:  # add_pragma
            text, changed = _add_pragma(text, str(action.get("pragma")))
            description = f"add_pragma {action.get('pragma')}"

        if changed:
            applied.append(description)
        else:
            skipped.append(f"{op}: no change")

    if text.count("\n") != before_lines:
        raise AssertionError(
            f"import repair changed the line count ({before_lines} -> "
            f"{text.count(chr(10))}); ProofCase line numbers would be invalidated"
        )
    return text, applied, skipped


# --- verification -----------------------------------------------------------


def _first_error_line(output: str) -> int:
    """Line of EasyCrypt's first complaint, or -1.

    Delegates to :func:`integration.agent.ec_errors.first_error_line`. This
    module used to carry its own copy of the two location regexes, and the copy
    was where the bug lived: it matched only ``file.ec:120:`` and not
    ``[file.ec: line 120 (8)]``, so every probe returned -1, no migration could
    ever show progress, and all of them were rolled back. One definition means
    one place to get it right.
    """
    return first_error_line(output)


def _probe(
    path: Path, text: str, config: AgentConfig
) -> tuple[bool, str, ClassifiedError]:
    """Write `text` to `path` and ask EasyCrypt how far it gets.

    Returns the classification rather than a bare line number, so callers can
    steer on *what* broke and not only on *where*.
    """
    path.write_text(text, encoding="utf-8")
    result = validate_file(path, config)
    output = (result.stderr or "").strip() or (result.stdout or "").strip()
    # EasyCrypt re-emits every file-level warning on every invocation, so the
    # one `[critical]` line arrives buried under notices that are identical for
    # the repaired and unrepaired file. Classifying the stripped text keeps
    # `ClassifiedError.message` the actual failure rather than the first
    # warning above it.
    return result.returncode == 0, output, classify_error(strip_warning_lines(output))


def repair_imports(
    source: Path,
    config: AgentConfig,
    *,
    source_version: str | None = None,
    target_version: str | None = None,
    manifest_path: Path | None = None,
    work_path: Path | None = None,
    min_confidence: str = "medium",
) -> ImportRepairResult:
    """Repair `source`'s imports/syntax, verifying every step with EasyCrypt.

    Applies all matching migrations at once and checks the result. If that does
    not make the file load, retries incrementally -- applying one migration at
    a time and keeping it only when EasyCrypt demonstrably gets further (the
    file loads, or the first error moves later in the file). This ordering
    matters: the bulk attempt is one EasyCrypt call for the common case where
    every rule is needed, and the incremental pass costs one call per rule only
    when something did not work.

    The incremental pass is **error-directed**: before each step the current
    failure is classified (:mod:`integration.agent.ec_errors`) and the rules
    still untried are ordered by :func:`relevance` to it. Because the file is
    re-probed after every accepted rule, the classification advances with the
    file -- a parse error at line 5 pulls the syntax rules forward, and once it
    is gone an ``unknown_theory`` at line 108 pulls the theory rules forward
    instead. Nothing is ever excluded; see :func:`relevance` for why.

    `source` is never modified: the work happens on `work_path` (default
    ``<source>.import_repair.ec``). The caller decides whether to promote it.
    """
    ranking = {"low": 0, "medium": 1, "high": 2}
    threshold = ranking.get(min_confidence, 1)

    original = source.read_text(encoding="utf-8")
    work_path = work_path or source.with_suffix(".import_repair.ec")
    notes: list[str] = []

    loads_before, error_before, classified_before = _probe(work_path, original, config)
    line_before = classified_before.line
    if loads_before:
        return ImportRepairResult(
            changed=False, text=original, applied=[], considered=[],
            loads_before=True, loads_after=True,
            error_before="", error_after="",
            error_line_before=-1, error_line_after=-1,
            notes=["file already loads; no import repair attempted"],
        )

    try:
        manifest = load_manifest(manifest_path)
    except ImportRepairUnavailable as exc:
        return ImportRepairResult(
            changed=False, text=original, applied=[], considered=[],
            loads_before=False, loads_after=False,
            error_before=error_before, error_after=error_before,
            error_line_before=line_before, error_line_after=line_before,
            error_kind_before=classified_before.kind,
            error_kind_after=classified_before.kind,
            notes=[f"import repair unavailable: {exc}"],
        )

    known_versions = list((manifest.get("meta") or {}).get("known_versions") or [])
    migrations = select_by_version(
        parse_migrations(manifest), source_version, target_version, known_versions
    )
    if source_version and target_version and (
        source_version not in known_versions or target_version not in known_versions
    ):
        notes.append(
            f"version(s) not in the manifest ({source_version} -> "
            f"{target_version}); considering every migration"
        )

    tokens, requires = file_tokens(original), required_theories(original)
    candidates = [
        m for m in migrations
        if ranking.get(m.confidence, 1) >= threshold
        and matches(m, original, tokens, requires)
    ]
    considered = [m.id for m in candidates]

    if not candidates:
        return ImportRepairResult(
            changed=False, text=original, applied=[], considered=[],
            loads_before=False, loads_after=False,
            error_before=error_before, error_after=error_before,
            error_line_before=line_before, error_line_after=line_before,
            error_kind_before=classified_before.kind,
            error_kind_after=classified_before.kind,
            notes=notes + ["no migration matched this file"],
        )

    targeted = [m.id for m in candidates if relevance(m, classified_before)]
    if targeted:
        notes.append(
            f"first error classified as {classified_before.kind!r}"
            + (
                f" naming {', '.join(classified_before.identifiers[:5])}"
                if classified_before.identifiers else ""
            )
            + f"; {len(targeted)} of {len(candidates)} rules address it "
            f"({', '.join(targeted)}) and are tried first"
        )
    elif classified_before.kind == KIND_UNKNOWN:
        notes.append(
            "first error did not classify; trying rules in manifest order"
        )

    # Pass 1: everything at once. Order does not affect the outcome here (every
    # action is applied), but it does decide the order `applied` is reported in,
    # so the same relevance ordering is used for legibility.
    ordered_candidates = order_by_relevance(candidates, classified_before)
    bulk_text = original
    bulk_applied: list[AppliedMigration] = []
    for migration in ordered_candidates:
        bulk_text, applied, skipped = apply_actions(bulk_text, migration.actions)
        bulk_applied.append(AppliedMigration(
            migration_id=migration.id, kind=migration.kind,
            confidence=migration.confidence, summary=migration.summary,
            actions=applied, kept=bool(applied),
            reason="applied in bulk" if applied else f"no effect ({'; '.join(skipped)})",
            selected_for=classified_before.kind,
            relevance=relevance(migration, classified_before),
        ))

    loads_after, error_after, _classified_bulk = _probe(work_path, bulk_text, config)
    if loads_after:
        return ImportRepairResult(
            changed=bulk_text != original, text=bulk_text,
            applied=[a for a in bulk_applied if a.kept], considered=considered,
            loads_before=False, loads_after=True,
            error_before=error_before, error_after="",
            error_line_before=line_before, error_line_after=-1,
            error_kind_before=classified_before.kind, error_kind_after="",
            notes=notes + ["file loads after applying all matching migrations"],
        )

    # Pass 2: incremental, keeping only what demonstrably helps -- and choosing
    # what to try next from the error the file has *now*, not the one it started
    # with. Re-classifying each round is what makes the targeting adaptive: the
    # rule that fixes the parse error at line 5 changes the failure to a missing
    # theory at line 108, and the theory rules move to the front for the next
    # round without anyone having ordered them by hand.
    notes.append(
        "bulk apply did not make the file load; retrying incrementally and "
        "keeping only migrations EasyCrypt shows progress on"
    )
    current_text = original
    current = classified_before
    current_error = error_before
    remaining = list(candidates)
    kept: list[AppliedMigration] = []

    while remaining:
        migration = order_by_relevance(remaining, current)[0]
        remaining.remove(migration)
        score = relevance(migration, current)

        trial_text, applied, skipped = apply_actions(current_text, migration.actions)
        if not applied:
            kept.append(AppliedMigration(
                migration.id, migration.kind, migration.confidence,
                migration.summary, [], False, f"no effect ({'; '.join(skipped)})",
                selected_for=current.kind, relevance=score,
            ))
            continue

        trial_loads, trial_error, trial = _probe(work_path, trial_text, config)
        previous_line = current.line
        # Non-regression, not strict improvement. Every rule here is
        # independently justified by changelog/commit evidence and is
        # line-preserving, so the question is "does this HURT?", not "does this
        # help right now". A rule that fixes something at line 300 shows no
        # movement while an unrelated parse error still sits at line 108 --
        # requiring strict progress rolled those back and left the file broken.
        regressed = (not trial_loads) and trial.line >= 0 and previous_line >= 0 and (
            trial.line < previous_line
        )
        if regressed:
            kept.append(AppliedMigration(
                migration.id, migration.kind, migration.confidence,
                migration.summary, applied, False,
                f"rolled back: first error moved earlier "
                f"({previous_line} -> {trial.line})",
                selected_for=current.kind, relevance=score,
            ))
            continue

        if trial_loads:
            reason = "file loads"
        elif trial.line > previous_line:
            reason = f"first error moved later ({previous_line} -> {trial.line})"
        elif trial.kind != current.kind:
            # Same line, different complaint. That is real movement the line
            # number cannot show: `parse error` becoming `cannot find theory`
            # at the same position means the syntax is now accepted.
            reason = f"kept: error kind changed ({current.kind} -> {trial.kind})"
        else:
            reason = f"kept: no regression (first error still at {trial.line})"
        kept.append(AppliedMigration(
            migration.id, migration.kind, migration.confidence,
            migration.summary, applied, True, reason,
            selected_for=current.kind, relevance=score,
        ))

        current_text, current, current_error = trial_text, trial, trial_error
        if trial_loads:
            break

    loads_after, error_after, final = _probe(work_path, current_text, config)
    return ImportRepairResult(
        changed=current_text != original, text=current_text,
        applied=kept, considered=considered,
        loads_before=False, loads_after=loads_after,
        error_before=error_before, error_after=error_after,
        error_line_before=line_before, error_line_after=final.line,
        error_kind_before=classified_before.kind,
        error_kind_after="" if loads_after else final.kind,
        notes=notes,
    )


def format_for_prompt(result: ImportRepairResult) -> str:
    """Render what import repair did, for the agent prompt.

    The model needs this because the file it is now proving against is not the
    file the corpus shipped: a tactic that references a renamed symbol will
    otherwise look inexplicably wrong.
    """
    kept = [a for a in result.applied if a.kept]
    if not kept:
        return ""
    lines = ["This file's imports/syntax were repaired before the proof was opened:"]
    for action in kept:
        lines.append(f"- [{action.kind}] {action.migration_id}: {'; '.join(action.actions)}")
        if action.summary:
            lines.append(f"    {action.summary}")
    if not result.loads_after:
        lines.append(
            "NOTE: the file still does not load cleanly; the remaining error is "
            "not something the migration manifest covers."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair a .ec file's imports/syntax")
    parser.add_argument("file", type=Path)
    parser.add_argument("--source-version", default=None)
    parser.add_argument("--target-version", default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--easycrypt", type=Path, default=None)
    parser.add_argument(
        "--min-confidence", default="medium", choices=("low", "medium", "high"),
    )
    parser.add_argument(
        "--write", action="store_true",
        help="overwrite the input file with the repaired text (default: report only)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    config = AgentConfig()
    if args.easycrypt:
        config.easycrypt_bin = args.easycrypt

    result = repair_imports(
        args.file, config,
        source_version=args.source_version,
        target_version=args.target_version,
        manifest_path=args.manifest,
        min_confidence=args.min_confidence,
    )

    import json as _json
    print(_json.dumps(result.to_dict(), indent=2))

    if args.write and result.changed:
        args.file.write_text(result.text, encoding="utf-8")
        print(f"\nwrote {args.file}", file=sys.stderr)

    return 0 if result.loads_after else 1


if __name__ == "__main__":
    sys.exit(main())

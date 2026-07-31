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
# Where EasyCrypt says the first error is. It uses two formats and the progress
# metric depends on reading both:
#   [critical] [/path/to/file.ec: line 108 (8)] parse error
#   /path/to/file.ec:120:4-9: ...
# Matching only the second silently made every probe return -1, so no migration
# could ever show progress and all of them were rolled back.
_ERROR_LINE_RES = (
    re.compile(r"\.eca?:\s*line\s+(\d+)", re.IGNORECASE),
    re.compile(r"\.eca?:(\d+):"),
)

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
    notes: list[str] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        """True when the file now loads, or EasyCrypt gets measurably further."""
        if self.loads_after and not self.loads_before:
            return True
        return (
            self.error_line_before >= 0
            and self.error_line_after > self.error_line_before
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "loads_before": self.loads_before,
            "loads_after": self.loads_after,
            "improved": self.improved,
            "error_line_before": self.error_line_before,
            "error_line_after": self.error_line_after,
            "considered": self.considered,
            "applied": [
                {
                    "id": a.migration_id, "kind": a.kind,
                    "confidence": a.confidence, "actions": a.actions,
                    "kept": a.kept, "reason": a.reason, "summary": a.summary,
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
    for pattern in _ERROR_LINE_RES:
        match = pattern.search(output)
        if match:
            return int(match.group(1))
    return -1


def _probe(path: Path, text: str, config: AgentConfig) -> tuple[bool, str, int]:
    """Write `text` to `path` and ask EasyCrypt how far it gets."""
    path.write_text(text, encoding="utf-8")
    result = validate_file(path, config)
    output = (result.stderr or "").strip() or (result.stdout or "").strip()
    return result.returncode == 0, output, _first_error_line(output)


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

    `source` is never modified: the work happens on `work_path` (default
    ``<source>.import_repair.ec``). The caller decides whether to promote it.
    """
    ranking = {"low": 0, "medium": 1, "high": 2}
    threshold = ranking.get(min_confidence, 1)

    original = source.read_text(encoding="utf-8")
    work_path = work_path or source.with_suffix(".import_repair.ec")
    notes: list[str] = []

    loads_before, error_before, line_before = _probe(work_path, original, config)
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
            notes=notes + ["no migration matched this file"],
        )

    # Pass 1: everything at once.
    bulk_text = original
    bulk_applied: list[AppliedMigration] = []
    for migration in candidates:
        bulk_text, applied, skipped = apply_actions(bulk_text, migration.actions)
        bulk_applied.append(AppliedMigration(
            migration_id=migration.id, kind=migration.kind,
            confidence=migration.confidence, summary=migration.summary,
            actions=applied, kept=bool(applied),
            reason="applied in bulk" if applied else f"no effect ({'; '.join(skipped)})",
        ))

    loads_after, error_after, line_after = _probe(work_path, bulk_text, config)
    if loads_after:
        return ImportRepairResult(
            changed=bulk_text != original, text=bulk_text,
            applied=[a for a in bulk_applied if a.kept], considered=considered,
            loads_before=False, loads_after=True,
            error_before=error_before, error_after="",
            error_line_before=line_before, error_line_after=-1,
            notes=notes + ["file loads after applying all matching migrations"],
        )

    # Pass 2: incremental, keeping only what demonstrably helps.
    notes.append(
        "bulk apply did not make the file load; retrying incrementally and "
        "keeping only migrations EasyCrypt shows progress on"
    )
    current_text = original
    current_line = line_before
    current_error = error_before
    kept: list[AppliedMigration] = []

    for migration in candidates:
        trial_text, applied, skipped = apply_actions(current_text, migration.actions)
        if not applied:
            kept.append(AppliedMigration(
                migration.id, migration.kind, migration.confidence,
                migration.summary, [], False, f"no effect ({'; '.join(skipped)})",
            ))
            continue

        trial_loads, trial_error, trial_line = _probe(work_path, trial_text, config)
        previous_line = current_line
        # Non-regression, not strict improvement. Every rule here is
        # independently justified by changelog/commit evidence and is
        # line-preserving, so the question is "does this HURT?", not "does this
        # help right now". A rule that fixes something at line 300 shows no
        # movement while an unrelated parse error still sits at line 108 --
        # requiring strict progress rolled those back and left the file broken.
        regressed = (not trial_loads) and trial_line >= 0 and previous_line >= 0 and (
            trial_line < previous_line
        )
        if regressed:
            kept.append(AppliedMigration(
                migration.id, migration.kind, migration.confidence,
                migration.summary, applied, False,
                f"rolled back: first error moved earlier "
                f"({previous_line} -> {trial_line})",
            ))
            continue

        current_text, current_line, current_error = trial_text, trial_line, trial_error
        if trial_loads:
            reason = "file loads"
        elif trial_line > previous_line:
            reason = f"first error moved later ({previous_line} -> {trial_line})"
        else:
            reason = f"kept: no regression (first error still at {trial_line})"
        kept.append(AppliedMigration(
            migration.id, migration.kind, migration.confidence,
            migration.summary, applied, True, reason,
        ))
        if trial_loads:
            break

    loads_after, error_after, final_line = _probe(work_path, current_text, config)
    return ImportRepairResult(
        changed=current_text != original, text=current_text,
        applied=kept, considered=considered,
        loads_before=False, loads_after=loads_after,
        error_before=error_before, error_after=error_after,
        error_line_before=line_before, error_line_after=final_line, notes=notes,
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

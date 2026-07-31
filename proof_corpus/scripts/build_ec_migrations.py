#!/usr/bin/env python3
"""
build_ec_migrations.py

Emit ``proof_corpus/ec_migrations.toml`` -- the per-version manifest of what
EasyCrypt libraries were created, moved, deleted or had symbols move between
them, expressed as **rewrite rules a script can apply to a `.ec` file before
any proof is attempted**.

    output/library_history.json   (mined from git -- analyze_library_history.py)
  + <easycrypt>/theories/**       (current require/export structure)
  + curated engine rules (below)  (parser/syntax changes, not library changes)
  -> ec_migrations.toml

Everything library-related here is **derived from the commit history**, not
from the prose in ``repair_doc/*.json``. Those files were written by reading
the current sources and the release notes; their own ``caveat`` says "No true
git-diff was possible". ``analyze_library_history.py`` does the diff, so every
library rule below is backed by a commit SHA and a release tag and can be
re-checked with ``git show``.

The headline case falls out on its own: 125 declarations disappear from
``SmtMap`` in r2025.02 and appear in ``FMap`` in the same release. That is the
split, enumerated exactly, without anyone having written it down.

What is NOT derived
-------------------
Parser and module-system changes (``proc *`` removal, ``declare module X : T``
becoming ``X <: T``, the memory-restriction rework) are engine changes, not
library changes, so no amount of theory-file history reveals them. They stay in
``CURATED_ENGINE_MIGRATIONS`` -- but their version pins also come from commits,
recovered by ``collect_changelog.py --git-log``.

Regenerating
------------
    python3 proof_corpus/scripts/analyze_library_history.py   # mine git
    python3 proof_corpus/scripts/build_ec_migrations.py       # emit TOML

Edit ``CURATED_ENGINE_MIGRATIONS`` to add an engine rule; library rules come
from the history and change when the history does. Or edit the TOML directly
and stop regenerating -- the processing script only ever reads TOML.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "ai4ec.ec-migrations/1"

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_CORPUS = REPO_ROOT / "proof_corpus"

DEFAULT_HISTORY = PROOF_CORPUS / "output" / "library_history.json"
DEFAULT_CHANGELOG = PROOF_CORPUS / "output" / "changelog_index.json"
DEFAULT_OUT = PROOF_CORPUS / "ec_migrations.toml"

DEFAULT_THEORY_DIRS = (
    REPO_ROOT / "integration" / "extern" / "easycrypt" / "theories",
    PROOF_CORPUS / "easycrypt" / "theories",
    REPO_ROOT / "easycrypt" / "theories",
)

# A symbol move needs to be substantial before it becomes a rule: one or two
# names crossing between theories in a release is usually a small refactor, not
# an interface change a proof would notice.
MIN_MOVED_SYMBOLS = 3
# How many moved names to list in the rule's match condition. The full list can
# run to 125; the manifest stays readable and the match still fires because any
# one of them is enough.
MAX_MATCH_SYMBOLS = 40

# Theories the ENGINE loads into every file before it reads a single line, so
# they are never something a proof must require. `ecCommands.ml` exports
# `EcCoreLib.i_Pervasive` unconditionally at scope creation; Logic arrives with
# it. Emitting "you must require Pervasive" would be actively wrong advice.
ENGINE_PRELOADED = frozenset({"Pervasive", "Logic"})

_REQUIRE_RE = re.compile(
    r"^\s*(?:from\s+\w+\s+)?require\s+(?P<mode>import|export|)\s*(?P<names>[^.]*)\.",
    re.MULTILINE,
)
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
_COMMENT_RE = re.compile(r"\(\*.*?\*\)", re.DOTALL)


# --- curated engine rules ---------------------------------------------------
#
# Not derivable from theory-file history: these are changes to the parser and
# the module system. Version pins come from commits recovered by
# `collect_changelog.py --git-log`, since EasyCrypt's release notes are empty
# before r2025.02.

CURATED_ENGINE_MIGRATIONS: list[dict[str, Any]] = [
    {
        "id": "proc-star-removed",
        "kind": "syntax_change",
        "breaks_at": "r2023.09",
        "summary": (
            "The `proc *` marker (a distinguished/initialising procedure in a "
            "module type) is no longer parsed. Drop the star; current EasyCrypt "
            "infers this from usage."
        ),
        "confidence": "high",
        "match": {"matches_regex": r"\bproc\s+\*\s"},
        "actions": [
            {"op": "replace_regex", "pattern": r"\bproc\s+\*\s", "replacement": "proc "},
        ],
        "provenance": {
            "commits": ["57be028cc", "df8a2f924"],
            "note": (
                "r2023.09 commits 'strip out code related to proc *' and "
                "'Remove parser support for `proc *` in module sigs'."
            ),
        },
    },
    {
        "id": "declare-module-ascription",
        "kind": "syntax_change",
        "breaks_at": "r2023.09",
        "summary": "`declare module X : T` (bare ascription) is now `declare module X <: T`.",
        "confidence": "high",
        "match": {"matches_regex": r"\bdeclare\s+module\s+(\w+)\s*:\s"},
        "actions": [
            {
                "op": "replace_regex",
                "pattern": r"\bdeclare\s+module\s+(\w+)\s*:\s",
                "replacement": r"declare module \1 <: ",
            },
        ],
        "provenance": {
            "commits": ["dc50f44bf", "6d0e46493"],
            "note": (
                "r2023.09 commits 'Forbid the usage of [declare] for concrete "
                "modules' and 'Enforce section restrictions on the types of "
                "declared modules'."
            ),
        },
    },
    {
        "id": "old-module-restriction-sets",
        "kind": "syntax_change",
        "breaks_at": "r2024.09",
        "summary": (
            "Unprefixed module-restriction sets like `{RO, Adv}` are no longer "
            "accepted; current syntax is `{-RO, -Adv}`. The `old_mem_restr` "
            "pragma restores the old reading without touching every site, which "
            "keeps line numbers stable."
        ),
        "confidence": "medium",
        "match": {"matches_regex": r"\{\s*[A-Z]\w*\s*(,\s*[A-Z]\w*\s*)*\}"},
        "actions": [{"op": "add_pragma", "pragma": "+old_mem_restr"}],
        "provenance": {
            "commits": ["b53230696", "9b940b4f5"],
            "note": (
                "r2024.09 commits 'simplify representation of memory restriction "
                "(#569)' and 'Simplify/clean up memory/call restrictions'. The "
                "pragma is a compatibility shim, not a port."
            ),
        },
    },
]


# --- source parsing ---------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def theory_index(theory_dirs: Iterable[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in theory_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix in (".ec", ".eca") and path.is_file():
                index.setdefault(path.stem, path)
    return index


def parse_requires(path: Path) -> tuple[list[str], list[str]]:
    """``(require [import], require export)`` for one theory file."""
    header = _COMMENT_RE.sub(" ", _read(path))
    plain: list[str] = []
    exported: list[str] = []
    for match in _REQUIRE_RE.finditer(header):
        target = exported if match.group("mode") == "export" else plain
        for name in _NAME_RE.findall(match.group("names") or ""):
            if name not in target:
                target.append(name)
    return plain, exported


def export_closure(theory: str, index: dict[str, Path], seen: set[str] | None = None) -> set[str]:
    """Everything a `require import <theory>` transitively brings into scope.

    Only `require export` propagates: a plain `require import` inside a theory
    does NOT re-export to that theory's own importers. This is precisely why
    `require import AllCore.` does not give a file `List` -- AllCore exports
    only Core, Int, Real and Xint.
    """
    seen = seen if seen is not None else set()
    if theory in seen or theory not in index:
        return seen
    seen.add(theory)
    _plain, exported = parse_requires(index[theory])
    for name in exported:
        export_closure(name, index, seen)
    return seen


# --- derivation from history ------------------------------------------------


def derive_symbol_moves(history: dict[str, Any], earliest_tag: str | None) -> list[dict[str, Any]]:
    """Symbols leaving theory A and arriving in theory B in the same release.

    This is the shape of a theory split or a library reorganisation, and it is
    exactly what breaks a `require import`: the file still requires A, but the
    names it uses now live in B.
    """
    libraries = history.get("libraries") or {}
    releases = sorted({
        release
        for record in libraries.values()
        for release in (record.get("symbol_events") or {})
    })

    migrations = []
    for release in releases:
        if earliest_tag and release == earliest_tag:
            # Everything reachable from the oldest tag is attributed to it,
            # including a decade of pre-tag history. "Removed in r2022.04"
            # cannot be distinguished from "never existed in our window", so a
            # rule keyed there would fire on files it has no business touching.
            continue
        for source, source_record in libraries.items():
            removed = set((source_record.get("symbol_events") or {})
                          .get(release, {}).get("removed", []))
            if not removed:
                continue
            for destination, destination_record in libraries.items():
                if destination == source:
                    continue
                added = set((destination_record.get("symbol_events") or {})
                            .get(release, {}).get("added", []))
                moved = sorted(removed & added)
                if len(moved) < MIN_MOVED_SYMBOLS:
                    continue
                migrations.append({
                    "id": f"{source.lower()}-symbols-moved-to-{destination.lower()}-{release}",
                    "kind": "symbol_moved",
                    "breaks_at": release,
                    "summary": (
                        f"{len(moved)} declarations moved from {source} to "
                        f"{destination} in {release}. A file that requires "
                        f"{source} and uses any of them must also require "
                        f"{destination}."
                    ),
                    "confidence": "high" if len(moved) >= 10 else "medium",
                    "match": {
                        "requires_theory": [source],
                        "missing_require": [destination],
                        "uses_symbols": moved[:MAX_MATCH_SYMBOLS],
                    },
                    "actions": [
                        {"op": "add_require", "theory": destination, "after": source},
                    ],
                    "provenance": {
                        "derived_from": "library_history.json",
                        "moved_symbol_count": len(moved),
                        "commits": _release_commits(source_record, release)
                                   + _release_commits(destination_record, release),
                        "note": (
                            f"Derived: {len(moved)} names present in {source} "
                            f"before {release} and in {destination} after. "
                            f"Full list in library_history.json under "
                            f"libraries.{source}.symbol_events.{release}.removed."
                        ),
                    },
                })
    return migrations


def _release_commits(record: dict[str, Any], release: str) -> list[str]:
    return sorted({
        event["sha"]
        for event in record.get("path_events") or []
        if event.get("release") == release and event.get("sha")
    })[:4]


def derive_theory_lifecycle(
    history: dict[str, Any], earliest_tag: str | None
) -> list[dict[str, Any]]:
    """Rules for theories created, moved or deleted inside the tracked window."""
    migrations = []
    for library, record in (history.get("libraries") or {}).items():
        for event in record.get("moved") or []:
            release = event.get("release")
            if not release or release == earliest_tag:
                continue
            # A move changes the file's location in the tree, not the theory
            # name a proof requires, so there is no rewrite to apply -- but it
            # is the fingerprint of a reorganisation, and the accompanying
            # symbol moves (derived separately) are what actually break code.
            migrations.append({
                "id": f"{library.lower()}-moved-{release}",
                "kind": "theory_renamed",
                "breaks_at": None,
                "summary": (
                    f"{library} moved from {event['from_path']} to "
                    f"{event['to_path']} in {release}. `require import "
                    f"{library}` still resolves; this is recorded because the "
                    f"reorganisation usually moves symbols too."
                ),
                "confidence": "low",
                "match": {"requires_theory": [library]},
                "actions": [],
                "provenance": {
                    "derived_from": "library_history.json",
                    "commits": [event["sha"]],
                    "note": event["subject"],
                },
            })
        for event in record.get("created") or []:
            release = event.get("release")
            if not release or release == earliest_tag:
                continue
            migrations.append({
                "id": f"{library.lower()}-added-{release}",
                "kind": "theory_added",
                "breaks_at": None,
                "summary": (
                    f"{library} did not exist before {release} (created at "
                    f"{event['path']}). A proof written earlier cannot have "
                    f"required it; if it needs {library}'s contents, they lived "
                    f"somewhere else."
                ),
                "confidence": "medium",
                "match": {"missing_require": [library], "uses_symbols": [library]},
                "actions": [{"op": "add_require", "theory": library}],
                "provenance": {
                    "derived_from": "library_history.json",
                    "commits": [event["sha"]],
                    "note": f"{event['date'][:10]} {event['subject']}",
                },
            })
        for event in record.get("deleted") or []:
            release = event.get("release")
            if not release or release == earliest_tag:
                continue
            if record.get("exists_now"):
                continue  # deleted then reinstated; not a live breakage
            migrations.append({
                "id": f"{library.lower()}-removed-{release}",
                "kind": "theory_removed",
                "breaks_at": release,
                "summary": (
                    f"{library} was removed in {release}; a `require import "
                    f"{library}` no longer resolves. No automatic replacement "
                    f"is known -- this rule reports the problem."
                ),
                "confidence": "low",
                "match": {"requires_theory": [library]},
                "actions": [],
                "provenance": {
                    "derived_from": "library_history.json",
                    "commits": [event["sha"]],
                    "note": f"{event['date'][:10]} {event['subject']}",
                },
            })
    return migrations


def derive_export_gaps(
    history: dict[str, Any], index: dict[str, Path], hub: str = "AllCore",
) -> list[dict[str, Any]]:
    """Tracked libraries a `require import <hub>` does NOT bring into scope.

    Derived from the sources rather than asserted: `AllCore.ec` is four lines
    (`require (*--*) Ring.` / `require export Core Int Real Xint.`), so its
    export closure is small and everything outside it needs an explicit
    require. A proof assuming otherwise fails with unknown symbols.
    """
    if hub not in index:
        return []
    closure = export_closure(hub, index)
    tracked = list((history.get("libraries") or {}))

    migrations = []
    for library in sorted(tracked):
        if library == hub or library in closure or library not in index:
            continue
        if library in ENGINE_PRELOADED:
            continue
        record = (history.get("libraries") or {}).get(library) or {}
        symbols = _representative_symbols(record)
        if not symbols:
            continue
        migrations.append({
            "id": f"{hub.lower()}-does-not-export-{library.lower()}",
            "kind": "require_semantics",
            "breaks_at": None,
            "summary": (
                f"`require import {hub}.` does not bring {library} into scope "
                f"({hub} exports only {', '.join(sorted(closure - {hub})) or 'nothing'}). "
                f"A file that requires {hub}, uses {library} names, and never "
                f"requires {library} needs an explicit require."
            ),
            "confidence": "medium",
            "match": {
                "requires_theory": [hub],
                "missing_require": [library],
                "uses_symbols": symbols,
            },
            "actions": [{"op": "add_require", "theory": library, "after": hub}],
            "provenance": {
                "derived_from": f"{hub}.ec export closure + library_history.json",
                "note": (
                    f"{hub}'s transitive `require export` closure is "
                    f"{sorted(closure)}; {library} is not in it."
                ),
            },
        })
    return migrations


def _representative_symbols(record: dict[str, Any], limit: int = 12) -> list[str]:
    """Distinctive names a library currently provides, for match conditions.

    Taken from the most recent release that added names, so they reflect the
    current interface rather than something long since renamed.
    """
    events = record.get("symbol_events") or {}
    for release in sorted(events, reverse=True):
        names = [n for n in events[release].get("added", []) if len(n) > 3]
        if names:
            return sorted(names)[:limit]
    return []


# --- TOML emission ----------------------------------------------------------
#
# Written by hand rather than with a library: `tomli_w` is not vendored, and
# emitting it ourselves keeps the explanatory comments that make this manifest
# editable, which a serializer would drop.


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _s(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if "\\" in text and "'" not in text and "\n" not in text:
        return f"'{text}'"
    return f'"{_esc(text)}"'


def _arr(values: list[Any], indent: str = "") -> str:
    if not values:
        return "[]"
    rendered = ", ".join(_s(v) for v in values)
    if len(rendered) <= 88:
        return f"[{rendered}]"
    return "[\n" + "\n".join(f"{indent}  {_s(v)}," for v in values) + f"\n{indent}]"


def _multiline(text: str, indent: str) -> str:
    body = " ".join(str(text).split())
    if len(body) <= 84:
        return _s(body)
    body = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    lines, current = [], ""
    for word in body.split(" "):
        if len(current) + len(word) + 1 > 84:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return '"""\n' + "\n".join(f"{indent}{line}" for line in lines) + "\n" + indent + '"""'


def render_migration(migration: dict[str, Any]) -> str:
    out = ["[[migration]]", f'id = {_s(migration["id"])}', f'kind = {_s(migration["kind"])}']
    if migration.get("breaks_at"):
        out.append(f'breaks_at = {_s(migration["breaks_at"])}')
    else:
        out.append("# no breaks_at: applies whenever source < target")
    out.append(f'confidence = {_s(migration.get("confidence", "medium"))}')
    out.append(f'summary = {_multiline(migration["summary"], "  ")}')

    match = migration.get("match") or {}
    out += ["", "  [migration.match]"]
    for key in ("requires_theory", "missing_require", "uses_symbols", "not_uses_symbols"):
        if match.get(key):
            out.append(f"  {key} = {_arr(match[key], '  ')}")
    if match.get("matches_regex"):
        out.append(f'  matches_regex = {_s(match["matches_regex"])}')

    for action in migration.get("actions") or []:
        out += ["", "  [[migration.action]]"]
        out += [f"  {k} = {_s(v)}" for k, v in action.items()]

    provenance = migration.get("provenance") or {}
    if provenance:
        out += ["", "  [migration.provenance]"]
        if provenance.get("derived_from"):
            out.append(f'  derived_from = {_s(provenance["derived_from"])}')
        for key in ("changelog", "commits"):
            if provenance.get(key):
                out.append(f"  {key} = {_arr(provenance[key], '  ')}")
        if provenance.get("moved_symbol_count"):
            out.append(f'  moved_symbol_count = {provenance["moved_symbol_count"]}')
        if provenance.get("note"):
            out.append(f'  note = {_multiline(provenance["note"], "    ")}')
    return "\n".join(out)


def render_library(library: str, record: dict[str, Any]) -> str:
    out = ["[[library]]", f'theory = {_s(library)}']
    paths = record.get("current_paths") or []
    out.append(f'path = {_s(paths[0] if paths else "")}')
    out.append(f'exists_now = {_s(bool(record.get("exists_now")))}')
    out.append(f'commits_examined = {int(record.get("commits_examined") or 0)}')
    if record.get("releases_touched"):
        out.append(f'releases_touched = {_arr(record["releases_touched"])}')
    events = record.get("symbol_events") or {}
    if events:
        summary = ", ".join(
            f"{rel}: +{len(v.get('added', []))}/-{len(v.get('removed', []))}"
            for rel, v in sorted(events.items())
        )
        out.append(f'symbol_churn = {_multiline(summary, "  ")}')
    renames = [
        f"{e.get('from_path') or '?'} -> {e['path']} ({e['release']}, {e['sha']})"
        for e in record.get("path_events") or []
        if e["event"] in ("renamed", "added", "deleted")
    ]
    if renames:
        out.append(f'path_history = {_arr(renames[:6], "")}')
    return "\n".join(out)


HEADER = '''\
# EasyCrypt import-migration manifest
#
# GENERATED by proof_corpus/scripts/build_ec_migrations.py from
# output/library_history.json (mined from the git history by
# analyze_library_history.py) -- NOT from the prose in repair_doc/*.json.
# Every library rule below is backed by a commit SHA and a release tag and can
# be re-checked with `git show`.
#
# CONSUMER: integration/agent/import_repair.py
#
# WHAT THIS IS FOR
# Import breakage is pre-proof. When a `require import` no longer resolves,
# EasyCrypt cannot load the file at all, `llm -upto` returns nonzero, and
# integration/experiment/repair_bootstrap.py skips the trial as
# `goal_unreachable` -- so none of the changelog evidence ever gets a chance to
# help. These rules fix the file first, so a proof can be attempted at all.
#
# [[migration]] -- a rewrite rule
#   id           unique slug
#   kind         symbol_moved | theory_added | theory_removed | theory_renamed
#                | theory_split | symbol_renamed | syntax_change
#                | require_semantics
#   breaks_at    release where the OLD form stops working. A rule applies when
#                source_version < breaks_at <= target_version. Omitted means
#                "applies whenever source < target".
#   confidence   high | medium | low
#   [migration.match]      ALL present conditions must hold:
#     requires_theory      file has `require [import] <T>`
#     missing_require      file does NOT require <T>
#     uses_symbols         file references any of these tokens
#     not_uses_symbols     file references none of these tokens
#     matches_regex        raw pattern over the file text
#   [[migration.action]]   applied in order:
#     add_require      theory=<T> [after=<U>]  add T to an existing require line
#     replace_require  theory=<T> with_theory=<U>
#     remove_require   theory=<T>
#     rename_symbol    old=<a> new=<b>         whole-token rename
#     replace_regex    pattern=<re> replacement=<s>
#     add_pragma       pragma=<p>              folded onto line 1
#
# LINE NUMBERS ARE LOAD-BEARING. integration/experiment/protocols.py::ProofCase
# records absolute lemma line numbers, so every action above is line-preserving:
# add_require extends an existing require line, add_pragma folds onto line 1,
# and the rest are in-place substitutions. import_repair.apply_actions asserts
# the line count is unchanged.
#
# CAVEAT ON THE OLDEST TAG: every commit reachable from the earliest release tag
# is attributed to it, including a decade of pre-tag history. "Changed in
# <earliest tag>" therefore cannot be distinguished from "existed before our
# window", so no rule is keyed to that release.
#
# [[library]] -- per-theory history summary, also from library_history.json.
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--changelog-index", type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument("--theories", type=Path, action="append", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.history.is_file():
        print(
            f"error: {args.history} not found -- run\n"
            f"  python3 proof_corpus/scripts/analyze_library_history.py\n"
            f"first (it mines the git history this manifest is derived from)",
            file=sys.stderr,
        )
        return 1
    history = json.loads(_read(args.history))
    if not str(history.get("schema") or "").startswith("ai4ec.library-history/"):
        print(f"error: {args.history} is not a library-history file", file=sys.stderr)
        return 1

    tags = list((history.get("generated_from") or {}).get("tags") or [])
    earliest_tag = tags[0] if tags else None

    theory_dirs = args.theories or [p for p in DEFAULT_THEORY_DIRS if p.is_dir()]
    index = theory_index(theory_dirs)
    if not index:
        print(
            f"warning: no theory sources found; export-closure rules skipped",
            file=sys.stderr,
        )

    known_versions = tags
    if args.changelog_index.is_file():
        changelog = json.loads(_read(args.changelog_index))
        from_changelog = [r["version"] for r in changelog.get("releases") or []]
        if from_changelog:
            known_versions = from_changelog

    migrations = (
        derive_symbol_moves(history, earliest_tag)
        + derive_theory_lifecycle(history, earliest_tag)
        + derive_export_gaps(history, index)
        + CURATED_ENGINE_MIGRATIONS
    )

    unknown = sorted({
        m["breaks_at"] for m in migrations
        if m.get("breaks_at") and m["breaks_at"] not in known_versions
    })
    if unknown:
        print(
            f"warning: breaks_at value(s) not in the known-version list: "
            f"{', '.join(unknown)}",
            file=sys.stderr,
        )

    libraries = history.get("libraries") or {}
    chunks = [
        HEADER,
        f'schema = "{SCHEMA}"',
        f'generated_at = "{datetime.now(timezone.utc).isoformat(timespec="seconds")}"',
        "",
        "[meta]",
        f'library_history = {_s(str(args.history.relative_to(REPO_ROOT)))}',
        f"known_versions = {_arr(known_versions)}",
        f'earliest_tag = {_s(earliest_tag or "")}',
        f"tracked_libraries = {_arr(sorted(libraries))}",
        "",
        "# " + "=" * 74,
        "# Migrations",
        "# " + "=" * 74,
        "",
    ]
    for migration in migrations:
        chunks += [render_migration(migration), ""]

    chunks += [
        "# " + "=" * 74,
        "# Libraries (history summary; see library_history.json for full detail)",
        "# " + "=" * 74,
        "",
    ]
    for library in sorted(libraries):
        chunks += [render_library(library, libraries[library]), ""]

    args.out.write_text("\n".join(chunks), encoding="utf-8")

    # Parse it back immediately: a manifest that does not round-trip is worse
    # than none, because the consumer fails at repair time instead of here.
    try:
        import tomllib
        parsed = tomllib.loads(_read(args.out))
    except Exception as exc:
        print(f"error: emitted TOML does not parse: {exc}", file=sys.stderr)
        return 1

    kinds: dict[str, int] = defaultdict(int)
    for migration in parsed.get("migration") or []:
        kinds[migration["kind"]] += 1
    print(f"Wrote {args.out}", file=sys.stderr)
    print(
        f"  {len(parsed.get('migration') or [])} migrations "
        f"({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}), "
        f"{len(parsed.get('library') or [])} libraries",
        file=sys.stderr,
    )
    derived = sum(
        1 for m in parsed.get("migration") or []
        if (m.get("provenance") or {}).get("derived_from")
    )
    print(
        f"  {derived} derived from git history, "
        f"{len(parsed.get('migration') or []) - derived} curated engine rules",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

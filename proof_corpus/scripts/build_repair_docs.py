#!/usr/bin/env python3
"""
build_repair_docs.py

Reprocess the hand/LLM-authored per-library reference docs in
``repair_doc/*_lib.json`` into a compact, machine-checked, import-focused
artifact: ``output/repair_docs_index.json``.

    repair_doc/*_lib.json        (authored prose -- long, inconsistently shaped)
  + <easycrypt>/theories/**      (ground truth: real requires, real exports)
  + output/changelog_index.json  (what changed, per theory)
  -> output/repair_docs_index.json

Like ``build_changelog_index.py`` this is a **pure derivation**: no network, no
API key, no LLM calls. Re-run it whenever the theory tree or the changelog
index changes. The authored `repair_doc/*.json` files are never modified.

Why
---
The authored docs are the project's best import-repair knowledge, but they are
hard to use as-is:

1. **Long and unstructured.** `current_content_summary` runs to 300+ words of
   prose in one blob. `integration/agent/repair_hints.py` re-sends its hint
   block on *every* agent step, so this competes directly with premises and
   failure history for context.
2. **Inconsistently shaped.** `version_diffs_found` is a list in 5 of the 18
   files and a bare sentence ("None found by name in the scanned changelog
   window.") in the other 13. `requires` is prose
   ("AllCore, FMap, Distr, Mu_mem, FinType, StdBigop, FelTactic; (FullEager
   also pulls in List, FSet, IterProc; ...)"), not a list.
3. **Only 4 of 18 carry an `import_repair_note`** -- the one field written
   specifically for repairing a `require import` that no longer resolves.
4. **Unverifiable.** The docs' own `caveat` says no true git-diff was possible;
   `requires` was transcribed by hand and can drift from the actual source.
5. **They cover 18 of 128 theories.** Anything outside that set has no entry at
   all, so "which theory provides `frng`?" is unanswerable for most of the tree.

This script fixes all five by deriving the checkable parts from the EasyCrypt
sources themselves and keeping the authored prose only where it adds something
a parser cannot: the `import_repair_note` narrative.

What it produces
----------------
* **`libraries[]`** -- one record per documented library, with the authored
  prose condensed to a `summary`, the authored `import_repair_note` preserved
  verbatim, plus *derived and verified*: `requires` / `imports` / `clones`
  (parsed from the real source header), `exports` (declared names), and the
  changelog entries that actually touched that theory.
* **`theories{}`** -- the same derived facts for **every** theory in the tree,
  documented or not, so import repair is not limited to the 18 curated ones.
* **`symbol_index{}`** -- `symbol -> [theories that declare it]` across the
  whole tree. This is the lookup that answers *"EasyCrypt says `dom` is
  unknown; what do I need to `require import`?"*, which nothing in the system
  could answer before: `ec.exe llm -premises` reports what **is** in scope,
  never what **could** be.

Usage
-----
    python3 proof_corpus/scripts/build_repair_docs.py
    python3 proof_corpus/scripts/build_repair_docs.py --summary-chars 400
    python3 proof_corpus/scripts/build_repair_docs.py --no-symbol-index

Every path defaults to its conventional location relative to the repository
root, so the no-argument form is the normal way to run it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "ai4ec.repair-docs-index/1"

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_CORPUS = REPO_ROOT / "proof_corpus"

DEFAULT_CHANGELOG_INDEX = PROOF_CORPUS / "output" / "changelog_index.json"
DEFAULT_OUT = PROOF_CORPUS / "output" / "repair_docs_index.json"

# repair_doc has lived in more than one place; accept both rather than making
# the caller remember which. `output/repair_doc` is checked first because that
# is where callers most often look for it.
DEFAULT_REPAIR_DOC_DIRS = (
    PROOF_CORPUS / "output" / "repair_doc",
    PROOF_CORPUS / "repair_doc",
)

DEFAULT_THEORY_DIRS = (
    REPO_ROOT / "integration" / "extern" / "easycrypt" / "theories",
    PROOF_CORPUS / "easycrypt" / "theories",
    REPO_ROOT / "easycrypt" / "theories",
)

_THEORY_SUFFIXES = (".ec", ".eca")
_LIBRARY_SUFFIX = "_lib.json"

# --- EasyCrypt source parsing ----------------------------------------------

# `require import A B C.` / `require A B.` / `require (*--*) Ring.` /
# `require export X.` / `from Foo require import Bar.`
# The `(*--*)` marker is a comment EasyCrypt developers use to visually align
# require lines; it appears in ~50 theory files and must not be read as a name.
_REQUIRE_RE = re.compile(
    r"^\s*(?:from\s+(?P<from>[A-Za-z_][\w']*)\s+)?"
    r"require\s+(?P<mode>import|export|)\s*(?P<names>[^.]*)\.",
    re.MULTILINE,
)
# A bare `import X.` / `import IntID IntOrder.` -- brings an already-required
# theory's names into scope. Distinct from `require`: getting these two
# confused is itself a common import-repair mistake.
_IMPORT_RE = re.compile(r"^\s*import\s+(?P<names>[^.]*)\.", re.MULTILINE)
_CLONE_RE = re.compile(
    r"^\s*clone\s+(?:(?P<mode>import|export|include)\s+)?(?P<name>[A-Za-z_][\w'.]*)",
    re.MULTILINE,
)
_COMMENT_RE = re.compile(r"\(\*.*?\*\)", re.DOTALL)

# `rename "dunifin" as "dbool"` inside a clone introduces `dbool` as an
# exported name even though nothing in the file declares it with `op`/`lemma`.
# DBool.ec is exactly this case, and it is one of only four libraries with a
# hand-written import_repair_note ("dbool ... are NOT primitive -- they are a
# renamed clone of Distr.MFinite's dunifin/dunifinE"), so missing it would
# leave the symbol index unable to answer the very lookup that note describes.
_RENAME_RE = re.compile(
    r'^\s*rename\s+(?:\[\w+\]\s*)?"[^"]+"\s+as\s+"(?P<name>[^"]+)"',
    re.MULTILINE,
)

# Indentation is allowed on purpose: a `theory Foo. ... end Foo.` block indents
# its contents and those names ARE exported (as `Foo.name`), so anchoring hard
# to column 0 would drop real symbols. Proof-internal bindings (`have`, `pose`)
# use different keywords and so never match this pattern anyway.
_DECL_RE = re.compile(
    r"^\s*(?:local\s+|declare\s+|abstract\s+)*"
    r"(?P<kind>lemma|axiom|op|pred|type|abbrev|module|theory|const)\s+"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_']*)"
)

_NAME_RE = re.compile(r"[A-Za-z_][\w'.]*")


def _strip_comments(text: str) -> str:
    return _COMMENT_RE.sub(" ", text)


def _names(blob: str) -> list[str]:
    out: list[str] = []
    for name in _NAME_RE.findall(blob):
        if name not in out:
            out.append(name)
    return out


def parse_theory_source(path: Path) -> dict[str, Any]:
    """Extract a theory's real import surface and declared names.

    Everything here is read from the source EasyCrypt actually compiles, so it
    cannot drift from reality the way the authored `requires` prose can.
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    header = _strip_comments(raw)

    requires: list[str] = []
    exports: list[str] = []
    for match in _REQUIRE_RE.finditer(header):
        target = exports if match.group("mode") == "export" else requires
        for name in _names(match.group("names") or ""):
            if name not in target:
                target.append(name)

    imports: list[str] = []
    for match in _IMPORT_RE.finditer(header):
        for name in _names(match.group("names") or ""):
            if name not in imports and name not in requires:
                imports.append(name)

    clones: list[str] = []
    for match in _CLONE_RE.finditer(header):
        name = match.group("name")
        if name and name not in clones:
            clones.append(name)

    declarations: dict[str, list[str]] = {}
    for line in raw.splitlines():
        match = _DECL_RE.match(line)
        if match:
            declarations.setdefault(match.group("kind"), []).append(match.group("name"))

    for match in _RENAME_RE.finditer(header):
        declarations.setdefault("clone_rename", []).append(match.group("name"))

    return {
        "requires": requires,
        "require_exports": exports,
        "imports": imports,
        "clones": clones,
        "declarations": declarations,
    }


def scan_theories(theory_dirs: Iterable[Path]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Parse every theory in the tree.

    Returns ``(theory name -> facts, symbol -> [declaring theories])``. The
    first directory that defines a theory wins, matching the search order of
    DEFAULT_THEORY_DIRS (the vendored fork the harness actually runs is first).
    """
    theories: dict[str, dict] = {}
    symbol_index: dict[str, list[str]] = {}

    for root in theory_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in _THEORY_SUFFIXES or not path.is_file():
                continue
            name = path.stem
            if name in theories:
                continue
            facts = parse_theory_source(path)
            declarations = facts.pop("declarations")
            try:
                display_path = str(path.relative_to(REPO_ROOT))
            except ValueError:
                # --theories may point anywhere (a system-wide opam install, a
                # tmp tree in tests); an absolute path is the honest answer.
                display_path = str(path)
            counts = {kind: len(names) for kind, names in sorted(declarations.items())}
            flat = [n for names in declarations.values() for n in names]

            theories[name] = {
                "theory": name,
                "path": display_path,
                **facts,
                "declaration_counts": counts,
                "declaration_total": len(flat),
            }
            for symbol in set(flat) | {name}:
                owners = symbol_index.setdefault(symbol, [])
                if name not in owners:
                    owners.append(name)

    for owners in symbol_index.values():
        owners.sort()
    return theories, dict(sorted(symbol_index.items()))


# --- authored-doc normalization ---------------------------------------------


def as_list(value: Any) -> list[str]:
    """Normalize a field that is sometimes a list and sometimes a string.

    `version_diffs_found` is a list in 5 of the 18 library docs and a plain
    sentence in the other 13; iterating the string form yields one character
    per step.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


_NO_DIFF_PREFIXES = ("none", "no pr title", "no diffs", "not found")


def real_version_diffs(value: Any) -> list[str]:
    """Drop the "nothing found" sentences that 13 of the docs store where a
    list of diffs would go. A statement that nothing was found is not a
    version note and must not be rendered as one."""
    return [
        item for item in as_list(value)
        if item.strip() and not item.strip().lower().startswith(_NO_DIFF_PREFIXES)
    ]


def condense(text: str, limit: int) -> str:
    """Clip prose to `limit` characters, preferring a sentence boundary.

    The authored summaries are single 300+ word paragraphs. Cutting at the last
    complete sentence keeps the result readable instead of ending mid-clause.
    """
    text = " ".join(str(text or "").split())
    if limit <= 0 or len(text) <= limit:
        return text
    window = text[:limit]
    for sep in (". ", "; "):
        cut = window.rfind(sep)
        if cut > limit * 0.5:
            return window[: cut + 1]
    return window.rsplit(" ", 1)[0].rstrip(",;:") + " ..."


def theory_name_from_path(path: str) -> str | None:
    stem = str(path).rsplit("/", 1)[-1]
    for suffix in _THEORY_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return None


# --- hint synthesis ---------------------------------------------------------


def derive_import_note(
    theory: str,
    facts: dict[str, Any] | None,
    changelog_entries: list[dict[str, Any]],
) -> str | None:
    """Build an import-repair note for a theory that has no authored one.

    Deliberately factual and short: what the theory needs, what it provides,
    and whether anything in the tracked window changed it. It never speculates
    about *why* something broke -- that is what the authored notes do well and
    a template cannot.
    """
    if facts is None:
        return None

    parts: list[str] = []
    requires = facts.get("requires") or []
    if requires:
        parts.append(
            f"`require import {theory}.` needs {', '.join(requires[:8])}"
            + (" (and others)" if len(requires) > 8 else "")
            + " in scope first"
        )
    imports = facts.get("imports") or []
    if imports:
        parts.append(
            f"it also `import`s {', '.join(imports[:5])} (names brought into "
            f"scope without a separate require)"
        )
    clones = facts.get("clones") or []
    if clones:
        parts.append(f"clones {', '.join(clones[:5])}")

    total = facts.get("declaration_total") or 0
    if total:
        counts = facts.get("declaration_counts") or {}
        shape = ", ".join(f"{n} {kind}" for kind, n in list(counts.items())[:4])
        parts.append(f"declares {total} names ({shape})")

    if changelog_entries:
        versions = sorted({str(e.get("version")) for e in changelog_entries})
        parts.append(
            f"changed in {', '.join(versions)} -- see the changelog entries below"
        )

    if not parts:
        return None
    return f"{theory}: " + "; ".join(parts) + "."


# Prose names are bare identifiers, unlike source names which may be dotted
# (`clone PROM.FullRO`). Using the source regex here made every sentence-final
# name ("... FinType.") capture its own full stop and never match.
_PROSE_NAME_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_']*\b")
_PROSE_FILLER = {
    "and", "also", "pulls", "in", "clones", "imports", "import", "requires",
    "the", "a", "an", "of", "for", "with", "plus", "only", "via", "from",
}


def cross_check_requires(
    authored: str | None,
    derived: list[str],
    *,
    also_declared: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Compare the authored `requires` prose against the parsed source.

    The docs' own caveat admits they were transcribed without a diff, so a
    mismatch is worth surfacing rather than silently trusting either side. The
    derived list is authoritative (it is what EasyCrypt compiles); the authored
    prose often adds useful conditional detail ("FullEager also pulls in ...")
    that the parser cannot express.

    `also_declared` should carry the theory's `imports` and `clones`: several
    docs describe those in the same sentence ("...; imports CoreMap."), and
    counting them as bogus `require` claims would report a disagreement that
    isn't one.
    """
    if not authored:
        return None
    claimed = {
        name for name in _PROSE_NAME_RE.findall(authored)
        if name.lower() not in _PROSE_FILLER
    }
    derived_set = set(derived)
    known = derived_set | set(also_declared)
    missing = sorted(derived_set - claimed)
    # Only flag capitalized leftovers: EasyCrypt theory names are capitalized,
    # so a lowercase leftover is ordinary prose, not a claimed dependency.
    extra = sorted(n for n in claimed - known if n[:1].isupper())
    if not missing and not extra:
        return None
    return {
        "in_source_not_in_prose": missing,
        "in_prose_not_in_source": extra,
    }


def build(
    *,
    repair_doc_dir: Path,
    theories: dict[str, dict],
    symbol_index: dict[str, list[str]],
    changelog_index: dict[str, Any] | None,
    summary_chars: int,
    max_exports: int,
    include_symbol_index: bool,
) -> dict[str, Any]:
    by_theory: dict[str, list[str]] = {}
    entries_by_key: dict[str, dict] = {}
    if changelog_index:
        entries_by_key = {e["key"]: e for e in changelog_index.get("entries") or []}
        by_theory = (changelog_index.get("indexes") or {}).get("by_theory") or {}

    def changelog_for(theory: str) -> list[dict[str, Any]]:
        rows = []
        for key in by_theory.get(theory, []):
            entry = entries_by_key.get(key)
            if entry is None:
                continue
            rows.append({
                "version": entry.get("version"),
                "id": entry.get("id"),
                "kind": entry.get("kind"),
                "title": entry.get("title"),
                "repair_hint": entry.get("repair_hint"),
                "import_relevant": bool(entry.get("import_relevant")),
                "url": entry.get("url"),
            })
        rows.sort(key=lambda r: (str(r["version"]), str(r["id"])))
        return rows

    libraries: list[dict[str, Any]] = []
    doc_paths = sorted(repair_doc_dir.glob(f"*{_LIBRARY_SUFFIX}"))
    for doc_path in doc_paths:
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: skipping {doc_path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(doc, dict):
            continue

        path = str(doc.get("path") or "")
        theory = theory_name_from_path(path) or doc_path.name[: -len(_LIBRARY_SUFFIX)]
        facts = theories.get(theory)
        entries = changelog_for(theory)

        authored_note = str(doc.get("import_repair_note") or "").strip() or None

        libraries.append({
            "theory": theory,
            "source_file": doc_path.name,
            "path": (facts or {}).get("path") or path,
            "resolved_in_tree": facts is not None,
            # --- authored, condensed ---
            "summary": condense(doc.get("current_content_summary"), summary_chars),
            "summary_full_chars": len(str(doc.get("current_content_summary") or "")),
            "import_repair_note": authored_note,
            "import_repair_note_source": "authored" if authored_note else "derived",
            "version_notes": real_version_diffs(doc.get("version_diffs_found")),
            "requires_prose": str(doc.get("requires") or "").strip() or None,
            # --- derived from the real source ---
            "requires": (facts or {}).get("requires") or [],
            "require_exports": (facts or {}).get("require_exports") or [],
            "imports": (facts or {}).get("imports") or [],
            "clones": (facts or {}).get("clones") or [],
            "declaration_counts": (facts or {}).get("declaration_counts") or {},
            "declaration_total": (facts or {}).get("declaration_total") or 0,
            "requires_mismatch": cross_check_requires(
                doc.get("requires"),
                (facts or {}).get("requires") or [],
                also_declared=(
                    ((facts or {}).get("imports") or [])
                    + ((facts or {}).get("clones") or [])
                    + ((facts or {}).get("require_exports") or [])
                ),
            ),
            # --- derived from the changelog index ---
            "changelog": entries,
            "changed_in": sorted({str(e["version"]) for e in entries}),
            "import_relevant_changes": [e for e in entries if e["import_relevant"]],
        })

    # Fill in a derived note wherever the authors did not write one.
    for library in libraries:
        if library["import_repair_note"]:
            continue
        library["import_repair_note"] = derive_import_note(
            library["theory"],
            theories.get(library["theory"]),
            library["changelog"],
        )

    documented = {lib["theory"] for lib in libraries}
    theory_records = {
        name: {
            **facts,
            "documented": name in documented,
            "changed_in": sorted({
                str(entries_by_key[k].get("version"))
                for k in by_theory.get(name, [])
                if k in entries_by_key
            }),
        }
        for name, facts in theories.items()
    }
    if max_exports >= 0:
        for record in theory_records.values():
            record.pop("declarations", None)

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_from": {
            "repair_doc": str(repair_doc_dir),
            "theories": theory_records and next(iter(theory_records.values()))["path"].rsplit("/", 2)[0],
            "changelog_index": bool(changelog_index),
        },
        "libraries": libraries,
        "theories": theory_records,
        "stats": {
            "documented_libraries": len(libraries),
            "libraries_resolved_in_tree": sum(1 for l in libraries if l["resolved_in_tree"]),
            "authored_import_notes": sum(
                1 for l in libraries if l["import_repair_note_source"] == "authored"
            ),
            "derived_import_notes": sum(
                1 for l in libraries
                if l["import_repair_note_source"] == "derived" and l["import_repair_note"]
            ),
            "libraries_with_requires_mismatch": sum(
                1 for l in libraries if l["requires_mismatch"]
            ),
            "theories_in_tree": len(theory_records),
            "symbols_indexed": len(symbol_index),
            "ambiguous_symbols": sum(1 for owners in symbol_index.values() if len(owners) > 1),
        },
    }
    if include_symbol_index:
        result["symbol_index"] = symbol_index
    return result


def _first_existing(candidates: Iterable[Path]) -> Path | None:
    for path in candidates:
        if path.is_dir():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repair-doc", type=Path, default=None,
        help="directory of *_lib.json docs (default: output/repair_doc, then repair_doc)",
    )
    parser.add_argument(
        "--theories", type=Path, action="append", default=None,
        help="EasyCrypt theories directory (repeatable; first match wins)",
    )
    parser.add_argument("--changelog-index", type=Path, default=DEFAULT_CHANGELOG_INDEX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--summary-chars", type=int, default=600,
        help="condense each authored summary to this many characters (0 = keep in full)",
    )
    parser.add_argument("--max-exports", type=int, default=0, help="reserved; counts are always emitted")
    parser.add_argument(
        "--no-symbol-index", action="store_true",
        help="omit the tree-wide symbol -> theory map (smaller output)",
    )
    parser.add_argument("--indent", type=int, default=1)
    args = parser.parse_args()

    repair_doc_dir = args.repair_doc or _first_existing(DEFAULT_REPAIR_DOC_DIRS)
    if repair_doc_dir is None or not repair_doc_dir.is_dir():
        tried = args.repair_doc or ", ".join(str(p) for p in DEFAULT_REPAIR_DOC_DIRS)
        print(f"error: repair_doc directory not found (tried: {tried})", file=sys.stderr)
        return 1

    theory_dirs = args.theories or [p for p in DEFAULT_THEORY_DIRS if p.is_dir()]
    if not theory_dirs:
        print(
            f"error: no EasyCrypt theories directory found (tried: "
            f"{', '.join(str(p) for p in DEFAULT_THEORY_DIRS)}). The derived "
            f"requires/exports facts are the point of this script.",
            file=sys.stderr,
        )
        return 1

    theories, symbol_index = scan_theories(theory_dirs)

    changelog_index = None
    if args.changelog_index.is_file():
        loaded = json.loads(args.changelog_index.read_text(encoding="utf-8"))
        if str(loaded.get("schema") or "").startswith("ai4ec.changelog-index/"):
            changelog_index = loaded
        else:
            print(
                f"warning: {args.changelog_index} is not a changelog index; "
                f"run build_changelog_index.py first. Continuing without "
                f"per-theory change history.",
                file=sys.stderr,
            )
    else:
        print(
            f"warning: {args.changelog_index} not found -- continuing without "
            f"per-theory change history",
            file=sys.stderr,
        )

    result = build(
        repair_doc_dir=repair_doc_dir,
        theories=theories,
        symbol_index=symbol_index,
        changelog_index=changelog_index,
        summary_chars=max(0, args.summary_chars),
        max_exports=args.max_exports,
        include_symbol_index=not args.no_symbol_index,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=args.indent or None, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    stats = result["stats"]
    print(f"Wrote {args.out}", file=sys.stderr)
    print(
        f"  {stats['documented_libraries']} documented libraries "
        f"({stats['libraries_resolved_in_tree']} matched to a theory in the tree)",
        file=sys.stderr,
    )
    print(
        f"  import notes: {stats['authored_import_notes']} authored, "
        f"{stats['derived_import_notes']} derived",
        file=sys.stderr,
    )
    print(
        f"  {stats['theories_in_tree']} theories parsed, "
        f"{stats['symbols_indexed']} symbols indexed "
        f"({stats['ambiguous_symbols']} declared in more than one theory)",
        file=sys.stderr,
    )
    if stats["libraries_with_requires_mismatch"]:
        print(
            f"  note: {stats['libraries_with_requires_mismatch']} libraries' authored "
            f"`requires` prose disagrees with the parsed source (see "
            f"`requires_mismatch`); the parsed list is authoritative",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

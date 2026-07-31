#!/usr/bin/env python3
"""
build_changelog_index.py

Derive `changelog_index.json` -- a flat, typed, pre-indexed view of the
EasyCrypt release changelog -- from the artifacts we already have:

    output/changelog.yaml       (authored/LLM-classified, from process_changelog.py)
  + output/raw_releases.json    (raw GitHub data, from collect_changelog.py)
  + <easycrypt>/theories/**     (the real symbol vocabulary)
  + repair_doc/tactics_ref.json (the real tactic vocabulary)
  -> output/changelog_index.json

This is a **pure derivation**: no network, no API key, no LLM calls, no
re-billing. Re-run it whenever any input changes.

Why this exists
---------------
`changelog.yaml`'s nested `releases -> entries` shape has four properties that
make it a poor query surface for both proof repair and corpus analysis:

1. **`identifiers` is mostly English prose.** It is produced by a regex over
   the PR title plus whatever the classifier LLM volunteered, with no check
   that a name exists in EasyCrypt. Measured on the current file: only 14.5%
   of identifier slots name a real declared EasyCrypt symbol, and 51.7% of
   high/medium-relevance entries carry **no** resolvable identifier at all.
   Since `retrieve_entries.score_entries` matches Tier B by exact token
   overlap against this list, retrieval both misses real hits and fires on
   coincidental words like `use`, `from`, `level`, `code`, `error`.
   `compute_exposure_score.detect_content_bracket_version` matches the same
   list against repo sources, so the noise corrupts version detection too.

2. **The richest evidence is discarded.** All 276 PRs in `raw_releases.json`
   carry `changed_files`, `labels`, and a full PR `body`; 69 of them touch
   `theories/`. `process_changelog.py` passes some of that to the classifier
   as *context* and then drops it. A changed file like
   `theories/datatypes/FMap.ec` is exact, machine-checked theory scope -- far
   better evidence than a guessed identifier, and exactly what import repair
   needs.

3. **No random access.** Answering "what changed about FMap?" or "what
   changed about the `rewrite` tactic?" requires a full nested scan of every
   release. Range queries re-sort the release list on every call and locate
   endpoints with `list.index()`, which silently falls back to "the entire
   changelog" when a tag is missing.

4. **Untyped identifiers.** A theory name, a lemma name, and a tactic name
   are all flattened into one `identifiers` list, so a consumer cannot ask
   for "entries about this *theory*" without re-deriving the distinction.

The derived index fixes all four while keeping every authored field intact.
See `proof_corpus/CHANGELOG_INDEX_SCHEMA.md` for the full schema.

Usage
-----
    python3 proof_corpus/scripts/build_changelog_index.py
    python3 proof_corpus/scripts/build_changelog_index.py \
        --changelog proof_corpus/output/changelog.yaml \
        --raw proof_corpus/output/raw_releases.json \
        --theories integration/extern/easycrypt/theories \
        --tactics-ref proof_corpus/repair_doc/tactics_ref.json \
        --out proof_corpus/output/changelog_index.json

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

import yaml

SCHEMA = "ai4ec.changelog-index/1"

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_CORPUS = REPO_ROOT / "proof_corpus"

DEFAULT_CHANGELOG = PROOF_CORPUS / "output" / "changelog.yaml"
DEFAULT_RAW = PROOF_CORPUS / "output" / "raw_releases.json"
DEFAULT_TACTICS_REF = PROOF_CORPUS / "repair_doc" / "tactics_ref.json"
DEFAULT_OUT = PROOF_CORPUS / "output" / "changelog_index.json"

# Theory trees to try, in order. The vendored fork under integration/extern is
# the build the harness actually runs against, so it is the authoritative
# vocabulary; proof_corpus/easycrypt is a fallback checkout.
DEFAULT_THEORY_DIRS = (
    REPO_ROOT / "integration" / "extern" / "easycrypt" / "theories",
    PROOF_CORPUS / "easycrypt" / "theories",
    REPO_ROOT / "easycrypt" / "theories",
)

# --- weighting --------------------------------------------------------------
# Materialized onto every entry as `breaking_weight` so retrieval and exposure
# scoring cannot drift apart. Values mirror compute_exposure_score.py's
# KIND_WEIGHTS x RELEVANCE_MULTIPLIER, which remains the definition of record.
KIND_WEIGHTS = {
    "mechanism_change": 5.0,
    "syntax_change": 4.0,
    "lemma_removed": 4.0,
    "lemma_renamed": 3.0,
    "lemma_changed": 3.0,
    "tactic_change": 3.0,
    "lemma_added": 1.0,
    "documentation": 0.0,
    "internal": 0.0,
}
RELEVANCE_MULTIPLIER = {"high": 1.0, "medium": 0.6, "low": 0.1, "unknown": 0.3}

# --- vocabulary extraction --------------------------------------------------

# Declaration heads in EasyCrypt theory sources. Indentation is tolerated on
# purpose: a `theory Foo. ... end Foo.` block indents its contents and those
# names ARE exported. Proof-internal bindings (`have`, `pose`) use different
# keywords, so they never match this pattern regardless.
_DECL_RE = re.compile(
    r"^\s*(?:local\s+|declare\s+|abstract\s+)*"
    r"(?:lemma|axiom|op|pred|type|abbrev|module|theory|const|hint)\s+"
    r"(?:\[[^\]]*\]\s*)?"
    r"([A-Za-z_][A-Za-z0-9_']*)"
)
_THEORY_SUFFIXES = (".ec", ".eca")

# Backticked spans in authored prose are high-confidence identifier candidates:
# the changelog authors and the classifier LLM both use them to mark code.
_BACKTICK_RE = re.compile(r"`([^`\n]{1,80})`")

# A conservative identifier shape. Matches retrieve_entries.tokenize_proof so
# the index and the query side agree on what a token even is.
_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_']*\b")

# English words that also happen to be declared somewhere in the theory tree
# (`op all`, `type message`, `op change`, ...). Without this list, folding the
# classifier's `identifiers` into the resolved buckets reintroduces exactly the
# noise this file exists to remove: `from`, `type` and `change` were the 2nd,
# 4th and 5th most common "symbols" before it was applied. A name on this list
# is still accepted when it is backticked in prose, or when the PR actually
# touched the theory that declares it -- i.e. when there is corroboration
# beyond a bare word appearing in a title.
_AMBIGUOUS_WORDS = {
    "a", "add", "all", "an", "and", "any", "as", "at", "be", "before", "big",
    "bind", "body", "bug", "by", "call", "case", "change", "check", "class",
    "clear", "code", "const", "default", "do", "docker", "document", "does",
    "empty", "end", "error", "exact", "exists", "feat", "field", "file",
    "first", "fix", "for", "form", "from", "get", "head", "hint", "id", "if",
    "improve", "in", "index", "is", "it", "kind", "last", "left", "lemma",
    "lemmas", "level", "list", "make", "map", "match", "max", "memory",
    "message", "min", "mode", "name", "new", "next", "not", "of", "on", "one",
    "only", "op", "or", "order", "out", "pair", "part", "path", "position",
    "pred", "print", "printing", "proof", "prop", "range", "rename", "right",
    "same", "set", "should", "side", "size", "some", "sort", "split", "state",
    "step", "sub", "support", "swap", "tactic", "test", "the", "then", "this",
    "to", "top", "type", "types", "unit", "up", "use", "used", "value", "was",
    "when", "which", "with", "work", "zero",
}

# Prose-only words to keep out of the last-resort `title_tokens` bucket.
_PROSE_STOPWORDS = _AMBIGUOUS_WORDS | {
    "also", "already", "always", "are", "avoid", "back", "based", "because",
    "been", "being", "better", "both", "but", "can", "cannot", "could",
    "either", "else", "ensure", "every", "existing", "had", "has", "have",
    "however", "into", "its", "just", "longer", "may", "more", "most",
    "much", "must", "need", "needed", "needs", "now", "other", "over",
    "properly", "rather", "really", "since", "so", "still", "such", "than",
    "that", "their", "them", "there", "these", "they", "those", "through",
    "thus", "under", "until", "using", "very", "via", "were", "what",
    "whether", "while", "will", "within", "without", "would", "you", "your",
}


def _is_distinctive(name: str) -> bool:
    """True when a name looks like a deliberately-chosen EasyCrypt identifier
    rather than an English word: it carries a capital, an underscore, a digit,
    or a prime (`FMap`, `set_set_swap`, `nth0`, `map1`, `addr'`). Bare
    lowercase words (`dom`, `frng`) are NOT distinctive by this rule and need
    corroboration -- a backtick or a touched theory -- to be accepted."""
    return bool(re.search(r"[A-Z0-9_']", name))

# Tactic names that are punctuation-ish or multi-word and therefore never
# recovered from a vocabulary file by token scanning alone.
_EXTRA_TACTICS = {
    "smt", "auto", "trivial", "progress", "simplify", "done", "admit",
    "apply", "rewrite", "have", "move", "case", "elim", "split", "left",
    "right", "exists", "congr", "subst", "ring", "field", "algebra",
    "proc", "proc*", "inline", "wp", "sp", "skip", "call", "rnd", "seq",
    "while", "if", "swap", "cfold", "unroll", "rcondt", "rcondf", "byequiv",
    "byphoare", "bypr", "conseq", "transitivity", "fel", "eager", "sim",
    "islossless", "phoare", "hoare", "equiv", "outline", "kill", "async",
    "splitwhile", "alias", "fission", "fusion", "circuit", "extens", "bind",
}

# Non-theory top-level directories, mapped to the coarse "area" they represent.
_AREA_BY_PREFIX = (
    ("theories/", "library"),
    ("src/", "engine"),
    ("libs/", "engine"),
    ("doc/", "docs"),
    ("refman/", "docs"),
    ("examples/", "examples"),
    ("tests/", "tooling"),
    ("scripts/", "tooling"),
    (".github/", "tooling"),
)

# Vocabulary of things that actually break a `require import` / `clone`: the
# import machinery itself, or a theory being split, renamed, moved, merged or
# removed.
#
# Deliberately NOT "the PR touched a file under theories/". A library change is
# not an import change: #724 ("remove alt-ergo from EasyCrypt TCB") edited 21
# theory files to adjust `smt` calls and has nothing to do with imports, but a
# touched-theories rule flagged it. Theory involvement is treated as a
# supporting signal below, never a sufficient one.
# STRONG: vocabulary that is unambiguously about the import/cloning machinery.
# `require` is included only in its EasyCrypt spelling (`require import`, or
# `require` immediately before a capitalized theory name) -- bare "requires" /
# "requiring" is ordinary English and produced a false positive on #724
# ("remove alt-ergo ... requiring proofs previously relying on it to use other
# solvers"), which has nothing to do with imports.
_IMPORT_PROSE_STRONG_RE = re.compile(
    r"(\bimports?\b|\bimported\b|\bimporting\b|\bclones?\b|\bcloning\b|"
    r"\bexport\w*|\binstantiat\w*|\bnamespace\b|\babbrev\b|"
    r"\brequire\s+import\b|\brequire\s+[A-Z]|"
    r"\bin\s+scope\b|\bout\s+of\s+scope\b|\bno\s+longer\s+in\s+scope\b)",
    re.IGNORECASE,
)

# WEAK: a theory being reorganized. These words are common in other senses
# (`split` is also a tactic -- #675 "Tactic `split` with break position"), so
# they only count when a theory is demonstrably involved.
_IMPORT_PROSE_WEAK_RE = re.compile(
    r"\b(split|splits|renam\w*|moved|merg\w*|relocat\w*|"
    r"remove[sd]?\s+the\s+theory)\b",
    re.IGNORECASE,
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def build_symbol_vocabulary(theory_dirs: Iterable[Path]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Scan EasyCrypt theory sources for declared names.

    Returns ``(symbol -> [theory names declaring it], theory name -> path)``.
    A symbol can legitimately be declared in several theories (e.g. a name
    replayed by a clone), so the mapping is one-to-many; consumers that need a
    single answer should treat multiple owners as an ambiguity to report, not
    resolve silently.
    """
    symbol_owners: dict[str, set[str]] = {}
    theory_paths: dict[str, str] = {}

    for root in theory_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in _THEORY_SUFFIXES or not path.is_file():
                continue
            theory = path.stem
            # First tree wins: DEFAULT_THEORY_DIRS is ordered by authority.
            theory_paths.setdefault(theory, str(path.relative_to(REPO_ROOT)))
            symbol_owners.setdefault(theory, set()).add(theory)
            for line in _read_text(path).splitlines():
                match = _DECL_RE.match(line)
                if match:
                    symbol_owners.setdefault(match.group(1), set()).add(theory)

    return (
        {name: sorted(owners) for name, owners in symbol_owners.items()},
        theory_paths,
    )


def build_tactic_vocabulary(tactics_ref: Path | None) -> set[str]:
    """Collect tactic names from repair_doc/tactics_ref.json plus a hardcoded
    core set. The reference file is a curated catalog with several differently
    shaped sections, so this walks it structurally rather than assuming one
    layout."""
    tactics: set[str] = set(_EXTRA_TACTICS)
    if tactics_ref is None or not tactics_ref.is_file():
        return tactics

    try:
        doc = json.loads(_read_text(tactics_ref))
    except (json.JSONDecodeError, OSError):
        return tactics

    def harvest(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "tactics":
                    if isinstance(value, dict):
                        tactics.update(str(k) for k in value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and item.get("name"):
                                tactics.add(str(item["name"]))
                            elif isinstance(item, str):
                                tactics.add(item)
                elif key == "glosses" and isinstance(value, dict):
                    tactics.update(str(k) for k in value)
                else:
                    harvest(value)
        elif isinstance(node, list):
            for item in node:
                harvest(item)

    harvest(doc)

    # Entries like "smt / smt(lemmas...)", "sp / wp", "proc '*'" carry several
    # names in one string; split them back out and drop argument syntax.
    expanded: set[str] = set()
    for raw in tactics:
        for piece in re.split(r"\s*/\s*|\s*\|\s*", str(raw)):
            piece = piece.strip().strip("'\"")
            piece = re.sub(r"\s*\(.*$", "", piece)
            piece = re.sub(r"\s+<.*$", "", piece)
            if piece and len(piece) <= 24 and not piece.startswith("["):
                expanded.add(piece)
    return expanded


# --- entry enrichment -------------------------------------------------------


def _candidate_tokens(entry: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Split an entry's candidate names into three confidence tiers.

    Returns ``(backticked, claimed, prose)``:

    * **backticked** -- marked as code in the authored prose. The authors and
      the classifier LLM both use backticks for real names, so these are
      accepted on sight when they resolve.
    * **claimed** -- whatever the classifier put in `identifiers`. Measured to
      be ~85% English prose, so these need to resolve *and* clear the
      ambiguity test before being promoted.
    * **prose** -- every remaining identifier-shaped word. Last resort only.
    """
    text = "\n".join(
        str(entry.get(field) or "")
        for field in ("title", "summary", "repair_hint")
    )
    backticked = {token.strip() for token in _BACKTICK_RE.findall(text)}
    # A backticked span can be a phrase ("proc change") or carry call syntax
    # ("smt(...)"); keep the whole span AND its identifier-shaped pieces.
    for span in list(backticked):
        backticked.update(_TOKEN_RE.findall(span))
    backticked = {t for t in backticked if t and len(t) > 1}

    claimed = {str(i) for i in (entry.get("identifiers") or []) if len(str(i)) > 1}
    prose = {t for t in _TOKEN_RE.findall(text) if len(t) > 1}
    return backticked, claimed - backticked, prose - backticked - claimed


def _theory_from_path(path: str) -> str | None:
    if not path.startswith("theories/"):
        return None
    stem = path.rsplit("/", 1)[-1]
    for suffix in _THEORY_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return None


def _classify_touches(changed_files: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    buckets: dict[str, list[str]] = {}
    areas: set[str] = set()
    for raw in changed_files:
        path = str(raw)
        for prefix, area in _AREA_BY_PREFIX:
            if path.startswith(prefix):
                buckets.setdefault(area, []).append(path)
                areas.add(area)
                break
        else:
            buckets.setdefault("other", []).append(path)
    return {k: sorted(v) for k, v in buckets.items()}, sorted(areas)


def enrich_entry(
    entry: dict[str, Any],
    *,
    version: str,
    ordinal: int,
    repo: str,
    pr_details: dict[str, Any],
    symbol_owners: dict[str, list[str]],
    theory_paths: dict[str, str],
    tactic_vocab: set[str],
    body_excerpt_chars: int,
) -> dict[str, Any]:
    """Turn one legacy changelog entry into an indexed entry.

    Every authored field is preserved verbatim; the new fields are additive,
    so nothing a human or the classifier LLM wrote is lost or rewritten.
    """
    pr_id = str(entry.get("id") or "")
    detail = pr_details.get(pr_id) or {}
    changed_files = [str(f) for f in (detail.get("changed_files") or [])]
    touches, areas = _classify_touches(changed_files)

    backticked, claimed, prose = _candidate_tokens(entry)

    # Exact, machine-checked theory scope: which theory files this PR actually
    # changed. This is the strongest evidence in the whole record and the
    # thing import repair most needs.
    theories_touched = sorted(
        {
            name
            for path in touches.get("library", [])
            if (name := _theory_from_path(path))
        }
    )
    touched_set = set(theories_touched)

    def accept(name: str) -> bool:
        """Promote a candidate to a resolved name?

        Backticked names are always accepted. A name the classifier merely
        claimed must additionally either look like a deliberate identifier
        (`_is_distinctive`) or be corroborated by a theory the PR actually
        touched -- otherwise `from`, `type` and `change` sail straight back in.
        """
        if name in backticked:
            return True
        if name not in _AMBIGUOUS_WORDS:
            return True
        return _is_distinctive(name) or bool(touched_set & set(symbol_owners.get(name, [])))

    candidates = backticked | claimed
    # Tactic-ness wins over symbol-ness. Several tactic names are also declared
    # somewhere in the theory tree (`rewrite`, `simplify`, `exact`, `change`),
    # and classifying those as symbols both overstates the match (a symbol hit
    # is stronger evidence than a tactic hit) and hides what the entry is
    # actually about.
    tactics = sorted(t for t in candidates if t in tactic_vocab and accept(t))
    tactic_set = set(tactics)
    symbols = sorted(
        t for t in candidates
        if t in symbol_owners and t not in tactic_set and accept(t)
    )

    # Theories only *mentioned*: a resolved name that is itself a theory, or
    # the theory that declares a resolved symbol (so a hit on `frng` surfaces
    # FMap without anyone naming FMap). Owner expansion is restricted to
    # distinctive symbols -- expanding `all` or `map` would otherwise pull in
    # every theory that happens to declare a name that common.
    mentioned: set[str] = set()
    for symbol in symbols:
        if symbol in theory_paths:
            mentioned.add(symbol)
        if _is_distinctive(symbol) or symbol in backticked:
            mentioned.update(symbol_owners.get(symbol, []))
    theories_mentioned = sorted(mentioned - touched_set)

    resolved = set(symbols) | set(tactics) | touched_set | mentioned
    title_tokens = sorted(
        t for t in (claimed | prose)
        if t not in resolved and t.lower() not in _PROSE_STOPWORDS
    )

    kind = str(entry.get("kind") or "unknown")
    relevance = str(entry.get("relevance") or "unknown")
    weight = KIND_WEIGHTS.get(kind, 0.0) * RELEVANCE_MULTIPLIER.get(relevance, 0.3)

    narrative = " ".join(
        str(entry.get(field) or "")
        for field in ("title", "summary", "repair_hint")
    )
    # Import relevance needs the prose to actually be about the import/cloning
    # machinery (strong) or about a theory being reorganized (weak, and only
    # when a theory is demonstrably involved). Touching a file under theories/
    # is never sufficient on its own: a library change is not an import change.
    theory_involved = bool(theories_touched or theories_mentioned)
    structural = kind in ("mechanism_change", "syntax_change")
    import_relevant = bool(
        (_IMPORT_PROSE_STRONG_RE.search(narrative) and (theory_involved or structural))
        or (_IMPORT_PROSE_WEAK_RE.search(narrative) and theory_involved and structural)
    )

    # Entries recovered from git log (collect_changelog --git-log) are keyed by
    # short SHA, not PR number, and link to the commit rather than a pull
    # request. Everything else about them is identical.
    source = str(entry.get("source") or "pr")
    sha = str(entry.get("sha") or "") or None
    if source == "commit" and sha:
        url = f"https://github.com/{repo}/commit/{sha}"
    elif pr_id:
        url = f"https://github.com/{repo}/pull/{pr_id}"
    else:
        url = None

    body = str(detail.get("body") or "")
    return {
        "key": f"{version}#{pr_id}" if pr_id else f"{version}#?",
        "version": version,
        "ordinal": ordinal,
        # `id` is the legacy field name and is what both repair_hints.py
        # modules read; `pr` is the same value under a name that says what it
        # is. Keep both -- dropping `id` silently breaks every existing
        # consumer, and they are one short string.
        "id": pr_id,
        "pr": pr_id if source == "pr" else None,
        "source": source,
        "sha": sha,
        "url": url,
        # --- authored fields, preserved verbatim ---
        "title": entry.get("title"),
        "kind": kind,
        "relevance": relevance,
        "summary": entry.get("summary"),
        "repair_hint": entry.get("repair_hint"),
        "identifiers": list(entry.get("identifiers") or []),
        # --- derived fields ---
        "breaking_weight": round(weight, 3),
        "symbols": symbols,
        "tactics": tactics,
        "theories_touched": theories_touched,
        "theories_mentioned": theories_mentioned,
        "title_tokens": title_tokens,
        "labels": [str(x) for x in (detail.get("labels") or [])],
        "touches": touches,
        "areas": areas,
        "import_relevant": import_relevant,
        "body_excerpt": body[:body_excerpt_chars].strip() or None,
        "has_pr_details": bool(detail),
    }


# --- index assembly ---------------------------------------------------------


def _release_sort_key(release: dict[str, Any]) -> tuple[str, str]:
    """Chronological order, with the version tag as a deterministic tiebreak.

    EasyCrypt tags are `rYYYY.MM`, which sorts correctly as a plain string, so
    the tag is a safe secondary key when two releases share a timestamp (or a
    timestamp is missing entirely).
    """
    return (str(release.get("published_at") or ""), str(release.get("version") or ""))


def _add(index: dict[str, list[str]], key: str, value: str) -> None:
    bucket = index.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def build_index(
    changelog: dict[str, Any],
    *,
    raw: dict[str, Any] | None,
    symbol_owners: dict[str, list[str]],
    theory_paths: dict[str, str],
    tactic_vocab: set[str],
    sources: dict[str, str | None],
    body_excerpt_chars: int = 600,
) -> dict[str, Any]:
    repo = str(changelog.get("repo") or (raw or {}).get("repo") or "EasyCrypt/easycrypt")

    pr_details_by_version: dict[str, dict[str, Any]] = {}
    body_chars_by_version: dict[str, int] = {}
    if raw:
        for release in raw.get("releases") or []:
            tag = str(release.get("tag_name") or "")
            pr_details_by_version[tag] = release.get("pr_details") or {}
            body_chars_by_version[tag] = len(str(release.get("body") or ""))

    ordered = sorted(changelog.get("releases") or [], key=_release_sort_key)

    releases: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for ordinal, release in enumerate(ordered):
        version = str(release.get("version") or "")
        pr_details = pr_details_by_version.get(version, {})
        keys: list[str] = []
        for raw_entry in release.get("entries") or []:
            enriched = enrich_entry(
                raw_entry,
                version=version,
                ordinal=ordinal,
                repo=repo,
                pr_details=pr_details,
                symbol_owners=symbol_owners,
                theory_paths=theory_paths,
                tactic_vocab=tactic_vocab,
                body_excerpt_chars=body_excerpt_chars,
            )
            entries.append(enriched)
            keys.append(enriched["key"])

        year, _, month = version.lstrip("r").partition(".")
        # Coverage is NOT the same as "this release changed nothing". Four of
        # EasyCrypt's tagged releases (r2022.04 .. r2024.09) ship with an empty
        # or one-line GitHub release body upstream, so there is nothing to
        # classify and the catalog has no entries for them at all. A consumer
        # that sees 14 releases and assumes 14 releases' worth of evidence will
        # silently conclude "no known change explains this failure" for the
        # entire pre-r2025.02 span. Record the distinction so it can be warned
        # about instead of inferred.
        body_chars = body_chars_by_version.get(version)
        releases.append({
            "version": version,
            "published_at": release.get("published_at"),
            "ordinal": ordinal,
            "year": int(year) if year.isdigit() else None,
            "month": int(month) if month.isdigit() else None,
            "entry_keys": keys,
            "entry_count": len(keys),
            "has_notes": bool(keys),
            "source_body_chars": body_chars,
            "coverage": (
                "covered" if keys
                else "empty_upstream" if body_chars is not None and body_chars < 64
                else "no_entries"
            ),
        })

    indexes: dict[str, Any] = {
        "by_symbol": {},
        "by_theory": {},
        "by_tactic": {},
        "by_kind": {},
        "by_version": {},
        "import_relevant": [],
    }
    for entry in entries:
        key = entry["key"]
        for symbol in entry["symbols"]:
            _add(indexes["by_symbol"], symbol, key)
        for theory in entry["theories_touched"] + entry["theories_mentioned"]:
            _add(indexes["by_theory"], theory, key)
        for tactic in entry["tactics"]:
            _add(indexes["by_tactic"], tactic, key)
        _add(indexes["by_kind"], entry["kind"], key)
        _add(indexes["by_version"], entry["version"], key)
        if entry["import_relevant"]:
            indexes["import_relevant"].append(key)

    for name in ("by_symbol", "by_theory", "by_tactic", "by_kind"):
        indexes[name] = dict(sorted(indexes[name].items()))

    resolved_entries = sum(
        1 for e in entries
        if e["symbols"] or e["tactics"] or e["theories_touched"] or e["theories_mentioned"]
    )
    high_medium = [e for e in entries if e["relevance"] in ("high", "medium")]
    high_medium_resolved = sum(
        1 for e in high_medium
        if e["symbols"] or e["tactics"] or e["theories_touched"] or e["theories_mentioned"]
    )

    return {
        "schema": SCHEMA,
        "repo": repo,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_from": sources,
        "releases": releases,
        "entries": entries,
        "indexes": indexes,
        "vocabulary": {
            "symbols": len(symbol_owners),
            "theories": len(theory_paths),
            "tactics": len(tactic_vocab),
        },
        "coverage": {
            "releases_with_entries": sum(1 for r in releases if r["has_notes"]),
            "releases_without_entries": [
                r["version"] for r in releases if not r["has_notes"]
            ],
            "starts_at": next((r["version"] for r in releases if r["has_notes"]), None),
            "ends_at": next(
                (r["version"] for r in reversed(releases) if r["has_notes"]), None
            ),
        },
        "stats": {
            "releases": len(releases),
            "entries": len(entries),
            "entries_with_pr_details": sum(1 for e in entries if e["has_pr_details"]),
            "entries_with_resolved_names": resolved_entries,
            "entries_with_touched_theories": sum(1 for e in entries if e["theories_touched"]),
            "high_medium_entries": len(high_medium),
            "high_medium_with_resolved_names": high_medium_resolved,
            "import_relevant_entries": len(indexes["import_relevant"]),
            "distinct_symbols_referenced": len(indexes["by_symbol"]),
            "distinct_theories_referenced": len(indexes["by_theory"]),
            "distinct_tactics_referenced": len(indexes["by_tactic"]),
        },
    }


def _resolve_theory_dirs(explicit: list[Path] | None) -> list[Path]:
    if explicit:
        return explicit
    return [path for path in DEFAULT_THEORY_DIRS if path.is_dir()] or list(DEFAULT_THEORY_DIRS)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument(
        "--raw", type=Path, default=DEFAULT_RAW,
        help="raw_releases.json from collect_changelog.py (changed_files/labels/body)",
    )
    parser.add_argument(
        "--theories", type=Path, action="append", default=None,
        help="EasyCrypt theories directory (repeatable; first match wins)",
    )
    parser.add_argument("--tactics-ref", type=Path, default=DEFAULT_TACTICS_REF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--body-excerpt-chars", type=int, default=600,
        help="how much of each PR body to carry (0 disables)",
    )
    parser.add_argument("--indent", type=int, default=1, help="JSON indent (0 = compact)")
    args = parser.parse_args()

    if not args.changelog.is_file():
        print(f"error: changelog not found: {args.changelog}", file=sys.stderr)
        return 1
    changelog = yaml.safe_load(_read_text(args.changelog)) or {}

    raw = None
    if args.raw and args.raw.is_file():
        raw = json.loads(_read_text(args.raw))
    else:
        print(
            f"warning: {args.raw} not found -- changed_files/labels/PR bodies "
            f"will be missing, which disables exact theory scoping",
            file=sys.stderr,
        )

    theory_dirs = _resolve_theory_dirs(args.theories)
    symbol_owners, theory_paths = build_symbol_vocabulary(theory_dirs)
    if not symbol_owners:
        print(
            f"warning: no EasyCrypt theory sources found under "
            f"{[str(p) for p in theory_dirs]} -- symbol resolution disabled",
            file=sys.stderr,
        )
    tactic_vocab = build_tactic_vocabulary(args.tactics_ref)

    index = build_index(
        changelog,
        raw=raw,
        symbol_owners=symbol_owners,
        theory_paths=theory_paths,
        tactic_vocab=tactic_vocab,
        sources={
            "changelog": str(args.changelog),
            "raw_releases": str(args.raw) if raw else None,
            "theories": next((str(p) for p in theory_dirs if p.is_dir()), None),
            "tactics_ref": str(args.tactics_ref) if args.tactics_ref.is_file() else None,
        },
        body_excerpt_chars=max(0, args.body_excerpt_chars),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(index, indent=args.indent or None, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    stats = index["stats"]
    print(f"Wrote {args.out}", file=sys.stderr)
    print(
        f"  {stats['releases']} releases, {stats['entries']} entries "
        f"({stats['entries_with_pr_details']} with PR details)",
        file=sys.stderr,
    )
    print(
        f"  resolved names on {stats['entries_with_resolved_names']}/{stats['entries']} entries; "
        f"{stats['high_medium_with_resolved_names']}/{stats['high_medium_entries']} "
        f"high/medium-relevance entries",
        file=sys.stderr,
    )
    print(
        f"  {stats['distinct_symbols_referenced']} symbols, "
        f"{stats['distinct_theories_referenced']} theories, "
        f"{stats['distinct_tactics_referenced']} tactics referenced; "
        f"{stats['import_relevant_entries']} import-relevant entries",
        file=sys.stderr,
    )
    coverage = index["coverage"]
    print(
        f"  coverage: {coverage['starts_at']} .. {coverage['ends_at']} "
        f"({coverage['releases_with_entries']}/{stats['releases']} releases)",
        file=sys.stderr,
    )
    if coverage["releases_without_entries"]:
        print(
            f"  warning: no cataloged entries for "
            f"{', '.join(coverage['releases_without_entries'])} -- these releases "
            f"have empty release notes upstream, so a repair range that spans them "
            f"has no evidence to offer, which is NOT the same as 'nothing changed'",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
retrieve_entries.py

Given a structured EasyCrypt changelog, a source and target EasyCrypt version,
and a (broken) proof script or failing tactic, return the small set of
changelog entries most likely to explain the break -- ready to be injected
into a proof-repair prompt.

Two input formats
-----------------
This module reads **both** changelog formats and normalizes them, so callers
never have to care which one is on disk:

* ``output/changelog_index.json`` (**preferred**) -- the flat, typed,
  pre-indexed format produced by ``build_changelog_index.py``. Entries carry
  resolved ``symbols`` / ``tactics`` / ``theories_touched`` /
  ``theories_mentioned`` buckets, exact ``touches`` from the PR's changed
  files, an ``import_relevant`` flag, an integer ``ordinal`` per release, and
  precomputed inverted indexes.
* ``output/changelog.yaml`` (legacy) -- the nested ``releases -> entries``
  format from ``process_changelog.py``, whose only name evidence is the
  untyped, ~85%-English ``identifiers`` list.

``load_changelog`` accepts either and always returns a dict with a legacy
``releases`` view (each release carrying a nested ``entries`` list), so the
four functions the two ``repair_hints.py`` modules depend on --
``load_changelog``, ``tokenize_proof``, ``releases_in_range``,
``score_entries`` -- keep their existing signatures and semantics. When the
indexed format is loaded, those same functions silently get better evidence to
work with, and the richer index-only helpers below become available.

Filtering strategy
------------------
  Tier A (always kept): high-relevance mechanism_change entries in range --
    these are structural changes (cloning, imports, module system) that can
    break a proof without the proof textually referencing anything that
    changed, so identifier overlap can't be relied on to surface them.
  Tier B (kept on match): entries whose names overlap with tokens found in the
    query text (exact token match, not substring). With the indexed format
    this matches the *resolved* buckets and reports which kind of name hit
    (symbol / tactic / theory); with the legacy format it falls back to the
    raw `identifiers` list, exactly as before.
  Tier C (dropped): everything else, especially kind in
    {internal, documentation} or relevance == "low".

Results are ranked: Tier A first, then Tier B by (match strength, # matched
names, proximity to target version), and truncated to --top-n.

Usage:
    python3 retrieve_entries.py \
        --changelog ../output/changelog_index.json \
        --proof broken_proof.ec \
        --source-version r2025.02 \
        --target-version r2025.10 \
        --top-n 12

    # ask what changed about a theory or symbol, independent of a proof
    python3 retrieve_entries.py --changelog ../output/changelog_index.json \
        --theory FMap --source-version r2022.04 --target-version r2026.07
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import yaml

# Tokens that will appear in almost every proof script and carry no
# discriminative signal for matching against changelog identifiers.
GENERIC_TOKENS = {
    "proof", "qed", "lemma", "have", "move", "apply", "by", "smt", "auto",
    "case", "if", "then", "else", "let", "in", "with", "end", "module",
    "op", "theory", "import", "require", "export", "type", "axiom",
}

INDEX_SCHEMA = "ai4ec.changelog-index/1"

# Relative strength of each kind of Tier-B name hit. A symbol or theory match
# is direct evidence that the entry is about something the failing step
# touches; a bare `identifiers` hit is the legacy, low-precision signal and is
# ranked last so it can never outrank corroborated evidence.
_MATCH_WEIGHTS = {
    "symbol": 4.0,
    "theory_touched": 3.0,
    "tactic": 2.5,
    "theory_mentioned": 2.0,
    "identifier": 1.0,
}


# --- loading ----------------------------------------------------------------


def is_index(changelog: dict) -> bool:
    """True when `changelog` came from build_changelog_index.py."""
    return str(changelog.get("schema") or "").startswith("ai4ec.changelog-index/")


def _materialize_releases(index: dict) -> dict:
    """Give an indexed changelog the legacy nested `releases -> entries` view.

    The index stores entries once, flat, and references them from each release
    by key. Every existing consumer expects the nested shape, so it is rebuilt
    here at load time rather than duplicated on disk. Entries are shared by
    reference, so `index["entries"]` and `release["entries"]` are the same
    objects -- a consumer that mutates one sees the change in the other.
    """
    by_key = {entry["key"]: entry for entry in index.get("entries") or []}
    for release in index.get("releases") or []:
        release["entries"] = [
            by_key[key] for key in release.get("entry_keys") or [] if key in by_key
        ]
    index["_entries_by_key"] = by_key
    return index


def load_changelog(path: str) -> dict:
    """Load either changelog format and normalize to a dict with `releases`.

    Dispatch is on content, not filename: a `.json` file that turns out to be
    legacy-shaped, or a `.yaml` file holding an index, both work.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if path.endswith(".json"):
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    if is_index(data):
        return _materialize_releases(data)
    return data


def load_index(path: str) -> dict:
    """Load an indexed changelog, refusing the legacy format.

    Use this when the caller genuinely needs the index-only helpers; use
    `load_changelog` when either format is acceptable.
    """
    data = load_changelog(path)
    if not is_index(data):
        raise ValueError(
            f"{path} is a legacy changelog, not a {INDEX_SCHEMA} index. "
            f"Run proof_corpus/scripts/build_changelog_index.py first."
        )
    return data


def tokenize_proof(text: str) -> set[str]:
    """Extract candidate identifier tokens from a proof script. Exact-token
    matching only (case-sensitive), since EasyCrypt identifiers are exact
    tokens -- we deliberately do NOT do substring/fuzzy matching here to
    avoid false positives like 'map' matching 'map1'."""
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_']*\b", text)
    return {t for t in tokens if t.lower() not in GENERIC_TOKENS and len(t) > 1}


# --- version ranges ---------------------------------------------------------


def _ordered_releases(releases: list[dict]) -> list[dict]:
    """Chronological order, with the version tag as a deterministic tiebreak.

    An indexed changelog already carries an integer `ordinal` computed the same
    way at build time; sorting on it is exact and avoids re-deriving an order
    from timestamps on every call.
    """
    if releases and all("ordinal" in r for r in releases):
        return sorted(releases, key=lambda r: r["ordinal"])
    return sorted(
        releases,
        key=lambda r: (str(r.get("published_at") or ""), str(r.get("version") or "")),
    )


def resolve_version(changelog: dict, version: str) -> int | None:
    """Return the ordinal of `version`, or None when it isn't cataloged."""
    for position, release in enumerate(_ordered_releases(changelog.get("releases") or [])):
        if str(release.get("version")) == str(version):
            return release.get("ordinal", position)
    return None


def releases_in_range(
    releases: list[dict], source_version: str, target_version: str
) -> list[dict]:
    """Order releases chronologically and return the slice strictly after
    source_version up to and including target_version.

    Falls back to returning everything (with a warning) if either tag isn't
    found, rather than silently returning nothing. That fail-open behavior is
    deliberate and load-bearing for old corpora: EasyCrypt's GitHub Releases
    only go back to r2022.04, so a 2020-era proof legitimately has no source
    tag in the catalog and "every release we know about" is the correct,
    maximally-exposed answer. Use `releases_between(..., strict=True)` when a
    caller would rather hear about the miss than silently widen the range.
    """
    ordered = _ordered_releases(releases)
    versions = [r["version"] for r in ordered]

    if source_version not in versions or target_version not in versions:
        missing = [v for v in (source_version, target_version) if v not in versions]
        print(
            f"warning: version(s) not found in changelog: {missing}. "
            f"Using full changelog range instead.",
            file=sys.stderr,
        )
        return ordered

    i_src = versions.index(source_version)
    i_tgt = versions.index(target_version)
    lo, hi = sorted((i_src, i_tgt))
    # entries strictly after the source version, up to and including target
    return ordered[lo + 1 : hi + 1]


def releases_between(
    changelog: dict,
    source_version: str,
    target_version: str,
    *,
    strict: bool = False,
) -> list[dict]:
    """`releases_in_range` over a whole changelog dict, with an opt-in strict
    mode that raises instead of silently widening to the full history."""
    releases = changelog.get("releases") or []
    if strict:
        for version in (source_version, target_version):
            if resolve_version(changelog, version) is None:
                raise KeyError(f"version {version!r} is not in the changelog")
    return releases_in_range(releases, source_version, target_version)


# --- matching ---------------------------------------------------------------


def _entry_names(entry: dict) -> list[tuple[str, str]]:
    """Every name this entry can be matched on, as (name, match_kind) pairs.

    Indexed entries expose typed buckets; legacy entries only have the untyped
    `identifiers` list, which is reported as match_kind "identifier" so a
    consumer can tell corroborated evidence from a bare title-word hit.
    """
    typed: list[tuple[str, str]] = []
    for field, kind in (
        ("symbols", "symbol"),
        ("theories_touched", "theory_touched"),
        ("tactics", "tactic"),
        ("theories_mentioned", "theory_mentioned"),
    ):
        for name in entry.get(field) or []:
            typed.append((str(name), kind))
    if typed:
        return typed
    return [(str(name), "identifier") for name in (entry.get("identifiers") or [])]


def score_entries(
    in_range: list[dict],
    proof_tokens: set[str],
    top_n: int,
    *,
    tier_a_cap: int | None = None,
) -> list[dict]:
    """Rank the entries in `in_range` against `proof_tokens`.

    Output entries keep every field the source entry had, plus:
      version, reason, overlap, proximity   (unchanged, legacy contract)
      match_kinds, match_strength           (new; empty/0.0 for Tier A)
    `overlap` remains the flat list of matched names so existing consumers
    (both repair_hints.py modules) keep working untouched.

    `tier_a_cap` bounds how many unmatched "structural change in range"
    entries may occupy the result. Tier A is unconditional by design -- a
    cloning/import change can break a proof that names nothing that changed --
    but over a wide version range there are more high-relevance mechanism
    changes than result slots, and concatenating Tier A ahead of Tier B let
    them crowd out every name-matched hit. Measured on the real changelog over
    r2025.02 -> r2026.07, all 6 of a 6-slot budget went to Tier A and the
    actual `SmtMap`/`FMap` match never appeared. The cap defaults to a third of
    the budget (at least one), so directly-matched evidence always has room.
    Pass 0 to suppress Tier A entirely, or a large number for the old behavior.
    """
    tier_a = []
    tier_b = []

    n_releases = len(in_range)
    for pos, rel in enumerate(in_range):
        # distance from target release (0 = the target release itself)
        proximity = n_releases - 1 - pos
        for entry in rel.get("entries", []):
            kind = entry.get("kind")
            relevance = entry.get("relevance")
            structural = kind == "mechanism_change" and relevance == "high"

            if not structural and (relevance == "low" or kind in ("internal", "documentation")):
                continue

            matched: dict[str, str] = {}
            for name, match_kind in _entry_names(entry):
                if name in proof_tokens:
                    # Keep the strongest classification of a name that appears
                    # in more than one bucket.
                    if _MATCH_WEIGHTS.get(match_kind, 0.0) > _MATCH_WEIGHTS.get(
                        matched.get(name, ""), 0.0
                    ):
                        matched[name] = match_kind

            kinds = sorted(set(matched.values()))
            strength = sum(_MATCH_WEIGHTS.get(k, 1.0) for k in matched.values())
            scored = {**entry, "version": rel["version"],
                      "overlap": sorted(matched),
                      "proximity": proximity,
                      "match_kinds": kinds,
                      "match_strength": round(strength, 3)}

            if matched:
                # A structural change that ALSO names something the failing
                # step touches is the strongest evidence available -- it used
                # to be filed as a generic Tier A entry and lose its match
                # entirely, which is how the real `SmtMap` -> `FMap` split
                # (#605, mechanism_change/high) came back with an empty
                # overlap. Score it in Tier B, with the structural bonus.
                if structural and any(k != "identifier" for k in kinds):
                    scored["reason"] = f"structural change matching {', '.join(kinds)}"
                    scored["match_strength"] = round(
                        strength + _MATCH_WEIGHTS["symbol"], 3
                    )
                elif structural:
                    # Structural, but the only hit is a bare legacy-identifier
                    # word. No bonus: an untyped `identifiers` match is the
                    # weakest signal we have and must not outrank a resolved
                    # symbol hit just because its entry happens to be
                    # mechanism_change/high.
                    scored["reason"] = "structural change, weak identifier match"
                else:
                    scored["reason"] = f"matched {', '.join(kinds)}"
                tier_b.append(scored)
            elif structural:
                scored["reason"] = "structural change in range"
                tier_a.append(scored)

    tier_b.sort(key=lambda e: (-e["match_strength"], -len(e["overlap"]), -e["proximity"]))
    # Closest to the target release first, then most breaking. Previously Tier A
    # came out in whatever order the release list happened to be in.
    tier_a.sort(key=lambda e: (e["proximity"], -float(e.get("breaking_weight") or 0.0)))

    if tier_a_cap is None:
        tier_a_cap = max(1, top_n // 3)
    kept_a = tier_a[: max(0, tier_a_cap)]
    # Tier A keeps priority within its quota; Tier B fills the rest. Any
    # leftover budget goes back to Tier A rather than being wasted.
    combined = kept_a + tier_b[: max(0, top_n - len(kept_a))]
    if len(combined) < top_n:
        already = {id(e) for e in combined}
        combined += [e for e in tier_a if id(e) not in already][: top_n - len(combined)]
    return combined[:top_n]


# --- index-only helpers -----------------------------------------------------


def _lookup(changelog: dict, index_name: str, names) -> list[dict]:
    if not is_index(changelog):
        return []
    by_key = changelog.get("_entries_by_key") or {
        e["key"]: e for e in changelog.get("entries") or []
    }
    index = (changelog.get("indexes") or {}).get(index_name) or {}
    seen: list[dict] = []
    emitted: set[str] = set()
    for name in names:
        for key in index.get(str(name)) or []:
            if key not in emitted and key in by_key:
                emitted.add(key)
                seen.append(by_key[key])
    return seen


def coverage_gap(changelog: dict, source_version: str, target_version: str) -> list[str]:
    """Releases in `(source, target]` that have no cataloged entries at all.

    Four of EasyCrypt's tagged releases (r2022.04 .. r2024.09) ship empty
    GitHub release notes, so the catalog has nothing for them. Retrieval over a
    range that spans them returns few or no hits, which reads as "no known
    change explains this failure" when the truth is "we have no notes for that
    period." Callers should report a non-empty result here rather than let the
    silence be mistaken for evidence.

    Works on both formats: a legacy release simply has no `entries` key
    populated for those versions either.
    """
    in_range = releases_in_range(
        changelog.get("releases") or [], source_version, target_version
    )
    return [
        str(release.get("version"))
        for release in in_range
        if not (release.get("entries") or release.get("entry_keys"))
    ]


def entries_for_symbols(changelog: dict, names) -> list[dict]:
    """Every entry mentioning any of `names` as a resolved EasyCrypt symbol.

    O(1) per name against the prebuilt inverted index -- this is the query
    that used to require scanning every release.
    """
    return _lookup(changelog, "by_symbol", names)


def entries_for_theories(changelog: dict, names) -> list[dict]:
    """Every entry that touched or mentioned any of the named theories."""
    return _lookup(changelog, "by_theory", names)


def entries_for_tactics(changelog: dict, names) -> list[dict]:
    """Every entry about any of the named tactics."""
    return _lookup(changelog, "by_tactic", names)


def import_relevant_entries(changelog: dict, versions=None) -> list[dict]:
    """Entries flagged as relevant to `require`/`import`/`clone` repair.

    An entry qualifies when the PR changed a file under `theories/`, when it
    names a theory, or when it is a mechanism/syntax change whose prose is
    about the import or cloning machinery. Optionally restricted to a set of
    release versions (e.g. the output of `releases_between`).
    """
    if not is_index(changelog):
        return []
    by_key = changelog.get("_entries_by_key") or {
        e["key"]: e for e in changelog.get("entries") or []
    }
    keys = (changelog.get("indexes") or {}).get("import_relevant") or []
    allowed = {str(v) for v in versions} if versions is not None else None
    out = []
    for key in keys:
        entry = by_key.get(key)
        if entry is None:
            continue
        if allowed is not None and str(entry.get("version")) not in allowed:
            continue
        out.append(entry)
    return out


def prompt_ready(entries: list[dict]) -> list[dict]:
    """Compact, prompt-shaped projection of scored entries.

    Drops bookkeeping the repair LLM does not need while keeping the fields
    that tell it *what* changed, *where*, and *how confident* the match is.
    """
    out = []
    for entry in entries:
        row = {
            "version": entry.get("version"),
            "id": entry.get("id") or entry.get("pr"),
            "kind": entry.get("kind"),
            "repair_hint": entry.get("repair_hint") or entry.get("summary"),
            "matched_identifiers": entry.get("overlap"),
            "reason": entry.get("reason"),
        }
        for optional in ("url", "theories_touched", "match_kinds", "import_relevant"):
            if entry.get(optional):
                row[optional] = entry[optional]
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--changelog", required=True,
        help="changelog_index.json (preferred) or legacy changelog.yaml",
    )
    ap.add_argument("--proof", default=None, help="path to the (broken) .ec proof script")
    ap.add_argument(
        "--text", default=None,
        help="literal query text instead of --proof (e.g. a failing tactic + error)",
    )
    ap.add_argument(
        "--theory", action="append", default=None,
        help="report everything that changed about this theory (repeatable; index only)",
    )
    ap.add_argument(
        "--symbol", action="append", default=None,
        help="report everything that changed about this symbol (repeatable; index only)",
    )
    ap.add_argument(
        "--import-relevant", action="store_true",
        help="report only entries relevant to require/import/clone repair (index only)",
    )
    ap.add_argument("--source-version", required=True, help="tag the proof was written against")
    ap.add_argument("--target-version", required=True, help="tag the proof needs to work on")
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--out", default=None, help="write JSON result here (default: stdout)")
    args = ap.parse_args()

    changelog = load_changelog(args.changelog)
    in_range = releases_in_range(
        changelog.get("releases") or [], args.source_version, args.target_version
    )
    versions_in_range = [r["version"] for r in in_range]

    if args.theory or args.symbol or args.import_relevant:
        if not is_index(changelog):
            print(
                "error: --theory/--symbol/--import-relevant need the indexed "
                "changelog; run build_changelog_index.py first.",
                file=sys.stderr,
            )
            sys.exit(2)
        selected: list[dict] = []
        if args.theory:
            selected += entries_for_theories(changelog, args.theory)
        if args.symbol:
            selected += entries_for_symbols(changelog, args.symbol)
        if args.import_relevant:
            selected += import_relevant_entries(changelog, versions_in_range)
        seen: set[str] = set()
        results = []
        for entry in selected:
            if entry["key"] in seen or entry["version"] not in versions_in_range:
                continue
            seen.add(entry["key"])
            results.append({**entry, "reason": "direct index lookup", "overlap": []})
        results.sort(key=lambda e: (-e.get("breaking_weight", 0.0), e["ordinal"]))
        results = results[: args.top_n]
    else:
        if args.proof:
            with open(args.proof, encoding="utf-8") as handle:
                query_text = handle.read()
        elif args.text:
            query_text = args.text
        else:
            print("error: one of --proof / --text / --theory / --symbol is required", file=sys.stderr)
            sys.exit(2)
        results = score_entries(in_range, tokenize_proof(query_text), args.top_n)

    out = json.dumps(prompt_ready(results), indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(out)
        print(f"Wrote {len(results)} entries to {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()

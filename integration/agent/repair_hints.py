"""Repair-mode retrieval: changelog + repair_doc facts for a failed tactic.

Ported from shannon-prover's ``core/easycrypt/repair_hints.py`` (the
interaction/session-machinery half of that project isn't compatible with
this project's own EasyCrypt wrapper -- see ``integration/experiment/repair_bootstrap.py``
-- but this knowledge-retrieval half has zero dependency on that machinery:
it's pure Python, needing only PyYAML plus the sibling ``proof_corpus/``
directory's static files.

When the replay-until-failure bootstrap (``integration/experiment/repair_bootstrap.py``)
hits a tactic that no longer applies, this module surfaces two static,
sibling-repo sources of dated, sourced facts about what changed between
EasyCrypt releases -- instead of (or alongside) the ambient premise catalog
``integration/agent/premises.py`` already supplies:

* ``proof_corpus/output/changelog_index.json`` -- a structured EasyCrypt
  release changelog, matched via ``proof_corpus/scripts/retrieve_entries.py``'s
  name-overlap scoring (loaded dynamically, not vendored). Falls back to the
  legacy ``proof_corpus/output/changelog.yaml`` when the index has not been
  built; see ``resolve_changelog_path``.
* ``proof_corpus/repair_doc/*.json`` -- per-library reference notes, matched
  by simple token/path overlap (no existing script covers this half).

Unlike shannon-prover, this project has no ``ToolView``/view-compiler layer
-- its prompt (``integration/agent/prompt.py::build_prompt``) is built from
flat text sections, so results here are formatted as a plain-text block via
``format_repair_hints_for_prompt`` rather than a structured JSON view.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import types
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


class RepairHintsUnavailable(Exception):
    """Raised when proof_corpus's changelog/retrieval tooling can't be
    loaded. Repair mode must degrade gracefully on this (proceed without
    hints, logged) rather than crash -- proof_corpus is a sibling repo
    outside this project's version control and may not be present, or may
    drift its layout/function names independently."""


_REQUIRED_RETRIEVE_ENTRIES_ATTRS = (
    "load_changelog",
    "tokenize_proof",
    "releases_in_range",
    "score_entries",
)

_REPAIR_DOC_LIBRARY_SUFFIX = "_lib.json"

# Changelog files to try, best first. ``changelog_index.json`` is the flat,
# typed, pre-indexed format from ``proof_corpus/scripts/build_changelog_index.py``:
# its entries carry resolved symbol/tactic/theory buckets and exact
# changed-file theory scope, so ``score_entries`` matches on corroborated
# names instead of the legacy ``identifiers`` list (measured ~85% English
# prose). ``changelog.yaml`` remains a fully supported fallback -- proof_corpus
# is a sibling directory that may not have been re-indexed yet.
_CHANGELOG_CANDIDATES = (
    ("output", "changelog_index.json"),
    ("output", "changelog.yaml"),
)


def _default_proof_corpus_root() -> Path:
    return REPO_ROOT / "proof_corpus"


def resolve_proof_corpus_root() -> Path:
    env_override = os.environ.get("SHANNON_PROOF_CORPUS_DIR")
    root = Path(env_override) if env_override else _default_proof_corpus_root()
    if not root.is_dir():
        raise RepairHintsUnavailable(
            f"proof_corpus directory not found at {root} "
            f"(set SHANNON_PROOF_CORPUS_DIR to override)"
        )
    return root


def resolve_changelog_path(corpus_root: Path | None = None) -> Path:
    """Return the best available changelog file under ``corpus_root``.

    Prefers the indexed JSON and falls back to the legacy YAML. Raises
    ``RepairHintsUnavailable`` when neither exists, so a missing corpus stays
    a degrade-gracefully condition rather than a crash.
    """
    root = corpus_root if corpus_root is not None else resolve_proof_corpus_root()
    for parts in _CHANGELOG_CANDIDATES:
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(root.joinpath(*parts)) for parts in _CHANGELOG_CANDIDATES)
    raise RepairHintsUnavailable(f"no changelog found (tried: {tried})")


_retrieve_entries_module_cache: types.ModuleType | None = None


def _load_retrieve_entries_module() -> types.ModuleType:
    global _retrieve_entries_module_cache
    if _retrieve_entries_module_cache is not None:
        return _retrieve_entries_module_cache

    corpus_root = resolve_proof_corpus_root()
    script_path = corpus_root / "scripts" / "retrieve_entries.py"
    if not script_path.is_file():
        raise RepairHintsUnavailable(f"retrieve_entries.py not found at {script_path}")

    spec = importlib.util.spec_from_file_location(
        "integration._vendor.retrieve_entries", script_path,
    )
    if spec is None or spec.loader is None:
        raise RepairHintsUnavailable(f"could not load module spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive
        raise RepairHintsUnavailable(
            f"failed to import {script_path}: {exc}"
        ) from exc

    missing = [
        attr for attr in _REQUIRED_RETRIEVE_ENTRIES_ATTRS
        if not hasattr(module, attr)
    ]
    if missing:
        raise RepairHintsUnavailable(
            f"{script_path} is missing expected function(s): {missing} "
            f"(proof_corpus schema may have drifted)"
        )

    _retrieve_entries_module_cache = module
    return module


def get_changelog_repair_hints(
    *,
    failing_tactic_text: str,
    ec_error_text: str,
    source_ec_version: str,
    target_ec_version: str,
    top_n: int = 12,
) -> list[dict[str, Any]]:
    """Return the changelog entries most likely to explain the failure.

    Wraps retrieve_entries.py's score_entries(releases_in_range(...),
    tokenize_proof(...), top_n). The failing tactic text is tokenized
    together with EasyCrypt's raw error text (rather than a whole proof
    file, since the caller here only has the one failing step) -- per the
    project decision to skip a separate error-catalog stage and extract
    identifiers directly from these two strings.
    """
    module = _load_retrieve_entries_module()
    changelog = module.load_changelog(str(resolve_changelog_path()))
    tokens = module.tokenize_proof(f"{failing_tactic_text}\n{ec_error_text}")
    in_range = module.releases_in_range(
        changelog.get("releases", []), source_ec_version, target_ec_version,
    )
    return module.score_entries(in_range, tokens, top_n)


def get_changelog_repair_hints_by_release(
    *,
    failing_tactic_text: str,
    ec_error_text: str,
    source_ec_version: str,
    target_ec_version: str,
    already_consumed_versions: set[str] | None = None,
    top_n_per_release: int = 4,
) -> tuple[list[dict[str, Any]], str | None]:
    """Walk changelog releases in chronological order (oldest first) within
    ``(source_ec_version, target_ec_version]``, skipping any release already
    in ``already_consumed_versions``, and return the entries from the FIRST
    release with an applicable hit -- rather than ``get_changelog_repair_hints``'s
    flat whole-range lookup.

    Why this matters for an old repo spanning many releases: a flat lookup
    over the whole range returns up to ``top_n`` entries pooled across every
    release at once, with no signal about which one to try first, and can
    silently crowd out the release that actually explains a failure once an
    identifier was renamed more than once across the span (e.g. renamed at
    r2023.09, renamed AGAIN at r2025.02 -- a flat lookup returns both with no
    ordering; hopping surfaces r2023.09 first, matching the order a human
    porting the proof release-by-release would actually hit them).

    ``releases_in_range`` already returns releases sorted chronologically
    (ascending by ``published_at``) -- this walks that order directly rather
    than routing through ``score_entries``'s whole-range flattening.

    Returns ``(entries, matched_version)``; ``matched_version`` is ``None``
    if no release in the (remaining, unconsumed) range has any applicable
    entry. Callers that get a NEW failure after acting on this hop's
    suggestion should pass the previous ``matched_version`` (accumulated) in
    ``already_consumed_versions`` to advance to the next hop rather than
    re-surfacing the same release.
    """
    module = _load_retrieve_entries_module()
    changelog = module.load_changelog(str(resolve_changelog_path()))
    tokens = module.tokenize_proof(f"{failing_tactic_text}\n{ec_error_text}")
    in_range = module.releases_in_range(
        changelog.get("releases", []), source_ec_version, target_ec_version,
    )
    consumed = already_consumed_versions or set()

    for release in in_range:  # already chronological, oldest first
        version = str(release.get("version") or "")
        if version in consumed:
            continue
        hits = module.score_entries([release], tokens, top_n_per_release)
        if hits:
            return hits, version
    return [], None


def _as_list(value: Any) -> list[str]:
    """Normalize a repair_doc field that is sometimes a list and sometimes a
    bare string.

    ``version_diffs_found`` is a list in 5 of the 18 ``*_lib.json`` files and a
    plain sentence ("None found by name in the scanned changelog window.") in
    the other 13. Iterating the string form yields one *character* per step,
    which silently turned those docs' version notes into single-character
    tokens -- dropped by the ``len > 1`` filter, so they contributed nothing to
    matching and rendered as garbage in the prompt.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _truncate(text: str, limit: int) -> str:
    """Clip prose to `limit` characters on a word boundary.

    Repair-hint text is re-sent on every agent step, so it competes directly
    with premises and failure history for context. Some
    ``current_content_summary`` fields run past 2000 characters.
    """
    text = " ".join(text.split())
    if limit <= 0 or len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.")
    return f"{clipped} ..."


def _tokenize_for_repair_doc(text: str) -> set[str]:
    """Same tokenization rule retrieve_entries.py uses (exact-token,
    case-sensitive), reused directly rather than a second regex so
    changelog and repair_doc matching stay consistent."""
    module = _load_retrieve_entries_module()
    return module.tokenize_proof(text)


def _snippets_from_index(
    index: dict[str, Any], identifiers: set[str], max_docs: int
) -> list[dict[str, Any]]:
    """Score the condensed library records the same way the raw docs are.

    Adds two signals the raw files cannot provide: a hit on a name the theory
    actually declares (from the parsed source, weighted highest -- it is the
    direct answer to "where does this symbol live"), and a hit on the theory's
    own name in its `requires` list.
    """
    if not identifiers:
        return []
    symbol_index = index.get("symbol_index") or {}
    owning_theories: set[str] = set()
    for name in identifiers:
        owning_theories.update(symbol_index.get(name) or [])

    scored: list[tuple[int, dict[str, Any]]] = []
    for library in index.get("libraries") or []:
        theory = str(library.get("theory") or "")
        path_field = str(library.get("path") or "")

        declares_hit = theory in owning_theories
        path_hit = any(ident and ident in path_field for ident in identifiers)
        name_hit = theory in identifiers

        text = "\n".join(
            [
                str(library.get("summary") or ""),
                str(library.get("import_repair_note") or ""),
                " ".join(str(v) for v in (library.get("version_notes") or [])),
            ]
        )
        overlap = identifiers & _safe_tokenize(text)

        if not (overlap or path_hit or declares_hit or name_hit):
            continue

        score = (
            len(overlap)
            + (200 if declares_hit else 0)
            + (100 if path_hit or name_hit else 0)
        )
        entry = dict(library)
        entry["_file"] = library.get("source_file")
        entry["_matched_identifiers"] = sorted(overlap)
        entry["_path_hit"] = path_hit
        entry["_declares_matched_symbol"] = declares_hit
        # Keys the prompt renderer already understands, so the index path and
        # the raw-file path format identically.
        entry["current_content_summary"] = library.get("summary")
        entry["version_diffs_found"] = library.get("version_notes") or []
        requires = library.get("requires") or []
        entry["requires"] = ", ".join(requires) if requires else None
        scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _score, entry in scored[:max_docs]]


def load_repair_docs_index() -> dict[str, Any] | None:
    """Load ``output/repair_docs_index.json`` if it has been built.

    Produced by ``proof_corpus/scripts/build_repair_docs.py``: the authored
    ``repair_doc/*_lib.json`` prose condensed, plus per-theory facts parsed
    from the real EasyCrypt sources (`requires`/`imports`/`clones`) and a
    tree-wide ``symbol_index``. Returns ``None`` (never raises) when it is
    absent or unreadable -- callers fall back to the raw authored docs.
    """
    try:
        path = resolve_proof_corpus_root() / "output" / "repair_docs_index.json"
    except RepairHintsUnavailable:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if not str(data.get("schema") or "").startswith("ai4ec.repair-docs-index/"):
        return None
    return data


def resolve_symbol_theories(
    identifiers: set[str], index: dict[str, Any] | None = None
) -> dict[str, list[str]]:
    """Map each identifier to the theories that declare it, tree-wide.

    This answers the question the harness could not previously ask at all:
    ``ec.exe llm -premises`` reports what **is** in scope, never what
    **could** be, so an "unknown symbol" error had no route back to
    "`require import <Theory>.`". Only names that actually resolve are
    returned; a name owned by several theories keeps all owners, because
    picking one silently is exactly the guess a repair agent should not make
    (``eq_except`` is declared by both ``FMap`` and ``SmtMap`` -- the two
    sides of the r2025.02 split).
    """
    index = index if index is not None else load_repair_docs_index()
    if not index:
        return {}
    symbol_index = index.get("symbol_index") or {}
    resolved = {}
    for name in sorted(identifiers):
        owners = symbol_index.get(name)
        if owners:
            resolved[name] = list(owners)
    return resolved


def get_repair_doc_snippets(
    *,
    identifiers: set[str],
    repair_doc_dir: Path | None = None,
    max_docs: int = 3,
) -> list[dict[str, Any]]:
    """Return proof_corpus/repair_doc/*_lib.json entries whose content or
    path overlaps `identifiers`, ranked by overlap count.

    No existing script covers this half (unlike changelog matching, which
    reuses retrieve_entries.py) -- this is new matching logic over
    hand/LLM-curated per-library reference docs.

    When ``output/repair_docs_index.json`` exists, its condensed records are
    used instead of the raw authored files: same matching, but the summary is
    clipped, ``requires`` is the list parsed from the real source rather than
    prose, and every library has an ``import_repair_note`` (4 authored, 14
    derived) instead of only four.
    """
    index = load_repair_docs_index()
    if index is not None:
        return _snippets_from_index(index, identifiers, max_docs)

    if repair_doc_dir is None:
        repair_doc_dir = resolve_proof_corpus_root() / "repair_doc"
    if not repair_doc_dir.is_dir():
        raise RepairHintsUnavailable(f"repair_doc directory not found at {repair_doc_dir}")
    if not identifiers:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for doc_path in sorted(repair_doc_dir.glob(f"*{_REPAIR_DOC_LIBRARY_SUFFIX}")):
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue

        path_field = str(doc.get("path") or "")
        path_hit = any(
            ident and ident in path_field for ident in identifiers
        )

        summary = str(doc.get("current_content_summary") or "")
        diffs = " ".join(_as_list(doc.get("version_diffs_found")))
        note = str(doc.get("import_repair_note") or "")
        doc_tokens = _tokenize_for_repair_doc(f"{summary}\n{diffs}\n{note}")
        overlap = identifiers & doc_tokens

        if not overlap and not path_hit:
            continue

        score = len(overlap) + (100 if path_hit else 0)
        entry = dict(doc)
        entry["_file"] = doc_path.name
        entry["_matched_identifiers"] = sorted(overlap)
        entry["_path_hit"] = path_hit
        scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _score, entry in scored[:max_docs]]


def _safe_tokenize(text: str) -> set[str]:
    """Tokenize with the shared rule, degrading to a local regex.

    The repair-docs index path must not hard-fail when proof_corpus's
    ``retrieve_entries.py`` is missing: its primary signals (the theory
    declares a matched symbol, a path/name hit) need no tokenizer at all, and
    losing the secondary prose-overlap score is far better than losing the
    whole lookup.
    """
    try:
        return _tokenize_for_repair_doc(text)
    except RepairHintsUnavailable:
        tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_']*\b", text)
        return {t for t in tokens if len(t) > 1}


def _extract_identifiers(failing_tactic_text: str, ec_error_text: str) -> set[str]:
    try:
        return _tokenize_for_repair_doc(f"{failing_tactic_text}\n{ec_error_text}")
    except RepairHintsUnavailable:
        # Fall back to a bare identifier-shaped-token regex if
        # retrieve_entries.py itself couldn't be loaded, so repair_doc
        # matching can still be attempted independently of the changelog path.
        tokens = re.findall(
            r"\b[A-Za-z_][A-Za-z0-9_']*\b", f"{failing_tactic_text}\n{ec_error_text}",
        )
        return {t for t in tokens if len(t) > 1}


def format_repair_hints_for_prompt(
    changelog_entries: list[dict[str, Any]],
    repair_doc_entries: list[dict[str, Any]],
    *,
    summary_chars: int = 700,
    symbol_theories: dict[str, list[str]] | None = None,
) -> str:
    """Render changelog + repair_doc hits as a plain-text prompt section.

    integration/'s prompt is built from flat strings (see
    integration/agent/prompt.py::build_prompt), not a structured ToolView
    like shannon-prover's -- this replaces that project's
    build_repair_hints_tool_view/emit_repair_hints pair.

    Every field rendered here is optional: entries from the legacy
    ``changelog.yaml`` carry only version/title/repair_hint, while entries
    from ``changelog_index.json`` additionally carry ``kind``, ``reason``,
    ``overlap`` (which names actually matched), and ``theories_touched``
    (which theory files the release actually changed). The extra lines are
    emitted only when present, so both formats render sensibly.

    Why the detail matters: the model has to decide whether a hint applies to
    the failure in front of it. "Structural change in range" and "matched
    symbol FMap" are very different confidence levels, and a hint that names
    the theory the failing step imports is far more actionable than a bare
    prose sentence. Previously all of that was dropped.
    """
    lines: list[str] = []
    if symbol_theories:
        # Put this first: when a name in the failing step no longer resolves,
        # "it lives in theory T" is the single most directly actionable fact
        # available, and it is checked against the installed sources rather
        # than inferred from prose.
        lines.append(
            "Where the names in this step are declared (current EasyCrypt tree):"
        )
        for name, owners in symbol_theories.items():
            if len(owners) == 1:
                lines.append(f"- `{name}` is declared in {owners[0]} "
                             f"(`require import {owners[0]}.`)")
            else:
                lines.append(
                    f"- `{name}` is declared in {len(owners)} theories: "
                    f"{', '.join(owners)} -- qualify the reference or require "
                    f"the one you mean"
                )
        lines.append("")
    if changelog_entries:
        lines.append("Known EasyCrypt changelog entries in range:")
        for entry in changelog_entries:
            version = str(entry.get("version") or "")
            title = str(entry.get("title") or "")
            hint = str(entry.get("repair_hint") or entry.get("summary") or "")
            kind = str(entry.get("kind") or "")
            head = f"- [{version}]"
            if kind:
                head += f" ({kind})"
            lines.append(f"{head} {title}: {hint}")

            detail: list[str] = []
            overlap = [str(x) for x in (entry.get("overlap") or [])]
            reason = str(entry.get("reason") or "")
            if overlap:
                detail.append(f"matched {', '.join(overlap)}")
            elif reason:
                detail.append(reason)
            touched = [str(x) for x in (entry.get("theories_touched") or [])]
            if touched:
                shown = ", ".join(touched[:6])
                if len(touched) > 6:
                    shown += f", +{len(touched) - 6} more"
                detail.append(f"changed theories: {shown}")
            if entry.get("url"):
                detail.append(str(entry["url"]))
            if detail:
                lines.append(f"    ({'; '.join(detail)})")
    if repair_doc_entries:
        if lines:
            lines.append("")
        lines.append("Known library reference notes:")
        for doc in repair_doc_entries:
            path = str(doc.get("path") or doc.get("_file") or "")
            summary = _truncate(str(doc.get("current_content_summary") or ""), summary_chars)
            lines.append(f"- {path}: {summary}")
            # `import_repair_note` is written specifically for repairing a
            # `require import` that no longer resolves, and is the single most
            # actionable field in a repair_doc entry. It was previously
            # dropped entirely, and is deliberately NOT truncated.
            note = str(doc.get("import_repair_note") or "").strip()
            if note:
                lines.append(f"    IMPORT REPAIR: {note}")
            requires = str(doc.get("requires") or "").strip()
            if requires and requires.lower() not in ("none", "none."):
                lines.append(f"    this theory requires: {requires}")
            for diff in _as_list(doc.get("version_diffs_found"))[:3]:
                # 13 of the 18 library docs record "no diffs found" as a
                # sentence rather than an empty list; that is not a version
                # note and should not be shown as one.
                if diff.lower().startswith(("none", "no pr title")):
                    continue
                lines.append(f"    version note: {_truncate(diff, summary_chars)}")
    return "\n".join(lines)


def get_repair_hints_text(
    *,
    failing_tactic_text: str,
    ec_error_text: str,
    source_ec_version: str,
    target_ec_version: str,
    already_consumed_versions: set[str] | None = None,
    top_n_per_release: int = 4,
) -> tuple[str, list[str], str | None]:
    """Fetch changelog + repair_doc hints and format them for the prompt.

    The changelog half hops through releases in chronological order
    (``get_changelog_repair_hints_by_release``) rather than pooling the whole
    ``(source, target)`` range at once -- see that function's docstring.
    ``repair_doc`` has no per-release ordering concept (it's a flat set of
    library reference docs, not changelog entries), so
    ``get_repair_doc_snippets`` is unchanged.

    Returns ``(text, notes, matched_version)``. ``text`` is empty if nothing
    was found or proof_corpus couldn't be reached (never raises -- repair
    hints are optional supplementary context, per the shannon-prover
    original's design). ``notes`` carries any degrade-gracefully
    explanations for logging. ``matched_version`` is the release the
    changelog half hopped to (or ``None``) -- pass it back via
    ``already_consumed_versions`` on a later call (after a NEW failure) to
    advance to the next hop instead of re-surfacing the same release.
    """
    notes: list[str] = []

    changelog_entries: list[dict[str, Any]] = []
    matched_version: str | None = None
    try:
        changelog_entries, matched_version = get_changelog_repair_hints_by_release(
            failing_tactic_text=failing_tactic_text,
            ec_error_text=ec_error_text,
            source_ec_version=source_ec_version,
            target_ec_version=target_ec_version,
            already_consumed_versions=already_consumed_versions,
            top_n_per_release=top_n_per_release,
        )
    except RepairHintsUnavailable as exc:
        notes.append(f"changelog repair hints unavailable: {exc}")

    # Surface uncataloged releases as a logged note rather than letting an
    # empty result read as "no known change explains this". Optional: older
    # proof_corpus checkouts have no coverage_gap(), which is not an error.
    try:
        module = _load_retrieve_entries_module()
        if hasattr(module, "coverage_gap"):
            changelog = module.load_changelog(str(resolve_changelog_path()))
            gap = module.coverage_gap(
                changelog, source_ec_version, target_ec_version
            )
            if gap:
                notes.append(
                    "changelog has no cataloged entries for "
                    f"{', '.join(gap)} (empty release notes upstream) -- absence "
                    "of hints across that span is missing data, not evidence "
                    "that nothing changed"
                )
    except RepairHintsUnavailable:
        pass

    identifiers = _extract_identifiers(failing_tactic_text, ec_error_text)
    repair_doc_entries: list[dict[str, Any]] = []
    try:
        repair_doc_entries = get_repair_doc_snippets(identifiers=identifiers)
    except RepairHintsUnavailable as exc:
        notes.append(f"repair_doc hints unavailable: {exc}")

    # Only names actually present in the failing step/error are resolved, so
    # this stays small; it is empty when the repair-docs index has not been
    # built (build_repair_docs.py).
    symbol_theories = resolve_symbol_theories(identifiers)

    text = format_repair_hints_for_prompt(
        changelog_entries, repair_doc_entries, symbol_theories=symbol_theories,
    )
    return text, notes, matched_version

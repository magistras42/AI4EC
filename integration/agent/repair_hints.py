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

* ``proof_corpus/output/changelog.yaml`` -- a structured EasyCrypt release
  changelog, matched via ``proof_corpus/scripts/retrieve_entries.py``'s
  existing identifier-overlap scoring (loaded dynamically, not vendored).
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
    corpus_root = resolve_proof_corpus_root()
    changelog_path = corpus_root / "output" / "changelog.yaml"
    if not changelog_path.is_file():
        raise RepairHintsUnavailable(f"changelog not found at {changelog_path}")

    changelog = module.load_changelog(str(changelog_path))
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
    corpus_root = resolve_proof_corpus_root()
    changelog_path = corpus_root / "output" / "changelog.yaml"
    if not changelog_path.is_file():
        raise RepairHintsUnavailable(f"changelog not found at {changelog_path}")

    changelog = module.load_changelog(str(changelog_path))
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


def _tokenize_for_repair_doc(text: str) -> set[str]:
    """Same tokenization rule retrieve_entries.py uses (exact-token,
    case-sensitive), reused directly rather than a second regex so
    changelog and repair_doc matching stay consistent."""
    module = _load_retrieve_entries_module()
    return module.tokenize_proof(text)


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
    """
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
        diffs = " ".join(str(d) for d in (doc.get("version_diffs_found") or []))
        doc_tokens = _tokenize_for_repair_doc(f"{summary}\n{diffs}")
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
) -> str:
    """Render changelog + repair_doc hits as a plain-text prompt section.

    integration/'s prompt is built from flat strings (see
    integration/agent/prompt.py::build_prompt), not a structured ToolView
    like shannon-prover's -- this replaces that project's
    build_repair_hints_tool_view/emit_repair_hints pair.
    """
    lines: list[str] = []
    if changelog_entries:
        lines.append("Known EasyCrypt changelog entries in range:")
        for entry in changelog_entries:
            version = str(entry.get("version") or "")
            title = str(entry.get("title") or "")
            hint = str(entry.get("repair_hint") or entry.get("summary") or "")
            lines.append(f"- [{version}] {title}: {hint}")
    if repair_doc_entries:
        if lines:
            lines.append("")
        lines.append("Known library reference notes:")
        for doc in repair_doc_entries:
            path = str(doc.get("path") or doc.get("_file") or "")
            summary = str(doc.get("current_content_summary") or "")
            lines.append(f"- {path}: {summary}")
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

    identifiers = _extract_identifiers(failing_tactic_text, ec_error_text)
    repair_doc_entries: list[dict[str, Any]] = []
    try:
        repair_doc_entries = get_repair_doc_snippets(identifiers=identifiers)
    except RepairHintsUnavailable as exc:
        notes.append(f"repair_doc hints unavailable: {exc}")

    text = format_repair_hints_for_prompt(changelog_entries, repair_doc_entries)
    return text, notes, matched_version

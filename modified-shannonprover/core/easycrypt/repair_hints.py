"""Repair-mode retrieval: changelog + repair_doc facts for a failed tactic.

Proof-repair mode replays an existing (outdated) proof against the current
EasyCrypt install until the first tactic fails (see
``workflow/proof_management/repair_intent.py``). At that point, instead of
(or alongside) the normal library-wide lemma search, this module surfaces
two static, sibling-repo sources of dated, sourced facts about what changed:

* ``proof_corpus/output/changelog.yaml`` -- a structured EasyCrypt release
  changelog, matched via ``proof_corpus/scripts/retrieve_entries.py``'s
  existing identifier-overlap scoring (loaded dynamically, not vendored).
* ``proof_corpus/repair_doc/*.json`` -- per-library reference notes, matched
  by simple token/path overlap (no existing script covers this half).

Results are packaged as a ``ToolView`` (``core/easycrypt/session_tool_view.py``)
and written the same way any other manager retrieval result is: as a
``tool.view.produced`` event whose artifact ``_iter_guidance_sources``
(``core/easycrypt/session_agent_view.py``) picks up automatically. This
module never talks to EasyCrypt and never touches the proof-state compiler
(``core/easycrypt/analysis/ec_proof_ir.py``) -- it only supplies additional
FACTUAL-tier evidence alongside it, per ``docs/design/compiler_view_boundary.md``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import types
from pathlib import Path
from typing import Any

from core.easycrypt.session_events import append_event
from core.easycrypt.session_tool_view import (
    Recommendation,
    SourceRef,
    make_tool_view,
    write_tool_view_artifact,
)


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
    # shannon-prover/core/easycrypt/repair_hints.py -> shannon-prover/ -> AI4EC/
    return Path(__file__).resolve().parents[2].parent / "proof_corpus"


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
        "shannon_prover._vendor.retrieve_entries", script_path,
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


def build_repair_hints_tool_view(
    *,
    failing_tactic_text: str,
    ec_error_text: str,
    source_ec_version: str,
    target_ec_version: str,
    top_n: int = 12,
) -> dict[str, Any]:
    """Assemble changelog + repair_doc hits into a ToolView dict.

    Never raises: on any RepairHintsUnavailable/version-mismatch condition
    this returns a valid, empty-evidence ToolView with ``ok=True`` and an
    explanatory note -- repair-hints are optional supplementary context,
    never a hard error even when proof_corpus can't be reached.
    """
    notes: list[str] = []
    kb_evidence: list[dict[str, Any]] = []
    retrieval_evidence: list[dict[str, Any]] = []
    recommendations: list[Recommendation] = []

    changelog_entries: list[dict[str, Any]] = []
    try:
        changelog_entries = get_changelog_repair_hints(
            failing_tactic_text=failing_tactic_text,
            ec_error_text=ec_error_text,
            source_ec_version=source_ec_version,
            target_ec_version=target_ec_version,
            top_n=top_n,
        )
    except RepairHintsUnavailable as exc:
        notes.append(f"changelog repair hints unavailable: {exc}")

    for entry in changelog_entries:
        entry_id = str(entry.get("id") or "")
        version = str(entry.get("version") or "")
        kb_evidence.append({
            "id": f"changelog.{version}.{entry_id}",
            "version": version,
            "kind": entry.get("kind"),
            "title": entry.get("title"),
            "matched_identifiers": entry.get("overlap") or [],
            "reason": entry.get("reason"),
        })
        recommendations.append(Recommendation(
            id=f"changelog_{version}_{entry_id}",
            kind="fact",
            producer="repair_hints.changelog",
            action=str(entry.get("repair_hint") or entry.get("summary") or entry.get("title") or ""),
            why=str(entry.get("reason") or "changelog entry in the replay's version range"),
            confidence="medium",
            source_refs=[SourceRef(
                kind="changelog",
                id=entry_id,
                title=str(entry.get("title") or ""),
                details={"version": version, "kind": str(entry.get("kind") or "")},
            )],
            metadata={
                "version": version,
                "matched_identifiers": entry.get("overlap") or [],
            },
        ))

    identifiers = _extract_identifiers(failing_tactic_text, ec_error_text)
    repair_doc_entries: list[dict[str, Any]] = []
    try:
        repair_doc_entries = get_repair_doc_snippets(identifiers=identifiers)
    except RepairHintsUnavailable as exc:
        notes.append(f"repair_doc hints unavailable: {exc}")

    for doc in repair_doc_entries:
        doc_file = str(doc.get("_file") or "")
        retrieval_evidence.append({
            "id": f"repair_doc.{doc_file}",
            "path": doc.get("path"),
            "matched_identifiers": doc.get("_matched_identifiers") or [],
            "summary": doc.get("current_content_summary"),
        })
        recommendations.append(Recommendation(
            id=f"repair_doc_{doc_file}",
            kind="fact",
            producer="repair_hints.repair_doc",
            action=str(doc.get("current_content_summary") or ""),
            why="repair_doc reference for a library the failing step touches",
            confidence="medium",
            source_refs=[SourceRef(
                kind="repair_doc",
                id=doc_file,
                path=str(doc.get("path") or ""),
                title=doc_file,
            )],
            metadata={"matched_identifiers": doc.get("_matched_identifiers") or []},
        ))

    return make_tool_view(
        tool="repair_hints",
        recommendations=recommendations,
        evidence={"kb": kb_evidence, "retrieval": retrieval_evidence},
        notes=notes,
        ok=True,
    ).to_dict()


def emit_repair_hints(
    session_dir: Path,
    *,
    failing_tactic_text: str,
    ec_error_text: str,
    source_ec_version: str,
    target_ec_version: str,
    source: str,
) -> dict[str, Any]:
    """Build, persist, and record a repair-hints ToolView in one call.

    Shared by the eager bootstrap-time emission
    (``workflow/proof_management/repair_intent.py``, ``source="repair_bootstrap"``)
    and the on-demand ``repair_hints`` context-topic intent
    (``workflow/proof_management/repl_session.py``, ``source="repair_hints_intent"``).
    """
    view = build_repair_hints_tool_view(
        failing_tactic_text=failing_tactic_text,
        ec_error_text=ec_error_text,
        source_ec_version=source_ec_version,
        target_ec_version=target_ec_version,
    )
    event_payload = write_tool_view_artifact(session_dir, view)
    append_event(session_dir, "tool.view.produced", event_payload, source=source)
    return event_payload

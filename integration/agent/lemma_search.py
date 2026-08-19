"""Lemma catalog search helpers (semantic + lexical modes).

Catalog keys are EasyCrypt-qualified paths (``Theory.basename``). An optional
``theory:Path`` token may appear in any search query; it filters the catalog
before the chosen mode runs.
"""

from __future__ import annotations

import re
from typing import Iterable

import numpy as np

from .embeddings import EmbeddingClient, rank_by_cosine
from .premises import lemma_ref_from_key

SEARCH_MODES = frozenset({"semantic", "substring", "prefix", "exact"})
DEFAULT_SEARCH_MODE = "semantic"

# ``theory:RField`` or ``theory:Ring.IntID`` — applies to any search mode.
_THEORY_FILTER_RE = re.compile(r"(?i)\btheory:([A-Za-z_][\w.]*)")


def normalize_search_mode(raw: str | None) -> str:
    mode = (raw or "").strip().lower()
    if not mode:
        return DEFAULT_SEARCH_MODE
    if mode in SEARCH_MODES:
        return mode
    # Common aliases
    if mode in {"name", "substr", "contains"}:
        return "substring"
    if mode in {"startswith", "start"}:
        return "prefix"
    return DEFAULT_SEARCH_MODE


def split_theory_filter(query: str) -> tuple[str | None, str]:
    """Extract ``theory:...`` tokens from *query*; return (filter, residual).

    The last ``theory:`` token wins when several appear. Residual text is the
    query with those tokens removed (whitespace-normalized).
    """
    matches = list(_THEORY_FILTER_RE.finditer(query))
    if not matches:
        return None, query.strip()
    theory = matches[-1].group(1)
    residual = _THEORY_FILTER_RE.sub(" ", query)
    residual = " ".join(residual.split())
    return theory, residual


def theory_matches(theory_path: str, filter_text: str) -> bool:
    """Whether a lemma's theory path matches an agent-supplied filter.

    Matches when the theory equals the filter, is under it as a prefix
    (``RField`` → ``RField.AddMonoid``), ends with it as a path segment
    (``IntID`` → ``Ring.IntID``), or shares a dotted path component.
    """
    theory = theory_path.strip().lower()
    filt = filter_text.strip().lower()
    if not filt:
        return True
    if not theory:
        return False
    if theory == filt or theory.startswith(filt + ".") or theory.endswith("." + filt):
        return True
    return filt in theory.split(".")


def filter_catalog_by_theory(
    catalog: dict[str, str], theory: str | None
) -> dict[str, str]:
    if not theory:
        return catalog
    return {
        key: sig
        for key, sig in catalog.items()
        if theory_matches(lemma_ref_from_key(key).theory, theory)
    }


def search_lemmas(
    catalog: dict[str, str],
    embedder: EmbeddingClient | None,
    search_index: dict[str, np.ndarray] | None,
    query: str,
    *,
    mode: str = DEFAULT_SEARCH_MODE,
    top_k: int,
) -> str:
    mode = normalize_search_mode(mode)
    theory, residual = split_theory_filter(query)
    scoped = filter_catalog_by_theory(catalog, theory)
    scope_note = f" [theory:{theory}]" if theory else ""

    if theory and not scoped:
        return (
            f"{mode} search{scope_note}: no lemmas under theory filter "
            f"`{theory}`"
        )

    if mode == "semantic":
        return _semantic_search(
            scoped,
            embedder,
            _scoped_index(search_index, scoped),
            residual,
            top_k=top_k,
            scope_note=scope_note,
        )
    if mode == "exact":
        return _exact_search(scoped, residual, scope_note=scope_note)
    if mode == "prefix":
        return _prefix_search(scoped, residual, top_k=top_k, scope_note=scope_note)
    return _substring_search(scoped, residual, top_k=top_k, scope_note=scope_note)


def _scoped_index(
    search_index: dict[str, np.ndarray] | None, scoped: dict[str, str]
) -> dict[str, np.ndarray] | None:
    if search_index is None:
        return None
    return {k: search_index[k] for k in scoped if k in search_index}


def _format_hits(
    title: str, hits: Iterable[tuple[str, str, str]], *, empty: str
) -> str:
    rows = list(hits)
    if not rows:
        return empty
    lines = [title]
    for name, detail, signature in rows:
        if detail:
            lines.append(f"- {name} ({detail}): {signature}")
        else:
            lines.append(f"- {name}: {signature}")
    return "\n".join(lines)


def _basename_keys(catalog: dict[str, str], query: str) -> list[str]:
    lower = query.lower()
    return [
        key
        for key in catalog
        if lemma_ref_from_key(key).name.lower() == lower
    ]


def _semantic_search(
    catalog: dict[str, str],
    embedder: EmbeddingClient | None,
    search_index: dict[str, np.ndarray] | None,
    query: str,
    *,
    top_k: int,
    scope_note: str = "",
) -> str:
    if not query:
        # Theory-only (or empty) semantic query: list scoped lemmas.
        keys = sorted(catalog)[:top_k]
        return _format_hits(
            f"Semantic search{scope_note} (theory listing, top {len(keys)}):",
            ((k, "", catalog[k]) for k in keys),
            empty=f"Semantic search{scope_note}: empty catalog",
        )
    if embedder is None or search_index is None:
        return (
            f"Semantic search `{query}`{scope_note}: embedding index unavailable; "
            "try mode `substring` or `exact` via the name field."
        )
    ranked = rank_by_cosine(search_index, embedder.embed(query), top_k)
    if not ranked:
        return f"Semantic search `{query}`{scope_note}: no results"
    lines = [f"Semantic search `{query}`{scope_note} (top {len(ranked)}):"]
    for name, score in ranked:
        signature = catalog.get(name, "(signature unavailable)")
        lines.append(f"- {name} (cosine={score:.4f}): {signature}")
    return "\n".join(lines)


def _exact_search(
    catalog: dict[str, str], query: str, *, scope_note: str = ""
) -> str:
    if not query:
        keys = sorted(catalog)[:50]
        return _format_hits(
            f"Exact name{scope_note} (theory listing, top {len(keys)}):",
            ((k, "", catalog[k]) for k in keys),
            empty=f"Exact name{scope_note}: empty catalog",
        )
    if query in catalog:
        return f"Exact name `{query}`{scope_note}:\n- {query}: {catalog[query]}"
    lower = query.lower()
    for name, signature in catalog.items():
        if name.lower() == lower:
            return (
                f"Exact name `{query}`{scope_note} (case-insensitive):\n"
                f"- {name}: {signature}"
            )
    basename_hits = _basename_keys(catalog, query)
    if len(basename_hits) == 1:
        key = basename_hits[0]
        return (
            f"Exact name `{query}`{scope_note} (unique basename):\n"
            f"- {key}: {catalog[key]}"
        )
    if basename_hits:
        lines = [
            f"Exact name `{query}`{scope_note}: "
            f"{len(basename_hits)} lemmas share this basename:"
        ]
        for key in sorted(basename_hits)[:20]:
            lines.append(f"- {key}: {catalog[key]}")
        return "\n".join(lines)
    return (
        f"Exact name `{query}`{scope_note}: not found. "
        "Try mode `substring` or `prefix`, or a semantic query."
    )


def _prefix_search(
    catalog: dict[str, str],
    query: str,
    *,
    top_k: int,
    scope_note: str = "",
) -> str:
    if not query:
        hits = [(name, "", signature) for name, signature in sorted(catalog.items())][
            :top_k
        ]
        return _format_hits(
            f"Prefix search{scope_note} (theory listing, top {len(hits)}):",
            hits,
            empty=f"Prefix search{scope_note}: empty catalog",
        )
    lower = query.lower()
    hits: list[tuple[str, str, str]] = []
    for name, signature in sorted(catalog.items()):
        basename = lemma_ref_from_key(name).name
        if name.lower().startswith(lower) or basename.lower().startswith(lower):
            hits.append((name, "prefix", signature))
        if len(hits) >= top_k:
            break
    return _format_hits(
        f"Prefix search `{query}`{scope_note} (top {len(hits)}):",
        hits,
        empty=f"Prefix search `{query}`{scope_note}: no matching lemma names",
    )


def _substring_search(
    catalog: dict[str, str],
    query: str,
    *,
    top_k: int,
    scope_note: str = "",
) -> str:
    if not query:
        hits = [(name, "", signature) for name, signature in sorted(catalog.items())][
            :top_k
        ]
        return _format_hits(
            f"Substring search{scope_note} (theory listing, top {len(hits)}):",
            hits,
            empty=f"Substring search{scope_note}: empty catalog",
        )
    lower = query.lower()
    name_hits: list[tuple[str, str, str]] = []
    sig_hits: list[tuple[str, str, str]] = []
    for name, signature in sorted(catalog.items()):
        basename = lemma_ref_from_key(name).name
        if lower in name.lower() or lower in basename.lower():
            name_hits.append((name, "name", signature))
        elif lower in signature.lower():
            sig_hits.append((name, "signature", signature))
    hits = (name_hits + sig_hits)[:top_k]
    return _format_hits(
        f"Substring search `{query}`{scope_note} "
        f"(top {len(hits)}; name matches first):",
        hits,
        empty=(
            f"Substring search `{query}`{scope_note}: no matches in lemma names "
            "or signatures. Try a shorter token or semantic mode."
        ),
    )

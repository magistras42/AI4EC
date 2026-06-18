"""Parse and cache EasyCrypt premises blocks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

THEORY_HEADER_RE = re.compile(r"^=+\s*.+\s*=+$")
PREMISE_RE = re.compile(r"^(lemma|axiom)\s+(\w+)[^:]*:\s*(.+)$")


def parse_premises(premises_text: str) -> dict[str, str]:
    premises: dict[str, str] = {}
    current_theory = ""

    for raw_line in premises_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if THEORY_HEADER_RE.match(line):
            current_theory = line.strip("= ").strip()
            continue
        match = PREMISE_RE.match(line)
        if not match:
            continue
        kind, name, _stmt = match.groups()
        if current_theory:
            text = f"[{current_theory}] {kind} {name}: {match.group(3)}"
        else:
            text = f"{kind} {name}: {match.group(3)}"
        premises[name] = text

    return premises


def cache_path(work_copy: Path, cursor_upto: int, embed_model: str) -> Path:
    return work_copy.with_name(f".{work_copy.stem}.premises_cache.json")


def load_cached_embeddings(
    work_copy: Path,
    cursor_upto: int,
    embed_model: str,
    declaration_count: int,
) -> dict[str, list[float]] | None:
    path = cache_path(work_copy, cursor_upto, embed_model)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    mtime = work_copy.stat().st_mtime
    if (
        payload.get("mtime") != mtime
        or payload.get("cursor_upto") != cursor_upto
        or payload.get("embed_model") != embed_model
        or payload.get("declaration_count") != declaration_count
    ):
        return None
    return payload.get("embeddings")


def save_cached_embeddings(
    work_copy: Path,
    cursor_upto: int,
    embed_model: str,
    declaration_count: int,
    premises: dict[str, str],
    embeddings: dict[str, list[float]],
) -> None:
    path = cache_path(work_copy, cursor_upto, embed_model)
    payload = {
        "mtime": work_copy.stat().st_mtime,
        "cursor_upto": cursor_upto,
        "embed_model": embed_model,
        "declaration_count": declaration_count,
        "premises_hash": _hash_premises(premises),
        "embeddings": embeddings,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _hash_premises(premises: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(premises):
        digest.update(name.encode())
        digest.update(premises[name].encode())
    return digest.hexdigest()

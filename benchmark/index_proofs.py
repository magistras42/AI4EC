"""Stage 3: build a single proof index from extracted .ec files."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchmark.config import BenchmarkPaths
from benchmark.ec_scanner import scan_proofs


@dataclass
class ProofIndexEntry:
    repo_url: str
    repo_slug: str
    split: str
    file: str
    line: int
    kind: str
    name: str
    signature: str


def _load_clone_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Clone manifest not found: {path}. Run `python -m benchmark clone` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_slug: dict[str, dict] = {}
    for record in payload.get("repos", []):
        slug = record.get("slug")
        if slug:
            by_slug[slug] = record
    return by_slug


def _iter_ec_files(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"Data directory not found: {data_dir}. Run `python -m benchmark extract` first."
        )
    return sorted(data_dir.rglob("*.ec"))


def run_index(paths: BenchmarkPaths) -> list[ProofIndexEntry]:
    repo_meta = _load_clone_manifest(paths.clone_manifest)
    entries: list[ProofIndexEntry] = []

    for ec_path in _iter_ec_files(paths.data_dir):
        rel_file = str(ec_path.relative_to(paths.data_dir))
        repo_slug = ec_path.relative_to(paths.data_dir).parts[0]
        meta = repo_meta.get(repo_slug, {})
        source = ec_path.read_text(encoding="utf-8", errors="replace")

        for decl in scan_proofs(source):
            entries.append(
                ProofIndexEntry(
                    repo_url=meta.get("url", ""),
                    repo_slug=repo_slug,
                    split=meta.get("split", ""),
                    file=rel_file,
                    line=decl.line,
                    kind=decl.kind,
                    name=decl.name,
                    signature=decl.signature,
                )
            )

    entries.sort(key=lambda e: (e.repo_slug, e.file, e.line, e.name))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(entries),
        "proofs": [asdict(e) for e in entries],
    }
    paths.proofs_index.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[ok] indexed {len(entries)} proofs -> {paths.proofs_index}")
    return entries

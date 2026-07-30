"""Stage 2: extract .ec files into data/<repo-slug>/ preserving relative paths."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchmark.config import BenchmarkPaths


@dataclass
class ExtractedFile:
    repo_slug: str
    source: str
    dest: str
    bytes: int
    sha256: str


def _load_clone_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Clone manifest not found: {path}. Run `python -m benchmark clone` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("repos", [])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_ec_files(repo_root: Path) -> list[Path]:
    """Every .ec file under a clone root, sorted, with .git pruned."""
    files: list[Path] = []
    for path in repo_root.rglob("*.ec"):
        if ".git" in path.parts:
            continue
        files.append(path)
    return sorted(files)


def _copy_ec(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target = source.resolve()
        shutil.copy2(target, dest)
    else:
        shutil.copy2(source, dest)


def run_extract(paths: BenchmarkPaths, *, force: bool = False) -> list[ExtractedFile]:
    del force  # reserved for future selective sync behavior
    repo_root = paths.clone_dir.parent
    clone_records = _load_clone_manifest(paths.clone_manifest)
    paths.data_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[ExtractedFile] = []
    for record in clone_records:
        if record.get("status") not in {"ok", "present"}:
            continue
        slug = record["slug"]
        clone_path = repo_root / record["path"]
        if not clone_path.is_dir():
            print(f"[error] missing clone directory for {slug}: {clone_path}")
            continue

        repo_count = 0
        for ec_path in iter_ec_files(clone_path):
            rel = ec_path.relative_to(clone_path)
            dest = paths.data_dir / slug / rel
            _copy_ec(ec_path, dest)
            rel_dest = str(dest.relative_to(paths.data_dir))
            extracted.append(
                ExtractedFile(
                    repo_slug=slug,
                    source=str(ec_path.relative_to(clone_path)),
                    dest=rel_dest,
                    bytes=dest.stat().st_size,
                    sha256=_sha256(dest),
                )
            )
            repo_count += 1

        print(f"[ok] {slug}: {repo_count} .ec files")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": [asdict(f) for f in extracted],
    }
    paths.extract_manifest.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return extracted

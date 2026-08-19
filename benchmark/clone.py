"""Stage 1: shallow-clone repositories from repositories.md."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchmark.config import BenchmarkPaths
from benchmark.repos import RepoEntry, SkippedEntry, parse_repositories


@dataclass
class CloneRecord:
    url: str
    slug: str
    split: str
    status: str
    path: str | None
    commit: str | None
    error: str | None


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def _head_commit(repo_path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _clone_repo(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def run_clone(
    paths: BenchmarkPaths,
    *,
    force_refresh: bool = False,
    limit: int | None = None,
    only: set[str] | None = None,
) -> list[CloneRecord]:
    entries, skipped = parse_repositories(paths.repos_file)
    if only is not None:
        entries = [e for e in entries if e.slug in only]
    if limit is not None:
        entries = entries[:limit]

    paths.clone_dir.mkdir(parents=True, exist_ok=True)
    records: list[CloneRecord] = []

    for entry in entries:
        dest = paths.clone_dir / entry.slug
        record = _clone_one(entry, dest, force_refresh=force_refresh)
        records.append(record)
        _print_clone_status(entry.slug, record)

    for skip in skipped:
        records.append(
            CloneRecord(
                url=skip.url,
                slug="",
                split=skip.split,
                status="skipped",
                path=None,
                commit=None,
                error=skip.reason,
            )
        )
        print(f"[skipped] {skip.url}: {skip.reason}")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repos": [asdict(r) for r in records],
    }
    paths.clone_manifest.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def _print_clone_status(slug: str, record: CloneRecord) -> None:
    if record.status == "present":
        print(f"[info] {slug}: already present, skipping clone")
        return
    print(f"[{record.status}] {slug}")


def _clone_one(entry: RepoEntry, dest: Path, *, force_refresh: bool) -> CloneRecord:
    rel_path = str(dest.relative_to(dest.parent.parent))

    if dest.exists() and _is_git_repo(dest):
        if not force_refresh:
            commit = _head_commit(dest)
            return CloneRecord(
                url=entry.url,
                slug=entry.slug,
                split=entry.split,
                status="present",
                path=rel_path,
                commit=commit,
                error=None,
            )
        shutil.rmtree(dest)

    if dest.exists():
        shutil.rmtree(dest)

    try:
        _clone_repo(entry.url, dest)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return CloneRecord(
            url=entry.url,
            slug=entry.slug,
            split=entry.split,
            status="error",
            path=None,
            commit=None,
            error=stderr or str(exc),
        )

    commit = _head_commit(dest)
    return CloneRecord(
        url=entry.url,
        slug=entry.slug,
        split=entry.split,
        status="ok",
        path=rel_path,
        commit=commit,
        error=None,
    )


def clone_exit_code(records: list[CloneRecord]) -> int:
    cloneable = [r for r in records if r.status not in {"skipped"}]
    if not cloneable:
        return 1
    if all(r.status == "error" for r in cloneable):
        return 1
    return 0

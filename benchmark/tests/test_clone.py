"""Tests for clone stage behavior."""

from __future__ import annotations

from pathlib import Path

from benchmark.clone import run_clone
from benchmark.config import BenchmarkPaths


def test_skip_existing_repo_without_force_refresh(tmp_path: Path):
    repos_file = tmp_path / "repos.md"
    repos_file.write_text(
        "## Training\nhttps://github.com/octocat/Hello-World\n",
        encoding="utf-8",
    )
    clone_dir = tmp_path / "clone"
    slug_dir = clone_dir / "octocat-Hello-World"
    slug_dir.mkdir(parents=True)
    (slug_dir / ".git").mkdir()

    paths = BenchmarkPaths(
        repos_file=repos_file,
        clone_dir=clone_dir,
        data_dir=tmp_path / "data",
    )
    records = run_clone(paths, force_refresh=False)

    assert len(records) == 1
    assert records[0].status == "present"
    assert records[0].path is not None

"""Tests for build-status annotation of the proof index."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.config import BenchmarkPaths
from benchmark.index_proofs import run_index

SOURCE_OK = "lemma good_one : true.\nproof. trivial. qed.\n"
SOURCE_BROKEN = "lemma broken_one : false.\nproof. trivial. qed.\n"


def _fixture_tree(tmp_path: Path, *, with_build_report: bool) -> BenchmarkPaths:
    clone_dir = tmp_path / ".clone"
    data_dir = tmp_path / "data"
    clone_dir.mkdir(parents=True)

    (clone_dir / "clone_manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-30T00:00:00+00:00",
                "repos": [
                    {
                        "url": "https://github.com/owner/repo",
                        "slug": "owner-repo",
                        "split": "training",
                        "status": "ok",
                        "path": ".clone/owner-repo",
                        "commit": "abc123",
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    (data_dir / "owner-repo" / "proof").mkdir(parents=True)
    (data_dir / "owner-repo" / "Good.ec").write_text(SOURCE_OK, encoding="utf-8")
    (data_dir / "owner-repo" / "proof" / "Broken.ec").write_text(SOURCE_BROKEN, encoding="utf-8")

    if with_build_report:
        (data_dir / "build_report.json").write_text(
            json.dumps(
                {
                    "generated_at": "2026-07-30T00:00:00+00:00",
                    "toolchain": {"binary": "/x/easycrypt", "git_hash": "r-test"},
                    "settings": {},
                    "repos": [],
                    "files": [
                        {
                            "repo_slug": "owner-repo",
                            "file": "Good.ec",
                            "status": "ok",
                            "returncode": 0,
                            "duration_s": 1.0,
                            "admit_count": 2,
                            "diagnostics": [],
                            "note": "",
                        },
                        {
                            "repo_slug": "owner-repo",
                            "file": "proof/Broken.ec",
                            "status": "error",
                            "returncode": 1,
                            "duration_s": 1.0,
                            "admit_count": 0,
                            "diagnostics": [
                                {
                                    "severity": "warning",
                                    "file": "proof/Broken.ec",
                                    "line": 1,
                                    "span": "0-5",
                                    "message": "noise",
                                },
                                {
                                    "severity": "critical",
                                    "file": "proof/Broken.ec",
                                    "line": 2,
                                    "span": "0-20",
                                    "message": "cannot prove goal (strict)",
                                },
                            ],
                            "note": "",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    return BenchmarkPaths(
        repos_file=tmp_path / "repositories.md",
        clone_dir=clone_dir,
        data_dir=data_dir,
        overrides_file=tmp_path / "overrides.json",
    )


def test_entries_carry_build_status_and_first_hard_error(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, with_build_report=True)

    entries = {e.name: e for e in run_index(paths)}

    assert entries["good_one"].build_status == "ok"
    assert entries["good_one"].build_error == ""
    assert entries["good_one"].admit_count == 2

    assert entries["broken_one"].build_status == "error"
    # The warning is skipped in favor of the first critical/error diagnostic.
    assert entries["broken_one"].build_error == "cannot prove goal (strict)"


def test_only_building_filters_out_failing_files(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, with_build_report=True)

    names = {e.name for e in run_index(paths, only_building=True)}

    assert names == {"good_one"}


def test_missing_build_report_yields_unknown_status(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, with_build_report=False)

    entries = run_index(paths)

    assert len(entries) == 2
    assert {e.build_status for e in entries} == {"unknown"}
    assert all(e.build_error == "" and e.admit_count == 0 for e in entries)


def test_only_building_without_report_selects_nothing(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, with_build_report=False)

    assert run_index(paths, only_building=True) == []


def test_index_json_includes_new_fields(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, with_build_report=True)
    run_index(paths)

    payload = json.loads(paths.proofs_index.read_text(encoding="utf-8"))
    entry = next(p for p in payload["proofs"] if p["name"] == "broken_one")

    assert entry["file"] == "owner-repo/proof/Broken.ec"
    assert entry["build_status"] == "error"
    assert entry["build_error"] == "cannot prove goal (strict)"


def test_malformed_build_report_does_not_break_indexing(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, with_build_report=True)
    paths.build_report.write_text("{broken", encoding="utf-8")

    entries = run_index(paths)

    assert len(entries) == 2
    assert {e.build_status for e in entries} == {"unknown"}

"""Tests for the build stage: -script parsing, status classification, reports."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.build import (
    Diagnostic,
    count_admits,
    load_previous_results,
    parse_script_output,
    render_summary,
    run_build,
)
from benchmark.config import BenchmarkPaths
from benchmark.toolchain import Toolchain

SCRIPT_OK = """P 2 28 0.41791 -1.00 -1.00
P 3 67 1.00000 -1.00 -1.00
"""

SCRIPT_ERROR = """P 2 28 0.25926 -1.00 -1.00
E critical bad.ec: line 3 (0-39) cannot prove goal (strict)
P 4 108 1.00000 -1.00 -1.00
"""

# A stub EasyCrypt: succeeds on good*.ec, fails with a diagnostic on bad*.ec,
# fails with no diagnostic on crash*.ec, and hangs on slow*.ec.
STUB_EASYCRYPT = r"""#!/bin/sh
if [ "$1" = "config" ]; then
  echo "git-hash: r-test"
  echo "known provers: Z3@4.13.3"
  exit 0
fi
target=""
for arg in "$@"; do target="$arg"; done
case "$target" in
  *slow*) sleep 30; exit 0 ;;
  *crash*) echo "Fatal error: out of memory" >&2; exit 2 ;;
  *weird*) echo "E something entirely new"; exit 1 ;;
  *bad*) printf '%s' "$SCRIPT_ERROR_OUTPUT"; exit 1 ;;
  *) printf '%s' "$SCRIPT_OK_OUTPUT"; exit 0 ;;
esac
"""


def _toolchain(tmp_path: Path) -> Toolchain:
    stub = tmp_path / "bin" / "easycrypt"
    stub.parent.mkdir(parents=True, exist_ok=True)
    body = STUB_EASYCRYPT.replace("$SCRIPT_ERROR_OUTPUT", SCRIPT_ERROR).replace(
        "$SCRIPT_OK_OUTPUT", SCRIPT_OK
    )
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return Toolchain(binary=stub, git_hash="r-test", provers=["Z3@4.13.3"], load_path=[])


def _fixture_tree(tmp_path: Path, files: dict[str, str]) -> BenchmarkPaths:
    """Lay out .clone/<slug>/ plus a clone manifest, mirroring stage 1's output."""
    clone_dir = tmp_path / ".clone"
    repo = clone_dir / "owner-repo"
    for rel, source in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    manifest = {
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
    clone_dir.mkdir(parents=True, exist_ok=True)
    (clone_dir / "clone_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    return BenchmarkPaths(
        repos_file=tmp_path / "repositories.md",
        clone_dir=clone_dir,
        data_dir=tmp_path / "data",
        overrides_file=tmp_path / "overrides.json",
    )


def test_parse_script_output_extracts_diagnostic_fields() -> None:
    diagnostics = parse_script_output(SCRIPT_ERROR)

    assert diagnostics == [
        Diagnostic(
            severity="critical",
            file="bad.ec",
            line=3,
            span="0-39",
            message="cannot prove goal (strict)",
        )
    ]


def test_parse_script_output_ignores_progress_lines() -> None:
    assert parse_script_output(SCRIPT_OK) == []


def test_parse_script_output_keeps_unrecognized_diagnostics() -> None:
    diagnostics = parse_script_output("E something entirely new\n")

    assert len(diagnostics) == 1
    assert diagnostics[0].severity == "unknown"
    assert diagnostics[0].message == "something entirely new"
    assert diagnostics[0].line is None


def test_count_admits_ignores_comments() -> None:
    source = "lemma a : true.\nproof. admit. qed.\n(* admit admitted *)\nlemma b. admitted.\n"

    assert count_admits(source) == 2


def test_run_build_classifies_every_outcome(tmp_path: Path) -> None:
    paths = _fixture_tree(
        tmp_path,
        {
            "good.ec": "lemma a : true.\n",
            "bad.ec": "lemma b : false.\n",
            "crash.ec": "lemma c : true.\n",
            "weird.ec": "lemma d : true.\n",
            "slow.ec": "lemma e : true.\n",
        },
    )

    run_build(
        paths,
        toolchain=_toolchain(tmp_path),
        jobs=4,
        file_timeout=2,
    )

    report = json.loads(paths.build_report.read_text(encoding="utf-8"))
    status = {f["file"]: f["status"] for f in report["files"]}

    assert status == {
        "good.ec": "ok",
        "bad.ec": "error",
        "crash.ec": "crashed",
        "weird.ec": "error",
        "slow.ec": "timeout",
    }


def test_run_build_report_shape_and_repo_summary(tmp_path: Path) -> None:
    paths = _fixture_tree(
        tmp_path,
        {"good.ec": "lemma a : true.\n", "bad.ec": "lemma b : false.\nproof. admit. qed.\n"},
    )

    summaries = run_build(paths, toolchain=_toolchain(tmp_path), jobs=2, file_timeout=10)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.slug == "owner-repo"
    assert summary.url == "https://github.com/owner/repo"
    assert summary.split == "training"
    assert (summary.files_ok, summary.files_error, summary.files_total) == (1, 1, 2)
    assert summary.status == "partial"
    assert summary.admit_total == 1
    assert summary.compile_rate == 0.5

    report = json.loads(paths.build_report.read_text(encoding="utf-8"))
    assert report["toolchain"]["git_hash"] == "r-test"
    assert report["settings"]["eco_cache"] is True
    assert report["settings"]["file_timeout"] == 10
    bad = next(f for f in report["files"] if f["file"] == "bad.ec")
    assert bad["diagnostics"][0]["message"] == "cannot prove goal (strict)"
    assert bad["admit_count"] == 1

    assert paths.build_summary.exists()
    assert "cannot prove goal" in paths.build_summary.read_text(encoding="utf-8")


def test_run_build_marks_fully_green_repo_as_built(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, {"good.ec": "lemma a : true.\n"})

    summaries = run_build(paths, toolchain=_toolchain(tmp_path), jobs=1, file_timeout=10)

    assert summaries[0].status == "built"


def test_run_build_honors_skip_override(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, {"good.ec": "lemma a : true.\n"})
    paths.overrides_file.write_text(
        json.dumps({"owner-repo": {"skip": "not interesting"}}), encoding="utf-8"
    )

    summaries = run_build(paths, toolchain=_toolchain(tmp_path), jobs=1)

    assert summaries[0].status == "skipped"
    assert summaries[0].skip_reason == "not interesting"
    assert json.loads(paths.build_report.read_text(encoding="utf-8"))["files"] == []


def test_rerun_reuses_previous_ok_results(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, {"good.ec": "lemma a : true.\n", "bad.ec": "lemma b.\n"})
    toolchain = _toolchain(tmp_path)

    run_build(paths, toolchain=toolchain, jobs=2, file_timeout=10)

    # Break the stub: anything actually re-run now fails to launch.
    toolchain.binary.unlink()

    summaries = run_build(paths, toolchain=toolchain, jobs=2, file_timeout=10)

    assert summaries[0].files_ok == 1  # good.ec came from the cached report
    report = json.loads(paths.build_report.read_text(encoding="utf-8"))
    bad = next(f for f in report["files"] if f["file"] == "bad.ec")
    assert bad["status"] == "crashed"  # bad.ec was retried and could not launch


def test_refresh_recompiles_previously_ok_files(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, {"good.ec": "lemma a : true.\n"})
    toolchain = _toolchain(tmp_path)
    run_build(paths, toolchain=toolchain, jobs=1, file_timeout=10)

    toolchain.binary.unlink()
    summaries = run_build(paths, toolchain=toolchain, jobs=1, file_timeout=10, refresh=True)

    assert summaries[0].files_ok == 0


def test_only_filters_repos(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, {"good.ec": "lemma a : true.\n"})

    summaries = run_build(paths, toolchain=_toolchain(tmp_path), only={"other-repo"})

    assert summaries == []


def test_load_previous_results_tolerates_malformed_report(tmp_path: Path) -> None:
    path = tmp_path / "build_report.json"
    path.write_text("{broken", encoding="utf-8")

    assert load_previous_results(path) == {}


def test_render_summary_ranks_by_compile_rate(tmp_path: Path) -> None:
    paths = _fixture_tree(tmp_path, {"good.ec": "lemma a : true.\n"})
    toolchain = _toolchain(tmp_path)
    summaries = run_build(paths, toolchain=toolchain, jobs=1, file_timeout=10)

    text = render_summary(toolchain, summaries, [], {"smt_timeout": 20, "jobs": 1})

    assert "# EasyCrypt Build Report" in text
    assert "`owner-repo`" in text
    assert "r-test" in text

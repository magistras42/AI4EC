"""Stage 4: compile every .ec file in every cloned repo with EasyCrypt.

Builds run against the *clone* tree (`.clone/<slug>/`), not the extracted `.ec`
copies under `data/`, because a repo needs its full contents to compile: its own
`easycrypt.project`, submodules, and any non-.ec files it references.

Diagnostics come from `easycrypt compile -script`, whose output is two line
kinds::

    P <line> <offset> <fraction> <mem> <frag>          progress, ignored
    E <severity> <file>: line <N> (<span>) <message>   diagnostic

Note that a file whose lemmas are all `admit`ed compiles with returncode 0 and
no diagnostic, so `admit_count` is recorded alongside the status -- a green
build is not by itself evidence of a real proof.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from benchmark.build_config import (
    DEFAULT_FILE_TIMEOUT,
    DEFAULT_SMT_TIMEOUT,
    RepoBuildConfig,
    derive_config,
    load_overrides,
)
from benchmark.config import BenchmarkPaths
from benchmark.ec_scanner import strip_comments
from benchmark.toolchain import Toolchain

SCRIPT_DIAG_RE = re.compile(r"^E (\S+) (.*?): line (\d+) \(([^)]*)\) (.*)$")
ADMIT_RE = re.compile(r"\b(admit|admitted)\b")
HARD_SEVERITIES = ("critical", "error")
BUDGET_EXHAUSTED = "repo budget exhausted"


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    file: str
    line: int | None
    span: str
    message: str


@dataclass
class FileBuildResult:
    repo_slug: str
    file: str
    status: str
    returncode: int | None
    duration_s: float
    admit_count: int
    diagnostics: list[Diagnostic] = field(default_factory=list)
    note: str = ""

    @property
    def first_error(self) -> str:
        for diag in self.diagnostics:
            if diag.severity in HARD_SEVERITIES:
                return diag.message
        return self.diagnostics[0].message if self.diagnostics else ""


@dataclass
class RepoBuildResult:
    slug: str
    url: str
    split: str
    commit: str | None
    status: str
    files_total: int
    files_ok: int
    files_error: int
    files_timeout: int
    files_skipped: int
    admit_total: int
    duration_s: float
    has_project_file: bool
    skip_reason: str | None

    @property
    def compile_rate(self) -> float:
        attempted = self.files_ok + self.files_error + self.files_timeout
        return self.files_ok / attempted if attempted else 0.0


def default_jobs() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


def parse_script_output(text: str) -> list[Diagnostic]:
    """Extract diagnostics from `easycrypt compile -script` output."""
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("E "):
            continue
        match = SCRIPT_DIAG_RE.match(line)
        if match:
            severity, file_part, line_no, span, message = match.groups()
            diagnostics.append(
                Diagnostic(
                    severity=severity,
                    file=file_part,
                    line=int(line_no),
                    span=span,
                    message=message.strip(),
                )
            )
        else:
            # Keep unrecognized diagnostics rather than dropping them silently,
            # so a change in EasyCrypt's output degrades instead of hiding errors.
            diagnostics.append(
                Diagnostic(
                    severity="unknown",
                    file="",
                    line=None,
                    span="",
                    message=line[2:].strip(),
                )
            )
    return diagnostics


def count_admits(source: str) -> int:
    return len(ADMIT_RE.findall(strip_comments(source)))


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def compile_file(
    toolchain: Toolchain,
    config: RepoBuildConfig,
    ec_file: Path,
    *,
    no_cache: bool,
) -> FileBuildResult:
    """Compile one repo-relative .ec file and classify the outcome."""
    admit_count = count_admits(_read_source(config.root / ec_file))
    cmd = [str(toolchain.binary), *config.compile_args(ec_file, no_cache=no_cache)]
    started = time.monotonic()

    try:
        result = subprocess.run(
            cmd,
            cwd=config.root,
            capture_output=True,
            text=True,
            timeout=config.file_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return FileBuildResult(
            repo_slug=config.slug,
            file=ec_file.as_posix(),
            status="timeout",
            returncode=None,
            duration_s=round(time.monotonic() - started, 3),
            admit_count=admit_count,
            note=f"exceeded {config.file_timeout}s",
        )
    except OSError as exc:
        return FileBuildResult(
            repo_slug=config.slug,
            file=ec_file.as_posix(),
            status="crashed",
            returncode=None,
            duration_s=round(time.monotonic() - started, 3),
            admit_count=admit_count,
            note=str(exc),
        )

    duration = round(time.monotonic() - started, 3)
    diagnostics = parse_script_output((result.stdout or "") + "\n" + (result.stderr or ""))

    if result.returncode == 0:
        status = "ok"
    elif diagnostics:
        status = "error"
    else:
        status = "crashed"

    note = ""
    if status == "crashed":
        note = (result.stderr or result.stdout or "").strip()[:400]

    return FileBuildResult(
        repo_slug=config.slug,
        file=ec_file.as_posix(),
        status=status,
        returncode=result.returncode,
        duration_s=duration,
        admit_count=admit_count,
        diagnostics=diagnostics,
        note=note,
    )


def _load_clone_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Clone manifest not found: {path}. Run `python -m benchmark clone` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("repos", [])


def load_previous_results(path: Path) -> dict[tuple[str, str], dict]:
    """Index a previous build report by (repo_slug, file) for incremental runs."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    previous: dict[tuple[str, str], dict] = {}
    for record in payload.get("files", []):
        slug = record.get("repo_slug")
        rel = record.get("file")
        if slug and rel:
            previous[(slug, rel)] = record
    return previous


def _result_from_record(record: dict) -> FileBuildResult:
    diagnostics = [Diagnostic(**d) for d in record.get("diagnostics", [])]
    return FileBuildResult(
        repo_slug=record["repo_slug"],
        file=record["file"],
        status=record["status"],
        returncode=record.get("returncode"),
        duration_s=record.get("duration_s", 0.0),
        admit_count=record.get("admit_count", 0),
        diagnostics=diagnostics,
        note=record.get("note", ""),
    )


def _build_repo(
    toolchain: Toolchain,
    config: RepoBuildConfig,
    record: dict,
    *,
    jobs: int,
    no_cache: bool,
    repo_timeout: int | None,
    previous: dict[tuple[str, str], dict],
) -> tuple[RepoBuildResult, list[FileBuildResult]]:
    started = time.monotonic()

    if config.skip:
        summary = RepoBuildResult(
            slug=config.slug,
            url=record.get("url", ""),
            split=record.get("split", ""),
            commit=record.get("commit"),
            status="skipped",
            files_total=0,
            files_ok=0,
            files_error=0,
            files_timeout=0,
            files_skipped=0,
            admit_total=0,
            duration_s=0.0,
            has_project_file=config.has_project_file,
            skip_reason=config.skip,
        )
        return summary, []

    pending: list[Path] = []
    results: list[FileBuildResult] = []
    for rel in config.ec_files:
        cached = previous.get((config.slug, rel.as_posix()))
        if cached is not None and cached.get("status") == "ok":
            results.append(_result_from_record(cached))
        else:
            pending.append(rel)

    deadline = started + repo_timeout if repo_timeout else None
    over_budget: list[Path] = []

    def _work(rel: Path) -> FileBuildResult:
        return compile_file(toolchain, config, rel, no_cache=no_cache)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {}
        for rel in pending:
            if deadline is not None and time.monotonic() > deadline:
                over_budget.append(rel)
                continue
            futures[pool.submit(_work, rel)] = rel
        for future in futures:
            results.append(future.result())

    for rel in over_budget:
        results.append(
            FileBuildResult(
                repo_slug=config.slug,
                file=rel.as_posix(),
                status="skipped",
                returncode=None,
                duration_s=0.0,
                admit_count=0,
                note=BUDGET_EXHAUSTED,
            )
        )

    results.sort(key=lambda r: r.file)
    counts = {"ok": 0, "error": 0, "timeout": 0, "crashed": 0, "skipped": 0}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1

    files_error = counts["error"] + counts["crashed"]
    if counts["ok"] and not (files_error or counts["timeout"]):
        status = "built"
    elif counts["ok"]:
        status = "partial"
    else:
        status = "failed"

    summary = RepoBuildResult(
        slug=config.slug,
        url=record.get("url", ""),
        split=record.get("split", ""),
        commit=record.get("commit"),
        status=status,
        files_total=len(results),
        files_ok=counts["ok"],
        files_error=files_error,
        files_timeout=counts["timeout"],
        files_skipped=counts["skipped"],
        admit_total=sum(r.admit_count for r in results),
        duration_s=round(time.monotonic() - started, 3),
        has_project_file=config.has_project_file,
        skip_reason=None,
    )
    return summary, results


def run_build(
    paths: BenchmarkPaths,
    *,
    toolchain: Toolchain,
    jobs: int | None = None,
    only: set[str] | None = None,
    limit: int | None = None,
    file_timeout: int = DEFAULT_FILE_TIMEOUT,
    smt_timeout: int = DEFAULT_SMT_TIMEOUT,
    repo_timeout: int | None = None,
    no_cache: bool = False,
    refresh: bool = False,
) -> list[RepoBuildResult]:
    """Compile every cloned repo and write the build report."""
    jobs = jobs or default_jobs()
    clone_root = paths.clone_dir.parent
    records = _load_clone_manifest(paths.clone_manifest)
    overrides = load_overrides(paths.overrides_file)
    previous = {} if refresh else load_previous_results(paths.build_report)

    buildable = [r for r in records if r.get("status") in {"ok", "present"}]
    if only is not None:
        buildable = [r for r in buildable if r.get("slug") in only]
    if limit is not None:
        buildable = buildable[:limit]

    paths.data_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[RepoBuildResult] = []
    all_files: list[FileBuildResult] = []

    for record in buildable:
        slug = record["slug"]
        root = clone_root / record["path"]
        if not root.is_dir():
            print(f"[error] missing clone directory for {slug}: {root}")
            continue

        config = derive_config(
            root,
            slug,
            overrides,
            file_timeout=file_timeout,
            smt_timeout=smt_timeout,
        )
        summary, files = _build_repo(
            toolchain,
            config,
            record,
            jobs=jobs,
            no_cache=no_cache,
            repo_timeout=repo_timeout,
            previous=previous,
        )
        summaries.append(summary)
        all_files.extend(files)
        _print_repo_status(summary)

    _write_report(
        paths,
        toolchain=toolchain,
        summaries=summaries,
        files=all_files,
        settings={
            "file_timeout": file_timeout,
            "smt_timeout": smt_timeout,
            "repo_timeout": repo_timeout,
            "jobs": jobs,
            "eco_cache": not no_cache,
        },
    )
    return summaries


def _print_repo_status(summary: RepoBuildResult) -> None:
    if summary.status == "skipped":
        print(f"[skipped] {summary.slug}: {summary.skip_reason}")
        return
    admits = f", {summary.admit_total} admits" if summary.admit_total else ""
    print(
        f"[{summary.status}] {summary.slug}: "
        f"{summary.files_ok}/{summary.files_total} files compile "
        f"({summary.compile_rate:.0%}{admits}, {summary.duration_s:.1f}s)"
    )


def _write_report(
    paths: BenchmarkPaths,
    *,
    toolchain: Toolchain,
    summaries: list[RepoBuildResult],
    files: list[FileBuildResult],
    settings: dict,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "toolchain": toolchain.as_dict(),
        "settings": settings,
        "repos": [asdict(s) for s in summaries],
        "files": [asdict(f) for f in files],
    }
    paths.build_report.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.build_summary.write_text(
        render_summary(toolchain, summaries, files, settings),
        encoding="utf-8",
    )
    total_ok = sum(s.files_ok for s in summaries)
    total_files = sum(s.files_total for s in summaries)
    print(
        f"[ok] built {len(summaries)} repos, {total_ok}/{total_files} files compile "
        f"-> {paths.build_report}"
    )


def _dominant_errors(files: list[FileBuildResult]) -> dict[str, str]:
    """Most frequent first-error message per repo, for the summary table."""
    tally: dict[str, Counter] = defaultdict(Counter)
    for result in files:
        if result.status in {"error", "crashed", "timeout"}:
            message = result.first_error or result.note or result.status
            tally[result.repo_slug][message.replace("|", "\\|")[:90]] += 1
    return {
        slug: f"{msg} ({count}×)"
        for slug, counter in tally.items()
        for msg, count in [counter.most_common(1)[0]]
    }


def render_summary(
    toolchain: Toolchain,
    summaries: list[RepoBuildResult],
    files: list[FileBuildResult],
    settings: dict,
) -> str:
    """Human-readable build report, repos ranked by compile rate."""
    dominant = _dominant_errors(files)
    lines = [
        "# EasyCrypt Build Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"EasyCrypt: `{toolchain.git_hash or 'unknown'}` (`{toolchain.binary}`)",
        f"Provers: {', '.join(toolchain.provers) or 'none'}",
        f"Settings: SMT timeout {settings.get('smt_timeout')}s, "
        f"file timeout {settings.get('file_timeout')}s, jobs {settings.get('jobs')}",
        "",
        "A file whose lemmas are all `admit`ed compiles cleanly, so the *admits*",
        "column matters as much as the compile rate.",
        "",
        "| Repo | Split | Status | Files OK | Rate | Admits | Dominant error |",
        "|------|-------|--------|----------|------|--------|----------------|",
    ]

    ranked = sorted(
        summaries,
        key=lambda s: (s.status == "skipped", -s.compile_rate, s.slug),
    )
    for summary in ranked:
        if summary.status == "skipped":
            lines.append(
                f"| `{summary.slug}` | {summary.split} | skipped | – | – | – | "
                f"{summary.skip_reason} |"
            )
            continue
        lines.append(
            f"| `{summary.slug}` | {summary.split} | {summary.status} | "
            f"{summary.files_ok}/{summary.files_total} | "
            f"{summary.compile_rate:.0%} | {summary.admit_total} | "
            f"{dominant.get(summary.slug, '')} |"
        )

    lines.append("")
    return "\n".join(lines) + "\n"

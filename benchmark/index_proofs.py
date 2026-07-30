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
    build_status: str = "unknown"
    build_error: str = ""
    admit_count: int = 0


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


def _load_build_results(path: Path) -> dict[tuple[str, str], dict]:
    """Index `build_report.json` by (repo_slug, repo-relative file).

    Build results are keyed on the path within the clone, which is the same
    relative path the extract stage mirrors under `data/<slug>/`. A missing
    report is not an error -- the build stage is optional.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[warn] ignoring malformed build report: {path}")
        return {}
    results: dict[tuple[str, str], dict] = {}
    for record in payload.get("files", []):
        slug = record.get("repo_slug")
        rel = record.get("file")
        if slug and rel:
            results[(slug, rel)] = record
    return results


def _first_error(record: dict) -> str:
    diagnostics = record.get("diagnostics", [])
    for diag in diagnostics:
        if diag.get("severity") in {"critical", "error"}:
            return diag.get("message", "")
    if diagnostics:
        return diagnostics[0].get("message", "")
    return record.get("note", "")


def run_index(paths: BenchmarkPaths, *, only_building: bool = False) -> list[ProofIndexEntry]:
    repo_meta = _load_clone_manifest(paths.clone_manifest)
    build_results = _load_build_results(paths.build_report)
    entries: list[ProofIndexEntry] = []

    for ec_path in _iter_ec_files(paths.data_dir):
        rel_path = ec_path.relative_to(paths.data_dir)
        rel_file = str(rel_path)
        repo_slug = rel_path.parts[0]
        rel_in_repo = Path(*rel_path.parts[1:]).as_posix()
        meta = repo_meta.get(repo_slug, {})
        build = build_results.get((repo_slug, rel_in_repo))
        build_status = build.get("status", "unknown") if build else "unknown"

        if only_building and build_status != "ok":
            continue

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
                    build_status=build_status,
                    build_error=_first_error(build) if build else "",
                    admit_count=build.get("admit_count", 0) if build else 0,
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
    if build_results:
        ok = sum(1 for e in entries if e.build_status == "ok")
        print(f"[ok] indexed {len(entries)} proofs ({ok} in files that compile) -> {paths.proofs_index}")
    else:
        print(
            f"[ok] indexed {len(entries)} proofs -> {paths.proofs_index} "
            "(no build report; run `python -m benchmark build` to annotate)"
        )
    return entries

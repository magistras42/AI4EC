"""Default paths for the benchmark extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOS_FILE = REPO_ROOT / "repositories.md"
DEFAULT_CLONE_DIR = REPO_ROOT / ".clone"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_OVERRIDES_FILE = Path(__file__).resolve().parent / "overrides.json"
CLONE_MANIFEST_NAME = "clone_manifest.json"
EXTRACT_MANIFEST_NAME = "extract_manifest.json"
PROOFS_INDEX_NAME = "proofs_index.json"
BUILD_REPORT_NAME = "build_report.json"
BUILD_SUMMARY_NAME = "build_report.md"


@dataclass(frozen=True)
class BenchmarkPaths:
    repos_file: Path
    clone_dir: Path
    data_dir: Path
    overrides_file: Path = DEFAULT_OVERRIDES_FILE

    @property
    def clone_manifest(self) -> Path:
        return self.clone_dir / CLONE_MANIFEST_NAME

    @property
    def extract_manifest(self) -> Path:
        return self.data_dir / EXTRACT_MANIFEST_NAME

    @property
    def proofs_index(self) -> Path:
        return self.data_dir / PROOFS_INDEX_NAME

    @property
    def build_report(self) -> Path:
        return self.data_dir / BUILD_REPORT_NAME

    @property
    def build_summary(self) -> Path:
        return self.data_dir / BUILD_SUMMARY_NAME


def default_paths() -> BenchmarkPaths:
    return BenchmarkPaths(
        repos_file=DEFAULT_REPOS_FILE,
        clone_dir=DEFAULT_CLONE_DIR,
        data_dir=DEFAULT_DATA_DIR,
        overrides_file=DEFAULT_OVERRIDES_FILE,
    )

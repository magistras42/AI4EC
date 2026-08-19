"""Parse repository URLs from repositories.md."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#]+)\s*$"
)
GITHUB_BARE_RE = re.compile(r"^https://github\.com/(?P<owner>[^/\s]+)\s*$")
SPLIT_HEADER_RE = re.compile(r"^##\s+(Training|Evaluation)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class RepoEntry:
    url: str
    owner: str
    repo: str
    slug: str
    split: str

    @property
    def is_valid(self) -> bool:
        return bool(self.repo)


@dataclass(frozen=True)
class SkippedEntry:
    url: str
    reason: str
    split: str


def slug_from_owner_repo(owner: str, repo: str) -> str:
    return f"{owner}-{repo}"


def parse_repositories(path: Path) -> tuple[list[RepoEntry], list[SkippedEntry]]:
    """Return valid repo entries and skipped invalid URLs."""
    text = path.read_text(encoding="utf-8")
    current_split = "training"
    entries: list[RepoEntry] = []
    skipped: list[SkippedEntry] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        split_match = SPLIT_HEADER_RE.match(line)
        if split_match:
            current_split = split_match.group(1).lower()
            continue

        if line.startswith("#"):
            continue

        if not line.startswith("https://github.com/"):
            continue

        repo_match = GITHUB_REPO_RE.match(line)
        if repo_match:
            owner = repo_match.group("owner")
            repo = repo_match.group("repo")
            entries.append(
                RepoEntry(
                    url=line,
                    owner=owner,
                    repo=repo,
                    slug=slug_from_owner_repo(owner, repo),
                    split=current_split,
                )
            )
            continue

        bare_match = GITHUB_BARE_RE.match(line)
        if bare_match:
            skipped.append(
                SkippedEntry(
                    url=line,
                    reason="not a repository URL (missing repo name)",
                    split=current_split,
                )
            )
            continue

        skipped.append(
            SkippedEntry(
                url=line,
                reason="unrecognized GitHub URL format",
                split=current_split,
            )
        )

    return entries, skipped

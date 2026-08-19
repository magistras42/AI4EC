"""Detect the EasyCrypt version endpoints a repair should be scoped to.

Roadmap item W6 in [`docs/PROOF_REPAIR_HANDOFF.md`](../../docs/PROOF_REPAIR_HANDOFF.md).
Before this module, both endpoints were hardcoded in ``specs.py`` as
``r2022.04`` -> ``r2026.07`` with a comment admitting it was "a broad
illustrative default". That is not merely imprecise: it silently makes
``releases_in_range`` span the entire catalog, so every Tier-A
``mechanism_change`` entry in the changelog is eligible for every failure,
and the import-repair manifest considers rules whose version window the file
never actually crossed.

Two very different questions are answered here, and only one of them has a
reliable answer:

**Target** -- which EasyCrypt is installed? Reliable. The binary is asked
directly (``ec.exe --version``) and its answer is mapped onto the nearest
release tag the changelog knows about.

**Source** -- which EasyCrypt was the proof written against? Fundamentally a
guess. ``.ec`` files carry no version pragma, so the evidence is external:
a pinned submodule commit, a git authoring date, a ``easycrypt.project``
file. Every heuristic here reports HOW it decided (``DetectedVersion.method``
and ``.confidence``) rather than returning a bare string, because a caller
that cannot tell a parsed ``--version`` from a date-bracket guess will
present both to the model with the same authority.

When detection fails the result is ``None`` and the caller keeps its existing
fail-open behaviour (consider every release / every rule), which is the same
convention ``releases_in_range`` and ``select_by_version`` already use. A
wrong narrow guess is worse than an honest wide one.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# Release tags look like r2025.02. Matches EasyCrypt's own tag convention,
# which has been rYYYY.MM since the oldest tag (see the changelog pipeline
# notes in docs/PROOF_REPAIR_HANDOFF.md W5b).
VERSION_TAG_RE = re.compile(r"\br(\d{4})\.(\d{2})\b")

# Fallback when the binary cannot be interrogated. Deliberately a constant
# rather than "today": the harness should not claim a release exists just
# because the calendar advanced past it.
FALLBACK_TARGET_VERSION = "r2026.07"


@dataclass(frozen=True)
class DetectedVersion:
    """A version endpoint plus the evidence behind it."""

    version: str | None
    method: str
    confidence: str  # high | medium | low | none
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "method": self.method,
            "confidence": self.confidence,
            "detail": self.detail,
        }


def _tag_sort_key(tag: str) -> tuple[int, int]:
    match = VERSION_TAG_RE.search(tag)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def normalize_version_tag(text: str) -> str | None:
    """Extract an ``rYYYY.MM`` tag from arbitrary text, if one is present."""
    match = VERSION_TAG_RE.search(text or "")
    if not match:
        return None
    return f"r{match.group(1)}.{match.group(2)}"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> str:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{proc.stdout}\n{proc.stderr}"


def detect_target_version(
    easycrypt_bin: Path,
    known_versions: list[str] | None = None,
) -> DetectedVersion:
    """Which EasyCrypt release the installed binary corresponds to.

    The patched fork's ``ec.exe`` has **no ``--version`` flag** (its commands
    are compile/llm/cli/config/runtest/why3config/docgen), so the binary
    cannot be asked directly. The source tree it was built from can be: the
    binary lives at ``<tree>/_build/default/src/ec.exe``, and
    ``git describe --tags`` in ``<tree>`` yields e.g. ``r2026.06-6-g07e77d8c``
    -- the release the build descends from, plus its distance past it.

    The answer is then snapped onto the newest CATALOGED release that is not
    newer than it. The fork carries tags the changelog has no entries for, and
    claiming a version the knowledge base cannot reason about would scope
    ``releases_in_range`` to an empty window.
    """
    easycrypt_bin = Path(easycrypt_bin)
    if not easycrypt_bin.exists():
        return DetectedVersion(
            version=None,
            method="binary_missing",
            confidence="none",
            detail=f"{easycrypt_bin} does not exist",
        )

    tag, method, detail = _describe_easycrypt_tree(easycrypt_bin)
    if tag is None:
        return DetectedVersion(
            version=None,
            method=method,
            confidence="none",
            detail=detail,
        )

    if known_versions:
        catalog = sorted(known_versions, key=_tag_sort_key)
        not_newer = [v for v in catalog if _tag_sort_key(v) <= _tag_sort_key(tag)]
        if not_newer:
            snapped = not_newer[-1]
            return DetectedVersion(
                version=snapped,
                method=method,
                confidence="high",
                detail=(
                    detail
                    + (
                        f"; snapped to nearest cataloged release {snapped}"
                        if snapped != tag
                        else ""
                    )
                ),
            )
        # The build predates every cataloged release: report honestly rather
        # than snapping upward to a release it does not contain.
        return DetectedVersion(
            version=None,
            method=method,
            confidence="none",
            detail=f"{detail}; older than every cataloged release",
        )
    return DetectedVersion(
        version=tag, method=method, confidence="high", detail=detail
    )


def _describe_easycrypt_tree(easycrypt_bin: Path) -> tuple[str | None, str, str]:
    """``git describe --tags`` the source tree a built ec.exe came from.

    Returns ``(tag, method, detail)``. Walks up from the binary rather than
    assuming a fixed depth, so a differently-laid-out build still resolves as
    long as some ancestor is the EasyCrypt git tree.
    """
    for parent in easycrypt_bin.resolve().parents:
        if not (parent / ".git").exists():
            continue
        described = _run(["git", "describe", "--tags"], cwd=parent).strip()
        tag = normalize_version_tag(described)
        if tag:
            return (
                tag,
                "git_describe",
                f"{parent.name} describes as {described.splitlines()[0].strip()}",
            )
        # A git tree with no usable tag: fall back to its commit date below.
        return (None, "git_describe_untagged", f"git describe said {described[:120]!r}")
    return (None, "no_source_tree", f"no git tree above {easycrypt_bin}")


def detect_source_version(
    corpus_path: Path,
    known_versions: list[str] | None = None,
) -> DetectedVersion:
    """Best guess at the EasyCrypt a corpus file was written against.

    Tried in descending order of trustworthiness:

    1. An explicit ``rYYYY.MM`` in a project/config file next to the sources.
    2. The repo's own git authoring date, bracketed against release dates.

    Returning ``None`` is a valid, common outcome -- notably for the ElGamal
    corpus, which is from 2020 and therefore predates EasyCrypt's oldest tag
    entirely. That "predates the catalog" answer is itself useful and is
    reported as such rather than being rounded up to the oldest release.
    """
    corpus_path = Path(corpus_path)
    root = corpus_path if corpus_path.is_dir() else corpus_path.parent

    explicit = _explicit_version_marker(root)
    if explicit is not None:
        return explicit

    authored = _git_authoring_date(root)
    if authored is None:
        return DetectedVersion(
            version=None,
            method="undetected",
            confidence="none",
            detail="no version marker and no git history",
        )

    if known_versions:
        oldest = sorted(known_versions, key=_tag_sort_key)[0]
        oldest_key = _tag_sort_key(oldest)
        authored_key = (authored.year, authored.month)
        if authored_key < oldest_key:
            return DetectedVersion(
                version=None,
                method="predates_catalog",
                confidence="medium",
                detail=(
                    f"corpus authored {authored.isoformat()}, before the oldest "
                    f"cataloged release {oldest}; every release is in range"
                ),
            )
        candidates = [
            v for v in sorted(known_versions, key=_tag_sort_key)
            if _tag_sort_key(v) <= authored_key
        ]
        if candidates:
            return DetectedVersion(
                version=candidates[-1],
                method="git_commit_date",
                confidence="low",
                detail=(
                    f"corpus authored {authored.isoformat()}; nearest release "
                    f"not after that date is {candidates[-1]}. This is a date "
                    "bracket, not a recorded version"
                ),
            )
    return DetectedVersion(
        version=None,
        method="git_commit_date_unmatched",
        confidence="none",
        detail=f"corpus authored {authored.isoformat()}; no release matched",
    )


def _explicit_version_marker(root: Path) -> DetectedVersion | None:
    """An rYYYY.MM written down in the corpus itself."""
    for name in ("easycrypt.project", ".easycrypt", "easycrypt.version", "README.md"):
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tag = normalize_version_tag(text)
        if tag:
            return DetectedVersion(
                version=tag,
                method="project_file",
                confidence="medium" if name == "README.md" else "high",
                detail=f"{name} names {tag}",
            )
    return None


def _git_authoring_date(root: Path) -> date | None:
    """Date of the most recent commit touching this corpus, if it is a repo."""
    out = _run(["git", "log", "-1", "--format=%ad", "--date=short", "."], cwd=root)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", out)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def resolve_version_window(
    *,
    corpus_path: Path,
    easycrypt_bin: Path,
    source_override: str | None = None,
    target_override: str | None = None,
    known_versions: list[str] | None = None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """Resolve both endpoints, preferring explicit overrides over detection.

    Returns ``(source, target, provenance)``. ``provenance`` is JSON-ready and
    belongs in the trial artifacts: it is what lets a later reader tell a
    parsed binary version from a date-bracket guess.
    """
    if source_override:
        source = DetectedVersion(
            version=source_override,
            method="explicit",
            confidence="high",
            detail="supplied by caller",
        )
    else:
        source = detect_source_version(corpus_path, known_versions)

    if target_override:
        target = DetectedVersion(
            version=target_override,
            method="explicit",
            confidence="high",
            detail="supplied by caller",
        )
    else:
        target = detect_target_version(easycrypt_bin, known_versions)
        if target.version is None:
            target = DetectedVersion(
                version=FALLBACK_TARGET_VERSION,
                method="fallback",
                confidence="low",
                detail=(
                    f"{target.method}: {target.detail}; "
                    f"falling back to {FALLBACK_TARGET_VERSION}"
                ),
            )

    return (
        source.version,
        target.version,
        {"source": source.as_dict(), "target": target.as_dict()},
    )

"""EasyCrypt version endpoint detection (roadmap W6).

The property that matters most here is HONESTY, not coverage: a wrong narrow
guess scopes `releases_in_range` to the wrong window and is worse than an
admitted `None`, which every consumer already treats as "consider everything".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integration.agent.ec_version import (
    FALLBACK_TARGET_VERSION,
    DetectedVersion,
    detect_target_version,
    normalize_version_tag,
    resolve_version_window,
)

KNOWN = [
    "r2022.04", "r2023.09", "r2024.01", "r2024.09",
    "r2025.02", "r2025.11", "r2026.05", "r2026.06", "r2026.07",
]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("r2026.06-6-g07e77d8c", "r2026.06"),
        ("EasyCrypt r2025.02", "r2025.02"),
        ("pinned at r2022.04\n", "r2022.04"),
        ("no version here", None),
        ("", None),
    ],
)
def test_normalize_version_tag(text, expected):
    assert normalize_version_tag(text) == expected


def test_missing_binary_is_reported_not_guessed():
    result = detect_target_version(Path("/nonexistent/ec.exe"), KNOWN)
    assert result.version is None
    assert result.method == "binary_missing"
    assert result.confidence == "none"


def test_explicit_overrides_win_over_detection(tmp_path):
    source, target, provenance = resolve_version_window(
        corpus_path=tmp_path,
        easycrypt_bin=Path("/nonexistent/ec.exe"),
        source_override="r2022.04",
        target_override="r2025.02",
        known_versions=KNOWN,
    )
    assert (source, target) == ("r2022.04", "r2025.02")
    assert provenance["source"]["method"] == "explicit"
    assert provenance["target"]["method"] == "explicit"
    assert provenance["source"]["confidence"] == "high"


def test_undetectable_target_falls_back_but_says_so(tmp_path):
    _, target, provenance = resolve_version_window(
        corpus_path=tmp_path,
        easycrypt_bin=Path("/nonexistent/ec.exe"),
        known_versions=KNOWN,
    )
    assert target == FALLBACK_TARGET_VERSION
    assert provenance["target"]["method"] == "fallback"
    # A fallback must never masquerade as a detected value.
    assert provenance["target"]["confidence"] == "low"


def test_undetectable_source_stays_none_rather_than_guessing(tmp_path):
    """None means 'consider every release' -- the existing fail-open rule."""
    source, _, provenance = resolve_version_window(
        corpus_path=tmp_path,
        easycrypt_bin=Path("/nonexistent/ec.exe"),
        known_versions=KNOWN,
    )
    assert source is None
    assert provenance["source"]["confidence"] == "none"


def test_project_file_marker_is_detected(tmp_path):
    (tmp_path / "easycrypt.project").write_text("requires r2025.02\n", encoding="utf-8")
    source, _, provenance = resolve_version_window(
        corpus_path=tmp_path,
        easycrypt_bin=Path("/nonexistent/ec.exe"),
        known_versions=KNOWN,
    )
    assert source == "r2025.02"
    assert provenance["source"]["method"] == "project_file"
    assert provenance["source"]["confidence"] == "high"


def test_readme_marker_is_lower_confidence_than_a_project_file(tmp_path):
    (tmp_path / "README.md").write_text("Built against r2024.09.", encoding="utf-8")
    _, _, provenance = resolve_version_window(
        corpus_path=tmp_path,
        easycrypt_bin=Path("/nonexistent/ec.exe"),
        known_versions=KNOWN,
    )
    assert provenance["source"]["version"] == "r2024.09"
    assert provenance["source"]["confidence"] == "medium"


def test_detection_snaps_onto_cataloged_releases_only():
    """The fork carries tags the changelog has no entries for."""
    described = DetectedVersion(
        version="r2026.06", method="git_describe", confidence="high"
    )
    assert described.version in KNOWN
    # A target the catalog does not know would leave releases_in_range empty.
    catalog_without_recent = ["r2022.04", "r2025.02"]
    assert "r2026.06" not in catalog_without_recent


def test_provenance_is_json_ready():
    payload = DetectedVersion(
        version="r2026.06", method="git_describe", confidence="high", detail="x"
    ).as_dict()
    assert set(payload) == {"version", "method", "confidence", "detail"}


@pytest.mark.integration
def test_detects_the_real_installed_easycrypt():
    """Against the vendored fork actually built in this tree."""
    from integration.agent.config import DEFAULT_EASYCRYPT_BIN

    if not DEFAULT_EASYCRYPT_BIN.exists():
        pytest.skip("EasyCrypt binary not built")
    result = detect_target_version(DEFAULT_EASYCRYPT_BIN, KNOWN)
    assert result.version is not None
    assert result.method == "git_describe"
    assert result.confidence == "high"
    assert result.version in KNOWN

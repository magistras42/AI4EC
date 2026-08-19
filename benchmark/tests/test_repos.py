"""Tests for repositories.md parsing."""

from __future__ import annotations

from pathlib import Path

from benchmark.repos import parse_repositories


def test_parse_repositories_md():
    repos_file = Path(__file__).resolve().parents[2] / "repositories.md"
    entries, skipped = parse_repositories(repos_file)

    assert len(entries) > 50
    assert any(e.slug == "EasyCrypt-easycrypt" for e in entries)
    assert any(e.split == "evaluation" for e in entries)
    assert any(s.url == "https://github.com/formosa-crypto" for s in skipped)

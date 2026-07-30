"""Tests for per-repo build recipe derivation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.build_config import (
    OverridesError,
    derive_config,
    load_overrides,
)


def _make_repo(root: Path) -> Path:
    """A small repo: two top-level .ec files, a proof/ subdir, and .git noise."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Main.ec").write_text("lemma a : true.\n", encoding="utf-8")
    (root / "Util.ec").write_text("lemma b : true.\n", encoding="utf-8")
    (root / "proof").mkdir()
    (root / "proof" / "Deep.ec").write_text("lemma c : true.\n", encoding="utf-8")
    (root / "proof" / "old").mkdir()
    (root / "proof" / "old" / "Stale.ec").write_text("lemma d : true.\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "Hook.ec").write_text("noise\n", encoding="utf-8")
    return root


def test_derive_config_collects_ec_files_and_include_dirs(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")

    config = derive_config(root, "owner-repo")

    assert [f.as_posix() for f in config.ec_files] == [
        "Main.ec",
        "Util.ec",
        "proof/Deep.ec",
        "proof/old/Stale.ec",
    ]
    assert [d.as_posix() for d in config.include_dirs] == [".", "proof", "proof/old"]
    assert config.skip is None
    assert config.has_project_file is False


def test_derive_config_detects_project_file(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    (root / "easycrypt.project").write_text("[general]\nidirs = proof\n", encoding="utf-8")

    assert derive_config(root, "owner-repo").has_project_file is True


def test_exclude_globs_drop_files_and_their_include_dirs(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    overrides = {"owner-repo": {"exclude": ["proof/old/**"]}}

    config = derive_config(root, "owner-repo", overrides)

    assert "proof/old/Stale.ec" not in [f.as_posix() for f in config.ec_files]
    assert "proof/old" not in [d.as_posix() for d in config.include_dirs]


def test_exclude_matches_bare_filename_pattern(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    overrides = {"owner-repo": {"exclude": ["**/Stale.ec", "Util.ec"]}}

    files = [f.as_posix() for f in derive_config(root, "owner-repo", overrides).ec_files]

    assert files == ["Main.ec", "proof/Deep.ec"]


def test_skip_short_circuits_file_enumeration(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    overrides = {"owner-repo": {"skip": "upstream EasyCrypt"}}

    config = derive_config(root, "owner-repo", overrides)

    assert config.skip == "upstream EasyCrypt"
    assert config.ec_files == []
    assert config.include_dirs == []


def test_overrides_replace_timeouts_and_add_include_dirs(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    overrides = {
        "owner-repo": {
            "file_timeout": 600,
            "smt_timeout": 45,
            "include_dirs": ["vendor/theories"],
        }
    }

    config = derive_config(root, "owner-repo", overrides, file_timeout=300, smt_timeout=20)

    assert config.file_timeout == 600
    assert config.smt_timeout == 45
    assert Path("vendor/theories") in config.include_dirs


def test_defaults_apply_when_slug_has_no_override(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")

    config = derive_config(root, "owner-repo", {"other-repo": {"file_timeout": 9}},
                           file_timeout=111, smt_timeout=22)

    assert (config.file_timeout, config.smt_timeout) == (111, 22)


def test_compile_args_shape(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    config = derive_config(root, "owner-repo", smt_timeout=17)

    args = config.compile_args(Path("proof/Deep.ec"), no_cache=False)
    assert args[:2] == ["compile", "-script"]
    assert "-no-eco" not in args
    assert args[args.index("-timeout") + 1] == "17"
    assert args[-1] == "proof/Deep.ec"
    assert "-I" in args and "proof" in args

    assert "-no-eco" in config.compile_args(Path("Main.ec"), no_cache=True)


def test_load_overrides_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_overrides(tmp_path / "absent.json") == {}


def test_load_overrides_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"owner-repo": {"timeuot": 30}}), encoding="utf-8")

    with pytest.raises(OverridesError, match="unknown keys"):
        load_overrides(path)


def test_load_overrides_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(OverridesError, match="invalid JSON"):
        load_overrides(path)


def test_shipped_overrides_file_is_valid() -> None:
    shipped = Path(__file__).resolve().parents[1] / "overrides.json"
    assert "EasyCrypt-easycrypt" in load_overrides(shipped)

"""Tests for EasyCrypt binary resolution and probing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmark.toolchain import ToolchainError, parse_config_output, resolve_toolchain

CONFIG_OUTPUT = """git-hash: r2026.05-16-g76bf9e9
load-path:
  <system>@/opt/ec/theories
  <system>@/opt/ec/theories/prelude
why3 configuration file
  /home/u/.config/easycrypt/why3.conf
EasyCrypt configuration file
  <none>
known provers: Alt-Ergo@2.6.0, CVC5@1.2.0, Z3@4.13.3
Commands PATH: /opt/ec/commands
"""


def _stub_binary(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "easycrypt"
    stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def test_parse_config_output() -> None:
    git_hash, provers, load_path = parse_config_output(CONFIG_OUTPUT)
    assert git_hash == "r2026.05-16-g76bf9e9"
    assert provers == ["Alt-Ergo@2.6.0", "CVC5@1.2.0", "Z3@4.13.3"]
    assert load_path == ["<system>@/opt/ec/theories", "<system>@/opt/ec/theories/prelude"]


def test_parse_config_output_ignores_other_indented_sections() -> None:
    _, _, load_path = parse_config_output(CONFIG_OUTPUT)
    assert not any("why3.conf" in entry for entry in load_path)


def test_resolve_toolchain_probes_explicit_binary(tmp_path: Path) -> None:
    # The real `easycrypt config` reports on stderr, so the stub does too.
    payload = CONFIG_OUTPUT.replace("\n", "\\n")
    stub = _stub_binary(tmp_path, f'printf "{payload}" >&2')

    toolchain = resolve_toolchain(stub)

    assert toolchain.binary == stub
    assert toolchain.git_hash == "r2026.05-16-g76bf9e9"
    assert "Z3@4.13.3" in toolchain.provers
    assert toolchain.as_dict()["git_hash"] == "r2026.05-16-g76bf9e9"


def test_resolve_toolchain_missing_binary_is_actionable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("EASYCRYPT", raising=False)

    with pytest.raises(ToolchainError) as excinfo:
        resolve_toolchain(tmp_path / "nope")

    message = str(excinfo.value)
    assert "No usable EasyCrypt binary" in message
    assert "--easycrypt" in message


def test_resolve_toolchain_rejects_nonzero_exit(tmp_path: Path) -> None:
    stub = _stub_binary(tmp_path, 'echo "boom" >&2; exit 3')

    with pytest.raises(ToolchainError, match="exited 3"):
        resolve_toolchain(stub)


def test_resolve_toolchain_rejects_installation_without_provers(tmp_path: Path) -> None:
    stub = _stub_binary(tmp_path, 'echo "git-hash: rX"; echo "known provers: "')

    with pytest.raises(ToolchainError, match="why3config"):
        resolve_toolchain(stub)


def test_resolve_toolchain_prefers_env_over_path(tmp_path: Path, monkeypatch) -> None:
    payload = CONFIG_OUTPUT.replace("\n", "\\n")
    env_bin = _stub_binary(tmp_path / "env", f'printf "{payload}" >&2')
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _stub_binary(path_dir, "exit 1")

    monkeypatch.setenv("EASYCRYPT", str(env_bin))
    monkeypatch.setenv("PATH", f"{path_dir}{os.pathsep}{os.environ['PATH']}")

    assert resolve_toolchain().binary == env_bin

"""Locate and probe the EasyCrypt binary used by the build stage.

Stages 1-3 need nothing but Python and git. Stage 4 needs a working EasyCrypt
installation, so it resolves the binary up front, records its identity in the
build report, and fails with an actionable message rather than producing a
report full of misleading failures.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from benchmark.config import REPO_ROOT

FALLBACK_EASYCRYPT_BIN = (
    REPO_ROOT / "integration" / "extern" / "easycrypt" / "_build" / "default" / "src" / "ec.exe"
)
PROBE_TIMEOUT = 60


class ToolchainError(RuntimeError):
    """The EasyCrypt binary is missing or does not answer `easycrypt config`."""


@dataclass(frozen=True)
class Toolchain:
    binary: Path
    git_hash: str
    provers: list[str]
    load_path: list[str]

    def as_dict(self) -> dict:
        return {
            "binary": str(self.binary),
            "git_hash": self.git_hash,
            "provers": list(self.provers),
            "load_path": list(self.load_path),
        }


def _candidate_binaries(explicit: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env_path = os.environ.get("EASYCRYPT")
    if env_path:
        candidates.append(Path(env_path))
    which = shutil.which("easycrypt")
    if which:
        candidates.append(Path(which))
    candidates.append(FALLBACK_EASYCRYPT_BIN)
    return candidates


def _find_binary(explicit: Path | None) -> Path:
    tried: list[str] = []
    for candidate in _candidate_binaries(explicit):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        tried.append(str(candidate))
    raise ToolchainError(
        "No usable EasyCrypt binary found. Tried: "
        + ", ".join(tried)
        + ".\nInstall EasyCrypt and put it on PATH (e.g. `opam switch set easycrypt_env`), "
        "set $EASYCRYPT, or pass --easycrypt PATH."
    )


def parse_config_output(text: str) -> tuple[str, list[str], list[str]]:
    """Parse `easycrypt config` into (git_hash, provers, load_path).

    The output is sectioned by unindented headers with indented bodies::

        git-hash: r2026.05-16-g76bf9e9
        load-path:
          <system>@/.../theories
        known provers: Alt-Ergo@2.6.0, Z3@4.13.3
    """
    git_hash = ""
    provers: list[str] = []
    load_path: list[str] = []
    in_load_path = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        indented = raw[:1].isspace()

        if not indented:
            in_load_path = False
            if stripped.startswith("git-hash:"):
                git_hash = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("known provers:"):
                body = stripped.split(":", 1)[1]
                provers = [p.strip() for p in body.split(",") if p.strip()]
            elif stripped.startswith("load-path:"):
                in_load_path = True
        elif in_load_path:
            load_path.append(stripped)

    return git_hash, provers, load_path


def resolve_toolchain(explicit: Path | None = None) -> Toolchain:
    """Find an EasyCrypt binary and probe it with `easycrypt config`."""
    binary = _find_binary(explicit)
    try:
        result = subprocess.run(
            [str(binary), "config"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolchainError(f"`{binary} config` timed out after {PROBE_TIMEOUT}s") from exc
    except OSError as exc:
        raise ToolchainError(f"Could not run `{binary} config`: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ToolchainError(
            f"`{binary} config` exited {result.returncode}: {detail[:400]}\n"
            "The EasyCrypt installation looks broken; try `easycrypt why3config`."
        )

    # `easycrypt config` prints its report on stderr, not stdout.
    git_hash, provers, load_path = parse_config_output(
        (result.stdout or "") + "\n" + (result.stderr or "")
    )
    if not provers:
        raise ToolchainError(
            f"`{binary} config` reports no known provers. "
            "Run `easycrypt why3config` after installing Alt-Ergo, CVC5, or Z3."
        )
    return Toolchain(
        binary=binary,
        git_hash=git_hash,
        provers=provers,
        load_path=load_path,
    )

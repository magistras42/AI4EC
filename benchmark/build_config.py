"""Per-repository build recipe for stage 4.

Every repo is compiled the same way -- `easycrypt compile` on each .ec file --
so results stay comparable. The only per-repo variation is which directories go
on the include path, which files to leave out, and how long to wait. Those are
derived from the clone tree and can be overridden per slug in `overrides.json`.

EasyCrypt already adds the input file's own directory to its load path and
discovers an `easycrypt.project` by walking upward from that directory, so a
repo carrying its own `[general] idirs=` needs no help from us. We record
whether it has one, but never override it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from benchmark.extract import iter_ec_files

PROJECT_FILE_NAME = "easycrypt.project"
DEFAULT_FILE_TIMEOUT = 300
DEFAULT_SMT_TIMEOUT = 20

OVERRIDE_KEYS = frozenset(
    {"skip", "exclude", "include_dirs", "file_timeout", "smt_timeout"}
)


class OverridesError(ValueError):
    """`overrides.json` is malformed."""


@dataclass(frozen=True)
class RepoBuildConfig:
    slug: str
    root: Path
    include_dirs: list[Path]
    ec_files: list[Path]
    exclude: list[str]
    file_timeout: int
    smt_timeout: int
    has_project_file: bool
    skip: str | None

    def compile_args(self, ec_file: Path, *, no_cache: bool) -> list[str]:
        """Argv tail for `<easycrypt> compile ...` for one repo-relative file."""
        args = ["compile", "-script"]
        if no_cache:
            args.append("-no-eco")
        args += ["-timeout", str(self.smt_timeout)]
        for include in self.include_dirs:
            args += ["-I", str(include)]
        args.append(str(ec_file))
        return args


def load_overrides(path: Path) -> dict[str, dict]:
    """Read the per-slug overrides file. A missing file means no overrides."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverridesError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OverridesError(f"{path}: expected a JSON object keyed by repo slug")

    for slug, entry in payload.items():
        if not isinstance(entry, dict):
            raise OverridesError(f"{path}: override for {slug!r} must be an object")
        unknown = set(entry) - OVERRIDE_KEYS
        if unknown:
            raise OverridesError(
                f"{path}: override for {slug!r} has unknown keys: "
                + ", ".join(sorted(unknown))
                + f" (recognized: {', '.join(sorted(OVERRIDE_KEYS))})"
            )
    return payload


def _glob_match(posix: str, pattern: str) -> bool:
    if fnmatch(posix, pattern):
        return True
    # `**/` should also match zero directories, which fnmatch does not do.
    if "**/" in pattern:
        return fnmatch(posix, pattern.replace("**/", "", 1))
    return False


def _matches_any(rel: Path, patterns: list[str]) -> bool:
    posix = rel.as_posix()
    return any(_glob_match(posix, pattern) for pattern in patterns)


def _derive_include_dirs(ec_files: list[Path]) -> list[Path]:
    """Repo root plus every directory holding at least one .ec file."""
    dirs = {Path(".")}
    for rel in ec_files:
        parent = rel.parent
        if parent != Path("."):
            dirs.add(parent)
    return sorted(dirs, key=lambda p: p.as_posix())


def derive_config(
    root: Path,
    slug: str,
    overrides: dict[str, dict] | None = None,
    *,
    file_timeout: int = DEFAULT_FILE_TIMEOUT,
    smt_timeout: int = DEFAULT_SMT_TIMEOUT,
) -> RepoBuildConfig:
    """Build the compile recipe for one cloned repository."""
    override = (overrides or {}).get(slug, {})
    skip = override.get("skip")
    exclude = list(override.get("exclude", []))

    if skip:
        return RepoBuildConfig(
            slug=slug,
            root=root,
            include_dirs=[],
            ec_files=[],
            exclude=exclude,
            file_timeout=int(override.get("file_timeout", file_timeout)),
            smt_timeout=int(override.get("smt_timeout", smt_timeout)),
            has_project_file=(root / PROJECT_FILE_NAME).is_file(),
            skip=skip,
        )

    all_files = [p.relative_to(root) for p in iter_ec_files(root)]
    ec_files = [f for f in all_files if not _matches_any(f, exclude)]
    include_dirs = _derive_include_dirs(ec_files)
    include_dirs += [Path(d) for d in override.get("include_dirs", [])]

    return RepoBuildConfig(
        slug=slug,
        root=root,
        include_dirs=include_dirs,
        ec_files=ec_files,
        exclude=exclude,
        file_timeout=int(override.get("file_timeout", file_timeout)),
        smt_timeout=int(override.get("smt_timeout", smt_timeout)),
        has_project_file=(root / PROJECT_FILE_NAME).is_file(),
        skip=None,
    )

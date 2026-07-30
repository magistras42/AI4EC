"""CLI for the benchmark extraction pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmark.build import default_jobs, run_build
from benchmark.build_config import DEFAULT_FILE_TIMEOUT, DEFAULT_SMT_TIMEOUT
from benchmark.clone import clone_exit_code, run_clone
from benchmark.config import BenchmarkPaths, default_paths
from benchmark.extract import run_extract
from benchmark.index_proofs import run_index
from benchmark.toolchain import ToolchainError, resolve_toolchain


def _build_paths(args: argparse.Namespace) -> BenchmarkPaths:
    defaults = default_paths()
    return BenchmarkPaths(
        repos_file=Path(args.repos_file),
        clone_dir=Path(args.clone_dir),
        data_dir=Path(args.data_dir),
        overrides_file=Path(getattr(args, "overrides", None) or defaults.overrides_file),
    )


def _parse_only(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {slug.strip() for slug in value.split(",") if slug.strip()}


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    defaults = default_paths()
    parser.add_argument(
        "--repos-file",
        default=str(defaults.repos_file),
        help="Path to repositories.md",
    )
    parser.add_argument(
        "--clone-dir",
        default=str(defaults.clone_dir),
        help="Directory for shallow git clones",
    )
    parser.add_argument(
        "--data-dir",
        default=str(defaults.data_dir),
        help="Directory for extracted .ec files and indexes",
    )


def _add_clone_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-clone repositories that already exist",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N repositories (for development)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated repo slugs to process",
    )
    parser.add_argument(
        "--recurse-submodules",
        action="store_true",
        help="Also fetch submodules (needed by repos vendoring Jasmin or EasyCrypt)",
    )


def _add_build_args(parser: argparse.ArgumentParser) -> None:
    defaults = default_paths()
    parser.add_argument(
        "--easycrypt",
        default=None,
        help="Path to the EasyCrypt binary (default: $EASYCRYPT, then PATH)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"Parallel compiles within a repo (default: {default_jobs()})",
    )
    parser.add_argument(
        "--file-timeout",
        type=int,
        default=DEFAULT_FILE_TIMEOUT,
        help=f"Wall-clock seconds per .ec file (default: {DEFAULT_FILE_TIMEOUT})",
    )
    parser.add_argument(
        "--smt-timeout",
        type=int,
        default=DEFAULT_SMT_TIMEOUT,
        help=f"EasyCrypt -timeout for SMT calls (default: {DEFAULT_SMT_TIMEOUT})",
    )
    parser.add_argument(
        "--repo-timeout",
        type=int,
        default=None,
        help="Wall-clock budget per repository; remaining files are marked skipped",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Pass -no-eco so nothing is read from or written to the .eco cache",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recompile files a previous report recorded as ok",
    )
    parser.add_argument(
        "--overrides",
        default=str(defaults.overrides_file),
        help="Path to the per-repo overrides JSON file",
    )


def _cmd_clone(args: argparse.Namespace) -> int:
    records = run_clone(
        _build_paths(args),
        force_refresh=args.force_refresh,
        limit=args.limit,
        only=_parse_only(args.only),
        recurse_submodules=args.recurse_submodules,
    )
    return clone_exit_code(records)


def _cmd_extract(args: argparse.Namespace) -> int:
    run_extract(_build_paths(args), force=getattr(args, "force", False))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        toolchain = resolve_toolchain(Path(args.easycrypt) if args.easycrypt else None)
    except ToolchainError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    print(f"[info] EasyCrypt {toolchain.git_hash or 'unknown'} at {toolchain.binary}")
    run_build(
        _build_paths(args),
        toolchain=toolchain,
        jobs=args.jobs,
        only=_parse_only(args.only),
        limit=args.limit,
        file_timeout=args.file_timeout,
        smt_timeout=args.smt_timeout,
        repo_timeout=args.repo_timeout,
        no_cache=args.no_cache,
        refresh=args.refresh,
    )
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    run_index(_build_paths(args), only_building=getattr(args, "only_building", False))
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    code = _cmd_clone(args)
    if code != 0:
        return code
    _cmd_extract(args)
    if not args.skip_build:
        code = _cmd_build(args)
        if code != 0:
            return code
    _cmd_index(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clone repos, extract .ec files, build them, and index EasyCrypt proofs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone_parser = subparsers.add_parser("clone", help="Stage 1: shallow-clone repositories")
    _add_shared_args(clone_parser)
    _add_clone_args(clone_parser)
    clone_parser.set_defaults(func=_cmd_clone)

    extract_parser = subparsers.add_parser("extract", help="Stage 2: extract .ec files")
    _add_shared_args(extract_parser)
    extract_parser.add_argument(
        "--force",
        action="store_true",
        help="Reserved for future selective re-extract behavior",
    )
    extract_parser.set_defaults(func=_cmd_extract)

    build_parser = subparsers.add_parser(
        "build",
        help="Stage 3: compile every .ec file with EasyCrypt",
    )
    _add_shared_args(build_parser)
    _add_build_args(build_parser)
    build_parser.add_argument("--limit", type=int, default=None)
    build_parser.add_argument("--only", default=None)
    build_parser.set_defaults(func=_cmd_build)

    index_parser = subparsers.add_parser("index", help="Stage 4: build proof index")
    _add_shared_args(index_parser)
    index_parser.add_argument(
        "--only-building",
        action="store_true",
        help="Index only proofs in files the build stage recorded as ok",
    )
    index_parser.set_defaults(func=_cmd_index)

    all_parser = subparsers.add_parser("all", help="Run clone, extract, build, and index")
    _add_shared_args(all_parser)
    _add_clone_args(all_parser)
    _add_build_args(all_parser)
    all_parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip the build stage (by far the slowest)",
    )
    all_parser.add_argument(
        "--only-building",
        action="store_true",
        help="Index only proofs in files the build stage recorded as ok",
    )
    all_parser.set_defaults(func=_cmd_all)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

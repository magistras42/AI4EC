"""CLI for the benchmark extraction pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmark.clone import clone_exit_code, run_clone
from benchmark.config import BenchmarkPaths, default_paths
from benchmark.extract import run_extract
from benchmark.index_proofs import run_index


def _build_paths(args: argparse.Namespace) -> BenchmarkPaths:
    defaults = default_paths()
    return BenchmarkPaths(
        repos_file=Path(args.repos_file),
        clone_dir=Path(args.clone_dir),
        data_dir=Path(args.data_dir),
    )


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


def _cmd_clone(args: argparse.Namespace) -> int:
    only = None
    if args.only:
        only = {slug.strip() for slug in args.only.split(",") if slug.strip()}
    records = run_clone(
        _build_paths(args),
        force_refresh=args.force_refresh,
        limit=args.limit,
        only=only,
    )
    return clone_exit_code(records)


def _cmd_extract(args: argparse.Namespace) -> int:
    run_extract(_build_paths(args), force=args.force)
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    run_index(_build_paths(args))
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    code = _cmd_clone(args)
    if code != 0:
        return code
    _cmd_extract(args)
    _cmd_index(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clone repos, extract .ec files, and index EasyCrypt proofs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone_parser = subparsers.add_parser("clone", help="Stage 1: shallow-clone repositories")
    _add_shared_args(clone_parser)
    clone_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-clone repositories that already exist",
    )
    clone_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Clone at most N repositories (for development)",
    )
    clone_parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated repo slugs to clone",
    )
    clone_parser.set_defaults(func=_cmd_clone)

    extract_parser = subparsers.add_parser("extract", help="Stage 2: extract .ec files")
    _add_shared_args(extract_parser)
    extract_parser.add_argument(
        "--force",
        action="store_true",
        help="Reserved for future selective re-extract behavior",
    )
    extract_parser.set_defaults(func=_cmd_extract)

    index_parser = subparsers.add_parser("index", help="Stage 3: build proof index")
    _add_shared_args(index_parser)
    index_parser.set_defaults(func=_cmd_index)

    all_parser = subparsers.add_parser("all", help="Run clone, extract, and index")
    _add_shared_args(all_parser)
    all_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-clone repositories that already exist",
    )
    all_parser.add_argument("--limit", type=int, default=None)
    all_parser.add_argument("--only", default=None)
    all_parser.set_defaults(func=_cmd_all)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

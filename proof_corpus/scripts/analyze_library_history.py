#!/usr/bin/env python3
"""
analyze_library_history.py

Mine the EasyCrypt git history for a fixed set of standard-library theories and
record, per release, what actually happened to each one: when the file was
created, moved or deleted, and which declarations were added or removed.

    <easycrypt clone with release tags>
  -> output/library_history.json

This is **evidence, not prose**. The per-library docs in `repair_doc/*.json`
were written by reading the current sources and the release notes; their own
`caveat` field says "No true git-diff was possible". This script does the diff.
Everything it reports is a fact about a commit, attributable to a release tag
and checkable with `git show`.

What it produces, per library
-----------------------------
* `path_events`  -- add / delete / rename, with the release each landed in.
  This is how a theory being **created**, **moved** or **removed** is detected;
  no heuristic is involved, git reports the status directly.
* `symbol_events` -- per release, the declaration names added and removed,
  extracted from the content diff (`op`/`lemma`/`type`/`pred`/`abbrev`/
  `axiom`/`module`/`theory`/`const`, plus `rename "x" as "y"` inside clones).
* `first_release` / `last_release` -- the window in which the file exists.

Why this beats the release notes for import repair: a symbol vanishing from
theory A in release R *and appearing in theory B in the same release* is a
symbol MOVE, which is exactly what breaks a `require import` -- and the
r2025.02 SmtMap -> FMap split shows up this way without anyone having written
it down. `build_ec_migrations.py` turns those pairings into rewrite rules.

Usage
-----
    python3 proof_corpus/scripts/analyze_library_history.py
    python3 proof_corpus/scripts/analyze_library_history.py \\
        --repo integration/extern/easycrypt \\
        --library FMap --library SmtMap

The clone must have the release tags. Fetch them with::

    git -C <clone> fetch https://github.com/EasyCrypt/easycrypt.git \\
        'refs/tags/*:refs/tags/*'
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "ai4ec.library-history/1"

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_CORPUS = REPO_ROOT / "proof_corpus"

DEFAULT_REPO = REPO_ROOT / "integration" / "extern" / "easycrypt"
DEFAULT_OUT = PROOF_CORPUS / "output" / "library_history.json"

# The standard-library theories tracked by default. These are the ones a
# broken proof is most likely to import, and the same set `repair_doc/`
# documents -- but here every claim is derived from the commit history rather
# than from those files.
DEFAULT_LIBRARIES = [
    "AllCore", "Bool", "Core", "CoreMap", "CoreReal", "DBool", "DInterval",
    "Distr", "FMap", "FSet", "Int", "Logic", "Pervasive", "PROM", "Real",
    "SmtMap",
]

_THEORY_SUFFIXES = (".ec", ".eca")

# Commits before the oldest release tag. EasyCrypt's tags only start at
# r2022.04 but the project goes back to 2012, so a large share of every
# library's history lands here. Naming it explicitly keeps "we know this
# happened before our earliest tag" distinct from "we could not attribute it".
PRE_HISTORY = "(pre-r2022.04)"

_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"

# `module type X = {` declares X, not "type": without the optional `type`
# after `module`, the name group captured the keyword and `type` was reported
# as a symbol of every theory declaring a module type (PROM has 7).
_DECL_RE = re.compile(
    r"^\s*(?:local\s+|declare\s+|abstract\s+)*"
    r"(?P<kind>lemma|axiom|op|pred|type|abbrev|module|theory|const)\s+"
    r"(?:type\s+)?"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_']*)"
)

# EasyCrypt keywords that can still slip through as a captured name on unusual
# spacing. A keyword is never an importable symbol.
_KEYWORDS = frozenset({
    "type", "of", "with", "as", "end", "import", "export", "require", "proof",
    "qed", "by", "op", "pred", "lemma", "axiom", "module", "theory", "const",
    "abbrev", "local", "declare", "abstract", "rename", "clone", "section",
})
_RENAME_DECL_RE = re.compile(
    r'^\s*rename\s+(?:\[\w+\]\s*)?"[^"]+"\s+as\s+"(?P<name>[^"]+)"'
)


def git(repo: Path, args: list[str], *, timeout: int = 300) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def release_map(repo: Path, tags: list[str]) -> dict[str, str]:
    """Map every commit SHA to the release that introduced it.

    Built by walking tags oldest-first and taking `rev-list <prev>..<tag>`, so
    each commit is attributed to the FIRST release containing it. One rev-list
    per tag rather than one `git describe --contains` per commit, which would
    be thousands of subprocesses.
    """
    mapping: dict[str, str] = {}
    previous: str | None = None
    for tag in tags:
        rev_range = f"{previous}..{tag}" if previous else tag
        for sha in git(repo, ["rev-list", rev_range]).split():
            mapping.setdefault(sha, tag)
        previous = tag
    return mapping


def ordered_tags(repo: Path) -> list[str]:
    """Release tags present in the clone, oldest first.

    Ordered by TAG NAME, not by the tagged commit's date. `rYYYY.MM` sorts
    lexicographically into chronological order, whereas commit dates do not:
    r2026.07's tagged commit is dated 2026-05-15, two weeks BEFORE r2026.06's
    (2026-06-11), so a date sort silently produced the release sequence
    "... r2026.07, r2026.05, r2026.06" and attributed commits to the wrong
    release.
    """
    tags = [t for t in git(repo, ["tag"]).split() if re.fullmatch(r"r\d{4}\.\d{2}", t)]
    return sorted(tags)


def locate_library(repo: Path, library: str, ref: str) -> list[str]:
    """Current path(s) for a library, by file basename at `ref`."""
    paths = []
    for line in git(repo, ["ls-tree", "-r", "--name-only", ref]).splitlines():
        line = line.strip()
        if not line.startswith("theories/"):
            continue
        stem = line.rsplit("/", 1)[-1]
        for suffix in _THEORY_SUFFIXES:
            if stem == f"{library}{suffix}":
                paths.append(line)
    return sorted(paths)


def historical_paths(repo: Path, library: str, ref: str) -> list[str]:
    """Every path this library's file has ever occupied, current or not.

    `--follow` alone is not enough. When a theory is split, git records the
    move as a delete plus an add rather than a rename (too much of the content
    changed for rename detection to fire), and `--follow` cannot chase across
    that. SmtMap is exactly this case: it lived at `theories/newth/SmtMap.ec`
    from 2018 until the r2025.02 split moved it to `theories/datatypes/`, and
    following the current path alone finds 1 commit instead of 34.

    A glob pathspec over the basename finds both ends without needing to know
    the move happened.
    """
    paths: set[str] = set()
    for suffix in _THEORY_SUFFIXES:
        globs = [f"**/{library}{suffix}", f"{library}{suffix}"]
        for line in git(repo, ["log", "--format=", "--name-only", ref, "--", *globs]).splitlines():
            line = line.strip()
            if line.rsplit("/", 1)[-1] == f"{library}{suffix}":
                paths.add(line)
    return sorted(paths)


def _declarations(lines: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        match = _DECL_RE.match(line)
        if match:
            names.add(match.group("name"))
            continue
        match = _RENAME_DECL_RE.match(line)
        if match:
            names.add(match.group("name"))
    return names - _KEYWORDS


def _split_records(stream: str) -> list[tuple[list[str], str]]:
    out = []
    for record in stream.split(_RECORD_SEP):
        record = record.lstrip("\n")
        if not record.strip():
            continue
        header, _, rest = record.partition("\n")
        fields = header.split(_FIELD_SEP)
        if len(fields) >= 3:
            out.append(([f.strip() for f in fields], rest))
    return out


def parse_status_log(stream: str) -> dict[str, dict[str, Any]]:
    """Parse `git log --name-status` into ``sha -> {..., statuses}``."""
    commits: dict[str, dict[str, Any]] = {}
    for fields, rest in _split_records(stream):
        sha, date, subject = fields[0], fields[1], fields[2]
        statuses: list[tuple[str, str, str | None]] = []
        for line in rest.splitlines():
            if "\t" not in line or line[:1] not in "AMDRCT":
                continue
            parts = line.split("\t")
            code = parts[0]
            if code.startswith("R") and len(parts) >= 3:
                statuses.append(("R", parts[2], parts[1]))
            elif len(parts) >= 2:
                statuses.append((code[:1], parts[1], None))
        commits[sha] = {
            "sha": sha, "short_sha": sha[:9], "date": date,
            "subject": subject, "statuses": statuses,
            "added": [], "removed": [],
        }
    return commits


def parse_patch_log(stream: str) -> dict[str, tuple[set[str], set[str]]]:
    """Parse `git log -p` into ``sha -> (declarations added, removed)``.

    A SEPARATE call from the name-status one: `git log --name-status -p` emits
    the name-status block *instead of* the patch, so asking for both in one
    invocation silently yields no diff lines at all and every symbol event
    comes back empty.
    """
    out: dict[str, tuple[set[str], set[str]]] = {}
    for fields, rest in _split_records(stream):
        added_lines: list[str] = []
        removed_lines: list[str] = []
        for line in rest.splitlines():
            if line.startswith(("+++", "---", "diff ", "index ", "@@",
                                "new file", "deleted file", "similarity ",
                                "rename from", "rename to", "Binary files",
                                "old mode", "new mode")):
                continue
            if line.startswith("+"):
                added_lines.append(line[1:])
            elif line.startswith("-"):
                removed_lines.append(line[1:])
        out[fields[0]] = (_declarations(added_lines), _declarations(removed_lines))
    return out


def analyze_library(
    repo: Path, library: str, ref: str, releases: dict[str, str],
) -> dict[str, Any]:
    paths = locate_library(repo, library, ref)
    # A library missing from the current tree is not an error -- it may have
    # been deleted, which is itself an import-repair-relevant fact. Walk every
    # path the file has ever occupied, not just the current one.
    probe_paths = historical_paths(repo, library, ref) or paths or [
        f"theories/{library}.ec"
    ]

    fmt = f"--format={_RECORD_SEP}%H{_FIELD_SEP}%aI{_FIELD_SEP}%s"
    merged: dict[str, dict[str, Any]] = {}
    for path in probe_paths:
        statuses = parse_status_log(git(repo, [
            "log", "--follow", "--name-status", "--no-color", fmt, ref, "--", path,
        ]))
        patches = parse_patch_log(git(repo, [
            "log", "--follow", "-p", "--unified=0", "--no-color", fmt, ref, "--", path,
        ]))
        for sha, commit in statuses.items():
            added, removed = patches.get(sha, (set(), set()))
            existing = merged.get(sha)
            if existing is None:
                merged[sha] = existing = commit
            else:
                # Union the STATUS lists too, not just the symbol sets. A split
                # commit adds the new path and deletes the old one, and those
                # arrive from two different per-path logs; keeping only the
                # first-seen commit record dropped the delete, so SmtMap moving
                # newth/ -> datatypes/ looked like a plain creation.
                for status in commit["statuses"]:
                    if status not in existing["statuses"]:
                        existing["statuses"].append(status)
            existing["added"] = sorted(set(existing["added"]) | added)
            existing["removed"] = sorted(set(existing["removed"]) | removed)

    unique = sorted(merged.values(), key=lambda c: c["date"])

    path_events: list[dict[str, Any]] = []
    per_release: dict[str, dict[str, set[str]]] = {}

    for commit in unique:
        release = releases.get(commit["sha"], PRE_HISTORY)
        for code, path, old_path in commit["statuses"]:
            stem = path.rsplit("/", 1)[-1]
            if not any(stem == f"{library}{s}" for s in _THEORY_SUFFIXES):
                # A rename AWAY from this library's name (e.g. SmtMap.ec ->
                # FMap.ec) still matters: it is how the new theory came to
                # exist. Keep it only when the OLD path was this library.
                if not (old_path and any(
                    old_path.rsplit("/", 1)[-1] == f"{library}{s}"
                    for s in _THEORY_SUFFIXES
                )):
                    continue
            if code in ("A", "D", "R"):
                path_events.append({
                    "release": release,
                    "event": {"A": "added", "D": "deleted", "R": "renamed"}[code],
                    "path": path,
                    "from_path": old_path,
                    "sha": commit["short_sha"],
                    "date": commit["date"],
                    "subject": commit["subject"],
                })

        if commit["added"] or commit["removed"]:
            bucket = per_release.setdefault(release, {"added": set(), "removed": set()})
            bucket["added"].update(commit["added"])
            bucket["removed"].update(commit["removed"])

    symbol_events = {}
    for release, bucket in per_release.items():
        # A name on both sides of a diff was edited in place, not added or
        # removed -- reporting it as either would be wrong.
        added = bucket["added"] - bucket["removed"]
        removed = bucket["removed"] - bucket["added"]
        modified = bucket["added"] & bucket["removed"]
        if added or removed or modified:
            symbol_events[release] = {
                "added": sorted(added),
                "removed": sorted(removed),
                "modified": sorted(modified),
            }

    releases_touched = sorted(
        {e["release"] for e in path_events} | set(symbol_events),
        key=lambda r: (r != PRE_HISTORY, r),
    )
    created = [e for e in path_events if e["event"] == "added"]
    deleted = [e for e in path_events if e["event"] == "deleted"]

    # An add and a delete of DIFFERENT paths in the same release is a move, not
    # a creation plus an unrelated removal. git only reports it as a rename when
    # the content is similar enough; a theory split changes too much for that,
    # which is why SmtMap moving newth/ -> datatypes/ arrives as A + D.
    moved: list[dict[str, Any]] = []
    for add in created:
        for drop in deleted:
            if add["release"] == drop["release"] and add["path"] != drop["path"]:
                moved.append({
                    "release": add["release"],
                    "from_path": drop["path"],
                    "to_path": add["path"],
                    "sha": add["sha"],
                    "subject": add["subject"],
                })
    moved_adds = {(m["release"], m["to_path"]) for m in moved}
    moved_deletes = {(m["release"], m["from_path"]) for m in moved}
    created = [e for e in created if (e["release"], e["path"]) not in moved_adds]
    deleted = [e for e in deleted if (e["release"], e["path"]) not in moved_deletes]

    return {
        "library": library,
        "current_paths": paths,
        "exists_now": bool(paths),
        "commits_examined": len(unique),
        "first_release": created[0]["release"] if created else None,
        "created": created,
        "deleted": deleted,
        "moved": moved,
        "renames": [e for e in path_events if e["event"] == "renamed"],
        "path_events": path_events,
        "symbol_events": symbol_events,
        "releases_touched": releases_touched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument(
        "--library", action="append", default=None,
        help=f"repeatable; default: {', '.join(DEFAULT_LIBRARIES)}",
    )
    parser.add_argument(
        "--ref", default=None,
        help="ref to treat as 'current' (default: the newest release tag)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--indent", type=int, default=1)
    args = parser.parse_args()

    if not (args.repo / ".git").exists() and not (args.repo / "HEAD").exists():
        print(f"error: {args.repo} is not a git repository", file=sys.stderr)
        return 1

    tags = ordered_tags(args.repo)
    if not tags:
        print(
            f"error: {args.repo} has no rYYYY.MM release tags. Fetch them:\n"
            f"  git -C {args.repo} fetch "
            f"https://github.com/EasyCrypt/easycrypt.git 'refs/tags/*:refs/tags/*'",
            file=sys.stderr,
        )
        return 1

    ref = args.ref or tags[-1]
    libraries = args.library or DEFAULT_LIBRARIES

    print(
        f"Building release map over {len(tags)} tags ({tags[0]} .. {tags[-1]}) ...",
        file=sys.stderr,
    )
    releases = release_map(args.repo, tags)
    print(f"  {len(releases)} commits attributed to a release", file=sys.stderr)

    results = {}
    for library in libraries:
        record = analyze_library(args.repo, library, ref, releases)
        results[library] = record
        where = record["current_paths"][0] if record["current_paths"] else "(absent)"
        print(
            f"  {library:12s} {record['commits_examined']:4d} commits, "
            f"{len(record['symbol_events']):2d} releases with symbol changes  {where}",
            file=sys.stderr,
        )

    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_from": {
            "repo": str(args.repo),
            "ref": ref,
            "tags": tags,
        },
        "libraries": results,
        "stats": {
            "libraries": len(results),
            "libraries_present": sum(1 for r in results.values() if r["exists_now"]),
            "total_commits_examined": sum(r["commits_examined"] for r in results.values()),
            "libraries_created_in_tracked_window": sum(
                1 for r in results.values()
                if r["first_release"] and r["first_release"] != PRE_HISTORY
            ),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=args.indent or None, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

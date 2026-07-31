#!/usr/bin/env python3
"""
collect_changelog.py

Fetch raw GitHub release notes (and, optionally, merged-PR data) for a
repository, e.g. EasyCrypt/easycrypt, and dump them to a single JSON file
for downstream processing.

Two data sources are supported:

1. GitHub Releases API (`/repos/{owner}/{repo}/releases`) — this is what
   backs the human-readable release notes page and is usually sufficient:
   each release's `body` is a markdown bullet list of PR titles.

2. (Optional, `--with-pr-details`) For each PR number referenced in a
   release body, fetch the PR itself (`/repos/{owner}/{repo}/pulls/{n}`)
   to get labels, changed files, and the PR description. This is slower
   (one API call per PR) and subject to GitHub's rate limits, but gives
   the processing step more to work with (e.g. changed files under
   `theories/` vs `src/` helps disambiguate "library change" vs
   "engine/tactic change").

Usage:
    export GITHUB_TOKEN=ghp_xxx   # recommended: raises rate limit from 60/hr to 5000/hr
    python collect_changelog.py --repo EasyCrypt/easycrypt --out raw_releases.json
    python collect_changelog.py --repo EasyCrypt/easycrypt --out raw_releases.json \
        --since r2024.09 --with-pr-details

Output JSON shape:
{
  "repo": "EasyCrypt/easycrypt",
  "fetched_at": "...",
  "releases": [
    {
      "tag_name": "r2025.10",
      "name": "Release 2025.10",
      "published_at": "2025-10-03T09:13:00Z",
      "html_url": "https://github.com/EasyCrypt/easycrypt/releases/tag/r2025.10",
      "body": "<raw markdown>",
      "pr_details": {  # only if --with-pr-details
        "794": {"title": "...", "labels": [...], "changed_files": [...]},
        ...
      }
    },
    ...
  ]
}
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"


def gh_get(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    """GET with basic rate-limit backoff."""
    while True:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 403 and "X-RateLimit-Remaining" in resp.headers:
            remaining = int(resp.headers.get("X-RateLimit-Remaining", "1"))
            if remaining == 0:
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - time.time(), 1)
                print(f"Rate limited. Sleeping {wait:.0f}s...", file=sys.stderr)
                time.sleep(wait + 1)
                continue
        resp.raise_for_status()
        return resp


def fetch_releases(session: requests.Session, repo: str) -> list[dict]:
    """Fetch all releases, paginated, newest first (GitHub default order)."""
    releases = []
    url = f"{GITHUB_API}/repos/{repo}/releases"
    params = {"per_page": 100, "page": 1}
    while True:
        resp = gh_get(session, url, params=params)
        page = resp.json()
        if not page:
            break
        releases.extend(page)
        if len(page) < params["per_page"]:
            break
        params["page"] += 1
    return releases


# Only match the trailing "by @user in <link>" anchor on each bullet line --
# NOT a blanket "#\d+" search over the whole body. A bare '#(\d+)' search
# also matches issue-closing references embedded in a PR's own title (e.g.
# "Fix underflow (closes #1056)"), which is an *issue* number, not a PR
# number, and 404s against the /pulls endpoint. Anchoring to the bullet's
# own "in https://.../pull/NNN" (or short "in #NNN") suffix avoids that.
PR_LINK_RE = re.compile(
    r"^\s*[-*].*\bin\s+(?:https?://\S+/pull/(\d+)|#(\d+))\s*$",
    re.MULTILINE,
)


def extract_pr_numbers(body: str) -> list[str]:
    """Pull true PR numbers out of release-note bullets, anchored to the
    trailing '... by @user in <pull-link>' each bullet ends with."""
    numbers = set()
    for m in PR_LINK_RE.finditer(body or ""):
        numbers.add(m.group(1) or m.group(2))
    return sorted(numbers, key=int)


def fetch_pr_details(session: requests.Session, repo: str, pr_numbers: list[str]) -> dict:
    """Fetch title/labels/changed-files for each PR number. One call per PR
    (plus one for changed files) -- use sparingly on large ranges.

    Records a failure entry (rather than silently dropping the key) so
    downstream processing can see what's missing instead of just not
    finding the key at all."""
    details = {}
    n_ok, n_failed = 0, 0
    for num in pr_numbers:
        pr_url = f"{GITHUB_API}/repos/{repo}/pulls/{num}"
        try:
            pr_resp = gh_get(session, pr_url)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            print(f"  ! could not fetch PR #{num} ({status}); recording as unavailable, continuing", file=sys.stderr)
            details[num] = {"error": str(e)}
            n_failed += 1
            continue
        pr = pr_resp.json()

        files_url = f"{pr_url}/files"
        try:
            files_resp = gh_get(session, files_url, params={"per_page": 100})
            changed_files = [f["filename"] for f in files_resp.json()]
        except requests.HTTPError:
            changed_files = []

        details[num] = {
            "title": pr.get("title"),
            "body": pr.get("body"),
            "labels": [l["name"] for l in pr.get("labels", [])],
            "changed_files": changed_files,
        }
        n_ok += 1
    if n_failed:
        print(f"  ({n_ok} PR(s) fetched OK, {n_failed} unavailable)", file=sys.stderr)
    return details


# --- git-log source ---------------------------------------------------------
#
# EasyCrypt's GitHub release bodies are empty before r2025.02 (r2022.04 is one
# sentence; r2023.09 and r2024.01 are literally 0 bytes). The releases-only
# pipeline therefore has NOTHING for that span, which is exactly where the
# corpus lives -- 18% of the tracked repos predate the changelog entirely and
# 44% span 13+ releases. This source fills those gaps from the repository
# itself: `git log <prev-tag>..<tag>`, which reaches back as far as the history
# does, independent of whether anyone wrote release notes.

# ASCII unit/record separators: commit subjects and bodies contain newlines,
# tabs, pipes and commas, so any printable delimiter is unsafe.
_GIT_FIELD_SEP = "\x1f"
_GIT_RECORD_SEP = "\x1e"
_GIT_LOG_FORMAT = _GIT_FIELD_SEP.join(["%H", "%s", "%an", "%aI", "%b"]) + _GIT_RECORD_SEP

# Default path filter. Library and engine changes are what break proofs; docs,
# CI config and test scaffolding are pure noise at repair time and would
# multiply the classification cost for nothing.
DEFAULT_GIT_LOG_PATHS = ["theories/", "src/", "libs/"]


def git(repo: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  ! git {' '.join(args[:2])} failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def commits_between(
    repo: Path, since_tag: str | None, until_tag: str, paths: list[str],
) -> list[dict]:
    """Return the non-merge commits in ``(since_tag, until_tag]``.

    ``since_tag=None`` means "everything reachable from until_tag", used for
    the oldest cataloged release, where there is no previous tag to bound it.
    Merges are excluded: their subjects are "Merge pull request #N from ..."
    boilerplate that carries no information the merged commits don't.
    """
    rev_range = f"{since_tag}..{until_tag}" if since_tag else until_tag
    args = ["log", "--no-merges", f"--format={_GIT_LOG_FORMAT}", rev_range]
    if paths:
        args += ["--", *paths]
    out = git(repo, args)
    if out is None:
        return []

    commits = []
    for record in out.split(_GIT_RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        fields = record.split(_GIT_FIELD_SEP)
        if len(fields) < 4:
            continue
        sha, subject, author, date = fields[0], fields[1], fields[2], fields[3]
        body = fields[4] if len(fields) > 4 else ""
        commits.append({
            "sha": sha,
            "short_sha": sha[:9],
            "title": subject.strip(),
            "author": author.strip(),
            "date": date.strip(),
            "body": body.strip(),
        })
    return commits


def commit_changed_files(repo: Path, sha: str, limit: int) -> list[str]:
    out = git(repo, ["show", "--name-only", "--format=", sha])
    if not out:
        return []
    files = [line.strip() for line in out.splitlines() if line.strip()]
    return files[:limit]


def resolve_tag_order(repo: Path, tags: list[str]) -> list[str]:
    """Order `tags` oldest-first by their commit date, keeping only tags the
    clone actually has. The fork carries the `-premises` patch on top of
    upstream and does not necessarily have upstream's tags, so a missing tag
    is expected and reported rather than fatal."""
    dated = []
    missing = []
    for tag in tags:
        out = git(repo, ["log", "-1", "--format=%aI", tag])
        if out is None or not out.strip():
            missing.append(tag)
            continue
        dated.append((out.strip(), tag))
    if missing:
        print(
            f"  ! clone has no such tag(s): {', '.join(missing)}. "
            f"Fetch them with: git -C {repo} fetch "
            f"https://github.com/EasyCrypt/easycrypt.git 'refs/tags/*:refs/tags/*'",
            file=sys.stderr,
        )
    dated.sort()
    return [tag for _date, tag in dated]


def attach_git_log(
    out_releases: list[dict],
    repo: Path,
    *,
    only_empty: bool,
    paths: list[str],
    max_files: int,
    max_commits: int,
) -> int:
    """Attach commit-derived entries to releases, in place.

    With ``only_empty`` (the default) this touches just the releases whose
    notes yielded no bullets -- the point is to fill holes, not to duplicate
    well-documented releases with a second, noisier view of the same changes.
    """
    by_tag = {str(rel.get("tag_name")): rel for rel in out_releases}
    ordered = resolve_tag_order(repo, list(by_tag))
    if not ordered:
        print("  ! no usable tags in the clone; skipping git-log source", file=sys.stderr)
        return 0

    filled = 0
    for position, tag in enumerate(ordered):
        release = by_tag[tag]
        if only_empty and release.get("bullet_count"):
            continue
        previous = ordered[position - 1] if position > 0 else None
        commits = commits_between(repo, previous, tag, paths)
        if len(commits) > max_commits:
            print(
                f"  ! {tag}: {len(commits)} commits exceeds --git-log-max-commits="
                f"{max_commits}; keeping the {max_commits} most recent",
                file=sys.stderr,
            )
            commits = commits[:max_commits]
        for commit in commits:
            commit["changed_files"] = commit_changed_files(repo, commit["sha"], max_files)
        release["commits"] = commits
        release["commit_range"] = f"{previous or '(root)'}..{tag}"
        if commits:
            filled += 1
        print(
            f"  {tag}: {len(commits)} commit(s) from {release['commit_range']}",
            file=sys.stderr,
        )
    return filled


def slice_by_tag(releases: list[dict], since: str | None, until: str | None) -> list[dict]:
    """Keep releases in (since, until] by tag_name, if provided. Assumes the
    API's default newest-first order; falls back to returning everything if
    tags aren't found."""
    if not since and not until:
        return releases
    tags = [r["tag_name"] for r in releases]
    start = tags.index(until) if until in tags else 0
    end = tags.index(since) + 1 if since in tags else len(releases)
    return releases[start:end]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/repo, e.g. EasyCrypt/easycrypt")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--since", default=None, help="oldest tag to include (exclusive upper bound moves toward HEAD)")
    ap.add_argument("--until", default=None, help="newest tag to include")
    ap.add_argument("--with-pr-details", action="store_true",
                     help="also fetch per-PR title/labels/changed files (slower, more API calls)")
    ap.add_argument(
        "--git-log", type=Path, default=None, metavar="EC_REPO",
        help="path to an EasyCrypt clone WITH release tags; derive entries from "
             "`git log <prev-tag>..<tag>` for releases whose notes are empty "
             "(EasyCrypt's are, before r2025.02)",
    )
    ap.add_argument(
        "--git-log-all-releases", action="store_true",
        help="with --git-log, derive commits for EVERY release, not only the "
             "ones with empty notes (noisier and much more to classify)",
    )
    ap.add_argument(
        "--git-log-paths", default=",".join(DEFAULT_GIT_LOG_PATHS),
        help="comma-separated path prefixes to keep (empty string = no filter). "
             f"Default: {','.join(DEFAULT_GIT_LOG_PATHS)}",
    )
    ap.add_argument("--git-log-max-files", type=int, default=60,
                    help="cap changed_files recorded per commit")
    ap.add_argument("--git-log-max-commits", type=int, default=400,
                    help="cap commits recorded per release range")
    args = ap.parse_args()

    session = requests.Session()
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Accept"] = "application/vnd.github+json"

    print(f"Fetching releases for {args.repo} ...", file=sys.stderr)
    releases = fetch_releases(session, args.repo)
    releases = slice_by_tag(releases, args.since, args.until)
    print(f"Got {len(releases)} releases after slicing.", file=sys.stderr)

    out_releases = []
    empty_tags = []
    for rel in releases:
        entry = {
            "tag_name": rel.get("tag_name"),
            "name": rel.get("name"),
            "published_at": rel.get("published_at"),
            "html_url": rel.get("html_url"),
            "body": rel.get("body") or "",
        }
        pr_numbers = extract_pr_numbers(entry["body"])
        # Record what we found even when --with-pr-details is off, so a caller
        # can tell "this release genuinely has no notes" from "we never looked".
        entry["body_chars"] = len(entry["body"])
        entry["bullet_count"] = len(pr_numbers)
        if not pr_numbers:
            empty_tags.append(str(entry["tag_name"]))
        if args.with_pr_details:
            print(f"  fetching {len(pr_numbers)} PR(s) for {entry['tag_name']} ...", file=sys.stderr)
            entry["pr_details"] = fetch_pr_details(session, args.repo, pr_numbers)
        out_releases.append(entry)

    git_log_filled = 0
    if args.git_log is not None:
        if not (args.git_log / ".git").exists() and not (args.git_log / "HEAD").exists():
            print(
                f"error: --git-log {args.git_log} is not a git repository",
                file=sys.stderr,
            )
            sys.exit(2)
        paths = [p.strip() for p in args.git_log_paths.split(",") if p.strip()]
        print(
            f"Deriving commit entries from {args.git_log} "
            f"({'all releases' if args.git_log_all_releases else 'releases with empty notes'}"
            f"{', paths: ' + ', '.join(paths) if paths else ', all paths'}) ...",
            file=sys.stderr,
        )
        git_log_filled = attach_git_log(
            out_releases,
            args.git_log,
            only_empty=not args.git_log_all_releases,
            paths=paths,
            max_files=args.git_log_max_files,
            max_commits=args.git_log_max_commits,
        )

    still_empty = [
        tag for tag in empty_tags
        if not (by_tag_commits := [
            r for r in out_releases
            if str(r.get("tag_name")) == tag and r.get("commits")
        ])
    ]
    output = {
        "repo": args.repo,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "releases": out_releases,
        "coverage": {
            "releases_fetched": len(out_releases),
            "releases_with_notes": len(out_releases) - len(empty_tags),
            "releases_without_notes": empty_tags,
            "releases_filled_from_git_log": git_log_filled,
            "releases_still_without_entries": still_empty,
        },
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {args.out}", file=sys.stderr)

    if empty_tags and git_log_filled:
        print(
            f"note: {len(empty_tags)} release(s) had no parseable notes "
            f"({', '.join(empty_tags)}); {git_log_filled} were filled from git log.",
            file=sys.stderr,
        )
    if still_empty:
        # EasyCrypt's own releases r2022.04 .. r2024.09 ship empty or one-line
        # bodies, so the notes-derived changelog has nothing for them. Say so at
        # collection time: downstream, "no entries in this range" is otherwise
        # indistinguishable from "nothing changed in this range", and a repair
        # run spanning those releases will silently find no evidence.
        hint = (
            "         Pass --git-log <path-to-easycrypt-clone> to derive entries "
            "from commits instead."
            if args.git_log is None
            else "         Even git log produced nothing for these (check the "
                 "clone has the tags and the path filter is not too narrow)."
        )
        print(
            f"warning: {len(still_empty)} release(s) have no entries at all: "
            f"{', '.join(still_empty)}.\n"
            f"         That is missing data, not evidence that nothing changed.\n"
            f"{hint}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
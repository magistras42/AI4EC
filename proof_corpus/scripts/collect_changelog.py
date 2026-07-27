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
import sys
import time
from datetime import datetime, timezone

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
    for rel in releases:
        entry = {
            "tag_name": rel.get("tag_name"),
            "name": rel.get("name"),
            "published_at": rel.get("published_at"),
            "html_url": rel.get("html_url"),
            "body": rel.get("body") or "",
        }
        if args.with_pr_details:
            pr_numbers = extract_pr_numbers(entry["body"])
            print(f"  fetching {len(pr_numbers)} PR(s) for {entry['tag_name']} ...", file=sys.stderr)
            entry["pr_details"] = fetch_pr_details(session, args.repo, pr_numbers)
        out_releases.append(entry)

    output = {
        "repo": args.repo,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "releases": out_releases,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
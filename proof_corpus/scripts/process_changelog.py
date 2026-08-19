#!/usr/bin/env python3
"""
process_changelog.py

Turn the raw release JSON produced by collect_changelog.py into a compact,
structured changelog: one record per PR/bullet, with a `kind` tag, extracted
identifiers (tactic/lemma/theory names), a one-line `repair_hint`, and a
relevance flag -- ready to be filtered and injected into a proof-repair
prompt.

Pipeline per bullet:
  1. Regex-parse the bullet into (title, author, pr_number).
  2. Cheap rule-based pre-classification (kind + candidate identifiers) from
     keywords/labels/changed-files, when available. This handles the bulk of
     "internal"/"ci"/"documentation" bullets for free, without an LLM call.
  3. Anything not confidently classified by rules is sent to the Anthropic
     API in a batch prompt (many bullets per call) to fill in `kind`,
     `repair_hint`, and structured fields. This is the expensive step, so
     entries are cached by (repo, pr_number, title) to avoid re-billing on
     reruns.

Step 3 goes through the Message Batches API by default: every unclassified
entry across *all* releases is chunked once, submitted as a single job, and
collected when the job ends. That halves the token cost versus one blocking
`messages.create()` per chunk, and it removes the serial round-trip -- the
git-log releases added ~600 entries, i.e. ~42 sequential calls, which took
about 20 minutes of pure waiting. Pass --sync for the old inline path when you
want results immediately and do not care about the discount.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python process_changelog.py --in raw_releases.json --out changelog.yaml \
        --cache llm_cache.json
    # inline, non-batched (immediate, full price):
    python process_changelog.py ... --sync

Output: a YAML file structured as:

releases:
  - version: r2025.10
    published_at: "2025-10-03T09:13:00Z"
    entries:
      - id: 795
        kind: tactic_change
        tactic: match
        summary: "..."
        repair_hint: "..."
        relevance: high
      - id: 798
        kind: internal
        relevance: low
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict

import yaml

try:
    import anthropic
except ImportError:
    anthropic = None  # only required if the LLM pass actually runs

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000

# --- Rule-based pre-classification -----------------------------------------

# (regex on title/labels, kind, low_relevance)
RULES = [
    (r"^\s*internal\b", "internal", True),
    (r"^\s*ci\b|continuous integration", "internal", True),
    (r"^\s*documentation\b|^\s*doc\b", "documentation", True),
    (r"^\s*lint\b|warnings?\b.*fix|fix.*warnings?", "internal", True),
    (r"^\s*runtest\b|test runner", "internal", True),
    (r"^\s*\[tactic\]|\btactic\b", "tactic_change", False),
    (r"new lemma|lemmas?:|add.*lemma", "lemma_added", False),
    (r"rename", "lemma_renamed", False),
    (r"^\s*cloning\b|clone", "mechanism_change", False),
    (r"import mechanism|^\s*import\b", "mechanism_change", False),
    (r"^\s*lexer\b|^\s*parser\b|parsing", "syntax_change", False),
    (r"deprecat|remov(e|ed|al)", "lemma_removed", False),
]

IDENTIFIER_RE = re.compile(r"\b([a-z][a-zA-Z0-9_]*(?:'[a-zA-Z0-9_]*)?)\b")
# words unlikely to be meaningful identifiers to extract for retrieval matching
STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "for", "and", "or", "is", "on",
    "by", "with", "fix", "add", "new", "some", "as", "be", "not", "when",
    "that", "this", "it", "do", "does", "improve", "actually", "properly",
}


@dataclass
class Entry:
    id: str
    title: str
    kind: str | None = None
    identifiers: list[str] = field(default_factory=list)
    summary: str | None = None
    repair_hint: str | None = None
    relevance: str = "unknown"  # "high" | "medium" | "low"
    needs_llm: bool = True
    # "pr" for a release-note bullet, "commit" for a git-log-derived entry
    # (collect_changelog.py --git-log). Commit entries exist because EasyCrypt's
    # release bodies are empty before r2025.02; see that script's git-log
    # section. `sha` is set only for commit entries.
    source: str = "pr"
    sha: str | None = None


BULLET_RE = re.compile(
    r"^\s*[-*]\s*(?P<title>.+?)\s+by\s+@(?P<author>\S+)\s+in\s+"
    r"(?:https?://\S+/pull/|#)(?P<pr>\d+)\s*$",
    re.MULTILINE,
)

# Commit subjects that are pure mechanics. Release-note bullets are curated by
# a human before publication; raw commit subjects are not, so this stage has to
# do the filtering the release notes would otherwise have done for free.
COMMIT_NOISE_RE = re.compile(
    r"^\s*(merge\b|revert\b|bump\b|typo\b|fix typo|whitespace|reindent|"
    r"cosmetic|nit\b|wip\b|\[ci\]|ci:|travis|appveyor|dependabot|"
    r"update (changelog|readme|copyright)|version bump)",
    re.IGNORECASE,
)


def parse_bullets(body: str) -> list[Entry]:
    entries = []
    for m in BULLET_RE.finditer(body or ""):
        entries.append(Entry(id=m.group("pr"), title=m.group("title").strip()))
    return entries


def parse_commits(commits: list[dict]) -> list[Entry]:
    """Build entries from collect_changelog's git-log records.

    Keyed by short SHA rather than PR number, so ids never collide with the
    release-note entries a release may also have. Obvious mechanical commits
    are dropped here rather than sent to the classifier: they are numerous
    (652 commits were recovered for EasyCrypt's four undocumented releases)
    and each one costs an API call's share of a batch.
    """
    entries = []
    for commit in commits or []:
        title = str(commit.get("title") or "").strip()
        if not title or COMMIT_NOISE_RE.match(title):
            continue
        entries.append(Entry(
            id=str(commit.get("short_sha") or commit.get("sha") or "")[:9],
            title=title,
            source="commit",
            sha=str(commit.get("sha") or "") or None,
        ))
    return entries


def rule_classify(entry: Entry) -> None:
    title_lower = entry.title.lower()
    for pattern, kind, low_relevance in RULES:
        if re.search(pattern, title_lower):
            entry.kind = kind
            entry.relevance = "low" if low_relevance else "medium"
            entry.needs_llm = not low_relevance  # still let LLM refine non-trivial ones
            break
    entry.identifiers = extract_identifiers(entry.title)


def extract_identifiers(title: str) -> list[str]:
    words = IDENTIFIER_RE.findall(title)
    return [w for w in words if w.lower() not in STOPWORDS and len(w) > 2]


# --- LLM classification pass ------------------------------------------------

LLM_SYSTEM_PROMPT = """\
You are helping build a structured changelog for the EasyCrypt proof \
assistant, to be used by another LLM doing automated proof repair across \
EasyCrypt versions. For each changelog bullet (a PR title, sometimes with \
extra context), output a JSON object with these fields:

- id: the PR number given (string, echo it back)
- kind: one of "tactic_change", "lemma_added", "lemma_renamed", \
"lemma_removed", "mechanism_change", "syntax_change", "documentation", \
"internal"
- identifiers: list of concrete tactic names, lemma names, or theory/module \
names mentioned or clearly implied (empty list if none)
- summary: one sentence, plain description of what changed (not the raw PR \
title)
- repair_hint: one sentence written for an LLM repairing a broken EasyCrypt \
proof script: what should it look for / try if this change is the likely \
cause of a proof failure? If the change is not the kind of thing that \
breaks proofs (e.g. internal refactor, CI, docs), set this to null.
- relevance: "high" if this could plausibly break or fix existing proof \
scripts, "medium" if uncertain, "low" if purely internal/non-semantic.

Return ONLY a JSON array of these objects, one per input bullet, in the \
same order as the input, with no preamble, no markdown fences, no commentary.

Some inputs have "source": "commit" -- these are raw git commit subjects for \
releases that shipped with no release notes, not curated PR titles. They are \
terser, often internal, and frequently prefixed with a subsystem ("op:", \
"Internal:", "opsem:"). Judge them the same way, but be MORE willing to mark \
them "internal"/"low": an unremarkable refactor commit is not a changelog \
entry a proof-repair agent needs to see.
"""


# One entry plus the PR/commit metadata that goes into its prompt. Carried as a
# pair because chunks are built across releases, so there is no single release
# `pr_details` map to look the context up in later.
Pending = tuple[Entry, dict]


def request_params(chunk: list[Pending]) -> dict:
    """The Messages-API params for one chunk, shared by both the batch and the
    inline path so the two cannot drift apart."""
    payload = [
        {
            "id": e.id,
            "title": e.title,
            "source": e.source,
            "changed_files": ctx.get("changed_files", []),
            "labels": ctx.get("labels", []),
        }
        for e, ctx in chunk
    ]
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": LLM_SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": "Classify these EasyCrypt changelog bullets:\n\n"
                       + json.dumps(payload, indent=2),
        }],
    }


def apply_response(chunk: list[Pending], text: str, label: str) -> bool:
    """Fill in `chunk`'s entries from one model response. Returns False if the
    response was unusable, leaving every entry's needs_llm set -- the caller
    keys the cache off that flag, so a rejected response is retried next run
    rather than cached as a row of nulls."""
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        print(f"  ! failed to parse LLM output for {label}", file=sys.stderr)
        return False

    by_id = {e.id: e for e, _ in chunk}
    for r in results:
        e = by_id.get(str(r.get("id")))
        if not e:
            continue
        e.kind = r.get("kind", e.kind)
        e.identifiers = list(dict.fromkeys((e.identifiers or []) + (r.get("identifiers") or [])))
        e.summary = r.get("summary")
        e.repair_hint = r.get("repair_hint")
        e.relevance = r.get("relevance", e.relevance)
        e.needs_llm = False
    return True


def chunk_pending(pending: list[Pending], batch_size: int) -> list[list[Pending]]:
    return [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]


def classify_inline(client, pending: list[Pending], batch_size: int) -> None:
    """One blocking messages.create() per chunk. Full price, immediate."""
    chunks = chunk_pending(pending, batch_size)
    for n, chunk in enumerate(chunks, 1):
        print(f"  LLM classifying {len(chunk)} entries ({n}/{len(chunks)}) ...", file=sys.stderr)
        resp = client.messages.create(**request_params(chunk))
        text = "".join(block.text for block in resp.content if block.type == "text")
        apply_response(chunk, text, f"chunk {n}")


def classify_batched(client, pending: list[Pending], batch_size: int,
                     poll_seconds: int, timeout_seconds: int) -> None:
    """Submit every chunk as one Message Batches job, then collect.

    Batch results come back in arbitrary order and are matched by `custom_id`,
    so the id -> chunk map is what stitches them back onto the right entries.
    """
    chunks = chunk_pending(pending, batch_size)
    by_custom_id = {f"chunk_{n:05d}": chunk for n, chunk in enumerate(chunks)}
    requests = [
        {"custom_id": custom_id, "params": request_params(chunk)}
        for custom_id, chunk in by_custom_id.items()
    ]

    print(f"  submitting {len(pending)} entries as {len(requests)} batch requests ...",
          file=sys.stderr)
    batch = client.messages.batches.create(requests=requests)
    print(f"  batch {batch.id} created; polling every {poll_seconds}s", file=sys.stderr)

    deadline = time.monotonic() + timeout_seconds
    while batch.processing_status != "ended":
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"batch {batch.id} still {batch.processing_status} after "
                f"{timeout_seconds}s; it is not lost -- rerun with the same "
                f"--cache to pick up whatever completed, or inspect it with "
                f"client.messages.batches.retrieve('{batch.id}')"
            )
        time.sleep(poll_seconds)
        batch = client.messages.batches.retrieve(batch.id)
        counts = batch.request_counts
        print(f"    {batch.processing_status}: processing={counts.processing} "
              f"succeeded={counts.succeeded} errored={counts.errored} "
              f"canceled={counts.canceled} expired={counts.expired}", file=sys.stderr)

    n_ok = n_bad = 0
    for result in client.messages.batches.results(batch.id):
        chunk = by_custom_id.get(result.custom_id)
        if chunk is None:
            print(f"  ! unknown custom_id {result.custom_id} in batch results", file=sys.stderr)
            continue
        outcome = result.result
        if outcome.type != "succeeded":
            detail = getattr(getattr(outcome, "error", None), "type", "")
            print(f"  ! {result.custom_id}: {outcome.type} {detail}".rstrip(), file=sys.stderr)
            n_bad += 1
            continue
        text = "".join(b.text for b in outcome.message.content if b.type == "text")
        if apply_response(chunk, text, result.custom_id):
            n_ok += 1
        else:
            n_bad += 1
    print(f"  batch complete: {n_ok} chunks applied, {n_bad} unusable", file=sys.stderr)


def load_cache(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(path: str | None, cache: dict) -> None:
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def cache_key(repo: str, entry: Entry) -> str:
    return f"{repo}::{entry.id}::{entry.title}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=None, help="path to LLM result cache JSON (recommended)")
    ap.add_argument("--batch-size", type=int, default=15,
                    help="entries per request (a request is one chunk of the batch job)")
    ap.add_argument("--skip-llm", action="store_true", help="rule-based classification only, no API calls")
    ap.add_argument("--sync", action="store_true",
                    help="use blocking messages.create() per chunk instead of the "
                         "Message Batches API (immediate, but no 50%% discount)")
    ap.add_argument("--poll-interval", type=int, default=30,
                    help="seconds between batch status polls (default 30)")
    ap.add_argument("--batch-timeout", type=int, default=86400,
                    help="give up waiting on the batch after this many seconds "
                         "(default 86400, the API's own 24h expiry)")
    args = ap.parse_args()

    with open(args.infile, encoding="utf-8") as f:
        raw = json.load(f)
    repo = raw["repo"]
    cache = load_cache(args.cache)

    client = None
    if not args.skip_llm:
        if anthropic is None:
            print("anthropic package not installed; run with --skip-llm or `pip install anthropic`", file=sys.stderr)
            sys.exit(1)
        client = anthropic.Anthropic()

    # Classification is a single pass over every release, so the releases are
    # built first and rendered afterwards: asdict() copies, and the batch job
    # fills the entries in between.
    per_release: list[tuple[dict, list[Entry]]] = []
    pending: list[Pending] = []
    n_commit_entries = 0
    for rel in raw["releases"]:
        entries = parse_bullets(rel["body"])
        pr_details = dict(rel.get("pr_details") or {})

        # Releases with no notes are filled from git log by collect_changelog
        # --git-log. Their commit records carry changed_files/body inline, so
        # they feed the classifier through the same extra-context channel as
        # pr_details rather than needing a second code path.
        commit_entries = parse_commits(rel.get("commits") or [])
        if commit_entries:
            n_commit_entries += len(commit_entries)
            by_short = {
                str(c.get("short_sha") or "")[:9]: c for c in rel.get("commits") or []
            }
            for entry in commit_entries:
                commit = by_short.get(entry.id, {})
                pr_details[entry.id] = {
                    "changed_files": commit.get("changed_files", []),
                    "labels": [],
                }
            entries = entries + commit_entries

        for e in entries:
            rule_classify(e)
            key = cache_key(repo, e)
            if key in cache:
                cached = cache[key]
                e.kind = cached["kind"]
                e.identifiers = cached["identifiers"]
                e.summary = cached["summary"]
                e.repair_hint = cached["repair_hint"]
                e.relevance = cached["relevance"]
                e.needs_llm = False
            elif e.needs_llm and client is not None:
                pending.append((e, pr_details.get(e.id, {})))

        per_release.append((rel, entries))

    if pending:
        if args.sync:
            classify_inline(client, pending, args.batch_size)
        else:
            classify_batched(client, pending, args.batch_size,
                             args.poll_interval, args.batch_timeout)
        # Only entries the model actually classified are cached. Caching the
        # rest would persist `summary: null` / `repair_hint: null` for a chunk
        # that merely failed to parse, and the cache hit would then suppress
        # the retry forever.
        for e, _ in pending:
            if e.needs_llm:
                continue
            cache[cache_key(repo, e)] = {
                "kind": e.kind, "identifiers": e.identifiers,
                "summary": e.summary, "repair_hint": e.repair_hint,
                "relevance": e.relevance,
            }
        n_unclassified = sum(1 for e, _ in pending if e.needs_llm)
        if n_unclassified:
            print(f"  ! {n_unclassified} entries left unclassified; rerun to retry them",
                  file=sys.stderr)

    out_releases = [
        {
            "version": rel["tag_name"],
            "published_at": rel["published_at"],
            "entries": [
                {k: v for k, v in asdict(e).items() if k != "needs_llm"}
                for e in entries
            ],
        }
        for rel, entries in per_release
    ]

    save_cache(args.cache, cache)
    if n_commit_entries:
        print(
            f"  ({n_commit_entries} entries came from git-log commits rather than "
            f"release notes)",
            file=sys.stderr,
        )

    with open(args.out, "w", encoding="utf-8") as f:
        yaml.dump({"repo": repo, "releases": out_releases}, f, sort_keys=False, allow_unicode=True)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

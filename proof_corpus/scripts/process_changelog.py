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

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python process_changelog.py --in raw_releases.json --out changelog.yaml \
        --cache llm_cache.json

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
from dataclasses import dataclass, field, asdict

import yaml

try:
    import anthropic
except ImportError:
    anthropic = None  # only required if the LLM pass actually runs

MODEL = "claude-sonnet-4-6"

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


BULLET_RE = re.compile(
    r"^\s*[-*]\s*(?P<title>.+?)\s+by\s+@(?P<author>\S+)\s+in\s+"
    r"(?:https?://\S+/pull/|#)(?P<pr>\d+)\s*$",
    re.MULTILINE,
)


def parse_bullets(body: str) -> list[Entry]:
    entries = []
    for m in BULLET_RE.finditer(body or ""):
        entries.append(Entry(id=m.group("pr"), title=m.group("title").strip()))
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
"""


def llm_classify_batch(client, batch: list[Entry], extra_context: dict) -> None:
    """Send a batch of entries to the API and fill in fields in place."""
    payload = [
        {
            "id": e.id,
            "title": e.title,
            "changed_files": extra_context.get(e.id, {}).get("changed_files", []),
            "labels": extra_context.get(e.id, {}).get("labels", []),
        }
        for e in batch
    ]
    user_msg = (
        "Classify these EasyCrypt changelog bullets:\n\n"
        + json.dumps(payload, indent=2)
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=LLM_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        print(f"  ! failed to parse LLM output for batch starting id={batch[0].id}", file=sys.stderr)
        return

    by_id = {e.id: e for e in batch}
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
    ap.add_argument("--batch-size", type=int, default=15)
    ap.add_argument("--skip-llm", action="store_true", help="rule-based classification only, no API calls")
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

    out_releases = []
    for rel in raw["releases"]:
        entries = parse_bullets(rel["body"])
        pr_details = rel.get("pr_details", {})

        to_llm = []
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
                to_llm.append(e)

        for i in range(0, len(to_llm), args.batch_size):
            batch = to_llm[i:i + args.batch_size]
            print(f"  LLM classifying {len(batch)} entries for {rel['tag_name']} ...", file=sys.stderr)
            llm_classify_batch(client, batch, pr_details)
            for e in batch:
                cache[cache_key(repo, e)] = {
                    "kind": e.kind, "identifiers": e.identifiers,
                    "summary": e.summary, "repair_hint": e.repair_hint,
                    "relevance": e.relevance,
                }

        out_releases.append({
            "version": rel["tag_name"],
            "published_at": rel["published_at"],
            "entries": [
                {k: v for k, v in asdict(e).items() if k != "needs_llm"}
                for e in entries
            ],
        })

    save_cache(args.cache, cache)

    with open(args.out, "w", encoding="utf-8") as f:
        yaml.dump({"repo": repo, "releases": out_releases}, f, sort_keys=False, allow_unicode=True)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

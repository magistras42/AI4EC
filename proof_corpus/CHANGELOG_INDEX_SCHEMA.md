# `changelog_index.json` — schema and rationale

The indexed EasyCrypt changelog: a flat, typed, pre-indexed derivation of
`output/changelog.yaml`, built for two consumers that the nested YAML served
badly — **proof repair** (`integration/agent/repair_hints.py`) and **corpus
analysis** (`scripts/compute_exposure_score.py`).

```
output/changelog.yaml       (authored / LLM-classified — process_changelog.py)
output/raw_releases.json    (raw GitHub data — collect_changelog.py)
<easycrypt>/theories/**     (the real symbol vocabulary)
repair_doc/tactics_ref.json (the real tactic vocabulary)
        │
        ▼  scripts/build_changelog_index.py   (pure derivation: no network, no LLM, no cost)
output/changelog_index.json
```

Rebuild after changing any input:

```bash
python3 proof_corpus/scripts/build_changelog_index.py
```

`changelog.yaml` remains the authored source of record and is **not** replaced.
Every consumer reads either format; see [Compatibility](#compatibility).

---

## Why the legacy format needed replacing

Four measured defects, all on the current 14-release / 276-entry catalog.

### 1. `identifiers` is ~85% English prose

The field is produced by a regex over the PR title plus whatever the
classifier LLM volunteered, with **no check that a name exists in EasyCrypt**.

| Measure | Value |
|---|---|
| Identifier slots that name a real declared EasyCrypt symbol | **208 / 1432 (14.5%)** |
| Distinct identifiers that resolve | 115 / 805 (14.3%) |
| Entries with **no** resolvable identifier | 161 / 276 (58.3%) |
| …among **high/medium**-relevance entries | **78 / 151 (51.7%)** |

The most frequent "identifiers" in the catalog were `tactic`, `error`, `code`,
`use`, `docker`, `from`, `printing`, `feat`, `level`, `message`.

This matters twice over:

- `retrieve_entries.score_entries` matched Tier B by exact token overlap
  against this list, so retrieval both **missed** real hits (half of the
  useful entries had nothing to match on) and **fired on coincidence** (every
  `.ec` file contains the word `list`).
- `compute_exposure_score.detect_content_bracket_version` matched the same
  list against repo sources to estimate which EasyCrypt version a repo was
  written against, so the noise propagated into corpus ranking.

### 2. The richest evidence was discarded

All **276/276** PRs in `raw_releases.json` carry `changed_files`, `labels`, and
a full PR `body`. **69** of them touch `theories/`. `process_changelog.py`
passed some of that to the classifier as *context* and then dropped it.

A changed file like `theories/datatypes/FMap.ec` is exact, machine-checked
theory scope — strictly better evidence than a guessed identifier, and exactly
what import repair needs.

### 3. No random access

"What changed about `FMap`?" required a full nested scan of every release.
Range queries re-sorted the release list on every call and located endpoints
with `list.index()`.

### 4. Untyped names

A theory, a lemma, and a tactic were flattened into one `identifiers` list, so
no consumer could ask for "entries about this *theory*" without re-deriving the
distinction.

### Bonus finding: the catalog covers 10 of 14 releases

`r2022.04`, `r2023.09`, `r2024.01` and `r2024.09` have **empty release notes
upstream** (0–31 characters of body), so they have no entries at all. The
catalog effectively starts at **r2025.02**.

This is not visible in the legacy format: a consumer sees 14 releases, gets no
hits for the pre-2025 span, and concludes "no known change explains this
failure" when the truth is "we have no notes for that period." The index
records it explicitly (`coverage`, and `retrieve_entries.coverage_gap`).

**This directly affects `elgamal-changelog-repair`**, whose spec declares
`source_ec_version="r2022.04"` for a 2020-era corpus: the entire span where its
breakage actually happened is uncataloged.

---

## Schema

Top level:

```json
{
  "schema": "ai4ec.changelog-index/1",
  "repo": "EasyCrypt/easycrypt",
  "generated_at": "2026-07-30T...Z",
  "generated_from": { "changelog": "...", "raw_releases": "...", "theories": "...", "tactics_ref": "..." },
  "releases":   [ ... ],
  "entries":    [ ... ],
  "indexes":    { ... },
  "vocabulary": { "symbols": 6326, "theories": 128, "tactics": 80 },
  "coverage":   { ... },
  "stats":      { ... }
}
```

### `releases[]`

| Field | Notes |
|---|---|
| `version` | e.g. `r2025.02` |
| `published_at` | ISO timestamp from GitHub |
| `ordinal` | **integer chronological position**, 0 = oldest. Range queries are integer comparisons; no re-sorting, no `list.index()` |
| `year`, `month` | parsed from the `rYYYY.MM` tag |
| `entry_keys` | keys into `entries[]` — entries are stored **once**, flat |
| `entry_count` | |
| `has_notes` | `false` when nothing was cataloged for this release |
| `source_body_chars` | length of the upstream GitHub release body |
| `coverage` | `covered` \| `empty_upstream` \| `no_entries` |

`releases[].entries` is **not** stored on disk — `retrieve_entries.load_changelog`
materializes it from `entry_keys` at load time, so the legacy nested view is
available without duplicating every entry in the file.

### `entries[]`

**Authored fields, preserved verbatim** (the index is additive, never a rewrite
of what a human or the classifier wrote):
`title`, `kind`, `relevance`, `summary`, `repair_hint`, `identifiers`.

**Identity / provenance:**

| Field | Notes |
|---|---|
| `key` | `"r2025.02#605"` — stable, unique, cross-referenceable |
| `version`, `ordinal` | denormalized from the release |
| `id` | the PR number. **Kept under its legacy name** — both `repair_hints.py` modules read `entry["id"]` |
| `pr` | same value, self-describing name |
| `url` | direct link to the PR |
| `labels` | GitHub labels (e.g. `breaking change`) |
| `body_excerpt` | first 600 chars of the PR body |
| `has_pr_details` | whether `raw_releases.json` had this PR |

**Derived name buckets** — the core of the change:

| Field | Meaning | Confidence |
|---|---|---|
| `symbols` | names resolved against real declarations in the EasyCrypt theory tree | high |
| `tactics` | names resolved against the tactic vocabulary | high |
| `theories_touched` | theories whose files this PR **actually changed** | **highest — machine-checked** |
| `theories_mentioned` | theories implied by a resolved symbol (a hit on `frng` surfaces `FMap`) | medium |
| `title_tokens` | leftover identifier-shaped words, stopword-filtered | low, last resort |

**Scope and weighting:**

| Field | Notes |
|---|---|
| `touches` | changed files bucketed into `library` / `engine` / `docs` / `examples` / `tooling` / `other` |
| `areas` | the distinct bucket names |
| `import_relevant` | prose is about the import/cloning machinery (or a theory being split/renamed/moved, with a theory demonstrably involved). Touching a file under `theories/` is **not** sufficient — a library change is not an import change |
| `breaking_weight` | `KIND_WEIGHTS[kind] × RELEVANCE_MULTIPLIER[relevance]`, materialized so retrieval and exposure scoring cannot drift apart |

### `indexes`

Inverted indexes from name → `[entry key]`, so lookups are O(1) per name:

`by_symbol`, `by_theory`, `by_tactic`, `by_kind`, `by_version`, and a flat
`import_relevant` key list (25 entries on the current catalog).

---

## How names are resolved

The precision problem is that many English words *are* declared somewhere in
the theory tree (`op all`, `type message`, `op change`). The builder uses
graded evidence rather than a single lookup:

1. **Backticked in the authored prose** → accepted on sight. Backticks are the
   authors' and the classifier's own marker for code.
2. **Claimed in `identifiers`** → accepted only if the name resolves **and**
   either looks like a deliberate identifier (carries a capital, underscore,
   digit, or prime: `FMap`, `set_set_swap`, `nth0`) **or** is corroborated by a
   theory the PR actually touched.
3. **Everything else** → `title_tokens`, after stopword filtering.

Two further rules:

- **Tactic-ness beats symbol-ness.** `rewrite`, `simplify`, `exact` and
  `change` are all declared somewhere *and* are tactics; classifying them as
  symbols overstated match strength and hid what the entry was about.
- **Owner expansion only for distinctive symbols.** Expanding `all` or `map`
  to "every theory that declares it" pulled in a third of the library.

Result: high/medium-relevance entries carrying at least one usable name went
from **73/151 (48.3%)** to **103/151 (68.2%)**, with the English-word
"identifiers" removed from the vocabulary entirely.

---

## Retrieval changes (`scripts/retrieve_entries.py`)

The four functions both `repair_hints.py` modules depend on —
`load_changelog`, `tokenize_proof`, `releases_in_range`, `score_entries` —
keep their signatures and semantics. Behavior improvements:

- **Typed matching.** `score_entries` matches the resolved buckets when present
  and falls back to `identifiers` otherwise. Each result reports `match_kinds`
  and a `match_strength` (symbol 4.0 > theory_touched 3.0 > tactic 2.5 >
  theory_mentioned 2.0 > identifier 1.0), and `reason` says which fired.
- **Tier A is capped** (default: a third of the budget). Previously Tier A was
  concatenated ahead of Tier B; over `r2025.02 → r2026.07` all 6 of a 6-slot
  budget went to unmatched "structural change in range" entries and the actual
  `SmtMap`/`FMap` hit never appeared.
- **A structural entry keeps its name match.** A `mechanism_change`/`high`
  entry that *also* matches by name is the strongest evidence available; it
  used to be filed as generic Tier A and lose its overlap entirely — which is
  precisely what happened to the real SmtMap→FMap split (#605).
- **No structural bonus for a bare `identifier` hit**, so the weakest signal
  can never outrank a resolved symbol.

New index-only helpers: `load_index`, `resolve_version`, `releases_between`
(with `strict=True` to raise instead of silently widening),
`entries_for_symbols`, `entries_for_theories`, `entries_for_tactics`,
`import_relevant_entries`, `coverage_gap`, `prompt_ready`.

New CLI modes:

```bash
# what changed about a theory, across a version range
python3 scripts/retrieve_entries.py --changelog output/changelog_index.json \
    --theory FMap --source-version r2022.04 --target-version r2026.07

# everything relevant to require/import/clone repair in range
python3 scripts/retrieve_entries.py --changelog output/changelog_index.json \
    --import-relevant --source-version r2025.02 --target-version r2026.07
```

---

## Compatibility

Nothing had to change to keep working.

| Consumer | Status |
|---|---|
| `integration/agent/repair_hints.py` | Prefers `changelog_index.json`, falls back to `changelog.yaml` (`resolve_changelog_path`) |
| `modified-shannonprover/core/easycrypt/repair_hints.py` | Same preference, same fallback |
| `scripts/compute_exposure_score.py` | Reads either format; uses resolved names via `changelog_entry_names` |
| `integration/tests/fixtures/repair_hints/` | Legacy-format fixture, untouched; all 20 pre-existing tests pass unmodified |

`load_changelog` dispatches on **content**, not filename, so a `.json` file in
the legacy shape and a `.yaml` file holding an index both work.

`releases_in_range` keeps its fail-open behavior (unknown tag ⇒ full history
with a warning). That is deliberate and load-bearing: EasyCrypt's releases only
go back to `r2022.04`, so a 2020-era proof legitimately has no source tag and
"everything we know about" is the correct, maximally-exposed answer. Use
`releases_between(..., strict=True)` when a caller would rather hear about the
miss.

---

## Known limitations

- **Coverage still starts at r2025.02.** The index reports the gap; it cannot
  fill it. Recovering pre-2025 history needs a different source than GitHub
  release notes (e.g. `git log` over `theories/`).
- **Rename pairs are still not modelled.** The catalog has exactly 3
  `lemma_renamed`/`lemma_removed`/`lemma_changed` entries, and none records an
  old-name → new-name pair; renames are buried in `mechanism_change` prose. A
  future `renames: [{from, to}]` field would need either a better classifier
  pass or diffs of the theory sources across tags.
- **`symbols` is per-name, not per-signature.** Knowing `FMap` declares `frng`
  does not say what `frng`'s type is; that needs the theory sources (see the
  symbol→theory index proposed in `docs/PROOF_REPAIR_HANDOFF.md` §W4).
- **`title_tokens` is still noisy** by design. Treat it as a last resort.
- The 5 `enrich_entry` weights and the ambiguity stoplist are heuristics tuned
  against this one catalog; re-check them if the corpus grows substantially.

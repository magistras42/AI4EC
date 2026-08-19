# `repair_docs_index.json` — schema and rationale

The reprocessed per-library repair docs: authored prose condensed, everything
checkable re-derived from the EasyCrypt sources, and a tree-wide
symbol → theory map for import repair.

```
repair_doc/*_lib.json         (authored prose — long, inconsistently shaped)
<easycrypt>/theories/**       (ground truth: real requires, real exports)
output/changelog_index.json   (what changed, per theory)
        │
        ▼  scripts/build_repair_docs.py   (pure derivation: no network, no LLM, no cost)
output/repair_docs_index.json
```

Rebuild after changing any input:

```bash
python3 proof_corpus/scripts/build_repair_docs.py
```

The authored `repair_doc/*_lib.json` files are **never modified** — same
separation as `changelog.yaml` vs `changelog_index.json`.

---

## Why

The authored docs are the project's best import-repair knowledge and were also
its least usable asset.

| Problem | Evidence |
|---|---|
| **Long and unstructured** | `current_content_summary` runs 300+ words in one paragraph. `repair_hints.py` re-sends its block on *every* agent step, competing with premises and failure history for context |
| **Inconsistently shaped** | `version_diffs_found` is a list in **5 of 18** files and a bare sentence in the other **13**. `requires` is prose, not a list |
| **Sparse where it matters** | Only **4 of 18** carry an `import_repair_note` — the one field written specifically for a `require import` that no longer resolves |
| **Unverifiable** | The docs' own `caveat` says no git-diff was possible; `requires` was transcribed by hand. Cross-checking against the real sources found **2 genuine disagreements** (see below) |
| **Narrow** | 18 of **128** theories are documented, so "which theory provides `frng`?" was unanswerable for 86% of the tree |

The last point was the important one. `ec.exe llm -premises` reports what **is**
in scope; nothing in the system could report what **could** be. An "unknown
symbol" error therefore had no route back to `require import <Theory>.`

---

## What it produces

### `libraries[]` — one record per documented library

Authored, condensed:

| Field | Notes |
|---|---|
| `summary` | `current_content_summary` clipped to `--summary-chars` (default 600) on a sentence boundary |
| `summary_full_chars` | original length, so the clipping is visible |
| `import_repair_note` | authored verbatim where one exists, otherwise **derived** (see below) |
| `import_repair_note_source` | `authored` \| `derived` |
| `version_notes` | `version_diffs_found` normalized to a list, with the 13 "None found by name…" sentences dropped — a statement that nothing was found is not a version note |
| `requires_prose` | the original prose, kept for the conditional detail a parser cannot express ("FullEager also pulls in List, FSet, IterProc") |

Derived from the real source:

| Field | Notes |
|---|---|
| `requires` | parsed from `require [import\|export] …` — handles multi-name lines, the `(*--*)` alignment comment, and `from X require …` |
| `require_exports` | `require export` tracked separately: it re-exports names to whoever requires *this* theory |
| `imports` | bare `import X.` — needs X already required. Conflating this with `require import` is itself a common repair mistake |
| `clones` | `clone [import\|export\|include] X` |
| `declaration_counts` / `declaration_total` | how many ops/lemmas/types/… the theory declares |
| `requires_mismatch` | where the authored prose and the parsed source disagree |

Derived from the changelog index:

| Field | Notes |
|---|---|
| `changelog[]` | entries that touched or named this theory (version, id, kind, title, repair_hint, url) |
| `changed_in` | the versions involved |
| `import_relevant_changes` | the subset flagged `import_relevant` |

### `theories{}` — every theory in the tree, documented or not

The same derived facts for all **128** theories, so import repair is not
limited to the 18 curated ones. Each carries `documented: true|false`.

### `symbol_index{}` — `symbol → [declaring theories]`

**6325 symbols** across the tree; **502** are declared in more than one theory.
This is the lookup that answers *"EasyCrypt says `dom` is unknown — what do I
need to require?"*

Ambiguity is preserved deliberately:

```json
"eq_except": ["FMap", "SmtMap"],
"frng":      ["FMap"],
"dbool":     ["DBool"]
```

`eq_except` really is declared by both sides of the r2025.02 split. Picking one
silently is exactly the guess a repair agent must not make, so the consumer is
told to qualify the reference instead.

Omit this section with `--no-symbol-index` (348 KB → 116 KB).

---

## Derived import notes

Where the authors wrote no `import_repair_note`, one is synthesized from
verified facts — deliberately factual and short. It never speculates about
*why* something broke; that is what the authored notes do well and a template
cannot:

```
FSet: `require import FSet.` needs Core, Int, List, StdRing, StdOrder, Finite
in scope first; it also `import`s IntOrder (names brought into scope without a
separate require); declares 163 names (1 abbrev, 3 axiom, 147 lemma, 12 op);
changed in r2025.02, r2025.03 -- see the changelog entries below.
```

Result: **18/18 libraries** now have an import note (4 authored, 14 derived),
up from 4.

---

## Parsing notes

Handled EasyCrypt syntax variants, all present in the real tree:

```easycrypt
require import AllCore SmtMap Finite List FSet Ring StdOrder.
require (*--*) Ring.            (* alignment comment, not a name *)
require export StdOrder.
from Foo require import Bar.
import CoreMap.                 (* bare import: X must already be required *)
clone import Quotient as Q.
clone include MkRO.
  rename "dunifin" as "dbool"   (* introduces `dbool` — see below *)
```

Two decisions worth knowing:

- **Indentation does not disqualify a declaration.** A `theory Foo. … end Foo.`
  block indents its contents and those names *are* exported (as `Foo.name`).
  Proof-internal bindings use different keywords (`have`, `pose`) and never
  match the declaration pattern anyway.
- **`rename "X" as "Y"` inside a clone introduces `Y`.** `DBool.ec` is exactly
  this case — `dbool` is a renamed clone of `Distr.MFinite`'s `dunifin` and is
  declared nowhere with `op`/`lemma`. Without this rule the symbol index could
  not answer the very lookup that `dbool_lib.json`'s hand-written note
  describes.

---

## Cross-check findings

Comparing the authored `requires` prose against the parsed source found **2
genuine disagreements** out of 18 (the other 9 initial hits were a bug in the
comparison, since fixed — the name regex was swallowing sentence-final periods):

| Library | Finding |
|---|---|
| `Distr` | Source has `require import … Discrete.` (`Distr.ec:26`); the prose omits it. **The doc is wrong.** |
| `PROM` | Prose names `FullEager` / `FinEager` as dependencies; they are `theory` blocks declared *inside* `PROM.ec`, not requires. **Prose describes internal structure.** |

Both are reported, not silently corrected. The parsed list is authoritative
(it is what EasyCrypt compiles); the prose is kept because it carries
conditional detail the parser cannot express.

---

## Consumption

`integration/agent/repair_hints.py`:

- `load_repair_docs_index()` — returns `None` (never raises) when the index
  has not been built.
- `get_repair_doc_snippets()` — uses the condensed records when the index
  exists, falling back to the raw authored files otherwise. Scoring gains two
  signals the raw files cannot provide: **the theory declares a matched
  symbol** (weighted highest — it is the direct answer to "where does this
  live") and a hit on the theory's own name.
- `resolve_symbol_theories()` — the symbol → theory lookup.
- `format_repair_hints_for_prompt(..., symbol_theories=...)` — renders the
  resolution **first**, ahead of changelog entries, because it is the most
  directly actionable fact available:

```
Where the names in this step are declared (current EasyCrypt tree):
- `dom` is declared in FMap (`require import FMap.`)
- `frng` is declared in FMap (`require import FMap.`)
- `eq_except` is declared in 2 theories: FMap, SmtMap -- qualify the reference
  or require the one you mean
```

Everything degrades gracefully: a missing index, a missing theory tree, or a
missing `retrieve_entries.py` each cost a signal, never the whole lookup.

---

## Known limitations

- **`clone … with op x <- y` bindings are not resolved.** 127 such lines exist;
  they substitute into a cloned theory rather than introducing a new top-level
  name, so they are out of scope for the symbol index — but a proof referring
  to a clone-local name will not resolve here.
- **No signatures.** The index says `FMap` declares `frng`, not what `frng`'s
  type is. Answering "will this lemma apply?" still needs `-premises`.
- **`theories{}` covers the vendored fork only.** A different EasyCrypt build
  needs a rebuild; nothing detects staleness automatically.
- **Derived notes are templated.** They state verified facts and cannot explain
  a semantic change the way the 4 authored notes do. Writing more authored
  notes remains the highest-value manual work in the corpus.

# `shannon-llm-integration` vs `llm-integration`

**As of:** 2026-07-31 · **Base:** `llm-integration` @ `78ec712d` · **Head:** `f18ce3d7` + uncommitted work

`llm-integration` already carries `integration/` (the agent loop, the experiment
runner, the patched EasyCrypt fork submodule) and `benchmark/`. This branch adds
the **knowledge base** and the machinery that consumes it, and fixes a set of
bugs that made the existing retrieval path either silently ineffective or
actively wrong.

> **Note on `modified-shannonprover/`.** The branch also vendored a 136 MB copy
> of Shannon Prover in `f18ce3d7`. Nothing in `integration/` imports from it —
> the `"Ported from shannon-prover"` docstrings are attribution only — and it is
> being removed. It is excluded from every figure below.

---

## 1. What the branch adds

### 1.1 `proof_corpus/` — the knowledge base (new)

| Artifact | What it is |
|---|---|
| `output/changelog.yaml` | 14 releases, **913 classified entries** — LLM-assigned `kind`, `identifiers`, `summary`, `repair_hint`, `relevance` |
| `output/changelog_index.json` | Derived flat/typed/indexed query form; what retrieval actually reads |
| `output/repair_docs_index.json` | Reprocessed library docs + a tree-wide **`symbol_index` of 6325 symbols across 128 theories** |
| `output/library_history.json` | Git-mined history for 16 standard-library theories: path events + per-release symbol churn |
| `ec_migrations.toml` | **15 import-repair rules** (12 derived from history, 3 curated engine rules) + 16 library records |
| `output/exposure_results.json`, `ladder.md` | Per-repo breaking-change exposure and the ranked corpus ladder |
| Three `*_SCHEMA.md` files | Schema, rationale, and the measurements behind each format |

Derivation scripts, all free to rerun (no network, no LLM):
`build_changelog_index.py`, `build_repair_docs.py`, `analyze_library_history.py`,
`build_ec_migrations.py`.

### 1.2 `integration/agent/import_repair.py` — pre-proof repair (new, ~665 lines)

The branch's main functional addition. Applies version-windowed, evidence-backed,
**line-preserving** rewrites to a `.ec` file before any proof is attempted,
verifying each against EasyCrypt.

### 1.3 Tests (new: 97 tests)

`test_import_repair.py` (33), `test_changelog_index.py` (24),
`test_repair_docs_index.py` (22), `test_process_changelog.py` (18).
Suite: **199 passed**, 2 pre-existing failures in `test_goal_state.py`
(unrelated; they fail identically on the base branch).

---

## 2. Gaps in `llm-integration` this branch closes

### 2.1 Import breakage ended the trial before any repair could happen ⭐

**The most important structural gap.** Import failure is *pre-proof*: when a
`require import` no longer resolves, EasyCrypt cannot load the file at all,
`llm -upto` returns nonzero, and `repair_bootstrap.py` recorded
`skip_reason="goal_unreachable"`. The trial ended before a single tactic was
tried — **none of the changelog evidence ever got a chance to help.**

Closed by `import_repair.py` + `ec_migrations.toml`. Measured on the real
ElGamal corpus (`data/derens99-ElGamal-proof/hashedelgamal.ec`, 2020-era):
first error moves **line 108 → 357 → 453**, stopping at a broken *tactic*
(`invalid 'position' parameter`) rather than a broken import — the intended
boundary.

This also generalizes `corpora/elgamal.py::port_legacy_easycrypt_syntax`, which
hardcoded four fixes for one file. They are now version-pinned manifest rules
applying to any corpus.

### 2.2 The changelog covered 10 of 14 releases

`collect_changelog.py` read only GitHub **release bodies**, and EasyCrypt's are
empty before r2025.02 (r2022.04 = one sentence; r2023.09 and r2024.01 = 0 chars;
r2024.09 = "Release 2024.09"). Four releases contributed nothing.

Closed by `--git-log`, which walks `git log <prev-tag>..<tag>` for releases with
no notes. Recovered **652 commits → 637 classified entries**, taking coverage to
**14/14**. It paid for itself immediately by pinning three migration rules to
releases no release body mentions.

### 2.3 Retrieval matched on English words, not identifiers

`retrieve_entries.score_entries` matched against a raw `identifiers` list
polluted with ordinary prose. Only **14.5%** of entries had anything resolvable,
so retrieval both **missed** real hits and **fired on coincidence** — every `.ec`
file contains the word `list`. The same list fed
`compute_exposure_score.detect_content_bracket_version`, so the noise propagated
into corpus ranking.

Closed by the typed index: names are bucketed into theories / symbols / tactics,
and matching is exact and case-sensitive against the typed buckets.

### 2.4 The richest evidence was collected and then discarded

All 276 PRs in `raw_releases.json` carry `changed_files`, `labels`, and a full
body; 69 touch `theories/`. `process_changelog.py` passed some of it to the
classifier as *context* and then dropped it. A changed file like
`theories/datatypes/FMap.ec` is exact, machine-checked theory scope — strictly
better evidence than a guessed identifier, and exactly what import repair needs.
Now retained and indexed.

### 2.5 "Which theory provides symbol X?" was unanswerable

`-premises` tells the agent what **is** in scope, never what **could** be. The
authored `repair_doc/` covered 18 of 128 theories, so the question failed for
86% of the tree. Closed by `symbol_index` (6325 symbols), read via
`repair_hints.resolve_symbol_theories`. Ambiguity is preserved deliberately —
`eq_except` resolves to both `FMap` and `SmtMap`, the two sides of the r2025.02
split, and the renderer tells the model to qualify rather than guess.

### 2.6 Library facts were prose, not evidence

`repair_doc/*.json` was written by reading current sources and release notes;
its own `caveat` admits *"No true git-diff was possible."*
`analyze_library_history.py` does the diff, so every library rule now carries a
commit SHA and a release tag and is re-checkable with `git show`.

The payoff: the SmtMap→FMap split falls out as **125 declarations** moving in
r2025.02, versus the ~dozen the hand-written note happened to mention — and the
list regenerates when the tree moves.

---

## 3. Bugs found and fixed

Each was found by running the thing, not by reading it.

| Bug | Effect | Fix |
|---|---|---|
| **Error-line regex matched one format** | `_ERROR_LINE_RE` expected `path.ec:108`; EasyCrypt emits `[critical] [path.ec: line 108 (8)]`. Every probe returned −1, so **every migration was silently rolled back** and import repair looked like a no-op | `_ERROR_LINE_RES` handles both; regression test added |
| **Cache poisoning in the classifier** | A chunk whose JSON failed to parse still wrote every entry to `llm_cache.json` as `summary: null`. The cache is consulted before the LLM, so the bad rows **suppressed their own retry permanently** | Only entries whose `needs_llm` actually cleared are cached |
| **Tier A flooded every result slot** | All 6 of a 6-slot budget went to unmatched structural entries; the real SmtMap→FMap hit never appeared | `tier_a_cap` (default `top_n//3`) |
| **Structural entries lost their name match** | `mechanism_change`/`high` entries `continue`d before matching, so the SmtMap entry returned empty overlap | Match all entries first, then route matched structural ones to Tier B with a bonus |
| **`git log --name-status -p`** | Emits name-status *instead of* the patch — **zero symbol events** across all 16 libraries | Two separate log passes |
| **Tag ordering by commit date** | r2026.07's tagged commit is dated 2026-05-15, *before* r2026.06's — releases sorted wrong | Sort by tag name (`rYYYY.MM` sorts lexicographically) |
| **`--follow` can't chase a theory split** | Git records it as delete+add, so SmtMap showed 1 commit instead of 34 | Basename glob pathspec |
| **`module type X = {` captured `type` as the name** | Produced a spurious `require import PROM` on ElGamal | `(?:type\s+)?` in `_DECL_RE` + keyword blocklist |
| **`version_diffs_found` string/list mismatch** | A list in 5 of 18 repair_doc files, a bare sentence in 13 — iterating the string yielded one character per step | `_as_list` normalizer |
| **Pervasive/Logic in export-gap rules** | The engine auto-exports them (`ecCommands.ml:876`), so telling a proof to require them is wrong advice | `ENGINE_PRELOADED` blocklist |
| **"Strict improvement" rollback criterion** | A rule fixing line 300 shows no movement while a parse error sits at 108, so correct rules were reverted | Changed to **non-regression** |
| **TOML writer didn't escape backslashes** | `distr_lib.json` documents `\in`/`\notin` | Caught by the generator's own round-trip check |
| **`/eval/` not gitignored** | `compute_exposure_score.py` clones into `./eval/<name>` relative to CWD; running it from the repo root leaves **376 MB** of un-ignored clones | Added `/eval/` to `.gitignore` |

### Known-broken, not yet fixed

**`_build_spec` drops `replay_bootstrap`** ([`integration/experiment/__main__.py`](../integration/experiment/__main__.py)).
The CLI silently falls back to mutation mode, so **none of the repair machinery
above actually runs in an experiment**. Verified at runtime. This is the single
highest-leverage next action — everything else is downstream of it. See
`PROOF_REPAIR_HANDOFF.md` §6.2 and §7 W1.

---

## 4. Consequence worth knowing

Filling the four undocumented releases changed **44 of 77** breaking-change
exposure scores and moved **54 of 75** ladder ranks. Repos predating r2025.02
used to cross four *empty* releases and score nothing for them; they now score
382 → **1211**.

The corpus ladder had been systematically **under-weighting the oldest, most-broken
repos** — exactly the population this project exists to repair. Any corpus
selection made off the previous ladder should be re-checked.

---

## 5. Reproducing the artifacts

```bash
# Free — no network, no LLM. Order matters.
python3 proof_corpus/scripts/analyze_library_history.py   # needs the EC clone
python3 proof_corpus/scripts/build_changelog_index.py     # consumes changelog.yaml
python3 proof_corpus/scripts/build_repair_docs.py
python3 proof_corpus/scripts/build_ec_migrations.py       # consumes library_history.json

# Costs money (Message Batches API; cached in llm_cache.json)
python3 proof_corpus/scripts/collect_changelog.py --repo EasyCrypt/easycrypt \
    --out proof_corpus/output/raw_releases.json --with-pr-details \
    --git-log integration/extern/easycrypt
python3 proof_corpus/scripts/process_changelog.py \
    --in proof_corpus/output/raw_releases.json \
    --out proof_corpus/output/changelog.yaml \
    --cache proof_corpus/output/llm_cache.json

# Rerun after ANY classification pass — these read the changelog
cd proof_corpus && python3 scripts/compute_exposure_score.py \
    --csv scripts/repositories.csv --changelog output/changelog_index.json \
    --target-version r2026.07 --easycrypt-repo ../integration/extern/easycrypt \
    --out output/exposure_results.json
python3 scripts/rank_repos.py --in output/exposure_results.json --out output/ladder.md
```

# `proof_corpus/` — the knowledge base

Static, versioned facts about **what changed between EasyCrypt releases**, plus
the scripts that derive them and the query APIs the harness reads.

Nothing here calls an LLM at *query* time. One script (`process_changelog.py`)
calls one at *build* time, and one (`collect_changelog.py`) needs the network.
Everything else is a pure derivation and free to rerun.

---

## Contents

| Path | What it is | Rebuilt by |
|---|---|---|
| `output/raw_releases.json` | Raw GitHub release bodies, PR labels/`changed_files`, plus git-log commits for releases with no notes | `collect_changelog.py` (network) |
| `output/changelog.yaml` | **Source of record.** 14 releases, 913 classified entries | `process_changelog.py` (**paid**) |
| `output/llm_cache.json` | Classification cache keyed by `(repo, id, title)` | `process_changelog.py` |
| `output/changelog_index.json` | Flat/typed/indexed query form — **what retrieval actually reads** | `build_changelog_index.py` |
| `output/library_history.json` | Git-mined history for 16 theories: path events + per-release symbol churn | `analyze_library_history.py` |
| `output/repair_docs_index.json` | Condensed library docs + tree-wide `symbol_index` (6325 symbols / 128 theories) | `build_repair_docs.py` |
| `ec_migrations.toml` | 15 import-repair rules + 16 library records | `build_ec_migrations.py` |
| `output/exposure_results.json`, `output/ladder.md` | Per-repo breaking-change exposure and the ranked corpus ladder | `compute_exposure_score.py` → `rank_repos.py` |
| `repair_doc/*.json` | 18 **authored** library docs + `tactics_ref.json`. Hand-written; not generated | — |

Schemas and rationale: [`CHANGELOG_INDEX_SCHEMA.md`](CHANGELOG_INDEX_SCHEMA.md),
[`REPAIR_DOCS_INDEX_SCHEMA.md`](REPAIR_DOCS_INDEX_SCHEMA.md),
[`EC_MIGRATIONS_SCHEMA.md`](EC_MIGRATIONS_SCHEMA.md).

---

## Run order

Stages are numbered by dependency. **Order matters** — every stage after 1 reads
an earlier stage's output, and running one out of order silently produces a
stale artifact rather than an error.

```
  1. collect_changelog.py          network      raw_releases.json
           │
  2. process_changelog.py          PAID         changelog.yaml + llm_cache.json
           │
  3. build_changelog_index.py      free         changelog_index.json
           │
           ├──────────────┬──────────────────┬─────────────────────┐
           ▼              ▼                  ▼                     ▼
  5. build_repair_docs  6. build_ec_      7. compute_exposure_   (retrieval
     _docs.py              migrations.py      score.py            reads #3)
     repair_docs_          ec_migrations      exposure_results
     index.json            .toml                    │
           ▲                    ▲                   ▼
           │                    │            8. rank_repos.py
           │            4. analyze_library      ladder.md
           │               _history.py
           │               library_history.json
      repair_doc/*.json
      (authored)
```

Stage 4 is independent of the changelog — it only needs the EasyCrypt clone —
so it can run any time before stage 6.

### The commands

```bash
# --- 1. Collect (network; needs GITHUB_TOKEN in .env, and `pip install requests`)
python3 proof_corpus/scripts/collect_changelog.py \
    --repo EasyCrypt/easycrypt \
    --out proof_corpus/output/raw_releases.json \
    --with-pr-details \
    --git-log integration/extern/easycrypt

# --- 2. Classify (COSTS MONEY; needs ANTHROPIC_API_KEY)
#   Submits one Message Batches job and polls. Cached, so reruns are near-free.
#   --skip-llm verifies the plumbing without spending; --sync uses blocking
#   per-chunk calls at full price.
python3 proof_corpus/scripts/process_changelog.py \
    --in proof_corpus/output/raw_releases.json \
    --out proof_corpus/output/changelog.yaml \
    --cache proof_corpus/output/llm_cache.json

# --- 3-6. Derivations (free: no network, no LLM)
python3 proof_corpus/scripts/build_changelog_index.py
python3 proof_corpus/scripts/analyze_library_history.py   # needs the EC clone; slow
python3 proof_corpus/scripts/build_repair_docs.py
python3 proof_corpus/scripts/build_ec_migrations.py

# --- 7-8. Corpus ranking. Run from proof_corpus/ so clones land in the
#   gitignored proof_corpus/eval/ rather than a second copy at the repo root.
cd proof_corpus
python3 scripts/compute_exposure_score.py \
    --csv scripts/repositories.csv \
    --changelog output/changelog_index.json \
    --target-version r2026.07 \
    --easycrypt-repo ../integration/extern/easycrypt \
    --out output/exposure_results.json
python3 scripts/rank_repos.py \
    --in output/exposure_results.json \
    --out output/ladder.md
```

### If you only reclassify

Stages 3, 5, 6, 7, 8 all read the changelog. **Rerun all of them**, in that
order. Skipping stage 7 is the easy mistake: the exposure scores and the corpus
ladder are derived from the changelog, and a stale ladder silently misranks
which repos are worth experimenting on.

### If you only re-mine git history

Rerun stage 6 only.

---

## What the agent loop should read

**None of this is queried directly.** Everything reaches the harness through two
modules in `integration/agent/`, which own the file formats so a schema change
surfaces as a clean exception rather than a silent mismatch.

### `repair_hints.py` — retrieval

| Function | Answers |
|---|---|
| `get_repair_hints_text(...)` | **The entry point.** Returns `(text, notes, matched_version)`. Never raises — hints are optional context, so failures come back as `notes` |
| `get_changelog_repair_hints_by_release(...)` | "What changed, walking releases oldest-first, stopping at the first hit?" — the release-order hop |
| `resolve_symbol_theories(names)` | "Which theory provides `X` today?" — the question `-premises` cannot answer, since it reports what *is* in scope, never what *could* be |
| `get_repair_doc_snippets(...)` | Per-library reference notes scored by token/path overlap |
| `format_repair_hints_for_prompt(...)` | Renders the above into the flat prompt (there is no structured view layer) |

`retrieve_entries.py` is loaded **dynamically** by `repair_hints.py`, never
vendored — that is deliberate, so format drift raises `RepairHintsUnavailable`
instead of misbehaving quietly. Do not copy it into `integration/`.

Its typed query helpers are the ones worth building on:
`entries_for_symbols`, `entries_for_theories`, `entries_for_tactics`,
`import_relevant_entries`, `coverage_gap`, `prompt_ready`.

### `import_repair.py` — pre-proof rewriting

Reads `ec_migrations.toml` and applies version-windowed, **line-preserving**
rewrites to a `.ec` file, verifying each against EasyCrypt. Entry point
`repair_imports(...)`; `format_for_prompt(result)` tells the solver what was
rewritten, since it is proving against a file the harness edited.

> **Line numbers are load-bearing.** `ProofCase` records absolute lemma line
> numbers, so `apply_actions` **asserts** the line count is unchanged. Any new
> action that inserts or deletes a line must reindex `ProofCase` too.

---

## Wiring status — read this before assuming it runs

Retrieval and import repair are **implemented and tested**, but the production
path that would invoke them is broken:

- `AgentConfig.changelog_hints` is populated in exactly one place —
  `integration/experiment/repair_bootstrap.py`. That module runs only in
  `replay_bootstrap` mode.
- `_build_spec` / `_with_sandbox_dir` in `integration/experiment/__main__.py`
  rebuild `ExperimentSpec` without carrying `replay_bootstrap`, and
  `runner._experiment_mode` has no branch for it. `--spec
  elgamal-changelog-repair` therefore reports mode `mutation`.

So **a CLI-launched experiment currently reads none of this.** Verified:

```
spec                        registry  _build_spec  _with_sandbox  _experiment_mode
elgamal-changelog-repair    True      False        False          'mutation'
```

Until that is fixed, the working entry points are the standalone CLIs:

```bash
python3 -m integration.agent.import_repair FILE.ec \
    --source-version r2022.04 --target-version r2026.07 [--write]

python3 proof_corpus/scripts/retrieve_entries.py \
    --changelog proof_corpus/output/changelog_index.json \
    --proof broken.ec --source-version r2022.04 --target-version r2026.07 --top-n 12
```

See [`docs/PROOF_REPAIR_HANDOFF.md`](../docs/PROOF_REPAIR_HANDOFF.md) §6.2 and
§7 W1.

---

## Conventions

- **Never vendor these scripts.** Load them dynamically and validate the
  expected attributes.
- **EasyCrypt is the oracle.** Every proposed edit is validated by running the
  binary; no heuristic accepts a change on its own.
- **Repair hints must degrade gracefully.** `get_repair_hints_text` never
  raises. Preserve that.
- **Prefer derived over authored.** `repair_doc/*.json` was written by reading
  sources and release notes — its own `caveat` admits *"No true git-diff was
  possible."* `analyze_library_history.py` does the diff, so anything it can
  establish should come from there, carrying a commit SHA and release tag.

# AI4EC Proof-Repair Handoff

**Branch:** `shannon-llm-integration` · **As of:** 2026-07-30 · **Audience:** engineers
picking up EasyCrypt proof-repair work (`integration/`, `proof_corpus/`).

This document is written to be pasted into (or referenced by) future task
instructions. It describes **what exists today**, **what is verified broken**,
and **what to build next**, with a bias toward the weakest part of the system:
**repairing EasyCrypt `require import` / theory-level breakage.**

Everything below was read from the working tree on this branch. Where a claim is
a design intention rather than verified behavior, it says so.

---

## 0. Ten-second orientation

The project answers one research question: **when an old EasyCrypt proof no
longer compiles against a modern EasyCrypt, can an LLM agent repair it, and does
giving it dated, sourced facts about what changed between releases help?**

Three codebases collaborate:

| Directory | Role | Owned by us? |
|---|---|---|
| `integration/` | The **primary harness**: a self-contained LLM agent loop + experiment runner around a patched EasyCrypt binary. This is where new work usually goes. | Yes |
| `proof_corpus/` | The **knowledge base**: a structured EasyCrypt release changelog, per-library reference docs, mined library history, and the migration manifest. Static data + derivation/retrieval scripts. | Yes |
| `data/`, `.clone/`, `benchmark/` | Corpus acquisition: 60+ cloned EasyCrypt repos, extracted `.ec` files, and a lemma index. | Yes |

> **Design lineage.** Several modules carry `"Ported from shannon-prover's …,
> reimplemented because …"` docstrings — `integration/agent/repair_hints.py`,
> `integration/experiment/repair_bootstrap.py`, `protocols.py`. Shannon Prover
> is a *separate published system* (arXiv:2607.02847) with a manager/MCP
> architecture; only its knowledge-retrieval half was ever ported here, and it
> was rewritten rather than copied because its EasyCrypt session model is
> incompatible with ours (it uses a persistent daemon whose `commit_tactic`
> always exits 0; we use a stateless `ec.exe llm` subprocess whose returncode
> **is** ground truth). **Nothing in `integration/` imports from it** — those
> docstrings are attribution, not a dependency, and the environment variable
> `SHANNON_PROOF_CORPUS_DIR` is just a name. You do not need that codebase to
> work here.

Plus `integration/extern/easycrypt` — a git submodule pointing at
`KevinLeeFM/easycrypt` branch `llm-integration`, a fork of EasyCrypt carrying a
patched `llm` subcommand. **The entire harness depends on this fork**; stock
EasyCrypt will not work.

### 0.1 Status as of 2026-07-30

| Capability | State |
|---|---|
| Changelog coverage | ✅ **14/14 releases**, 913 classified entries (was 10/14, 276) |
| Symbol → theory resolution | ✅ 6325 symbols across 128 theories |
| Machine-readable theory deps | ✅ `import_repair_note` on 18/18 libraries |
| Library history mined from git | ✅ 16 theories, per-release symbol churn |
| **Pre-proof import repair** | ✅ Built and measured — §4 |
| Hints threaded per-step | ❌ Still frozen at bootstrap — §7 W3 |
| Import-repair audit artifact | ✅ Per-trial `import_repair.json` written; ❌ never aggregated — §7 W8 |
| Version detection | ❌ Still hardcoded `r2022.04`/`r2026.07` — §7 W6 |
| Repair-specific metrics | ❌ Nothing reported — §7 W8 |
| **`replay_bootstrap` reaching the runner** | ❌ **Broken** — §6.2, and it is the first thing to fix |

The single highest-leverage next action is **§7 W1**: `replay_bootstrap` is
silently dropped by `_build_spec`, so the CLI falls back to mutation mode and
none of the repair machinery above actually runs in an experiment. Everything
else in this document is downstream of that.

---

## 1. The EasyCrypt interface (read this first)

`integration/agent/easycrypt.py` is the *only* place the harness talks to
EasyCrypt. It shells out to `ec.exe llm ...` — a subcommand added by the fork
(`integration/extern/easycrypt/src/ec.ml`, around lines 419–802).

Three calls, and that is the whole surface:

| Wrapper | Command | Purpose |
|---|---|---|
| `fetch_goal` ([easycrypt.py:42](../integration/agent/easycrypt.py#L42)) | `llm -upto N FILE.ec` | Print the proof state as of line `N` |
| `fetch_goal_and_premises` ([easycrypt.py:46](../integration/agent/easycrypt.py#L46)) | `llm -upto N -premises FILE.ec` | Same, plus a dump of every in-scope axiom/lemma (`EcEnv.Ax.all`) after a `(* --- premises --- *)` separator |
| `validate_file` ([easycrypt.py:55](../integration/agent/easycrypt.py#L55)) | `llm -lastgoals FILE.ec` | Compile the whole file; **`returncode != 0` is a trustworthy per-tactic failure signal** |

Facts worth internalizing:

- `-premises` **requires** `-upto` (enforced at `ec.ml:618`).
- Premise catalog keys are EasyCrypt-qualified paths — `Theory.basename`, e.g.
  `RField.exprM`, `Ring.IntID.exprM` — parsed by
  [`premises.py:56 parse_premises`](../integration/agent/premises.py#L56). This
  was a bug fix (see `CHANGELOG.md` 2026-07-25): bare basenames collided across
  theories.
- Section-local lemmas appear with a `local ` prefix and are accepted by the
  parser (commit `78ec712d`).
- The binary path resolves from `$EASYCRYPT`, else
  `integration/extern/easycrypt/_build/default/src/ec.exe`
  ([config.py:15](../integration/agent/config.py#L15)). It **is** built in this
  tree.
- Goal resolution is *not* trivial. `resolve_goal`
  ([easycrypt.py:99](../integration/agent/easycrypt.py#L99)) walks the cursor
  backwards and has explicit workarounds for two EasyCrypt behaviors: the
  post-`proc` goal is hidden until `skip` is applied (`_probe_post_proc_goal`),
  and a discharged proof shows an empty goal until `qed.` is written
  (`_probe_qed_discharge`). Both probe by writing a temp file. Do not "simplify"
  this without reproducing those two cases.

---

## 2. `integration/agent/` — the agent loop

Entry point: `run_agent(source, config, work_copy)` at
[loop.py:174](../integration/agent/loop.py#L174) (~1500 lines; the single
biggest file). CLI: `python3 -m integration.agent FILE.ec`.

### 2.1 Control flow

```
create working copy (.agent.ec)  →  build premise index (embeddings)
  └─ per step (max_steps, default 200):
       resolve current goal  →  rank premises by cosine similarity
       build prompt          →  LLM returns ONE JSON action
       dispatch action:
         tactic        → append to file, `llm -lastgoals`, keep or roll back
         undo          → remove N trailing tactics
         lookup_lemma  → exact name lookup in the Ax.all catalog
         search_lemmas → semantic / substring / prefix / exact search
       update stuck counter, error history, trajectory log
```

The agent **edits a real `.ec` file and re-runs EasyCrypt every step.** There is
no in-memory proof state and no daemon. A failed tactic is physically removed
from the file (`proof.remove_lines`, [loop.py:707](../integration/agent/loop.py#L707)).

### 2.2 Guardrails (all learned from real failures — see `CHANGELOG.md`)

These exist because frontier models repeatedly resubmitted the same failing
tactic 17–19 times. Do not remove them casually.

| Guardrail | Where | Behavior |
|---|---|---|
| Duplicate hard-reject | [loop.py:653](../integration/agent/loop.py#L653) | A tactic that already failed at this goal (after normalization) is rejected **without calling EasyCrypt** |
| Tactic normalization | [`error_history.normalize_tactic`](../integration/agent/error_history.py#L28) | Collapses whitespace, trailing `.`, and `&&`/`\|\|` ↔ `/\`/`\/` |
| Identical-fail abort | `identical_fail_limit=3` | Exit `STUCK` after 3 identical failures at one goal |
| Weighted stuck counter | `repeat_stuck_weight=2` | Repeats advance the stuck counter faster than novel failures |
| Retrieval budget | `max_continuous_searches=5` | Warn on the 4th consecutive lookup/search, reject from the 6th; resets on a tactic or undo |
| No-op undo cap | `max_consecutive_noop_undos=3` | Undo that removes nothing 3× in a row ⇒ `STUCK` |
| Compound-tactic diagnostics | `_probe_prefix_subgoal` [loop.py:1122](../integration/agent/loop.py#L1122) | On a failed `a; b; c.`, replays each segment and reports the goal after each successful prefix |

### 2.3 Prompt assembly

[`build_prompt`](../integration/agent/prompt.py#L422) concatenates flat text
sections in a fixed order. **There is no structured view layer** — every piece
of evidence has to be rendered into a string by whoever produces it, which is
why retrieval modules expose `format_*_for_prompt` helpers rather than typed
view objects.

Order: static rules (goal-form reading, program-logic tactic menu, simplify
rule, lemma-search rule, anti-loop rule, rollback rule) → search warning →
**repair hint** → **`## Known EasyCrypt library changes`** ← *this is where
changelog/repair-doc facts land* → informal proof / broken formal proof →
few-shots → current goal → active goal-shape hints → top-k premises → failed
tactics + banned list → recent failures at earlier goals → recent reasoning →
proof tail → lookup results → tool spec.

### 2.4 Providers and cost

`AgentConfig` ([config.py:44](../integration/agent/config.py#L44)) supports two
chat providers: LM Studio (local, default) and DeepSeek (paid). **Embeddings
always go to LM Studio**, even with `--deepseek`. Token usage is tracked per
call and priced from a snapshot in
[`pricing.py`](../integration/agent/pricing.py).

> ⚠️ **Hard rule, from [`AGENTS.md`](../AGENTS.md): never answer the DeepSeek
> confirmation prompt on the user's behalf.** No piping `YES`, no pty tricks, no
> background runs. Give the user the command and stop. There is deliberately no
> `--yes` flag.

---

## 3. `integration/experiment/` — the experiment runner

`python3 -m integration.experiment run --spec <name> ...`. Full flag table in
[`integration/experiment/README.md`](../integration/experiment/README.md).

### 3.1 The four specs

Registered in [`specs.py`](../integration/experiment/specs.py):

| Spec | Corpus | What the solver is given | Dispatch |
|---|---|---|---|
| `joy-tactic-repair` | Joy of EasyCrypt (working proofs) | A *mutated* (deliberately broken) tactic script as `repair_hint` | mutation path, `runner.py` |
| `joy-informal-repair` | Joy of EasyCrypt | An LLM-written **natural-language** sketch + a red-herring-salted lemma manifest; never the real tactics | `run_informal_trial` |
| `elgamal-broken-repair` | `derens99/ElGamal-proof` (**genuinely** broken, 2020-era) | The corpus's own non-compiling tactic script verbatim; full ambient premise catalog | `run_broken_formal_trial` |
| `elgamal-changelog-repair` | same as above | **Replay-until-failure** + changelog/repair-doc hints | `run_replay_bootstrap_trial` |

The first three *simulate* breakage or reconstruct from scratch. The fourth is
the one this branch exists for.

### 3.2 `elgamal-changelog-repair` — the replay-bootstrap mode

[`repair_bootstrap.py::run_replay_bootstrap_trial`](../integration/experiment/repair_bootstrap.py#L64):

1. Check the target goal is reachable (`fetch_goal_and_premises`). If not →
   `SKIPPED`, `skip_reason="goal_unreachable"`. **Remember this branch — §6.1.**
2. Split the lemma's original tactic script on `.<whitespace>` (preserves dots
   inside identifiers like `G1.bad`) — `_original_tactics`, line 45.
3. Replay tactic-by-tactic: `append_tactic` → `validate_file`. Stop at the first
   nonzero return code. This preserves the **longest still-valid prefix** of the
   original proof.
4. If all tactics replay → `COMPLETE` with **zero LLM calls**. This is the cheap
   win; it is also the honest measurement of "how much of the old proof still
   holds."
5. Otherwise: fetch changelog + repair-doc hints for *that specific failing
   tactic and error*, and hand off to `run_agent` starting from the
   partially-replayed file (not an admitted/empty goal).

Artifacts written per trial: `original.ec`, `agent_start.ec`,
`agent_work.agent.ec`, `bootstrap_result.json` (accepted/total/failed tactic),
`repair_hints_hop.json` (which release the changelog hop landed on),
`repair_hints_notes.json` (degrade-gracefully notes), `agent_log.json`.

### 3.3 Corpus preparation (the part that already touches imports)

[`corpora/elgamal.py`](../integration/experiment/corpora/elgamal.py) draws a
line between two kinds of breakage:

1. **Syntax/API drift** — mechanical, must be fixed before *any* tool can even
   load the file. Handled offline by `port_legacy_easycrypt_syntax`
   ([elgamal.py:62](../integration/experiment/corpora/elgamal.py#L62)):
   `SmtMap`→`FMap`, drop `proc *`, `declare module X : Y`→`X <: Y`, prepend
   `pragma +old_mem_restr.`. **Every edit is line-number-preserving** (fixes are
   folded onto line 1) so the corpus index's lemma line numbers stay valid.
2. **Genuinely broken tactic scripts** — the actual object of study.

Every lemma *before* the target has its body replaced with `admit.`
(`admit_prior_lemmas`) — a safe over-approximation of "the target's
dependencies are proven," valid because the file is a linear lemma sequence.

---

## 4. `integration/agent/import_repair.py` — the pre-proof pass ⭐

**Added 2026-07-30.** This is the subsystem that closes the gap §6.1 describes,
and it is where import work now belongs.

The problem it solves is that import breakage is *pre-proof*. When a
`require import` no longer resolves, EasyCrypt cannot load the file at all,
`llm -upto` returns nonzero, and `repair_bootstrap.py` records
`skip_reason="goal_unreachable"` — the trial ends before a single tactic is
tried, and none of the changelog evidence ever gets a chance to help.

### 4.1 How it runs

CLI: `python3 -m integration.agent.import_repair FILE.ec --source-version
r2022.04 --target-version r2026.07 [--write]`. Called from the
`goal_unreachable` branch of `repair_bootstrap.py`.

1. **Probe.** `validate_file` on the untouched file. If it already loads, stop —
   no rules are applied to a healthy file.
2. **Select.** Rules from `proof_corpus/ec_migrations.toml` whose
   `(source, target]` window the file crosses. If either endpoint is unknown to
   the manifest, *every* rule is considered — EasyCrypt's releases only reach
   back to r2022.04, so a 2020-era proof legitimately has no source tag. Same
   fail-open convention `releases_in_range` uses.
3. **Match.** Filter by the `[migration.match]` conditions and
   `--min-confidence`.
4. **Apply in bulk, probe once.** One EasyCrypt call for the common case.
5. **Otherwise apply incrementally**, probing after each rule and keeping it
   unless EasyCrypt stops *earlier* than before. **Non-regression, not strict
   improvement** — every rule is independently evidence-backed and
   line-preserving, so the question is "does this hurt?"; a rule fixing
   something at line 300 shows no movement while an unrelated parse error sits
   at line 108.
6. **Report.** `error_line_before` / `error_line_after` measure partial
   progress; `format_for_prompt` tells the solver what was rewritten, since it
   is proving against a file the harness edited.

The source file is never modified — work happens on a copy; the caller
promotes.

### 4.2 Line numbers are load-bearing

`protocols.py::ProofCase` records **absolute lemma line numbers**, so every
action is line-preserving: `add_require` extends an existing line, `add_pragma`
folds onto line 1, `remove_require` blanks rather than deletes, and the rest are
in-place substitutions. `apply_actions` **asserts** the line count is unchanged
and raises if a rule violates it. Do not add an inserting or deleting action
without also reindexing `ProofCase`.

### 4.3 Where the rules come from

Derived from **commit history**, not prose:

```
<easycrypt clone with tags>
   └─▶ scripts/analyze_library_history.py  →  output/library_history.json
          └─▶ scripts/build_ec_migrations.py (+ theory sources, + curated engine rules)
                 └─▶ ec_migrations.toml  ──▶  integration/agent/import_repair.py
```

`repair_doc/*.json` was written by reading current sources and release notes;
its own `caveat` says *"No true git-diff was possible."* `analyze_library_history.py`
does the diff over 16 tracked theories, so every library rule carries a commit
SHA and a release tag and is re-checkable with `git show`.

The payoff: the SmtMap→FMap split falls out as **125 declarations** leaving
`SmtMap` and arriving in `FMap` in r2025.02, versus the ~dozen names the
hand-written note happened to mention — and the list regenerates when the tree
moves. Parser and module-system changes (`proc *`, `declare module X : T`,
memory restrictions) are *engine* changes no theory history reveals, so they
live in `CURATED_ENGINE_MIGRATIONS` in the generator; their version pins came
from `collect_changelog.py --git-log`.

Full schema: [`proof_corpus/EC_MIGRATIONS_SCHEMA.md`](../proof_corpus/EC_MIGRATIONS_SCHEMA.md).

### 4.4 Measured result

`data/derens99-ElGamal-proof/hashedelgamal.ec` (2020-era, genuinely broken):

Re-measured 2026-07-31 against the current 15-rule manifest:

```
first error: line 108  →  line 357  →  line 453
considered 4, kept 4, rejected 0:
  smtmap-symbols-moved-to-fmap-r2025.02   (symbol_moved, 125 symbols, derived)
  proc-star-removed                       (108 → 357)
  declare-module-ascription               (no regression, still 357)
  old-module-restriction-sets             (357 → 453)
```

Only 4 of the 15 rules are even *considered* — the other 11 fail their
`[migration.match]` conditions on this file, which is the manifest working as
intended. Note `declare-module-ascription` is kept on **non-regression**, not
on movement; under the original "strictly decreases" criterion it would have
been rolled back (§7 W4.4).

The remaining error at 453 is `invalid 'position' parameter` on a `seq 1 1 :`
**tactic** — a genuinely broken proof, not an import problem. That is the
intended boundary: import repair gets the file loadable through its
declarations and stops where tactic-level repair begins.

The three `syntax_change` rules are exactly what
[`corpora/elgamal.py::port_legacy_easycrypt_syntax`](../integration/experiment/corpora/elgamal.py)
hardcodes for one file. They are now manifest rules with version pins, applying
to any corpus — **that function is now redundant and should be deleted** once a
second corpus confirms the general path.

### 4.5 Known limitations

- **15 migrations** — 12 derived from git history, 3 curated engine rules;
  by kind: 9 `require_semantics`, 3 `syntax_change`, 1 each `symbol_moved` /
  `theory_added` / `theory_renamed`. Confidence: 3 high, 11 medium, 1 low.
  Only the ones with hard evidence. A rule with no `breaks_at` is still useful.
- **Only one symbol-level move.** `smtmap-symbols-moved-to-fmap-r2025.02`
  carries all 125 moved declarations, but it is the sole `symbol_moved` rule —
  `repair_docs_index.json`'s `symbol_index` (6325 symbols) has the data to
  generate more, and nothing consumes it for this purpose yet.
- **9 of 15 rules are AllCore export-gap rules** of the same shape. They are
  cheap and correct, but the manifest's *variety* of evidence is thinner than
  the rule count suggests.
- **`match` is AND-only.** Express alternatives as separate rules.
- **Regex actions are unanchored** — they apply to comments and strings too.
- **Progress is measured by first-error line**, so a rule that fixes one error
  and introduces another later reads as progress.

---

## 5. `proof_corpus/` — the knowledge base

Static data + retrieval scripts. No LLM at query time.

> **Update (2026-07-30):** the changelog now has a second, derived form —
> `output/changelog_index.json`, built by `scripts/build_changelog_index.py`.
> It is what retrieval reads by default. §5.1–5.2 below describe the authored
> source format, which is unchanged and still the record of truth; §5.5
> describes the derived index. Full schema and rationale:
> [`proof_corpus/CHANGELOG_INDEX_SCHEMA.md`](../proof_corpus/CHANGELOG_INDEX_SCHEMA.md).

### 5.1 `output/changelog.yaml` (435 KB)

14 releases, `r2022.04` → `r2026.07`, **913 entries** — 276 from GitHub release
notes and 637 recovered from git log for the four releases that shipped with no
notes (§7 W5b). Schema:

```yaml
repo: ...
releases:
  - version: r2025.02
    published_at: "..."
    entries:
      - id: "605"                # upstream PR number
        title: "split SmtMap into SMT Array and finite map"
        kind: mechanism_change   # tactic_change | syntax_change | lemma_added
                                 # lemma_removed | lemma_renamed | lemma_changed
                                 # internal | documentation
        identifiers: [SmtMap, FMap, ...]
        summary: "..."
        repair_hint: "..."       # LLM-authored, actionable
        relevance: high          # high | medium | low
```

Distribution after the full classification pass (2026-07-30): `internal` 273,
`lemma_added` 193, `mechanism_change` 172, `tactic_change` 161,
`syntax_change` 64, `documentation` 20, and 30 lemma rename/remove/change
entries. Relevance: **155 high, 391 medium, 367 low**. No entry is
unclassified.

Classification is `scripts/process_changelog.py`: cheap regex rules handle the
obvious `internal`/`ci`/`docs` bullets for free, and everything else goes to
Claude through the **Message Batches API** — all unclassified entries across
every release are chunked once and submitted as a single job, with results
matched back by `custom_id` (they return unordered). Results are cached in
`output/llm_cache.json` keyed by `(repo, id, title)`, so reruns are nearly free.
`--sync` restores blocking per-chunk calls; `--skip-llm` verifies the plumbing
without spending.

### 5.2 `scripts/retrieve_entries.py` — the matcher

Loaded **dynamically** (`importlib.util.spec_from_file_location`), never
vendored, so schema drift surfaces as a clean `RepairHintsUnavailable` rather
than a silent mismatch. Required functions: `load_changelog`,
`tokenize_proof`, `releases_in_range`, `score_entries`.

Three-tier ranking (`score_entries`, line 84):

- **Tier A (always kept):** `kind == mechanism_change` **and** `relevance == high`.
  Rationale: structural changes (cloning, imports, module system) break proofs
  *without the proof textually mentioning anything that changed*, so identifier
  overlap cannot find them.
- **Tier B (kept on match):** exact, case-sensitive token overlap between the
  entry's `identifiers` and tokens from the query text. Deliberately no
  substring/fuzzy matching (avoids `map` matching `map1`). A `GENERIC_TOKENS`
  stoplist drops `proof`, `lemma`, `apply`, `import`, `require`, …
- **Tier C (dropped):** `relevance == low`, or `kind` in `{internal, documentation}`.

Sorted Tier A first, then Tier B by `(overlap count, proximity to target)`.

### 5.3 `repair_hints.py` — the ported retrieval layer

[`integration/agent/repair_hints.py`](../integration/agent/repair_hints.py).
Public entry point: `get_repair_hints_text(...) -> (text, notes, matched_version)`.
**It never raises** — hints are optional supplementary context; failures come
back as `notes` for logging.

Two matchers:

- **Changelog, release-order hop** — `get_changelog_repair_hints_by_release`
  ([line 144](../integration/agent/repair_hints.py#L144)). Walks releases
  **oldest-first** within `(source, target]`, skipping `already_consumed_versions`,
  and returns entries from the **first release with a hit**. The older flat
  lookup `get_changelog_repair_hints` is retained at line 113 but unused by the
  prompt path. Why hopping matters: when an identifier was renamed twice across
  a long span, a flat lookup returns both renames with no ordering; hopping
  surfaces the earlier one first, matching the order a human porting
  release-by-release would hit them.
- **repair_doc, token/path overlap** — `get_repair_doc_snippets`
  ([line 210](../integration/agent/repair_hints.py#L210)). Scores `*_lib.json`
  docs by `len(token overlap) + 100 * (identifier appears in the doc's path)`.
  Top 3.

`SHANNON_PROOF_CORPUS_DIR` overrides the corpus root (used by the tests to point
at `integration/tests/fixtures/repair_hints/`).

> **Update (2026-07-30):** these authored files now have a derived counterpart,
> `output/repair_docs_index.json`, built by `scripts/build_repair_docs.py` —
> summaries condensed, `requires` re-derived from the real sources, an
> `import_repair_note` on all 18 libraries, facts for all 128 theories, and a
> tree-wide `symbol_index`. Retrieval prefers it. Schema:
> [`proof_corpus/REPAIR_DOCS_INDEX_SCHEMA.md`](../proof_corpus/REPAIR_DOCS_INDEX_SCHEMA.md).

### 5.4 `repair_doc/*.json` — per-library reference notes ⭐

**18 library docs + 1 tactics reference.** These are the single most
import-relevant asset in the repo, and they are currently the most underused.

```json
{
  "path": "theories/datatypes/SmtMap.ec",
  "confidence": "verified_current_source (whole file, 102 lines)",
  "caveat": "No true git-diff was possible ...",
  "current_content_summary": "POST-SPLIT content: ... total maps ...",
  "import_repair_note": "MAJOR STRUCTURAL CHANGE, r2025.02 (#605) ...",
  "requires": "AllCore, CoreMap.",
  "version_diffs_found": ["r2025.02: ...", ...]
}
```

Two fields deserve special attention:

- **`import_repair_note`** — present on 4 of 18 docs (`smtmap`, `allcore`,
  `prom`, `dbool`). These are written *specifically* for import repair. Example
  (`allcore_lib.json`): *"AllCore does NOT pull in List, Distr, FSet, FMap …
  If a proof assumes `require import AllCore.` alone gives it List/Distr lemmas,
  that assumption is wrong under the current tree and the missing theory must be
  required explicitly."* **This field is never rendered into any prompt.** See §6.3.
- **`requires`** — a hand-written theory dependency edge list (`"AllCore, FMap,
  Distr, Mu_mem, FinType, StdBigop, FelTactic"`). This is a partial theory
  dependency graph sitting unparsed as a prose string. See §7.4.

`tactics_ref.json` is a different, larger schema (ambient_logic, program_logics,
refman_rst_tactics, version_history, …). It is **not** matched by
`get_repair_doc_snippets`, which only globs `*_lib.json` — but it *is* now the
source of the tactic vocabulary used when building the changelog index (§5.5).

> ⚠️ `version_diffs_found` is a **list in 5 of the 18** library docs and a bare
> sentence ("None found by name in the scanned changelog window.") in the other
> 13. Anything consuming it must normalize; iterating the string form yields one
> character per step. `repair_hints._as_list` does this now, but the underlying
> data is still inconsistent — normalizing the `repair_doc` schema itself is
> unclaimed work.

### 5.5 `output/changelog_index.json` — the derived query format

Built by `scripts/build_changelog_index.py` from `changelog.yaml` +
`raw_releases.json` + the EasyCrypt theory sources + `tactics_ref.json`. Pure
derivation: no network, no API key, no LLM calls, so it is free to rebuild.

```bash
python3 proof_corpus/scripts/build_changelog_index.py
```

What it adds, and why each mattered:

| Addition | Replaces |
|---|---|
| Typed name buckets: `symbols`, `tactics`, `theories_touched`, `theories_mentioned`, `title_tokens` | one untyped `identifiers` list that was **85% English prose** — only 14.5% of its slots named a real EasyCrypt symbol |
| `theories_touched` / `touches` / `areas`, from the PR's own `changed_files` | nothing — all 276 PRs had this data and it was discarded after classification |
| `import_relevant` flag | nothing |
| Flat `entries[]` with stable `key`s + inverted indexes (`by_symbol`, `by_theory`, `by_tactic`, …) | a full nested scan per query |
| Integer `ordinal` per release | re-sorting by `published_at` and locating endpoints with `list.index()` |
| `coverage` block | silence — see below |
| `breaking_weight`, `url`, `labels`, `body_excerpt` | nothing |

**Coverage finding:** `r2022.04`, `r2023.09`, `r2024.01` and `r2024.09` have
**empty release notes upstream** (0–31 chars of body), so the catalog has no
entries for them. It effectively starts at **r2025.02**, not r2022.04. This
directly undercuts `elgamal-changelog-repair`, whose spec declares
`source_ec_version="r2022.04"` for a 2020-era corpus (§6.6): the whole span
where its breakage actually happened is uncataloged. `retrieve_entries.coverage_gap`
reports it, and `get_repair_hints_text` surfaces it through `notes`.

Retrieval reads **both** formats, dispatching on content rather than filename.
`load_changelog`, `tokenize_proof`, `releases_in_range` and `score_entries`
keep their signatures, so both `repair_hints.py` modules and the legacy test
fixture work untouched.

---

## 6. Current state: what is verified broken

Ordered by how much it costs. Items 6.1–6.3 are the ones that most directly
block import repair.

### 6.1 ✅ ~~An import-level break is classified as an un-runnable trial~~ — ADDRESSED 2026-07-30

`repair_bootstrap.py` now attempts a verified, line-preserving import repair
before recording `goal_unreachable`, driven by
[`integration/agent/import_repair.py`](../integration/agent/import_repair.py)
and the rule manifest
[`proof_corpus/ec_migrations.toml`](../proof_corpus/ec_migrations.toml)
(schema: [`EC_MIGRATIONS_SCHEMA.md`](../proof_corpus/EC_MIGRATIONS_SCHEMA.md)).
On the real ElGamal corpus the first error moves **line 108 → 357 → 453**,
stopping at a broken *tactic* rather than a broken import — the intended
boundary. `port_legacy_easycrypt_syntax`'s four hardcoded fixes are now manifest
rules with evidence-based version pins.

Full description in **§4**. Still open: the manifest has 15 rules but 9 of them
are AllCore export-gap rules of one shape, and `symbol_moved` has exactly one
instance (the SmtMap split, carrying all **125** moved declarations).
`repair_docs_index.json`'s `symbol_index` has the data to generate more and
nothing consumes it for that yet. The original analysis follows, since it
explains why this mattered.

**This was the most important structural gap in the system.**

`run_replay_bootstrap_trial` opens with
([repair_bootstrap.py:86](../integration/experiment/repair_bootstrap.py#L86)):

```python
goal_result = fetch_goal_and_premises(case.file, case.proof_start_line, agent_config)
if goal_result.returncode != 0 or not has_open_goals(goal_result.stdout):
    return TrialResult(..., reason="SKIPPED", skip_reason="goal_unreachable")
```

A broken `require import` makes the file fail to *load*, so `-upto` returns
nonzero and the trial is skipped before a single changelog lookup happens. The
knowledge base's strongest evidence — Tier-A `mechanism_change` entries about
cloning/imports/the module system, and the `import_repair_note` fields — is
gated behind a code path that import failures can never reach.

Today the only thing that repairs an import is
`port_legacy_easycrypt_syntax`: **hardcoded regexes, for one file, applied
offline, with no LLM and no knowledge-base involvement.** It works, and it was
the right call to unblock the corpus, but it does not generalize to a second
repository.

### 6.2 🔴 The CLI silently drops `replay_bootstrap`, so `--spec elgamal-changelog-repair` does not run replay mode

Verified at runtime (repro below).
[`__main__.py::_build_spec`](../integration/experiment/__main__.py#L29) and
[`_with_sandbox_dir`](../integration/experiment/__main__.py#L53) rebuild the
`ExperimentSpec` to inject the CLI's `--data-dir` / sandbox path, copying
`mutations`, `informal`, and `broken_formal` — **but not `replay_bootstrap`**,
which then falls back to its dataclass default of `None`
([protocols.py:100](../integration/experiment/protocols.py#L100)).

Consequence: `run_trial` ([runner.py:400](../integration/experiment/runner.py#L400))
sees all four mode fields as `None` and falls through to the **mutation** path
with `spec.mutations = None`. The spec is registered correctly in `specs.py`;
only the CLI rebuild loses it. Any run launched through the documented CLI has
never exercised replay-bootstrap mode.

Repro (stubs `numpy`/`openai` so it runs without the venv):

```python
import sys, types
np = types.ModuleType("numpy"); np.ndarray = object; sys.modules["numpy"] = np
oa = types.ModuleType("openai"); oa.OpenAI = object; sys.modules["openai"] = oa
from pathlib import Path
from integration.experiment.__main__ import _build_spec
from integration.experiment.specs import SPECS
print(SPECS.get("elgamal-changelog-repair").replay_bootstrap)      # ReplayBootstrapConfig(...)
print(_build_spec("elgamal-changelog-repair", Path("data")).replay_bootstrap)  # None
```

Fix: add `replay_bootstrap=spec.replay_bootstrap` to both constructors. Better:
stop enumerating fields — use `dataclasses.replace(spec, corpus=...)` so a
fifth mode can never be dropped the same way.

### 6.3 ✅ ~~`format_repair_hints_for_prompt` discards most of the retrieved evidence~~ — FIXED 2026-07-30

It used to render only `- [{version}] {title}: {hint}` and
`- {path}: {summary}`, dropping `import_repair_note`, `version_diffs_found`,
`requires`, the entry's `kind`, and the match reason.

Now renders all of it, with a `summary_chars` budget on long prose (the block
is re-sent every agent step) and `import_repair_note` deliberately never
truncated. Two matching bugs were fixed alongside it: `version_diffs_found`'s
string/list inconsistency (§5.4), and `import_repair_note` not being tokenized
for `get_repair_doc_snippets` — which is why an SmtMap failure never used to
retrieve the SmtMap import-repair note that describes exactly how to fix it.

### 6.4 🟠 The release hop is single-shot; `already_consumed_versions` is never threaded

`get_changelog_repair_hints_by_release` accepts `already_consumed_versions` so a
caller can advance to the next release after acting on a hint. No caller ever
passes it. `run_replay_bootstrap_trial` calls `get_repair_hints_text` **exactly
once**, at bootstrap, then freezes the result into
`AgentConfig.changelog_hints` for the whole agent run
([repair_bootstrap.py:186](../integration/experiment/repair_bootstrap.py#L186)).

So: the model sees hints for the *first* failing tactic and for exactly *one*
release, for every subsequent step, even after the goal has moved on. The
`repair_hints_hop.json` artifact exists precisely to make the next hop possible
and is currently write-only.

### 6.5 🟠 `_experiment_mode` has no `replay_bootstrap` branch

[runner.py:145](../integration/experiment/runner.py#L145) returns
`"mutation"` for replay-bootstrap specs (verified at runtime:
`_experiment_mode(SPECS.get("elgamal-changelog-repair")) == "mutation"`), so
`summary.json` mislabels the run.
Cosmetic, but it will corrupt any results table built from `summary.json`.
(`TrialResult.mode` is set correctly to `"replay_bootstrap"` inside
`repair_bootstrap.py` — the two disagree.)

### 6.6 🟡 Version endpoints are guessed, not detected

`elgamal-changelog-repair` hardcodes `source_ec_version="r2022.04"`,
`target_ec_version="r2026.07"` — the full cataloged range — with a comment
admitting it is "a broad illustrative default … narrow it once the corpus's
actual EC version at authoring time is known"
([specs.py:57](../integration/experiment/specs.py#L57)). The ElGamal corpus is
from **2020**, which predates the changelog's earliest release entirely. Nothing
detects a repo's authoring-time EasyCrypt version, and nothing detects the
*installed* fork's version either.

Consequence: `releases_in_range` spans all 14 releases, so every Tier-A
`mechanism_change`+`high` entry in the whole changelog is eligible. With 48 high-
relevance entries across the corpus, Tier A can crowd out the Tier B identifier
matches that are actually about the failing tactic.

### 6.7 🟡 Knowledge-base coverage and matching limits

Partly addressed on 2026-07-30 (§5.5); what remains:

- 18 library docs against 127 `.ec`/`.eca` files in the vendored fork's
  `theories/` — ~14% coverage, skewed toward maps/sets/distributions. Only 4
  carry an `import_repair_note`.
- **The changelog covers 10 of 14 releases** — nothing before `r2025.02`
  (§5.5). The index reports the gap; it cannot fill it. Recovering pre-2025
  history needs a source other than GitHub release notes (e.g. `git log` over
  `theories/`).
- Only 3 changelog entries are typed `lemma_renamed`/`lemma_removed`/
  `lemma_changed` — the categories most useful for "this name no longer exists."
  Renames are mostly buried in prose inside `mechanism_change` summaries, and
  **no entry records an old-name → new-name pair.**
- Token matching is exact and case-sensitive over the failing tactic + raw error
  text. EasyCrypt errors frequently name *no* library identifier at all
  ("cannot prove goal (strict)"), which leaves Tier B empty exactly when help is
  most needed. (Tier A now has a capped quota so it cannot swamp the result, but
  it also cannot substitute for a real match.)
- ~~`tactics_ref.json` is unreachable from the retrieval path~~ — it is now the
  tactic vocabulary for the index build, though still not matched by
  `get_repair_doc_snippets`'s `*_lib.json` glob.

### 6.8 🟡 Environment

No Python environment in this working tree currently satisfies
`integration/agent/requirements-agent.txt` (`openai`, `numpy`, `pytest`) —
`import numpy` fails on the active interpreter, so `python3 -m
integration.experiment` and `pytest` cannot run as-is. `PyYAML` is present.
The EasyCrypt binary **is** built. Set up a venv before claiming a test passes.

---

## 7. Roadmap

Do these in order. W1–W3 are small and unblock measurement; W4–W5 are the real
import-repair capability; W6+ is research infrastructure.

### W1 — Fix the wiring (hours, no design work)

1. `__main__.py`: carry `replay_bootstrap` through `_build_spec` and
   `_with_sandbox_dir` — preferably via `dataclasses.replace`. (§6.2)
2. `runner.py::_experiment_mode`: add the `replay_bootstrap` branch. (§6.5)
3. Add a test that asserts every field of a registered `ExperimentSpec` survives
   the CLI rebuild, for **all** registered specs. That is the regression that
   should have caught §6.2.
4. Stand up a venv from `requirements-agent.txt`; make `pytest integration/tests`
   green and record the command in the README.

**Definition of done:** a real `--spec elgamal-changelog-repair` run produces
trials whose `bootstrap_result.json` shows a nonzero `accepted_count` and whose
`summary.json` says `mode: replay_bootstrap`.

### W2 — ✅ Render all the evidence we already retrieve — DONE 2026-07-30

Delivered together with the indexed changelog format (§5.5). See §6.3.
Remaining follow-on: the rendered block is still assembled once at bootstrap
and frozen for the run — that is W3, which is still open.

### W3 — Thread the hop (half a day)

Make hints a per-step function of the *current* failure rather than a frozen
string:

- Accumulate `consumed_versions` across a trial and pass it into
  `get_changelog_repair_hints_by_release` on each new failure.
- Either (a) recompute `changelog_hints` inside the agent loop when a tactic
  fails, or (b) expose a `repair_hints` **agent action** alongside
  `lookup_lemma` / `search_lemmas` so the model can request the next hop.
  Option (b) keeps the always-on prompt small and is the recommended shape.
- If you add an action, it must participate in the retrieval budget
  (`max_continuous_searches`) or it becomes a new stalling move.

> **Update (2026-07-30):** step 2 below (the symbol→theory index) **is now
> built** — `proof_corpus/scripts/build_repair_docs.py` produces
> `output/repair_docs_index.json` with a `symbol_index` over 6325 symbols /
> 128 theories, and `repair_hints.resolve_symbol_theories` surfaces it in the
> prompt. Steps 1, 4, 5 and 6 (detect / verify / preserve line numbers /
> audit artifact) are still open, and they are what turns a *hint* into an
> actual repair. See `proof_corpus/REPAIR_DOCS_INDEX_SCHEMA.md`.

### W4 — ⭐ An import-repair pre-pass — **LARGELY DONE 2026-07-30**

**Goal:** turn "file does not load" from a skipped trial into a first-class,
knowledge-base-driven repair phase, and delete the hardcoded
`port_legacy_easycrypt_syntax` in favor of something general.

**Built** as [`integration/agent/import_repair.py`](../integration/agent/import_repair.py)
(not `experiment/`, since the agent path wants it too), driven by
`proof_corpus/ec_migrations.toml`, invoked from the `goal_unreachable` branch
of `repair_bootstrap.py`. Full description in **§4**; measured on ElGamal it
moves the first error from line 108 → 453. Steps 2, 3, 5 below are done; **1
(error classification), 4 (verify criterion) and 6 (audit artifact) are the
open remainder** — see the annotations inline.

Original design sketch, retained because the open steps still follow it:

1. **Detect.** Run `validate_file` (or `-upto 1`) on the untouched corpus file.
   Classify the failure: parse error / unknown theory / unknown symbol /
   type error. A small error classifier is still needed — `import_repair.py`
   currently only extracts the first error's *line number*
   (`_ERROR_LINE_RES`), not its kind, so rule selection cannot yet be driven by
   what actually went wrong.
2. **Localize.** For "unknown symbol `X`" errors, the question is *which theory
   provides `X` today*. Build a **symbol → theory index** by scanning the
   vendored fork's `theories/**/*.ec{,a}` (127 files) for `lemma`/`op`/`type`/
   `abbrev`/`module` declarations, keyed by qualified `Theory.basename` to match
   the convention `parse_premises` already uses. Cache it as JSON next to the
   binary; invalidate on binary mtime. This is the piece the system is missing
   entirely: `-premises` tells you what **is** in scope, never what **could** be.

   > ✅ **Done.** `build_repair_docs.scan_theories` produces exactly this and
   > writes it to `output/repair_docs_index.json` as `symbol_index`
   > (6325 symbols, 502 of them declared in more than one theory).
   > `repair_hints.resolve_symbol_theories(identifiers)` is the read API, and
   > the prompt now leads with the result. Ambiguity is preserved on purpose —
   > `eq_except` resolves to both `FMap` and `SmtMap`, the two sides of the
   > r2025.02 split, and the renderer tells the model to qualify rather than
   > guess. Note the parser also handles `rename "x" as "y"` inside a clone,
   > which is the only way `DBool.dbool` is reachable.
3. **Propose.** Rank candidate fixes from three sources, most-specific first:
   (a) an exact symbol→theory hit from the index ⇒ concrete
   `require import <Theory>.`; (b) a `repair_doc` `import_repair_note` for a
   theory the file requires; (c) changelog entries in range, which the indexed
   format now lets you query directly instead of scanning —
   `retrieve_entries.entries_for_theories(index, ["SmtMap"])` or
   `import_relevant_entries(index, versions_in_range)` (25 entries carry the
   flag). Emit as a prompt section with provenance, or apply mechanically when
   the index hit is unambiguous.

   > ✅ **Done, via the manifest.** `ec_migrations.toml` encodes the proposals
   > as `[migration.match]` / `[[migration.action]]` pairs with
   > `[migration.provenance]`, and `format_for_prompt` renders what was applied.
   > Source (c) is not yet wired in — rules are static, not queried per file.
4. **Verify.** Re-run `validate_file` after each candidate edit; keep it only if
   the error count strictly decreases. Never accept an edit on faith — the
   harness's whole epistemology is "EasyCrypt is the oracle."

   > ⚠️ **Done, but the criterion was deliberately relaxed.** "Strictly
   > decreases" proved wrong in practice: every rule is independently
   > evidence-backed, so a rule that fixes line 300 shows *no movement* while an
   > unrelated parse error still sits at line 108, and a strict criterion rolls
   > it back. The implemented test is **non-regression** — keep the rule unless
   > EasyCrypt now stops *earlier*. The open work is to measure something better
   > than first-error line (§4.5).
   >
   > This step also produced the worst bug of the sprint: `_ERROR_LINE_RE`
   > matched only `path.ec:108`, but EasyCrypt actually emits
   > `[critical] [path.ec: line 108 (8)]`. Every probe returned -1, so **every
   > migration was silently rolled back** and the pass looked like a no-op.
   > `_ERROR_LINE_RES` now handles both formats and there is a regression test.
   > If you add a third output format, add it there.
5. **Preserve line numbers** wherever possible, exactly as
   `port_legacy_easycrypt_syntax` does (fold onto an existing line rather than
   inserting), because the corpus index records absolute lemma line numbers and
   `ProofCase` carries them.

   > ✅ **Done and enforced.** `apply_actions` asserts the line count is
   > unchanged and raises if a rule violates it. See §4.2.
6. Record every applied edit in an `import_repair.json` artifact so a run can be
   audited and the porting can be reproduced offline.

   > ✅ **Done.** `repair_bootstrap.py` writes `import_repair.json` into the
   > trial directory (alongside `original.ec` and `import_repaired.ec`) on
   > every `goal_unreachable` attempt, whether or not the repair is promoted.
   > What is still missing is **aggregation**: nothing rolls those per-trial
   > files up into `summary.json`, so attempt/success rates are recorded but
   > never reported. That is W8.

**Watch out for:** `clone`/`clone import` is a different mechanism from
`require import` and has its own changelog entries (`#903` pre-emptive
renamings, `#1062` clone-override soundness, `#1069` PolyReduceZp override
style). Do not conflate them. Also, `require` order matters in EasyCrypt and
some theories must be required before others are usable — see `#988` *"Check
that `Distr` is in scope when tagging distributions."*

### W5 — ✅ Machine-readable theory dependencies — DONE 2026-07-30

`build_repair_docs.py` parses `requires` / `require_exports` / `imports` /
`clones` straight from the theory sources and keeps the authored prose as
cross-validation, which immediately found 2 genuine disagreements (`Distr`
really requires `Discrete`; `PROM`'s prose names two of its own internal
theories). `import_repair_note` is now present on **18/18** libraries
(4 authored, 14 derived) instead of 4.

Still open from the original item: **authored** notes for more libraries.
The 14 derived notes state verified facts but cannot explain a semantic change
the way the 4 hand-written ones do — writing more of those remains the
highest-value manual work in the corpus.

### W5b — ✅ Fill the pre-r2025.02 changelog void — DONE 2026-07-30

`collect_changelog.py --git-log <ec-clone>` derives entries from
`git log <prev-tag>..<tag>` for releases whose notes are empty. It recovered
**652 commits** across EasyCrypt's four undocumented releases, taking the
catalog from **10/14 to 14/14** releases, and immediately paid for itself by
pinning three migration rules to releases no release body mentions (§W4).
`process_changelog.py` classifies commit entries alongside PR entries.

**The LLM classification pass has been run.** `changelog.yaml` now holds **913
classified entries across 14 releases** (276 from release notes, 637 from git
log; 155 high-relevance, 391 medium, 367 low) with no unclassified rows. The
classifier goes through the Message Batches API — all unclassified entries
across every release are chunked once and submitted as a single job, matched
back by `custom_id`. `--sync` restores the old blocking per-chunk path.

The original investigation follows, since the reasoning still applies.

Investigated 2026-07-30. **The release-notes pipeline is working correctly and
is not the problem; its *source* is.**

- EasyCrypt's GitHub tags are **already** `rYYYY.MM`, including the oldest.
  `collect_changelog.py` copies `tag_name` verbatim and `process_changelog.py`
  carries it into `version` — **there is no version renaming in the changelog
  pipeline** and none is needed. (Renaming *does* happen on the repo side:
  `compute_exposure_score.resolve_ec_sha` maps an old-style pin — a bare commit
  SHA, the pre-tag convention — onto `rYYYY.MM` via `git describe --tags`.)
- `BULLET_RE` skips exactly 8 lines across all releases; all 8 are
  "@user made their first contribution" notes. Correctly skipped, not a bug.
- The classifier is cached by `(repo, pr_number, title)`, so re-runs are already
  cheap. It is not the bottleneck.

The real limit: **`collect_changelog.py` only reads GitHub release bodies**, and
EasyCrypt's are empty before r2025.02 (r2022.04 = "First EasyCrypt stable
release.", r2023.09/r2024.01 = 0 chars, r2024.09 = "Release 2024.09"). Against
the corpus we actually have this is the dominant gap:

| Measure | Value |
|---|---|
| Corpus repos predating the changelog entirely | **14 / 77 (18%)** |
| Repos whose exposure spans ≥13 releases | **34 / 77 (44%)** |
| Repos resolving to a real tag via a submodule pin | **2 / 77** |
| Repos falling back to the weak `git_commit_date` heuristic | **47 / 77** (+12 more that only establish "predates the changelog") |

> **Rescored 2026-07-31** after the classification pass. Filling the four
> undocumented releases changed **44 of 77** exposure scores and moved **54 of
> 75** ladder ranks: repos predating r2025.02 used to cross four *empty*
> releases and score nothing for them, and now score 382 → **1211**. Corpus
> selection made before that date was reading a ladder that systematically
> under-weighted exactly the oldest, most-broken repos — the ones this project
> exists to repair. If you have experiment results selected off the old ladder,
> re-check which repos they used.

**Implemented** as `--git-log`, plus `--git-log-paths` (default
`theories/,src/,libs/`), `--git-log-all-releases`, and caps on files/commits per
range. `collect_changelog.py` also records `body_chars`/`bullet_count` per
release and a `coverage` block, so the gap is reported at collection time
rather than discovered as puzzling silence during a repair run.

Note the boundary case: `r2022.04` is the *oldest* tag, so its range is
`(root)..r2022.04` — EasyCrypt's entire pre-tag history, 3579 commits, capped
at `--git-log-max-commits`. That is honest but coarse; a narrower lower bound
would need a date cutoff rather than a tag.

### W6 — Version detection

Replace the hardcoded `r2022.04`/`r2026.07` pair:

- **Target:** ask the built binary (`ec.exe --version` or the dune build info in
  `src/EcDuneSites.ml`) and map to the nearest changelog tag.
- **Source:** heuristics over the corpus repo — git history dates, an
  `easycrypt.project` file, or the newest EasyCrypt feature the file uses. Even
  a coarse "this proof is from 2020, before the changelog starts" is far better
  than assuming the full range, because it tells `releases_in_range` to include
  everything *deliberately* rather than by accident. `proof_corpus/scripts/
  compute_exposure_score.py` already has a `VERSION_TAG_RE` and per-repo
  exposure scoring worth reusing — and its content-bracket heuristic now matches
  on resolved names rather than English words, so it is a more trustworthy
  starting point than it was.

Note that Tier-A flooding itself is already fixed (`score_entries` caps the
Tier-A quota, §5.5), so narrowing the range is now about *precision and honesty*
— particularly reporting `coverage_gap` when the range reaches below r2025.02 —
rather than about reclaiming result slots.

### W7 — Version-hopping binaries (designed, not implemented)

A full design already exists:
[`docs/plans/ec_version_hopping_infrastructure.md`](plans/ec_version_hopping_infrastructure.md).
Build N per-release EasyCrypt binaries and bisect the exact release where a
tactic stopped applying, then narrow the changelog lookup to that single
transition. Key decisions already made in that doc: use **git worktrees** off
the existing fork clone (not N clones); build **lazily and cached** with an LRU
cap; use **upstream tags without the `-premises` patch** (validation only needs
`llm -upto`); wire in as an **optional pre-step** behind a flag.

Do not start this before W1–W5. It is the most expensive item and it improves
hint *precision*, which only matters once hints are actually reaching the model
every step and covering imports.

### W8 — Measurement

Nothing currently reports repair-specific outcomes. At minimum, aggregate into
`summary.json`: distribution of `accepted_count / total_count` (how much of old
proofs still replays), `fully_replayed` rate (the zero-LLM win rate), which
changelog release each trial hopped to, whether the hinted entry's identifier
appeared in the tactic the model eventually accepted, and — once W4 lands —
import-repair attempt/success counts. Without this, W2–W5 cannot be shown to
help.

---

## 8. Conventions for working in this repo

- **Never answer the DeepSeek confirmation.** [`AGENTS.md`](../AGENTS.md). Hand
  the user the command.
- **Document the *why* in docstrings, at length.** This codebase's module
  docstrings carry the design rationale (see `repair_bootstrap.py`,
  `repair_hints.py`, `corpora/elgamal.py`) — including *rejected* alternatives
  and cross-project porting notes. Match that register; it is why this handoff
  was possible to write.
- **Update `CHANGELOG.md`.** Entries are grouped by implementation date with a
  short problem statement, then per-file bullets. Follow the existing shape.
- **Never vendor `proof_corpus` scripts.** Load them dynamically and validate
  the expected attributes, so drift raises `RepairHintsUnavailable` instead of
  silently misbehaving.
- **Repair hints must degrade gracefully.** `get_repair_hints_text` never
  raises; failures become `notes`. Preserve that.
- **Tests use local fixtures, never the sibling corpus.**
  `integration/tests/fixtures/repair_hints/` holds a trimmed
  `retrieve_entries.py`, a synthetic `changelog.yaml`, and two repair_doc
  entries; `SHANNON_PROOF_CORPUS_DIR` points the module at them.
- **EasyCrypt is the oracle.** Every proposed edit gets validated by running the
  binary. No heuristic accepts a change on its own.
- **Line numbers are load-bearing.** The corpus index stores absolute lemma line
  numbers; any offline source transformation must preserve them.

---

## 9. File inventory

### `integration/agent/` — the agent

| File | Lines | Role |
|---|---|---|
| `loop.py` | 1537 | Agent loop, action dispatch, stuck detection, error enrichment |
| `llm.py` | 731 | Provider clients, action JSON parsing and repair, retrospectives |
| `prompt.py` | 696 | Prompt assembly, goal-shape classification, active hints |
| `repair_hints.py` | ~640 | **Changelog + repair_doc retrieval, symbol→theory resolution** |
| `import_repair.py` | ~560 | **Pre-proof import/syntax repair driven by `ec_migrations.toml`, verified against EasyCrypt** |
| `lemma_search.py` | 289 | Semantic/substring/prefix/exact search with `theory:` filter |
| `config.py` | 265 | `AgentConfig`, provider selection, thinking modes |
| `easycrypt.py` | 232 | `ec.exe llm` wrappers, goal resolution probes |
| `proof_file.py` | 224 | `.ec` file mutation: append/undo/bounds/cursor |
| `premises.py` | 144 | `Ax.all` parsing into qualified `Theory.basename` keys, embedding cache |
| `error_history.py` | 141 | Per-goal failure memory and tactic normalization |
| `pricing.py`, `usage.py`, `run_log.py`, `embeddings.py` | ~440 | Cost, token accounting, structured logs, embeddings |

### `integration/experiment/` — the runner

| File | Lines | Role |
|---|---|---|
| `runner.py` | 579 | Trial orchestration, `TrialResult`, mode dispatch |
| `__main__.py` | 359 | CLI (**contains the §6.2 bug**) |
| `informal.py` | 279 | Writer LLM, red-herring manifests, contamination check |
| `repair_bootstrap.py` | 221 | **Replay-until-failure trial mode** |
| `proof_extract.py` | 205 | Sandbox building, tactic stripping, `admit_prior_lemmas` |
| `corpora/elgamal.py` | 186 | Broken 2020 corpus + **`port_legacy_easycrypt_syntax`** |
| `corpora/joy.py` | 89 | Joy of EasyCrypt corpus |
| `specs.py` / `protocols.py` | 70 / 117 | Spec registry and mode marker dataclasses |

### `proof_corpus/`

| Path | Role |
|---|---|
| `output/changelog.yaml` | LLM-classified changelog, 14 releases / **913 entries**. **Source of record** |
| `output/changelog_index.json` | Derived flat/typed/indexed query format (§5.5). What retrieval reads. 554 entries with resolved names, 84 import-relevant |
| `output/raw_releases.json` | Raw GitHub data: bodies, labels, `changed_files` for 276 PRs + 652 git-log commits |
| `output/llm_cache.json` | Classification cache keyed by `(repo, id, title)` — delete an entry to force reclassification |
| `output/repair_docs_index.json` | Reprocessed library docs + tree-wide `symbol_index` (6325 symbols) |
| `output/library_history.json` | Mined git history for 16 tracked theories: path events + per-release symbol churn |
| `ec_migrations.toml` | Per-version import-repair rules (**15**: 12 derived, 3 curated) + 16 library history records. **Derived** from `library_history.json` plus the AllCore export closure |
| `output/exposure_results.json`, `ladder.md` | Per-repo breaking-change exposure and the ranked corpus ladder. **Derived from the changelog — rerun after any classification pass** |
| `CHANGELOG_INDEX_SCHEMA.md`, `REPAIR_DOCS_INDEX_SCHEMA.md`, `EC_MIGRATIONS_SCHEMA.md` | Schemas, rationale, and the measurements behind them |
| `scripts/build_changelog_index.py`, `build_repair_docs.py`, `build_ec_migrations.py` | The derivations (no network, no LLM — free to rerun) |
| `scripts/analyze_library_history.py` | Mines the EasyCrypt git history per theory (needs the clone; no LLM) |
| `scripts/retrieve_entries.py` | The matcher; reads both formats |
| `scripts/{collect,process}_changelog.py` | How `changelog.yaml` was built (GitHub + git log; Batches-API classification, cached) |
| `scripts/compute_exposure_score.py`, `estimate_repair_difficulty.py`, `rank_repos.py` | Corpus selection and ranking |
| `repair_doc/*.json` | 18 library docs + `tactics_ref.json` |

### `integration/tests/` — 217 tests, all local fixtures

| File | Tests | Covers |
|---|---|---|
| `test_agent.py`, `test_goal_state.py` | 60+ | Agent loop, guardrails, goal resolution (2 pre-existing failures in `test_goal_state.py`) |
| `test_import_repair.py` | 33 | Manifest parsing, version selection, matching, line-preserving actions, both EasyCrypt error formats, bulk/incremental loop |
| `test_changelog_index.py` | 24 | Index derivation + the two-format retriever |
| `test_repair_docs_index.py` | 22 | Repair-doc derivation, `symbol_index`, symbol→theory resolution |
| `test_process_changelog.py` | 18 | Batch classification: `custom_id` matching (stub returns results **reversed**), parse-failure guard, cache poisoning, poll/timeout |
| `test_repair_hints.py`, `test_premises.py` | 20+ | Retrieval rendering; premise parsing (`test_premises.py` needs `hypothesis`) |

---

## 10. Commands

```bash
# Corpus preparation
python3 -m benchmark clone --only tejasanilshah-the-joy-of-easycrypt
python3 -m benchmark extract
python3 -m benchmark index

# Single-file agent run
python3 -m integration.agent path/to/file.ec --log-file run.json

# Experiment (local LM Studio)
python3 -m integration.experiment run \
  --spec elgamal-changelog-repair --trials 10 \
  --stuck-limit 20 --max-steps 200 \
  --llm-model <model> --embed-model <embed-model>

# Rebuild the derived artifacts (free: no network, no LLM). Order matters —
# each stage consumes the previous one.
python3 proof_corpus/scripts/analyze_library_history.py   # needs the EC clone; slow
python3 proof_corpus/scripts/build_changelog_index.py     # consumes changelog.yaml
python3 proof_corpus/scripts/build_repair_docs.py
python3 proof_corpus/scripts/build_ec_migrations.py       # consumes library_history.json

# NOTE: build_changelog_index.py reads output/changelog.yaml, so rerun it after
# any classification pass or the index silently serves stale entries.

# Repair a file's imports before proving (verified against EasyCrypt)
python3 -m integration.agent.import_repair FILE.ec \
  --source-version r2022.04 --target-version r2026.07 [--write]

# Re-collect the changelog INCLUDING commits for undocumented releases.
# The second command calls a paid API for anything not already cached; it
# submits one Message Batches job and polls, so expect minutes, not seconds.
# Add --skip-llm to verify the plumbing without spending, or --sync to get
# blocking per-chunk calls back at full price.
python3 proof_corpus/scripts/collect_changelog.py --repo EasyCrypt/easycrypt \
  --out proof_corpus/output/raw_releases.json --with-pr-details \
  --git-log integration/extern/easycrypt
python3 proof_corpus/scripts/process_changelog.py \
  --in proof_corpus/output/raw_releases.json \
  --out proof_corpus/output/changelog.yaml \
  --cache proof_corpus/output/llm_cache.json

# Changelog retrieval, standalone
python3 proof_corpus/scripts/retrieve_entries.py \
  --changelog proof_corpus/output/changelog_index.json \
  --proof broken.ec --source-version r2022.04 --target-version r2026.07 --top-n 12

# "What changed about this theory?" / "What's relevant to import repair?"
python3 proof_corpus/scripts/retrieve_entries.py \
  --changelog proof_corpus/output/changelog_index.json \
  --theory FMap --source-version r2022.04 --target-version r2026.07
python3 proof_corpus/scripts/retrieve_entries.py \
  --changelog proof_corpus/output/changelog_index.json \
  --import-relevant --source-version r2025.02 --target-version r2026.07

# Tests
python3 -m venv .venv && .venv/bin/pip install -r integration/agent/requirements-agent.txt hypothesis
.venv/bin/python -m pytest integration/tests integration/experiment/tests
```

DeepSeek runs: **print the command for the user, never run it yourself.**

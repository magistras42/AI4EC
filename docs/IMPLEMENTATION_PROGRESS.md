# Implementation Progress — Proof-Repair Handoff Roadmap

**Date:** 2026-08-03 · **Branch:** `shannon-llm-integration` · **Scope:** the
remaining work in [`PROOF_REPAIR_HANDOFF.md`](PROOF_REPAIR_HANDOFF.md), plus a
provider architecture that lets DeepSeek, a local model (Gemma via LM Studio),
or Claude act as the repair agent.

Companion document: [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) — what
the end-to-end run actually produced.

---

## 1. What changed, in one table

| Roadmap item | Before | After |
|---|---|---|
| **W1** — CLI drops `replay_bootstrap` | 🔴 Whole knowledge base unreachable from any experiment | ✅ Fixed via `dataclasses.replace` + a field-exhaustive regression test |
| **W1** — `_experiment_mode` | 🟠 Labelled replay runs `"mutation"` | ✅ Correct branch; agrees with `TrialResult.mode` |
| **W1 adjacent** — `import_repair` in the agent loop | ❌ Only reachable from `repair_bootstrap.py` | ✅ Wired into `run_agent`, behind `--import-repair` |
| **W3** — hop threading | 🟠 Hints frozen at bootstrap for the whole run | ✅ Re-fetched per failure, `consumed_versions` threaded |
| **W4 step 1** — error classification | ❌ Only a line number | ✅ `ec_errors.py`, load-vs-proof boundary explicit |
| **W6** — version detection | ❌ Hardcoded `r2022.04`/`r2026.07` | ✅ `ec_version.py`; target detected as **r2026.06** |
| **W8** — repair metrics | ❌ Recorded, never reported | ✅ `repair_metrics.py` → `summary.json` |
| **Providers** | DeepSeek + LM Studio, OpenAI-SDK only | ✅ Three backends behind one protocol, Claude added |
| Tests | 277 passing | **563 passing**, suite fully green |

Since this table was written, everything §11 listed as open has been
implemented — including W7, which was open when the row above was drafted. See
§11 for what each turned out to be; the short version is that four of the six
items uncovered a defect rather than merely adding a feature.

| Roadmap item | Outcome |
|---|---|
| **W4.1 adjacent** — error-kind rule selection | ✅ `import_repair.py` now consumes `ec_errors`; targeting re-classifies each round |
| **W4.5** — progress measure | ✅ Graded outcome; **3 classifier holes found and fixed** by re-measuring |
| **Symbol moves** | ✅ 1 → 5, by mining 135 theories instead of 16 |
| **W5** — authored notes | ✅ 4/14 → **14 authored / 4 derived**; found the r2024.09 cost-logic removal |
| **W7** — version hopping | ✅ Implemented (binaries not yet built — see §11) |
| **Hint-uptake A/B** | ⚠️ Arm + scorer built, but §10.1 already dropped an A/B on this corpus for variance — the run should probably not happen |

---

## 2. W1 — the one dropped field

This was the highest-leverage item in the roadmap and it is worth stating the
mechanism precisely, because the blast radius was much larger than "one
experiment mode is broken".

`_build_spec` and `_with_sandbox_dir` in
[`integration/experiment/__main__.py`](../integration/experiment/__main__.py)
rebuilt `ExperimentSpec` field-by-field in order to inject the CLI's
`--data-dir` and sandbox path. When `replay_bootstrap` was added as a fifth
mode, nobody added it to those two constructors, so it silently fell back to
its dataclass default of `None`. `run_trial` then saw *every* mode field unset
and dispatched down the **mutation** path with `spec.mutations = None`.

Because `repair_bootstrap.py` is the only producer of
`AgentConfig.changelog_hints` in the entire codebase, that single dropped field
made all of this unreachable from any CLI-launched run:

| Asset | Reached via | Status before |
|---|---|---|
| `changelog_index.json` (913 entries) | `get_repair_hints_text` | never queried |
| `repair_docs_index.json` (6325 symbols) | `resolve_symbol_theories` | never queried |
| `repair_doc/*.json` (18 library notes) | `get_repair_doc_snippets` | never queried |
| `ec_migrations.toml` (15 rules) | `repair_imports` | never invoked |

**The fix** replaces both enumerated rebuilds with `dataclasses.replace(spec,
corpus=...)`, which copies every field by construction. A sixth mode cannot be
lost the same way. The same flaw existed in the `--red-herring-ratio` override
block in `main()` and was fixed identically.

**The regression test** ([`test_cli_spec_rebuild.py`](../integration/experiment/tests/test_cli_spec_rebuild.py))
iterates `dataclasses.fields(ExperimentSpec)` rather than a hardcoded list, and
asserts every non-corpus field of *every registered spec* survives *both*
rebuilds. A field added tomorrow is covered the day it is added, not the day
someone notices it never ran.

**Definition of done, as the handoff specified it** — all three met, evidence
in the companion document:

- `bootstrap_result.json` shows nonzero `accepted_count` ✅
- `summary.json` says `mode: replay_bootstrap` ✅
- the prompt log contains a non-empty `## Known EasyCrypt library changes`
  section ✅ (6/6 prompts)

---

## 3. Provider architecture

### 3.1 The shape

The request was to support DeepSeek, Gemma (local), *or* Claude as the repair
agent. The split is drawn at **transport only**:

```
                       ┌──────────────────────────────────────┐
loop.py ─ decide() ──▶ │ LlmClient (parsing, JSON repair,     │
                       │ tactic salvage, retrospectives)      │
                       └───────────────┬──────────────────────┘
                                       │ ChatBackend protocol
                       ┌───────────────┴───────────────┐
                       ▼                               ▼
        OpenAICompatBackend                    AnthropicBackend
        ├─ lm_studio  (local Gemma, …)         └─ claude-opus-5 (default)
        └─ deepseek   (hosted, paid)              via Messages API
```

Everything downstream of a reply — action-JSON extraction, the backslash-escape
repair EasyCrypt's `/\` forces on us, prover-style tactic-line salvage, the
retrospective parser — is provider-independent and lives once in `llm.py`. A
backend's whole job is `(system, user, thinking) → ChatReply`. Adding a fourth
provider means implementing two methods.

`loop.py` is entirely provider-blind; it still just calls `decide()`.

### 3.2 Why Claude is not a base-URL swap

Anthropic is **not** OpenAI-compatible, and each difference is a 400 waiting to
happen if papered over:

| Concern | OpenAI-compatible | Anthropic |
|---|---|---|
| System prompt | a `messages[0]` entry | top-level `system` parameter |
| Thinking | `extra_body.thinking` | first-class `thinking` field |
| Effort | `reasoning_effort` (high/max) | `output_config.effort` (low…max) |
| Sampling | `temperature` honoured | **removed on Opus 5 — 400 if sent** |
| Reply shape | one `message.content` string | list of typed content blocks |

Consequences that are implemented rather than hoped for:

- **`temperature` is never sent** on the Anthropic path.
  `AgentConfig.llm_temperature` simply has no effect there.
- **Requests stream** and reassemble with `get_final_message()`. With adaptive
  thinking at `high` effort a single tactic-selection step can run for minutes;
  a non-streaming call at `max_tokens=16384` risks an idle-connection timeout
  that would lose the step.
- **Thinking blocks never become `text`.** A model that "answered" only inside
  its reasoning has not committed to an action. `_anthropic_reply` keeps the
  channels apart, and the existing last-resort reasoning salvage still only
  accepts text that parses as an action object.
- **`stop_reason: "refusal"`** is surfaced as a recoverable `LlmFormatError`
  carrying the policy category, so the loop records a format error and the
  operator can see why the step went nowhere — rather than an empty reply.
- **Structured outputs are used properly.** Claude's `output_config.format`
  constrains the reply to the *action schema*, not merely to "some JSON", so
  `--llm-json-mode` on this provider removes a real failure class instead of
  trading one for another.

### 3.3 Defaults and cost

Default model is **`claude-opus-5`** with adaptive thinking (`display:
summarized`, so the run log's `thought` field is populated) at effort `high`.
Adaptive is Claude's own per-request scaling, so the harness's
trajectory-window heuristic is deliberately *not* applied to it — Claude
decides with more information than a failure counter has. DeepSeek keeps its
existing behaviour, including thinking-off by default.

`pricing.py` gained Anthropic rates, with cache reads at 0.1× and cache writes
at 1.25× base input. `TokenUsage` gained `cache_write_prompt_tokens` and a
`record_anthropic()` method, because Anthropic partitions the prompt
differently: `input_tokens` is only the *uncached remainder*, with reads and
writes reported alongside it. Summing all three is what keeps `prompt_tokens`
meaning the same thing across providers — reading `input_tokens` alone would
under-report every cached prefix.

### 3.4 The paid-provider gate

Claude costs money, so it is gated exactly like DeepSeek. `deepseek_confirm.py`
generalized into [`paid_confirm.py`](../integration/experiment/paid_confirm.py),
covering every provider in `PAID_LLM_PROVIDERS`, with published per-token rates
in the banner. `deepseek_confirm.py` remains as a compatibility shim.

**[`AGENTS.md`](../AGENTS.md)'s rule is unchanged and now provider-independent:
an agent must never answer that prompt.** There is still no `--yes` flag for
any provider. The gate was exercised during testing by letting it abort; it was
never answered.

Invalid combinations fail at **startup**, not as a 400 after EasyCrypt time has
been spent:

```
$ … --provider anthropic --thinking disabled --reasoning-effort max
error: Claude rejects thinking='disabled' at reasoning effort 'max';
       use effort high or below, or leave thinking adaptive
```

---

## 4. Version detection (W6) — and a correction it produced

The spec hardcoded `source_ec_version="r2022.04"`, `target_ec_version="r2026.07"`,
with a comment admitting it was "a broad illustrative default".

The target is genuinely knowable, and the hardcoded value was **wrong**.
`ec.exe` has no `--version` flag (its commands are
compile/llm/cli/config/runtest/why3config/docgen), but the tree it was built
from does:

```
$ git -C integration/extern/easycrypt describe --tags
r2026.06-6-g07e77d8c
```

So the installed fork is **r2026.06** plus six commits — the pinned target
named a release *newer* than the binary actually present, meaning migration
rules pinned to r2026.07 were being considered against a build that does not
contain them.

[`ec_version.py`](../integration/agent/ec_version.py) walks up from the binary
to find that tree, and **snaps the answer onto cataloged releases only** — the
fork carries tags (r2026.05, r2026.06, r2026.07) the changelog may not cover,
and claiming a version the knowledge base cannot reason about would scope
`releases_in_range` to an empty window.

The design decision worth flagging: every result carries `method` and
`confidence`, never a bare string. A parsed `git describe` (high) and a
git-date bracket (low) must not reach the model with equal authority. When
detection fails the answer is `None`, which every consumer already treats as
"consider everything" — **a wrong narrow guess is worse than an honest wide
one.** The 2020-era ElGamal corpus stays honestly undetected.

`ReplayBootstrapConfig`'s version fields now default to `None` (= detect), with
an explicit value still winning. That is the difference between a deliberate
narrowing and an admission of ignorance, and the spec's was the latter.

---

## 5. Error classification (W4 step 1)

[`ec_errors.py`](../integration/agent/ec_errors.py) classifies failures by
kind, handling **both** EasyCrypt location formats — the one whose absence
caused "the worst bug of the sprint" (every probe returned -1, so every
migration was silently rolled back):

```
path.ec:108: parse error
[critical] [path.ec: line 108 (8)] cannot find theory: `SmtMap'
```

The distinction it exists to draw is **pre-proof vs in-proof**, and it lands
exactly on the ElGamal boundary the handoff documents:

| Line | Message | Kind | Whose job |
|---|---|---|---|
| 108 | ``cannot find theory: `SmtMap'`` | `unknown_theory` | import repair |
| 453 | `invalid 'position' parameter` | `tactic_error` | the solver |

Unrecognized output classifies as `unknown` and **counts as a load failure**,
so an unfamiliar message can never cause a repair attempt to be skipped.

---

## 6. Live hint hops (W3)

Hints were fetched once at bootstrap and frozen into `AgentConfig` for the
entire run: the model saw evidence for the *first* failing tactic, for exactly
*one* release, on every subsequent step — even after the goal had moved on.
`repair_hints_hop.json` existed precisely to make the next hop possible and was
write-only.

`_refresh_changelog_hints` in `loop.py` now re-fetches on each tactic failure,
accumulating `consumed_versions` and passing them to
`get_changelog_repair_hints_by_release` so each new failure advances to the
next release with a hit. It is opt-in (`live_changelog_hints`), because each
refresh is a retrieval pass and a mutation trial's synthetic failures map to no
real release.

One rough edge found and fixed during the E2E run: passing an empty string for
an undetected source version made `retrieve_entries` emit
`version(s) not found in changelog: ['']` on *every* lookup. Unknown source is
now spelled as the oldest cataloged release — same fail-open range, no noise.

---

## 7. Repair metrics (W8)

Trials already wrote rich per-trial evidence and nothing rolled it up, so
attempt/success rates were recorded but never reported — meaning W2–W5 could
not be shown to help or not help.

[`repair_metrics.py`](../integration/experiment/repair_metrics.py) aggregates
into `summary.json`:

- **replay** — accepted/total distribution, mean/min/max replayed fraction, and
  the `fully_replayed` rate (the zero-LLM cheap-win rate)
- **import_repair** — attempts, improvement rate, files made loadable, mean
  migrations kept, mean first-error-line advance
- **changelog_hops** — which release each trial hopped to, misses included
- **hint_uptake** — whether an identifier the hint named appears in a tactic
  EasyCrypt *accepted*; the closest available proxy for "the knowledge base
  helped", short of a counterfactual hints-disabled run. **Only names the hint
  INTRODUCED count** — one already in the source proves nothing about the
  hint. Run D reported 50% before that correction and 0% after, the whole
  difference being `Adv`, the corpus's own adversary module
  ([`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §12.4)

It is pure derivation from files the trials already wrote — no EasyCrypt, no
LLM, no network — so it can re-score a finished run after the fact. Malformed
or partial artifacts are skipped rather than fatal.

---

## 8. Files

**New**

| File | Purpose |
|---|---|
| `integration/agent/ec_errors.py` | Error classification (W4.1) |
| `integration/agent/ec_version.py` | Version endpoint detection (W6) |
| `integration/experiment/paid_confirm.py` | Provider-generic spend gate |
| `integration/experiment/repair_metrics.py` | Repair outcome aggregation (W8) |
| `integration/tests/test_providers.py` | 25 tests — provider selection, request shape, Claude backend |
| `integration/tests/test_ec_errors.py` | 14 tests — classification, both location formats |
| `integration/tests/test_ec_version.py` | 11 tests — detection honesty, overrides |
| `integration/experiment/tests/test_cli_spec_rebuild.py` | 18 tests — the W1 regression |
| `integration/experiment/tests/test_repair_metrics.py` | 14 tests — aggregation |

**New since, for §11**

| File | Purpose |
|---|---|
| `integration/experiment/ec_versions.py` | Per-release EasyCrypt provisioning + registry (W7) |
| `integration/experiment/version_hop.py` | Break-release localization (W7) |
| `integration/experiment/compare_runs.py` | Paired-arm scoring across seeds |
| `integration/experiment/tests/test_version_hop.py` | 33 tests — provisioning, bisection, integration |
| `integration/experiment/tests/test_compare_runs.py` | 19 tests — mostly what it refuses to conclude |

Also modified for §11: `import_repair.py` (error-kind selection, graded
outcome, minimisation), `ec_errors.py` (three classifier holes),
`repair_metrics.py` (outcome distribution), `repair_bootstrap.py` (hop
pre-step, hints-off arm), `protocols.py` / `runner.py` / `__main__.py` (flags,
`arm` block), `proof_corpus/scripts/analyze_library_history.py` +
`build_ec_migrations.py` (theory discovery, distinctiveness), 13
`proof_corpus/repair_doc/*.json`, and the regenerated
`ec_migrations.toml` / `library_history.json` / `repair_docs_index.json`.

**Modified**

`llm.py` (backend split), `config.py` (provider config, detection fields),
`pricing.py` + `usage.py` (Anthropic cost/usage), `loop.py` (import repair,
live hops), `experiment/__main__.py` (W1 fix, `--provider`), `runner.py`
(`_experiment_mode`, metrics), `repair_bootstrap.py` (detection, hint
persistence), `specs.py` / `protocols.py` (version defaults),
`agent/__main__.py` (provider + `--import-repair`), `deepseek_confirm.py`
(shim), `CHANGELOG.md`.

---

## 9. Running it

```bash
# Environment (the tree's .venv already satisfies this)
python3 -m venv .venv && .venv/bin/pip install \
  -r integration/agent/requirements-agent.txt hypothesis

# Tests — 563 pass, 1 skipped
.venv/bin/python -m pytest integration/tests integration/experiment/tests

# Local model (Gemma et al. via LM Studio) — free, no gate
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --trials 10 --stuck-limit 20 --max-steps 200 \
  --llm-model <model> --embed-model <embed-model>
```

**Paid providers — print the command, let a human run it.** Both prompt for
confirmation; per `AGENTS.md` an agent must never answer either prompt.

Embeddings always run on LM Studio regardless of provider (Anthropic has no
embeddings API), so an embedding model must be loaded there first. The CLI
probes that endpoint *before* the confirmation prompt, so a run that cannot
start never asks anyone to authorize spend. `.env` is not auto-loaded — source
it with `set -a; . ./.env; set +a`.

```bash
# Claude (default claude-opus-5, adaptive thinking, effort high)
export ANTHROPIC_API_KEY=...        # or: ant auth login
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --provider anthropic --trials 6 --stuck-limit 10 --max-steps 25 --seed 7 \
  --reasoning-effort high

# DeepSeek. Thinking stays ON; truncation is fixed with budget, not by
# disabling it -- see "Do not turn thinking off" in ELGAMAL_E2E_RESULTS.md.
export DEEPSEEK_API_KEY=...
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --provider deepseek --trials 10 --stuck-limit 20 --max-steps 200 \
  --llm-model deepseek-v4-flash --thinking adaptive --llm-max-tokens 32768 \
  --embed-model <local-embed-model>
```

Embeddings always stay on LM Studio regardless of provider.

### 9.1 The hints-on / hints-off arm (§11 item 6)

> ⚠️ **Read §10.1 before spending anything on this.** A *different* A/B —
> `show_remaining_original` — was already run on this corpus and **dropped, not
> deferred**: run-to-run variance under identical configuration reached 11-vs-1
> accepted tactics, exceeding any between-arm difference it could detect.
> [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §9.2 says to re-open an A/B
> "if at all", at roughly 10x the spend.
>
> Nothing about the hints-on/hints-off variable escapes that. Same corpus, same
> model, same variance. **This section is not a recommendation to run it.** The
> arm and the scorer exist so the option is not blocked by missing plumbing, and
> because `--no-changelog-hints` is independently useful for debugging whether a
> hint block is *causing* a failure. Deciding the experiment is worth its cost is
> a separate judgement, and the evidence so far says it is not.

If it is run anyway: both arms need a real model, so **print these and let a
human run them**. Use the same seeds in both arms and at least 5 per arm.

```bash
for SEED in 1 2 3 4 5; do
  # arm A — with the knowledge base
  python3 -m integration.experiment run --spec elgamal-changelog-repair \
    --provider anthropic --trials 15 --seed $SEED \
    --output-dir runs/hints-on-$SEED

  # arm B — identical but for the one variable
  python3 -m integration.experiment run --spec elgamal-changelog-repair \
    --provider anthropic --trials 15 --seed $SEED --no-changelog-hints \
    --output-dir runs/hints-off-$SEED
done

python3 -m integration.experiment.compare_runs \
  --arm hints-on  runs/hints-on-*  \
  --arm hints-off runs/hints-off-* \
  --json runs/ab_report.json
```

`compare_runs` reports `CONCLUSIVE` only when the gap between arm means
exceeds the widest within-arm range, and warns about the ways a pairing goes
quietly wrong (mismatched seeds, mixed models, a run stopped by its spend cap,
both arms accidentally sharing the same hints setting). It reads only
`summary.json`, so a finished pair can be re-scored at any time.

### 9.2 Version hopping (§11 item 5)

Free to plan, expensive to run — each release is an opam switch and a full
OCaml build.

```bash
# See what is buildable and what is already cached.
python3 -m integration.experiment.ec_versions --list

# Print the plan without touching the machine.
python3 -m integration.experiment.ec_versions --version r2025.02 --dry-run

# Actually build (minutes, hundreds of MB; cached afterwards).
python3 -m integration.experiment.ec_versions --version r2025.02

# Then, on a replay_bootstrap spec:
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --version-hop --trials 15
```

---

## 10. Where to go next (evidence-based) — **items 1 and 2 are now DONE**

> **Status, 2026-08-04.** Items (1) and (2) below were implemented in
> `e13b6ecd` and `06f1c28c`. Item (3) was deliberately dropped. The analysis is
> kept because it is what motivated the work; read §10.1 for what actually
> happened, then §11 for what is genuinely still open.

Classifying all 134 tactic failures points somewhere other than the knowledge
base. `rnd` (31), `seq` (15) and `skip` (14) are **45% of all failures**, and
each fails because the model cannot see the *shape of the remaining program* --
instruction counts per side, and what the last instruction is.

Worse, the prompt misleads it: `format_active_goal_shape_hints` treats a
synchronised `equiv` (which prints `[programs are in sync]` instead of an
instruction list) as having no code left, and advises `skip.`. That advice was
emitted **121 times**; `skip` then failed with `left instruction list is not
empty`. See [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §9.

Recommended order: (1) fix the `[programs are in sync]` misdetection, (2) give
program-logic goals a structured, numbered view, (3) only then revisit the A/B,
with >= 5 seeds per cell.

### 10.1 What was done, and what it did and did not buy

**(1) and (2) — done.** Three parsing defects were fixed, then a fourth and
larger one: the hints were reading *all* open subgoals as one goal. 90% of
prompts carried more than one subgoal and 70% of the goal text was inactive.
Scoped to the active goal, measured over run C's 88 iterations:

| | before | after |
|---|---:|---:|
| Instruction counts fabricated from an inactive goal | 33 | **0** |
| Failures where the hint asserted PROGRAM-LOGIC wrongly | 17 | **10** |
| Records advised `skip.` | 121 | 57 |

**(3) — dropped, not deferred.** Run-to-run variance under identical
configuration reached 11-vs-1 accepted tactics, which exceeds any between-arm
difference the A/B could detect. It would need ~10x the spend to say anything.

**What this did not buy.** Run C repaired **1 of 4** broken lemmas. The
mechanism-level wins are real and measurable; they did not translate into
repair capability. See [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §11.3.

A separate line of work -- replaying the original script incrementally instead
of handing the whole remainder over at the first break -- is designed in
[`plans/INCREMENTAL_REPAIR_DESIGN.md`](plans/INCREMENTAL_REPAIR_DESIGN.md).
Note its §2.1: the economic premise originally given for it was retracted after
measurement, and it is now scoped to the isolated-break case only.

---

## 11. What was still open — all six now implemented

> **Status, 2026-08-04.** Items 1–6 below were worked in the order given. Five
> are finished; item 6's *infrastructure* is finished and the run itself is
> the one thing here that an agent cannot do. Suite: **563 passed, 1 skipped**
> (was 345). What each item turned out to be is recorded under it.

**1. Rule-selection by error kind — done** (`ab4707ab`).
`import_repair.py` had zero references to `ec_errors.py` and selected rules by
version window and `[migration.match]` alone, so a file with a parse error at
line 5 could have ten require-semantics rules probed against it — one EasyCrypt
invocation each — before reaching the syntax rule that fixed it.

Rules are now scored against the classification: 4 for naming the identifier
EasyCrypt blamed, 2 for a kind that can plausibly fix that error kind
(`MIGRATION_KINDS_BY_ERROR`). The incremental pass **re-classifies after every
accepted rule**, so targeting follows the file rather than its first error —
fixing a parse error uncovers a missing theory, and the theory rules move to
the front on their own.

Ordering, never exclusion. A file usually has more than one thing wrong with
it, so a rule irrelevant to the error at line 5 may be exactly what the error
at line 300 needs; and `unknown` exists precisely because this is a heuristic
over human-readable compiler output. Zero relevance means "try it later".

**2. W4.5 — a better progress measure — done** (`ca83e978`).
The graded outcome replaces the boolean:

| | |
|---|---|
| `loads` | compiles clean |
| `reached_proof` | load errors gone; a **tactic** is now at fault |
| `advanced` | still a load error, but a later or different one |
| `none` / `regressed` | |

`reached_proof` is the insight the line number could not express. Import
repair's job is to get a file past *loading*; a file whose only remaining
complaint is a bad tactic has been handed to the solver, and that is this
module finishing even though EasyCrypt still exits nonzero. `resolved` is the
headline; `improved` keeps its low bar because its two call sites are
promotion gates.

**Re-measuring the corpus found three classifier holes.** All four "unknown"
results were in-proof failures the patterns missed: EasyCrypt quotes
Lisp-style (``` `position' ```) but the pattern allowed `'position'` only;
`cannot save an incomplete proof` vs `proof is incomplete`; and ``expecting a
`memory', not a `formula'`` matched nothing. The fixtures used straight quotes
because they were written from prose rather than compiler output. This
mattered — `unknown` counts as a *load* failure, so the failures that most
clearly belonged to the solver were reported as possibly belonging to import
repair.

Re-measured (local EasyCrypt only, no LLM): **12/12 resolved — 7 compile
clean, 5 `reached_proof`, zero pre-proof errors remaining.** The manifest has
no gap left on this corpus and every remaining failure is tactic-level, which
agrees with §10.1's finding that the bottleneck is program-logic tactics.

**3. Symbol-level moves — done, 1 → 5** (`4e80aea8`).
The cause was structural, not a shortage of reorganisations: the history miner
tracked a hardcoded 16 theories out of ~127, and a move is only visible when
*both* ends are tracked. Discovery now mines every theory present at any
release tag (135, 48 seconds).

At 135 theories co-occurrence alone produces coincidences, and two facts
separate a real absorption from two unrelated edits landing in one release:

- **distinctiveness** — `add`, `mul`, `opp`, `rone`, `rzero` live in 5–8
  theories because every algebraic structure declares them. The 6325-symbol
  index is the measure. *BitWord → Ring was exactly those five names.*
- **absorption share** — OldFMap lost 118 names in r2023.09; 4 also appear in
  PolyReduce's additions, because PolyReduce arrived from Kyber that release
  and defines `reduce`. 3% is noise; the real ones are 37–100%.

Also: `nosmt` was being parsed as a declaration name (`lemma nosmt foo`) and
showed up as a "moved symbol" in four rules.

A 116-rule manifest broke the old "keep anything that does not hurt" policy —
it put `require import Commitment` and `SDist` into a hashed-ElGamal proof.
Harmless is not the bar when the repaired file is shown to the model as *this
is what was wrong with your proof*. `_minimize` takes each rule back out,
least-relevant first, and restores it only if its absence costs graded
progress. **Going 15 → 116 rules changed the repaired output not at all.**

**4. W5 — authored notes — done, ratio inverted to 14 authored / 4 derived**
(`9880fc5a`). Ten new notes, each citing a release tag, a commit SHA or a line
of EasyCrypt source — a test enforces that. The surrounding prose carries a
caveat saying "No true git-diff was possible"; one now is.

The find worth the exercise is the **r2024.09 cost-logic removal** (commit
`41c2667f`). Nothing in the corpus mentioned it, and it explains removals in
five libraries that otherwise look unrelated — AllCore lost 9 names, SmtMap 8,
DInterval 3, DBool 2, Bool 1, all in one release. What makes it prose rather
than a rule is the engine half: `cost` and `schema` were removed from
`src/ecLexer.mll`'s keyword table in the same commit, so a pre-r2024.09 proof
carrying annotations fails with a **parse error**, not an unknown symbol.
Debugging that from the line number alone would look in entirely the wrong
place, and there is no rewrite to generate — the repair is to delete them.

**5. W7 — version-hopping binaries — implemented** (`b4da8626`).
`ec_versions.py` (registry, lazy/cached/LRU-bounded provisioning via git
worktrees and opam switches) and `version_hop.py` (localization), wired in
behind `--version-hop`. Three departures from the design, recorded in the
plan's front matter:

- **Binary search, not the flowchart's walk.** A build is minutes; over the
  14-release catalog bisection is ~4 probes against up to 14. It assumes a
  tactic breaks once and stays broken — `--version-hop-strategy linear` keeps
  the exhaustive answer, and the result records which produced it.
- **Three-valued probes.** The one that would have made the feature report
  wrong answers. A 2020 proof repaired to load against r2026.06 requires FMap,
  and FMap did not exist before r2024.09 — so the file does not *load* at
  r2023.09 and the tactic is never reached. Read as "broken here", that puts
  the boundary at the wrong release. `ec_errors` is the discriminator: a
  pre-proof failure is INCONCLUSIVE and is excluded from the search.
- **Tags come from the existing clone**, not `git ls-remote` — the fork
  already carries all 14.

The plan's option (a) held: `-premises` exists only on the fork's HEAD, hop
validation only runs `llm -lastgoals`, so nothing needs rebasing.

⚠️ **No EasyCrypt binary has been built by this pipeline.** The 33 tests stub
the shell; worktree creation was verified against the live clone (correct
release, 521K on disk rather than a full clone); the opam and dune steps have
only been dry-run. Pre-build with
`python3 -m integration.experiment.ec_versions --version rYYYY.MM`.

**6. Hint uptake — plumbing built; the run should probably NOT happen**
(`d578596f`).

⚠️ **Scope correction.** This item is in tension with §10.1 and §10.1 wins. A
*different* A/B (`show_remaining_original`) was already run on this corpus and
**dropped, not deferred**, because run-to-run variance under identical
configuration reached 11-vs-1 accepted tactics —
[`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §6, whose §9.2 says to
re-open an A/B "if at all". The hints-on/hints-off variable is different but
the corpus, the model and the variance are the same, so the verdict carries.
**Nothing below should be read as a recommendation to spend on this run.**

What was built, and why it stands on its own:

- **`--no-changelog-hints`.** There was no way to turn the knowledge base off
  — `changelog_hints` was populated unconditionally. That is worth fixing
  independently of any A/B: it is how you check whether a hint block is
  *causing* a failure, which is a debugging need, not an experimental one.
- **`summary.json` `arm` block.** Two runs' summaries were previously
  indistinguishable; pairing relied on remembering which directory was which.
- **`compare_runs.py`.** The scorer exists *because* of the variance. Its
  main job is refusing to conclude: `conclusive` is True only when the gap
  between arm means exceeds the widest within-arm range, is never True with
  one run per arm, and when nothing separates the arms it prints that this is
  a statement about *power*. It makes §6's lesson mechanical instead of
  something a reader has to remember.

So the deliverable here is an instrument that says "you cannot conclude that",
plus the flag that was missing for unrelated reasons. Running the experiment
remains a judgement call, and the evidence says no.

### Closed earlier

- ~~**The 2 `test_goal_state.py` failures**~~ — fixed; that file is 16/16 green.

### Genuinely still open

- **A real W7 provision.** Build one release and confirm a hop end to end.
- **The paired A/B run itself.** Needs a real model and real spend; per
  `AGENTS.md` an agent must never answer that prompt.
- **Rule-selection tuning.** Targeting now orders rules by relevance; whether
  the affinity table's weights are right is an empirical question no run has
  asked yet.

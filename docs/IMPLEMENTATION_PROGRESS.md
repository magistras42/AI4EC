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
| Tests | 277 passing | **412 passing**, suite fully green |

W7 (version-hopping binaries) is deliberately **not** started — the handoff
says not to begin it before W1–W5, and it improves hint *precision*, which only
matters once hints reach the model every step. They now do; W7 is the natural
next item.

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
  helped", short of a counterfactual hints-disabled run

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

# Tests — 394 pass, 0 fail
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

---

## 10. Where to go next (evidence-based)

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

---

## 11. What is still open

- **W7** — version-hopping binaries. Designed in
  [`plans/ec_version_hopping_infrastructure.md`](plans/ec_version_hopping_infrastructure.md),
  correctly deferred until now. It is the natural next item.
- **W4.5** — a better progress measure than first-error line. A rule that fixes
  one error and introduces another later still reads as progress.
- **W5** — authored `import_repair_note`s for more libraries. The 14 derived
  notes state verified facts but cannot explain a semantic change the way the
  4 hand-written ones do. Still the highest-value manual work in the corpus.
- **Rule-selection by error kind.** `ec_errors.py` now provides the
  classification, but `import_repair.py` still selects rules by version window
  and `[migration.match]` only — it does not yet narrow by what actually broke.
- **Symbol-level moves.** `symbol_moved` still has exactly one instance; the
  6325-symbol index has the data to generate more.
- **Hint uptake is a proxy.** Establishing that hints *help* needs a paired
  hints-on/hints-off run on the same corpus, which requires a real model.
- **The 2 `test_goal_state.py` failures** predate this work and are untouched.

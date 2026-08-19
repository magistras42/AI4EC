# Changelog

All notable changes to this project are recorded here, grouped by the date they were implemented.

## 2026-08-04

### Retraction: the "82% tactic reuse" figure was an artifact

Commit `3035586e` and an earlier revision of `ELGAMAL_E2E_RESULTS.md` §11.4
reported that 46 of 56 accepted tactics (82%) on `INDCPA_HEG_G1` were
"verbatim from the original proof script", and used it to justify
`plans/INCREMENTAL_REPAIR_DESIGN.md`. **The figure does not survive scrutiny.**

The measurement was set membership -- does this accepted tactic appear anywhere
in the original script? That script is 33 lines but only **16 distinct**, and
is dominated by generic one-word tactics (`auto` 6x, `sp` 4x, `if; progress`
4x). Of the 46 matches, the number that were distinctive -- unique, non-generic,
longer than 8 characters -- is **zero**. Every match was `auto` matching `auto`,
which any EasyCrypt proof scores against any other.

Longest common subsequence, which respects order, gives 14/33, 3/17 and 5/83
across the three trials. Of the 46 matches, only 4 were the next-expected
original tactic; 42 were out of order.

The design document's economic justification is withdrawn (§2.1, §9 there).
What survives is narrower and still real: replay is free where it applies
(11 of 15 lemmas in run C), and an isolated break can be an isolated cheap
repair -- trial_002 fixed
`apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).` to
`apply (INDCPA_Sec Adv &m).` in 2 calls for $0.0058. But three of the four
paid trials broke early and diverged wholesale, so the design should be scoped
to the isolated-break case only.

The design doc's own §9 had demanded this re-measurement before any code was
written, on the grounds that it would "either confirm the premise or kill the
project cheaply". It killed it, at a cost of zero.

### Correction: do not disable thinking to avoid truncation

This file and the docs previously recommended `--thinking disabled` for
DeepSeek. That recommendation was wrong and the runs already on disk refute it.
Three proofs ran under both settings with the same model:

| Proof | `disabled` | `adaptive` |
|---|---|---|
| INDCPA_HEG_G1 | 9 calls, 0 accepted, STUCK | 70 calls, **18** accepted |
| G2_G3 | 8 calls, 0 accepted, STUCK | 42 calls, **10** accepted |
| G1_G2_eq | 6 calls, 1 accepted, STUCK | 4 calls, **2** accepted |

Adaptive accepted **43%** of productive calls against **4%** disabled (32/74 vs
1/23). Anthropic at `high` effort, also a thinking mode, scored 52%.

The mistake is worth recording because it is a cheap one to repeat: truncation
was the metric under active investigation, so the recommendation optimised for
truncation rather than for accepted tactics, and proposed removing the very
capability producing the truncations. Truncation is a **budget** problem -- mean
output was ~13.2k tokens against a 16,384 cap, 82% of the ceiling.

The notebook now uses `LLM_MAX_TOKENS = 32768` with thinking left adaptive, and
the example commands in both docs are corrected. Samples are small (74
productive calls vs 23) and run-to-run variance under identical config has
reached 11 vs 1 accepted, so the direction is solid but the effect size is not.

### EasyCrypt warnings were drowning the only feedback signal the agent gets

EasyCrypt reprints every file-level warning on every invocation, so the same
two notices (`global axiom Adv_choose_ll in section` and its twin) rode along
with each tactic failure: **106 of 193 error lines, 41% of all error text.**

The sharp consequence was not verbosity. `loop.py` takes **line 0** of the
message as "the reason" when reporting a failed compound tactic -- and
EasyCrypt prints warnings first, so in **53 of 53** captured failures that
line was a warning rather than the failure. The agent's diagnostic named the
wrong thing every single time.

`ec_errors.strip_warning_lines` now removes them, applied at the two sites
where error text reaches the model. Output consisting only of warnings is
returned unchanged (something beats nothing), and error *classification* is
unaffected -- verified over all 53 blobs.

### `\/` in a tactic was silently corrupting the tactic

`\/` is a **legal** JSON escape meaning `/`. So a reply carrying EasyCrypt's
disjunction parsed cleanly and yielded the wrong tactic -- `smt(a \/ b).`
decoded to `smt(a / b).`, sent to EasyCrypt with nothing raised anywhere. The
existing repair pass ran only as a fallback *after* `json.loads` failed, so it
never saw this case: nothing ever failed.

Two further fixes in the same parser:

- The repair now runs **first** rather than as a fallback, which is what makes
  the case above reachable. It is idempotent, so well-formed replies are
  unaffected.
- **Raw control characters** inside strings are escaped instead of rejected. A
  multi-line tactic (`proc.\nwp.\nskip.`) is natural to write and is illegal
  JSON; it was being thrown away entirely.

Found by testing the parser directly against realistic EasyCrypt replies, not
from the run logs -- a silent corruption leaves no trace in them by
construction.

### The `format_error` spike was not a parser bug

51 of the 52 `format_error` outcomes in `run-20260803T211932Z` were empty
replies with `finish_reason='length'` -- the thinking-budget exhaustion already
fixed via the truncation retry and the `LlmProviderError` reclassification;
that run predates both. Exactly **one** was genuinely malformed JSON. The
parser fixes above are real, but they are not what caused those 52, and no
claim is made here that they would have.

### Repo hygiene

Added a root `pytest.ini`. There was none, so a bare `pytest` descended into
`proof_corpus/eval/Cryptolib` -- vendored third-party suites with their own
config that fail to collect without generated vectors -- reporting 4 collection
errors unrelated to this project. `testpaths` is now pinned and the
`integration` marker registered.

## 2026-08-03

### The prompt was misreading program-logic goals (45% of failures)

Classifying all 134 tactic failures pointed away from the knowledge base:
`rnd` (31), `seq` (15) and `skip` (14) were **45%** of them, and each fails
because the model cannot see the shape of the remaining program -- instruction
counts per side, and what the last instruction is.

`format_active_goal_shape_hints` was actively making that worse. Three parsing
defects in `_program_statement_block`, each independently sufficient to make a
non-empty program look empty:

1. **Only ONE line after `pre =` was dropped**, so a multi-line precondition
   leaked in and was mistaken for statements. Since a precondition of plain
   equalities contains no instruction markers, the leaked text read as "no
   code".
2. **`<$` (random sampling) matched no statement pattern at all.** A goal whose
   remaining code was a sampling therefore read as empty -- and `rnd`, the
   tactic for samplings, was the single largest failure bucket.
3. **`[programs are in sync]`** -- EasyCrypt's way of saying both sides hold
   IDENTICAL remaining code -- was read as an absent statement list, i.e.
   emptiness.

The result was the advice *"little or no code left ... Apply `skip.`"*, emitted
**121 times** across the measured runs, with the chosen tactic then failing in
53 of those. In 2 of the 53 the failure was `skip` reporting `left instruction
list is not empty` -- the goal directly contradicting the prompt. The other 51
failed on other tactics (`rnd`, `seq`) and are only circumstantially linked to
the bad advice.

Now: statements are extracted by recognising instruction forms (`<$`, `<-`,
`<@`, `while`, `if`, an `(N)` index, or a closed procedure header) rather than
by line position; `[programs are in sync]` produces an explicit do-NOT-skip
bullet; and program-logic goals gain structured facts -- per-side instruction
counts (for `seq N M`) and the last instruction's kind (for `rnd`/`wp`/`call`).

Measured over 390 real goal/outcome records captured from the DeepSeek runs and
checked in as `integration/tests/fixtures/goals/elgamal_goals.json`:

| | before | after |
|---|---:|---:|
| records advised to `skip.` | 121 | **57** |
| ...where the chosen tactic then failed | 53 | **27** |
| ...directly contradicted by `left instruction list is not empty` | 2 | **0** |
| distinct goals given instruction counts | 0 | **43** |

A caveat on reading this table: only the third row is direct evidence that the
advice was wrong, and it covers just 2 records. The 53 -> 27 row is the weaker
claim -- most of those failures were `rnd`/`seq` tactics whose connection to the
`skip` advice is circumstantial. What is established is that the parser no
longer reports emptiness for programs that demonstrably have instructions; that
repair performance improves is *not* established and needs a run.

The advice is retained where it is correct -- an empty program still gets it,
now with a caveat naming the exact error to expect if statements are present
but unprinted. A first attempt removed the false advice *and* the true advice;
the test for a genuinely empty program caught it.

### Thinking was eating the whole output budget (the dominant failure mode)

Across four DeepSeek trials, **79 of 85** recoverable format errors were the
same thing: `finish_reason='length'` with an EMPTY visible reply. The model
spent the entire 16,384-token output budget on hidden reasoning and never wrote
the JSON action. Mean output was 12.3k tokens per call against that cap, so
calls were routinely hitting the ceiling. 36-70% of every trial's iterations
were lost this way -- far more than any prompt or retrieval change was worth.

Two things were wrong. The harness told the model to "escape your backslashes",
which is nonsense advice for a reply that contains nothing; only **2 of 85**
errors were genuine escaping problems. And a wasted call still advanced the
stuck counter, so budget exhaustion looked like the model being stuck.

`_decide_json` now detects a truncated empty reply (`length` on
OpenAI-compatible providers, `max_tokens` on Anthropic) and retries once with
thinking **disabled**, which guarantees the budget goes to the answer. If that
still comes back empty the message says to raise `--llm-max-tokens` instead of
blaming formatting.

> **The retry is a backstop, not a recommended configuration.** Disabling
> thinking as a *default* is measurably wrong -- see the 2026-08-04 entry.
> The right fix for routine truncation is a bigger output budget.

This also invalidates the earlier claim in this file that backslash escaping
explained arm B's zero-accepted result -- it did not; budget exhaustion did.

### A malformed provider response no longer kills a trial

DeepSeek intermittently returns HTTP 200 with **no `choices`** in the body
(more often with thinking on). `response.choices[0]` then raised `TypeError`,
which is not an `LlmFormatError`, so `loop.py`'s catch-all converted a
transient hiccup into a terminal `ExitReason.LLM_ERROR`.

Found by running experiments, not by reading code: it destroyed **4 of 7**
completed trials across an A/B, discarding 70 and 31 iterations of real
progress, and one arm died after only 4 iterations. Two of three comparisons
in that experiment are unusable as a result -- a completed run against a
crash-truncated one is not a comparison.

`OpenAICompatBackend.complete` now raises `LlmFormatError` for a missing/empty
`choices` and for a choice with no message, which the loop already handles by
recording a format error and retrying the step. The exposure predates the
backend refactor -- `response.choices[0]` was always unguarded -- but thinking
modes surface it far more often, alongside the `finish_reason='length'`
empty-content replies seen in the same runs.

### The replay-bootstrap solver sees the rest of the original proof

`run_replay_bootstrap_trial` discarded every original tactic after the break:
the solver resumed from the replayed prefix and never saw where the 2020 author
was heading. Measured on one run, **46 original tactics across three lemmas**
were withheld. It now passes them as `informal_proof` (and writes
`informal_proof.md`, which this mode never produced), behind
`ReplayBootstrapConfig.show_remaining_original` for A/B work.

The generic "Broken formal proof -- it does NOT compile" heading would be
**false** here: only the first remaining tactic is known-broken, the rest were
never reached and are untested. Saying otherwise invites the model to discard
the structure it most needs, so `build_prompt` gained an optional
`informal_proof_heading` and this mode supplies an accurate one.

First measured effect, on `INDCPA_Security`: the 2020 proof passed two
liveness axioms modern EasyCrypt no longer takes,
`apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).` Shown that as a stale
reference the model submitted `apply (INDCPA_Sec Adv &m).` -- dropping the
obsolete arguments -- and EasyCrypt accepted it (`validate_file` returncode 0).
Without the reference the same lemma ran 10 iterations and was never solved.
That is **n=1 on the easiest lemma**, where the reference was a single line
containing the answer modulo two arguments; the two lemmas that would have
tested it properly both hit the crash above and are being re-run.

### `port_legacy_easycrypt_syntax` is off by default; import repair does the porting

`ElGamalCorpus(port_legacy_syntax=...)` now defaults to **False**. Sandboxes
ship the raw 2020 syntax and `integration/agent/import_repair.py` ports it from
`proof_corpus/ec_migrations.toml`, verifying every edit against EasyCrypt.

This is the migration handoff 4.4 asked for, and it is measured rather than
assumed. Full corpus, both ways:

| | pre-ported | raw (new default) |
|---|---|---|
| Goals reachable before repair | 15/15 | 3/15 |
| Trials lost to `goal_unreachable` | 0 | **0** |
| `import_repair` attempted | 0 | 12 |
| ...improved | — | **12 (100%)** |
| ...made the file load | — | 7 |
| Mean first-error-line advance | — | **+547.8** |
| Fully replayed | 11/15 | 11/15 |

Identical trial coverage. The four rules the manifest applies
(`smtmap-symbols-moved-to-fmap-r2025.02`, `proc-star-removed`,
`declare-module-ascription`, `old-module-restriction-sets`) are the same four
fixes the hardcoded function performed -- but version-pinned, commit-sourced,
and applicable to any corpus rather than to one file.

Pre-porting was also **hiding the subsystem under study**: it made the file
load, so `goal_unreachable` never fired, 6.1's mechanism was never exercised in
any run, and the `repair_doc` import notes -- which attach only to pre-proof
failures -- consumed 66% of every hint block while being structurally unable to
help. Both are now reachable.

`port_legacy_syntax=True` still works, for isolating tactic-level repair from
import-level repair and for reproducing prior experiments.

### The two long-standing `test_goal_state.py` failures were stale test doubles

Diagnosed rather than kept as a footnote. Both fakes in that file declared
`decide(self, prompt)`, but `LlmClient.decide` grew a `thinking=` keyword when
adaptive thinking landed. The loop's call therefore raised `TypeError`, which
surfaced as `ExitReason.LLM_ERROR` -- so
`test_agent_completes_full_hoare_proof` saw `LLM_ERROR` instead of `COMPLETE`,
and `test_agent_does_not_complete_after_proc_alone` recorded **zero** fake
calls (`assert 0 >= 2`). Every other fake in the suite already absorbed the
kwarg with `**_kwargs`; these two were missed.

Confirmed identical at `HEAD` in a clean worktree before touching them, so they
predate this branch's work. Fixed by adding `**_kwargs` to both. The suite is
now **fully green** -- there are no known-failing tests.

### Retrieval is steered by the classified error kind

The DeepSeek run made the gap concrete: all three broken ElGamal lemmas failed
on program logic (`seq`, `rnd`, `wp`) and **every** hint surfaced was about the
SmtMap->FMap import split. Confident, long, and unable to fix the failure.

`get_repair_hints_text` now takes an `error_kind` from `ec_errors.classify_error`
and routes on it. Proof-level failures query the changelog index's `by_tactic`
bucket (new `get_tactic_change_hints_by_release`) and suppress the
`repair_doc` library notes, whose load-bearing field is literally
`import_repair_note`. Load failures keep the existing import-shaped path
unchanged. `error_kind=None` preserves the old undirected behaviour.

Measured on the real `seq 1 1 : ...` failure from trial 0:

| | Retrieved | Block size |
|---|---|---|
| Before | r2023.09 glob-unsoundness, operator-unfolding, section entries | 4,559 chars |
| After | **r2026.02 `[seq]: remove bck/fwd option + cleanup`** | 458 chars |

Both call sites classify before retrieving, and `repair_hints_hop.json` now
records the `failure_kind` alongside the release it hopped to.

### Notebook, plus the experiment knobs it needs

`notebooks/elgamal_repair_experiment.ipynb`, adapted from the `elgamal-broken-repair`
notebook on another branch. It defaults to this branch's
`elgamal-changelog-repair` spec, switches provider in one line, and separates
**model repairs from zero-LLM replays** — the distinction `successes` hides.

Three `ExperimentConfig` fields the notebook depends on did not exist here and
were added: `sort_by_difficulty` (deterministic shortest-first, so runs are
comparable without a shared seed), `adaptive_steps_multiplier` /
`min_adaptive_steps` (step budget scaled to each proof's own tactic count
instead of one flat `--max-steps`), and `cost_limit_usd`, which builds the
`SpendBudget` added above.

### `hint_uptake` was measuring nothing

`_accepted_tactics` read a top-level `"iterations"` key. `AgentRunLog` writes
`{"source", "work_copy", "events": [...]}` with per-step records tagged
`{"event": "iteration"}`, so the lookup always returned `[]` and the metric was
pinned at 0.0 by construction — reporting a finding where it had measured
nothing. Fixed, with the methodological guard that matters more: a 0 rate over
0 accepted tactics means *not measurable*, not *hints ignored*, so the
aggregate now carries `trials_with_accepted_tactics` and
`rate_among_scorable` (`None` when nothing qualifies).

### A run-level spend cap (`--max-spend-usd`)

Spend could previously be bounded only indirectly -- `--trials` x `--max-steps`
caps call *count*, `--llm-max-tokens` caps each call's output -- and the cost
estimate was computed only after the run had finished. "Try this for about a
dollar" was not expressible.

`integration/agent/budget.py` adds `SpendBudget`: one mutable object shared by
every trial in a run (unlike `TokenUsage`, which the runner rebuilds per trial
for per-trial reporting), priced with the same `pricing.py` tables that produce
`estimated_cost`. The loop checks it *before* each chat call and exits with the
new `ExitReason.BUDGET_EXHAUSTED`; `run_experiment` stops launching further
trials and records `budget` / `budget_stopped` in `summary.json`. A
budget-stopped trial is counted as neither a success nor an error, so it cannot
corrupt the success rate.

Two limits are documented rather than papered over: the cap is checked after
usage is known, so actual spend can **overshoot by at most one call**; and a cap
requested for a model with no published rates is **refused outright** rather
than silently not enforced, because a budget that quietly does nothing is worse
than no budget.

The confirmation banner now shows the cap (or states that none is set).

### The knowledge base now reaches the model (roadmap W1)

`_build_spec` and `_with_sandbox_dir` rebuilt `ExperimentSpec` field-by-field
to inject `--data-dir`/sandbox paths, and never copied `replay_bootstrap`.
It fell back to its dataclass default of `None`, so `run_trial` saw every mode
field unset and dispatched `--spec elgamal-changelog-repair` down the
**mutation** path with `spec.mutations = None`. Because
`repair_bootstrap.py` is the only producer of `AgentConfig.changelog_hints`,
that one dropped field made the entire `proof_corpus/` knowledge base — 913
changelog entries, the 6325-symbol index, 18 library notes, 15 migration rules
— unreachable from any CLI-launched experiment.

Both rebuilds now use `dataclasses.replace`, so a sixth mode cannot be lost the
same way. `_experiment_mode` gained its missing `replay_bootstrap` branch
(`summary.json` had been labelling these runs `"mutation"` while
`TrialResult.mode` said otherwise). `test_cli_spec_rebuild.py` asserts over
`dataclasses.fields` that every field of every registered spec survives both
rebuilds — the regression that should have caught this.

Verified end to end on ElGamal: 6/6 solver prompts now contain a populated
`## Known EasyCrypt library changes` section.

### Three interchangeable solver providers, including Claude

`llm.py` split along a `ChatBackend` protocol at the transport boundary only.
`OpenAICompatBackend` serves LM Studio (local Gemma et al.) and DeepSeek;
the new `AnthropicBackend` serves Claude via the Messages API. Reply parsing,
action-JSON extraction, the `/\`-escape repair and tactic salvage are
provider-independent and stayed in one place; `loop.py` is provider-blind.

Anthropic is not an OpenAI base-URL swap: system prompt is a top-level
parameter, thinking/effort are first-class request fields, and Opus 5 rejects
`temperature`/`top_p`/`top_k` outright. Requests stream and reassemble via
`get_final_message()` so a minutes-long adaptive-thinking step cannot die to an
idle-connection timeout. Defaults: `claude-opus-5`, adaptive thinking with
summarized display, effort `high`.

`--provider {lm_studio,deepseek,anthropic}` on both CLIs (`--deepseek` kept as
an alias). The DeepSeek confirmation gate generalized to
`paid_confirm.py` and now covers Claude too, with published per-token rates in
the banner. **AGENTS.md's rule is unchanged and now provider-independent: an
agent must never answer that prompt.** Invalid combinations (Claude's
disabled-thinking above `high` effort; DeepSeek given `xhigh`) are rejected at
startup rather than as a 400 mid-trial.

### Version endpoints detected instead of hardcoded (W6)

`ec_version.py`. The spec hardcoded `r2022.04` → `r2026.07`, self-described as
"a broad illustrative default". The target is genuinely knowable and that value
was **wrong**: `ec.exe` has no `--version`, but `git describe --tags` on the
tree it was built from reports `r2026.06-6-g07e77d8c`, so the pinned target
named a release one ahead of the binary actually installed. Detection snaps
onto cataloged releases only, and reports method/confidence rather than a bare
string — a parsed `git describe` and a date-bracket guess must not reach the
model with equal authority. The 2020-era ElGamal source stays honestly
undetected (fail-open), which is the same convention `releases_in_range` uses.

### Error classification, live hint hops, import repair in the agent loop

- **`ec_errors.py` (W4 step 1)** — classifies failures by kind, handling both
  EasyCrypt location formats. The load-vs-proof boundary is now explicit:
  ElGamal line 108 is `unknown_theory` (import repair's job), line 453 is
  `tactic_error` (the solver's).
- **W3** — changelog hints are re-fetched on each tactic failure, threading
  `consumed_versions` so successive failures hop to the next release rather
  than re-surfacing the one already shown. Opt-in via
  `live_changelog_hints`; only version-drift experiments enable it.
- **`import_repair` in `loop.py`** — a single-file `python3 -m integration.agent
  FILE.ec` run on an unloadable file previously just failed. Now gated behind
  `--import-repair`, skipped when the classifier says the failure is
  proof-level, and its summary is prepended to the same prompt section.

### Repair outcomes are finally reported (W8)

`repair_metrics.py` aggregates per-trial artifacts into `summary.json`:
replayed-fraction distribution, the zero-LLM `fully_replayed` rate,
import-repair attempt/success and first-error-line advance, changelog hop
distribution, and hint uptake (whether a hinted identifier appears in a tactic
EasyCrypt accepted). Pure derivation from files trials already wrote, so it can
re-score a finished run. Measured on ElGamal: **3/6 fully replayed**, mean
replayed fraction **0.67**.

Tests: 364 passing (was 277), same 2 pre-existing `test_goal_state.py` failures.

## 2026-07-31

### Downstream artifacts rebuilt after the classification pass

Classifying the 637 git-log entries changed `changelog.yaml`, and three derived
artifacts read it. Rebuilt in dependency order:

| Artifact | Effect |
|---|---|
| `output/changelog_index.json` | 276 → **913 entries**; 554 with resolved names; **84 import-relevant** |
| `output/exposure_results.json` | **44 of 77** breaking-change scores changed |
| `output/ladder.md` | **54 of 75** corpus ranks moved |

The exposure shift is the consequential one and it is not a rounding artifact.
Repos predating r2025.02 used to cross four releases that were **empty**, so
they scored nothing for them; those releases now carry 637 classified entries
and the same repos score 382 → **1211**. The ladder had been systematically
under-weighting the oldest, most-broken repos — exactly the population this
project exists to repair. Any corpus selection made off the previous ladder is
suspect.

`repair_docs_index.json` and `ec_migrations.toml` regenerated byte-identically
apart from their `generated_at` stamps. Both *do* read `changelog_index.json`
(for per-theory "what changed" enrichment and rule provenance respectively), so
they must still be rebuilt **after** it — they were, which is why the content
did not move. Run order is documented in the new
[`proof_corpus/README.md`](proof_corpus/README.md).

### Doc corrections

`PROOF_REPAIR_HANDOFF.md` and `EC_MIGRATIONS_SCHEMA.md` described a **10**-rule
manifest with `symbol_moved` "in the vocabulary but unused", and quoted an
ElGamal run listing rule ids (`smtmap-split-fmap`, `allcore-missing-*`) that no
longer exist. Both predated the rewrite of `build_ec_migrations.py` to derive
from `library_history.json`. Actual current state, verified by regenerating and
re-running:

- **15 migrations** — 12 derived from git history, 3 curated engine rules;
  9 `require_semantics`, 3 `syntax_change`, 1 each `symbol_moved` /
  `theory_added` / `theory_renamed`.
- `symbol_moved` **is** used: `smtmap-symbols-moved-to-fmap-r2025.02` carries
  all 125 moved declarations.
- ElGamal re-measured: **considered 4, kept 4, rejected 0**, first error
  108 → 357 → 453. `declare-module-ascription` is kept on *non-regression* —
  under the original "strictly decreases" criterion it would have been rolled
  back.

Also removed all `modified-shannonprover/` content from the handoff (the folder
is being dropped from the branch). Nothing in `integration/` imports from it;
the `"Ported from shannon-prover"` docstrings are attribution only, and the
design lineage is preserved as a note in §0.

## 2026-07-30

### Pre-proof import repair (`ec_migrations.toml` + `integration/agent/import_repair.py`)

Import breakage is *pre-proof*: when a `require import` no longer resolves,
EasyCrypt cannot load the file at all, so `llm -upto` returns nonzero and
`repair_bootstrap.py` recorded `skip_reason="goal_unreachable"` — the trial
ended before a single tactic was tried, and none of the changelog evidence ever
got a chance to help.

#### New: `proof_corpus/scripts/analyze_library_history.py`

Mines the EasyCrypt git history for 16 standard-library theories (AllCore,
Bool, Core, CoreMap, CoreReal, DBool, DInterval, Distr, FMap, FSet, Int, Logic,
Pervasive, PROM, Real, SmtMap) and records, per release: file add/move/delete
events, and the declarations added and removed from the content diff. Output:
`output/library_history.json`.

This replaces prose with evidence. The `repair_doc/*.json` files were written
by reading the current sources and the release notes; their own `caveat` says
*"No true git-diff was possible"*. This does the diff, so every fact carries a
commit SHA and a release tag and is checkable with `git show`.

The headline case falls out on its own: **125 declarations leave `SmtMap` and
arrive in `FMap` in r2025.02** — the split, enumerated exactly, versus the
dozen names the hand-written note happened to mention.

Three parsing details that mattered: `--follow` cannot chase a theory split
(git records it as delete+add, not a rename), so historical paths are found by
basename glob — without it SmtMap showed 1 commit instead of 34; `git log
--name-status -p` emits name-status *instead of* the patch, so statuses and
diffs need separate passes, and asking for both silently yielded zero symbol
events; and release ordering must sort by **tag name**, not commit date —
r2026.07's tagged commit predates r2026.06's by four weeks.

#### New: `proof_corpus/ec_migrations.toml`

A manifest of per-version rewrite rules — what libraries were created, moved or
had symbols move between them, and **what to do to a file** about it. Library
rules are **derived from `library_history.json`**, not from `repair_doc/`:
symbol moves, theory lifecycle events, and `require export` closure gaps
(`AllCore.ec` is four lines and exports only Core/Int/Real/Xint, so everything
else needs an explicit require — derived from the source, not asserted).

Parser and module-system changes (`proc *`, `declare module X : T`, memory
restrictions) are *engine* changes no theory-file history reveals, so they stay
curated in the generator — with version pins from commits recovered by
`--git-log`. TOML because engine rules must be hand-written and derived rules
must be reviewable; `tomllib` is stdlib, so nothing new is vendored.

Each `[[migration]]` carries a version window (`breaks_at`), a `[migration.match]`
(requires_theory / missing_require / uses_symbols / not_uses_symbols /
matches_regex), and ordered `[[migration.action]]`s (`add_require`,
`replace_require`, `remove_require`, `rename_symbol`, `replace_regex`,
`add_pragma`). `build_ec_migrations.py` round-trips its own output through
`tomllib` before writing, so a manifest that would fail to parse is caught at
generation time rather than at repair time.

Current output: **15 migrations — 12 derived from git history, 3 curated engine
rules** — plus a `[[library]]` history summary per tracked theory.

Two correctness guards worth noting. No rule is keyed to the earliest tag:
every commit reachable from r2022.04 is attributed to it, including a decade of
pre-tag history, so "changed in r2022.04" cannot be distinguished from "existed
before our window". And `Pervasive`/`Logic` are excluded from export-gap rules
because the engine exports them into every file (`ecCommands.ml:876`) — telling
a proof to require them would be actively wrong.

**Every action is line-preserving** — `ProofCase` records absolute lemma line
numbers, so `add_require` extends an existing line, `add_pragma` folds onto
line 1, `remove_require` blanks rather than deletes. `apply_actions` asserts
the line count is unchanged.

Version pins come from evidence, and three were only pinnable because the
git-log source (below) recovered EasyCrypt's undocumented releases:
`proc *` removed in **r2023.09** (`57be028cc`, `df8a2f924`), `declare` restricted
the same release (`dc50f44bf`, `6d0e46493`), memory restrictions reworked in
**r2024.09** (`b53230696`, `9b940b4f5`). Those three are exactly what
`corpora/elgamal.py::port_legacy_easycrypt_syntax` hardcodes for one file; they
now apply to any corpus.

Schema: `proof_corpus/EC_MIGRATIONS_SCHEMA.md`.

#### New: `integration/agent/import_repair.py`

Selects rules by version window, matches, applies in bulk, and **verifies every
change against EasyCrypt** via `validate_file`. If bulk apply does not make the
file load, it retries incrementally, keeping a rule unless EasyCrypt stops
*earlier* than before — non-regression rather than strict improvement, because
a rule fixing something at line 300 shows no movement while an unrelated parse
error sits at line 108. The source file is never modified.

Wired into `repair_bootstrap.py`'s `goal_unreachable` branch: the trial now
attempts import repair before giving up, writes `import_repair.json`, and
prepends a summary of what was rewritten to the solver's prompt (it is proving
against a file the harness edited).

Measured on `data/derens99-ElGamal-proof/hashedelgamal.ec`: first error moves
**line 108 → 357 → 453**, with 4 migrations kept (the history-derived
`smtmap-symbols-moved-to-fmap-r2025.02` plus the three engine rules). The remaining error is
`invalid 'position' parameter` on a `seq 1 1 :` tactic — a genuinely broken
proof, not an import problem, which is exactly the intended boundary.

#### `collect_changelog.py` — git-log source

`--git-log <ec-clone>` derives entries from `git log <prev-tag>..<tag>` for
releases whose notes are empty, which is how EasyCrypt ships everything before
r2025.02. Non-merge commits only, filtered to `theories/`, `src/`, `libs/` by
default (`--git-log-paths`), with `--git-log-all-releases` to cover documented
releases too and caps on files/commits per range.

Recovered **652 commits** across the four undocumented releases, taking the
catalog from **10/14 to 14/14 releases**. `process_changelog.py` classifies
commit entries alongside PR entries (`source`/`sha` fields, short-SHA ids,
a `COMMIT_NOISE_RE` that drops merges/bumps/typos before they cost an API call,
and a prompt note that commit subjects deserve a lower relevance bar);
`build_changelog_index.py` links them to `/commit/<sha>` rather than `/pull/N`.

The LLM classification pass over the 637 new commit entries has now been run
(45 chunks, no parse failures, no API errors). `changelog.yaml` holds **913
entries across 14 releases** — 276 from release notes, 637 from git log — with
no unclassified rows:

| relevance | entries | | kind | entries |
|---|---|---|---|---|
| high | 155 | | internal | 273 |
| medium | 391 | | lemma_added | 193 |
| low | 367 | | mechanism_change | 172 |
| | | | tactic_change | 161 |
| | | | syntax_change | 64 |

Reproduce with:

```bash
python3 proof_corpus/scripts/collect_changelog.py --repo EasyCrypt/easycrypt \
    --out proof_corpus/output/raw_releases.json --with-pr-details \
    --git-log integration/extern/easycrypt
python3 proof_corpus/scripts/process_changelog.py \
    --in proof_corpus/output/raw_releases.json \
    --out proof_corpus/output/changelog.yaml \
    --cache proof_corpus/output/llm_cache.json
```

#### Classification moved to the Message Batches API

That run took ~20 minutes, almost all of it waiting: the classifier issued one
blocking `messages.create()` per 15-entry chunk, 45 chunks, strictly serial,
because chunking happened *inside* the per-release loop.

`process_changelog.py` now collects every unclassified entry across all
releases first, chunks once, and submits a single
`client.messages.batches.create()` job — half the token cost, one round trip.
Results come back unordered, so each request carries a `custom_id` and is
stitched back onto its chunk by that id rather than by position. `--sync`
keeps the old inline path for when immediacy beats the discount;
`--poll-interval` and `--batch-timeout` control the wait.

Restructuring forced the release rendering to move after the classification
pass: `asdict()` copies, so building the output dicts inside the release loop
would have snapshotted the entries before the batch filled them in.

**Fixed alongside it — cache poisoning.** When a chunk's response failed to
parse, the old loop still wrote every entry in it to `llm_cache.json`. Those
rows held `summary: null` / `repair_hint: null`, and because the cache is
consulted before the LLM on every run, the bad rows suppressed their own retry
permanently. `apply_response` now returns whether it succeeded, and only
entries whose `needs_llm` actually cleared get cached; the rest are reported
and retried next run.

#### Tests

`integration/tests/test_import_repair.py` — 33 tests covering manifest parsing
(including the real checked-in file), version selection, matching, each action's
line-preserving edit, both EasyCrypt error formats, and the bulk/incremental
verification loop with EasyCrypt stubbed. Four assert properties of the
generated manifest directly: that the SmtMap→FMap move is derived from history
with ≥100 symbols, that no rule tells a proof to require an engine-preloaded
theory, and that nothing is keyed to the earliest tag.

`integration/tests/test_process_changelog.py` — 18 tests over the batch
classification pass with the API stubbed. The stub yields results **reversed**,
so any code that matches responses positionally instead of by `custom_id`
fails; separate tests cover the parse-failure guard, an errored chunk leaving
its entries retryable, poll-until-ended, the timeout, and an end-to-end
`main()` assertion that a failed chunk writes an *empty* cache and that a
second run with a warm cache calls the API zero times.

### Reprocessed repair docs (`repair_docs_index.json`) and symbol→theory resolution

The per-library docs in `repair_doc/*_lib.json` were the project's best
import-repair knowledge and its least usable asset: 300+ word single-paragraph
summaries re-sent on every agent step, `version_diffs_found` a list in 5 files
and a bare sentence in 13, `requires` as prose, an `import_repair_note` on only
**4 of 18**, and coverage of just **18 of 128** theories — so "which theory
provides `frng`?" was unanswerable for 86% of the tree.

#### New: `proof_corpus/scripts/build_repair_docs.py`

Pure derivation (no network, no LLM) from the authored docs + the real
EasyCrypt theory sources + `changelog_index.json`. The authored files are never
modified. Produces:

- **`libraries[]`** — authored summary condensed on a sentence boundary,
  `import_repair_note` preserved verbatim where written and **derived where
  not** (now 18/18: 4 authored + 14 derived), `version_notes` normalized with
  the 13 "None found by name…" sentences dropped, plus `requires` / `imports` /
  `require_exports` / `clones` / declaration counts **parsed from the real
  source**, and the changelog entries that touched that theory.
- **`theories{}`** — the same derived facts for all **128** theories,
  documented or not.
- **`symbol_index{}`** — `symbol -> [declaring theories]` over **6325**
  symbols (**502** ambiguous). This answers the question nothing in the system
  could ask: `ec.exe llm -premises` reports what **is** in scope, never what
  **could** be, so an "unknown symbol" error had no route back to
  `require import <Theory>.` Ambiguity is preserved — `eq_except` really is
  declared by both `FMap` and `SmtMap`, the two sides of the r2025.02 split.

Parses the syntax variants the real tree uses: multi-name requires, the
`(*--*)` alignment comment, `require export`, `from X require`, bare `import`,
`clone import/include`, and `rename "x" as "y"` (which is how `DBool` exports
`dbool` — declared nowhere with `op`/`lemma`, and the subject of one of the
four hand-written import notes).

Cross-checking the authored `requires` prose against the parsed source found
**2 genuine disagreements**: `Distr.ec:26` requires `Discrete` and the prose
omits it; `PROM`'s prose names `FullEager`/`FinEager`, which are theories
declared *inside* PROM.ec. Both are reported, not silently corrected.

Schema and rationale: `proof_corpus/REPAIR_DOCS_INDEX_SCHEMA.md`.

#### `integration/agent/repair_hints.py`

- `get_repair_doc_snippets` uses the condensed records when the index exists
  and falls back to the raw authored files otherwise. Scoring gains two signals
  the raw files cannot provide: **the theory declares a matched symbol**
  (weighted highest) and a hit on the theory's own name.
- New `load_repair_docs_index` / `resolve_symbol_theories`.
- The prompt now **leads** with symbol resolution, ahead of changelog entries —
  when a name no longer resolves, "it lives in theory T, `require import T.`"
  is the most directly actionable fact available, and it is checked against the
  installed sources rather than inferred from prose.
- `_safe_tokenize`: the index path no longer hard-fails when proof_corpus's
  `retrieve_entries.py` is absent — its primary signals need no tokenizer.

#### `collect_changelog.py`

Records `body_chars` / `bullet_count` per release and a top-level `coverage`
block, and **warns** when a release has no parseable notes. EasyCrypt's own
r2022.04–r2024.09 ship empty or one-line bodies; downstream, "no entries in
this range" was indistinguishable from "nothing changed in this range".

#### Tests

`integration/tests/test_repair_docs_index.py` — 22 tests covering source
parsing, the symbol index, note derivation, prose cross-checking, and the
retrieval/prompt path.

### Indexed changelog format (`changelog_index.json`)

`proof_corpus/output/changelog.yaml`'s nested `releases -> entries` shape was a
poor query surface for both proof repair and corpus analysis. Measured on the
current 14-release / 276-entry catalog:

- Only **14.5%** of `identifiers` slots named a real declared EasyCrypt symbol;
  the most frequent "identifiers" were `tactic`, `error`, `code`, `use`,
  `docker`, `from`. **51.7%** of high/medium-relevance entries had *no*
  resolvable identifier, so Tier-B retrieval could never fire for them.
- All **276/276** PRs in `raw_releases.json` carry `changed_files`, `labels`
  and a PR body — **69** touching `theories/` — and every bit of it was
  discarded after classification.
- No random access: "what changed about `FMap`?" meant scanning every release.
- Theory names, lemma names and tactic names were flattened into one untyped list.

#### New: `proof_corpus/scripts/build_changelog_index.py`

Pure derivation (no network, no API key, no LLM calls) from `changelog.yaml` +
`raw_releases.json` + the EasyCrypt theory sources + `repair_doc/tactics_ref.json`.
Produces a flat entry list with stable `key`s, integer release `ordinal`s,
typed name buckets (`symbols` / `tactics` / `theories_touched` /
`theories_mentioned` / `title_tokens`), exact `touches` + `areas` from changed
files, an `import_relevant` flag (prose-driven: touching a theory file is not sufficient), a materialized `breaking_weight`, PR
`url`/`labels`/`body_excerpt`, and inverted indexes (`by_symbol`, `by_theory`,
`by_tactic`, `by_kind`, `by_version`, `import_relevant`).

Names are resolved by graded evidence: backticked prose accepted on sight;
classifier-claimed names only when they resolve *and* look like deliberate
identifiers or are corroborated by a touched theory. Tactic-ness beats
symbol-ness; owner expansion is restricted to distinctive symbols. High/medium
entries carrying a usable name: **73/151 → 103/151**.

Also records **coverage**: `r2022.04`, `r2023.09`, `r2024.01` and `r2024.09`
have empty release notes upstream, so the catalog effectively starts at
`r2025.02` — relevant because `elgamal-changelog-repair` declares
`source_ec_version="r2022.04"` for a 2020-era corpus.

Schema and rationale: `proof_corpus/CHANGELOG_INDEX_SCHEMA.md`.

#### `retrieve_entries.py`

- Reads **both** formats, dispatching on content rather than filename;
  `load_changelog` materializes the legacy nested `releases -> entries` view
  from the flat index. The four functions both `repair_hints.py` modules
  depend on keep their signatures and semantics.
- Typed matching with `match_kinds` / `match_strength` (symbol 4.0 >
  theory_touched 3.0 > tactic 2.5 > theory_mentioned 2.0 > identifier 1.0).
- **Tier A capped** (default a third of the budget). Previously all 6 of a
  6-slot budget went to unmatched "structural change in range" entries and the
  real `SmtMap`/`FMap` hit never appeared.
- **A structural entry keeps its name match** — `mechanism_change`/`high`
  entries used to be filed as generic Tier A and lose their overlap entirely,
  which is exactly what happened to the SmtMap→FMap split (#605). No
  structural bonus for a bare `identifier` hit.
- New helpers: `load_index`, `resolve_version`, `releases_between(strict=)`,
  `entries_for_symbols` / `_theories` / `_tactics`, `import_relevant_entries`,
  `coverage_gap`, `prompt_ready`; new `--theory` / `--symbol` /
  `--import-relevant` CLI modes.

#### `integration/agent/repair_hints.py` (and the vendored shannon-prover copy)

- `resolve_changelog_path` prefers `changelog_index.json`, falls back to
  `changelog.yaml`.
- `format_repair_hints_for_prompt` now renders what was previously discarded:
  entry `kind`, which names matched and why, `theories_touched`, the PR URL,
  and — for repair_doc hits — **`import_repair_note`** (written specifically
  for repairing a `require import` that no longer resolves; never truncated),
  `requires`, and `version_diffs_found`. Long summaries are clipped to a
  `summary_chars` budget since this text is re-sent every agent step.
- Fixed a latent bug: `version_diffs_found` is a list in 5 of the 18
  `*_lib.json` files and a bare sentence in the other 13; iterating the string
  form yielded one *character* per step, so those docs contributed nothing to
  matching. `_as_list` normalizes it, and `import_repair_note` is now
  tokenized for matching — the SmtMap import-repair note now surfaces on an
  SmtMap failure.
- Uncataloged releases in range are reported through `notes` so an empty result
  is not mistaken for "nothing changed".

#### `compute_exposure_score.py`

Reads either format; `changelog_entry_names` prefers resolved buckets over the
untyped `identifiers`, so version-bracket detection no longer fires on English
words present in every `.ec` file (183 → 140 matching entries on the ElGamal
corpus).

#### Tests

`integration/tests/test_changelog_index.py` — 24 tests covering derivation,
both loader paths, the ranking fixes, and prompt rendering. The 20 pre-existing
`test_repair_hints.py` tests pass unmodified against the untouched
legacy-format fixture.

## 2026-07-26

### Proactive goal-shape hints and broader program-logic few-shots

Failures on hard Hoare/equiv repairs often burned the step budget rediscovering
shape-level tactics (`unroll`/`rcondf`, `proc*`, `call (_: true)`, `inline`,
`seq`, ambient `progress` after `skip`). Guidance is now shown *with the
current goal*, not only after an error, and stays pattern-level (no
corpus-specific lemma names).

#### Prompt (`prompt.py`)

- New **Program-logic tactic menu**: match leading statements; covers `proc` vs
  `proc*`, abstract `call (_: true)`, concrete `inline`, bounded `unroll` +
  `rcondt`/`rcondf`, asymmetric `seq`, empty-program `skip` → ambient, and
  nonlinear SMT.
- New **Finding algebraic identities** (substring/exact + optional `theory:`).
- Stronger ambient-vs-HL transition note after `skip.`.
- **Active goal-shape hints** section inserted immediately under **Current
  goal**, selected by classifying the displayed state (implication-wrapped HL,
  program-logic with while/call/proc header/asymmetric equiv, ambient,
  nonlinear residuals).

#### Few-shots (`examples/tactics_fewshot.md`)

- Added shape-based examples for `proc*`, `call (_: true)`, `inline`, bounded
  `unroll`/`rcondf`, asymmetric `seq`, and post-`skip` ambient residuals.
- Explicit anti-overfit note: match goal shape, do not memorize tutorial lemmas.

#### Error enrichment (`loop.py`)

- Shared goal classifiers imported from `prompt.py` (single source of truth).
- Extra hints for nonempty instruction lists; SMT/InvalidGoalShape text mentions
  `progress`/`simplify` and asymmetric `equiv`/`seq`.

#### Tests

- Coverage for proactive hints, few-shot pattern presence without Joy lemma
  names, and the nonempty-instruction-list error hint.

## 2026-07-25

### Multi-step undo

Agents can undo several trailing tactics in one action via optional JSON
`count` (string or int; omit / `""` / `"1"` = one step). Never removes the
lemma signature or `proof.` line; undoes as many as remain if fewer than
requested. Logged as `undo_count` (requested) and `undone` (actual).

### Qualified lemma keys, theory filter, adaptive thinking, JSON mode off

Harness bug: `parse_premises` keyed Ax.all dumps by bare basename, so
`RField.exprM` and `Ring.IntID.exprM` collided and informal-repair trial
`exp_product` could never see the gold lemma.

#### Premise identity (`premises.py`)

- Catalog keys are EasyCrypt-style qualified paths (`EcPath.tostring`):
  `Theory.basename` (e.g. `RField.exprM`, `Ring.IntID.exprM`).
- Added `LemmaRef` (`theory`, `name`, `.key`) documenting the identity model.
- Same basename in different theories no longer overwrite each other.

#### Theory filter on search (`lemma_search.py`, prompt, lookup)

- Optional `theory:Path` token in any `search_lemmas` query (semantic /
  substring / prefix / exact) scopes the catalog before ranking.
- Lookup accepts qualified paths, bare basenames (lists ambiguities), and the
  same `theory:` filter syntax.
- Prompt/tool docs show qualified apply names and theory-filter examples.

#### Adaptive DeepSeek thinking

- `--thinking adaptive` enables thinking only when any of the last
  `--thinking-failure-window` steps (default 5) is
  `failed` / `rejected` / `search_limited` / `format_error`.
- Per-call resolution via `resolve_thinking_for_step` → `llm.decide(..., thinking=...)`.

#### JSON `response_format` off by default

- DeepSeek `json_object` does not enforce the action schema; mode is opt-in
  via `--llm-json-mode` only (LM Studio `json_schema` remains opt-in too).

### Multi-mode lemma search, search budget, subgoal diagnostics, proof-shape hints

Follow-ups from DeepSeek-v4-pro informal-repair failures (`run-20260724T215126Z`): weak semantic retrieval, opaque compound-tactic errors, missing `simplify`/`move =>` shape guidance.

#### Lemma search (`lemma_search.py`, prompt, loop)

- Search modes via JSON `name` on `search_lemmas`: `semantic` (default), `substring`, `prefix`, `exact`.
- Substring prefers name matches over signature matches; exact is case-insensitive.
- Prompt documents modes and advises falling back from semantic to lexical search for short identifiers.
- **Continuous retrieval budget** (`AgentConfig.max_continuous_searches=5`): lookup/search share one streak; warn on the 4th; reject from the 6th until a tactic or undo resets the streak.

#### Subgoal feedback (loop)

- On failed `;`-compound tactics, diagnostically replay **each** segment in order, attach `[diagnostic subgoal] ...` with the open goal after every successful prefix, stop at the first failing segment (with its error), then roll back.
- Prompt/few-shot continue to prefer atomic `while` / stepwise discharge over one-shot compounds.

#### Proof-shape prompts (no lemma-specific examples)

- New **Goal shape before program-logic tactics**: introduce `H => hoare[...]` / `forall ..., hoare[...]` with `move =>` before `proc`/`while`/`wp`.
- New **When to simplify / progress**: when residual goals are definitionally heavy after HL steps; avoid spamming on already-simple ambient goals.
- Few-shot notes `simplify.`/`progress.` on busy residuals without citing a concrete Exp.exp example.

#### Goal-shape error hints

- Fixed “expecting hoare/…” pattern to match EasyCrypt’s `hoare[F]` messages.
- If that error occurs on an implication/forall-wrapped HL goal → hint to `move => ...` first.
- Otherwise keep the ambient-logic misuse hint.

#### Tests

- Unit and integration coverage for modes, goal-shape hints, search budget warn/block, and prompt sections.

## 2026-07-24

### Anti-spam harness and prompt (tactic repeat memory)

Frontier-model runs (`run-20260724T215126Z`) showed DeepSeek-v4-pro repeatedly resubmitting the same failing tactic (often 17–19×) despite soft “do not repeat” prompt text. The harness now enforces failure memory and the prompt/few-shot guide the model away from one-shot loops.

#### Harness (`integration/agent/`)

- **Tactic normalization** (`error_history.normalize_tactic`): compare tactics after collapsing whitespace, trailing `.`, and `&&`/`||` ↔ EasyCrypt `/\`/`\/`.
- **Hard-reject duplicates**: before calling EasyCrypt, reject any tactic that already failed at the current goal under normalization; record outcome `rejected` and skip the EC round-trip.
- **Early identical-spam abort**: new `AgentConfig.identical_fail_limit` (default `3`) exits `STUCK` when the same normalized tactic fails that many times at one goal.
- **Weighted stuck counter**: new `AgentConfig.repeat_stuck_weight` (default `2`) so hard-rejected repeats advance `stuck_limit` faster than novel failures.
- **Specialized error hints**: one-shot `while (...); auto; smt()` (and similar) failures get stepwise while-guidance instead of the generic SMT lemma hint.

#### Prompt and few-shot

- Deduplicated **Previously failed at this goal** list with `Nx` counts (avoids prompt bloat that reinforced the bad tactic).
- Stronger ban text: harness will **REJECT** normalized duplicates; explicit **Banned tactics at this goal** list.
- New **Anti-loop rule** section: after a failure, change strategy class (split compound, change invariant/lemma, different head tactic, or lookup/undo).
- While few-shot rewritten to prefer stepwise `while (inv).` then per-subgoal `wp`/`skip`/`smt`; one-shot `; auto; smt()` is optional and must be abandoned after a single failure (`examples/tactics_fewshot.md`).

#### Tests

- Coverage for normalization, hard-reject of `&&`/`/\` variants, deduped prompt formatting, while-oneshot hint override, and updated stuck / failure-accumulation expectations (`integration/tests/test_agent.py`).

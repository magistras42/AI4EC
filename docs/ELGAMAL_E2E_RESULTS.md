# End-to-End Results — `elgamal-changelog-repair`

**Date:** 2026-08-03 · **Branch:** `shannon-llm-integration` · **Corpus:**
`data/derens99-ElGamal-proof/hashedelgamal.ec` (2020-era Hashed ElGamal,
genuinely broken against modern EasyCrypt)

Companion document: [`IMPLEMENTATION_PROGRESS.md`](IMPLEMENTATION_PROGRESS.md)
— what was built and why.

---

## 0. Read this first: `successes` is not a repair rate

Three runs are reported here, and they answer different questions:

| Run | Solver | What it establishes |
|---|---|---|
| **A — stubbed** | Local stub returning a deliberately-failing tactic | That the *harness* works: dispatch, replay, retrieval, prompt assembly, metrics |
| **B — DeepSeek** (`deepseek-v4-flash`, thinking off) | Real paid API, 23 calls, $0.0305 | That a *model* was given the knowledge base and what it did with it |
| **C — DeepSeek, post-fix** (thinking adaptive, 32k cap) | Real paid API, 162 calls, $1.0074, all 15 lemmas | Whether the 2026-08-04 fixes changed anything — see §11 |

**The single most important reading correction:** run B's `summary.json` says
`successes: 3`, and **none of those three came from the model**. All three are
trials that replayed verbatim with **zero LLM calls** (`steps: 0`, `calls: 0`).
On the three genuinely broken lemmas, DeepSeek repaired **0 of 3**.

`successes` counts trials that reached `COMPLETE` by any route, and the
zero-LLM replay path is a route. That is the correct definition for the field,
but it means the headline number must never be read as a repair rate. The
per-trial breakdown in §5 is the number that answers "did the model fix
anything".

**The same trap, larger, in run C:** `successes: 12` of 15 — of which **11 are
zero-LLM replays and exactly 1 is a model repair**. The correct summary of
run C is *"1 of 4 broken lemmas repaired"*, not *"12 of 15"*. See §11.1.

Across every run in this document, the model has repaired **1** genuinely
broken lemma, and it was the smallest one (2 tactics, 2 calls).

---

## 1. Headline result (run B, DeepSeek)

```
Spec: elgamal-changelog-repair
Mode: replay_bootstrap          ← was "mutation" before the W1 fix
Trials: 6 run, 0 skipped
Successes: 3, stuck: 3, max_steps: 0      ← all 3 successes are zero-LLM replays
Errors: 0
Tokens: 255430 in, 1008 out (23 chat calls)
Estimated cost: $0.030475 USD (deepseek-v4-flash)
Budget: $1.00 cap, 3% consumed, budget_stopped: false
```

`Mode: replay_bootstrap` is itself a result. Before this work the CLI silently
dropped the mode marker, dispatched these runs down the mutation path, and
labelled them `"mutation"`. **No CLI-launched run had ever exercised
replay-bootstrap mode.** `0 skipped` matters too: this corpus is exactly the
population that used to die at `skip_reason="goal_unreachable"`.

---

## 2. W1's definition of done

The handoff specified three conditions. All three hold.

**1. `bootstrap_result.json` shows a nonzero `accepted_count`** ✅

```json
{ "accepted_count": 21, "total_count": 52,
  "failed_tactic": "seq 1 1 : (={glob Adv, choice,x1,x2} /\\ q{1} = q1{2} …",
  "fully_replayed": false }
```

**2. `summary.json` says `mode: replay_bootstrap`** ✅ (above)

**3. The prompt contains a non-empty `## Known EasyCrypt library changes`
section** ✅ — **6 of 6 prompts**. This is the property §6.2 silently broke;
it is what proves the knowledge base reached the model. Verbatim excerpt from
the captured prompt log:

```
## Known EasyCrypt library changes
Where the names in this step are declared (current EasyCrypt tree):
- `Adv` is declared in RndExcept (`require import RndExcept.`)
- `RO` is declared in PROM (`require import PROM.`)

Known EasyCrypt changelog entries in range:
- [r2022.04] (mechanism_change) move cost axioms in abstract theories (fix #175):
    If proofs involving cost annotations or complexity reasoning in theories like
    Bool, List, DBool, DInterval, or DiffieHellman break, check that the required
    cost axioms are now instantiated via the relevant abstract theory rather than
    declared inline.
    (structural change in range; https://github.com/EasyCrypt/easycrypt/commit/89df8ee…)
- [r2022.04] (mechanism_change) Refactor & generalize the section mechanism:
    If proofs using sections fail, review section variable declarations and axiom
    abstractions for compatibility with the new generalized section handling.
    (matched section; …)
```

Both halves of the retrieval stack are visible: the **symbol→theory
resolution** (6325-symbol index — `Adv` → `RndExcept`, `RO` → `PROM`) and the
**changelog entries** with their match reason and provenance URL. Note the
second entry matched on `section`, which is directly relevant — this proof
*does* use sections, and the run log shows EasyCrypt warning about
`global axiom Adv_choose_ll in section`.

---

## 3. How much of a 2020 proof still compiles in 2026

This is the measurement the replay-bootstrap mode exists to produce, and W8 is
what finally reports it.

| Trial | Lemma | Tactics replayed | Fraction | Outcome |
|---|---|---:|---:|---|
| 0 | `INDCPA_HEG_G1` | 21 / 52 | 0.40 | broke → solver |
| 1 | `grexpAll` | 5 / 5 | 1.00 | **fully replayed** |
| 2 | `G1_G2_eq` | 18 / 85 | 0.21 | broke → solver |
| 3 | `G2_G3` | 13 / 30 | 0.43 | broke → solver |
| 4 | `gen_log` | 3 / 3 | 1.00 | **fully replayed** |
| 5 | `log_gen` | 3 / 3 | 1.00 | **fully replayed** |

Aggregated by `repair_metrics.py` into `summary.json`:

```json
"replay": {
  "trials": 6,
  "fully_replayed": 3,
  "fully_replayed_rate": 0.5,
  "mean_replayed_fraction": 0.6748,
  "min_replayed_fraction": 0.2118,
  "max_replayed_fraction": 1.0,
  "total_tactics_accepted": 63,
  "total_tactics": 178
}
```

Two things worth drawing out.

**The zero-LLM win is real and it is half the trials.** Three lemmas replayed
verbatim against a 2026 EasyCrypt with **zero LLM calls** — trials 1, 4 and 5
finished in 4.3s, 2.6s and 2.6s. Any harness that admits everything and asks a
model to reconstruct from scratch would have paid full price for all three. It
is the cheapest possible correct answer to "is this actually broken?".

**The breakage is concentrated, not diffuse.** The three broken lemmas are the
game-hopping proofs (`INDCPA_HEG_G1`, `G1_G2_eq`, `G2_G3`), and they retain
21–43% of their original tactics. The simple algebraic lemmas are untouched.
`total_tactics_accepted: 63 / 178` — a third of the development's tactics still
apply — is a far more useful characterization of "how bad is version drift
here" than a binary compiles/doesn't.

---

## 4. Version detection corrected a real error

Every trial wrote `ec_versions.json`:

```json
{
  "source": { "version": null, "method": "undetected", "confidence": "none",
              "detail": "no version marker and no git history" },
  "target": { "version": "r2026.06", "method": "git_describe", "confidence": "high",
              "detail": "easycrypt describes as r2026.06-6-g07e77d8c" }
}
```

**The previously hardcoded target was wrong.** The spec pinned `r2026.07`; the
fork actually built in this tree is `r2026.06` plus six commits. Migration rules
and changelog entries pinned to r2026.07 were being considered against a binary
that does not contain them. `ec.exe` has no `--version` flag, which is why this
went unnoticed — the answer had to come from `git describe` on the source tree.

The `source: null` is the correct and honest answer, not a failure: this corpus
is from 2020, predating EasyCrypt's oldest tag, and `data/` carries no git
history. `None` means "consider every release", the same fail-open convention
`releases_in_range` already uses. A confident wrong guess here would have
narrowed the window to the wrong span.

---

## 5. What DeepSeek actually did — and a metric bug it exposed

### 5.1 Model performance: 0 of 3 repaired

| Trial | Lemma | Steps | Tactics accepted | Outcome |
|---|---|---:|---:|---|
| 0 | `INDCPA_HEG_G1` | 9 | **0** | STUCK (10 unproductive iterations) |
| 2 | `G1_G2_eq` | 6 | **1** (`progress.`) | STUCK (same tactic failed 3x) |
| 3 | `G2_G3` | 8 | **0** | STUCK (10 unproductive iterations) |

Across 23 paid calls the model landed **one** tactic, and it was `progress.` —
a generic structural tactic, not a repair. `--max-steps 25` was never reached;
the anti-loop guardrails fired first, which is them working as designed.

What it tried (trial 0, in order): `skip.` → `wp.` →
`rnd{1}; rnd{2}; wp; skip; smt().` → `seq 1 1 : (={glob Adv, x1,x2} /\ ...)` →
*(duplicate, hard-rejected)* → same `seq` again → `rnd{1}; rnd{2}.` →
`call (_: true).` → *(duplicate, hard-rejected)*.

That is a model guessing at the shape of a pRHL game-hop proof, not one acting
on evidence. The duplicate-rejection guardrail hard-rejected 6 tactics across
the 3 trials without spending an EasyCrypt call — the exact behaviour it was
added for.

### 5.2 The hop worked; the targeting did not

All 3 broken trials hopped to **r2023.09**, and the live per-failure refresh
(W3) fired **7 / 3 / 6** times — `changelog_hint_refresh` events in the run
logs confirm the hop threading works in production, not just in tests.

But the retrieved evidence is aimed at the wrong failure class. The hint block
was substantively about **imports**: the SmtMap→FMap split, PROM's dependency
on FMap, FMap's current API. The actual failures are **program-logic** failures
— `seq`, `rnd`, `wp`, `skip` on equiv goals. No import fact can fix those.

The Tier-A `mechanism_change` entries that did surface (glob unsoundness, the
new `proc op` command, delta-unfolding warnings, section restrictions) are
structural but unrelated to the failing tactic. Note also that r2023.09 is one
of the four releases with **empty upstream release notes**, so its 169 entries
are all git-log-derived commit messages (W5b's recovery) rather than curated
notes — lower signal per entry than the r2025.02-style PR entries.

### 5.3 A bug in my own W8 metric, found by this run

The first `summary.json` reported `hint_uptake.rate: 0.0`. That number was
**not trustworthy**: `_accepted_tactics` read a top-level `"iterations"` key,
but `AgentRunLog` writes `{"source", "work_copy", "events": [...]}` with
per-step records tagged `{"event": "iteration"}`. The lookup always returned an
empty list, so uptake was pinned at 0.0 by construction — reporting a finding
where it had measured nothing.

Fixed, plus a guard for the deeper methodological problem: **a 0 rate with 0
accepted tactics means "not measurable", not "hints were ignored"**. The
aggregate now carries the denominator. Re-scored against this run:

```json
"hint_uptake": {
  "trials_scored": 3,
  "trials_using_a_hinted_identifier": 0,
  "rate": 0.0,
  "trials_with_accepted_tactics": 1,
  "rate_among_scorable": 0.0
}
```

Only **1 of 3** trials could evidence uptake at all, and its single accepted
tactic (`progress.`) contains no identifier. So the honest statement is: **this
run does not measure whether the hints help.** It measures that DeepSeek at
`thinking: disabled` could not make progress on these goals.

---

## 6. The A/B that did not work, and the four bugs it found

An A/B was run on whether showing the solver the **rest of the original proof**
(the tactics after the replay break, previously discarded -- 46 across three
lemmas) improves repair. Arm A withheld them, arm B showed them as a labelled
stale reference; one variable, everything else pinned.

### 6.1 Verdict: inconclusive, and it could not have been otherwise

The run-to-run variance exceeds the between-arm difference. Arm B on `G2_G3`,
**same arm, same lemma, same config**:

| Run | Iterations | Accepted | Outcome |
|---|---:|---:|---|
| First | 42 | **11** | MAX_STEPS |
| Second | 17 | **1** | STUCK |

An order of magnitude apart. With n=1 per cell, no arrangement of these three
lemmas separates the reference's effect from noise. This was predictable in
advance -- `thinking=adaptive` plus a stochastic model over a 40-step search is
high-variance by construction -- and should have been anticipated before
spending on it.

The one repeatable result: **arm B solved `INDCPA_Security` in both independent
runs; arm A never did.** The 2020 proof passed two liveness axioms modern
EasyCrypt no longer takes, and the model dropped them:

```
apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).   <- original, fails today
apply (INDCPA_Sec Adv &m).                              <- model's repair, accepted
```

`validate_file` returns 0. It is a genuine version-drift repair, and it is also
the easiest possible case: a one-line proof whose reference contained the answer
modulo two arguments.

### 6.2 What the A/B was actually good for

It was a poor experiment and an excellent bug-finder. Four defects, all fixed
with regression tests:

| Bug | Impact |
|---|---|
| `response.choices[0]` on a `choices=None` body raised `TypeError`, which `loop.py`'s catch-all turned into a terminal abort | destroyed **4 of 7** completed trials, discarding 70 and 31 iterations |
| Thinking consumed the whole output budget (`finish_reason='length'`, empty reply) | **79 of 85** recoverable errors; 36-70% of every trial |
| Provider failures advanced the stuck counter | one trial exited "STUCK after 20 unproductive iterations" when 14 were empty replies -- it got 6 real attempts |
| `format_error` is in `THINKING_FAILURE_OUTCOMES`, so budget exhaustion switched thinking **on** for the next step | a feedback loop feeding the condition that caused it |

The harness also told the model to *"escape every backslash in tactic strings"*
on 79 replies that contained nothing at all to format.

After the fixes, a fresh A/B ran with **zero** wasted iterations in either arm.
That is the well-powered result from this work: n=85 before, n=0 after.

---

## 7. Running the live comparison

The harness is provider-agnostic; the same command differs only by
`--provider`. **Both paid providers prompt for confirmation, and per
[`AGENTS.md`](../AGENTS.md) an agent must never answer that prompt — these are
for a human to run.**

**Prerequisite for every provider: a local embeddings endpoint.** Embeddings
never go to the chat provider — Anthropic has no embeddings API at all — so
LM Studio (or another OpenAI-compatible `/v1/embeddings` server) must be
running with an embedding model loaded. One ElGamal lemma has **2,583 premises
in scope, ~57k tokens to embed per trial**. The CLI now probes this *before*
the spend prompt, so an unreachable embedder aborts with `No paid call was
made` rather than after you have authorized money.

```bash
# Claude — default claude-opus-5, adaptive thinking, effort high.
# .env is not auto-loaded; source it (nothing in the repo depends on dotenv).
set -a; . ./.env; set +a
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --provider anthropic --trials 6 --stuck-limit 10 --max-steps 25 --seed 7 \
  --reasoning-effort high

# DeepSeek. Leave thinking adaptive and give it budget -- see the note below.
export DEEPSEEK_API_KEY=...
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --provider deepseek --trials 10 --stuck-limit 20 --max-steps 200 \
  --llm-model deepseek-v4-flash --thinking adaptive --llm-max-tokens 32768 \
  --embed-model <local-embed-model>

# Local model (Gemma et al. via LM Studio) — free, no confirmation
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --trials 10 --stuck-limit 20 --max-steps 200 \
  --llm-model <model> --embed-model <embed-model>
```

Embeddings always use LM Studio regardless of provider, so a local embedding
server is needed in all three cases.

### Do not turn thinking off to avoid truncation

An earlier version of this file recommended `--thinking disabled` for DeepSeek.
That was wrong, and the runs already on disk say so. Three proofs ran under
both settings with the same model:

| Proof | `disabled` | `adaptive` |
|---|---|---|
| INDCPA_HEG_G1 | 9 calls, **0** accepted, STUCK | 70 calls, **18** accepted |
| G2_G3 | 8 calls, **0** accepted, STUCK | 42 calls, **10** accepted |
| G1_G2_eq | 6 calls, **1** accepted, STUCK | 4 calls, **2** accepted |

Adaptive accepted **43%** of productive calls against **4%** disabled (32/74 vs
1/23); Anthropic at `high` effort, also a thinking mode, scored 52%. Every
thinking-disabled trial went STUCK having accepted almost nothing.

The reasoning error behind the bad advice is worth naming, because it is easy
to repeat: truncation was the metric under investigation, so the recommendation
optimised for *that* rather than for accepted tactics, and proposed removing
the capability generating the truncations. Truncation is a **budget** problem.
Mean output was ~13.2k tokens against a 16,384 cap -- 82% of the ceiling -- so
calls hit it routinely. Raise `--llm-max-tokens` to 32768 and leave thinking on;
the truncation retry then acts as a backstop rather than a routine cost.

Sample sizes are small (74 productive calls vs 23) and run-to-run variance
under identical config has reached 11 vs 1 accepted (§9.1), so treat 43% vs 4%
as "clearly better", not as a precise effect size. The direction is not in
doubt: three matched proofs, all agreeing.

Comparison is then a direct read of `summary.json`: `successes`/`stuck`,
`repair_metrics.hint_uptake.rate`, `token_usage_per_trial`, and
`estimated_cost` (populated for both paid providers — Anthropic pricing
includes the cache-read/cache-write split).

**To establish that the knowledge base helps**, run the same spec twice with
the same seed and compare `hint_uptake` and success rate. That is the
counterfactual W8's metrics were built to support and it is the obvious next
experiment.

---

## 8. Verification summary

| Claim | Evidence |
|---|---|
| Replay-bootstrap mode dispatches from the CLI | `Mode: replay_bootstrap` in `summary.json` |
| The knowledge base reaches the prompt | Populated `## Known EasyCrypt library changes` in every solver prompt |
| Symbol→theory index is consulted | `Adv` → `RndExcept`, `RO` → `PROM` in the hint block |
| Replay preserves the valid prefix | 63/178 tactics accepted across 6 lemmas |
| Zero-LLM win path works | 3 trials COMPLETE with 0 chat calls, 3–4s each |
| W3 live hop fires per failure | 7 / 3 / 6 `changelog_hint_refresh` events in the run logs |
| Version detection corrected the spec | `r2026.06` via `git describe`, vs hardcoded `r2026.07` |
| Detection stays honest when it cannot know | `source: null`, `confidence: "none"` |
| Metrics aggregate into summary.json | full `repair_metrics` block |
| Spend cap enforced and reported | `$1.00` cap, 3% used, `budget_stopped: false` |
| Cost accounting is accurate | $0.0305 across 23 calls, cache split reported |
| No regressions | **412 tests pass, suite fully green** (the 2 long-standing `test_goal_state.py` failures were stale test doubles missing `**kwargs`, now fixed) |

**Established about model performance:** across every run, DeepSeek repaired
exactly **one** lemma (`INDCPA_Security`, reproduced twice, and only with the
remaining-original reference). On the harder game-hopping proofs it accepted
individual tactics but never closed a proof.

**Established about the harness:** wasted iterations went from **36-70%** to
**0%** after four bug fixes. That is the largest measured improvement in this
work, and none of it came from the knowledge base.

**Not established:** whether the knowledge base helps, and whether showing the
remaining original proof helps (§6.1). Both need several seeds per cell; single
runs are swamped by run-to-run variance larger than either effect.

---

## 9. Where the remaining failures actually are

Every tactic failure across all runs (n=134), classified with `ec_errors.py`:

| Tactic the model reached for | Failures | What EasyCrypt said |
|---|---:|---|
| `rnd` | **31** | 20x `invalid arguments`, 10x `invalid last instruction` |
| `seq` | **15** | 13x ``invalid `position' parameter`` |
| `skip` | **14** | 2x `left instruction list is not empty` |
| `smt()` | 14 | `cannot prove goal (strict)` |
| others | 60 | |

The top three -- **60 of 134, 45%** -- share one root cause, and it is **not**
version drift, missing lemmas, or a weak model. Each needs the model to know the
*shape of the remaining program*:

* `seq N M : (inv)` needs the instruction **counts** on each side.
* `rnd` needs the **last instruction on both sides to be a sampling**.
* `skip` needs **both instruction lists to be empty**.

None of that is reliably legible in the prompt today, so the model guesses.

### 9.1 The prompt is actively misdirecting the model

`format_active_goal_shape_hints` decides there is "little or no code left" by
looking for a printed instruction list. But on a synchronised `equiv` EasyCrypt
prints no list -- it prints:

```
&1 (left ) : {choice, guess : bool, ...} [programs are in sync]
```

`[programs are in sync]` means the two sides have **identical remaining code**,
i.e. code very much remains. The heuristic reads its absence as emptiness and
emits:

> *"Detected: little or no code left under `pre`/`post`. Apply `skip.`"*

Measured across all runs:

| | |
|---|---:|
| Steps where the prompt advised `skip.` | **121** |
| ...where the chosen tactic then failed | **53** |
| ...where that failure was `skip` reporting `left instruction list is not empty` | **2** |
| ...goals carrying `[programs are in sync]` | **48** |

In those 2 steps the goal directly contradicted the advice. That is a small
number, and it is the only *direct* evidence in the data that the advice caused
a failure -- the remaining 51 failures were other tactics (`rnd`, `seq`) whose
link to the `skip` advice is circumstantial.

The defect is still worth fixing on its own terms: it is deterministic rather
than stochastic, needs no model to reproduce, and the parser demonstrably
reported emptiness for programs that visibly contain instructions. But the
evidence supports "the prompt asserted something false", not "this is what was
costing us proofs".

### 9.2 Recommended next work, in order

1. **Fix the `[programs are in sync]` misdetection.** Pure logic bug, testable
   against captured goal text with no API spend. Removes 121 instances of
   confidently wrong advice.
2. **Give program-logic goals a structured view**: numbered instruction lists
   per side, and an explicit "last instruction is a sampling / assignment /
   call" fact. Directly addresses the 45% of failures above. `prompt.py` already
   has `_split_equiv_columns` and `_program_statement_block` to build on.
3. **Only then re-open the A/B**, if at all -- and with >= 5 seeds per cell,
   roughly 10x the spend, now that the variance is known.

The ordering matters: (1) and (2) are deterministic harness defects fixable and
verifiable offline, while (3) is a stochastic experiment that has so far cost
more than it returned.

## 10. Historical: what to run next (superseded by 9.2)

Three things this run makes worth doing, in order:

1. **Turn thinking on.** `--thinking adaptive` (or `enabled`) with
   `--reasoning-effort high`. This run was `disabled` — the repo's DeepSeek
   default, chosen for cheap tactic selection — and these are hard pRHL
   game-hop goals. At $0.03 per 6-trial run there is a lot of headroom under a
   $1 cap.
2. **Compare against Claude.** The provider work exists for exactly this. Same
   `--seed 7`, so the same 3 broken lemmas:
   ```bash
   set -a; . ./.env; set +a
   .venv/bin/python -m integration.experiment run \
     --spec elgamal-changelog-repair --provider anthropic \
     --trials 6 --stuck-limit 10 --max-steps 25 --seed 7 \
     --reasoning-effort high --max-spend-usd 5.00
   ```
3. **Then, and only then, the hints-on/hints-off counterfactual.** It is only
   meaningful once a configuration lands tactics — otherwise both arms score 0
   for the same uninformative reason.

A fourth, longer-term: **the retrieval targets the wrong failure class for
tactic-level breakage.** Tier-B matching keys on identifiers in the failing
tactic, but `seq 1 1 : (={glob Adv, q1, q2, ...})` names local variables and
modules, not library symbols. The `ec_errors.py` classifier added here can
already tell a `tactic_error` from an `unknown_theory`; wiring that into
retrieval so program-logic failures pull *tactic* changelog entries rather than
import notes is the obvious next research step.

---

## 11. Run C — full corpus, after the 2026-08-04 fixes

**Date:** 2026-08-04 · `deepseek-v4-flash`, thinking `adaptive`,
`--llm-max-tokens 32768`, `COST_LIMIT_USD 1.00`, all 15 lemmas, ~7h15m.
Run directory: `integration/output/experiments/run-20260804T164111Z/`.

First run with the truncation fix, the goal-shape parser fixes, the warning
filter, and the raised output budget all in place.

### 11.1 Headline

```
Trials: 15 run, 0 skipped
Successes: 12    stuck: 0    max_steps: 2    budget_stopped: True
Spend: $1.0074 over 162 calls
```

**Do not read `successes: 12` as a repair rate** (§0 applies unchanged). The
breakdown by route:

| Route | Trials | |
|---|---:|---|
| Replayed verbatim, **zero LLM calls** | **11** | the harness working, not the model |
| **Repaired by the model** | **1** | `INDCPA_Security`, 2 calls, $0.0058 |
| Unsolved | 3 | `G2_G3`, `INDCPA_HEG_G1` (MAX_STEPS), `G1_G2_eq` (budget) |

| # | Lemma | Reason | Steps | Calls | Cost |
|---:|---|---|---:|---:|---:|
| 0 | enc_stateless | COMPLETE | 0 | 0 | — |
| 1 | INDCPA_Sec | COMPLETE | 0 | 0 | — |
| 2 | INDCPA_Security | **COMPLETE (model)** | 2 | 2 | $0.0058 |
| 3–10 | log_gen … G2_bad_ub | COMPLETE | 0 | 0 | — |
| 11 | G2_G3 | MAX_STEPS | 42 | 53 | $0.3975 |
| 12 | INDCPA_HEG_G1 | MAX_STEPS | 77 | 84 | $0.4479 |
| 13 | correctness | COMPLETE | 0 | 0 | — |
| 14 | G1_G2_eq | BUDGET_EXHAUSTED | 18 | 23 | $0.1561 |

### 11.2 The fixes worked at the mechanism level

**Zero `format_error` in 162 calls**, against 51-of-52 in the pre-fix A/B run.
Same lemma, same model, before and after:

| `INDCPA_HEG_G1` | pre-fix | run C |
|---|---:|---:|
| accepted | 18 | **56** |
| failed | 18 | 13 |
| `format_error` | **31** | **0** |
| exit | LLM_ERROR | MAX_STEPS |

Truncation is gone and accepted tactics roughly tripled. The proof still did
not close: it ran out of *steps*, not budget.

### 11.3 The fixes did not translate into repaired proofs

One repair, and it was a 2-call fix on the easiest broken lemma. All three
genuinely hard proofs failed. This is the caveat from §9.2 landing exactly as
written -- fixing the guidance moved the model to the next obstacle rather than
to a solution. Mechanism-level wins (no truncation, 3x accepted tactics, no
fabricated hints) are real and measurable, and they are **not** the same thing
as repair capability.

Anyone quoting this work should quote "1 of 4 broken lemmas repaired", not
"12 of 15 successes".

### 11.4 RETRACTED: the "82% reuse" result

An earlier revision of this section reported that **46 of 56 (82%)** accepted
tactics on `INDCPA_HEG_G1` were "verbatim from the original proof script", and
presented it as the strongest result in the run. **That figure is an artifact.
Do not cite it.**

The measurement was set membership -- "does this accepted tactic appear
somewhere in the original script?" The original remaining script for that trial
is 33 lines but only **16 distinct**, dominated by generic one-word tactics
(`auto` 6x, `sp` 4x, `if; progress` 4x, `proc` 2x). Of the 46 matches, the
number that were **distinctive** -- unique in the original, non-generic, longer
than 8 characters -- is **zero**. Every match was `auto` matching `auto`, which
any EasyCrypt proof would score against any other.

Measured three ways across the trials that produced accepted tactics:

| Lemma | accepted | set-match ("82%") | distinctive | LCS | original |
|---|---:|---:|---:|---:|---:|
| `G2_G3` | 9 | 5 | **0** | 3 | 17 |
| `INDCPA_HEG_G1` | 56 | 46 | **0** | 14 | 33 |
| `G1_G2_eq` | 7 | 6 | **0** | 5 | 83 |

Longest common subsequence -- how much of the original survives *in order* --
is the fairest measure and gives 14/33, 3/17, 5/83. Of the 46 matches in
trial_012, only 4 were the next-expected original tactic; 42 were out of order.
The model was playing common tactics in a plausible order, not transcribing the
reference text.

The consequence for
[`plans/INCREMENTAL_REPAIR_DESIGN.md`](plans/INCREMENTAL_REPAIR_DESIGN.md) is
recorded in its §2.1 and §9: the economic justification is withdrawn, and the
invasive part of that design should not be built on it.

### 11.4b What the run does support

The one genuine model repair, trial_002 (`INDCPA_Security`), is a clean
existence proof for cheap isolated repair: bootstrap broke at 1 of 2 tactics,
the model rewrote

```
apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).   ->   apply (INDCPA_Sec Adv &m).
```

dropping two now-implicit section axioms, and the proof closed. **2 calls,
$0.0058.**

Against that, the three hard lemmas broke early and never recovered
(`G1_G2_eq` died at 18/85 tactics). Run C's four paid trials therefore split
**1 isolated-break / 3 wholesale-divergence**, which is the ratio that should
govern how much effort the incremental design is worth.

### 11.5 Two operational findings

**The spend cap is soft.** Final spend was $1.0074 against a $1.00 limit. The
budget is checked between calls, so a call already in flight can carry past the
limit. Harmless at this scale; worth knowing before setting a limit that
matters.

**Proof length does not predict cost.** `correctness` is 58 tactics and
replayed free in about a minute; `INDCPA_Security` is 2 tactics and needed the
model. What matters is whether the 2020 tactics still compile, not how many
there are. A prediction made here from line count alone was wrong.

### 11.6 Cost shape

99.8% of output tokens were reasoning; the visible answer averaged ~29 tokens
per call. Mean output was ~19.6k tokens against the 32768 cap -- comfortably
inside it, which is why truncation vanished, but it costs ~3.4 minutes of wall
clock per call. The two MAX_STEPS trials consumed ~5 of the run's 7 hours.

For the next run, `REASONING_EFFORT = "high"` would cap thinking rather than
letting it run to ~19.6k tokens. Note this is *not* a return to disabling
thinking, which §7 shows is measurably wrong.

---

## 12. Run D — full corpus, 2026-08-05, after the §11 (roadmap) work

First run against the tree with error-kind rule selection, graded import-repair
outcomes, minimisation, the 116-rule manifest and the 14 authored notes all in
place. DeepSeek `deepseek-v4-flash`, adaptive thinking, 32768-token cap,
15 trials, seed 7.

### 12.1 Headline

| | |
|---|---|
| Trials | **15** — 12 COMPLETE, 3 MAX_STEPS, 0 stuck, 0 errors |
| Spend | **$1.4887** (cap $5.00, not reached) |
| Zero-LLM verbatim replays | **11 of 15** |
| Repaired by the model | **1** (`INDCPA_Security`, 2 calls) |
| Tactics accepted overall | 185 / 301 |

The two most expensive trials, `G2_G3` ($0.28) and `INDCPA_HEG_G1` ($0.60),
account for ~60% of the bill and neither finished.

### 12.2 Import repair: the W4.5 claim reproduced end to end

```
attempted 12,  resolved 12 (100%)
  loads          7    compiles clean
  reached_proof  5    load errors gone; a TACTIC is now at fault
remaining_error_kinds:  tactic_error 3,  proof_incomplete 2
```

**Zero pre-proof errors remain**, reproducing the offline measurement exactly,
including `mean_first_error_line_advance` of 547.8. Every trial moved
`parse_error:108 -> tactic_error / proof_incomplete`. The manifest has no gap
left on this corpus; everything still failing is the solver's problem.

Minimisation fired on **10 of 12 trials** (4 rules kept, 3 dropped). Without
it each of those files would have reached the model carrying three unrelated
theory requires presented as "this is what was wrong with your proof".

Adaptive re-targeting is visible in the selection counts: 22 rules chosen
against `parse_error`, then **12 against `unknown_symbol`** — the
classification advancing as the file changed, which was not possible before.

Note that `resolved_rate` is 1.0, the same headline the old flattering metric
gave. It is now true for a defensible reason rather than by coincidence, but a
reader who looks only at that number sees no difference; the distribution and
`remaining_error_kinds` are where the change is legible.

### 12.3 Tactics per lemma in the repaired files

`original` is the 2020 script, `replayed` how much of it still compiles
verbatim, `repaired` what the file holds after the run.

| # | lemma | original | replayed | repaired | by model | outcome |
|---|---|---:|---:|---:|---:|---|
| 0 | enc_stateless | 1 | 1 | 1 | +0 | COMPLETE |
| 1 | INDCPA_Sec | 1 | 1 | 1 | +0 | COMPLETE |
| 2 | INDCPA_Security | 2 | 1 | 2 | **+1** | COMPLETE |
| 3 | log_gen | 3 | 3 | 3 | +0 | COMPLETE |
| 4 | gen_log | 3 | 3 | 3 | +0 | COMPLETE |
| 5 | grexpAll | 5 | 5 | 5 | +0 | COMPLETE |
| 6 | RO_track_f_ll | 8 | 8 | 8 | +0 | COMPLETE |
| 7 | G1_G2 | 5 | 5 | 5 | +0 | COMPLETE |
| 8 | G3_true | 16 | 16 | 16 | +0 | COMPLETE |
| 9 | RO_LCDHAdv | 17 | 17 | 17 | +0 | COMPLETE |
| 10 | G2_bad_ub | 15 | 15 | 15 | +0 | COMPLETE |
| 11 | G2_G3 | 30 | 13 | 13 | +0 | MAX_STEPS |
| 12 | INDCPA_HEG_G1 | 52 | 21 | 21 | +0 | MAX_STEPS |
| 13 | correctness | 58 | 58 | 58 | +0 | COMPLETE |
| 14 | G1_G2_eq | 85 | 18 | **69** | **+51** | MAX_STEPS |
| | **TOTAL** | **301** | | **237** | | |

Two things this table says that the outcome column does not.

**`correctness` (58 tactics) replayed verbatim, for free.** Proof length does
not predict version drift — the same point §11.4 made when the "82% reuse"
figure was retracted.

**`G1_G2_eq` is the real repair story and it is scored as a failure.** The
model added 51 tactics on top of an 18-tactic replayed prefix, reaching 69 of
the original 85, at 107 accepted against 24 failed — a far healthier ratio than
either other MAX_STEPS trial. It ran out of steps at 139 of a 145-step budget.
This is the one trial where raising `--max-steps` is worth trying, and the only
one where the harness, not the model, looks like the binding constraint.

**Counting note, and a correction.** Counts come from the proof body of
`trials/*/agent_work.agent.ec`. Two passes were wrong before this one:

1. Dropping any line starting with `(*` undercounted two lemmas, because
   `(* prove 1/2 *) trivial.` is a tactic.
2. Counting physical **lines** is not counting tactics. A
   `seq 5 5 : (invariant …)` wraps across lines and `wp; skip; smt().` is one
   line holding three steps.
3. Closing a statement at any depth-0 `.` splits **qualified names**.
   `by rewrite RealOrder.lerr_eq (G2_bad_ub &m).` is one tactic, and the dot in
   `RealOrder.lerr_eq` is at depth 0 with identifier characters on both sides.
   That over-counted `G1_G2` as 6 and produced a phantom "+1 by the model" on a
   lemma the agent never ran against.

A terminator is now a depth-0 `.` **followed by whitespace or end of input**,
with comments stripped first.

**The calibration that catches this class of error**: any trial with
`fully_replayed: true` and zero agent iterations must count exactly its
replayed prefix, because nothing was added. That is ground truth rather than an
estimate, it covers 11 of 15 trials per run, and it is asserted rather than
eyeballed. Mistake 3 was caught by it; mistakes 1 and 2 were not, because
`correctness` alone happened to be insensitive to both.

### 12.3b The same table for run C, and what the pair says

Run C (`run-20260804T164111Z`) under the same spec and model, **stopped by its
$1.00 cap**.

| # | lemma | original | replayed | repaired | by model | outcome |
|---|---|---:|---:|---:|---:|---|
| 0 | enc_stateless | 1 | 1 | 1 | +0 | COMPLETE |
| 1 | INDCPA_Sec | 1 | 1 | 1 | +0 | COMPLETE |
| 2 | INDCPA_Security | 2 | 1 | 2 | **+1** | COMPLETE |
| 3 | log_gen | 3 | 3 | 3 | +0 | COMPLETE |
| 4 | gen_log | 3 | 3 | 3 | +0 | COMPLETE |
| 5 | grexpAll | 5 | 5 | 5 | +0 | COMPLETE |
| 6 | RO_track_f_ll | 8 | 8 | 8 | +0 | COMPLETE |
| 7 | G1_G2 | 5 | 5 | 5 | +0 | COMPLETE |
| 8 | G3_true | 16 | 16 | 16 | +0 | COMPLETE |
| 9 | RO_LCDHAdv | 17 | 17 | 17 | +0 | COMPLETE |
| 10 | G2_bad_ub | 15 | 15 | 15 | +0 | COMPLETE |
| 11 | G2_G3 | 30 | 13 | 19 | **+6** | MAX_STEPS |
| 12 | INDCPA_HEG_G1 | 52 | 21 | 28 | **+7** | MAX_STEPS |
| 13 | correctness | 58 | 58 | 58 | +0 | COMPLETE |
| 14 | G1_G2_eq | 85 | 18 | 11 | **−7** | BUDGET_EXHAUSTED |
| | **TOTAL** | **301** | | **192** | | |

The `−7` is not an artifact: the cap killed the trial mid-repair, immediately
after two `undone` events, so the file was left holding *less* than the
replayed prefix. That trial is unusable as a data point.

**Correction.** An earlier revision of this section claimed run C repaired two
lemmas, `INDCPA_Security` and `G1_G2`. That was wrong and the cause was the
counter, not the run — see the counting note in §12.3. `G1_G2` replays 5/5
verbatim with `fully_replayed=true` and **zero** agent iterations in both runs;
the model never touched it. Both runs partially repaired exactly one lemma,
`INDCPA_Security`.

| repaired tactics | run C | run D |
|---|---:|---:|
| total | 192 | **237** |
| spend | $1.0074 (capped out) | $1.4887 (finished) |

Every COMPLETE lemma is identical across the two runs — the eleven free
replays and `correctness` are deterministic, as they must be. All the
difference sits in the three hard lemmas:

| lemma | run C | run D | |
|---|---:|---:|---|
| G2_G3 | 19 | 13 | **worse** — MAX_STEPS both times, run D got less far |
| INDCPA_HEG_G1 | 28 | 21 | **worse**, same way |
| G1_G2_eq | 11 | 69 | run C's 11 is the cut-off artifact; against its 18-tactic baseline run D added 51 where run C never got to try |

**This does not show the §11 work helping or hurting.** Two lemmas got worse,
one got much better, one run each, on a corpus whose measured run-to-run spread
(§6) is larger than any of those gaps. It is the same reason the A/B was
dropped, and the same reason the first-instruction fix in §12.4 is reported as
addressing a *failure class* rather than as improving outcomes.

### 12.4 Two defects this run exposed

Both were found by looking at the artifacts rather than the summary, and both
are fixed.

**`hint_uptake` reported 50% on a run whose real uptake was 0%.** Only 4 trials
called the model, and in 2 of them the single "used" identifier was `Adv` — the
corpus's own adversary module, in the source long before any hint existed,
mentioned in passing by a module-restriction note, and unavoidably present in
accepted tactics. The extractor was also harvesting English prose, because
"capitalized with a lowercase in it" is the shape of an EasyCrypt theory *and*
of a word starting a sentence: `The`, `This`, `Where`, `Known`, `Drop`,
`Unprefixed`, `EasyCrypt`, plus `github.com` from provenance URLs.

Fixed by differencing out identifiers already present in `original.ec` — a name
the proof already used is not evidence a hint taught it anything — and by
dropping prose and URL hosts. The same run now scores **0.0**, which is the
truth and a better input to "does the knowledge base help" than a 50% built
from one pre-existing token.

**The prompt named the last instruction and never the first.** EasyCrypt's
tactics divide on exactly that: `rnd` and `wp` consume from the END of the
program, `if` / `rcondt` / `rcondf` from the FRONT. The shape block gave only
the tail.

Failure taxonomy over all 78 tactic failures:

| message | n | tactic | cause |
|---|---:|---|---|
| `expecting a goal of the form: hoare[S], …` | 15 | mixed | hint asserted PROGRAM-LOGIC on an ambient goal |
| `invalid last instruction` | 13 | `rnd` | hint **did** name the last instruction |
| `invalid first instruction` | 13 | `if` | hint **never named the first** |
| `left instruction list is not empty` | 10 | `skip` | |

The captured goal makes the third concrete: the model tried `if{2}` when the
**right** side's first instruction was an assignment, not a conditional. It
could never have worked and nothing in the prompt said so. Both ends are now
reported per side, with the tactic keyed to the correct end — "assignment ->
`wp`" is said only of the *last* instruction, since `wp` works backwards.

### 12.5 One thing deliberately not fixed

The 15 `expecting a goal of the form` failures come from the hint asserting
PROGRAM-LOGIC on a `[programs are in sync]` goal whose judgment was already
discharged. There is no discriminator in the printed goal:

| | accepted a program-logic tactic | rejected it |
|---|---:|---:|
| sync goals | 89 | 12 |
| …of which the statement block was empty | 36 | 4 |

Block emptiness occurs at the same rate in both, so conditioning the advice on
it would be guessing — the same mistake §9.1 already had to undo once. The hint
now names its own recovery instead ("if you get that error the judgment is
discharged, go ambient"), matching the existing `skip.` fallback.

### 12.5b The step budget, not the model, ended all three

> **Superseded by §13.** This section's recommendation — raise the multiplier
> to 2.5 — was tested in run E and did **not** help. Two trials then stopped
> on `STUCK_LIMIT` without using their enlarged budget, one of them after only
> 35% of it. Read §13.2 before acting on anything below.

Every unfinished lemma exhausted its budget exactly:

| lemma | tactic lines | budget (1.4x) | steps used | cost | $/step |
|---|---:|---:|---:|---:|---:|
| G2_G3 | 30 | 42 | **42** | $0.2843 | 0.0068 |
| INDCPA_HEG_G1 | 55 | 77 | **77** | $0.5990 | 0.0078 |
| G1_G2_eq | 104 | 145 | **145** | $0.5995 | 0.0041 |

None stopped because the model gave up; all three hit the ceiling. `G1_G2_eq`
is the clearest — 107 accepted against 24 failed, the healthiest ratio in the
run, cut off with 51 tactics already added.

The multiplier is therefore raised **1.4 -> 2.5** (budgets 75 / 137 / 260),
with the $5.00 cap left where it is. Projected from the measured $/step above,
2.5x lands at **~$2.65** against that cap:

| multiplier | projected total |
|---|---:|
| 1.4 (run D) | $1.49 |
| 2.0 | $2.13 |
| **2.5** | **$2.65** |
| 3.0 | $3.19 |

Read those as a **floor, not a ceiling**. The projection is linear in steps,
and cost per step grows as the trajectory lengthens the prompt, so the true
figure will be higher. The cap is the real protection — it is checked before
every call, so overshoot is bounded by one call.

What this does not assume: that more steps will finish these proofs. It only
removes the harness as the binding constraint, so the next run measures the
model rather than the budget. If a lemma still stops at its new ceiling with a
healthy accept ratio, the answer is more steps again; if the accept ratio
collapses first, the answer is not steps at all.

### 12.6 What run D does and does not establish

**Does.** Import repair is finished work on this corpus: 12/12 resolved, no
pre-proof errors left, and the graded outcome reproduces offline. The cheap-win
path is real and large — 11 of 15 lemmas cost nothing.

**Does not.** Whether any of the §11 work improves *repair capability*. Both
runs partially repaired the same two lemmas (`INDCPA_Security`, `G1_G2`) and
finished neither of the three hard ones; §12.3b shows two of those three going
*backwards* in run D and one going far forwards. The first-instruction fact
addresses 13 of 78 failures deterministically, but whether that changes
outcomes needs another run, and per §6 one run cannot settle it.

---

## 13. Run E — the step-budget experiment, and its answer

Run D's §12.5b raised `ADAPTIVE_MULTIPLIER` 1.4 -> 2.5 on the grounds that all
three unfinished lemmas had exhausted their budget exactly, so the harness
rather than the model looked like the binding constraint. Run E tested that.
Same spec, model, seed and corpus; only the multiplier changed. The spend cap
stayed at $5.00.

**The answer is no.** More steps did not buy repaired proofs.

### 13.1 Headline, against both prior runs

| | run C (1.4x) | run D (1.4x) | **run E (2.5x)** |
|---|---|---|---|
| complete / stuck / max-steps | 12 / 0 / 2 | 12 / 0 / 3 | **12 / 2 / 1** |
| tactics retained | 192 | 237 | **249** |
| spend | $1.0074 (capped) | $1.4887 | $1.4633 |
| wall clock | 7.5 h | 9.4 h | 9.8 h |
| import_repair resolved | 12/12 | 12/12 | **12/12** |

Twelve lemmas complete in all three runs, and `import_repair` resolves 12 of 12
in all three — that half of the system is stable and finished.

### 13.2 The failure mode moved from MAX_STEPS to STUCK

This is the result. Two trials stopped **without using their enlarged budget**:

| lemma | budget | steps used | outcome | |
|---|---:|---:|---|---|
| G2_G3 | 75 | 75 | MAX_STEPS | used it all |
| INDCPA_HEG_G1 | 137 | **104** | **STUCK** | 76% of budget |
| G1_G2_eq | 260 | **91** | **STUCK** | **35% of budget** |

`G1_G2_eq` is the lemma the whole change was aimed at — it was cut off at
139/145 in run D with 51 tactics added. Given 260 steps it stopped at 91. The
budget was never the thing stopping it; twenty consecutive unproductive
iterations (`STUCK_LIMIT`) was.

### 13.3 And the model was performing *better* while it happened

| accepted / failed | run C | run D | **run E** |
|---|---|---|---|
| G2_G3 | 9/31 = 0.29 | 19/18 = 1.06 | **43/27 = 1.59** |
| INDCPA_HEG_G1 | 56/13 = 4.31 | 34/34 = 1.00 | **62/33 = 1.88** |
| G1_G2_eq | 7/9 = 0.78 | 110/25 = 4.40 | **73/11 = 6.64** |

`G1_G2_eq` at 6.64 accepted per failure is the best ratio recorded on that
lemma in any run, and it still went stuck holding fewer tactics than run D's
attempt. A high accept ratio and an early stop are not contradictory: the model
was landing most of what it tried, ran into something it could not get past,
and burned its stuck allowance there.

So §12.5b's diagnostic — "if the accept ratio collapses first, the answer is
not steps at all" — returns an answer it did not anticipate. The ratio did not
collapse. The run stopped anyway.

### 13.4 Tactics in the fixed proofs

`original` is the 2020 script, `replayed` how much still compiles verbatim,
`FIXED` what the repaired file holds at the end.

| # | lemma | original | replayed | FIXED | by model | % of original | outcome |
|---|---|---:|---:|---:|---:|---:|---|
| 0 | enc_stateless | 1 | 1 | 1 | +0 | 100% | COMPLETE |
| 1 | INDCPA_Sec | 1 | 1 | 1 | +0 | 100% | COMPLETE |
| 2 | INDCPA_Security | 2 | 1 | 2 | **+1** | 100% | COMPLETE |
| 3 | log_gen | 3 | 3 | 3 | +0 | 100% | COMPLETE |
| 4 | gen_log | 3 | 3 | 3 | +0 | 100% | COMPLETE |
| 5 | grexpAll | 5 | 5 | 5 | +0 | 100% | COMPLETE |
| 6 | RO_track_f_ll | 8 | 8 | 8 | +0 | 100% | COMPLETE |
| 7 | G1_G2 | 5 | 5 | 5 | +0 | 100% | COMPLETE |
| 8 | G3_true | 16 | 16 | 16 | +0 | 100% | COMPLETE |
| 9 | RO_LCDHAdv | 17 | 17 | 17 | +0 | 100% | COMPLETE |
| 10 | G2_bad_ub | 15 | 15 | 15 | +0 | 100% | COMPLETE |
| 11 | G2_G3 | 30 | 13 | **8** | **−5** | 27% | MAX_STEPS |
| 12 | INDCPA_HEG_G1 | 52 | 21 | **49** | **+28** | 94% | STUCK |
| 13 | correctness | 58 | 58 | 58 | +0 | 100% | COMPLETE |
| 14 | G1_G2_eq | 85 | 18 | **58** | **+40** | 68% | STUCK |
| | **TOTAL** | **301** | **185** | **249** | **+64** | **83%** | |

Calibration passed: every trial that replayed verbatim with zero agent
iterations counts exactly its replayed prefix (see §12.3's counting note).

Three things the outcome column hides.

**`INDCPA_HEG_G1` reached 94% of the original script** — 49 of 52 tactics, 28
of them added by the model — and is still recorded as a failure. It is three
tactics short. That is the closest any run has come to a large repair.

**`G2_G3` ended BELOW its replayed prefix** (8 against 13, −5). Five `undone`
events and MAX_STEPS arrived before the model rebuilt what it had removed. Same
shape as run C's `G1_G2_eq` (§12.3b): a trial cut off mid-backtrack leaves the
file worse than the bootstrap left it, and the number is not a measure of
anything.

**Retained tactics rose 237 -> 249 (+12)**, which is inside the noise. `G2_G3`
alone swung 19 -> 13 between two *identically configured* runs, so a 12-tactic
difference across a whole run separates nothing.

### 13.5 What run E establishes

**Does.** `max_steps` was not the binding constraint. The lemma with the most
headroom used 35% of it. Raising the multiplier is not the lever, and §12.5b's
recommendation is superseded by this section.

**Does not.** That `STUCK_LIMIT` is the lever instead. That is the obvious next
knob and this run does not justify turning it: three runs, one seed each, and
§6's measured spread exceeds every difference in §13.1. What the run does
justify is *looking at* the point where `G1_G2_eq` went stuck at 91 steps with
a 6.64 accept ratio — a specific, reproducible state, which is better evidence
than another knob turned.

Note also that the two failure modes are not equivalent for cost. STUCK stops
early, so run E cost slightly *less* than run D ($1.4633 against $1.4887)
despite 1.8x the step budget. Budget headroom that is never used is free.

---

## 14. How the repair actually works, and where it stops

Two halves of the system, documented against run E's artifacts. They are in
very different states, and the honest summary is that one is finished and the
other has not yet been shown to do anything.

### 14.1 Import repair: what it changed, and why the file then loaded

Every ElGamal lemma starts unloadable. `INDCPA_HEG_G1` (trial 12) is
representative — `parse_error` at line 108, so `llm -upto` cannot reach a goal
and the trial would once have been discarded as `goal_unreachable`.

Four rules fired, each selected against the error EasyCrypt reported *at that
moment*, and each verified by re-running the compiler before being kept:

| rule | kind | action | selected for | evidence it helped |
|---|---|---|---|---|
| `proc-star-removed` | syntax_change | `replace_regex` ×2 | `parse_error` (rel 2) | first error 108 → 127 |
| `smtmap-symbols-moved-to-fmap-r2025.02` | symbol_moved | `add_require FMap` | `unknown_symbol` (rel 2) | 127 → 357 |
| `declare-module-ascription` | syntax_change | `replace_regex` ×1 | `parse_error` (rel 2) | kind changed `parse_error` → `unknown` |
| `old-module-restriction-sets` | syntax_change | `add_pragma +old_mem_restr` | `unknown` (rel 0) | 357 → 453 |

The resulting diff, on a 486-line file that stays 486 lines:

```diff
-require import AllCore Distr SmtMap DBool FSet.
+pragma +old_mem_restr. require import AllCore Distr SmtMap DBool FSet FMap.
-  proc * init() : unit
+  proc init() : unit
-    proc * choose(pubk : group) : text * text {RO.f}
+    proc choose(pubk : group) : text * text {RO.f}
-declare module Adv : ADV{RO, Adv2LCDHAdv}.
+declare module Adv <: ADV{RO, Adv2LCDHAdv}.
```

Four separate 2020-era breakages, four different mechanisms:

1. **`proc *`** — a parser feature deleted in r2023.09. Pure syntax.
2. **`SmtMap` → `FMap`** — 125 declarations moved in r2025.02. The file still
   requires `SmtMap`, which still exists, so nothing looks wrong until a name
   fails to resolve. `FMap` is *added* rather than substituted, because
   `FMap.ec` itself requires `SmtMap` and the file uses both halves.
3. **`declare module X : T`** — module-system syntax, now `<: T`.
4. **Unprefixed restriction sets `{RO, Adv}`** — semantics changed; the pragma
   restores the old reading in one line rather than editing every site, which
   is what keeps line numbers stable.

**Line-preservation is the load-bearing property.** `ProofCase` records
absolute lemma line numbers, so a repair that inserted a line would silently
point every later trial at the wrong lemma. The pragma folds onto line 1, the
require is extended in place, and the rest are in-place substitutions;
`apply_actions` asserts the count is unchanged.

The endpoint is the point: `parse_error:108` → `tactic_error:453`. The file now
loads far enough to open the lemma, and everything still wrong with it is the
solver's problem. That is `reached_proof`, and it held for 12 of 12 attempts in
all three runs.

### 14.2 The knowledge base: one part earns its place, one does not

The block handed to the model has three sections. They are not equally useful
and the artifacts say so plainly.

**The import-repair summary — essential.** The first 14 lines tell the model
what was changed in its file and why. Without it the model is proving against
a file it has never seen: a tactic naming `SmtMap.dom` would look inexplicably
wrong. This is not a "hint" in the retrieval sense, it is the diff.

**Symbol resolution — the most targeted content in the block.**

```
- `mem_empty` is declared in FMap (`require import FMap.`)
- `output` is declared in 6 theories: DDH_hybrid, Hybrid, PRG, Pr_half,
  SDist, TotalProb -- qualify the reference or require the one you mean
```

Derived from the 6325-symbol index, specific to the failing step, and
actionable. This is what the index is for.

**Changelog retrieval — no measured contribution.** For trial 14 all four
entries retrieved were r2023.09 *chore* commits about raw `smt` calls, matched
on nothing more than the token `smt` appearing in the failing tactic
`smt(mem_empty).`:

```
- [r2023.09] (mechanism_change) [chore] fix more raw smt calls  (matched smt)
- [r2023.09] (mechanism_change) [chore] fix raw smt calls (theories/...)  (matched smt)
```

Matching a tactic *head* against changelog prose retrieves everything that
mentions the word. `smt` appears in hundreds of commits and means nothing
specific.

`hint_uptake` for run E is **0.0** — not one identifier the changelog half
introduced appeared in a tactic EasyCrypt accepted, across all four scoring
trials. (That number is only trustworthy since §12.4 stopped it counting
pre-existing names and English prose; before that it read 50%.)

**So: import repair and the symbol index are doing work. The changelog
retrieval has not been shown to.** It may still be right for a corpus whose
breakage is genuinely a documented library change rather than 2020 syntax;
this one's is not.

### 14.3 Where the harness gets stuck — and it is not where it looked

Run E's two STUCK trials end the same way. `G1_G2_eq`, last 20 steps:

```
accepted  wp.
accepted  wp.
accepted  wp.
... (20 consecutive, all accepted)
```

Every one succeeded. And the goal never moved:

```
23 of 23 consecutive steps saw a BYTE-IDENTICAL goal
(sha1 293cdab8, 5464 chars, unchanged throughout)
```

`wp.` is a **no-op** in that state. EasyCrypt returns 0, so the harness records
`accepted`, appends the line, and shows the model the same goal again. The
model, reasonably, tries `wp.` again.

This is not rare:

| run | accepted tactics that left the goal unchanged | worst offenders |
|---|---|---|
| C | **44 / 73 (60%)** | `auto` 31, `progress` 3, `call` 3 |
| D | **100 / 164 (61%)** | `wp` 57, `auto` 17, `seq` 9 |
| E | **113 / 179 (63%)** | `wp` 60, `auto` 25, `if` 6 |

**Around 60% of every "successful" tactic in every run accomplishes nothing.**

The reason the loop does not notice is precise.
`integration/agent/loop.py` hashes the *proof text*:

```python
state_hash = _proof_state_hash(proof.tail(config.proof_tail_lines))   # 20 lines
if state_hash in seen_proof_states:
    stuck_counter = _increment_stuck(config, stuck_counter)
else:
    seen_proof_states.add(state_hash); stuck_counter = 0
```

A no-op still **appends a line**, so the tail changes, the hash is new, and
`stuck_counter` resets to zero. Detection only begins once the tail is
*entirely* identical `wp.` lines — after `proof_tail_lines` (20) wasted steps —
and then needs `stuck_limit` (20) more to trip. Up to **40 steps of pure waste
before the harness reacts**, at ~116 s and real money per step.

That fully explains run E: `G1_G2_eq` stopped at 91 of 260 steps with a 6.64
accept ratio. It was not failing. It was succeeding at nothing, and the
detector measured the wrong thing.

### 14.4 Proposed features, ranked by the evidence above

**1. Goal-based no-op detection.** *(highest value, smallest change)*

Compare the goal before and after an accepted tactic. Identical goal means the
tactic did nothing, whatever EasyCrypt's return code says. Then:

- **undo it** — it does not belong in the proof. `G1_G2_eq`'s "58 retained
  tactics" is inflated by ~20 `wp.` lines that a human would delete on sight,
  so this improves the *artifact*, not just the search;
- **do not reset `stuck_counter`** — a no-op is the definition of an
  unproductive step, and resetting on one is why detection takes 40 steps;
- **tell the model**: "`wp.` was accepted but left the goal unchanged; it has
  nothing left to consume here."

Evidence: 60–63% of accepted tactics in all three runs; the direct cause of
both STUCK outcomes. Attacks wall clock, spend, proof quality and the stuck
limit simultaneously. `_proof_state_hash` already exists — this is hashing the
goal instead of the proof tail, plus an undo.

**2. Ban a tactic that just no-op'd, the way failures are already banned.**

`prompt.py::_banned_tactic_strings` exists and lists tactics that *failed*. A
no-op is recorded as `accepted`, so it never reaches that list, which is why
the model repeats it 20 times. Extending the ban to no-ops is a few lines and
depends only on (1).

**3. Narrow changelog retrieval, or stop paying for it.**

Matching on a tactic head retrieves every commit mentioning that word — four
r2023.09 `smt` chores for a `smt(mem_empty)` failure. Options, cheapest first:
require the match to be an *identifier* rather than a tactic name; drop entries
whose `repair_hint` is generic; or gate the whole changelog half behind a
relevance floor. §14.2 shows the symbol-index half already works, so this is
about removing noise from a block that also carries signal, not about
abandoning the knowledge base.

**Explicitly NOT recommended: raising `STUCK_LIMIT`.** It is the obvious knob
and it would make things worse — it buys more repetitions of a no-op, not more
progress. Fix the detector first; the limit may then be too *generous* rather
than too tight.

**Deferred, and why.** Trial-level parallelism is a real ~2.4× on wall clock
(§throughput analysis: 99.6% of 9.4 h sits in 4 independent trials) but
`SpendBudget` is shared mutable state checked before each call with no locking,
so under concurrency the cap becomes approximate — unacceptable on a paid run
without a reservation model. Worth doing, not worth doing casually. And it is
second priority behind (1), because (1) makes the expensive trials cheaper
rather than merely running them in parallel.

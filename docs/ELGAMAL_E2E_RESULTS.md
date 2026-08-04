# End-to-End Results — `elgamal-changelog-repair`

**Date:** 2026-08-03 · **Branch:** `shannon-llm-integration` · **Corpus:**
`data/derens99-ElGamal-proof/hashedelgamal.ec` (2020-era Hashed ElGamal,
genuinely broken against modern EasyCrypt)

Companion document: [`IMPLEMENTATION_PROGRESS.md`](IMPLEMENTATION_PROGRESS.md)
— what was built and why.

---

## 0. Read this first: how to read `successes: 3`

Two runs are reported here, and they answer different questions:

| Run | Solver | What it establishes |
|---|---|---|
| **A — stubbed** | Local stub returning a deliberately-failing tactic | That the *harness* works: dispatch, replay, retrieval, prompt assembly, metrics |
| **B — DeepSeek** (`deepseek-v4-flash`, thinking off) | Real paid API, 23 calls, $0.0305 | That a *model* was given the knowledge base and what it did with it |

**The single most important reading correction:** run B's `summary.json` says
`successes: 3`, and **none of those three came from the model**. All three are
trials that replayed verbatim with **zero LLM calls** (`steps: 0`, `calls: 0`).
On the three genuinely broken lemmas, DeepSeek repaired **0 of 3**.

`successes` counts trials that reached `COMPLETE` by any route, and the
zero-LLM replay path is a route. That is the correct definition for the field,
but it means the headline number must never be read as a repair rate. The
per-trial breakdown in §5 is the number that answers "did the model fix
anything".

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

# DeepSeek
export DEEPSEEK_API_KEY=...
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --provider deepseek --trials 10 --stuck-limit 20 --max-steps 200 \
  --llm-model deepseek-v4-flash --thinking disabled \
  --embed-model <local-embed-model>

# Local model (Gemma et al. via LM Studio) — free, no confirmation
python3 -m integration.experiment run --spec elgamal-changelog-repair \
  --trials 10 --stuck-limit 20 --max-steps 200 \
  --llm-model <model> --embed-model <embed-model>
```

Embeddings always use LM Studio regardless of provider, so a local embedding
server is needed in all three cases.

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

# Attacking §0: making the model's tactic proposals better

**Date:** 2026-08-06 · **Branch:** `shannon-llm-integration` · **Audience:** an
LLM agent implementing this after the current ElGamal run lands.

Companion to [`PROOF_REPAIR_NEXT_HANDOFF.md`](PROOF_REPAIR_NEXT_HANDOFF.md).
That document covers no-op detection, position errors and the wrong-class
problem. This one covers the single thing none of them fixed: **the model
proposes tactics that do nothing at roughly the same rate no matter what the
harness tells it.**

Do not start until the run in `integration/output/experiments/run-20260806T194914Z`
has finished — several numbers below should be re-measured with its data
included, and §7 says which.

---

## 1. Two numbers that are easy to confuse

The handoff's headline is "~50% of tactics the model proposes change nothing".
That is a statement about **proposals**. It is *not* a statement about the
proof script, and conflating the two produces the wrong plan.

Measured over all `agent_log.json` in `integration/output/experiments/`:

| | tactics KEPT in script | of which inert | detector catch rate |
|---|---:|---:|---:|
| pre-detection runs | 465 | **229 (49.2%)** | 0% |
| detection-era runs | 34 | **3 (8.8%)** | **93.6%** |

*Detection-era* = runs `20260806T031235Z`, `20260806T124022Z`,
`20260806T194914Z`. Earlier runs report zero no-ops **by construction** —
`confirm_noop` did not exist, so inert tactics were silently accepted. Never
pool the two eras; the pooled figure is meaningless.

**Conclusion.** `confirm_noop` works: it catches 93.6% of inert steps and the
share of retained-inert tactics fell 49.2% → 8.8%. The handoff's "None of them
reduced the rate" is true of *proposal quality* and false of *script
contamination*. Only proposal quality is still open, and it is what this
document addresses.

Reproduce with the snippet in §7 before changing anything. If these numbers do
not reproduce, stop and find out why first.

---

## 2. What NOT to build (measured and refuted)

### 2.1 Static precondition rules — refuted

The obvious idea: `ec_program.py` knows each side's last statement kind, so
predict that `wp` is inert when nothing assignable remains, `rnd` when the tail
is not a sampling, etc. **It does not work.**

| tactic | rule | precision | base rate |
|---|---|---:|---:|
| `wp` | neither side ends in an assignment | 3.8% | 2.9% |
| `auto` | neither side ends in assign/sampling | 8.5% | 9.2% |
| `rnd` | neither side ends in a sampling | 0.0% | 40.6% |

`auto` is *worse than chance*. EasyCrypt's `wp` processes conditionals and more
than the trailing assignment, so the syntactic precondition does not imply
inertness. Do not build a predictor. Do not "improve" these rules.

### 2.2 Subgoal count as an inertness signal — refuted

See handoff §2. Text equality implies count equality (0 counterexamples in 525
transitions), and a count-first rule wrongly calls 113 of those 525 inert.

---

## 3. The idea that does follow from the data: probe locally

### 3.1 The cost asymmetry

Measured on the real 741-line `G2_G3` working file
(`trials/trial_011/agent_work.agent.ec`), binary
`integration/extern/easycrypt/_build/default/src/ec.exe`:

| operation | cost |
|---|---|
| EasyCrypt full-prefix probe (`llm -lastgoals`) | **~1.5 s** |
| one model call (observed this run) | **~160 s** |
| **ratio** | **~100:1** |

Across all runs: **358 of 1024 steps were wasted** (`no_op` or `failed`) =
**15.9 hours** of model time. The same tactics run as local probes: **8.9
minutes**.

### 3.2 The design

At each step, before asking the model:

1. Build a candidate set (§3.3).
2. Run each candidate through EasyCrypt on a scratch copy of the proof.
3. Classify with `integration/agent/goal_diff.py::compute_state_diff`:
   * fails to compile → discard, remember the error
   * compiles, goal byte-identical → **inert**, discard
   * compiles, goal moves → **live**, keep with its `StateDiff`
4. Put only the live candidates in the prompt, each with its verdict
   (`PROGRESS`, `PROGRESS_DECOMPOSITION`, …) and metric deltas.
5. The model chooses among verified-live options and supplies arguments, rather
   than guessing whether a tactic applies at all.

This does not replace the model. It removes from its job the part it is
measurably bad at (predicting applicability) and keeps the part it is good at
(choosing strategy, writing invariants).

### 3.3 Candidate set — phase 1 (do this first)

**Bare parameterless tactics only.** Enumerable today, no argument synthesis:

```
wp.  auto.  skip.  trivial.  progress.  simplify.  sp.  subst.  rnd.  done.
```

Scope, measured: **92 of the 358 wasted steps (25.7%)** were exactly a bare
parameterless tactic. That is the guaranteed win — a quarter of all waste, at
1% of the cost.

Excluded from probing: anything already in `noop_by_goal[goal_hash]` or the
per-goal failed-tactic ban list. Those are known dead at this goal.

### 3.4 Candidate set — phase 2 (only after phase 1 is measured)

`seq` and `rnd` are where the remaining no-ops concentrate:

| tactic | inert | accepted | inert rate | share of all no-ops |
|---|---:|---:|---:|---:|
| `rnd` | 13 | 19 | **40.6%** | **29.5%** |
| `seq` | 9 | 60 | 13.0% | **20.5%** |
| `auto` | 9 | 113 | 7.4% | 20.5% |
| `wp` | 4 | 163 | 2.4% | 9.1% |

Top three = **70% of all no-ops**.

For `seq N M : (inv)` the invariant cannot be enumerated, but the **positions
now can**: `integration/agent/ec_program.py` gives exact bounds
(`len(pair.left)`, `len(pair.right)`), the divergence point
(`common_prefix_length`) and matched-call cuts (`seq_candidates`). Probe the
position grid with a placeholder invariant to find which cuts are structurally
legal, then let the model supply the invariant for the legal ones only.

Do not attempt this until phase 1 has shipped and been measured.

---

## 4. Two cheap fixes, independent of the above

### 4.1 A stuck model gets the least information

`loop.py` sets `last_accepted = None` on a confirmed no-op (and on undo), so
`format_state_diff` renders nothing. At exactly the moment the model is most
stuck, it receives no structural description of the goal.

Live example, `G2_G3` steps 27–31: `seq 1 1 : (…)`, `wp.`, `auto.`, `rnd.` —
four consecutive no-ops, four model calls, ~11 minutes, no state-diff shown at
any of them.

Fix: when the last step was inert, emit a block describing the *current* goal
structurally (subgoal count, statement counts per side, what has already been
ruled out at this goal) rather than a transition. Keep it distinct from the
"what your last tactic did" block — the semantics differ.

### 4.2 The ban list teaches one dead tactic per 160 s

Each rejected tactic costs a full model call to discover. With §3 in place,
present the probe results as a single "already ruled out here" list, so one
call rules out twenty candidates instead of one.

---

## 5. A real bug found while investigating (fix this regardless)

`loop.py::_probe_compound_subgoal` (~line 1500) reports a misleading
diagnostic. Real instance, `G2_G3` step 30, tactic `rnd; wp; skip; smt().`:

```
[critical] … left instruction list is not empty
[diagnostic subgoal] Step 1/4 `rnd.` OK — no open goal (discharged or empty).
Remaining segments were not applied (no open goal).
```

The diagnostic claims `rnd.` left no open goal. It did not: the same `rnd.` was
proposed alone at step 31 and `confirm_noop` proved it **inert** — the goal was
still there, unchanged.

**Cause.** Line ~1500 calls `resolve_goal(proof, config)` directly.
`resolve_goal` is lossy in exactly this case: after a goal-unchanged tactic it
walks back past each unchanged cursor and returns `""`. `loop.py::_current_goal`
exists precisely to fix this (it falls through to the raw `llm -upto` output),
and this function never got the fix.

**Fix.** Use `_current_goal(proof, config)` instead of `resolve_goal(...)` at
that call site. Then an empty result genuinely means discharged.

**Why it matters.** The message tells the model its compound over-reached and
an earlier segment closed the goal. Here the truth was the opposite — `rnd.`
did nothing and `skip` failed on a non-empty program. The model is being
actively misled at a stuck goal.

Add a regression test: a compound whose first segment is inert must report the
segment as inert, not as "no open goal".

---

## 5b. Three more defects found during run `20260806T194914Z`

All three are independent of §3/§4 and worth fixing before any further
measurement run, because each one corrupts what a run would tell you.

### 5b.1 A single transport error kills a trial

`G2_G3` exited `LLM_ERROR` after **38 productive steps and 100 minutes** on one
`Connection error.`

**Known trigger: the laptop was closed.** This was a host suspend, not DeepSeek
flakiness — do not go looking for provider reliability problems. It matters for
two reasons. First, the earlier `LLM_ERROR` rows in the run history
(`G1_G2_eq` at 0, 0 and 3 steps; see §5b.1a) are probably the same cause and
not evidence about the corpus. Second, it lowers the *likelihood* of
spontaneous recurrence but not the severity: any transient transport error
still ends a trial, and long trials run for hours unattended.

### 5b.1a `LLM_ERROR` is over-represented in the run history

`G1_G2_eq` across seven runs: `LLM_ERROR` (0 steps), `STUCK` (6), `LLM_ERROR`
(3), `BUDGET_EXHAUSTED` (18), `MAX_STEPS` (145), `STUCK` (91), `LLM_ERROR` (0).
Three of seven died on `LLM_ERROR`, two of them before completing a single
step. Once §5b.1 is fixed, re-read this history — some of that variance is
almost certainly infrastructure, not the agent, and the handoff's
"run-to-run variance exceeds most effects" warning may be partly an artifact
of this bug.

`openai.APIConnectionError` is not an `LlmFormatError`, so it misses the
provider-failure branch in `loop.py` (~line 445) and lands in the catch-all
`except Exception` (~line 463), which returns `ExitReason.LLM_ERROR`
immediately. **`max_consecutive_provider_failures` is bypassed entirely** — the
bound never gets a chance to apply.

This is a recurrence of a bug the code already documents. `llm.py` ~line 377
says:

> "`response.choices[0]` then raises TypeError, which is NOT an
> LlmFormatError, so loop.py's catch-all turns a transient hiccup into a
> terminal LLM_ERROR -- observed killing trials after 31 and 70 productive
> iterations."

That fix was applied to the no-choices case only. Network exceptions were left
on the fatal path.

**Fix.** Classify transport-level exceptions as provider-side so they go
through the retry bound. Catch `openai.APIConnectionError`,
`APITimeoutError`, `RateLimitError` and `InternalServerError` (5xx) and re-raise
as `LlmProviderError`. Do NOT widen the catch-all — an authentication failure
must still terminate promptly rather than retry five times.

**Test.** A backend raising `APIConnectionError` twice then succeeding must
finish the run, not exit `LLM_ERROR`. Mirror the existing
`test_provider_failures_do_not_exhaust_the_stuck_limit`.

### 5b.2 The repair ladder cites information the prompt did not print

At `G2_G3` step 1 the model was told:

> "The tactic is right, the INDEX is wrong. **Re-read the instruction counts
> above** and pick indices within them."

There were no instruction counts above. The goal was a `[programs are in sync]`
case: EasyCrypt prints no statement list, `_program_statement_block` returns
`""`, `parse_program_block` correctly reports `indexed=False`, and
`_seq_position_bullets` never ran. With nothing to read, the model guessed —
`seq 5 5`, `seq 7 7`, `seq 6 6`, `seq 5 5`.

**48% of our goals carry the sync marker**, so this is the common case, not an
edge case.

**Fix.** In `prompt.py::_ERROR_REPAIR_LADDER`, the `invalid 'position'
parameter` rung must branch on whether counts are available. When they are not,
say so and direct the model to the *original* tactic's indices, which encode the
author's structural knowledge, instead of implying a table it cannot see.

### 5b.3 An asymmetric cut is silently replaced by a symmetric guess

The original `G2_G3` tactic is `seq 4 3 : (…)` — deliberately asymmetric,
because the two programs are. Every model attempt was symmetric: `5 5`, `7 7`,
`6 6`, `5 5`.

**Fix.** When `config.broken_tactic` is a `seq N M` with `N != M`,
`format_broken_tactic_repair` should state that the asymmetry is information and
that collapsing it to `N N` discards the author's alignment. Cheap, and it
targets the largest failure category.

---

## 5c. CORRECTION — the `seq` bounds hint does not prevent position errors

Measured live on `INDCPA_HEG_G1`, run `20260806T194914Z`. **Do not build on the
assumption that correct instruction counts fix position errors. They do not.**

Of 12 failed `seq` attempts, 11 had exact bounds printed in the prompt (the
12th was a sync goal with none). **Every one used indices inside those
bounds and was still rejected:**

| step | tried | my bounds L/R | in range | EasyCrypt said |
|---:|---|---|---|---|
| 26–34 | `seq 1 1`, `2 2`, `3 4` | 11/10 | yes | ``invalid `position' parameter`` |
| 36 | `seq 4 4` | 13/12 | yes | `invalid split index: ^<5` |
| 38 | `seq 3 3` | 13/12 | yes | `invalid split index: ^<4` |

Steps 36 and 38 are the informative ones: EasyCrypt states its own limit, and
it is **far smaller** than the statement count (`<5` and `<4` against a
computed 13/12). The goal at step 26 was verified by hand to be a genuine
equiv with 11 and 10 top-level statements — the parse is right; the *model of
the precondition* is wrong.

**What is still true.** `ec_program.py` counts top-level statements correctly,
and the old line-counting overstated the maximum on 503 of 654 goals. That
measurement stands and the fix is worth keeping.

**What is now known false.** That `0..len(side)` is the admissible range for
`seq N M`. It is necessary, not sufficient. The prompt currently asserts
"N must be 0..13 and M must be 0..12. Any other index is `invalid 'position'
parameter`" — which is a **false statement** when EasyCrypt will only accept
N < 5.

**Action.** Either derive the real precondition (start from EasyCrypt's
`invalid split index: ^<K` message, which names K directly, and from `seq`'s
implementation in the fork) or soften the claim to a hard upper bound rather
than an admissible range. Do not leave the prompt asserting a range EasyCrypt
does not honour. Until then, treat the `^<K` error as the authoritative source
of K and feed it back on retry — it is strictly better information than
anything currently computed.

---

## 5d. Latency is prompt-driven, not throttling (diagnosed and acted on)

Per-step latency on `INDCPA_HEG_G1` roughly tripled during the run. Four lines
of evidence say the cause is prompt size (or the reasoning it provokes), not
the provider:

| steps | mean gap |
|---|---:|
| 1–10 | 93 s |
| 11–20 | 193 s |
| 21–30 | 235 s |
| 31–40 | 278 s |
| 41–49 | 237 s |

1. **It plateaus** at ~235–278 s, exactly where `history_steps` fills. A
   throttle degrades continuously.
2. **Two trials two hours apart trace the same curve against step number**, not
   clock time: `G2_G3` (20:15Z) 139 → 155 → 167 → 280 s; `INDCPA_HEG_G1`
   (22:15Z) 93 → 193 → 235 → 278 s.
3. **No rate limiting.** A naive grep "found" `429` six times; every hit was
   digits inside a cosine score (`0.5938940372504027`). There are no HTTP 429s.
   Match on the error field, not the whole event blob.
4. **Correlations point at accumulated context**: r(latency, step) = +0.60,
   r(latency, reply size) = +0.34, r(latency, goal size) = **−0.14**.

No stalls: median gap 225 s, largest 7 min, 49 steps in 2.79 h.

**Acted on.** `history_steps` 20 → 15, and a new thinking mode
`high_unless_stuck` (`config.py`): thinking enabled at effort `high` while the
model converts calls into progress, effort released once ≥4 of the last 6 steps
were unproductive (`STRUGGLE_OUTCOMES` includes `no_op` and `undone`, not just
failures). Set via `THINKING_MODE = "high_unless_stuck"` in the notebook.

**Caveat, unresolved.** "Prompt length" is the proximate cause; the mechanism
may be reasoning tokens, since adaptive thinking can scale depth with a longer
history of failures. The two are not separable from what is logged today —
`reasoning_tokens` is only recorded in the trial-level aggregate. **Log
per-call `reasoning_tokens` in the iteration event** before tuning this
further; it is a one-line change and it settles the question.

---

## 5e. What this run says about the harness overall

Read this before planning more harness work.

**93% of all COMPLETE trials required zero agent work.** Across ten runs: 100
COMPLETE, of which 93 were verbatim replays where the agent was never invoked.

**The model has repaired exactly one distinct lemma, ever** — `INDCPA_Security`,
a 2-line proof, in all seven runs where it was reached. No other lemma has ever
been repaired by the model.

The fair denominator is 3, not 15: only `INDCPA_Security` (1/2 replayed),
`G2_G3` (13/30) and `INDCPA_HEG_G1` (21/52) fail to replay. So the score is
**1 of 3, unchanged across ten runs and every configuration change** — no-op
detection, enforcement, prompt fixes, repair-first, and everything in this
document's companion.

The implication for §3 is uncomfortable and should be stated plainly: probing
targets *waste*, and waste has never been the binding constraint on solve rate.
It will make runs cheaper and faster. There is no evidence it will make them
succeed.

---

## 6. Implementation order

0. **§5c** — the prompt currently asserts a `seq` index range EasyCrypt does
   not honour. It is stating something false to the model on every equiv goal.
   Fix or soften before anything else here.
1. **§5b.1** — network exceptions must go through the retry bound. It
   destroyed a 100-minute trial; the known trigger was a laptop suspend, so it
   is less likely to recur spontaneously than first assessed, but any transient
   transport error still ends a trial.
2. **§5b.2** — the ladder's dangling reference to absent instruction counts.
   Affects the 48% of goals carrying the sync marker.
3. **§5** — the `_current_goal` fix in `_probe_compound_subgoal`. Small,
   self-contained, currently feeding the model false statements.
4. **§5b.3** — asymmetric-`seq` note in the repair prompt. One paragraph.
5. **§4.1** — structural block on a stuck goal. No new machinery.
6. **§3.3** — phase 1 probing, bare tactics only.
7. Measure (§7). Only then consider **§3.4** and **§4.2**.

Items 1–4 are small and independently testable. Do them before any measurement
run: each one corrupts what a run would tell you, and 1 can end a run outright.

**Status 2026-08-07: items 0–4 are DONE**, along with §5d's latency work.
Shipped: the `seq` ceiling wording and both position rungs (`prompt.py`), the
asymmetric-cut note, `TRANSPORT_ERRORS` → `LlmProviderError` (`llm.py`),
`_current_goal` in `_probe_compound_subgoal` (`loop.py`), `history_steps` 15,
and `high_unless_stuck` (`config.py`). Suite: 488 passed, 1 skipped. Items 5–7
(§4.1 structural block, §3.3 probing) remain.

---

## 9. Future work

### 9.1 What would it actually take to close `G2_G3`?

**This is the highest-value open question in the project, and it is not a
harness question.** Ten runs have tuned the harness around a metric that has
not moved. Before building more, find out what the gap actually is.

`G2_G3` is the tractable unsolved case: 30 original tactics, 13 replay
cleanly, the 14th (`seq 4 3 : (…)`) is where it stops. Best observed run
reached 14 accepted tactics in 38 steps before dying on a laptop suspend.

Method — deliberately *not* a harness run:

1. Work it by hand, or with a strong model and no step budget, no ban list, no
   prompt scaffolding. Just the goal, EasyCrypt, and as many attempts as it
   takes.
2. Record what closed it: how many steps, which tactics, and — the point of
   the exercise — **what information was needed that the harness never
   supplies.**
3. Classify the gap into one of: (a) proposal quality, which probing and
   prompt work can address; (b) missing lemma/library knowledge, which needs
   retrieval, not reasoning; (c) something structural about the proof that no
   step-at-a-time agent can find.

Only (a) justifies continuing the current line of work. If it is (b) or (c),
this document's §3 is the wrong investment and should be dropped.

Do the same for `INDCPA_HEG_G1` (21/52 replayed) if `G2_G3` closes.

### 9.2 Escalating to a more expensive model

The corpus runs on `deepseek-v4-flash` at ~$0.0035/call. Total spend across the
entire ten-run history is a few dollars. **Cost is not the constraint and never
has been** — wall-clock and solve rate are.

That makes a capability escalation cheap to try, and it should be tried as a
*measurement*, not a config change:

* **Ladder, in order:** `deepseek-v4-pro` (~3× the flash rate), then
  `claude-sonnet-5`, then `claude-opus-5` ($5/$25 per Mtok in/out — roughly
  100× flash on output). Even at Opus rates a full `G2_G3` trial at 75 steps
  is single-digit dollars.
* **Run the escalation on `G2_G3` alone**, not the suite. 12 of 15 lemmas are
  free replays and tell you nothing; spending Opus tokens on them is pure
  waste. Point the spec at the one lemma that matters.
* **Raise `COST_LIMIT_USD` before switching.** The $5.00 cap was sized for
  flash; at Opus rates it would stop a single trial partway and produce an
  uninterpretable result — which already happened once at $1.00
  (`BUDGET_EXHAUSTED` at 18 steps).
* **`llm_max_tokens` is provider-sensitive.** The notebook sets 32768 after
  measuring ~13.2k mean output against a 16384 cap; re-measure per model rather
  than carrying that number across.
* **Anthropic is not a base-URL swap.** `apply_anthropic_provider` handles the
  different client, message shape and thinking/effort parameters; Opus 5 takes
  no `temperature` at all. Thinking defaults to `adaptive` there and Claude
  scales depth itself — `resolve_thinking_for_step` deliberately passes it
  through untouched, so `high_unless_stuck` is a DeepSeek-only policy.

The question this answers is the one in §9.1: if a frontier model closes
`G2_G3` from the same prompt, the gap is proposal quality and §3 is worth
building. If it does not, no amount of harness engineering will.

---

## 7. Acceptance criteria

Re-run these before and after. All snippets assume repo root and
`.venv/bin/python`.

**Baseline reproduction** (must match §1 before you change anything):

```python
import json, glob, collections, pathlib, sys
sys.path.insert(0, '.')
from integration.agent.goal_diff import _normalize
DETECT = ("20260806T031235Z", "20260806T124022Z", "20260806T194914Z")
tot = collections.Counter()
for f in glob.glob("integration/output/experiments/*/trials/*/agent_log.json"):
    era = "detect" if any(d in f for d in DETECT) else "pre"
    it = [e for e in json.loads(pathlib.Path(f).read_text())["events"]
          if e.get("event") == "iteration"]
    for a, b in zip(it, it[1:]):
        if a.get("outcome") != "accepted":
            continue
        same = _normalize(a.get("goal") or "") == _normalize(b.get("goal") or "")
        tot[(era, "identical" if same else "moved")] += 1
    for e in it:
        if e.get("outcome") == "no_op":
            tot[(era, "flagged")] += 1
print(tot)
```

**Targets after phase 1:**

| metric | now | target |
|---|---:|---|
| wasted steps (`no_op` + `failed`) as share of all steps | 35.0% | materially lower |
| bare-parameterless wasted steps | 92 | ~0 (probed, never proposed) |
| retained-inert share of kept tactics | 8.8% | no worse |
| `G2_G3` accepted tactics at trial end | 3–19 across 4 runs | no worse |

**Non-negotiable invariants:**

* `test_noop_tactics.py::test_goal_text_equality_is_not_proof_of_inertness`
  must still pass. Probing must never delete a load-bearing tactic.
* `test_subgoal_count_does_not_rescue_the_skip_case` must still pass.
* Probing runs on a **scratch copy**. The working proof must be byte-identical
  before and after a probe round; assert this in a test.
* Full suite green: 475 passed, 1 skipped at time of writing.

---

## 8. Pitfalls

**Run-to-run variance exceeds most effects.** `G2_G3` retained 19 → 13 → 8 → 3
tactics across four runs, two identically configured. A single run is not
evidence. Prefer offline replay over the logged corpus to measurement by new
run wherever the question allows it.

**Probe cost is corpus-specific.** 1.5 s was measured on `G2_G3` (741 lines).
`G1_G2_eq` is 104 tactic lines with a 260-step budget and its prefix replay
will be slower. Measure probe cost on the **largest** lemma before fixing a
candidate-set size. At 20 candidates × 1.5 s = 30 s against a 160 s call it
pays; at 20 × 8 s it does not.

**Probing taxes the steps that were already fine.** ~65% of steps are not
wasted, and they pay the probe cost for no gain. Consider probing only when the
previous step was inert or failed, rather than unconditionally.

**Do not pool detection-era and pre-detection runs** for any inertness metric.
See §1.

**The Jupyter kernel caches modules.** New modules are invisible to a running
kernel regardless of `%autoreload`. Verify with an assertion that fails on
stale code, not with an import that succeeds either way.

**`nice -n 19` your probes** if a run is in flight, so measurement does not
distort the thing being measured.

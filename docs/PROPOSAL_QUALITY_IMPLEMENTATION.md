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

## 6. Implementation order

1. **§5** — the `_current_goal` fix. Small, self-contained, and it is currently
   feeding the model false statements.
2. **§4.1** — structural block on a stuck goal. No new machinery.
3. **§3.3** — phase 1 probing, bare tactics only.
4. Measure (§7). Only then consider **§3.4** and **§4.2**.

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

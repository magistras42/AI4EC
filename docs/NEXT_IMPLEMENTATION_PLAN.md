# What to build next, in order

**Date:** 2026-08-10 · **Branch:** `shannon-llm-integration` · **Audience:** the
LLM agent picking this up.

Companion to [`WRITEUP.md`](WRITEUP.md) (how the harness works) and
[`PROPOSAL_QUALITY_IMPLEMENTATION.md`](PROPOSAL_QUALITY_IMPLEMENTATION.md)
(the §0 problem). This document is only the ordered work queue.

---

## The number this is all measured against

Run `20260810T053405Z`, the first run whose `COMPLETE` can be trusted
(`fully_replayed` now requires `is_proof_complete`):

| | |
|---|---|
| proofs that close | **11 / 15** |
| closed **by the model** | **1** (`INDCPA_Security`, 1 step, $0.0012) |
| closed on replay, no LLM | 10 |
| did not close | `G2_bad_ub` (MAX_STEPS), `G2_G3`, `INDCPA_HEG_G1`, `G1_G2_eq` (STUCK) |
| spend | $1.0960 |

**Thirteen runs. One lemma. The model has never closed any of the hard three.**

Everything shipped in the last two sessions — no-op detection, the undo clamp,
corrected `seq` bounds, `smt` syntax, names-in-scope, transport retries,
`history_steps`, comment stripping — made failures **cheaper, better attributed
and non-destructive**. None of it moved the solve rate. Rank accordingly: an
item earns a high place only if there is a mechanism by which it changes
whether a proof closes.

---

## Tier 0 — Answer the question before building more (highest value, least code)

### 0.1 Find out what closing `G2_G3` actually requires

**Why first.** Thirteen runs have tuned the harness without establishing which
problem we have. Three possibilities, and they imply completely different work:

* **(a) proposal quality** — the model can't pick the right tactic. Probing
  and prompt work are the answer.
* **(b) missing knowledge** — it needs a lemma the retrieval never surfaces.
  Retrieval is the answer; better prompting is not.
* **(c) structural** — the proof needs a plan no step-at-a-time agent finds.
  Neither is the answer.

**Method — deliberately not a harness run.** `G2_G3` is the tractable case: 13
of 30 tactics replay, the 14th (`seq 4 3 : (…)`) is where it stops. Work it by
hand, or with a strong model, no step budget, no ban list, no prompt
scaffolding. Record what closed it and — the point — **what information was
needed that the harness never supplies**.

**Deliverable.** A classification of (a)/(b)/(c) with the evidence. If (b) or
(c), items 1.1 and 1.2 below should be dropped, not merely deprioritised.

### 0.2 Escalate the model on one lemma

**Cost has never been the constraint** — $1.0960 for a 15-trial run. A
`claude-opus-5` attempt at `G2_G3` alone is single-digit dollars.

* Point the spec at that one lemma. 12 of 15 are free replays; spending
  frontier tokens on them is waste.
* Raise `COST_LIMIT_USD` first — $5.00 was sized for `deepseek-v4-flash` and
  will stop an Opus trial mid-way, producing an uninterpretable result.
* Anthropic is not a base-URL swap; `apply_anthropic_provider` handles the
  different client and thinking parameters. `high_unless_stuck` is DeepSeek-only.

**This is the cheapest possible test of 0.1.** If a frontier model closes
`G2_G3` from the same prompt, the gap is proposal quality. If it does not, no
harness engineering will close it either.

---

## Tier 1 — Plausible mechanism for the solve rate

### 1.1 Consume the logic-class probe *(built, wired to nothing)*

**Evidence.** 24% of tactic failures are program-logic tactics applied to a
goal whose judgment is already discharged. Text classification cannot fix this:
`goal_looks_program_logic` says program-logic on **91%** of them, because a
discharged judgment still prints `pre`/`post`. The best text discriminator
found is 57.7% precision.

`easycrypt.probe_is_program_logic` already exists, is tested, and answers it
**decisively** — verified `True` on a live goal, `False` after `skip.`
discharges it, file left byte-identical.

**Build.** Call it from `loop.run_agent` and feed the result into
`format_active_goal_shape_hints`, overriding the text heuristic when the probe
is conclusive.

* Cost ~1.5 s against a ~170 s model call.
* **Gate it**: run only when the text heuristic is in its unreliable band (no
  instruction index column and 2 open goals — the 57.7% hedge in WRITEUP §4.1),
  not every step.
* `None` means inconclusive — fall back to the text heuristic, never assume.

**Acceptance.** Re-label the 568-step corpus with the probe; precision should
be ~100% against outcome ground truth. Then a run where
`expecting a goal of the form` failures drop from their current ~15%.

### 1.2 Local tactic probing, phase 1 *(the big one)*

Full design in `PROPOSAL_QUALITY_IMPLEMENTATION.md` §3. Summary:

**Evidence.** EasyCrypt probe ~1.5 s vs model call ~170 s — a **100:1** ratio.
358 of 1024 steps across all runs were wasted (`no_op` or `failed`) = **15.9
hours** of model time; the same tactics probed locally would be **8.9 minutes**.

**Build.** Before asking the model, run a candidate set through EasyCrypt on a
scratch copy, classify with `goal_diff.compute_state_diff`, and put only the
*live* candidates in the prompt with their state-diffs.

**Phase 1 is bare parameterless tactics only** — `wp. auto. skip. trivial.
progress. simplify. sp. subst. rnd. done.` That is 92 of the 358 wasted steps,
fully enumerable today. Do **not** attempt parameterised tactics until phase 1
is measured.

**Honest caveat, and why this is 1.2 not 1.1.** Probing targets *waste*, and
waste has never been the binding constraint on solve rate. Expect cheaper and
faster runs; do not expect more proofs to close. `G1_G2_eq` this run produced
13 consecutive no-ops permuting one idea (`mem_set` vs `FMap.mem_set`,
`move:` vs `rewrite … in`) — probing removes the cost of discovering they are
inert, not the fact that the model had one idea.

### 1.3 Tell a stuck model what it is looking at

**Evidence.** On a confirmed no-op the loop sets `last_accepted = None`, so
`format_state_diff` renders nothing. At the moment the model is most stuck it
receives the *least* structural information. `G1_G2_eq` went 33 steps without
an accepted tactic in that state.

**Build.** When the previous step was inert, emit a block describing the
*current* goal structurally — subgoal count, statement counts per side, what
has already been ruled out at this goal — rather than a transition. Keep it
distinct from the "what your last tactic did" block; the semantics differ.

### 1.4 Widen the no-op ban past cosmetic variation

**Evidence.** The ban is keyed on `(goal_hash, normalize_tactic(tactic))`, and
`normalize_tactic` only folds whitespace, trailing `.` and `&&`/`||`. On
`G1_G2_eq`, 13 consecutive no-ops at an unchanged goal were all *different*
strings, so none was ever banned:

```
move: H3; rewrite mem_set => hd; case hd => [hm | he…      x3
move: H3; rewrite mem_set => hd; smt().
move: H3; rewrite FMap.mem_set => hd; case hd => [hm…
rewrite mem_set in H3; case: H3 => [hm | heq]; …
rewrite FMap.mem_set in H3; smt().
```

**Build.** Extend normalisation so a qualified name and its basename collapse
(`FMap.mem_set` ≡ `mem_set`), and consider banning on the *head tactic plus
name set* rather than the full string when a goal has produced ≥3 no-ops.

**Risk.** Over-banning blocks a legitimate variation. Measure how many
*accepted* tactics would have been caught by the wider rule before shipping —
the same false-positive discipline that killed static precondition prediction
(`PROPOSAL_QUALITY` §2.1).

---

## Tier 2 — Harness correctness and observability

These do not move the solve rate. They make the next measurement trustworthy,
which is worth more than it sounds given how many conclusions this project has
had to retract.

### 2.1 Record the config in the run artifacts

**Evidence.** After the latency regression in `20260810T053405Z` (mean 270 s
vs 167 s, 26% of steps over 400 s) **it was not possible to determine which
timeout the run used.** There is no `run_flags.json`, and the `startup` event
records only `source`, `work_copy`, `premise_count`, `cursor_upto`.

**Build.** Add provider, model, thinking mode, effort, `lm_studio_timeout`,
`history_steps`, `llm_max_tokens` and `max_steps` to the `startup` event.
~10 lines. It would have made that question answerable in seconds.

### 2.2 Record `time.monotonic()` per step

Three runs died to the host sleeping, and one stall was reported **216 minutes**
late because the watcher slept too. A gap where wall-clock advanced but
monotonic did not is a suspend; the reverse is a hang. One field makes every
future post-mortem decidable instead of inferred.

### 2.3 Make non-proof breakage visible

**Evidence.** `import_repair` is the only component that repairs anything
outside a proof body, and its vocabulary is fixed (`add_require`,
`replace_require`, `remove_require`, `rename_symbol`, `replace_regex`,
`add_pragma`). Module, `clone`, `op`/`type` and `axiom` declarations are out of
scope for the entire harness — the agent loop only appends tactics after
`proof.`.

This has never bitten because every load failure on this corpus was an import
(94 trials, `resolved=True`, zero `goal_unreachable` skips) — a property of the
corpus, not the harness. And it matters: the single `+FMap` require is what
makes nine lemmas close.

**Build.** When import repair leaves a file unloadable, classify the residual
error by *declaration kind* rather than recording a flat `goal_unreachable`.
Costs nothing and turns an invisible class into a counted one. **Do not extend
the repair vocabulary until that count is non-zero.**

---

## Tier 3 — Speculative; measure before building

### 3.1 Decomposition ladder for large ambient residuals

`G2_bad_ub`'s open goal is ~2400 characters of nested quantifiers whose
conjuncts are largely reflexive. `smt` returns `cannot prove goal (strict)` and
`progress.` is a **no-op**, so the usual decomposition does nothing.

Possible: detect an ambient goal above a size threshold with a top-level
`forall`/`=>` chain and advise the explicit ladder (`move =>`, `split` per
conjunct, targeted `smt` per leaf). `goal_diff` already counts top-level
connectives and quantifiers.

**Why speculative.** One lemma, one run. It may be that the obligation needs a
lemma the corpus does not contain, in which case no decomposition advice helps.
**Count how many ambient failures share the shape before building anything.**

### 3.2 Snapshot/restore of proof state — evaluated, not recommended

The file is already a stack (`append_tactic` inserts before `qed`,
`remove_lines` only ever removes the line just added), so sequential undo
already reproduces any earlier state exactly. Snapshots would cost ~2.7 MB per
trial and add no recovery capability.

Worse, "restore checkpoint 5" is a bulk delete with a friendlier name, which
re-arms exactly what the clamp disarms (125 removals requested this run, ~31
performed). **If you want it, use snapshots for automatic regression detection
— restore when `net_tactics_vs_bootstrap` goes negative — rather than handing
the model a faster way to do the thing it does badly.**

---

## Rules for whoever implements this

**Do not add another prompt section without a prevention story.** Five were
added last session. Each was justified by a failure class the note *names*, and
that is not the same as one it *prevents*: `move => &1 &2` was warned about
explicitly and recurred four times in the next run.

**Never pool detection-era with pre-detection runs** for any inertness metric —
the latter report zero no-ops by construction.

**Compare tactics with the replay's own rule**: strip `(* … *)` comments, split
on `.<whitespace>`, and compare whitespace-insensitively. Physical-line
splitting reported `G2_bad_ub` as losing 12 of 15 prefix tactics when it lost 1;
byte-exact comparison reported zero re-attempts of a deleted tactic when there
was one.

**Strip warnings before reading an error.** EasyCrypt prints file-level
`[warning]` lines before the `[critical]` one; taking `splitlines()[0]` raw
invented a `global axiom … in section` failure mode that does not exist.

**Run-to-run variance exceeds most effects.** `G2_G3` retained 19 → 13 → 8 → 3
tactics across four identically-configured runs. Within *one* run this time,
inert rate swung 0% → 85% across consecutive 20-step windows on the same lemma.
A single run is not evidence.

**Restart the kernel and prove it.** New modules are invisible to a running
kernel regardless of `%autoreload`. Use
`notebook_support.verify_working_tree_is_live()`, which fails on stale code
rather than passing quietly.

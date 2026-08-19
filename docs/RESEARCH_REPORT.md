# Repairing Version-Drifted EasyCrypt Proofs with an LLM Agent

**Methodology and results.** Branch `shannon-llm-integration`, 2026-08-10.

---

## Abstract

We built a harness that takes EasyCrypt proofs written against a 2020-era
release, replays them against a current build (r2026.06), and drives an LLM to
repair the tactics that no longer apply. Over 17 experimental runs (222 trials,
1,470 model calls, $10.47) we measured which parts of the pipeline actually
determine whether a proof closes.

The headline result is negative and, we argue, the most useful thing the study
produced. **Of 15 ElGamal lemmas, 11 close; the LLM is responsible for exactly
one of them, and that one is a two-line proof.** The other ten close because a
single `require import` was added — syntax porting, not proof repair. Four
successive engineering phases made failures cheaper, better attributed,
non-destructive, and honestly counted, and **none of them moved the solve
rate.**

A secondary result concerns measurement rather than capability: for most of the
study the harness reported success it had not achieved. `validate_file`'s exit
code was read as "the proof closes" when it means "the tactics parsed", so **12
of 15 proofs were counted as COMPLETE without closing.** Several intermediate
conclusions in this project were drawn from that inflated number and had to be
retracted.

---

## 1. Problem and research questions

EasyCrypt is an interactive proof assistant for cryptographic security proofs.
Proofs are tactic scripts, and they are brittle across releases: a library
lemma is renamed, a tactic's argument form changes, a theory moves. A proof
that verified in 2020 typically fails against a 2026 build. Repairing such
proofs by hand is routine, tedious, and expensive expert time — an obvious
candidate for LLM assistance.

**RQ1.** Can an LLM agent repair version-drifted EasyCrypt proofs?

**RQ2.** Where does the difficulty actually lie — proposing the right tactic,
knowing the library, or something structural?

**RQ3.** Which harness affordances (structured goal information, error
enrichment, retrieval, guardrails) change the outcome, as opposed to changing
the cost?

RQ3 is the one this study is best positioned to answer, because it is the one
where we ran controlled-ish comparisons. RQ1 and RQ2 we answer only partially,
and §7 explains why.

---

## 2. Related systems and positioning

**shannon-prover** is a sibling project targeting the same problem with a
different architecture: a persistent EasyCrypt REPL managed by a session
manager, a workflow orchestrator, and a proof-tree supervisor. We deliberately
did **not** adopt that design (§3.1), and we ported three of its analyses —
structural state diffing, `seq` cut-point suggestion, and error classification
— reimplementing them against our own primitives. Where our measurements
contradicted theirs we recorded both (e.g. their `PROGRESS_DECOMPOSITION`
verdict does not identify the `skip.` case we needed it to; §4.4).

**Positioning.** Most LLM-for-proof-assistant work targets *synthesis* — write
a proof of this statement. We target *repair*: a correct proof exists, it is in
front of the model, and only some of it no longer applies. That is an easier
problem in principle, which is what makes the negative result interesting.

---

## 3. System design

### 3.1 The stateless-checker decision

The harness does not hold a live EasyCrypt session. It owns a proof **file**
and queries a fresh `ec.exe` process:

| invocation | returns |
|---|---|
| `llm -upto N file.ec` | goal state after line N |
| `llm -lastgoals file.ec` | validation; non-zero exit = failure |
| `llm -upto N -premises file.ec` | goal plus the ambient premise catalog |

`llm` is a fork-local subcommand, not upstream EasyCrypt.

**Rationale and consequences.** Statelessness buys simplicity — the file *is*
the state, so there is no session to desync and rollback is deleting a line. It
costs re-replay on every query: ~1.5 s on a 741-line file. That figure turns
out to be the single most important number in the system, because a model call
costs ~170 s. **The checker is ~100× cheaper than the model**, which is what
makes local verification of candidate tactics viable (§8.2).

The main cost is epistemic: the goal is observable only through a printer that
is *lossy*. `resolve_goal` returns `""` after a tactic that leaves the goal
unchanged, and three separate defects in this project came from reading that as
"proof discharged" (§6.1).

### 3.2 Pipeline

```
corpus case ──► import_repair ──► replay_bootstrap ──► agent loop ──► outcome
              (make it LOAD)     (replay original     (repair from
                                  tactic-by-tactic)    the break)
```

**Step 1 is load-bearing for everything after it.** If `import_repair` cannot
make the file load, the goal query fails, the trial is recorded
`goal_unreachable`, and *no tactic is ever tried*. On this corpus that step is
one line — adding `FMap` to a `require import` — and that one line is what
makes ten lemmas close (§5.2).

**Step 2** replays the original tactics one at a time, keeping the prefix that
still applies. The agent takes over at the first failure. This is the main
design difference from a from-scratch reconstruction mode we also implemented:
the agent begins with 185 of 300 tactics already verified rather than none.

**Step 3** is a per-step loop: resolve goal → rank premises → build prompt →
model call → apply/undo/lookup → classify outcome.

### 3.3 Instrumentation

Each step records goal text, tactic, outcome, error, ranked premises and
timing. Outcomes are `accepted`, `no_op`, `failed`, `rejected`, `undone`,
`complete`. The `no_op` class — a tactic EasyCrypt accepts that changes
nothing — required a dedicated detector (§4.4) and turned out to be a large
fraction of all activity.

---

## 4. Methodology

### 4.1 Corpus

15 lemmas from a public ElGamal/hashed-ElGamal development
(`derens99-ElGamal-proof`), 300 tactics total, ranging from 1-tactic lemmas to
an 85-tactic equivalence proof. Two secondary corpora (Joy, 33 cases; LQ-1, 1
case) were used as controls. Target: EasyCrypt r2026.06, resolved by
`git describe` on the vendored fork.

### 4.2 Experimental protocol

Each trial: sandbox the case, port syntax, replay, hand the residue to the
agent with a step budget of 2.5× the original tactic count (minimum 10).
Terminate on `COMPLETE`, `MAX_STEPS`, `STUCK`, `LLM_ERROR` or
`BUDGET_EXHAUSTED`. Model: `deepseek-v4-flash`, adaptive thinking, spend capped
at $5.00 per run.

### 4.3 Measures

* **Solve rate** — proofs the *model* closed. Distinguished throughout from
  proofs that close on replay, because conflating them is the central
  measurement error of this project (§6.2).
* **Inert rate** — `no_op` as a share of productive steps.
* **Net tactics vs bootstrap** — final tactic count minus the replayed prefix.
  Introduced late (§4.5) and the only measure that detected a destructive
  behaviour every other counter missed.
* **Per-step latency**, wall-clock and cost.

### 4.4 Detecting inert tactics

A tactic compiling is not the same as it doing anything: EasyCrypt accepts a
`wp.` with nothing left to consume and reports nothing. Naïve detection — "goal
text unchanged means the tactic did nothing" — is **unsound**, and our own
fixture disproves it: `skip.` renders byte-identical through the goal printer
and is load-bearing.

Our detector requires three independent signals: the resolved goal unchanged,
the raw cursor goal unchanged, **and** a removal proof (delete the line,
re-check, confirm the state is identical). It fails safe.

We evaluated and **rejected** the alternative used by shannon-prover, subgoal
count as primary signal, on measurement: across 525 accepted transitions, the
number where goal text was byte-identical but subgoal count moved is **zero**
(so the count can never rescue a tactic text comparison condemns), while 113 of
525 moved the text with every structural metric flat (so a count-first rule
would wrongly delete 21% of accepted steps).

### 4.5 A guardrail found by instrumentation

Adding the net-tactics measure revealed that the agent was **dismantling its
own verified prefix**: on one run, three trials finished holding 12 of 13, 9 of
21 and 2 of 18 replayed tactics. A single `undo(count=12)` erased 12 of 18 at
step 2, after one failed `smt`. No conventional counter showed it — accepted,
failed and no-op all looked ordinary.

We added a clamp: a *multi-tactic* undo stops at the prefix boundary;
single-step undos remain unrestricted, because a repair sometimes genuinely
needs to reach back. Measured before/after:

| | prefix survival |
|---|---|
| unprotected | 5/13, 5/21, 1/18 |
| clamped | **13/13**, 14/15, 20/21 |

~153 removals requested, ~31 performed. We further established the deletion is
**one-way**: the agent re-attempted a deleted tactic and it *failed*, because
the proof state in which it applied was gone. So the clamp prevents an
unrecoverable error, not a recoverable one.

---

## 5. Results

### 5.1 Aggregate

| | |
|---|---|
| runs with trials | 17 |
| trials | 222 |
| model calls | 1,470 |
| total spend | $10.47 |
| distinct lemmas ever closed **by the model** | **3** |

The three: `INDCPA_Security` (ElGamal, 2 tactics, closed 11 times),
`sampling_bound` (LQ-1, 5 tactics), `games_quadruple` (Joy, 5 tactics — and
this one was later shown to be an artifact of a harness bug, §6.3).

### 5.2 The definitive run

Run `20260810T053405Z` is the first whose `COMPLETE` can be trusted:

| | |
|---|---|
| proofs that close | **11 / 15** |
| closed **by the model** | **1** (`INDCPA_Security`, 1 step, $0.0012) |
| closed on replay, zero LLM calls | 10 |
| did not close | `G2_bad_ub`, `G2_G3`, `INDCPA_HEG_G1`, `G1_G2_eq` |
| spend | $1.0960 |

Per-lemma tactic accounting:

| lemma | original | replayed | model Δ | outcome |
|---|---:|---:|---:|---|
| `enc_stateless` | 1 | 1 | +0 | COMPLETE |
| `INDCPA_Sec` | 1 | 1 | +0 | COMPLETE |
| `INDCPA_Security` | 2 | 1 | **+1** | COMPLETE (model) |
| `log_gen` | 3 | 3 | +0 | COMPLETE |
| `gen_log` | 3 | 3 | +0 | COMPLETE |
| `grexpAll` | 5 | 5 | +0 | COMPLETE |
| `G1_G2` | 5 | 5 | +0 | COMPLETE |
| `RO_track_f_ll` | 8 | 8 | +0 | COMPLETE |
| `G2_bad_ub` | 15 | 15 | +2 | MAX_STEPS |
| `G3_true` | 16 | 16 | +0 | COMPLETE |
| `RO_LCDHAdv` | 17 | 17 | +0 | COMPLETE |
| `G2_G3` | 30 | 13 | +1 | STUCK |
| `INDCPA_HEG_G1` | 51 | 21 | +0 | STUCK |
| `correctness` | 58 | 58 | +0 | COMPLETE |
| `G1_G2_eq` | 85 | 18 | **−3** | STUCK |
| **total** | **300** | **185** | **+1** | |

**197 model calls produced a net of one tactic.** The replay gap is where the
difficulty lives: 115 of 300 tactics fail to replay, and 114 of those are in
three lemmas.

### 5.3 The hard lemmas have never been closed

| lemma | trials finished | model-closed | outcomes |
|---|---:|---:|---|
| `G2_G3` | 11 | **0** | 5 MAX_STEPS, 4 STUCK, 2 LLM_ERROR |
| `INDCPA_HEG_G1` | 9 | **0** | 4 STUCK, 3 LLM_ERROR, 2 MAX_STEPS |
| `G1_G2_eq` | 8 | **0** | 3 STUCK, 3 LLM_ERROR, 1 MAX_STEPS, 1 BUDGET |
| `G2_bad_ub` | 11 | **0** | 10 false COMPLETE (§6.2), 1 MAX_STEPS |

### 5.4 What each engineering phase bought (RQ3)

| phase | representative measured effect | solve rate |
|---|---|---|
| 1. Replay instead of reconstruct | agent starts from 185/300 verified tactics | — |
| 2. Waste suppression | retained-inert 49.2% → 8.8%; 0 leaked repeats | unchanged |
| 3. Correct the prompt's claims | `seq` maximum was **overstated on 503 of 654 goals**; `smt(a, b)` is a parse error the prompt was teaching | unchanged |
| 4. Correct the harness's self-report | 12 of 15 false COMPLETEs eliminated; prefix survival 1/18 → 20/21; latency tail 1207 s → 353 s | unchanged |

**This is the study's central finding.** Four phases of genuine, measured
improvement to cost, safety, and honesty produced no change in whether proofs
close.

### 5.5 Failure taxonomy

Over 426 failures: ~45% position errors (right tactic, wrong target), 24% wrong
logic class, ~4% name/scope, the remainder solver limits and argument errors.

Notably, **the mechanical share is large and shrinks under targeted fixes but
does not vanish.** On one lemma, 8 of 19 failures were `move =>` misuse — the
model introducing memories (`&1`, `&2`) that are already bound, or introducing
against a conclusion with no leading binder. A conclusion-shape check we built
would have warned on 8/8 of them.

Equally, the inert rate is **positional, not configurational**: within a single
trial it swung 0% → 85% → 11% across consecutive 20-step windows with no
configuration change. Any claim about inert rate from a single run is noise.

§5.7 re-derives this taxonomy independently over all 536 failures and maps it
onto shannon-prover's published failure modes; the two agree on the position
share (45% here, 43% there).

### 5.6 Cost, effort, and waste accounting

All figures below are re-derived from the run artifacts themselves —
`events.jsonl` (`trial_finish` records), `summary.json`, and the per-trial
`agent_log.json` iteration stream — over 17 runs, 222 trials and 1,470 model
calls, reproducing §5.1's aggregate exactly. Fifty trials invoked the agent;
they contain 1,553 iterations, of which 1,333 are tactic submissions.

Nothing here is typed in by hand. Every figure in §5.6 and §5.7 comes from:

```bash
python3 -m integration.experiment.effort_metrics integration/output/experiments
```

and the headline-rate table below adds
`--run run-20260810T053405Z --run run-20260807T141126Z --run run-20260807T145511Z`
to restrict to the runs whose `COMPLETE` can be trusted.

Four of the measures are new here and need definitions:

* **Interaction step / round** — one model call: goal → prompt → single tactic,
  undo, or lookup. Steps and calls are 1:1 in every trial.
* **Wasted edit** — a tactic submission that does not survive into the final
  script: rejected by EasyCrypt or the harness, accepted but inert (`no_op`,
  so the harness removes it), or accepted and later undone by the agent.
  Every submission is a file write, so this is literally the share of edits
  that had to be reverted.
* **Useless context** — prompt material the model never acts on. Measured for
  the two blocks where uptake is decidable: the ranked-premise block (does any
  shown name appear in the emitted tactic?) and lookup/search results.
* **Prompt size** — `prompt_tokens` per call as reported by the provider.

#### Per run

| run | spec | trials | closed | by model | replay | STUCK | MAX | wall (h) | median trial (s) | calls | prompt tok/call |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20260803T175504Z | elgamal | 6 | 3 | 0 | 3 | 0 | 0 | 0.3 | 85 | 20 | 19,587 |
| 20260803T202647Z | elgamal | 6 | 3 | 0 | 3 | 3 | 0 | 0.2 | 130 | 23 | 11,106 |
| 20260803T211932Z | elgamal | 15 | 11 | 0 | 11 | 0 | 2 | 5.3 | 17 | 126 | 15,939 |
| 20260804T164111Z | elgamal | 15 | 12 | 1 | 11 | 0 | 2 | 7.5 | 16 | 162 | 17,675 |
| 20260805T031435Z | elgamal | 15 | 12 | 1 | 11 | 0 | 3 | 9.4 | 16 | 283 | 19,590 |
| 20260805T134244Z | elgamal | 17 | 14 | 1 | 13 | 2 | 1 | 12.2 | 7 | 287 | 20,423 |
| 20260806T015722Z | elgamal | 11 | 11 | 1 | 10 | 0 | 0 | 0.1 | 5 | 2 | 7,597 |
| 20260806T031235Z | elgamal | 15 | 12 | 1 | 11 | 1 | 0 | 3.2 | 14 | 85 | 17,115 |
| 20260806T124022Z | elgamal | 12 | 11 | 1 | 10 | 1 | 0 | 1.6 | 6 | 31 | 17,837 |
| 20260806T194914Z | elgamal | 12 | 11 | 1 | 10 | 0 | 0 | 2.2 | 7 | 41 | 19,253 |
| 20260807T015202Z | elgamal | 11 | 11 | 1 | 10 | 0 | 0 | 0.3 | 5 | 4 | 9,408 |
| 20260807T031032Z | elgamal | 14 | 12 | 1 | 11 | 1 | 1 | 9.0 | 10 | 201 | 19,649 |
| 20260807T141126Z | **lq1** | 1 | 1 | 1 | 0 | 0 | 0 | 0.05 | 174 | 1 | 7,634 |
| 20260807T143538Z | joy | 14 | 14 | 1 | 13 | 0 | 0 | 0.1 | 5 | 2 | 9,029 |
| 20260807T145511Z | **joy** | 33 | 33 | 0 | 33 | 0 | 0 | 0.1 | 4 | 0 | — |
| 20260807T151318Z | elgamal | 10 | 10 | 1 | 9 | 0 | 0 | 0.1 | 12 | 4 | 9,277 |
| 20260810T053405Z | **elgamal** | 15 | 11 | 1 | 10 | 3 | 1 | 14.6 | 29 | 198 | 22,632 |

Bolded runs are the three whose `COMPLETE` can be trusted (§6.2). In every
other row "closed" is inflated, and the single "by model" closure is
`INDCPA_Security` — except `20260807T143538Z`, whose model closure is the
retracted `games_quadruple` artifact (§6.3).

#### Per lemma, definitive run `20260810T053405Z`

| lemma | outcome | orig | replayed | steps | s/step | wall | accepted | failed | no-op | undo | tactics undone | prompt tok/call | thinking tok/call |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `enc_stateless` | COMPLETE | 1 | 1 | 0 | — | 5 s | — | — | — | — | — | — | — |
| `INDCPA_Sec` | COMPLETE | 1 | 1 | 0 | — | 5 s | — | — | — | — | — | — | — |
| `INDCPA_Security` | COMPLETE (model) | 2 | 1 | **1** | 236 | 236 s | 0 | 0 | 0 | 0 | 0 | 8,262 | 0 |
| `log_gen` | COMPLETE | 3 | 3 | 0 | — | 9 s | — | — | — | — | — | — | — |
| `gen_log` | COMPLETE | 3 | 3 | 0 | — | 9 s | — | — | — | — | — | — | — |
| `grexpAll` | COMPLETE | 5 | 5 | 0 | — | 12 s | — | — | — | — | — | — | — |
| `G1_G2` | COMPLETE | 5 | 5 | 0 | — | 16 s | — | — | — | — | — | — | — |
| `RO_track_f_ll` | COMPLETE | 8 | 8 | 0 | — | 17 s | — | — | — | — | — | — | — |
| `G3_true` | COMPLETE | 16 | 16 | 0 | — | 37 s | — | — | — | — | — | — | — |
| `RO_LCDHAdv` | COMPLETE | 17 | 17 | 0 | — | 29 s | — | — | — | — | — | — | — |
| `correctness` | COMPLETE | 58 | 58 | 0 | — | 73 s | — | — | — | — | — | — | — |
| `G2_bad_ub` | MAX_STEPS | 15 | 15 | 47 | 259 | 3.4 h | 9 | 22 | 9 | 5 | 7 | 22,278 | 15,592 |
| `G2_G3` | STUCK | 30 | 13 | 41 | 290 | 3.3 h | 13 | 15 | 4 | 8 | 12 | 17,267 | 16,221 |
| `INDCPA_HEG_G1` | STUCK | 51 | 21 | 34 | 284 | 2.7 h | 6 | 14 | 4 | 6 | 6 | 18,969 | 15,824 |
| `G1_G2_eq` | STUCK | 85 | 18 | 71 | 254 | 5.0 h | 4 | 22 | 27 | 7 | 7 | 28,130 | 12,666 |

LQ-1's `sampling_bound` (5 original tactics, all replay, proof still open):
1 step, 174 s, 1 call, 7,634 prompt tokens, COMPLETE. Joy's 33 cases: 0 steps,
0 calls, 2–32 s each, all COMPLETE on replay.

#### Headline rates, trustworthy runs only (49 trials)

| measure | value |
|---|---|
| **proof completion rate** | 45 / 49 = **92%** |
| **completion attributable to the model** | 2 / 49 = **4%** |
| **repair success rate** (agent actually invoked) | 2 / 6 = **33%** |
| **interaction steps to a valid proof** | **1, both times** |
| steps spent on proofs that never closed | 193 |
| wall time, definitive run | 14.6 h for 15 lemmas |
| share of wall time spent on the four failures | **98%** |
| per-step latency, agent trials | 254–290 s |

**Every proof the model has ever closed, it closed on its first move.** The
observed useful search depth is 1. Beyond that the curve is flat and expensive:
193 further steps and 14.4 hours bought nothing. This is a sharper statement of
§5.4 — the binding constraint is not budget, patience, or step limit.

#### Wasted edits

Across all 1,333 tactic submissions:

| disposition | count | share |
|---|---:|---:|
| rejected by EasyCrypt or the harness | 536 | 40.2% |
| **kept in the final script** | **442** | **33.2%** |
| accepted, then undone by the agent | 213 | 16.0% |
| accepted but inert, removed as `no_op` | 142 | 10.7% |

**67% of all edits are reverted.** Restricted to the three trustworthy runs it
is **89%** (16 of 151 submissions kept), and in the four failing lemmas of the
definitive run it is 78%, 83%, 95% and 98% — `G1_G2_eq` made 53 file edits and
kept one. Note that 27% of the waste (355 of 1,333) is not rejection at all:
the checker accepted the edit and it still had to go, which is why acceptance
rate is not a usable progress signal (§4.4, §6.2).

#### Useless context and prompt size

| block | measured |
|---|---|
| ranked premises | shown on all 1,333 tactic turns, ~247 tokens/turn ≈ **329k tokens total**; a shown name appears in the emitted tactic on **18 turns (1.4%)**, and **0 of those 18 were accepted** |
| lemma lookup / search | 28 actions; **61% returned nothing**; only 11% are followed by an accepted tactic |
| goal text | median 2,630 chars ≈ 658 tokens — **2.9%** of a mature prompt |
| replayed history (`history_steps=15`) | not directly instrumented; by difference it is **~70% of a mature prompt** (see below) |

Prompt size per call: **7,634 tokens at step 1** (rules, few-shot block,
changelog hints, goal, premises, script tail) rising to a **28,130-token mean**
in the 71-step trial; mean across all agent-invoked trials 17,090. Provider
cache absorbs 56% of it. The growth is the 15-step history block, each entry
carrying a truncated thought (2,000 chars) and error (4,000 chars).

Against that, the model emits **~15,000 thinking tokens per step** (median
reasoning trace ~54,000 chars). Since the history block truncates each thought
to 2,000 chars, **roughly 96% of the model's own reasoning is discarded before
the next turn** — the agent re-derives its own state every step (§5.7).

The premise block is the clearest waste: 329k tokens spent, 1.4% uptake, zero
successful uses. It is embedding-similarity retrieval over the ambient catalog,
and on the ElGamal Pr-bridge goal it returns `Tactics.orW`, `RealOrder.invr_le0`
and `RField.invr_eq0`. Ranking by text similarity to a goal that is a
probability expression retrieves the vocabulary, not the structure (§8.5).

### 5.7 The ShannonProver failure taxonomy, measured

shannon-prover (§2) names three recurring failure modes of direct
checker-in-the-loop agents (arXiv:2607.02847, §2.1 and Table 1). Because our
harness is that baseline architecture by deliberate choice (§3.1), it is a
clean test of whether those modes are real and whether interface work touches
them. Their definitions, paraphrased:

* **Agent flinch** — the agent identifies a viable high-level route and
  abandons it when the route has to be written as a well-typed command,
  switching to a tactic that is easier to write.
* **Semantic mislocalization** — the agent attaches a failure to the wrong
  semantic object, pulled by names in the goal or the literal wording of an
  error.
* **Unconscious lowering** — the agent reads a local but unhelpful goal change
  as progress, while the step has consumed a high-level resource (call site,
  bridge lemma, oracle equivalence).

Measured over the 1,553 iterations, 536 failures and 1,092 stored reasoning
traces:

**Semantic mislocalization: present, and the one we partly fixed.** 232 of 536
failures (**43%**) are wrong-layer or wrong-position rejections — `expecting a
goal of the form: hoare[S]/equiv[S]`, `the conclusion is not a hoare or an
equiv`, `left instruction list is not empty`. This agrees with §5.5's
independently-derived 45%/24% split. Its share of failures by run:

| run | wrong-layer / failures |
|---|---:|
| 20260804T164111Z | 48% |
| 20260805T031435Z | 66% |
| 20260805T134244Z | 61% |
| 20260807T031032Z | 45% |
| **20260810T053405Z** | **11%** |

The drop follows the goal-shape hints, the program-logic tactic menu, the
`probe_is_program_logic` checker probe, and the in-scope-names note
(`ec_context`). Different lemmas across runs confound the comparison, but the
mechanism is targeted and the effect is large. Immediate re-tries of the same
head after a layer rejection are only 10%.

The name-driven form is untouched. `G1_G2_eq` names `mem_set` in **39 of its
53 tactics** while eight `search_lemmas` calls for it across `FMap` and
`SmtMap` all return *not found*; it permutes the surrounding syntax about
twenty ways rather than concluding the object is wrong. The premise ranker
(above) is the same failure on the retrieval side.

**Unconscious lowering: present, detected but not prevented.** 47% of tactic
attempts are lowering-class (`wp`, `auto`, `sp`, `smt`, `inline`, `progress`,
`skip`, `trivial`). Among runs after the `no_op` detector landed, **142 of 328
EasyCrypt-accepted moves — 43% — changed nothing**; `G1_G2_eq` is 51% inert.
**34% of accepted lowering steps (139 of 412) are later rolled back** by an
undo reaching past them, a mean 5.3 steps after the fact — the agent discovers
the loss late, because nothing warns it up front. The reasoning traces contain
the mechanism in the agent's own words: losing `={glob Adv}` from a `seq 2 2`
invariant and needing a 15-step undo to recover it; "`wp` on a call … the calls
would still be there unless `wp` erased them".

Our detector and bans (§4.4) are normalization-based, so they catch repetition
but not resource loss: `move: H3; rewrite mem_set => hd` and the same plus
`; smt()` are different tactics to the ban and the same non-move to the proof.
Nothing in the harness reports resource **liveness**, which is the remedy
shannon-prover proposes.

**Agent flinch: present but the weakest of the three, and partly suppressed.**
Of 29 failed named-route attempts, **26 (90%) were re-attempted later** — the
anti-loop rule keeps routes alive. Only 2 were dropped while the name kept
circulating in the reasoning, but one of those is textbook: in `G2_bad_ub`,
step 20 tries `apply (RO_LCDHAdv q1_L q2_L)` — the right bridge — takes a
proof-term mismatch, and never attempts it again; the reasoning re-derives that
route at steps 21, 22, 23, 28, 30, 32, 36, 39 and 47 while the emitted tactics
are only `auto`, `progress`, `move`, `smt`, `trivial`. At step 36 it states the
mechanism outright: *"that was a wasted search. We should avoid more lookups."*

We also tried a text-level flinch detector (reasoning names a structural route,
a cheap tactic is emitted: 387 hits, 198 with a retreat marker nearby) and
**reject it**: hand-checking a random sample shows most hits are state
confusion, not abandonment. That number should not be reported.

**What the traces show instead** is the root cause the paper assigns to all
three modes — the agent reconstructing context the interface should hand it.
Median reasoning is ~54,000 chars per step, dominated by re-reading the proof
script tail and re-deriving which goal is active, against a goal display that
is 2.9% of the prompt and a history block that discards 96% of what the model
thought last turn (§5.6).

| shannon-prover surface | our harness | status |
|---|---|---|
| L1 goal projection | full goal, last-tactic state diff | present |
| L2 typed IR / symbol table | `ec_context` in-scope names, `unknown_name_hint`, lemma search | partial — fixed the layer and scope errors |
| L2 resource selection by structure | embedding-ranked premises | **absent, and counterproductive** (1.4% uptake, 0 successes) |
| L3 resource liveness, lowering cost | — | **absent** — the largest remaining gap |
| L4 non-mutating probes | `probe_is_program_logic`, `_probe_post_proc_goal`, `_probe_compound_subgoal` | partial |
| L4 checkpoints and recovery | undo, `protected_prefix` clamp, stuck counter | present but coarse: step-count undo, not semantic rewind |
| neutral state-aware diagnostics | `[hint]` / `[diagnostic subgoal]` appended to checker errors | partial |

The reading consistent with §5.4: the interface work we did (L2/L4 fragments)
measurably removed the mechanical failure it targeted and **still did not
change whether proofs close**, while the two modes that remain unaddressed —
name-driven mislocalization and resource-destroying lowering — are exactly the
ones dominating the four lemmas that have never closed. That is a testable
ordering for §8, not a proof; the honest statement is that we have removed the
cheap failures and the expensive ones are untouched.

---

## 6. Threats to validity, and errors we made

This section is longer than usual because the measurement errors were the most
instructive part of the study.

### 6.1 Lossy observation

`resolve_goal` returns `""` for both "no goal" and "cannot resolve the goal".
Three defects followed: an inert-tactic detector that missed real no-ops; a
compound-tactic diagnostic that told the model "no open goal" while the real
error was `left instruction list is not empty`; and §6.2. The mitigation is a
lint that pins the legitimate call sites, because a comment ("any new caller
must…") had already failed to prevent the third occurrence.

### 6.2 The false-completion error (most serious)

`fully_replayed` was computed as "every tactic applied". `validate_file` exit 0
means the tactics **parsed**. For most of this study, **12 of 15 ElGamal proofs
were reported COMPLETE without closing**, and `G2_bad_ub` was a silent false
success in ten consecutive runs. Every "N of 15 complete" figure published
before the fix is inflated, and several intermediate conclusions in this
project were drawn from it and later retracted.

### 6.3 Corpus artifacts masquerading as results

The replay splitter did not strip comments before splitting on `.`-whitespace,
so comment prose became fake tactics — 19 of 33 Joy cases, and
`INDCPA_HEG_G1` 52 → 51. This inflated every "replayed N/M" denominator and
produced a spurious model "repair" on Joy (`games_quadruple`) that disappeared
once fixed: with the bug removed, all 33 Joy cases replay fully with zero LLM
calls.

### 6.4 Analysis-side errors

Two mistakes worth naming because they are easy to repeat. Comparing tactics by
**physical line** reported a lemma as losing 12 of 15 prefix tactics when it
lost 1 (a wrapped `seq` is three lines in one file and one in the other).
Comparing tactic text **byte-exactly** reported zero re-attempts of a deleted
tactic when there was one (the model rewrites spacing). Both were caught by
re-deriving the same claim two ways.

### 6.5 External validity

One corpus, one domain, one model. The syntax-porting dependency (§3.2) has
never failed here — 94 trials, zero load failures — which is a property of this
corpus, not the harness: its repair vocabulary covers `require`/symbol/pragma
edits and nothing for drifted `module`, `clone`, `op` or `axiom` declarations.
A corpus with those would fail before the agent runs.

### 6.6 Infrastructure noise in the historical record

Three runs died to the host sleeping, recorded as `LLM_ERROR`. Since
`LLM_ERROR` appears in 8 of the hard-lemma outcomes above, some of the
run-to-run variance previously attributed to the agent is infrastructure.

### 6.7 The corpus ladder measured 78% of the corpus

The difficulty ladder that selects experimental targets (Appendix B, stages
7–8) reports a lemma count and a proof-complexity score per repository. An
audit of the extractor against ground truth — the number of
`qed`/`save`/`abort`/`admit` terminators actually present in each repo's
sources — found it was seeing **30,424 of 39,223 proved obligations, 78%**.
Four defects, in descending order of cost:

1. **`proof.` was treated as mandatory.** Extraction was anchored on the
   literal keyword, but EasyCrypt lets a proof follow its statement directly:
   `lemma foo : P. <tactics> qed.` Every such lemma was invisible. Corpus-wide
   this was 12% of obligations, but the loss is concentrated, not spread:
   `SRI-High-Assurance-Crypto` lost 42% of its proofs, `Jasmin-Zk` 43%,
   `ZK-in-EC` 57%. A repo's score therefore depended on its authors' stylistic
   preference for an optional keyword.
2. **Semicolons were counted at any bracket depth.** EasyCrypt list literals
   are semicolon-separated, so `of_list [W64.of_int 0; W64.of_int 4; …]` read
   as a chain of tactics. **11.2% of all semicolons in the corpus (19,719 of
   175,993) sit inside brackets** — and they cluster in the Jasmin-adjacent
   repos with large extracted arrays, i.e. the same repos already losing the
   most lemmas to defect 1. Depth was being inflated for one set of repos and
   erased for an overlapping set.
3. **The legacy `save.` terminator was unsupported.** This one is a process
   failure rather than an oversight: it was diagnosed, fixed, and written up in
   `proof_corpus/AUTOCRYPT_VALIDATION.md`, but the code change landed on a
   branch that was never merged while the *documentation* of it was. The
   surviving branch therefore carried a validation document, and a docstring in
   `estimate_repair_difficulty.py` claiming `save` support, over a regex that
   did not have it. Two subsequent regenerations of the ladder reproduced the
   defect exactly and were read as confirmation that the numbers were stable.
4. **One unbalanced bracket silently consumed the rest of a file.** Introduced
   by the depth tracking that fixes defect 2: with no resynchronisation,
   nothing after a stray `[` is ever at depth 0 again, so no statement
   terminates. `eval/EasyPIR/puncturableprf.ec:134` declares
   `equiv [PPRF_Real.f ~ … ==> ={res}.` with no closing bracket and cost 8 of
   that file's 10 proofs. This matters more here than in a normal codebase: a
   corpus of *broken* proofs will contain malformed files by construction, so
   the damage from one has to stay bounded to the lemma containing it.

After the fixes, 34,195 of 34,596 terminators (98.8%) resolve to an extracted
block, plus 5,070 inline `by`/`realize` obligations that have no terminator at
all. AutoCrypt goes from 0 counted proofs to 55. Of the four repositories
previously reporting zero lemmas, two were genuine — `Cryptolib` is entirely
Jasmin extraction output and `Shipovnik-Verification` declares only axioms —
and both still report zero, which is the correct answer.

**The ladder's ordering did not change.** Not one of the 77 repositories moved
rank; only five saw any score change, the largest being AutoCrypt's +7.5 on a
scale running to 1,853. This is not reassurance, it is the more interesting
result: `proof_complexity` reaches `total_exposure_score` **only** through the
penalty applied to repos whose version is unknown or predates the changelog,
and that penalty's complexity scaling is clamped to [0.2, 2.0], where 9 of the
14 affected repos were already saturated at the ceiling. A 29% correction to
the corpus's measured proof complexity was absorbed entirely by a clamp. The
metric is nearly decorative in the current formula — which is worth knowing
before anyone reads the complexity column as though it ranked anything, and is
a stronger argument for reweighting the score than for any further parser work.

The class of error is the same one recorded in §6.3 and §6.4: a plausible
parser, never executed against a known answer. It is now pinned by 18 cases in
`integration/tests/test_proof_block_scanner.py`, built from the real shapes
above rather than invented ones. No test had covered `proof_corpus/scripts/`
at all.

---

## 7. Discussion

**RQ1 — can an LLM repair version-drifted proofs?** On this corpus, marginally.
It closed one two-line lemma reliably and nothing else. The honest framing is
that *syntax porting* repaired ten proofs and the LLM repaired one.

**RQ2 — where is the difficulty?** Unresolved, and this is the study's main
gap. Three hypotheses remain live: (a) proposal quality — the model cannot pick
the right tactic; (b) missing knowledge — it needs library facts retrieval
never surfaces; (c) structural — the proof needs a plan a step-at-a-time agent
cannot find. We never ran the experiment that distinguishes them (§8.1), and
without it, further harness engineering is unfalsifiable.

**RQ3 — which affordances matter?** Of everything built, the interventions with
a measured effect on *outcomes* were: replay-from-prefix (gives the agent 62%
of each proof for free), the undo clamp (prevents unrecoverable loss), and the
completion check (makes results trustworthy). The interventions with no
measured effect on outcomes — while being individually correct and often fixing
real falsehoods — were the prompt enrichments: structured `seq` bounds, state
diffs, names-in-scope, `smt` syntax, error ladders.

That asymmetry is the practical lesson. **Telling the model true things it
previously was told wrongly did not make it solve more proofs.** A prompt
section is justified by a failure class it *prevents*, not one it *names*; we
added five justified by the latter, and at least one failure class we warned
about explicitly recurred four times in the next run.

---

## 8. Future work

### 8.1 Determine which problem we have (prerequisite for everything else)

Close `G2_G3` by hand, or with an unconstrained frontier model — no step
budget, no ban list, no harness scaffolding — and record **what information was
needed that the harness never supplies**. Classify the gap as (a) proposal
quality, (b) missing knowledge, or (c) structural. Only (a) justifies
continuing the current line of work; (b) redirects effort to retrieval, and (c)
implies a different agent architecture entirely.

`G2_G3` is the right target: 13 of 30 tactics replay, and the break is a single
identifiable tactic (`seq 4 3 : (…)`).

### 8.2 Local verification of candidate tactics

The checker is ~100× cheaper than the model (1.5 s vs 170 s). Across all runs,
358 of 1,024 steps were wasted (`no_op` or `failed`) — **15.9 hours** of model
time that local probing would have resolved in **8.9 minutes**. The design:
enumerate candidates, run them through EasyCrypt on a scratch copy, and show
the model only those that *change the goal*, with their structural diffs.

Phase 1 (bare parameterless tactics) covers 92 of the 358 wasted steps and is
fully enumerable today. **Expected to make runs cheaper, not more successful** —
it targets waste, which our data says is not the binding constraint.

### 8.3 Ask the checker instead of guessing the goal class

24% of failures are program-logic tactics applied to already-discharged
judgments. Text classification cannot fix this — the printed form is genuinely
ambiguous, and our best text discriminator reaches 57.7% precision. A probe
tactic on a scratch copy answers it decisively for ~1.5 s. The probe is built
and tested but not yet consumed by the loop.

### 8.4 Model capability as an independent variable

Cost has never been the constraint ($10.47 across the entire study). Running
`claude-opus-5` on the three hard lemmas is single-digit dollars and directly
tests hypothesis (a) from §8.1. This is the cheapest high-information
experiment available and should be run before any further harness work.

### 8.5 Retrieval, if §8.1 implicates knowledge

Premise ranking is cosine similarity over the ambient catalog (2,583 entries at
a representative goal), top-10 into the prompt. Lemma lookup tools exist but
were used 9 times in 1,181 tactic actions, almost always *after* a failure. If
§8.1 says (b), the work is proactive retrieval keyed to goal structure rather
than reactive name lookup.

### 8.6 Broaden the corpus, and make non-proof breakage visible

One corpus is not a result. Extending to the vendored `eval/` tree (~40
projects) would test whether the syntax-porting dependency holds. Prerequisite:
classify residual load failures by *declaration kind* instead of a flat
`goal_unreachable`, so drifted `module`/`clone`/`op`/`axiom` declarations become
a counted class rather than an invisible one.

### 8.7 Measurement infrastructure

Two one-line changes that would have saved substantial analysis time: record
the provider configuration in the run artifacts (a latency regression could not
be attributed because nothing recorded the timeout in force), and record
`time.monotonic()` per step (a host suspend is currently indistinguishable from
a hang).

### 8.8 Methodological recommendation

Given that within-run inert-rate variance (0%→85%) exceeds every between-config
difference we measured, future comparisons need **≥5 seeds per arm** or should
be conducted as offline replays over the logged corpus rather than as new runs.
Several conclusions in this project's earlier documents rest on single runs and
should be treated as provisional.

---

## Appendix: reproduction

Environment: EasyCrypt r2026.06 (vendored fork, `git describe`), Python 3.12,
`deepseek-v4-flash`. Notebooks in `notebooks/`; specs registered in
`integration/experiment/specs.py`. Run artifacts under
`integration/output/experiments/<run>/`, archived in `important-runs/`.
Test suite: 558 tests.

The §5.6/§5.7 tables are regenerated from those artifacts by
`integration/experiment/effort_metrics.py` (`--run` to restrict to a subset,
`--json` to dump the full report); it reads finished artifacts only — no
EasyCrypt, no model, no network.

Companion documents: [`WRITEUP.md`](WRITEUP.md) (architecture and change
history), [`NEXT_IMPLEMENTATION_PLAN.md`](NEXT_IMPLEMENTATION_PLAN.md) (ranked
work queue), [`PROPOSAL_QUALITY_IMPLEMENTATION.md`](PROPOSAL_QUALITY_IMPLEMENTATION.md)
(the proposal-quality problem in detail).

---

## Appendix B: the corpus knowledge base (`proof_corpus/scripts/`)

Eleven scripts build the static facts the agent reads about what changed
between EasyCrypt releases, and rank the repositories used as repair targets.
They form two independent chains that meet only at the changelog index. Nothing
here calls an LLM at *query* time: one script does at *build* time, one needs
the network, and the remaining ten are pure derivations that are free to rerun.

The dependency order matters — every stage after the first reads an earlier
one's output, and running one out of order produces a stale artifact rather
than an error.

| # | Script | Cost | Reads | Writes | What it is for |
|---|---|---|---|---|---|
| 1 | `collect_changelog.py` | network | GitHub API | `raw_releases.json` | Fetches release bodies for `EasyCrypt/easycrypt`; `--with-pr-details` adds per-PR labels and changed files, which is what later lets a "library change" be told from an "engine change". Needs `GITHUB_TOKEN` for a usable rate limit. |
| 2 | `process_changelog.py` | **paid** | `raw_releases.json` | `changelog.yaml`, `llm_cache.json` | Classifies each release bullet into a `kind`, identifiers and a one-line repair hint. Rules handle the `internal`/`ci`/`documentation` bulk for free; the remainder goes to the Anthropic Message Batches API. Cached by `(repo, pr_number, title)`, so reruns are near-free. **The source of record** — 14 releases, 913 entries. |
| 3 | `build_changelog_index.py` | free | `changelog.yaml`, `raw_releases.json`, EC `theories/`, `tactics_ref.json` | `changelog_index.json` | Turns the nested YAML into the flat, typed query surface the harness actually reads. Its reason for existing is that `changelog.yaml`'s `identifiers` field is ~85% English prose — only 14.5% of its slots name a real EasyCrypt symbol — so this resolves names against the real theory tree and tactic vocabulary into separate `symbols` / `tactics` / `theories_touched` buckets. |
| 4 | `analyze_library_history.py` | free, slow | EasyCrypt clone with tags | `library_history.json` | Git-mines each standard-library theory for path events (add/move/delete) and per-release symbol churn. Independent of the changelog, so it can run any time before stage 6. Exists because the authored `repair_doc/*.json` files admit in their own `caveat` field that no true git-diff backed them; every claim here is attributable to a commit and re-checkable with `git show`. |
| 5 | `build_repair_docs.py` | free | `repair_doc/*_lib.json`, EC `theories/`, `changelog_index.json` | `repair_docs_index.json` | Condenses the authored per-library prose into a compact import-focused record, machine-checked against the real require/export structure, plus a tree-wide symbol index. Compaction is the point: `repair_hints.py` re-sends this block on *every* agent step, so it competes directly with premises for context. Never modifies the authored files. |
| 6 | `build_ec_migrations.py` | free | `library_history.json`, `changelog_index.json`, EC `theories/` | `ec_migrations.toml` | Emits rewrite rules applied to a `.ec` file *before* any proof is attempted — the syntax-porting step that §3.2 credits with ten of the eleven repairs. Library rules are derived from the mined history rather than prose; the headline case (125 declarations moving `SmtMap`→`FMap` in r2025.02) falls out of the diff without anyone having written it down. Engine/parser rules are curated. |
| 7 | `compute_exposure_score.py` | free | `changelog_index.json`, `eval/*` checkouts, EC clone | `exposure_results.json` | Scores each repository's exposure to breaking changes between its detected source version and a target. Detection is layered: explicit pin (submodule SHA, `EC_VERSION`, `easycrypt.project`, Docker tag, CI config) → git commit date → content-based bracketing. Also measures automation reliance and proof complexity. Deliberately never uses filesystem mtimes. **This is where §6.7's defects lived**; `--csv` skips repos already cloned, so it re-scores a populated `eval/` without touching the network. |
| 8 | `rank_repos.py` | free | `exposure_results.json` | `ladder.md` | Renders the ranked ladder, easiest to hardest, with the contributing factors per row so the ranking is not a black box. Repos that failed to clone are listed separately rather than dropped. Pure formatting — it makes no scoring decisions. |
| — | `retrieve_entries.py` | free | either changelog format | — | **Not a build stage: a runtime library.** `integration/agent/repair_hints.py` loads it by path with `importlib` at agent runtime to score changelog entries against a failing tactic. It normalises both the indexed and legacy formats so callers never branch on which is on disk. Editing it changes agent behaviour, not just an artifact. |
| — | `estimate_repair_difficulty.py` | free | one `.ec` file | stdout JSON | **Not part of the ladder** — nothing calls it. A per-*lemma* repair-time CLI, meant to be pointed at a specific broken lemma, combining depth, external-theory fan-out and automation reliance. It imports the block scanner and `count_tactics` from `compute_exposure_score.py` rather than duplicating them, so it inherits that module's parsing behaviour and cannot be where a parsing bug is fixed. |
| — | `download_corpus.sh` | network | `repositories.csv` | `../zip/*.zip` | Bulk-fetches each repo as a branch tarball via `curl` (GitHub and GitLab URL shapes; `Private` rows skipped). **Not the path that populated `eval/`**, and not interchangeable with it: it writes zips to `../zip/`, names them with underscores where `compute_exposure_score.py` sanitises to hyphens, and produces no `.git` directory — which silently disables the git-commit-date tier of version detection, leaving affected repos to fall through to the content-bracket heuristic. `compute_exposure_score.py --csv` is the supported path; it clones on demand and skips what already exists. |
| — | `repositories.csv` | — | — | — | Not a script: the 77 `Name,URL` rows defining the corpus. Two rows carry `Private` as their URL and are expected to fail to clone; they appear in the ladder's "not scored" table. |

**What the harness reads at runtime** is a much smaller set than what this
builds: `changelog_index.json`, `repair_docs_index.json`, `ec_migrations.toml`,
and `retrieve_entries.py` itself. `exposure_results.json` and `ladder.md` have
no programmatic consumer at all — they inform corpus selection by being read by
a person (see `proof_corpus/BENCHMARK_SELECTION_NOTES.md`). A wrong number in
the ladder therefore misleads the experimenter without corrupting any run,
which is why §6.7's defects survived as long as they did.

Stages 3, 5, 6, 7 and 8 all read the changelog. **Reclassifying (stage 2) means
rerunning all five, in that order.** Skipping stage 7 is the easy mistake: the
exposure scores are derived from the changelog, so a stale ladder silently
misranks which repositories are worth experimenting on. Re-mining git history
(stage 4) requires only stage 6.

Full commands are in `proof_corpus/README.md`.

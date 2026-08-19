# AI4EC harness — features and implementation

**Status:** partial. Sections marked *(stub)* are placeholders.
**Last updated:** 2026-08-10 · branch `shannon-llm-integration`

What this system does: take an EasyCrypt proof written against an old release,
replay it against a current build, and when it stops compiling, drive an LLM to
repair it. This document describes the machinery. For *what is broken and what
to do next*, see [`NEXT_IMPLEMENTATION_PLAN.md`](NEXT_IMPLEMENTATION_PLAN.md),
[`PROOF_REPAIR_NEXT_HANDOFF.md`](PROOF_REPAIR_NEXT_HANDOFF.md) and
[`PROPOSAL_QUALITY_IMPLEMENTATION.md`](PROPOSAL_QUALITY_IMPLEMENTATION.md).

---

## 0. Architecture

### 0.1 The central design decision: EasyCrypt is stateless here

Every other choice follows from this one. The harness does **not** hold a live
EasyCrypt session. It owns a **text file**, and asks a fresh `ec.exe` process
about it:

```
ec.exe llm -upto N file.ec        goal state after line N
ec.exe llm -lastgoals file.ec     validate; non-zero exit = failure
ec.exe llm -upto N -premises …    goal + ambient premise catalog
```

`llm` is a **fork-local** subcommand (added in `da4935c9`, 2026-04-11) and does
not exist upstream. shannon-prover, by contrast, runs a `ReplSessionManager`
over a persistent EasyCrypt process.

Four consequences run through everything below:

1. **The proof file *is* the state.** No session to desync, no snapshot to
   maintain, and rollback is deleting a line. §3.3's undo analysis and §8c.7's
   snapshot evaluation both rest on this.
2. **Every query re-replays the prefix**, so cost grows with proof length —
   ~1.5 s on a 741-line file. Still 100× cheaper than a model call (§7), which
   is what makes local probing viable (`NEXT_IMPLEMENTATION_PLAN` §1.2).
3. **The goal is only observable through a lossy printer.** `resolve_goal`
   returns `""` after a goal-unchanged tactic; three separate bugs came from
   reading that as "discharged" (§2.1).
4. **Version hopping is bounded by the fork**, not by EasyCrypt: 11 of 14
   release tags predate the `llm` command (§6.6).

### 0.2 Layers

```
notebooks/*.ipynb              operator surface; config, confirmation, results
  └─ experiment/runner.py      trial loop, mode dispatch, artifacts
       ├─ corpora/*.py         ProofCase: file, proof_start_line, tactic_lines
       ├─ agent/import_repair  make the file LOAD (syntax porting)   ← §1 callout
       ├─ repair_bootstrap     replay original tactics one at a time
       └─ agent/loop.py        the per-step agent loop
            ├─ easycrypt.py    the four EC invocations above
            ├─ proof_file.py   append / undo / bounds  (the state)
            ├─ prompt.py       assemble the per-step prompt
            │    ├─ ec_program.py   two-column dump → positioned statements
            │    ├─ ec_context.py   names in scope; what `move =>` can take
            │    ├─ ec_names.py     lemma-name resolution + suggestions
            │    └─ goal_diff.py    structural diff across one tactic
            ├─ llm.py          provider transport (lm_studio/deepseek/anthropic)
            ├─ embeddings.py   premise ranking — ALWAYS LM Studio
            ├─ repair_hints.py changelog evidence for the failing tactic
            └─ run_log.py      per-iteration + finish records
```

The split is drawn at **transport only** in `llm.py`: reply parsing, JSON
repair and tactic salvage are provider-independent. Embeddings never leave LM
Studio — neither hosted provider serves them.

### 0.3 The per-step cycle

```
resolve goal ──► rank premises ──► build prompt ──► model call ──► action
                                                                     │
   ┌─────────────────────────────────────────────────────────────────┤
   ▼                        ▼                      ▼                 ▼
 tactic                   undo                lookup/search       (parse fail)
   │                        │                      │                 │
 append + validate    pop from end          catalog query        format_error
   │                   (clamped, §3.3)            │                 │
 fail → roll back, enrich error                   └──► notes into next prompt
   │
 ok → confirm_noop (3 EC calls) → inert? remove + ban at this goal
   │
 accepted → state hash → stuck accounting → next step
```

Termination: `COMPLETE`, `MAX_STEPS`, `STUCK` (unproductive-iteration counter or
identical-failure limit), `LLM_ERROR`, `BUDGET_EXHAUSTED`.

### 0.4 Invariants the design maintains

| invariant | why it exists |
|---|---|
| The work file only grows at the insert point and shrinks from the end | makes it a stack, so undo is exact and snapshots are unnecessary (§3.3) |
| A failed tactic is rolled back before the next step | the model must never see a script it did not build |
| Bans are keyed to `(goal hash, tactic)`, never global | `wp.` being inert here says nothing about the next goal |
| `confirm_noop` fails safe | anything unexpected keeps the tactic; deleting a load-bearing one is unrecoverable |
| Multi-tactic undo is clamped at the replayed prefix | prefix deletion is one-way (§3.3) |
| Embeddings never gate correctness | they rank premises; a bad ranking costs quality, not soundness |

---

### 0.5 How the harness changed, and what each change bought

Every row is measured. "Solve rate" is proofs the **model** closed.

### Phase 1 — replay instead of reconstruct

`broken_formal` mode admitted every tactic and asked the model to rebuild the
proof from scratch. `replay_bootstrap` (§1) replays the original tactic by
tactic and hands over at the first failure.

**Bought:** the agent starts from a verified prefix instead of nothing. On
ElGamal that is 185 of 300 tactics already in place before the model is called.

### Phase 2 — stop the model wasting itself (runs E→H)

| change | measured effect |
|---|---|
| `confirm_noop` two-view + removal proof | inert tactics **retained in the script** fell 49.2% → 8.8%; catches 93.6% of inert steps |
| Hard-reject repeats of banned no-ops | 0 leaked repeats, was 7 per lemma |
| No-ops excluded from the stuck counter | trials stopped dying early (`G2_G3` 75 → 44 → 27 steps reversed) |
| Repair-first prompt | `rnd` fixation 19 → 1 on `G2_G3` |

**Bought:** proofs stopped accumulating garbage — one lemma had ended with 45
of its 58 lines a bare `wp.`. **Solve rate: unchanged.**

### Phase 3 — tell the model true things (2026-08-06/07)

| change | evidence |
|---|---|
| `ec_program.py` positions | line-counting **overstated** the `seq` maximum on 503 of 654 goals; one goal was reported `left: 15, right: 15` when the true counts were 13 and 12 |
| `seq` counts stated as a **ceiling**, not a range | 11 of 12 failed `seq` attempts were *inside* the counts — the range claim was false |
| `smt(a b)` not `smt(a, b)` | comma is a **parse error**; the prompt had been teaching it |
| `goal_diff.py` state diff | renders on 32% of accepted steps; `PROGRESS_DECOMPOSITION` stops a `seq` that triples subgoals reading as a regression |
| Changelog DF guard | `smt` tagged 3.07% of the catalog and retrieved unrelated chore commits; now pruned |

**Bought:** the prompt stopped asserting falsehoods. **Solve rate: unchanged.**

### Phase 4 — stop the harness lying to itself (2026-08-07/10)

The most valuable phase, because it invalidated the project's headline numbers.

| change | effect |
|---|---|
| `fully_replayed` requires `is_proof_complete` | **12 of 15 ElGamal proofs were being reported COMPLETE without closing.** `validate_file` exit 0 means the tactics *parsed*. `G2_bad_ub` had been a silent false COMPLETE in all ten prior runs |
| Comment stripping in the replay splitter | `(* … *)` was shredded into fake tactics: 19 of 33 Joy cases, plus `INDCPA_HEG_G1` 52 → 51. Every "replayed N/M" denominator was inflated |
| Undo clamp (`protected_prefix`) | see below |
| `net_tactics_vs_bootstrap` | the only metric that detected the agent dismantling its own prefix |
| Transport errors → `LlmProviderError` | one `Connection error.` had destroyed a 100-minute trial; now retried |
| `history_steps` 20 → 15, timeout 600 → 180 s | per-step latency stopped climbing (93→235 s plateau removed); tail 1207 s → 353 s |

**The undo clamp is the clearest single result:**

| | prefix survival |
|---|---|
| before (run `20260807T031032Z`) | `G2_G3` 5/13, `INDCPA_HEG_G1` 5/21, `G1_G2_eq` 1/18 |
| after (run `20260810T053405Z`) | `G2_G3` **13/13**, `G2_bad_ub` 14/15, `INDCPA_HEG_G1` 20/21 |

~153 removals requested, ~31 performed. And deletion is **one-way**: the agent
re-attempted a deleted tactic and it *failed*, because the state it applied in
was gone (§3.3).

**Bought:** the numbers became trustworthy. 11/15 proofs now genuinely close,
where the old count was inflated. **Solve rate: unchanged.**

### What thirteen runs say

| | |
|---|---|
| proofs closing (honest count) | 11/15 |
| closed **by the model** | **1** — `INDCPA_Security`, every run |
| hard lemmas attempted / closed | 12 / 0 |
| model calls in the last run | 197, for a **net of +1 tactic** |

Four phases of work made failures cheaper, better attributed, non-destructive
and honestly counted. **None of it moved the solve rate.** That is the finding
this document exists to record, and the reason
[`NEXT_IMPLEMENTATION_PLAN.md`](NEXT_IMPLEMENTATION_PLAN.md) puts *"find out
what closing `G2_G3` actually requires"* above every remaining code change.

### Per-lemma tactic accounting, run `20260810T053405Z`

| lemma | original | replayed | model Δ | final | outcome |
|---|---:|---:|---:|---:|---|
| `enc_stateless` | 1 | 1 | +0 | 1 | COMPLETE |
| `INDCPA_Sec` | 1 | 1 | +0 | 1 | COMPLETE |
| `INDCPA_Security` | 2 | 1 | **+1** | 2 | COMPLETE (model) |
| `log_gen` | 3 | 3 | +0 | 3 | COMPLETE |
| `gen_log` | 3 | 3 | +0 | 3 | COMPLETE |
| `grexpAll` | 5 | 5 | +0 | 5 | COMPLETE |
| `G1_G2` | 5 | 5 | +0 | 5 | COMPLETE |
| `RO_track_f_ll` | 8 | 8 | +0 | 8 | COMPLETE |
| `G2_bad_ub` | 15 | 15 | +2 | 17 | MAX_STEPS |
| `G3_true` | 16 | 16 | +0 | 16 | COMPLETE |
| `RO_LCDHAdv` | 17 | 17 | +0 | 17 | COMPLETE |
| `G1_G2_eq` | 85 | 18 | **−3** | 15 | STUCK |
| `G2_G3` | 30 | 13 | +1 | 14 | STUCK |
| `INDCPA_HEG_G1` | 51 | 21 | +0 | 21 | STUCK |
| `correctness` | 58 | 58 | +0 | 58 | COMPLETE |
| **total** | **300** | **185** | **+1** | **186** | |

The replay gap is where the difficulty lives: 115 of 300 tactics fail to
replay, and 114 of those are in just three lemmas — `G1_G2_eq` (67),
`INDCPA_HEG_G1` (30), `G2_G3` (17).

---

## 1. Shape of a run

```
notebooks/elgamal_repair_experiment.ipynb
  └─ integration/experiment/runner.py :: run_experiment(spec, config)
       └─ per trial:
            1. corpus loads the .ec case into a sandbox
            2. import_repair fixes load-time breakage      (agent/import_repair.py)
            3. repair_bootstrap replays the original proof (experiment/repair_bootstrap.py)
                 - stops at the first tactic that fails
                 - that tactic becomes `config.broken_tactic`
            4. run_agent drives the LLM from there         (agent/loop.py)
            5. artifacts written to output/experiments/<run>/trials/trial_NNN/
```

A trial that replays every original tactic never invokes the agent at all
(`fully_replayed: true`, zero LLM calls). **This is the common case — 93% of
COMPLETE trials across ten runs.**

> ### Step 2 is load-bearing for everything after it
>
> **The whole harness rests on being able to port a file's syntax forward to
> the target EasyCrypt before any proof repair is attempted.** Step 2 is not a
> convenience: if `import_repair` cannot make the file load, `llm -upto`
> returns nonzero, `repair_bootstrap` records `skip_reason="goal_unreachable"`,
> and the trial ends **before a single tactic is tried**. No replay, no
> changelog evidence, no agent. Steps 3–5 are unreachable.
>
> That dependency is easy to miss because it has never failed here — 94 trials,
> `resolved=True` every time, zero `goal_unreachable` skips. But that is a fact
> about this corpus, not about the harness. On ElGamal the entire porting job
> was one added theory:
>
> ```diff
> -require import AllCore Distr SmtMap DBool FSet.
> +require import AllCore Distr SmtMap DBool FSet FMap.
> ```
>
> and that single declaration change is what makes nine lemmas close (§8bb).
> Measured directly, 12 of the 15 ElGamal proofs do **not** close as shipped.
> So on this corpus the dominant repair is *syntax porting*, not proof repair —
> the agent's contribution sits on top of it.
>
> The porting vocabulary is fixed and narrow (`add_require`,
> `replace_require`, `remove_require`, `rename_symbol`, `replace_regex`,
> `add_pragma`). A corpus whose **module**, `clone`, `op`/`type` or `axiom`
> declarations drifted would exhaust those rules, fail to load, and be recorded
> as a skip rather than as a class of breakage nobody can repair. See §8bb for
> the proposed fix, which is to make that case *visible* before extending the
> vocabulary.

### 1.1 Trial artifacts

| file | contents |
|---|---|
| `original.ec` | the case as shipped |
| `agent_start.ec` | original with the whole proof body stripped — a template, **not** the agent's start state |
| `agent_work.agent.ec` | the file the agent actually mutates; contains the replayed prefix |
| `bootstrap_result.json` | `accepted_count`, `total_count`, `failed_tactic`, `fully_replayed` |
| `agent_log.json` | every iteration: goal, tactic, outcome, error, thinking, premises |
| `import_repair.json` | what import repair changed and why |
| `informal_proof.md` | the *remaining* original tactics, shown to the model as reference |
| `version_hop_input.ec` | prefix + failing tactic, snapshotted for version hopping |

> **Trap.** `agent_start.ec` is the stripped template. Probing it with
> `llm -lastgoals` shows the goal *before any tactic*, which is not where the
> agent stands. Use `agent_work.agent.ec`.

---

## 2. EasyCrypt interface (`agent/easycrypt.py`)

Everything goes through a **fork-local** `llm` subcommand:

| call | purpose |
|---|---|
| `ec.exe llm -upto N file.ec` | goal state after line N |
| `ec.exe llm -lastgoals file.ec` | validate; non-zero exit = failure |
| `ec.exe llm -upto N -premises file.ec` | goal + ambient premises |

`llm` was added to the fork in `da4935c9` (2026-04-11) and **does not exist in
any release tag before that** — see the handoff §6 for why version hopping is
blocked on this.

### 2.1 `resolve_goal` is lossy, and that has caused three bugs

After a tactic that leaves the goal unchanged, `resolve_goal` walks back past
each unchanged cursor and returns `""`. Callers that read `""` as "proof
discharged" are wrong. `loop.py::_current_goal` is the correct accessor — it
falls through to the raw `llm -upto` output.

Both `confirm_noop` and (since 2026-08-07) `_probe_compound_subgoal` use it.
Any new caller must.

---

## 3. The agent loop (`agent/loop.py`)

Per step: resolve goal → rank premises → build prompt → call model → apply
action → classify outcome.

### 3.1 Outcomes

| outcome | meaning |
|---|---|
| `accepted` | compiled and moved the goal |
| `no_op` | compiled and changed **nothing**; removed from the script and barred at this goal |
| `failed` | EasyCrypt rejected it; rolled back |
| `rejected` | refused before EasyCrypt — duplicate of a banned or inert tactic |
| `undone` | model asked to roll back |
| `llm_error` / `format_error` | provider or parse problem |

### 3.2 No-op detection (`confirm_noop`)

A tactic compiling is not the same as it doing anything. Three stages, all
required:

1. `resolve_goal` view unchanged, **and**
2. raw `llm -upto` view unchanged (each view mis-classifies a tactic the other
   gets right — `skip.` and `proc.` respectively), **and**
3. **removal proof** — take the line out, re-check; if the state is identical
   without it, it contributed nothing.

Fails safe: anything unexpected keeps the tactic. Costs ~3 extra EasyCrypt
calls (~1.5 s each) against a ~160 s model call.

**Measured effect:** inert tactics *retained in the script* fell from 49.2%
(pre-detection) to 8.8%, catching 93.6% of inert steps. The often-quoted "~50%
of proposals change nothing" is a statement about *proposals*, which is
unchanged — do not conflate the two, and never pool detection-era with
pre-detection runs, since the latter report zero no-ops by construction.

**Rejected alternative:** subgoal count as the primary signal. Text equality
implies count equality (0 counterexamples in 525 transitions), and a
count-first rule wrongly calls 113 of those 525 inert. See `goal_diff.py`.

### 3.3 The replayed prefix is protected from bulk undo

`repair_bootstrap` leaves the replayed original tactics in the working copy.
They **compile against the current build** — the one that broke is the next
one — so they are verified work, not the model's guesses. Nothing distinguished
them, and `undo_last_tactic` walked straight through them.

Measured on run `20260807T031032Z` before the fix:

| trial | undo actions | tactics removed | bootstrap → final |
|---|---:|---:|---|
| `G2_G3` | 10 | 37 | 13 → 12 |
| `INDCPA_HEG_G1` | 13 | 53 | 21 → 9 |
| `G1_G2_eq` | — | 12 **at step 2** | 18 → 6 |

`G1_G2_eq` is the clearest case: one `undo(count=12)` erased 12 of 18 replayed
tactics at step two, prompted by a single failed `smt(mem_rng_empty)`. Two of
the three trials finished holding *fewer* tactics than they were handed, and no
conventional counter showed it — accepted, failed and no-op all looked ordinary.

`ProofFile.protected_prefix` now clamps a **multi-tactic** undo at the prefix
boundary. It is not a floor: single-step undos are unrestricted, so a repair
that genuinely needs to reach back (a `seq` whose invariant is too weak
compiles and strands the proof later) still can — it just takes a sequence of
decisions rather than one number. The prompt states which tactics are verified
and that the clamp exists.

`run_log.finish` records `tactics_retained`, `replayed_prefix` and
`net_tactics_vs_bootstrap`. **A negative net is the signal**; it is the only
measure that caught this.

**Does the agent rebuild a prefix it undoes into? No — measured, it does not.**
That is the argument for the clamp, because "it can just redo them" would
otherwise make the whole thing unnecessary.

Measured by comparing the surviving script's head against the original,
tactic by tactic:

| run | trial | prefix | removals | head verbatim | outcome |
|---|---|---:|---:|---|---|
| **clamp ON** | `G2_G3` | 13 | 12 | **13/13** | intact |
| **clamp ON** | `G2_bad_ub` | 15 | 7 | **14/15** | lost 1: `auto.` → `progress.` |
| clamp OFF | `G2_G3` | 13 | 37 | 5/13 | lost 8 |
| clamp OFF | `INDCPA_HEG_G1` | 21 | 53 | 5/21 | lost 16 |
| clamp OFF | `G1_G2_eq` | 18 | 20 | 1/18 | lost 17 |

`G2_G3` under the clamp is the design working: 12 removals, all above the
boundary, so the agent churned its *own* additions and the original never
moved. `G2_bad_ub` shows the escape hatch — single-step undos are
unrestricted, it crossed by exactly one, and it did **not** restore the
tactic; it substituted `progress.` and carried on.

**It re-attempts the deleted tactic, and the re-attempt fails.** This is the
strongest argument for the clamp, and it corrects an earlier reading here that
said the tactic was simply never tried again — byte-exact comparison
understates it, because the model rewrites spacing. Compared
whitespace-insensitively:

| trial (clamp OFF) | deleted | re-attempted at | result |
|---|---|---|---|
| `G2_G3` | `call(_ : ={RO_track.bad_grp,RO_track.mp}).` | step 33 | **failed** — `the conclusion is not a hoare or an equiv` |
| `INDCPA_HEG_G1` | `sp.` | steps 23, 38, 64 | — |
| `G1_G2_eq` | `inline*.` | step 10 | — |

`G2_G3` also made **16** near-miss `call` attempts around the deleted one (9
accepted, 7 not), spread from step 14 to step 67.

So deleting a prefix tactic does not merely lose the line — it destroys the
proof *state* in which that line applied. Putting the same tactic back later
fails, because the surrounding context is gone. The clamp is therefore not
preventing a recoverable mistake; the mistake is genuinely unrecoverable, and
the agent burns tens of steps discovering that.

> **Measurement traps, both hit while producing this table.**
> 1. Compare tactics using the replay's own rule — strip `(* … *)` comments,
>    then split on `.<whitespace>` — never on physical lines. A single
>    `seq 5 5 : (…)` wraps across three lines in the original and is one line
>    in the work copy, so a line-by-line diff reported `G2_bad_ub` as losing 12
>    of 15 when it lost 1.
> 2. Compare tactic *text* whitespace-insensitively. `call(_ : ={a,b}).` and
>    `call (_: ={a, b}).` are the same tactic; byte equality reported zero
>    re-attempts where there was one.
>
> The corrected script is
> `scratchpad/prefix_survival.py`.

> **Measurement trap.** Compare tactics using the replay's own rule — strip
> `(* … *)` comments, then split on `.<whitespace>` — never on physical lines.
> A single `seq 5 5 : (…)` wraps across three lines in the original and is one
> line in the work copy, so a line-by-line diff reported `G2_bad_ub` as having
> lost 12 of 15 when it had lost 1. The first run of this very comparison got
> it wrong that way.

### 3.4 Stuck accounting

`stuck_counter` increments on failures, rejections and repeated proof states —
**not** on no-ops. Coupling "this step was wasted" to "this trial is going
nowhere" made trials die progressively earlier (75 → 44 → 27 steps on `G2_G3`)
while the inert rate stayed flat. Provider failures are counted separately
(`max_consecutive_provider_failures`), so infrastructure trouble is never read
as the model floundering.

---

## 4. Prompt construction (`agent/prompt.py`)

Assembled per step. Notable sections:

- **Current goal**, scoped to the *active* subgoal (`active_goal_text`). 90% of
  prompts carry more than one open goal and 70% of the text is inactive.
- **State diff** — what the last accepted tactic structurally did
  (`goal_diff.py`). Renders on ~32% of accepted steps.
- **Repair-first block** — the broken original tactic, stated as the task, on
  step 1 only.
- **Goal-shape hints** — program-logic vs ambient, `seq` bounds, first/last
  instruction per side, matched-call cut points.
- **Ban lists** — tactics that failed, and tactics proven inert, both keyed to
  the *goal hash* so the bar lifts when the goal moves.
- **Changelog hints** — release notes relevant to the failing tactic, subject
  to a document-frequency guard (§6.2).

### 4.1 Goal classification, and where it fails

`goal_looks_program_logic` is a syntactic form detector and is accurate as one:

| ground truth | n | says "program-logic" |
|---|---:|---|
| program-logic | 381 | **99.7%** ✓ |
| ambient | 28 | 28.6% ✗ |
| **discharged judgment still printing `pre`/`post`** | 67 | **91.0%** ✗ |

The third row is the open 24% "wrong logic class" bucket. Best known
discriminator: no instruction index column **and** 2 open goals → 57.7%
precision at 44.8% recall (base rate 11.8%). Shipped as a hedge, not a rule.

### 4.2 How `smt` is actually used — the reverse of the obvious guess

Measured over every `agent_log.json` in `integration/output/experiments/`:
124 tactics invoke `smt`.

| form | goal | n | accepted | failed | no-op |
|---|---|---:|---:|---:|---:|
| bare `smt()` | program-logic | 5 | 1 | 4 | 0 |
| bare `smt()` | **ambient** | 27 | **0** | 25 | 0 |
| compound (`…; smt()`) | program-logic | 89 | **19** | 51 | 11 |
| compound | ambient | 3 | 0 | 3 | 0 |

**76% of `smt` use is aimed at program-logic goals, and that is where every
success comes from.** The productive idiom is a compound that reduces the
judgment first and then calls the solver (`wp; skip; smt().`). It is not
misuse — it is the agent's main working pattern.

**Bare `smt()` at an ambient goal is 0 for 27.** The ambient residuals this
corpus produces are not the arithmetic `smt` closes easily: 16 of 30 carry a
quantifier, 12 have more than three top-level connectives, 3 contain `Pr[…]`.
26 of the 28 failures are `cannot prove goal (strict)` — the solver reaching
its limit, not a syntax error.

**Compounds mostly fail before `smt` runs.** Of 55 failed `…; smt()`
compounds, **40 died in the first segment** — 24 `invalid last instruction`
(the `rnd`) and 16 `left instruction list is not empty` (the `skip`). Only 7
reached the solver and lost there. So the error text on a failed compound is
usually about position, not about `smt`.

Both facts are now stated in the prompt (`format_active_goal_shape_hints`
ambient branch, and rung 3 of the repair ladder).

> **Measurement trap, hit while producing this table.** EasyCrypt prints
> file-level `[warning]` lines *before* the `[critical]` one, so taking
> `error.splitlines()[0]` attributes the failure to a warning. A first pass
> here reported "9× global axiom Adv_choose_ll in section" as an `smt` failure
> mode; it does not exist. Always apply `ec_errors.strip_warning_lines` first —
> the loop does, so the model never saw the wrong text, but the analysis did.

### 4.3 Names in scope (`agent/ec_context.py`, `agent/ec_names.py`)

16 of 426 measured failures are name/scope errors, and **10 of those are
`an hypothesis or variable named 'X' already exists`** — the model
re-introducing `&1`/`&2` or a hypothesis the goal's own context block already
lists. Nothing needs fetching: EasyCrypt prints every name in scope above the
dashed rule. The model was shown it and did not act on it.

`ec_context.parse_context` reads that block into typed entries (memory,
variable, hypothesis) and `format_context_note` states them, naming the exact
move that would fail (`move => &1 &2`) and the namespace rule below.

**The namespace rule.** On `G2_bad_ub` the model wrote `smt(hpre)` and got
``cannot find lemma `hpre'`` — while `hpre` was printed in the context with
its full statement. `smt(...)` takes *library lemma* names; a local hypothesis
is already part of the goal the solver sees. The error text does not
distinguish "no such name" from "that name is not a lemma", and those need
opposite responses.

**`ec_names` never blocks a tactic**, which is measured rather than cautious.
The Ax.all catalog has false negatives: `rpow_hmono` is absent from it at
`G2_bad_ub`'s cursor yet is part of that lemma's own original proof and
replays successfully. Bare basenames are the same — the catalog is keyed by
qualified path, so `addr0`, `subzz`, `mem_empty`, `dtext_ll` are all absent as
keys while being usable. **A rejecting pre-check would have blocked working
tactics**, so this only makes a failure informative:

| situation | response |
|---|---|
| name is in the goal's context | explain the kind; never search for it |
| name absent AND the error is a name error | nearest catalog entries by basename |
| name absent, error is anything else | **silent** — the catalog may be wrong |
| no catalog available | **silent** — no basis for any claim |

That third row is the important guard. Ungated, `apply rpow_hmono.` — a
tactic that works — would be told it used a bad name. A prompt that states
something false gets believed; the `smt(a, b)` comma below is the proof.

Cost is a handful of dict lookups: a tactic referencing a lemma-position name
references a median of 1 (max 2) across every run, so there is nothing
exhaustive about it. Retrieval of *relevant* lemmas is a separate mechanism
that already exists — `top_premises`, cosine over the Ax.all catalog, `top_k`
10 — and is untouched by this.

**What `move =>` can introduce** (`ec_context.format_introduction_note`). The
names-in-scope note above was not sufficient: on `G2_bad_ub`, 8 of 19 failures
were `move =>` errors and the context note stayed silent on 6 of them.

My first diagnosis was that `progress.`/`split` fanned the goal into branches
where `&1` was bound in some and not others, so a static snapshot could not
track it. **That was wrong** — every one of the eight had a *single* subgoal.
The two real causes are plain shape facts about the conclusion:

| cause | n | what the model wrote | EasyCrypt said |
|---|---:|---|---|
| memories read as binders | 4 | `move => &1 &2 hpre` against `forall &1 &2, …` | `'&1' already exists` |
| no leading binder at all | 4 | `move => hpre` against `(glob Adv){1} = …` | `nothing to introduce` |

`&1`/`&2` are **memories**. A pRHL conclusion can literally read
`forall &1 &2, …` while those names are already bound, so they are not
introducible — and because they appear in the *conclusion* rather than as
context entries, the names-in-scope check never saw them.

`leading_binders` parses the conclusion's binder chain, marks memories, and
`format_introduction_note` states what is introducible, what must be skipped,
or that `move =>` will fail outright. **Replayed against the eight recorded
failures: 8/8 would have been warned.**

Two parsing traps, both caught by tests: `forall (x : int) h,` binds `x` and
`h`, not `int` (splitting the binder list on whitespace reported the type as a
name); and `_goal_conclusion` matches EasyCrypt's 72-dash separator *exactly*,
so a shorter stand-in in a test silently yields the whole dump as the
"conclusion".

**`smt` argument syntax.** `smt(a, b)` is a PARSE ERROR; the separator is a
space. Verified against the binary: comma → `parse error`, space → proceeds to
lemma lookup. Two of five failures on `G2_bad_ub` were this, **and the prompt
was teaching it** — the ambient advice added in §4.2's work wrote
`smt(Lemma1, Lemma2)`. Corrected in `prompt.py` and in `loop.py`'s
`cannot prove goal (strict)` hint, which now says a comma is a parse error
rather than a failed proof.

---

## 5. Program structure (`agent/ec_program.py`)

Parses EasyCrypt's two-column dump into positioned statements. The index column
is shared between the programs:

```
x <- pubk0 ^ r            ( 9--)  if (g ^ (q1*q2) \notin RO.mp) {
                          ( 9.1)    y <$ dtext
                          (    )      RO.mp.[g ^ (q1*q2) <- y]
if (x \notin RO.mp) {     (10--)  u <- oget RO.mp.[g ^ (q1*q2)]
```

`( N--)` / `( N)` opens top-level statement N; `( N.k)` is nested; `(    )` is a
wrapped line. The column is located by **majority vote across rows**, because
program text carries its own parens (`(pubk, privk)`) and a no-argument call
`M.f()` would otherwise vote on every row.

Provides `parse_program_block`, `seq_candidates` (matched-call cut points,
ported from shannon-prover's `_compute_seq_suggestions`), and
`common_prefix_length`.

**Why it exists:** the prompt previously counted instruction-shaped *lines*,
which counts a statement's nested body as more statements. On 503 of 654 real
indexed goals that **overstated** the maximum, every time it differed — one
goal was reported as `left: 15, right: 15` when the true counts were 13 and 12.

**Known limitation.** Correct counts are *necessary but not sufficient*: 11 of
12 failed `seq` attempts on `INDCPA_HEG_G1` used indices inside the counts and
were rejected anyway, twice with EasyCrypt naming a far smaller limit
(`invalid split index: ^<5` against a count of 13). The prompt therefore states
the counts as a **ceiling**, not an admissible range, and points at EasyCrypt's
`^<K` as the authoritative limit.

---

## 6. Supporting modules

### 6.1 Import repair (`agent/import_repair.py`) *(stub)*
Verified, line-preserving repair of load-time breakage. 12/12 resolved on
ElGamal, zero pre-proof errors remaining. Off by default outside experiments.

### 6.2 Changelog retrieval (`agent/repair_hints.py`)
Release notes keyed to the failing tactic, with a **document-frequency guard**:
any tactic name whose bucket exceeds 2% of the catalog is dropped before
retrieval, and if that leaves nothing the prompt carries no changelog section.
On the shipped catalog (913 entries) this prunes exactly `smt` (3.07%),
`rewrite` (2.96%), `simplify` (2.63%) and `proc` (2.52%); the next bucket is
`rnd` at 1.31%. Matching on `smt` had been retrieving unrelated chore commits,
and `hint_uptake` was 0.0.

### 6.3 Premise retrieval (`agent/embeddings.py`)
Cosine ranking over EasyCrypt's `Ax.all` catalog. **Always LM Studio**,
regardless of chat provider — neither DeepSeek nor Anthropic serves embeddings,
and `EmbeddingClient` hard-wires `lm_studio_base_url`. Called far more often
than the model, and free.

### 6.4 Providers (`agent/llm.py`, `agent/config.py`)
`lm_studio` (local, OpenAI-compatible), `deepseek`, `anthropic`. The split is
drawn at *transport only*; all reply parsing is shared. Anthropic is not a
base-URL swap — different client, message shape, and thinking parameters.

**Thinking modes:** `disabled`, `enabled`, `adaptive` (harness heuristic: on
only after a recent failure), and `high_unless_stuck` (added 2026-08-07:
effort `high` while converging, released once ≥4 of the last 6 steps were
unproductive). Anthropic's `adaptive` is passed through untouched — Claude
scales depth itself, better than a trajectory window can.

**Transport errors** (`APIConnectionError`, `APITimeoutError`, `RateLimitError`,
`InternalServerError`) are re-raised as `LlmProviderError` so they go through
the retry bound. Auth and 400-class errors deliberately still fail fast.

### 6.5 Budget (`agent/budget.py`)
USD cap checked **before** each call, so spend overshoots by at most one call
in flight. Exits `BUDGET_EXHAUSTED`, reported separately from failure.

### 6.6 Version hopping (`experiment/ec_versions.py`) *(stub)*
Builds per-release EasyCrypt binaries in git worktrees + opam switches. Works
(r2025.02 provisioned in ~5 min, 38 MB binary, compiles real files) but the
binaries lack the fork's `llm` subcommand for 11 of 14 tags. See handoff §6.

---

## 7. Performance characteristics

Measured on run `20260806T194914Z` (`deepseek-v4-flash`, adaptive thinking):

| operation | cost |
|---|---|
| EasyCrypt goal probe, 741-line file | **~1.5 s** |
| one model call | **~160 s** (93 s early, ~235 s once the history window fills) |
| **ratio** | **~100:1** |

Per-step latency rises with accumulated prompt context and **plateaus** where
`history_steps` fills — it is not throttling (no 429s; two trials two hours
apart trace the same curve against step number). `history_steps` is the lever;
cut 20 → 15.

Across all runs, 358 of 1024 steps were wasted (`no_op` or `failed`) = 15.9 h of
model time; the same tactics probed locally would be 8.9 min. This asymmetry is
the basis of the probing proposal in the companion document.

---

## 8. Testing

`.venv/bin/python -m pytest integration/tests -q` — 488 passed, 1 skipped.
Tests marked `integration` exercise a real EasyCrypt binary.

Invariants worth knowing before changing anything:

- `test_goal_text_equality_is_not_proof_of_inertness` — `skip.` renders
  byte-identical and is load-bearing. Any widening of `confirm_noop` must
  survive it.
- `test_subgoal_count_does_not_rescue_the_skip_case` — pins why subgoal count
  is not the inertness signal.
- `test_the_prompt_states_the_counts_as_a_ceiling_not_a_range` — pins that the
  `seq` counts are not promised to be admissible.
- Fixtures containing goal text are **copied verbatim** from run logs, never
  retyped: the column offsets are the data, and a hand-transcribed copy parsed
  as 12/11 instead of 13/12.

---

## 8b. A worked example: what `G2_bad_ub` actually leaves open

The most instructive single trial, because it exposes three separate problems
at once. Run `20260807T151318Z`, trial 010.

**It was reported COMPLETE in all ten previous runs, and never closed.** The
bootstrap replayed all 19 original tactics, saw `validate_file` exit 0, and
returned `COMPLETE, steps=0, calls=0`. Exit 0 from `llm -lastgoals` means the
tactics *parsed*. Once `fully_replayed` also required `is_proof_complete`
(§3.3), the agent ran for the first time — with a 15/15 verified prefix.

This is not one lemma. **12 of the 15 ElGamal proofs do not close**; only
`log_gen`, `gen_log` and `grexpAll` do. Every "free replay COMPLETE" for the
other twelve was a false success, which means the headline counts in §9 and in
the handoff are inflated and should be rebuilt from a post-fix run.

### What is left open

One pRHL judgment, ~2400 characters, the residual of the `call(_: …)` at the
original script's lines 681–682:

* **pre** — the invariant the `seq 5 5` established: `q1{1}=q1{2}`, the two
  random-oracle maps equal, `bad_grp{1} = g^(q1*q2)`, and `badHappened{1}`
  characterised as membership in the map's domain.
* **post** — that invariant restated, conjoined with a large `forall` over
  `result_L/result_R`, both `glob Adv`s, and the maps, unfolding into nested
  quantifiers over `choiceL/choiceR ∈ {0,1}` and `yL/yR ∈ dtext` whose
  conjuncts are largely reflexive (`choiceR = choiceR`,
  `mu1 dtext yR = mu1 dtext yR`).

**It is an ambient goal wearing program-logic clothes.** `skip.` fails here
with `expecting a goal of the form: hoare[S], ehoare[S], phoare[S], equiv[S]`
— the judgment is discharged, and what remains is a quantified implication.
This is exactly the 24% wrong-logic-class bucket of §4.1, caught in the act.

### Three distinct failures, only one of them about proof search

Of the agent's first 15 steps: 4 no-ops, 5 failures.

| step | tactic | error | class |
|---|---|---|---|
| 6, 8 | `smt(Top.grexpA, Top.inj)` | `parse error` | **syntax** |
| 13 | `apply/andP` | ``unknown lemma `andP'`` | **hallucinated name** |
| 11 | `smt(Top.grexpA Top.inj)` | `cannot prove goal (strict)` | **real solver limit** |

1. **`smt(a, b)` is a parse error.** EasyCrypt separates hint lemmas with a
   SPACE. Verified directly against the binary: comma → `parse error`, space →
   proceeds to lemma lookup. Two of the five failures were this, and *the
   prompt was teaching it* — an earlier revision of the ambient advice in this
   very document's §4.2 work wrote `smt(Lemma1, Lemma2)`. Fixed in
   `prompt.py` and in `loop.py`'s `cannot prove goal (strict)` hint, which now
   says a comma is a parse error rather than a failed proof.
2. **`andP` does not exist** in this library — an ssreflect name. Nothing in
   the harness checks a lemma name before it is used, though `lookup_symbol`
   exists to.
3. **Only step 11 is a genuine proof-search failure**, and only after the
   syntax was right.

### Would better `smt` lemmas fix it?

Partly, and not first. The ordering matters:

* The syntax and name errors cost 3 of 5 failures and are free to fix.
* With correct syntax `smt` still returned `cannot prove goal (strict)`, so at
  *that* point better or additional lemmas are the right lever.
* But the goal is ~2400 characters of nested quantifiers, and `smt` scales
  badly in goal size. `progress.` being a **no-op** here (step 4) is the
  telling detail — the usual decomposition did nothing, so the residual needs
  to be broken up another way (`move => &1 &2` to introduce, then `split`, then
  targeted `smt` per conjunct) before lemma selection becomes the binding
  constraint.

This matches §4.2's corpus-wide finding: bare `smt` on an ambient residual is
0-for-27, and the successes come from *reducing first, then* calling the
solver.

---

## 8bb. What actually solved each lemma, and what import repair is doing

Run `20260807T151318Z`, first ten trials — the first ElGamal run with the
`fully_replayed` fix, so `COMPLETE` finally means the proof closes.

| trial | lemma | reason | LLM calls | bootstrap | fully |
|---|---|---|---:|---|---|
| 0 | `enc_stateless` | COMPLETE | **0** | 1/1 | True |
| 1 | `INDCPA_Sec` | COMPLETE | **0** | 1/1 | True |
| 2 | `INDCPA_Security` | COMPLETE | **4** | **1/2** | False |
| 3–9 | `log_gen`, `gen_log`, `grexpAll`, `RO_track_f_ll`, `G1_G2`, `G3_true`, `RO_LCDHAdv` | COMPLETE | **0** | full | True |

**Only `INDCPA_Security` was repaired by the model.** Its bootstrap is 1/2 —
the second tactic genuinely broke — and four calls fixed it. Every other trial
is a zero-call replay.

**The one- and two-line lemmas did NOT get solved by the agent.**
`enc_stateless` and `INDCPA_Sec` are 1 tactic each, replay 1/1, and complete
with zero calls. They compile and close *after import repair* — nothing was
proved by the model.

### The repair that mattered was not a proof repair

Those lemmas do **not** close in the corpus as shipped: measured directly,
12 of the 15 ElGamal proofs fail `is_proof_complete` on the original file.
They close in the run because import repair changed a **declaration**:

```diff
-require import AllCore Distr SmtMap DBool FSet.
+require import AllCore Distr SmtMap DBool FSet FMap.
```

One added theory, and nine lemmas go from not-closing to closing with no
tactic touched. So on this corpus the dominant repair mechanism is *non-proof*:
`import_repair` resolved every file (94 trials, `resolved=True`, and **no trial
in any run was ever skipped `goal_unreachable`**).

### The gap: non-proof breakage beyond imports

`import_repair` is the only component that repairs anything outside a proof
body, and its vocabulary is fixed —
`add_require`, `replace_require`, `remove_require`, `rename_symbol`,
`replace_regex`, `add_pragma` (`LINE_PRESERVING_OPS`).

Everything else in a `.ec` file is **out of scope for the whole harness**:

* `module` / `module type` declarations whose syntax or signature changed
* `clone` / `clone import` with renamed or re-typed parameters
* `op` and `type` declarations
* `axiom` / `pred` statements
* section and `declare` structure

`rename_symbol` and `replace_regex` can paper over a simple rename, but nothing
handles a *structural* change, and nothing detects one: the agent loop only
ever appends tactics after `proof.`, so a broken module declaration cannot be
repaired by it even in principle.

**Why this has not bitten yet, and why that is not reassurance.** On this
corpus every load failure was an import, so `resolved=True` everywhere and
zero `goal_unreachable` skips. That is a property of the corpus, not of the
harness. A proof whose *module* definition drifted would fail to load, exhaust
the manifest's rules, and be skipped as `goal_unreachable` with no repair path
— and the trial would be recorded as a skip rather than as a class of breakage
nobody can fix.

**Proposed fix.** Two steps, cheapest first:

1. **Make the gap visible.** When import repair leaves a file unloadable,
   classify the residual error (`ec_errors` already distinguishes pre-proof
   from in-proof) and record *which declaration kind* failed, rather than the
   flat `goal_unreachable`. Costs nothing and turns an invisible class into a
   counted one.
2. **Only then** consider extending the repair vocabulary. Adding
   module/clone rewriting to the manifest is real work and is unjustified
   until step 1 shows it happening. Note that the same evidence-based
   discipline already in `import_repair` applies — every edit verified against
   EasyCrypt and rolled back if it does not measurably improve how far the
   file loads.

---

## 8c. Open problems, with proposed fixes

Ordered by (evidence it matters) × (confidence the fix works). Each says what
to build, what it costs, and how to know it worked. Speculative ones are
marked; do not treat them as equal to the measured items.

### 8c.1 `resolve_goal`'s lossy return is a permanent footgun — CHEAP, CERTAIN

§2.1 records three bugs from callers reading `""` as "discharged". Two are
fixed and the doc says "any new caller must" use `_current_goal` — which is a
comment, not a mechanism, and the third bug arrived *after* the first two were
documented.

**Fix.** A lint test that fails on any direct `resolve_goal(` call outside
`easycrypt.py` and `loop.py::_current_goal`, with an allowlist. ~15 lines, no
runtime cost, and it converts a recurring class of bug into a build failure.
Stronger variant: rename to `resolve_goal_lossy` so the hazard is at every
call site. Implemented as `test_no_new_lossy_goal_callers` — see §8.

**Validation.** Introduce a direct call in a scratch branch; the test fails.

### 8c.2 `seq` positions: use EasyCrypt's own limit — **DONE**

§5's limitation: the counts are a ceiling, not an admissible range, and 11 of
12 failed `seq` attempts were *inside* the ceiling. But EasyCrypt names the
real limit — `invalid split index: ^<5` — and we only tell the model to read
it, reactively, after it has already guessed.

**Fix.** Parse K out of that error and carry it on the per-goal error history,
then have `_seq_position_bullets` state `N < K` instead of `N ≤ len(left)`
once K is known. Two small pieces: a `_split_index_limit(error) -> int | None`
regex, and a field on the goal-keyed error record.

**Cost.** No EasyCrypt calls; it reuses information already being thrown away.

**Shipped.** `ec_program.split_index_limit` parses K; `loop.run_agent` records
it per goal hash and feeds it back through `build_prompt(split_limit=…)`. Once
K is known the prompt states *"N must be strictly less than K — i.e. at most
K-1, not 13"* instead of restating the ceiling the model has already been
rejected inside of.

**Still to validate on a run.** Count `invalid split index` failures per trial
before and after.

### 8c.3 Wrong logic class: ask EasyCrypt instead of guessing — **DONE (probe built, not yet consumed)**

§4.1's 24% bucket. The best text-based discriminator is 57.7% precision, and
both our classifier and shannon-prover's get these wrong, because *the printed
form genuinely does not distinguish them* — a discharged judgment still prints
`pre`/`post`.

**Fix.** Stop inferring it. Probe on a scratch copy: append a tactic that can
only succeed on a live program-logic goal (`wp.` or `skip.`) and read the
return code. ~1.5 s against a ~170 s model call, and it is **decisive** rather
than 57.7% — the same economics as §3's local probing in the companion doc.

**Risk.** A probe tactic that fails for an unrelated reason reads as "ambient".
Mitigate by treating only the specific `expecting a goal of the form` error as
the ambient signal, not any failure.

**Shipped.** `easycrypt.probe_is_program_logic` appends `wp.`, reads the
return code, and restores the file byte-identically. Verified on both known
cases: a live program-logic goal → `True`; the same proof after `skip.`
discharges the judgment → `False`. Returns **`None`** when inconclusive —
`wp.` fails on plenty of live program-logic goals, so only the specific
`expecting a goal of the form` error counts as ambient, and callers must fall
back to the text heuristic rather than assume.

**Not yet wired into the prompt.** It costs ~1.5 s per step, so it should
probably run only when the text heuristic is in its unreliable band (the
57.7% hedge in §4.1) rather than unconditionally. Re-label the 568-step corpus
with it before deciding.

### 8c.4 Version hopping was blocked on the wrong question — **DONE**

§6.6: 11 of 14 release tags lack the fork's `llm` subcommand, so hopping cannot
fetch goals from them. Grafting `da4935c9` onto each release conflicts in four
files and needs a build per tag (~5 min each).

**Answered, and it deleted the problem.** `version_hop.probe_version` calls
**only** `validate_file` — it never fetches a goal. That is the single
question *"does this file still check out at release R"*, and upstream
`compile` answers it at every release.

Verified against both binaries:

| | `llm -lastgoals` | `compile` |
|---|---|---|
| passing proof, modern build | rc 0 | rc 0 |
| failing proof, modern build | rc 1 | rc 1 |
| passing proof, **r2025.02** | rc 1 (*unknown option*) | **rc 0** |
| failing proof, **r2025.02** | — | **rc 1** |

`easycrypt.check_file_compat` runs `compile`, and `probe_version` now uses it.
**All 14 tags are usable**; no cherry-picking, no per-release conflict
resolution. The agent loop keeps `validate_file`, whose stdout it also reads
for goal text.

### 8c.5 Large ambient residuals defeat both `smt` and `progress` — SPECULATIVE

§8b: the goal is ~2400 characters of nested quantifiers whose conjuncts are
largely reflexive; `smt` returns `cannot prove goal (strict)` and `progress.`
is a **no-op**, so the usual decomposition does nothing.

**Possible fix**, untested: detect an ambient goal above a size threshold with
a top-level `forall`/`=>` chain, and advise the explicit ladder — `move => …`
to introduce, `split` per conjunct, then targeted `smt` on each leaf — rather
than the current generic "reduce first" advice. The parsing is available
(`goal_diff` already counts top-level connectives and quantifiers).

**Why it is speculative.** One lemma, one run. It may simply be that this
obligation needs a lemma the corpus does not contain, in which case no
decomposition advice helps. Measure how many ambient failures share the shape
before building anything.

### 8c.6 Proposal quality is still the binding constraint — SEE COMPANION DOC

Everything above makes failures cheaper or more informative. None of it makes
the model propose better tactics, which is the constraint that has not moved
across eleven runs. `docs/PROPOSAL_QUALITY_IMPLEMENTATION.md` §3 (local
probing) and §9.1 (find out what closing `G2_G3` actually requires) remain the
higher-value work.

### 8c.7 Host suspend is indistinguishable from a hang — TOOLING, NOT HARNESS

Three runs died to the laptop sleeping, and the stall watcher reported one of
them 216 minutes late because it slept too. A monitor cannot detect a suspend
it was suspended for.

**Fix.** Record `time.monotonic()` alongside the wall clock in each iteration
event. A gap where wall-clock advanced but monotonic did not is a suspend; the
reverse is a hang. One field, and it makes every future post-mortem decidable
instead of inferred.

---

## 9. Results to date

> **These figures are INFLATED and are kept only as a record of what the
> harness used to report.** They count `fully_replayed` trials as successes,
> and §8b shows 12 of the 15 ElGamal proofs do not actually close. Rebuild
> this section from a run made after the `is_proof_complete` fix.

**As previously reported:** across ten runs, 100 COMPLETE trials, of which 93
required zero agent work; the model had solved only `INDCPA_Security`.

**What that missed:** most of those 93 "free replays" were proofs that parse
but leave goals open. Only `log_gen`, `gen_log` and `grexpAll` genuinely close
on replay. So the corpus is far less solved than the numbers said, and
correspondingly there is far more real repair work available than the "1 of 3
repairable lemmas" framing suggested.

**Repairs the model has genuinely produced**, both verified to close:

| lemma | corpus | cost |
|---|---|---|
| `INDCPA_Security` | ElGamal | 1–10 steps depending on run |
| `sampling_bound` | LQ1 | 1 step, $0.0008 |

The second was unreachable until the `fully_replayed` fix, which is the reason
to expect more once the corpus is re-measured honestly.

*(stub — rebuild the table from a post-fix full run.)*

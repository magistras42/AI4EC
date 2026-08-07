# AI4EC harness — features and implementation

**Status:** partial. Sections marked *(stub)* are placeholders.
**Last updated:** 2026-08-07 · branch `shannon-llm-integration`

What this system does: take an EasyCrypt proof written against an old release,
replay it against a current build, and when it stops compiling, drive an LLM to
repair it. This document describes the machinery. For *what is broken and what
to do next*, see [`PROOF_REPAIR_NEXT_HANDOFF.md`](PROOF_REPAIR_NEXT_HANDOFF.md)
and [`PROPOSAL_QUALITY_IMPLEMENTATION.md`](PROPOSAL_QUALITY_IMPLEMENTATION.md).

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

### 3.3 Stuck accounting

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

## 9. Results to date

**The agent has repaired one distinct lemma, ever.** Across ten runs, 100
COMPLETE trials, of which 93 required zero agent work. Only three lemmas fail
to replay; the model has solved `INDCPA_Security` (a 2-line proof) every time
and neither of the other two, ever — through every configuration change.

Read any "N complete of 15" headline with that in mind: it mostly reports that
EasyCrypt still compiles the corpus.

*(stub — expand with per-lemma detail and the §9.1 investigation once run.)*

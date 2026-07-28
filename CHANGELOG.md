# Changelog

All notable changes to this project are recorded here, grouped by the date they were implemented.

## 2026-07-28

### Broken-proof prompt strategy, judgment-type hints, adaptive steps, and experiment notebook

Analysis of `run-20260727T162303Z` (ElGamal `elgamal-broken-repair`, 3/4 trials
failed at MAX_STEPS) revealed three systemic issues: the prompt actively
discouraged following the broken proof, the agent couldn't identify hoare vs
phoare vs equiv judgments, and hard proofs got the same step budget as trivial
ones.

#### Prompt — broken proof as primary strategy (`prompt.py`)

- **Heading changed** from "reference only — do not paste it verbatim" to
  "your primary strategy — follow it step-by-step as closely as possible".
  Adds guidance that the proof *used to compile* on an older EasyCrypt version
  and only minor syntax/API drift needs repair.
- This directly addresses the observed pattern where the agent treated the
  broken proof as a vague hint instead of a near-solution requiring surgical
  fixes.

#### Prompt — judgment type detection (`prompt.py`)

- New `_detect_judgment_type()` helper classifies goals as hoare, phoare, or
  equiv from structural markers:
  - `Bound :` field → phoare (with guidance to use `rnd` for samplings)
  - `~` operator, `{1}`/`{2}` qualifiers, `={` sugar → equiv
  - `pre =` / `post =` without the above → hoare
- Active goal-shape hints now include a **Judgment type** bullet (e.g.
  "Judgment type: **phoare (Bound field present — use `rnd` for samplings,
  not `wp`)**").
- Addresses trial 3 failure where the agent applied `wp` to a phoare goal
  and trial 2 confusion about whether bodies were open.

#### Experiment runner — adaptive step limits (`config.py`, `runner.py`)

- `ExperimentConfig.adaptive_steps_multiplier: float | None` — when set,
  per-trial `max_steps = max(min_adaptive_steps, int(multiplier × proof_lines))`.
  Recommended: 1.4× for broken-formal trials.
- `ExperimentConfig.min_adaptive_steps: int = 10` — floor for trivial proofs.
- Applied in `run_broken_formal_trial()` after building the agent config.

#### Experiment runner — shortest-first ordering (`config.py`, `runner.py`)

- `ExperimentConfig.sort_by_difficulty: bool = False` — when True, uses
  `load_cases()` sorted by `len(tactic_lines)` ascending instead of random
  sampling. First N trials are the shortest proofs, giving a clearer
  capability gradient.

#### Jupyter notebook (`notebooks/elgamal_broken_repair.ipynb`)

- New notebook for running the ElGamal experiment with the improved settings.
- Sections: configuration, case preview (all 15 proofs with adaptive budgets),
  full experiment run, results summary, per-trial DataFrame + bar chart,
  failed-trial retrospective inspection, and single-trial debug mode.
- The 15 available proofs range from 1-line (enc_stateless) to 104-line
  (G1_G2_eq), with adaptive steps from 10 to 145.

#### Investigation notes (not code changes)

- **q1 scope issue** (trial 2): NOT a harness bug. Caused by agent choosing
  `proc*.` when the broken proof uses `proc.` + `inline*.`. After `proc.`,
  local variables like `q1` become accessible for `seq` invariants. The
  few-shot guide's "prefer `proc*.`" advice misled the agent for proofs that
  need oracle inlining.

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

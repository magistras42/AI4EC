# Proof Repair — What Remains, and What to Take From shannon-prover

**Date:** 2026-08-06 · **Branch:** `shannon-llm-integration` · **Audience:** the
next engineer or agent picking this up.

Read [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §12–§15 first for how
the measurements were obtained. This document is only what is still broken and
where the answers probably are.

> **2026-08-06, second pass.** §2, §3, §4 and §6 have been worked. §2's premise
> turned out to be **wrong** and is retracted below with the measurement that
> refutes it; §3 found a different and larger bug than the one it predicted.
> Sections are marked DONE / RETRACTED / OPEN. §5 is still open and still
> blocked on a solver LLM.

---

## 0. The one number that matters

Across every run, on every version of the code:

> **~50% of tactics the model proposes change nothing.**

| run | code | steps that changed nothing |
|---|---|---|
| E | before any of this work | 28% inert-accepted |
| G | + no-op detection | 50% |
| H | + detection, enforcement, prompt fixes | 52% |

Detection, removal, banning and re-prompting all work. **None of them reduced
the rate.** They stop the harness *wasting compute* on bad proposals; they do
not make the proposals better.

The independent port in §2 reproduces this number from the run logs without
being told it: of 525 accepted transitions, 268 (51.0%) leave the goal
byte-identical.

---

## 1. What is already done (do not redo)

| | where | state |
|---|---|---|
| Import repair, error-kind rule selection | `integration/agent/import_repair.py` | **finished** — 12/12 resolved on ElGamal, zero pre-proof errors left |
| Graded outcomes (W4.5) | same | finished |
| No-op detection | `loop.py::confirm_noop` | works; two-view + removal proof. **Keep it** — see §2 |
| No-op enforcement | `loop.py`, hard-reject path | works; 0 leaked repeats (was 7/lemma) |
| First-instruction fact | `prompt.py::_first_statement_kind` | shipped, moved `invalid last instruction` 7→3 |
| Repair-first prompt | `prompt.py::format_broken_tactic_repair` | shipped; `rnd` fixation 19→1 on `G2_G3` |
| Structural state diff | `integration/agent/goal_diff.py` | **new** — §2 |
| Program position parser | `integration/agent/ec_program.py` | **new** — §3 |
| Version hopping (W7) | `experiment/ec_versions.py`, `version_hop.py` | implemented; see §6 for the binary |

---

## 2. RETRACTED — subgoal count cannot replace the no-op signal

**The previous revision of this document said this was the highest-value item
and that shannon-prover had already solved it. That was wrong, and acting on it
would have deleted load-bearing tactics.**

The claim was that `ec_state_diff.py`'s `PROGRESS_DECOMPOSITION` arm "is
exactly the `skip.` case I got wrong", so swapping `confirm_noop`'s primary
signal from goal text to subgoal count would fix the known counterexample and
let the two-view conjunction be deleted. Three measurements say otherwise.

**1. `skip.` does not change the subgoal count.** On
`fixtures/hoare_after_proof.ec`, `skip.` converts a Hoare judgment into an
ambient goal *without splitting it*. The count is 1 on both sides in both
views. Running shannon-prover's own `compute_state_diff` on the transition
returns `NEUTRAL_OR_NO_CHANGE`, not `PROGRESS_DECOMPOSITION` — so a
count-primary detector deletes it, and the proof stops closing. Pinned as
`test_noop_tactics.py::test_subgoal_count_does_not_rescue_the_skip_case`.

**2. Subgoal count adds nothing on top of text comparison.** Over 525 accepted
transitions harvested from the run logs, the number where the goal text was
byte-identical but the subgoal count moved is **0**. Count equality is implied
by text equality, so the count cannot rescue any tactic the text comparison
condemns — not as a primary signal, and not as a veto either.

**3. Making it primary is actively worse.** 113 of those 525 moved the goal
text with *every* structural metric flat. A count-first rule calls all 113
inert — 21.5% of accepted steps deleted.

The category error is that upstream's verdict decides whether to *print a hint*
and ours decides whether to *delete a line*. `NEUTRAL_OR_NO_CHANGE` on "no
discriminating signal" is cheap for them and unsound for us.

### What was taken instead

`integration/agent/goal_diff.py` ports the metrics, the cosmetic-noise
detectors and the verdict taxonomy, and uses them where they *do* pay: telling
the model what its last accepted tactic structurally did. The harness could
always say a tactic did NOTHING and could never say what a productive one did,
so a `seq` that triples the subgoal count read to the model exactly like a
regression. `loop.py` now passes the previous goal and tactic to
`build_prompt(state_diff=...)`.

Adaptations, all measured, not inherited:

- `UNCLASSIFIED` replaces upstream's `NEUTRAL_OR_NO_CHANGE` for "text moved but
  no metric did", and prints nothing. This is the 113-case above.
- Body metrics are scoped to the *active* subgoal. 90% of our prompts carry
  more than one goal and 70% of the text is inactive.
- Text comparison keeps the `(remaining: N)` header. Stripping it (as the
  chrome filter first did) makes a step that discharged two of three subgoals
  compare byte-identical.
- `_detect_unreduced_glob`'s regex forbids nested parens upstream, so the
  deepest chain it can capture is depth 2 and its `>= 3` test can never fire.
  Rewritten to walk balanced parens.

Verdict distribution over the 525 real transitions: 51.0%
`NEUTRAL_OR_NO_CHANGE`, 21.1% `PROGRESS_DECOMPOSITION`, 16.8% `UNCLASSIFIED`,
11.0% `PROGRESS`, 0% `REGRESSION`. The block renders on 32% of accepted steps.

**Caveat, recorded rather than hidden:** the four cosmetic-noise detectors fire
on **0 of 987** real goals in the current ElGamal-only corpus. They are ported
because the previous revision asked for them; their tests are synthetic and the
feature is unvalidated against our own runs. `REGRESSION` likewise never fired
— every subgoal increase in the corpus came from a tactic on the decomposer
list.

---

## 3. DONE — position errors: the count the model was given was wrong

Taxonomy over 203 failures (runs C+D+E):

| category | share | addressed by |
|---|---:|---|
| wrong logic class | 24% | §4 |
| **position errors inside the right tactic class** | **~45%** | **this item** |
| wrong arguments | 8% | prompt ladder (partial) |
| smt gave up | 7% | — |

The plan here was to port shannon-prover's `ec_asym_seq_hint.py` and
`_compute_seq_suggestions`, blocked on getting typed statements. Looking at the
actual goal text first found something cheaper and worse.

### The bug

EasyCrypt prints a **shared index column between the two programs**:

```
x <- pubk0 ^ r                    ( 9--)  if (g ^ (q1 * q2) \notin RO.mp) {
                                  ( 9.1)    y <$ dtext
                                  (    )      RO.mp.[g ^ (q1*q2) <- y]
if (x \notin RO.mp) {             (10--)  u <- oget RO.mp.[g ^ (q1 * q2)]
```

`( N--)` (or `( N)`) opens top-level statement N; `( N.k)` is nested inside it
and `(    )` is a wrapped line. The prompt derived its instruction counts by
counting *lines that look like instructions*, which counts a statement's body
as more statements.

On the goal above the model was told **`left: 15, right: 15`**, directly above
the sentence "N/M must not exceed these counts". The true top-level counts are
**13 and 12**. Both stated maxima were out of range — an
`invalid 'position' parameter` handed to the model as a fact.

Across the corpus: of 654 indexed blocks, the old count differed on **503**,
and it **overstated the maximum in all 503**.

### What shipped

`integration/agent/ec_program.py` parses the dump by locating the index column
(majority vote across rows — program text carries its own parens, and a
no-argument call `M.f()` is the worst offender). It yields positioned, typed
statements per side, and:

- `seq_candidates` — the port of `_compute_seq_suggestions`. Matches calls to
  the same procedure across sides and proposes the cut there. Fires on 177
  blocks. On the goal above it proposes `seq 13 12`, matching `guess` on both.
- `common_prefix_length` — where the programs stop agreeing, the other natural
  cut.

The prompt now states exact bounds, the divergence point, and up to three
matched-call cuts. When no index column was printed (29 blocks, all
proc-header-only) it states **no count at all** rather than a guess.

Two incidental fixes: `_program_statement_block` used `.strip()`, which deletes
the leading spaces that *are* the empty left column on row one and misaligned
that row's marker (25 blocks lost); and side assignment is now pinned by test,
since a transposed `seq N M` is the exact error this prevents.

### On the previous revision's blocker

The claim that shannon-prover's parser "does not read our goal format" was
already retracted once (it was a swallowed `ImportError`). It is now moot: the
work is done with our own extractor, which the previous revision's own numbers
favoured (111 goals ours found and theirs missed, against 11 the other way).
Nothing imports from `shannon-prover/` — the logic is ported into
`integration/`.

---

## 4. DONE (partially) — the wrong-class discriminator is weak but real

25 failures say `expecting a goal of the form: hoare[S], ehoare[S], phoare[S],
equiv[S]` — a program-logic tactic applied to a goal that is not a
program-logic judgment. Neither classifier sees it:
`prompt.py::goal_looks_program_logic` says program-logic on 91% of them, and
shannon-prover's `classify_goal` said pRHL on all 25.

The suggestion was to retry with subgoal count in hand. Re-measured over 568
labelled steps (501 where a program-logic tactic was accepted, 67 where it drew
the class error; base rate 11.8%):

| signal | precision | recall |
|---|---:|---:|
| subgoal count alone | — | does not separate |
| no instruction index column | 30.6% | 56.7% |
| empty statement block | 34.0% | 50.7% |
| **no index column AND 2 open goals** | **57.7%** | **44.8%** |

**Subgoal count alone does not work** — 21.6% of accepted vs 50.7% of wrong
steps sit at two open goals, nowhere near a rule. Conjoined with "EasyCrypt
printed no instruction indices" it reaches 57.7% precision, ~5x the base rate,
and its precision beat the base rate in **all 9** runs measured.

57.7% is a hint, not a fact, so it ships as one: a bullet saying this
combination is a discharged judgment about half the time and that *if* the
class error appears, go ambient immediately rather than retrying. It does not
claim the goal is ambient — two in five of these really are program-logic
goals.

---

## 5. OPEN — the stuck budget is still un-validated end to end

`stuck_counter` no longer increments on no-ops (commit after run H), because
coupling "this step was wasted" to "this trial is going nowhere" made trials die
progressively earlier — `G2_G3` went 75 → 44 → 27 steps across runs while the
inert-step share stayed flat.

**Still no run has executed with it.** The measurement asked for — run the
ElGamal suite and check `G2_G3` gets a runway comparable to run E's 75 steps —
**could not be done here**: LM Studio is up but has only
`text-embedding-nomic-embed-text-v1.5` loaded, no chat model, and neither
`ANTHROPIC_API_KEY` nor `DEEPSEEK_API_KEY` is set. Load a solver model or set a
key and it is a single suite run.

What *was* validated is the mechanism the concern is about, behaviourally
rather than by reading the source
(`test_noop_tactics.py`, two new tests):

- eight consecutive **fresh** no-ops against a stuck limit of 3 exit
  `MAX_STEPS`, not `STUCK`, and the trial keeps all 8 steps;
- **repeating one** banned no-op still exits `STUCK`. This is what makes
  sparing the first occurrence safe, and it matters because the proof-state
  hash cannot catch it — a no-op appends a line before it is removed, so the
  tail hash is new every time.

**Confound to be aware of when that run happens:** §2's state-diff block, §3's
corrected `seq` bounds and §4's hedge all change the prompt. A run now measures
those together with the stuck-budget change. If the budget question needs
isolating, gate the prompt additions behind a flag first.

Relevant: `shannon-prover/core/easycrypt/session_no_progress.py` — their
equivalent policy, worth reading before re-tuning ours.

---

## 6. Smaller items

**Changelog document-frequency guard — DONE.** `by_tactic["smt"]` holds 28 of
913 entries (3.1%); matching on it retrieved four unrelated r2023.09 chore
commits. `repair_hints.py::_discriminative_tactics` now drops any tactic name
whose bucket exceeds 2% of the catalog before retrieval, and retrieves nothing
rather than noise when that leaves no query. Measured on the shipped catalog the
threshold prunes exactly `smt` (3.07%), `rewrite` (2.96%), `simplify` (2.63%)
and `proc` (2.52%); the next bucket down is `rnd` at 1.31%, so the cut is not
tuned to a hair. `hint_uptake` was **0.0** — no identifier the changelog half
introduced ever appeared in an accepted tactic — which is what to expect when
the query terms select 3% of the catalog at random.

**W7 binary — DONE, and it exposed a bigger problem.**

The provision works. `python3 -m integration.experiment.ec_versions --version
r2025.02` completed in ~5 minutes: worktree, opam switch `cs846-ec-r2025.02`,
`dune build`, a 38 MB `ec.exe` that compiles a real `.ec` file with exit 0.
Registry hygiene note: `--list` had reported r2025.02 as "built
2026-08-05T01:47:08" while no worktree, binary or switch existed — a stubbed
run wrote the real registry — but the tool already self-heals that
(`registry lists r2025.02 but ... is gone; forgetting it`), so no code changed.

**The binary is nevertheless unusable by the agent.** Its commands are
`compile, cli, config, runtest, why3config`. There is no `llm` subcommand — and
`llm -upto` / `llm -lastgoals` is the *only* interface the harness has for
fetching a goal (`easycrypt.py::fetch_goal`, `validate_file`).

That command is fork-local. It was added in `da4935c9` ("Add goal printing
flags (-upto, -lastgoals) and LLM agent guide") on **2026-04-11**, and
`ec_versions.py` builds each release from the fork at that release's tag. So:

| tag | date | usable |
|---|---|---|
| r2022.04 … r2026.03 (11 tags) | 2022-04-27 … 2026-03-10 | **no `llm` command** |
| r2026.05, r2026.06, r2026.07 | 2026-04-15 … | has it |

**11 of the 14 buildable tags cannot serve a goal to the agent.** Version
hopping across the range it was built for does not currently work, and no
amount of provisioning fixes it — the binaries build fine and simply lack the
interface.

The obvious repair is to graft `da4935c9` onto each release worktree before
building. It is not free: `git cherry-pick --no-commit da4935c9` onto r2025.02
conflicts in four files (`src/ec.ml`, `src/ecCommands.ml`, `src/ecOptions.ml`,
`src/ecOptions.mli`), so it needs manual resolution per release and a build to
validate each one (~5 min apiece). The worktree was reset afterwards and is
clean at `46099edd` with its binary intact.

Before spending that: check whether version hopping needs the old goal
*printer* at all, or only the old *checker*. If a hop only has to answer "does
this proof still compile under r2024.09", `compile` alone is enough and the
whole problem disappears.

**The A/B is built but should probably not be run.** `--no-changelog-hints`
and `compare_runs.py` exist. §10.1 already dropped an A/B on this corpus for
variance (11-vs-1 accepted tactics under identical config). Do not spend on it
without ≥5 seeds per arm. Not attempted.

---

## 7. Traps that cost me time

**The Jupyter kernel caches modules.** Runs C–F all executed a 22-hour-old
`loop.py` while I believed they were testing new code. Before any run:

```python
from integration.agent.loop import confirm_noop, _current_goal
from integration.agent.prompt import _first_statement_kind
```

`ImportError` means restart the kernel. `In[37]` instead of `In[1]` means it
was never restarted.

**Cell 6 before cell 12, always.** Running the experiment cell without
re-running the config cell reuses a stale `output_dir` and **overwrites a
previous run in place** — it happened, and mixed two runs' events into one
file.

**Counting tactics is harder than it looks.** Three bugs in a row: dropping
comment-prefixed lines (`(* note *) trivial.` is a tactic), counting physical
lines (a `seq ... : (inv)` wraps), and splitting on qualified names
(`RealOrder.lerr_eq` has a depth-0 dot). The invariant that catches all of
them: a trial with `fully_replayed: true` and zero agent iterations must count
exactly its replayed prefix. Assert it.

**Counting *instructions* is harder still, and got it wrong for longer.** See
§3. The lesson generalises: before porting an analysis that needs a structured
read of the goal, print the goal. The two-column index layout is not what
either codebase's line-shape heuristics assumed.

**Do not hand-transcribe goal text into a test.** The column offsets *are* the
data. A retyped copy of the §3 fixture silently parsed as 12/11 instead of
13/12. It is now read from
`integration/tests/fixtures/elgamal_equiv_block.txt`, copied verbatim from a
run log.

**Run-to-run variance exceeds most effects.** `G2_G3` retained 19 → 13 → 8 → 3
tactics across four runs, two of them identically configured. Do not read a
single run as evidence. This is why the A/B was dropped, and why §4's rule was
checked across all 9 runs before shipping rather than on the pooled numbers.

---

## 8. Suggested order

1. **§5** — load a solver model or set an API key and run the ElGamal suite.
   It is the only thing standing between four shipped changes and knowing
   whether any of them help. Note the confound above.
2. **§0** — proposal quality is still the whole problem and still unaddressed.
   §2's state-diff block is the first thing that even tries; measure whether
   the inert rate moves off ~50%.
3. **§6/W7** — decide whether version hopping needs the fork's `llm` printer or
   only `compile`. That question, not more provisioning, is what gates the
   feature; 11 of 14 tags are otherwise dead.
4. **§4** — 57.7% precision is a hint, not a discriminator. If a real one
   exists it is probably not in the printed goal text at all; consider asking
   EasyCrypt directly (`ec_error_classifier.py` splits SYNTAX from semantic
   errors, a finer cut than our `ec_errors.py` kinds).

Anything that needs a structured read of the goal now has one:
`integration/agent/ec_program.py` for positions and
`integration/agent/goal_diff.py` for structure. Neither imports from
`shannon-prover/`.

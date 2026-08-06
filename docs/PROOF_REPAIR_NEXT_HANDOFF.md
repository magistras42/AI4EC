# Proof Repair — What Remains, and What to Take From shannon-prover

**Date:** 2026-08-06 · **Branch:** `shannon-llm-integration` · **Audience:** the
next engineer or agent picking this up.

Read [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §12–§15 first for how
the measurements were obtained. This document is only what is still broken and
where the answers probably are.

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
not make the proposals better. Everything below is either (a) finishing the
symptom management or (b) finally attacking proposal quality.

If you only do one thing, do §2.

---

## 1. What is already done (do not redo)

| | where | state |
|---|---|---|
| Import repair, error-kind rule selection | `integration/agent/import_repair.py` | **finished** — 12/12 resolved on ElGamal, zero pre-proof errors left |
| Graded outcomes (W4.5) | same | finished |
| No-op detection | `loop.py::confirm_noop` | works; two-view + removal proof |
| No-op enforcement | `loop.py`, hard-reject path | works; 0 leaked repeats (was 7/lemma) |
| First-instruction fact | `prompt.py::_first_statement_kind` | shipped, moved `invalid last instruction` 7→3 |
| Repair-first prompt | `prompt.py::format_broken_tactic_repair` | shipped; `rnd` fixation 19→1 on `G2_G3` |
| Version hopping (W7) | `experiment/ec_versions.py`, `version_hop.py` | implemented, **never built a binary** |

---

## 2. OPEN — the no-op detector is measuring the wrong thing

**This is the highest-value item and shannon-prover already solved it.**

`loop.py::confirm_noop` decides "did this tactic do anything" by comparing
**goal text** through two views plus a removal re-check. That is provably
insufficient, and there is a test pinning the counterexample
(`test_goal_text_equality_is_not_proof_of_inertness`): on
`integration/tests/fixtures/hoare_after_proof.ec`, `skip.` renders
byte-identical either side and is load-bearing — remove it and the proof no
longer closes.

I worked around it by only acting on *repeats*, then widened to a two-view
conjunction. Both are patches over a wrong primary signal.

### What to take

`shannon-prover/core/easycrypt/analysis/ec_state_diff.py` (901 lines). It
answers the same question with **subgoal count** as the primary signal and text
comparison only as a tie-breaker:

```
PROGRESS                 post_subgoals == 0, or subgoal_count strictly decreased
PROGRESS_DECOMPOSITION   decomposition tactic + subgoal_count strictly INCREASED
PROGRESS_WITH_COSMETIC_NOISE
NEUTRAL_OR_NO_CHANGE     text bit-identical after normalisation ("toxic loops")
REGRESSION               non-decomposition tactic increased subgoal_count
```

`PROGRESS_DECOMPOSITION` is exactly the `skip.` case I got wrong. Their own
docstring records hitting the same trap:

> "An earlier tactic-name heuristic consistently missed `congr` /
> `call (_: Inv)` / `have h : P` / `while (I)` / compound tactics containing
> `case` — every one a legitimate decomposition."

**Portability is good.** It needs a subgoal count, and our goal text already
carries `Current goal (remaining: N)` — 243 of 277 sampled goals had it (the
other 34 are single-goal states). See `_extract_subgoal_count`.

Also useful there: `_detect_cosmetic_noise` (beta-redex, eta-expansion,
unreduced `glob`), which separates "the tactic did something ugly" from "the
tactic did nothing".

### Acceptance test

Replace the primary signal in `confirm_noop`, keep the removal proof, and
re-run the existing suite. `test_goal_text_equality_is_not_proof_of_inertness`
must still pass, and you should then be able to **delete the two-view
conjunction** — if subgoal count is the right signal, `proc.` and `skip.` stop
being special cases.

---

## 3. OPEN — position errors are ~45% of failures and nothing addresses them well

Taxonomy over 203 failures (runs C+D+E):

| category | share | addressed by |
|---|---:|---|
| wrong logic class | 24% | §4 |
| **position errors inside the right tactic class** | **~45%** | **this item** |
| wrong arguments | 8% | prompt ladder (partial) |
| smt gave up | 7% | — |

"Position error" means the model picked the right tactic and aimed it at the
wrong place: `invalid first instruction` (all `if`), `invalid last instruction`
(mostly `rnd`), `invalid 'position' parameter` (bad `seq N M` indices).

`prompt.py` now reports first/last instruction per side, which is the cheap
half. The expensive half is telling the model *which indices are valid*.

### What to take

`shannon-prover/core/easycrypt/analysis/ec_asym_seq_hint.py` (437 lines) —
generates concrete `seq N M : <invariant>` proposals for asymmetric pRHL goals,
with synthesised invariants:

```python
detect_and_propose(...)      -> AsymSeqProposal
detect_and_propose_all(...)
synthesize_invariants(align_result)
```

and `ec_goal_parser.py::_compute_seq_suggestions`, which matches CALLs to the
same procedure across the two sides and proposes the cut point at the match —
precisely the case where our model guesses indices.

### The blocker — smaller than I first reported

**CORRECTION.** An earlier revision of this document said the shannon-prover
parsers "do not read our goal format", citing `parse_goal` returning zero
statements on 25/25 failures and 202/202 successes. **That measurement was
invalid.** `ec_goal_parser._parse_prhl` delegates to
`core.easycrypt.analysis.swap_align` inside a bare `except Exception: pass`,
and I ran it without `shannon-prover/` on `sys.path` — so every call was
silently swallowing an `ImportError` and reporting zeros. The format was never
the problem in those numbers.

Re-measured with `PYTHONPATH=.:shannon-prover` over 307 real goals:

| | |
|---|---:|
| `classify_goal` | 303 pRHL, 4 probability |
| both extractors found statements | **131** |
| only ours found them | **111** |
| only theirs found them | 11 |
| neither | 54 |

So it parses **131 of our goals correctly, today, with no changes.** The 111 it
misses are one deliberate early return:

```python
# Programs-in-sync: EC collapses both sides; no actionable alignment.
if "[programs are in sync]" in raw_text:
    return _build_align_result([], [], pre_text, post_text, context_file)
```

**48% of our goals carry that marker, and 111 of them list statements anyway.**
That assumption holds for shannon-prover's own corpus and not for ours.
Stripping the marker before parsing recovers them — verified on a sample: 5
tested, 5 recovered, 0 still empty, with typed statements and `vars_written`
populated.

So the adaptation is roughly:

1. put `shannon-prover/` on the path (or vendor `swap_align` +
   `ec_program_statements` + `ec_sampling_statements`);
2. **remove the bare `except Exception: pass`** in `_parse_prhl` — it hid this
   for the whole investigation and will hide the next one;
3. treat `[programs are in sync]` as "both sides identical" rather than "no
   statements", so a listed program is still parsed.

Then `_compute_seq_suggestions` and `ec_asym_seq_hint` have the typed
`left_stmts` / `right_stmts` they need, and porting the analyses is no longer
blocked on writing our own structured reader.

Two caveats before trusting it further:

- `classify_goal` still calls **all 25 wrong-class failures `pRHL`** (§4). Fixing
  statement extraction does not fix classification.
- The one recovered sample came out `left=0 right=5` where our extractor sees
  `left=5 right=0`. One of the two has the sides swapped for our column layout.
  Check that before consuming positions — a `seq N M` with N and M transposed
  is exactly the position error this is meant to prevent.

## 4. OPEN — 24% wrong logic class, and NEITHER codebase can currently detect it

25 failures say `expecting a goal of the form: hoare[S], ehoare[S], phoare[S],
equiv[S]` — the model applied a program-logic tactic to a goal that is not a
program-logic judgment.

Both classifiers get these wrong:

- our `prompt.py::goal_looks_program_logic` → says program-logic
- their `ec_goal_parser.py::classify_goal` → says `pRHL`, on all 25

They are `[programs are in sync]` goals whose judgment is already discharged.
I looked for a textual discriminator and there is none:

| | accepted a program-logic tactic | rejected it |
|---|---:|---:|
| sync goals | 89 | 12 |
| …statement block empty | 36 | 4 |

Block emptiness occurs at the same rate in both. Current mitigation is a
recovery line in the prompt ("if you get that error, go ambient").

### Where to look

Subgoal count may crack this too — a discharged judgment likely differs in
`remaining:` from a live one. Worth testing before anything more elaborate.
Also `ec_error_classifier.py` (349 lines) splits EC errors into SYNTAX vs
semantic, which is a finer cut than our `ec_errors.py` kinds and may separate
"wrong class" from "right class, wrong target" more reliably.

---

## 5. OPEN — the stuck budget is now un-tuned

I removed `stuck_counter` incrementing on no-ops (commit after run H) because
coupling "this step was wasted" to "this trial is going nowhere" made trials
die progressively earlier — `G2_G3` went 75 → 44 → 27 steps across runs while
the inert-step share stayed flat.

**That change is untested.** No run has executed with it. First job for
whoever picks this up: run the ElGamal suite and check `G2_G3` gets a runway
comparable to run E's 75 steps without run E's padded script.

Relevant: `shannon-prover/core/easycrypt/session_no_progress.py` — their
equivalent policy, worth reading before re-tuning ours.

---

## 6. OPEN — smaller, well-specified

**W7 has never built a binary.** `ec_versions.py` is implemented and tested
with the shell stubbed; worktree creation is verified against the live clone,
but `opam switch create` + `dune build` have only been dry-run. One real
provision would close it:
`python3 -m integration.experiment.ec_versions --version r2025.02`.

**Changelog retrieval has no document-frequency guard.** `by_tactic["smt"]`
holds 28 of 913 entries (3.1%); matching on it retrieved four unrelated
r2023.09 chore commits. Skip tactic-keyed retrieval when the bucket exceeds
~2% of the catalog. `hint_uptake` is currently **0.0** — no identifier the
changelog half introduced ever appeared in an accepted tactic.

**The A/B is built but should probably not be run.** `--no-changelog-hints`
and `compare_runs.py` exist. §10.1 already dropped an A/B on this corpus for
variance (11-vs-1 accepted tactics under identical config). Do not spend on it
without ≥5 seeds per arm.

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

**Run-to-run variance exceeds most effects.** `G2_G3` retained 19 → 13 → 8 → 3
tactics across four runs, two of them identically configured. Do not read a
single run as evidence. This is why the A/B was dropped.

---

## 8. Suggested order

1. **§2** — replace the no-op primary signal with subgoal count from
   `ec_state_diff.py`. Biggest correctness win, and it removes two workarounds.
2. **§5** — validate the stuck-budget change with one run.
3. **§3** — typed statements, then port `_compute_seq_suggestions` and
   `ec_asym_seq_hint`. Largest failure category.
4. **§4** — retry the wrong-class discriminator with subgoal count in hand.
5. **§6** — the small items, any time.

Items 1 and 3 both need a faithful structured read of our goal format — but
per §3 that is **mostly already available**, not something to build from
scratch. `swap_align` parses 131 of 307 real goals as-is and 242 of 307 with
the `[programs are in sync]` early return relaxed. Budget it as an adaptation,
not a rewrite, and start by deleting the `except Exception: pass` that hid the
real state of things.

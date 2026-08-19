# SMT Solver Benchmark — Results Summary

**Question: does solver choice really matter? → Yes (H1 and H2 both confirmed)**

- Corpus: 48 .ec files (easycrypt/examples + the-joy-of-easycrypt; 1 dep-contaminated file excluded)
- Protocol: parallel success sweep → **serial re-timing** (median of 3 reps on success, 1 confirmation on failure) — all numbers below are serial measurements
- Setup: SMT timeout 10 s; solvers Z3 4.12.6 / CVC5 1.0.8 / Alt-Ergo 2.4.3; EasyCrypt main 7e192dd

## Per-solver scores (H1)

| configuration | solve rate | mean time (solved files) |
|---|---|---|
| default(all) | 40/48 | 4.68s |
| Alt-Ergo | 33/48 | 2.34s |
| Z3 | 32/48 | 2.45s |
| CVC5 | 30/48 | 2.38s |

- **Solver disagreement: 16/48 files** ← files where solver choice changes the outcome (the key H1 number)
- **Exactly one solver succeeds: 3 files**
  - `examples/ehoare/adversary.ec` — Z3 only
  - `examples/ehoare/qselect/qselect.ec` — CVC5 only
  - `the-joy-of-easycrypt/04-hoare-logic/hoare-logic.ec` — Alt-Ergo only

### How to read this table

- **`default(all)` is the out-of-the-box behavior with no solver specified.**
  It runs the three detected solvers together and counts success if any one
  succeeds. It is the actual state a user gets writing `smt()` with no
  configuration, and the baseline against which the three single-solver rows
  are compared.
- **The baseline (40/48) solves more than any single solver (33·32·30).**
  Combining solvers solves more than any one alone — direct evidence that
  "different solvers solve different problems" (H1). The three single-solver
  scores differing (30–33), and each solver having its own single-winner
  file, reinforce this.
- **The baseline's mean time is longer (4.68 s vs 2.3–2.5 s single).** The
  baseline runs several solvers at once, so wall-clock and CPU exceed a
  single solver. Read the time comparison as "how much more resource for the
  same file", not "who is faster".
- **Caution:** mean time is over *solved* files, and each solver solves a
  different set (30 vs 40 files), so mean times must not be read directly as
  a ranking. The fair time comparison is only H2's "both succeed" basis
  below.

## Upper bound of selection (H2)

> **Per-file virtual best solver (VBS)** = a hypothetical selector that
> assigns, per file and after the fact, a solver that solves it (fastest one
> if several). Under the "one solver per file" constraint nothing can beat
> it, so it is the ceiling for any real selector (it assumes the answer is
> known, hence "virtual"). Because of that constraint it can actually solve
> *fewer* files than the default bundle that runs solvers together (below:
> 38 < 40).

- The per-file best single solver (VBS) solves 38/48; the default portfolio solves 40/48
- On the 38 files both solve: baseline total 102.6 s vs VBS total 94.1 s → **8.3% time saved under perfect per-file solver selection** (the ceiling that LLM-based selection could target)

## Bonus finding: the case for call-level selection

Files the portfolio solves but **no single solver** does — direct evidence
that different smt calls within one file need different solvers:
- `examples/ChaChaPoly/chacha_poly.ec` (191 smt calls)
- `examples/UC/dh_enc.ec` (43 smt calls)

→ Suggests **call-level solver selection** (the next step of RQ3) can gain
beyond file-level.

## Measurement reliability

- (file, configuration) pairs whose verdict flipped between the parallel sweep and serial retime: 0
- Nondeterminism within 3 reps (flaky): 0
- Disagreeing-file count: 16 under the parallel sweep vs 16 serial — cross-check
- Warm-up (baseline) failures: 9 files — mostly legacy broken by version drift under `old/`, `to-port/`, `incomplete/` (a potential dataset for the proof-repair side):
  - `examples/ehoare/random_boolean_matrix.ec`
  - `examples/incomplete/FDH.ec`
  - `examples/old/list-ddh/DDH.ec`
  - `examples/old/list-ddh/DDH_indexed.ec`
  - `examples/old/list-ddh/RandOracle.ec`
  - `examples/old/list-ddh/list_ddh_no_inner.ec`
  - `examples/old/trapdoor.ec`
  - `examples/to-port/RingCloning.ec`
  - `examples/to-port/hashed_elgamal.ec`

## Interpretation guide (for the reader)

Separating what this experiment established from what it did not.

**1. Solver choice genuinely decides outcomes (established).**
In 16 of 48 files, success flipped with the solver, and each of the three
solvers has a file only it solves. The assumption "any solver gives the same
result" does not hold; the premise that solver choice has room for
improvement is supported by data.

**2. But at file granularity, "smarter selection" does not solve more files
(the twist).** Even the ideal per-file selector (VBS) solves only 38 files —
fewer than the default bundle (40). The cause is two files
(`chacha_poly.ec`, `dh_enc.ec`) that no single solver can solve alone and
that only a combination of solvers gets through. Handing an entire file to
one solver cannot beat the default bundle. Read this not as "solver selection
is pointless" but as "the selection unit must be smaller than a file (per
call)".

**3. The gain from file-level selection is resource savings, not more
solves.** On the 38 files both approaches solve, picking the best single
solver saves 8.3% time. The default bundle is safe but spends more compute.
So the real value of file-level solver selection is "solving what already
solves with less compute", not "solving the unsolved". That 8.3% is the
ceiling for any file-level selector, human or AI.

**4. Which solver wins is hard to predict from a file's surface (pilot).**
Given only file contents, a language model predicted the winner for the three
single-winner files as "Z3" every time (accuracy 33%, same as guessing),
while CVC5 and Alt-Ergo actually won two of them. The surface rationale
"nonlinear arithmetic, so Z3" did not match reality. With n=3 nothing is
statistically settled, but choosing winners from surface features (imported
theories, kind of arithmetic) does not look promising.

**In short**, the reader should take three things away: (a) the solver-choice
problem is real; (b) it pays off at call granularity, not file granularity;
(c) prediction likely needs the content of the actually-failing goal, not
surface file features.

## Limitations (honest)

- File-level measurement is a proxy for smt-call-level — a failure observes only the "first failure point"; per-call outcomes come with the next step (call-level instrumentation).
- The baseline portfolio runs solvers in parallel, so time comparisons against single solvers differ in CPU usage (solve-rate comparisons are fair).
- The corpus is tutorial/example-centric — needs extension to large real-world proofs (formosa-crypto, …).

## Three-sentence summary (for meetings)

> We validated RQ3's premise — across 48 files, **16 showed solver-dependent success**, and 3 could be solved by exactly one solver (each of Z3/CVC5/Alt-Ergo has a file only it solves). Perfect per-file solver selection would cut time by **8%** vs the default — the ceiling for LLM-based selection. Moreover, the 2 portfolio-only files show the need for call-level selection; next steps are per-call instrumentation and an LLM suggestion-loop prototype.

# STATUS — SMT Solver Benchmark: plan and result framework (2026-07-02)

> This document states the **purpose, method, and result framework** first.
> **Current state:** full pipeline complete — toolchain build, validation
> (23 tests passing), sweep, serial retime, analysis, H3 pilot.
> Final numbers in [§5](#5-results-2026-07-02-serial-retime-final); full
> summary in `results/SUMMARY.md`; H3 results in `results/h3/FINDINGS.md`.

---

## 1. Purpose

Validate, with data, the **premise** of proposal **RQ3** ("how much can
AI-based SMT solver selection/configuration improve EasyCrypt `smt()`
performance?"):

> "Do proof success and speed really differ across solvers?"

Only if this premise holds is there room for improvement in "choosing the
solver well", and only then does LLM-based selection research make sense. If
they do not differ (any solver solves everything, or all fail), RQ3 itself
must be reconsidered. This benchmark **quantifies** the premise and measures
the **upper bound** LLM-based selection could target.

## 2. Hypotheses

- **H1 (solver disagreement exists)** — at the same file and SMT timeout,
  there are files where solvers disagree on success/failure.
  · Metrics: number of disagreeing files, number of "exactly one solver
  succeeds (single winner)" files.
- **H2 (upper bound of selection)** — the per-file best solver (VBS, virtual
  best solver) beats the default portfolio on solve rate/time.
  · Metrics: VBS vs baseline solve-rate difference; total-time savings on
  commonly-solved files.
  · That savings rate is **the upper bound LLM-based solver selection could
  target**.
- **H3 (conditional; LLM predictability)** — on "single-winner" files, can
  an LLM predict the winner from the file contents alone.
  · Metric: prediction accuracy vs the 3-way random baseline (33%).
  · Pursued in earnest only with enough disagreeing/single-winner files;
  otherwise a feasibility pilot only.

## 3. Method (pipeline)

**Corpus** — a controlled set guaranteed compatible with the current version:
- `corpus/examples/` — a copy of the EasyCrypt distribution's `examples/`
  (45 .ec, 36 using smt)
- `corpus/the-joy-of-easycrypt/` — the pedagogical tutorial (4 .ec)

**Measured configurations** — each file verified under 4 configurations,
recording success and time: Z3 alone / CVC5 alone / Alt-Ergo alone /
`default(all)` (the default portfolio, baseline). Solver restriction is by
CLI flag only, no file edits (`easycrypt compile -p <Solver> -timeout <N>`).

**Two-pass protocol** (`bench_smt.py`):
1. **Pass 1, sweep (parallel)** — success/failure verdicts, rough timing.
   To defeat `.eco` caching, a warm-up builds dependency caches first; the
   measured run copies only the target file to a temp directory and runs
   with `-I <original>` (dependencies hit the cache, the target is always
   re-verified, no parallel contention).
2. **Pass 2, retime (serial)** — successful pairs re-run 3× (median);
   sweep-failed pairs re-confirmed once serially. This prevents verdicts
   flipped by parallel contention from hardening into the final data.
   **Final H1/H2 numbers come from these serial measurements.**

**Analysis** (`analyze.py`) — sweep + timing → auto-generates
`results/SUMMARY.md`.

**H3 pilot** (`h3_demo.py`) — builds **blind prompts** (file contents only,
no answer) for the single-winner files, and scores LLM predictions against
the ground truth.

## 4. Result framework (structure of `results/SUMMARY.md`)

The final summary is organized as:

1. **Per-solver scores (H1)** — solve rate and mean time per configuration.
2. **H1 key numbers** — count of solver-disagreement files; the
   single-winner file list (file → winner).
3. **Upper bound of selection (H2)** — VBS vs baseline solve rate; total-time
   savings on commonly-solved files (= the LLM-selection bound); files the
   baseline misses but a single solver solves.
4. **The case for call-level selection** — files solved only by the portfolio
   and by no single solver (direct evidence that different calls within one
   file need different solvers).
5. **Measurement reliability** — sweep↔retime verdict flips (size of the
   parallel noise), flaky count, warm-up-failure legacy file list.
6. **Limitations** — file-level as a proxy for call-level, the baseline's
   parallel-CPU asymmetry, etc.
7. **A three-sentence summary for meetings.**

H3 pilot results live separately under `results/h3/` (dataset, predictions,
accuracy).

## 5. Results (2026-07-02, serial retime, final)

> ✅ **Serial retime complete.** H1 and H2 both confirmed. Full summary in
> `results/SUMMARY.md`. Only the H3 pilot remains.

| metric | preliminary (parallel sweep) | **final (serial retime)** |
|---|---|---|
| files used | 48 | **48** (1 dep-contaminated excluded) |
| solver-disagreement files (H1) | 16 | **16** |
| single-winner files (H1) | 3 | **3** (one each for Z3, CVC5, Alt-Ergo) |
| VBS vs baseline solve rate (H2) | 38/48 vs 40/48 | **38/48 vs 40/48** |
| time saved under perfect selection (H2 bound) | ~9.1% | **8.3%** (102.6 s → 94.1 s) |
| portfolio-only files (all single solvers fail) | (confirmed in retime) | **2** (chacha_poly, UC/dh_enc) |
| H3 accuracy (single winners) | — | **1/3 = 33%** (= random). The LLM predicted Z3 for all three (mode collapse) |

**Per-solver solve rate (serial):** default(all) 40/48 · Alt-Ergo 33/48 ·
Z3 32/48 · CVC5 30/48.

**Measurement reliability:** sweep (parallel) → retime (serial) verdict flips:
**0**; flaky across 3 reps: **0** — the results are not driven by parallel
noise or nondeterminism (the 16 disagreements are identical under parallel
and serial runs).

Single winners (serial, final):
- `examples/ehoare/adversary.ec` — Z3 only
- `examples/ehoare/qselect/qselect.ec` — CVC5 only
- `the-joy-of-easycrypt/04-hoare-logic/hoare-logic.ec` — Alt-Ergo only

Portfolio-only (no single solver solves them → the case for call-level
selection):
- `examples/ChaChaPoly/chacha_poly.ec` (191 smt calls)
- `examples/UC/dh_enc.ec` (43 smt calls)

## 6. Interpretation scenarios (written in advance)

Judged with this frame once the experiment finishes:

- **H1 holds** — if a meaningful number of files disagree and each solver has
  a single-winner file → "solver choice decides outcomes" is confirmed by
  data. The RQ3 premise stands.
- **H2 reading** — a large VBS time saving means a large ceiling for
  LLM-based selection (time-centric value). Even with small savings, if the
  solve rate rises (single solvers solving files the baseline misses) the
  value lies in "does it solve at all", and that is what to emphasize.
- **If portfolio-only files appear** — strong evidence that file-level
  selection is insufficient and **call-level** selection is needed;
  justifies the next step of RQ3 (call-level instrumentation).
- **H3 reading** — accuracy meaningfully above 33% (random) suggests an LLM
  can infer winners from file features → worth scaling up. Near 33% signals
  that surface file features are not enough (more signal/features needed).

## 7. Environment (2026-07-02, no-root install)

| component | version |
|---|---|
| EasyCrypt | main `7e192dd` (2026-07-01), built from source |
| Z3 | 4.12.6 (official binary) |
| CVC5 | 1.0.8 (official binary) |
| Alt-Ergo | 2.4.3 (opam; OCaml 4.14 constraint) |
| Why3 | 1.8.2 |
| OCaml | 4.14.2 (opam switch) |

Activate with `source ~/ec-env.sh`.

## 8. Reproduction

```bash
source ~/ec-env.sh
python3 tests/test_bench_smt.py                                              # validation (23 tests)
python3 bench_smt.py corpus --jobs 8 --smt-timeout 10 --hard-timeout 300 \
    --out results/sweep.csv                                                  # pass 1: sweep
python3 bench_smt.py corpus --retime results/sweep.csv --reps 3 \
    --out results/timing.csv                                                 # pass 2: retime
python3 analyze.py                                                           # → results/SUMMARY.md
python3 h3_demo.py build                                                     # H3 pilot dataset/prompts
```

## 9. Next steps (decided after the results)

- **Call-level instrumentation** — file-level is a proxy; move to per-call
  success/time. Portfolio-only files are strong evidence this is the next
  step.
- **Corpus expansion** — toward real-world proofs (EasyTeach,
  formosa-crypto, …).
- **Legacy dataset sharing** — the warm-up-failure files (`old/`,
  `to-port/`, `incomplete/`) are a potential dataset for the proof-repair
  side.

## See also
- Design rationale and hypothesis definitions: `README.md`.
- Harness robustness (signal reaper, incremental saves, preflight, `.eco`
  defeat): the header comment of `bench_smt.py` and
  `tests/test_bench_smt.py`.

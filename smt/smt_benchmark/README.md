# SMT Solver Benchmark for EasyCrypt (RQ3 premise check)

> **See [STATUS.md](STATUS.md) for progress and next steps.** Code validation
> complete; the main experiment runs on a separate server.

**Question: does solver choice really matter?** — This validates, with data,
the premise behind proposal RQ3 ("how much can AI-based SMT solver
selection/configuration improve `smt()` performance?"): that success and
speed genuinely differ across solvers.

## Hypotheses

- **H1 (solver disagreement exists)**: at the same file and SMT timeout,
  there are files where solvers disagree on success/failure.
  Metrics: number of disagreeing files, number of "exactly one solver
  succeeds" files.
- **H2 (upper bound of selection)**: the per-file best solver (VBS, virtual
  best solver) beats the default portfolio on solve rate/time.
  Metrics: VBS vs baseline solve-rate difference, total-time savings on
  commonly-solved files.
  → The **upper bound** that LLM-based solver selection could target.
- **H3 (conditional)**: on "single-winner" files, can an LLM predict the
  winning solver from file contents alone. Pursued only if there are enough
  disagreeing files.

## Known limitations

- File-level measurement is a **proxy** for smt-call-level. A file can
  contain dozens of smt calls, and a failure shows only the "first failure
  point". Call-level instrumentation is the next step.
- The baseline (default portfolio) runs the detected solvers in parallel, so
  time comparisons against a single solver differ in CPU usage.

## Environment (built 2026-07-02)

| component | version |
|---|---|
| EasyCrypt | main `7e192dd` (2026-07-01), built from source |
| Z3 | 4.12.6 (official binary) |
| CVC5 | 1.0.8 (official binary) |
| Alt-Ergo | 2.4.3 (opam; 2.6.0 blocked by the OCaml 4.14 constraint) |
| Why3 | 1.8.2 |

Activate with `source ~/ec-env.sh`.

## Corpus

- `corpus/examples/` — a copy of the EasyCrypt distribution's `examples/`
  (45 .ec, 36 using smt). A controlled set guaranteed compatible with the
  current version.
- `corpus/the-joy-of-easycrypt/` — the pedagogical tutorial (4 .ec, one per
  chapter, 3 using smt).
- Files with their own `prover`/`timeout` pragmas are flagged in the CSV
  (e.g. `prover [""].` in `UC/MapAux.ec`, `UC/RndO.ec` — excluded from
  interpretation).

## How to run

```bash
source ~/ec-env.sh
# pass 1: parallel success/failure sweep (rough timing)
python3 bench_smt.py corpus --jobs 8 --smt-timeout 10 --hard-timeout 300 --out results/sweep.csv
# pass 2: serial re-timing of successful pairs, 3 reps (precise timing)
python3 bench_smt.py corpus --retime results/sweep.csv --reps 3 --out results/timing.csv
```

Result summary: `results/SUMMARY.md`.

# Unsat core extraction — corpus summary

- (file, solver) runs: 96; whole-file ok: Z3 32, CVC5 30
- dumps (= executed smt calls observed): 900
- per-dump status: error 2, timeout 11, unknown 4, unsat 883

## Sent vs used (unsat dumps only)
- asserts sent per call: mean 97.3, median 61, min 3, max 2088
- env lemmas SENT per call: mean 36.2, median 16, min 0, max 1322
- env lemmas (Top_*) USED (in core): mean 0.8, median 0, min 0, max 10
- calls whose core has 0 env lemmas: 495
- calls that SENT >0 env lemmas: 750; among them sent mean 42.6, median 20, min 1, max 1322 vs used mean 1.0, median 1, min 0, max 10

## Cross-solver comparison (calls unsat under both)
- joint calls: 284; identical env-lemma cores: 270; differing: 14

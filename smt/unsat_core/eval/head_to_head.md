loaded systems: llm8, lexical, filt_rev, random

## Per-(goal, solver): success rate and time (failures charged at cost)

### Z3 (n=208 matched replays)
  EC default (full send)     ok=199/208 (95.7%)  total=  109.0s  median=33ms
  oracle (unsat core)        ok=208/208 (100.0%) total=   10.7s  median=31ms
  llm8 top-8                 ok=194/208 (93.3%)  total=  133.1s  median=31ms
  lexical top-8              ok=119/208 (57.2%)  total=  700.1s  median=33ms
  filt_rev top-8             ok=188/208 (90.4%)  total=  211.8s  median=30ms
  random top-8               ok=102/208 (49.0%)  total=  813.2s  median=39ms

### CVC5 (n=199 matched replays)
  EC default (full send)     ok=191/199 (96.0%)  total=   39.2s  median=28ms
  oracle (unsat core)        ok=191/199 (96.0%)  total=   17.2s  median=24ms
  llm8 top-8                 ok=175/199 (87.9%)  total=  117.2s  median=24ms
  lexical top-8              ok=87/199 (43.7%)   total=  257.4s  median=22ms
  filt_rev top-8             ok=161/199 (80.9%)  total=  197.5s  median=25ms
  random top-8               ok=76/199 (38.2%)   total=  273.0s  median=21ms

## Portfolio view (EC parallel-all: goal ok if ANY solver unsat; time = min over launched solvers)
  EC default (full send)     ok=265/266 (99.6%)  total=   22.5s  median=28ms
  llm8 top-8                 ok=248/266 (93.2%)  total=  163.9s  median=26ms
  oracle                     ok=266/266 (100.0%) total=    7.2s  median=25ms
  lexical top-8              ok=132/266 (49.6%)  total=  875.5s  median=29ms
  filt_rev top-8             ok=230/266 (86.5%)  total=  328.2s  median=26ms
  random top-8               ok=108/266 (40.6%)  total= 1002.1s  median=34ms

## Hybrid (add pinned arms to the portfolio)
- Z3: union(full||llm8) 202/208 (97.1%), parallel-total 69.6s (full alone: 109.0s)
- CVC5: union 194/199 (97.5%), 17.4s (39.2s)
- goal-level: EC default (Z3||CVC5 full) 265/266 (99.6%) @ 22.5s -> +llm8 arms 266/266 (100.0%) @ 7.6s

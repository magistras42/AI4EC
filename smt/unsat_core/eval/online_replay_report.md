# Online replay validation (LLM top-8 pins)

## Z3 (208 goals matched, 199 with full-replay unsat)
- llm8 reproduces: 191/199 (96.0%)
- oracle reproduces: 199/199 (100.0%)
- offline full-coverage HIT: 91.5% -> online ok 100.0% | offline MISS -> online ok 52.9%
- full   time: median 32ms, mean 78ms  (n=191)
- llm8   time: median 30ms, mean 31ms  (n=191)
- oracle time: median 31ms, mean 31ms  (n=191)

## CVC5 (199 goals matched, 191 with full-replay unsat)
- llm8 reproduces: 172/191 (90.1%)
- oracle reproduces: 188/191 (98.4%)
- offline full-coverage HIT: 83.8% -> online ok 98.1% | offline MISS -> online ok 48.4%
- full   time: median 27.0ms, mean 42ms  (n=172)
- llm8   time: median 23.0ms, mean 28ms  (n=172)
- oracle time: median 24.0ms, mean 28ms  (n=172)

- cross-solver time ratio (full, n=125): median 1.37x, mean 2.44x
- cross-solver time ratio (llm8, n=121): median 1.38x, mean 1.76x

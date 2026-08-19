# AI4EC — Automating EasyCrypt Proof Effort

Experiments on reducing the bottlenecks of EasyCrypt proof work.
The central topic is **automatic hint selection for `smt()`**: extract the
lemmas the solver actually used (unsat cores) as ground-truth labels, then
recommend top-k lemmas per goal by embedding similarity to recover `smt`
calls that time out.

- **Experiment report: [`RESULTS.md`](RESULTS.md)** (problem → method → results → analysis)
- **Pipeline code & reproduction commands: [`unsat_core/README.md`](unsat_core/README.md)**

## Directories

| path | contents |
|---|---|
| `unsat_core/` | The experiment pipeline: unsat-core label extraction → datasets → embedding ranking → replay/EC-path validation (7 scripts) + result data |
| `smt_benchmark/` | Evaluation corpus (48 `.ec` files: 45 from the official EasyCrypt `examples/` + 4 from The Joy of EasyCrypt) + per-solver compile-timing baseline — a dependency of `unsat_core/` |

## Requirements (unsat_core experiments)

- `source ~/ec-env.sh` — easycrypt + z3/cvc5/alt-ergo (no-root install)
- Regenerating dumps requires a local 3-line EasyCrypt patch (see the top of
  `unsat_core/README.md`); scoring and tables reproduce from the shipped
  dumps/results without the patch
- Embedding (GPU): a Python env with torch/transformers, offline HF cache

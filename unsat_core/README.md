# unsat_core — automatic smt() hint selection: unsat-core labels + embedding ranking

`smt()` times out because it transmits thousands of env lemmas. This pipeline
extracts "the lemmas the solver actually used" (unsat cores) as ground-truth
labels and recommends top-k lemmas per goal by embedding similarity. The
experiment narrative and result tables are in `../RESULTS.md`
(problem → method → results → analysis).

> **Local patch dependency**: regenerating dumps (`extract_cores.py`,
> `build_datasets.py strip`, `replay_eval.py dumps`) requires a 3-line patch
> to `src/ecProvers.ml` in the local EasyCrypt build (with EC_SMT_DEBUG set,
> write numbered `<Prover>.NNNN.smt` files per call). Based on upstream HEAD
> 7e192dd. Scoring and tables reproduce from the already-generated
> dumps/result files without the patch.

## File layout (pipeline order = paper order)

| file | purpose | env |
|---|---|---|
| `smtcore.py` | [shared] SMT-LIB dump / EC source text-parsing primitives + PoC CLI | stdlib |
| `run_unsat_core_poc.sh` | [demo] single-file end-to-end: dump→name→core→demangle→pin | EC env |
| `extract_cores.py` | [1 labels] 48 corpus files × Z3/CVC5 → `results/cores.jsonl` | EC env |
| `build_datasets.py` | [2 datasets] `build` 266-goal eval.jsonl / `strip` 33-goal failing set / `baselines` + `report` Table B / `score` | EC env for strip only |
| `embed_rank.py` | [3 method] embedding ranking: `rank33` / `recall266` → preds json | GPU (HF) |
| `replay_eval.py` | [4 validation, replay axis] `dumps` / `run` full·top-k·oracle / `kablation` k sweep / `portfolio` table | EC env |
| `ec_path_eval.py` | [5 validation, EC axis] site pins + revert loop on real easycrypt compilation | EC env |

Environment: `source ~/ec-env.sh` (easycrypt + z3/cvc5); GPU steps need a
Python env with torch/transformers (offline HF cache is fine).

## Reproduction commands (paper result → command)

```bash
# 0. demo (List.size_cat: 430 lemmas sent → core of 1; pinning smt(size_cat): 1472→576 ms)
./run_unsat_core_poc.sh

# 1. ground-truth labels (done; results/cores.jsonl: 883 cores, mean 42.6 sent → 1.0 used)
python3 extract_cores.py --out results --jobs 8

# 2. datasets (done: eval/eval.jsonl 266 goals; stripped_workdir/failing_goals.json 33 goals)
python3 build_datasets.py build
python3 build_datasets.py strip --workdir stripped_workdir

# 3. embedding ranking. rank33 -> Table A preds; recall266 -> Table B
#    (ranks the FULL bare-smt pool by default; --eos = RESULTS §3.6 ablation)
python embed_rank.py rank33 \
    --workdir stripped_workdir --model Qwen/Qwen3-Embedding-4B \
    --out eval/preds_embed_top32_qe4.json
python embed_rank.py recall266 --model Qwen/Qwen3-Embedding-4B
python3 build_datasets.py baselines     # random/lexical rows of Table B
python3 build_datasets.py report        # prints the combined Table B

# 4-replay. Table A (k sweep) / RESULTS §3.5 portfolio table
python3 replay_eval.py kablation --workdir stripped_workdir \
    --preds eval/preds_embed_top32_qe4.json
python3 replay_eval.py run --dumpdir replay_dumps_backup \
    --preds eval/preds_armpicks_qe4.json --topk 8 \
    --out eval/online_replay_embed8_qe4.jsonl
python3 replay_eval.py portfolio

# 4-EC. RESULTS §3.4 EC-path validation (site pins: k=2–4 recommended)
python3 ec_path_eval.py --files examples/br93.ec --systems oracle embed \
    --preds embed=eval/preds_armpicks_qe4.json --topk 4
```

## Key facts (probe-verified)

- **Dumps**: `EC_SMT_DEBUG=1` → `Z3.smt`/`CVC5.smt` (SMT-LIB2) and
  `Alt-Ergo.smt` (native syntax) in cwd. Unpatched builds **overwrite** per
  call, keeping only the last one.
  Side note: `smt dump in="f".` / `EC_WHY3=f` give per-call files but in
  Why3 syntax, not SMT-LIB.
- **`smt(hints)` is a restrict**: giving wantedlemmas forces maxlemmas=0, so
  exactly those lemmas + goal-local hypotheses + ~19 builtin axioms are sent
  (measured: bare `smt` 243 axioms vs `smt(aux1)` 20).
  However, on the real EC path Why3 auto-adds hint-closure `'def` axioms per
  hint → site pins should use k=2–4.
- **`smt()` ≠ bare `smt`**: empty parentheses send ZERO env lemmas (bare
  uses the relevancy filter's selection).
- **Naming is mandatory**: both Z3 and CVC5 **silently omit** unnamed asserts
  from cores even when essential → every assert must be named
  (`smtcore.py name` does this).
- **Alt-Ergo 2.4.3 cores unusable**: SMT-LIB2 `get-unsat-core` unimplemented
  and `--unsat-core` has broken name mapping → extraction uses Z3/CVC5 only.
- **Demangling**: `Top.List.size_cat` → `Top_List_size_cat` (dots →
  underscores, `Top_` prefix). Operators are transliterated (`++`→`plpl`,
  …). Duplicate instances are disambiguated with `__k` suffixes.
- **Replay outcome ≠ EC outcome**: core options / direct replay change
  solver preprocessing (both directions observed). The goal text is a
  record's identity (call numbers are not 1:1 with source sites).

## Data directories

Code-cleanup history (2026-08-05/06): four LLM-pick generator scripts were
deleted (unused by this method) and the rest was restructured into the seven
files above (behavioral equivalence verified: byte-identical score/portfolio
tables pre/post + a GPU rerun of rank33 matching 33/33). On 2026-08-06 the
offline recall experiment was redesigned to rank the full bare-transmission
pool; the earlier transmitted-pool ranking survives only as the portfolio
arm's picks (`preds_armpicks_qe4.json`).

- `results/` — cores.jsonl (ground-truth labels), runs.csv, SUMMARY.md
- `eval/` — eval.jsonl (266 labeled goals);
  `preds_embed_top32_{minilm,bgem3,qe06,qe4,qe8,qe4eos}.json` (33-goal
  rankings for Table A; the untagged `preds_embed_top32.json` is a legacy
  qe06 file from the initial implementation — not used in any table);
  `fullenv_{random,lexical,minilm,bgem3,qe06,qe4,qe4eos,qe8}.json` (Table B
  rows: full-recall over the bare-transmission pools) with the matching
  `preds_evalcands_*.json` byproducts;
  `preds_armpicks_qe4.json` (ranking of the labeled calls' transmitted env
  — feeds the §3.5 portfolio arm; regenerate via
  `embed_rank.py recall266 --dumpdir replay_dumps_backup`);
  `online_replay_embed{8,16}_qe4.jsonl` (replay results),
  `online_results.json` (EC-path)
- `stripped_workdir/` — the 33-goal set (failing_goals.json), dumps,
  `k_ablation_embed{,_minilm,_bgem3,_qe06,_qe4,_qe8,_qe4eos}.json`,
  `k_ablation_embed_qe4_ksweep.json` (117M)
- `replay_dumps_backup/` — 855 per-call dumps for the 266-goal replays (40M)

## Limitations

- `run_unsat_core_poc.sh` targets only a file's last smt call; rewriting
  bare+option forms like `smt 30.` is unsupported.
- `ec_path_eval.py` demangling is dictionary suffix-matching — names absent
  from the dictionary are counted as dropped.

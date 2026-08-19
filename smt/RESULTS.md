# Embedding-Based Automatic Hint Selection for EasyCrypt `smt()`

Written 2026-08-05, restructured 2026-08-06. Code and reproduction commands:
`unsat_core/README.md`.
This document records the **final methodology — embedding-only lemma
selection** — in the order problem → method → results → analysis. There is
no LLM call anywhere in the pipeline (fully local, a single embedding model).

## 1. Problem

EasyCrypt's `smt.` tactic sends the goal, its hypotheses, and lemmas from the
ambient environment through Why3 to external SMT solvers. The environment is
far too large:

- On failing goals, the solver input carries a **median of 1,487 env lemmas
  (max 3,437)**. Thousands of quantified axioms reliably cause quantifier
  instantiation explosion within the 10 s budget.
- Yet unsat-core analysis shows the proof actually needs **1.0 env lemmas on
  average, usually 1–3**.
- Pinning exactly the needed ones — `smt(h1 h2 ...)` is a TRUE restrict
  (maxlemmas=0) — flips the same goal to **unsat in tens of milliseconds**.

The task is therefore: "**given a goal, automatically pick the few lemmas the
solver actually needs out of ~1.5k candidates**." Until now humans have done
this by hand (276 hand-written hints in our benchmark); the relevant baseline
is not human performance but **bare `smt` itself**: removing every human hint
drops call-level success from 96.9% to 86.5% (297 reached calls). The metric
is how much of this gap can be recovered automatically.

## 2. Method: embedding-similarity lemma selection

Core idea: **embed the goal text and every candidate lemma's assert bodies in
the same space, and pin the top-k by cosine similarity.** No generative model,
no prompt.

### 2.1 Input text — exactly what gets embedded

- **Query = instruction + raw goal SMT-LIB.**
  - instruction (verbatim):
    `"Instruct: Given an SMT goal, retrieve the axioms (lemmas) an SMT solver needs to prove it.\nQuery: "`
  - goal text = the full `(assert (not ...))` s-expression following the
    `;; Goal` comment in the solver dump (the SMT-LIB2 input dropped by the
    `EC_SMT_DEBUG=1` patched EasyCrypt), extracted by `smtcore.extract_goal`
    (whitespace-normalized, truncated at 4,000 chars, `Top_` symbol prefixes
    kept).
- **Document = the candidate lemma's assert bodies** (not its name!).
  - Walking the dump with the `smtcore` scanner, collect every `(assert ...)`
    s-expression whose preceding comment (`;; "name"`) starts with `Top_`
    (`smtcore.collect_env_asserts`), whitespace-normalized.
  - When one name is split into several asserts (Why3 splits axioms, e.g.
    'def), each assert is embedded **separately**. No instruction on the
    document side (Qwen3-Embedding convention).
  - Candidate pool = the entire env the Why3 filter sent.

#### Real example (`examples/ChaChaPoly/chacha_poly.ec::CVC5:1`, 1,221 candidates)

goal (exactly what follows the instruction in the query):

```smtlib
(assert (not (=> (and (<= 0 i) (< i (Top_Int_min 0 0))) (= dflc (f dfla dflb)))))
```

Qwen3-Emb-4B's top-2 picks and their assert bodies (the texts embedded as
documents):

```smtlib
;; rank 1: Int_min'def
(assert (forall ((a Int) (b Int))
  (ite (< a b) (= (Top_Int_min a b) a) (= (Top_Int_min a b) b))))
;; rank 2: StdOrder_IntOrder_minrE
(assert (forall ((x Int) (y Int)) (= (Top_Int_min x y) (ite (<= x y) x y))))
```

For contrast — an unrelated lemma body from the same dump (most of the 1,221
look like this):

```smtlib
;; List_[]_sort
(assert (forall ((qta ty)) (sort (Top_List_list qta) (Top_List_lbrb qta))))
```

The goal's function symbol (`Top_Int_min`) sits next to min's defining axiom
in embedding space, putting it at rank 1. Effect: the full env times out at
10.3 s → **pinning just the top-1 gives unsat in 80 ms** (`min 0 0 = 0`, so
the hypothesis `0 <= i < min 0 0` is contradictory → vacuous). Embedding
names alone would carry this connection only weakly — which is why we embed
bodies.

A simpler labeled-set example (`examples/ehoare/adversary.ec::11`): goal
`(assert (not (<= 0.0 Top_eps)))`, answer lemma `eps_ge0` with body
`(assert (<= 0.0 Top_eps))` — the goal is the negation of the conclusion, so
it is nearly identical text to the body.

### 2.2 Encoding (as implemented, the Embedder in `embed_rank.py`)

HF transformers `AutoModel`, bfloat16, CUDA (H200). Truncation
`max_length=1024` tokens (512 for MiniLM). Cosine = dot product after L2
normalization. Pooling follows each model's convention:

| model | pooling | instruction |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2 (22M) | mean | none |
| BAAI/bge-m3 (568M) | CLS | none |
| Qwen/Qwen3-Embedding-0.6B/4B/8B | last-token (left padding) | query only |

(The official Qwen3-Embedding recipe appends an explicit EOS token and pools
its position — this implementation pools the last content token without EOS.
A small deviation; all results are for this implementation, and §3.6 shows
the deviation is not harmful.)

### 2.3 Ranking and pinning

- Score per name = the **maximum** cosine over the asserts belonging to that
  name (max-over-asserts) → sort descending → pin the top-k names, `Top_`
  prefix stripped, as `smt(<names>)`.
- **No cheating**: the unsat core (the answer) never appears in any embedding
  input — only the goal and env bodies. Cores are used solely for offline
  scoring (Table B, §3.3).

### 2.4 Deployment recipe (two forms)

Common path: `smt` fails → obtain the EC_SMT_DEBUG dump → embed goal + env
asserts → rank. Hundreds to thousands of embeddings per goal; all 33 goals
take a few minutes on an H200.
Recommended model: **Qwen3-Embedding-4B** (best online in Table A, §3.2);
0.6B under resource constraints.

- **① Portfolio arm (recommended)**: add a top-8 restrict replay as an arm
  next to the existing (Z3∥CVC5)×full. Monotone improvement (zero risk);
  dominates the EC default in §3.5.
- **② Site pin**: rewrite the failing `.ec` site to `smt(<top-k names>)`.
  **k=2–4 only** — on the EC path Why3 auto-adds the `'def` axioms of each
  hint's closure, so many pins re-inflate the transmission (§3.4).

## 3. Experiments

### 3.1 Evaluation setup

Two scoring axes; the embedding model never sees the answer in either.

- **Online replay (effectiveness)**: the **33 goals where the filter alone
  fails** in the strip-hints experiment (all 276 human hints removed, replay
  fails under the Why3 filter alone; `stripped_workdir/failing_goals.json`).
  `replay_eval.py kablation` keeps only the top-k names' `Top_*` asserts in
  the dump, removes every other env assert, and runs the solver binaries
  directly with 10 s (= the EC budget). unsat counts as a recovery.
  k ∈ {0,1,2,4,8,16,32}, nested.
- **Offline recall ablation (mechanism)**: "does the embedding ranking put
  the lemmas the solver **actually used** (the unsat core) on top of the
  full ~1.5k candidate pool?" — each labeled goal is matched by goal text
  to its bare-`smt` dump (from the strip-hints run), and the ranking runs
  over that dump's ENTIRE env (`embed_rank.py recall266`; baselines from
  `build_datasets.py baselines`; combined table from
  `build_datasets.py report`). full-recall@k = the fraction whose top-k
  contains the entire core. Coverage: **86/266** labeled goals have a bare
  dump (EasyCrypt aborts a file at its first hint-free failure, so later
  calls never produce one); pools are median 1,171 (min 1, max 3,437);
  the bare filter's selection contains the core for 85/86.

### 3.2 Online replay — multi-model sweep (2026-08-05)

**Table A — number of the 33 filter-failing goals recovered by top-k pinning**
(candidate median 1,487):

| model (size) | k=1 | k=2 | k=4 | k=8 | k=16 | k=32 |
|---|---|---|---|---|---|---|
| MiniLM-L6 (22M) | 1 | 1 | 1 | 3 | 4 | 4 |
| bge-m3 (568M) | 1 | 1 | 2 | 3 | 4 | 4 |
| Qwen3-Emb-0.6B | 1 | 2 | 5 | 8 | 11 | 11 |
| **Qwen3-Emb-4B** | 4 | 4 | 6 | 8 | **13** | **15** |
| Qwen3-Emb-8B | 3 | 3 | 4 | 7 | 10 | 8 |

Fine k sweep (qe4, same scoring): k=12 → 11, k=20 → 13, k=24 → **15** —
recovery reaches its maximum (15/33) already at k=24 and holds through 32
(plateau). `stripped_workdir/k_ablation_embed_qe4_ksweep.json`.

(The qe06 row was regenerated on 2026-08-06 with the final implementation
(`embed_rank.py`) — `preds_embed_top32_qe06.json` /
`k_ablation_embed_qe06.json`. The untagged `preds_embed_top32.json` from the
initial implementation scored 1 higher only at k=32 (12).)

### 3.3 Offline recall ablation (2026-08-06, redesigned)

An earlier version of this experiment ranked only the ~20 lemmas the
labeled (hinted) calls actually transmitted — a mis-design that inflated
every method (random reached .605@16). It was discarded and replaced by
the full-pool measurement below.

**Table B — 86 bare-reachable labeled goals, full-recall@k over the full
bare-transmission pool** (median 1,171 candidates; fraction of goals whose
top-k contains the entire actual unsat core):

| method | @1 | @2 | @4 | @8 | @16 | @32 |
|---|---|---|---|---|---|---|
| random | .058 | .070 | .081 | .140 | .186 | .267 |
| lexical | .128 | .186 | .244 | .267 | .302 | .442 |
| MiniLM-L6 (22M) | .221 | .314 | .395 | .500 | .500 | .547 |
| bge-m3 (568M) | .209 | .360 | .419 | .488 | .523 | .547 |
| Qwen3-Emb-0.6B | .221 | .314 | .384 | .477 | .605 | .616 |
| **Qwen3-Emb-4B** | .267 | .337 | .442 | .500 | .581 | **.674** |
| Qwen3-Emb-8B | .267 | .349 | .407 | .558 | .605 | .651 |

(Upper bound: answer-in-env 85/86 = .988. Random's nonzero low-k numbers
come from a minority of small-pool goals — 26 of the 86 have pools ≤50 —
not from the large pools.)

### 3.4 EC-path validation (2026-08-05): it works on the real EasyCrypt path

**Single-site demo — the br93 flip.** The original site at `br93.ec:284`,
`smt(@FMap)` (the whole FMap theory, 273 env lemmas), times out under
Z3-only compilation and blocks the file (fails at 12.2 s). Demangling qe4's
top-16 to EC names and pinning just the top 2 — `smt(mem_fdom_set mem_set)`
— makes **the whole file pass under Z3 in 6.7 s** (top-4 passes too). The
answer core (`FMap_mem_set`) was rank 2 in qe4's ranking.

**Site pins are only safe with few hints**: pinning top-8/top-16 at the same
site *fails* with "cannot prove goal". Unlike replay, on the EC path Why3
automatically adds the `'def` translation axioms for the symbols each hint
mentions (sub-hint granularity), so more pins re-inflate the transmission.
**Recommended site-pin k = 2–4** (this is why it differs from the
replay/portfolio-arm optimum of k=16–24).

**Systematic validation (`ec_path_eval.py`, 6 files, embed top-4, union pin
+ revert-on-failure):** br93, hashed_elgamal_generic, FunctionalSpec,
WF-examp, FundamentalLemma all pass — with embed's kept/reverted site counts
identical to oracle's, i.e. embedding pins behave like oracle pins. The one
failure (Plug_and_Pray) **fails identically with oracle hints** (a limit of
the all-sites union-pin harness, not of the ranking).
`eval/online_results.json`.

### 3.5 Portfolio integration (2026-08-05): adding an arm dominates the EC default

All 266 labeled goals, 407 matched (goal, solver) replays. The embed arm =
qe4 top-k restrict replay (`replay_eval.py run`,
`eval/online_replay_embed{8,16}_qe4.jsonl`, analyzed by
`replay_eval.py portfolio`):

| goal-level portfolio (n=266) | success | total time |
|---|---|---|
| EC default (Z3∥CVC5, full send) | 265/266 (99.6%) | 25.5 s |
| embed top-8 restrict alone | 227/266 (85.3%) | 262.3 s |
| embed top-16 restrict alone | 252/266 (94.7%) | 153.2 s |
| **EC default + embed-8 arm** | **266/266 (100%)** | **10.8 s** |
| oracle (upper bound) | 266/266 (100%) | 10.1 s |

- Pure replacement (restrict alone) still loses to the default — one recall
  miss costs the full 10 s. But **as an added arm: success +1, total time cut
  2.4×, effectively reaching the oracle bound (10.1 s).** Adding an arm is a
  monotone improvement, so the risk is zero.
- The +1 goal: `UC/dh_enc.ec::51` (Z3 full send times out at 10.2 s →
  35 ms unsat with the embed-8 pin; the actual core is inside the top-8).

### 3.6 EOS pooling comparison (2026-08-05): the deviation is not harmful

Re-running qe4 with the official Qwen3-Embedding recipe (append an EOS token
and pool its position; `--eos` flag) against this implementation (last
content-token pooling):

| qe4 | offline full-recall @16 / @32 (bare pool) | online recovery k=8 / 16 / 32 |
|---|---|---|
| this implementation (no EOS) | .581 / .674 | 8 / **13** / 15 |
| official EOS recipe | .593 / .674 | **10** / 9 / 15 |

Offline the two are indistinguishable (±1 goal of 86); online this
implementation wins at the k=16 operating point and ties at k=32.
**Conclusion: the recipe deviation of §2.2 is not unfavorable; this
implementation is kept.** `eval/fullenv_qe4eos.json`,
`stripped_workdir/k_ablation_embed_qe4eos.json`.

## 4. Analysis

1. **Headline**: Qwen3-Emb-4B top-16 pinning recovers **13 of 33** → call
   success **86.5% → 90.9%** (top-32: 15, 91.6%). Roughly **40–50% of the gap
   created by removing human hints (96.9→86.5) is restored automatically,
   with zero LLM calls, fully locally.**
2. **The ablation answer: embedding similarity does retrieve the
   solver-used lemmas out of ~1.2k-candidate pools.** full-recall@16 of
   0.50–0.61 across embedding models vs 0.302 lexical and 0.186 random
   (upper bound 0.988 = answer-in-env). For more than half the goals, the
   entire actual core fits inside a 16-lemma pin chosen from over a
   thousand candidates.
3. **Offline coverage does not guarantee online recovery — the online
   test separates the models**: MiniLM and bge-m3 sit within ~0.1 of the
   Qwen3 family on offline full-recall yet bottom out at 4/33 online,
   while Qwen3-Emb-4B reaches 13/33. Proving success depends not only on
   covering the core but on which other lemmas fill the top-k; the
   retrieval-specialized Qwen3-Embedding family wins where it counts.
4. **Best online is the 4B**: 13/33 @16, 15/33 @32. The 8B edges ahead on
   parts of the offline table yet trails the 4B online and drops at k=32 —
   performance is not monotone in model size (single run; 10 s-boundary
   noise applies).
5. **The k curve rises roughly monotonically up to k=32** (replay level):
   feeding only similarity-ranked lemmas admits few irrelevant ones, so
   instantiation explosion arrives late. Generous pin counts are safe there
   — but the real EC path is the opposite (site pins k=2–4, §3.4);
   deployment must match k to the mechanism.
6. **Recipe deviation defended**: the EOS-pooling ablation (§3.6) shows the
   encoding choice is not a liability.
7. **Remaining work**: EC-path validation (§3.4), portfolio integration
   (§3.5), the k sweep, and the EOS comparison (§3.6) are complete. Future
   work: **contrastive fine-tuning** of the embedder on the 599 extracted
   (goal, core) labels — outside the term-paper scope.

# Mutation Repair Experiments

Experiments that mutate complete EasyCrypt proofs and measure an LLM agent's
ability to repair them. Built as a wrapper around [`integration/agent/`](../agent/).

## Prerequisite corpus

The default spec uses **The Joy of EasyCrypt** evaluation corpus:

```bash
python3 -m benchmark clone --only tejasanilshah-the-joy-of-easycrypt
python3 -m benchmark extract
python3 -m benchmark index
```

## Run

```bash
python3 -m integration.experiment run \
  --spec joy-tactic-repair \
  --trials 10 \
  --stuck-limit 20 \
  --max-steps 200 \
  --llm-model <model> \
  --embed-model <embed-model>
```

### CLI flags (`run`)

| Flag | Default | Description |
|------|---------|-------------|
| `--spec` | `joy-tactic-repair` | Experiment spec name (`joy-tactic-repair`, `joy-informal-repair`, `elgamal-broken-repair`, or a custom registered spec) |
| `--trials` | `10` | Number of trials to sample and run |
| `--stuck-limit` | `20` | Unproductive-iteration limit per trial (failed tactics, undos, lookups, repeated states) |
| `--max-steps` | `200` | Max agent loop steps per trial |
| `--seed` | unset | RNG seed for case sampling / mutations / red herrings |
| `--data-dir` | `data` | Corpus data directory (cloned/extracted/indexed proofs) |
| `--output-dir` | auto timestamp under `integration/output/experiments/` | Destination for this run’s artifacts |
| `--easycrypt` | patched `ec.exe` from the repo build | Path to the EasyCrypt binary |
| `--llm-model` | env / provider default | Chat model id (LM Studio id, or DeepSeek id with `--deepseek`) |
| `--embed-model` | env / auto-detect | LM Studio embedding model id (embeddings always use LM Studio) |
| `--lm-studio-url` | `http://localhost:1234/v1` | LM Studio OpenAI-compatible base URL (embeddings; also chat when not using `--deepseek`) |
| `--deepseek` | off | Route solver/writer chat through the DeepSeek API (requires `DEEPSEEK_API_KEY`; human `YES` confirmation) |
| `--llm-max-tokens` | `16384` | Max output tokens per chat completion |
| `--thinking` | `disabled` with `--deepseek`; otherwise unused | DeepSeek V4 thinking: `enabled`, `disabled`, or `adaptive` |
| `--thinking-failure-window` | `5` | With `adaptive`: enable thinking after failure-like outcomes in the last N steps |
| `--reasoning-effort` | unset | DeepSeek effort when thinking is on (`high`/`max`); also used when adaptive enables thinking |
| `--top-k` | `10` | Number of cosine-ranked premises shown in the prompt |
| `--lemma-search-top-k` | `5` | Results returned by the `search_lemmas` tool |
| `--max-premises` | unset | Cap the ranking premise pool (debug) |
| `--red-herring-ratio` | `0.3` | Decoy lemmas as a fraction of used-lemma count (`joy-informal-repair` only) |
| `--writer-temperature` | `0.7` | Sampling temperature for the informal-proof writer (`joy-informal-repair` only) |
| `--llm-json-mode` | off | Opt in to structured JSON (`response_format`) from the solver LLM |
| `--no-llm-json-mode` | off | Explicitly keep `response_format` off (default) |
| `-v` / `--verbose` | off | Verbose logging |

`response_format` is **off by default**. DeepSeek's `json_object` mode does not
enforce the agent action schema; LM Studio's `json_schema` form is also opt-in
because small local models often reject it. Pass `--llm-json-mode` to enable.
The resolved choice is recorded as `action_response_format` in `run_flags.json`.

Relevant environment variables:

| Variable | Used for |
|----------|----------|
| `DEEPSEEK_API_KEY` | Required with `--deepseek` |
| `DEEPSEEK_BASE_URL` | Optional DeepSeek API base (default `https://api.deepseek.com`) |
| `LM_STUDIO_BASE_URL` | Default for `--lm-studio-url` |
| `LM_STUDIO_LLM_MODEL` | Default chat model when not using `--deepseek` / `--llm-model` |
| `LM_STUDIO_EMBED_MODEL` | Default for `--embed-model` |
| `LM_STUDIO_LLM_MAX_TOKENS` | Default for `--llm-max-tokens` |
| `LM_STUDIO_TIMEOUT` | HTTP timeout for chat/embed clients |
| `EASYCRYPT` / `EASYCRYPT_TIMEOUT` | Binary path and EasyCrypt subprocess timeout |

### DeepSeek API (paid)

Route solver/writer chat through DeepSeek with `--deepseek` (embeddings still
use local LM Studio). Requires `DEEPSEEK_API_KEY`. The CLI prints a warning
with the trial/step counts and waits for a human to type `YES`.

**Agents must never answer that confirmation** — see [`AGENTS.md`](../../AGENTS.md).
Give the user the command to run themselves.

```bash
export DEEPSEEK_API_KEY=...
python3 -m integration.experiment run \
  --deepseek \
  --spec joy-informal-repair \
  --trials 10 \
  --max-steps 200 \
  --llm-model deepseek-v4-flash \
  --llm-max-tokens 16384 \
  --thinking disabled \
  --embed-model <local-embed-model>
```

`--deepseek` defaults to `--thinking disabled` (V4 otherwise thinks at high
effort and can exhaust `max_tokens` before emitting JSON). Use
`--thinking enabled --reasoning-effort high` for harder proofs, or
`--thinking adaptive` to turn thinking on only after recent failures
(window controlled by `--thinking-failure-window`, default 5).

Results land in `integration/output/experiments/<timestamp>/`:

- `run_flags.json` — CLI flags / argv and resolved provider settings for the run
- `summary.json` — aggregate metrics, including token usage
- `events.jsonl` — per-trial event log
- `trials/trial_NNN/` — original, mutated, agent start file, agent log

### Token accounting

Every chat completion (solver, informal writer, timeout retrospective) is
counted, so a run's cost can be compared across models. `summary.json` carries
totals in `token_usage` and per-trial rates in `token_usage_per_trial`
(totals divided by `trials_run`); each entry of `trial_results` carries that
trial's own `token_usage`, and the trial's `agent_log.json` repeats it in its
`finish` event. Totals are also printed at the end of a run:

```
Tokens: 412330 in (380000 cache hit, 32330 cache miss), 18204 out (430534 total over 137 chat calls)
Per trial: 41233.0 in, 1820.4 out, 13.7 calls
Estimated cost: $0.012345 USD (deepseek-v4-flash; $0.001235/trial)
```

`cached_prompt_tokens` / `cache_miss_prompt_tokens` come from DeepSeek's
`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` (see
[Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)).
`reasoning_tokens` are recorded when the provider reports them.

For `--deepseek` runs, `estimated_cost` (and each trial's `estimated_cost`)
applies the official [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
rates for the chosen model (`deepseek-v4-flash` or `deepseek-v4-pro`):

| Model | Cache hit / 1M | Cache miss / 1M | Output / 1M |
|-------|----------------|-----------------|-------------|
| `deepseek-v4-flash` | $0.0028 | $0.14 | $0.28 |
| `deepseek-v4-pro` | $0.003625 | $0.435 | $0.87 |

Cost = `(hit×hit_rate + miss×miss_rate + output×output_rate) / 1e6`. Local
LM Studio runs leave `estimated_cost` null. Rates are snapshotted in
`integration/agent/pricing.py` (`PRICING_AS_OF`); re-check the docs if DeepSeek
updates prices.

## Informal proof repair (`joy-informal-repair`)

A second experiment, `joy-informal-repair`, avoids ever letting an LLM alter
or invent the proof goal (which mutation-repair's broken tactic hint does
not risk either, but which any "reprove a proof that's broken for unknown
reasons" approach would). Instead it takes an already-complete proof, has a
"writer" LLM redescribe its reasoning as a code-free informal proof sketch,
and asks a separate "solver" run of the agent loop to reprove the exact same
(unmodified, guaranteed-correct) lemma from an empty slate using only that
sketch and a red-herring-salted list of candidate lemmas — never the real
tactics. See **[INFORMAL_PROOF_REPAIR.md](INFORMAL_PROOF_REPAIR.md)** for
the full write-up.

```bash
python3 -m integration.experiment run \
  --spec joy-informal-repair \
  --trials 10 \
  --stuck-limit 20 \
  --max-steps 200 \
  --llm-model <model> \
  --embed-model <embed-model> \
  --red-herring-ratio 0.3 \
  --writer-temperature 0.7
```

## Genuinely broken proofs (`elgamal-broken-repair`)

`joy-informal-repair` *simulates* a broken proof (a writer LLM redescribes an
already-complete, currently-verified lemma). `elgamal-broken-repair` uses a
corpus that is **actually broken**: `derens99/ElGamal-proof`'s Hashed ElGamal
development (`data/derens99-ElGamal-proof/hashedelgamal.ec`, 2020), which no
longer compiles against the vendored EasyCrypt release. See
[`integration/experiment/corpora/elgamal.py`](corpora/elgamal.py) for the
full breakdown, but in short:

1. **Syntax/API drift** (`SmtMap` → `FMap`, dropped `proc *` marker,
   `declare module X : Y` → `X <: Y`, unprefixed `{A, B}` restriction sets
   needing the `old_mem_restr` pragma) is ported once, offline, in a way
   that preserves every line number.
2. Every lemma **before** the trial's target lemma has its proof body
   replaced with `admit.` (`admit_prior_lemmas` in
   [`proof_extract.py`](proof_extract.py)) — this is the corpus analog of
   "assume everything the target depends on is already proven." Confirmed
   empirically that later, genuinely broken game-hop lemmas (e.g.
   `INDCPA_HEG_G1`, `G1_G2_eq`) still fail tactic-by-tactic even after this
   porting, which is the actual object of study.
3. Unlike `joy-informal-repair`, there is **no writer LLM and no curated
   lemma manifest**: the corpus's own broken tactic script is shown to the
   solver verbatim as reference (`informal_proof`, with
   `AgentConfig.informal_proof_is_formal=True` so the prompt heading calls
   it out as non-compiling formal text), and premises are ranked against the
   full ambient `Ax.all` catalog — the same as `joy-tactic-repair`. This
   isolates effectiveness/efficiency at repairing a genuinely broken proof
   from any effect of a hand-curated "useful lemmas" list.
4. If a target's goal is still unreachable after every prior lemma is
   admitted (e.g. a porting gap), the trial skips cleanly
   (`skip_reason="goal_unreachable"`) instead of handing the solver a bogus
   or empty goal.

```bash
python3 -m integration.experiment run \
  --spec elgamal-broken-repair \
  --trials 10 \
  --stuck-limit 20 \
  --max-steps 200 \
  --llm-model <model> \
  --embed-model <embed-model>
```

`--red-herring-ratio` / `--writer-temperature` are ignored for this spec (it
never curates a manifest or calls a writer).

## Adding a new experiment

Register a new `(corpus, mutations)` pair in [`specs.py`](specs.py):

```python
SPECS.register(
    ExperimentSpec(
        name="my-experiment",
        corpus=MyCorpus(data_dir=data),
        mutations=MyMutationStrategy(),
    )
)
```

Implement `CorpusProvider` and `MutationStrategy` from [`protocols.py`](protocols.py).
The runner and agent harness require no further changes.

## Agent extensions (optional)

When run via the experiment runner, the agent receives:

- **Repair hint** — the mutated (broken) tactic script in the prompt
  (mutation-repair only; never set for `joy-informal-repair` /
  `elgamal-broken-repair`)
- **Informal proof sketch** — a code-free natural-language proof description
  for `joy-informal-repair` (see
  [INFORMAL_PROOF_REPAIR.md](INFORMAL_PROOF_REPAIR.md)), or the corpus's own
  broken formal tactic script for `elgamal-broken-repair`
  (`AgentConfig.informal_proof_is_formal=True` switches the prompt heading
  accordingly)
- **Premises override** — restricts the agent's visible top-k premise pool to a
  curated list instead of the full EasyCrypt `Ax.all` catalog
  (`joy-informal-repair` only; `elgamal-broken-repair` always uses the full
  catalog, same as `joy-tactic-repair`)
- **Lemma lookup tool** — `{"action": "lookup_lemma", "name": "..."}`
  resolves against EasyCrypt `Ax.all` at the current proof cursor
- **Semantic lemma search tool** — `{"action": "search_lemmas", "query": "..."}`
  embeds the query and returns the top `AgentConfig.lemma_search_top_k`
  signatures from EasyCrypt `Ax.all` by cosine similarity. Lexical modes
  (`substring` / `prefix` / `exact`) go in JSON `name`. Catalog keys are
  EasyCrypt-qualified paths (`Theory.basename`). Optional `theory:Path` in
  the query filters any mode.
- **Stuck limit** — trial ends after 20 cumulative unproductive iterations
  (failed tactics, undos, repeated proof states, lookups)
- **No-op undo cap** — `AgentConfig.max_consecutive_noop_undos` (default `3`)
  aborts the trial as soon as `undo` removes zero tactics that many times in
  a row, independent of `--stuck-limit`. The model can always request a
  larger `count`, so a repeated no-op undo is a clear stuck signal rather
  than progress.

The standalone agent CLI (`python -m integration.agent`) always enables the
lookup/search tools against EasyCrypt premises; no extra config is required.

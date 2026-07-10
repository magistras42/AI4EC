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

Results land in `integration/output/experiments/<timestamp>/`:

- `summary.json` — aggregate metrics
- `events.jsonl` — per-trial event log
- `trials/trial_NNN/` — original, mutated, agent start file, agent log

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
  --embed-model <embed-model>
```

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
  (mutation-repair only; never set for `joy-informal-repair`)
- **Informal proof sketch** — a code-free natural-language proof description
  (`joy-informal-repair` only; see
  [INFORMAL_PROOF_REPAIR.md](INFORMAL_PROOF_REPAIR.md))
- **Premises override** — restricts the agent's visible premise pool to a
  curated list instead of the full corpus catalog (`joy-informal-repair` only)
- **Lemma lookup tool** — `{"action": "lookup_lemma", "name": "..."}`
- **Stuck limit** — trial ends after 20 cumulative unproductive iterations
  (failed tactics, undos, repeated proof states, lookups)

The standalone agent CLI (`python -m integration.agent`) is unchanged unless
these optional `AgentConfig` fields are set programmatically.

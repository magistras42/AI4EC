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
- **Lemma lookup tool** — `{"action": "lookup_lemma", "name": "..."}`
- **Stuck limit** — trial ends after 20 cumulative unproductive iterations
  (failed tactics, undos, repeated proof states, lookups)

The standalone agent CLI (`python -m integration.agent`) is unchanged unless
these optional `AgentConfig` fields are set programmatically.

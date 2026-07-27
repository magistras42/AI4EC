# Agent instructions (AI4EC)

## DeepSeek API experiments — human confirmation only

DeepSeek chat completions are **paid**. The experiment CLI (`python -m integration.experiment run --deepseek ...`) interactively warns the user and requires typing `YES` before any DeepSeek API calls.

**Agents MUST NEVER accept this confirmation for the user.**

- Do **not** pipe `YES` into the process, use expect/pty automation, or click/type the confirmation on the user's behalf.
- Do **not** set environment tricks or flags that bypass the prompt (there is intentionally no `--yes` / `--force` for DeepSeek).
- Do **not** run a DeepSeek experiment in the background and "handle" the prompt yourself.

When the user asks you to run an experiment that uses DeepSeek, **give them the exact command to run themselves** and stop. Example:

```bash
export DEEPSEEK_API_KEY=...   # if not already set
python -m integration.experiment run \
  --deepseek \
  --spec joy-informal-repair \
  --trials 10 \
  --stuck-limit 20 \
  --max-steps 200 \
  --llm-model deepseek-v4-flash \
  --llm-max-tokens 16384 \
  --thinking disabled \
  --embed-model <local-embed-model>
```

`--deepseek` defaults thinking to `disabled` (cheaper; avoids empty replies from
overthinking). Use `--thinking enabled --reasoning-effort high` only when needed,
or `--thinking adaptive` to enable thinking after recent failures.

They must run it in their own terminal and answer the confirmation prompt personally.

Embeddings still use local LM Studio (`--lm-studio-url` / `LM_STUDIO_BASE_URL`); only solver/writer chat goes to DeepSeek when `--deepseek` is set.

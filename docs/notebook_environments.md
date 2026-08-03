# Notebook Environment Standards

Every notebook in this repository must declare which kernel (virtual environment)
it requires so that any contributor can reproduce results without guesswork.

## Directory layout

```
env/
  <env-name>/
    pyproject.toml   # git-tracked — defines deps
    uv.lock          # git-tracked — pins exact versions
    .venv/           # git-ignored — local virtualenv
```

Each environment lives under `env/<name>/`. The `pyproject.toml` and `uv.lock`
are committed; the `.venv/` directory is generated locally and ignored via
`.gitignore`.

## Setting up an environment

```bash
cd env/<name>
python3 -m uv venv .venv --python 3.11
python3 -m uv sync
.venv/bin/python -m ipykernel install --user \
  --name <name> --display-name "<Display Name>"
```

After installation, select the kernel in VS Code / Jupyter by its display name.

## Notebook requirements

Every notebook **must** include a markdown cell (typically the first or second
cell) stating the required kernel. Use this format:

```markdown
> **Kernel:** `<kernel-name>` — see [`env/<env-dir>/pyproject.toml`](../env/<env-dir>/pyproject.toml) for dependencies.
```

This tells the reader exactly which environment to select and where to find its
dependency specification.

## Available environments

| Kernel name    | Display name  | Directory        | Purpose                              |
|----------------|---------------|------------------|--------------------------------------|
| `ai4ec-agent`  | AI4EC Agent   | `env/agent/`     | Agent experiment notebooks           |

## Adding a new environment

1. Create `env/<name>/pyproject.toml` with the required dependencies.
2. Run `python3 -m uv lock` in that directory to generate `uv.lock`.
3. Follow the setup steps above to create `.venv` and register the kernel.
4. Add the environment to the table above.
5. Ensure the notebook contains the kernel declaration markdown cell.

## Notes

- Environments use `PYTHONPATH` set in the kernel spec to make project-root
  packages (like `integration`) importable without an editable install.
- Keep dependencies minimal — only what the notebooks in that environment
  actually import.
- Pin `requires-python` to the minimum version needed (currently `>=3.11`).

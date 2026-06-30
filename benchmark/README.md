# Benchmark .ec Extraction Pipeline

Build a local EasyCrypt benchmark corpus from the GitHub repositories listed in [`repositories.md`](../repositories.md).

The pipeline has three independent stages. Each stage reads a manifest from the previous one and can be run on its own.

```
repositories.md  →  clone  →  .clone/          →  extract  →  data/  →  index  →  proofs_index.json
```

All generated artifacts live under **gitignored** directories (`.clone/` and `data/`) so extracted code is not committed.

## Prerequisites

- **Python 3.10+** (stdlib only; no pip install required)
- **git** on your `PATH`
- Network access for the clone stage

Run commands from the repository root:

```bash
cd /path/to/AI4EC
```

## Quick start

Run all three stages in sequence:

```bash
python3 -m benchmark all
```

This will:

1. Shallow-clone every valid repo URL in `repositories.md` into `.clone/<owner-repo>/`
2. Copy all `.ec` files into `data/<owner-repo>/`, preserving each file's path within the repo
3. Write `data/proofs_index.json` cataloguing every lemma and axiom

## Stages

### Stage 1 — Clone

```bash
python3 -m benchmark clone
```

Clones repositories with `git clone --depth 1`. Skips repos that are already cloned unless you pass `--force-refresh`.

**Output:** `.clone/clone_manifest.json`

```bash
# Re-clone everything
python3 -m benchmark clone --force-refresh

# Clone only the first 5 repos (useful for testing)
python3 -m benchmark clone --limit 5

# Clone specific repos by slug
python3 -m benchmark clone --only IvanRenison-SimpleORAM-Jasmin,EasyCrypt-easycrypt
```

Repo slugs are `owner-repo`, e.g. `IvanRenison-SimpleORAM-Jasmin` for `https://github.com/IvanRenison/SimpleORAM-Jasmin`.

Invalid URLs (such as `https://github.com/formosa-crypto`, which is an org rather than a repo) are skipped and recorded in the manifest with `status: "skipped"`.

Repos already present under `.clone/<slug>/` are left unchanged and reported as:

```
[info] IvanRenison-SimpleORAM-Jasmin: already present, skipping clone
```

### Stage 2 — Extract

```bash
python3 -m benchmark extract
```

Requires `.clone/clone_manifest.json` from stage 1.

Copies every `.ec` file from successfully cloned repos into `data/<slug>/`, mirroring the relative path inside each clone. This keeps intra-repo `require` dependencies intact (e.g. `require MAC` in `foo/MAC-PRF.ec` still finds `foo/MAC.ec`).

**Output:** `data/extract_manifest.json` and the extracted `.ec` tree under `data/`

### Stage 3 — Index

```bash
python3 -m benchmark index
```

Requires extracted `.ec` files under `data/` and `.clone/clone_manifest.json` (for repo URL and train/eval split metadata).

Scans each `.ec` file for `lemma` and `axiom` declarations and writes a single index file.

**Output:** `data/proofs_index.json`

Each proof entry contains:

| Field | Description |
|-------|-------------|
| `repo_url` | Source GitHub URL |
| `repo_slug` | `owner-repo` directory name under `data/` |
| `split` | `"training"` or `"evaluation"` (from `repositories.md` sections) |
| `file` | Path relative to `data/` |
| `line` | 1-based line where the declaration starts |
| `kind` | `"lemma"` or `"axiom"` |
| `name` | Declaration name |
| `signature` | Full declaration text (multi-line signatures normalized to one line) |

## Shared options

All subcommands accept:

```bash
--repos-file PATH   # default: repositories.md
--clone-dir PATH    # default: .clone
--data-dir PATH     # default: data
```

Example with custom directories:

```bash
python3 -m benchmark clone --clone-dir /tmp/ec-clones --data-dir /tmp/ec-data
python3 -m benchmark extract --clone-dir /tmp/ec-clones --data-dir /tmp/ec-data
python3 -m benchmark index  --clone-dir /tmp/ec-clones --data-dir /tmp/ec-data
```

## Typical workflows

**Full corpus rebuild:**

```bash
python3 -m benchmark clone --force-refresh
python3 -m benchmark extract
python3 -m benchmark index
```

**Refresh index after manual edits under `data/`:**

```bash
python3 -m benchmark index
```

**Add a new repo:** edit `repositories.md`, then:

```bash
python3 -m benchmark clone
python3 -m benchmark extract
python3 -m benchmark index
```

## Tests

```bash
python3 -m pytest benchmark/tests/ -q
```

## Limitations

- The proof scanner is regex-based and may miss exotic declaration syntax.
- The index catalogues proofs; it does not verify that files compile with EasyCrypt.
- Org-only GitHub URLs are skipped (logged in `clone_manifest.json`).
- `.ec` files in git submodules are not fetched unless the upstream repo vendors them.
- Extracted code may carry restrictive licenses; keep `data/` local and gitignored.

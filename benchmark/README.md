# Benchmark .ec Extraction Pipeline

Build a local EasyCrypt benchmark corpus from the GitHub repositories listed in [`repositories.md`](../repositories.md).

The pipeline has four independent stages. Each stage reads a manifest from the previous one and can be run on its own.

```
repositories.md → clone → .clone/ → extract → data/ ─┐
                            │                        ├→ index → proofs_index.json
                            └→ build → build_report.json
```

All generated artifacts live under **gitignored** directories (`.clone/` and `data/`) so extracted code is not committed.

## Prerequisites

- **Python 3.10+** (stdlib only; no pip install required)
- **git** on your `PATH`
- Network access for the clone stage
- **For the build stage only:** a working EasyCrypt with why3 and at least one SMT solver. Stages 1, 2 and 4 need none of this.

Verify the build prerequisite with:

```bash
easycrypt config     # should print a git-hash and a non-empty "known provers:" line
```

If it does not, activate the switch (`opam switch set easycrypt_env && eval $(opam env)`) and run `easycrypt why3config`.

Run commands from the repository root:

```bash
cd /path/to/AI4EC
```

## Quick start

Run all four stages in sequence:

```bash
python3 -m benchmark all --recurse-submodules
```

This will:

1. Shallow-clone every valid repo URL in `repositories.md` into `.clone/<owner-repo>/`
2. Copy all `.ec` files into `data/<owner-repo>/`, preserving each file's path within the repo
3. Compile every `.ec` file with EasyCrypt and write `data/build_report.json` + `data/build_report.md`
4. Write `data/proofs_index.json` cataloguing every lemma and axiom, annotated with its file's build status

The build stage dominates the runtime. Skip it with `python3 -m benchmark all --skip-build`.

## Stages

### Stage 1 — Clone

```bash
python3 -m benchmark clone
```

Clones repositories with `git clone --depth 1`. Skips repos that are already cloned unless you pass `--force-refresh`.

Pass `--recurse-submodules` if you intend to run the build stage. Several listed repos vendor Jasmin or EasyCrypt as submodules; without them their `require`s cannot resolve and the build stage reports failures that are artifacts of the clone rather than of the proofs.

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

### Stage 3 — Build

```bash
python3 -m benchmark build --jobs 8
```

Requires `.clone/clone_manifest.json` from stage 1 and a working EasyCrypt.

Runs `easycrypt compile -script` on every `.ec` file in every cloned repo and records the outcome. Every repo is compiled the same way so results stay comparable; repo-supplied `Makefile`s and test recipes are **never** executed.

Builds run against `.clone/<slug>/` rather than `data/<slug>/`, because a repo needs its full contents to compile — its own `easycrypt.project`, its submodules, and any non-`.ec` files it references.

**Output:** `data/build_report.json` and `data/build_report.md`

```
[info] EasyCrypt r2026.05-16-g76bf9e9 at /home/lr/.opam/easycrypt_env/bin/easycrypt
[built] alleystoughton-GuessingGame: 4/4 files compile (100%, 268.2s)
[failed] derens99-ElGamal-proof: 0/6 files compile (0%, 4.0s)
[built] tejasanilshah-the-joy-of-easycrypt: 4/4 files compile (100%, 6 admits, 6.7s)
```

Per-file statuses:

| Status | Meaning |
|--------|---------|
| `ok` | Exit code 0 |
| `error` | Non-zero exit with at least one diagnostic |
| `crashed` | Non-zero exit with no diagnostic (EasyCrypt itself failed) |
| `timeout` | Exceeded `--file-timeout` |
| `skipped` | Excluded by `overrides.json`, or the repo's `--repo-timeout` budget ran out |

> **A file whose lemmas are all `admit`ed compiles with exit code 0 and no diagnostic.** `build_status: "ok"` is therefore not by itself evidence of a real proof. Every file and repo also carries an `admit_count` / `admit_total`; read them together.

Include paths are derived automatically: the repo root plus every directory containing a `.ec` file. A repo carrying its own `easycrypt.project` needs nothing extra — EasyCrypt discovers it by walking upward from the input file — so the build stage records `has_project_file` but never overrides it.

Options:

```bash
--easycrypt PATH      # binary to use (default: $EASYCRYPT, then PATH)
--jobs N              # parallel compiles within a repo (default: half the CPUs)
--file-timeout S      # wall-clock per file (default: 300)
--smt-timeout S       # EasyCrypt -timeout for SMT calls (default: 20)
--repo-timeout S      # wall-clock budget per repo; leftovers marked skipped
--only SLUG,SLUG      # build specific repos
--limit N             # build at most N repos
--no-cache            # pass -no-eco; nothing read from or written to the .eco cache
--refresh             # recompile files a previous report recorded as ok
--overrides PATH      # per-repo overrides file (default: benchmark/overrides.json)
```

Reruns are incremental: files previously recorded `ok` are not recompiled unless you pass `--refresh`, which makes iterating on one failing repo cheap.

#### Per-repo overrides

[`overrides.json`](overrides.json) is committed and hand-maintained, keyed by repo slug:

```json
{
  "EasyCrypt-easycrypt": {"skip": "upstream EasyCrypt; its theories ship with the toolchain"},
  "tfaoliveira-libjc": {"exclude": ["proof/old/**"], "file_timeout": 600}
}
```

Recognized keys are `skip`, `exclude` (glob patterns), `include_dirs` (extra `-I`), `file_timeout`, and `smt_timeout`. Unknown keys are rejected so typos do not silently do nothing. Feed a full run's failures back into this file rather than loosening the runner.

### Stage 4 — Index

```bash
python3 -m benchmark index
```

Requires extracted `.ec` files under `data/` and `.clone/clone_manifest.json` (for repo URL and train/eval split metadata). Reads `data/build_report.json` if it exists.

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
| `build_status` | From stage 3; `"unknown"` when no build report exists |
| `build_error` | First `critical`/`error` diagnostic for the file, else `""` |
| `admit_count` | `admit`/`admitted` occurrences in the file, comments stripped |

Pass `--only-building` to emit only proofs whose file compiled:

```bash
python3 -m benchmark index --only-building
```

This is what downstream consumers (`integration/experiment/corpora/`) should select on — a proof in a file that does not load can never be opened, and an agent pointed at one burns its whole budget on a file that never had an active goal.

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
python3 -m benchmark build   --clone-dir /tmp/ec-clones --data-dir /tmp/ec-data
python3 -m benchmark index   --clone-dir /tmp/ec-clones --data-dir /tmp/ec-data
```

## Typical workflows

**Full corpus rebuild:**

```bash
python3 -m benchmark clone --force-refresh --recurse-submodules
python3 -m benchmark extract
python3 -m benchmark build --jobs 8
python3 -m benchmark index
```

**Refresh index after manual edits under `data/`:**

```bash
python3 -m benchmark index
```

**Iterate on one repo that fails to build:** edit `overrides.json`, then:

```bash
python3 -m benchmark build --only owner-repo --refresh
```

**Add a new repo:** edit `repositories.md`, then:

```bash
python3 -m benchmark clone --recurse-submodules
python3 -m benchmark extract
python3 -m benchmark build
python3 -m benchmark index
```

## Tests

```bash
python3 -m pytest benchmark/tests/ -q
```

The tests stub the EasyCrypt binary with shell scripts, so they do not need a real installation.

## Limitations

- The proof scanner is regex-based and may miss exotic declaration syntax.
- The build stage compiles files independently; it does not run a repo's own build recipe, so a repo whose `Makefile` does extra setup may report failures a `make` would not.
- `admit`ed lemmas compile cleanly. `build_status: "ok"` means *loads and checks*, not *proved* — read `admit_count` alongside it.
- Build results reflect one EasyCrypt version; a failure usually means the repo predates it, not that the proof was ever wrong. The version is recorded in `build_report.json` under `toolchain`.
- Org-only GitHub URLs are skipped (logged in `clone_manifest.json`).
- `.ec` files in git submodules require `clone --recurse-submodules`.
- Extracted code may carry restrictive licenses; keep `data/` local and gitignored.

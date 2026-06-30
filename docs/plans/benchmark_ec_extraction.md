---
name: Benchmark EC Extraction
overview: "Add a standalone Python `benchmark/` package with three independently runnable stages: shallow-clone repos from `repositories.md` into a gitignored `.clone/` folder, copy all `.ec` files into a gitignored `data/<repo-slug>/` tree preserving relative paths (so intra-repo `require` dependencies stay valid), and build a single proof index JSON file."
todos:
  - id: scaffold-package
    content: "Create benchmark/ package: config.py, repos.py (parse repositories.md + slug/split), root .gitignore"
    status: pending
  - id: stage-clone
    content: "Implement clone.py: shallow git clone, skip invalid URLs, write .clone/clone_manifest.json, CLI flags"
    status: pending
  - id: stage-extract
    content: "Implement extract.py: rglob *.ec, mirror paths under data/<slug>/, write data/extract_manifest.json"
    status: pending
  - id: ec-scanner
    content: Implement ec_scanner.py with comment-aware lemma/axiom detection + unit tests on fixture snippets
    status: pending
  - id: stage-index
    content: "Implement index_proofs.py: walk data/, emit data/proofs_index.json with required fields"
    status: pending
  - id: cli-main
    content: Wire __main__.py CLI (clone/extract/index/all) with shared path options
    status: pending
isProject: false
---

# Benchmark .ec Extraction Pipeline

## Goal

Automate building a local EasyCrypt benchmark corpus from the URLs in [`repositories.md`](repositories.md):

```mermaid
flowchart LR
  reposMd["repositories.md"] --> cloneStage["Stage 1: clone"]
  cloneStage --> cloneDir[".clone/owner-repo/"]
  cloneDir --> extractStage["Stage 2: extract"]
  extractStage --> dataDir["data/owner-repo/.../*.ec"]
  dataDir --> indexStage["Stage 3: index"]
  indexStage --> indexFile["data/proofs_index.json"]
```

All generated artifacts live in **gitignored** directories (license-sensitive extracted code stays local).

## Directory layout (new)

```
AI4EC/
├── .gitignore                    # add .clone/ and data/
├── repositories.md               # existing source of truth
└── benchmark/
    ├── __init__.py
    ├── __main__.py               # CLI entry: python -m benchmark <stage>
    ├── config.py                 # paths, defaults
    ├── repos.py                  # parse repositories.md
    ├── clone.py                  # stage 1
    ├── extract.py                # stage 2
    ├── index_proofs.py           # stage 3
    ├── ec_scanner.py             # comment-aware lemma/axiom scanner
    └── requirements.txt          # empty or minimal (stdlib-only preferred)
```

**Generated (gitignored):**

| Path | Purpose |
|------|---------|
| `.clone/<repo-slug>/` | Shallow git clones (`--depth 1`) |
| `.clone/clone_manifest.json` | Per-repo clone status (URL, slug, split, path, commit, errors) |
| `data/<repo-slug>/<rel-path>.ec` | Extracted files mirroring in-repo relative paths |
| `data/extract_manifest.json` | File list per repo (source path, dest path, bytes, sha256) |
| `data/proofs_index.json` | Single proof index (see schema below) |

## Stage 1 — Clone (`python -m benchmark clone`)

**Input:** [`repositories.md`](repositories.md)

**Parsing rules** ([`benchmark/repos.py`](benchmark/repos.py)):
- Split on `## Training` / `## Evaluation` headers → tag each URL with `split: "training" | "evaluation"`.
- Extract `https://github.com/<owner>/<repo>` lines; ignore comments/TODO lines.
- **Skip invalid entries** (e.g. `https://github.com/formosa-crypto`) with a manifest warning — no GitHub API expansion.
- Derive stable slug: `owner-repo` (e.g. `IvanRenison-SimpleORAM-Jasmin`).

**Clone behavior** ([`benchmark/clone.py`](benchmark/clone.py)):
- `git clone --depth 1 <url> .clone/<slug>/` via `subprocess`.
- Skip if destination already exists and is a valid git repo (idempotent reruns); optional `--force` to re-clone.
- Record `HEAD` commit hash in manifest.
- Continue on per-repo failure; exit non-zero only if all repos fail.
- Optional `--limit N` / `--only slug1,slug2` for dev iteration.

**Manifest entry example:**
```json
{
  "url": "https://github.com/IvanRenison/SimpleORAM-Jasmin",
  "slug": "IvanRenison-SimpleORAM-Jasmin",
  "split": "training",
  "status": "ok",
  "path": ".clone/IvanRenison-SimpleORAM-Jasmin",
  "commit": "abc123...",
  "error": null
}
```

## Stage 2 — Extract (`python -m benchmark extract`)

**Input:** `.clone/clone_manifest.json` (only `status: "ok"` repos)

**Dependency preservation strategy:** copy **every** `.ec` file from each cloned repo into `data/<slug>/`, preserving the **relative path** from the clone root. This is the key invariant:

- `require MAC` in `foo/MAC-PRF.ec` resolves to `MAC.ec` in the same directory — preserved by mirroring `data/<slug>/foo/MAC-PRF.ec` and `data/<slug>/foo/MAC.ec`.
- Stdlib theories (`AllCore`, `Distr`, etc.) still resolve via the local EasyCrypt install; no rewriting needed.
- No flattening across subdirectories; no cross-repo merging.

**Algorithm:**
```python
for entry in manifest["repos"]:
    if entry["status"] != "ok":
        continue
    src_root = Path(entry["path"])
    for ec in src_root.rglob("*.ec"):
        rel = ec.relative_to(src_root)
        dest = DATA_DIR / entry["slug"] / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ec, dest)
```

**Edge cases handled:**
- Skip `.git/` (rglob won't enter it by default if we start from repo root).
- Symlinks: copy file content (`follow_symlinks=False` by default in `copy2` — document behavior; use `shutil.copy2` on resolved path if symlink points to `.ec`).
- Re-runs: overwrite changed files; record sha256 in `extract_manifest.json`.
- Does **not** copy non-`.ec` assets (`.eco`, `.json`, etc.) — out of scope unless later needed for compilation.

**Not in scope (document as limitation):** git-submodule `.ec` dependencies inside a repo won't be present unless the upstream repo vendors them; optional future flag `--recurse-submodules`.

## Stage 3 — Index proofs (`python -m benchmark index`)

**Input:** `data/**/*.ec` + `extract_manifest.json` (for repo URL/split lookup)

**Output:** single [`data/proofs_index.json`](data/proofs_index.json)

**Index record schema:**
```json
{
  "repo_url": "https://github.com/IvanRenison/SimpleORAM-Jasmin",
  "repo_slug": "IvanRenison-SimpleORAM-Jasmin",
  "split": "training",
  "file": "IvanRenison-SimpleORAM-Jasmin/src/foo.ec",
  "line": 39,
  "kind": "lemma",
  "name": "dkey_fu",
  "signature": "lemma dkey_fu : is_full dkey."
}
```

Field definitions:
- **`file`**: path relative to `data/` (unique across the corpus thanks to per-repo slug prefix).
- **`line`**: 1-based line where the declaration starts (first line of the signature).
- **`kind`**: `lemma` or `axiom` (EasyCrypt uses `lemma` for provable statements; no `theorem` in typical `.ec` code).
- **`signature`**: full declaration text from `line` through the closing `.` before `proof.` (multi-line signatures supported).

**Scanner design** ([`benchmark/ec_scanner.py`](benchmark/ec_scanner.py)):

Reuse patterns from existing agent code where sensible:
- [`integration/agent/proof_file.py`](integration/agent/proof_file.py) — `proof.` / `qed.` detection
- [`integration/agent/premises.py`](integration/agent/premises.py) — `lemma|axiom` name extraction

Implementation: **comment-aware line scanner** (not full EasyCrypt parser):
1. Strip nested `(* ... *)` comments before matching.
2. Detect declaration start: `^\s*(?:(?:local|global)\s+)*(lemma|axiom)\s+(\w+)`.
3. Accumulate lines until:
   - `proof.` appears → signature ends on preceding completed statement, or
   - a line ends with `.` after `:` (covers axioms and single-line lemmas).
4. Normalize signature whitespace (join multi-line with single spaces or preserve `\n` — pick **single-line normalized** for embedding friendliness; document choice).

**Excluded from index (v1):** `realize` blocks, `clone` proofs, tactic-only fragments. Index all top-level and `local` lemmas/axioms.

**Tests:** add [`benchmark/tests/test_ec_scanner.py`](benchmark/tests/test_ec_scanner.py) using fixtures from [`integration/tests/fixtures/test_premises.ec`](integration/tests/fixtures/test_premises.ec) and [`easycrypt_claude_test/CS591Project-master/MAC-PRF.ec`](easycrypt_claude_test/CS591Project-master/MAC-PRF.ec) (copied snippets, not full 500-line file).

## CLI interface

[`benchmark/__main__.py`](benchmark/__main__.py):

```bash
python -m benchmark clone   [--force] [--limit N] [--only SLUGS]
python -m benchmark extract [--force]
python -m benchmark index
python -m benchmark all     # runs 1→2→3 sequentially (convenience only)
```

Shared options: `--repos-file repositories.md`, `--clone-dir .clone`, `--data-dir data`.

Each stage is independently runnable and reads/writes its own manifest; later stages fail fast with a clear message if the prior manifest is missing.

## Gitignore

Add root [`.gitignore`](.gitignore):
```
.clone/
data/
```

(Keep existing [`integration/.gitignore`](integration/.gitignore) unchanged.)

## Dependency on existing code

- **No dependency** on `integration/agent/` at runtime — benchmark is standalone.
- Reuse **ideas/patterns** from `proof_file.py` and `premises.py`, not imports (avoids pulling agent deps).
- **No EasyCrypt binary required** for extraction/indexing (regex scanner is sufficient for cataloging; compilation validation is a future optional stage).

## Known limitations (document in `benchmark/README.md` brief module docstring)

| Limitation | Mitigation |
|------------|------------|
| Scanner may miss exotic declaration syntax | Test against bundled fixtures; iterate regex |
| Cloned repos may not compile in isolation | Index is for cataloging/benchmarking, not compile verification |
| Org/user URLs skipped | Logged in `clone_manifest.json` |
| Submodule `.ec` files missing | Future `--recurse-submodules` flag |
| License: extracted code not redistributed | `data/` gitignored |

## Implementation order

1. `config.py` + `repos.py` + root `.gitignore`
2. `clone.py` + manifest writing
3. `extract.py` + manifest writing
4. `ec_scanner.py` + tests
5. `index_proofs.py` + `__main__.py` CLI
6. Smoke test: `clone --limit 2` on small repos, then extract + index

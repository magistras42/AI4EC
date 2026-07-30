# AI4EC

## Benchmark extraction

To build a local EasyCrypt proof corpus from external repositories, see [`benchmark/README.md`](benchmark/README.md).

```bash
python3 -m benchmark all --recurse-submodules
```

This clones the repositories listed in [`repositories.md`](repositories.md), extracts their `.ec` files,
compiles each one with EasyCrypt, and writes a proof index annotated with per-file build status.

The build stage needs a working EasyCrypt (check with `easycrypt config`) and dominates the runtime;
pass `--skip-build` to run extraction and indexing alone.

```bash
python3 -m benchmark build --jobs 8    # just the build stage
python3 -m benchmark index --only-building   # index only proofs in files that compile
```

Results land in `data/build_report.md` (human-readable ranking), `data/build_report.json`,
and `data/proofs_index.json`.

"""Benchmark pipeline: clone repos, extract .ec files, build them, index proofs.

Stages (run independently):
  python -m benchmark clone
  python -m benchmark extract
  python -m benchmark build
  python -m benchmark index

Generated artifacts live under gitignored `.clone/` and `data/`.

Limitations:
- Regex-based proof scanner may miss exotic declaration syntax.
- The build stage needs a working EasyCrypt install; stages 1, 2 and 4 do not.
- A file whose lemmas are all `admit`ed compiles cleanly, so `build_status: ok`
  is not by itself evidence of a real proof; check `admit_count` alongside it.
- Invalid GitHub URLs (e.g. org-only links) are skipped with warnings.
- Submodule .ec files are only fetched with `clone --recurse-submodules`.
"""

__version__ = "0.2.0"

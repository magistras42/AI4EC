"""Benchmark pipeline: clone repos, extract .ec files, index proofs.

Stages (run independently):
  python -m benchmark clone
  python -m benchmark extract
  python -m benchmark index

Generated artifacts live under gitignored `.clone/` and `data/`.

Limitations:
- Regex-based proof scanner may miss exotic declaration syntax.
- Index catalogues proofs; it does not verify compilation.
- Invalid GitHub URLs (e.g. org-only links) are skipped with warnings.
- Submodule .ec files are not fetched unless upstream vendors them.
"""

__version__ = "0.1.0"

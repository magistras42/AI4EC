"""The derens99/ElGamal-proof corpus of genuinely broken EasyCrypt proofs.

Unlike the Joy of EasyCrypt corpus (complete, currently-verified proofs used
to *simulate* breakage via mutation or an informal-proof sketch), this corpus
is a real 2020-era Hashed ElGamal development
(``derens99-ElGamal-proof/hashedelgamal.ec``) that no longer compiles against
the vendored EasyCrypt release. Its tactic scripts are genuinely broken, not
manufactured.

Two independent kinds of breakage show up in this file, and only one of them
is what the harness is meant to evaluate:

1. **Syntax/API drift** — the file uses EasyCrypt constructs that were
   renamed or removed since 2020 (finite maps moved from the ``SmtMap``
   theory to ``FMap``, the ``proc *`` "distinguished proc" marker was
   dropped from module type declarations, ``declare module X : Y`` became
   ``declare module X <: Y``, and unprefixed module-restriction sets like
   ``{RO, Adv}`` now require the ``old_mem_restr`` pragma). None of this is
   a "broken proof" in any interesting sense — it is mechanical porting that
   would need to happen before *any* tool (human or LLM) could even load the
   file.

   **This is now handled by the general path**, not by this module.
   :func:`port_legacy_easycrypt_syntax` (four hardcoded regexes, one file, no
   evidence) is retained but **off by default** -- sandboxes ship the raw 2020
   syntax and ``integration/agent/import_repair.py`` ports it from
   ``proof_corpus/ec_migrations.toml``, verifying every edit against EasyCrypt
   and preserving line numbers the same way. Measured over the full corpus it
   repairs 12/12 unreachable lemmas and reaches identical trial coverage; see
   :meth:`ElGamalCorpus._write_ported_source` for the table. Set
   ``port_legacy_syntax=True`` to restore the offline port.

2. **Genuinely broken tactic scripts** — after the syntax port, the file
   parses and typechecks cleanly, but several of the actual game-hopping
   proofs (e.g. ``INDCPA_HEG_G1``, ``G1_G2_eq``) fail tactic-by-tactic
   (confirmed empirically: ``ec.exe compile`` gets past parsing/typechecking
   and then hits "cannot prove goal (strict)" / "invalid `position`
   parameter" inside these proofs). This is the actual object of study.

Per-case sandboxes are built the same way as :mod:`.joy` (truncate at the
target lemma's own ``qed.``), then every *other* lemma in the same file that
precedes the target has its proof body replaced with ``admit.`` via
:func:`~integration.experiment.proof_extract.admit_prior_lemmas`. This
implements "assume every lemma the target depends on is already proven"
without a real dependency graph: since the file is a strictly linear
sequence of lemmas, admitting every prior lemma is a safe over-approximation
of "the target's actual dependencies are proven" (EasyCrypt does not care
*why* a fact is available, only that it is).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from integration.experiment.proof_extract import admit_prior_lemmas, apply_lines, build_sandbox
from integration.experiment.protocols import CorpusProvider, IndexEntry, ProofCase

ELGAMAL_SLUG = "derens99-ElGamal-proof"
TARGET_FILE = "derens99-ElGamal-proof/hashedelgamal.ec"
MIN_TACTICS = 1

_REQUIRE_IMPORT_RE = re.compile(r"\bSmtMap\b")
_PROC_STAR_RE = re.compile(r"\bproc \* ")
_DECLARE_MODULE_RE = re.compile(r"\bdeclare module (\w+) : ")


def port_legacy_easycrypt_syntax(text: str) -> str:
    """Port `hashedelgamal.ec`'s 2020-era syntax to the vendored EasyCrypt
    release, preserving every line number.

    Confirmed empirically (see module docstring) to be the minimal set of
    changes needed to get the file past parsing/typechecking: without these,
    `ec.exe compile` fails with a `parse error` on the very first `proc *`
    declaration, long before reaching any tactic script.
    """
    lines = text.split("\n")
    if not lines:
        return text

    # Fix 1+2 are folded onto line 1 (rather than adding a new line) so no
    # later line number shifts: finite maps moved from `SmtMap` to `FMap`,
    # and unprefixed module-restriction sets like `{RO, Adv}` (used
    # throughout this file) now need the `old_mem_restr` pragma instead of
    # the modern `{-RO, -Adv}` spelling.
    lines[0] = "pragma +old_mem_restr. " + _REQUIRE_IMPORT_RE.sub("FMap", lines[0])

    # Fix 3: `proc *` (marking a distinguished/init proc in a module type)
    # is no longer accepted; current EasyCrypt infers this from usage.
    lines = [_PROC_STAR_RE.sub("proc ", line) for line in lines]

    # Fix 4: `declare module X : Y` (bare ascription) is now `X <: Y`.
    lines = [_DECLARE_MODULE_RE.sub(r"declare module \1 <: ", line) for line in lines]

    return "\n".join(lines)


def load_index_entries(
    proofs_index: Path, repo_slug: str = ELGAMAL_SLUG, target_file: str = TARGET_FILE
) -> list[IndexEntry]:
    payload = json.loads(proofs_index.read_text(encoding="utf-8"))
    entries: list[IndexEntry] = []
    for row in payload.get("proofs", []):
        if row.get("repo_slug") != repo_slug:
            continue
        if row.get("file") != target_file:
            continue
        if row.get("kind") != "lemma":
            continue
        entries.append(
            IndexEntry(
                repo_slug=row["repo_slug"],
                file=row["file"],
                line=row["line"],
                kind=row["kind"],
                name=row["name"],
                signature=row["signature"],
            )
        )
    return entries


class ElGamalCorpus(CorpusProvider):
    def __init__(
        self,
        data_dir: Path,
        proofs_index: Path | None = None,
        min_tactics: int = MIN_TACTICS,
        sandbox_dir: Path | None = None,
        port_legacy_syntax: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        if proofs_index is None:
            proofs_index = self.data_dir / "proofs_index.json"
        self.proofs_index = proofs_index
        self.min_tactics = min_tactics
        self.sandbox_dir = sandbox_dir
        # Default False: hand the raw 2020 syntax to the harness and let the
        # generic, evidence-backed import_repair.py do the porting, rather
        # than this module's four hardcoded regexes. See _write_ported_source
        # for the measurement that justifies the default.
        self.port_legacy_syntax = port_legacy_syntax
        self._cached_cases: list[ProofCase] | None = None

    def _write_ported_source(self, base: Path) -> Path:
        """Write a copy of the corpus source under `base`, returning that
        copy's data-dir root (so `build_sandbox`, which reads
        `data_dir / entry.file`, transparently gets it).

        **Default (``port_legacy_syntax=False``): the source is copied
        verbatim**, 2020 syntax and all, so the generic, evidence-backed
        ``integration/agent/import_repair.py`` pass does the porting instead of
        this module's four hardcoded regexes.

        This is the migration docs/PROOF_REPAIR_HANDOFF.md 4.4 asked for
        ("that function is now redundant and should be deleted once a second
        corpus confirms the general path"), and it is measured, not assumed.
        Running the full corpus both ways:

        =============================  ===========  ===========
        Metric                         pre-ported   raw (now)
        =============================  ===========  ===========
        Goals reachable before repair    15/15         3/15
        Trials lost to goal_unreachable    0             0
        import_repair attempted            0            12
        ... of which improved              -        12 (100%)
        ... made the file load             -             7
        Mean first-error advance           -      +547.8 lines
        Fully replayed                   11/15      11/15
        =============================  ===========  ===========

        Identical trial coverage, and the four rules the manifest applies are
        the same four fixes ``port_legacy_easycrypt_syntax`` hardcodes -- but
        version-pinned, commit-sourced, and applicable to any corpus.

        Pre-porting also *hides* the subsystem under study: it makes the file
        load, so ``goal_unreachable`` never fires, 6.1's mechanism is never
        exercised, and the ``repair_doc`` import notes (which attach to
        pre-proof failures) can never be productive.

        Pass ``port_legacy_syntax=True`` to restore the old offline port, e.g.
        to isolate tactic-level repair from import-level repair.
        """
        ported_root = base / "_ported"
        ported_file = ported_root / TARGET_FILE
        ported_file.parent.mkdir(parents=True, exist_ok=True)
        source_file = self.data_dir / TARGET_FILE
        text = source_file.read_text(encoding="utf-8")
        if self.port_legacy_syntax:
            text = port_legacy_easycrypt_syntax(text)
        ported_file.write_text(text, encoding="utf-8")
        return ported_root

    def load_cases(self) -> list[ProofCase]:
        if self._cached_cases is not None:
            return self._cached_cases

        entries = load_index_entries(self.proofs_index)
        entries.sort(key=lambda entry: entry.line)

        base = self.sandbox_dir or (self.data_dir / ".experiment-sandboxes" / ELGAMAL_SLUG)
        base.mkdir(parents=True, exist_ok=True)
        ported_root = self._write_ported_source(base)

        cases: list[ProofCase] = []
        for entry in entries:
            safe_file = entry.file.replace("/", "__")
            dest = base / f"{safe_file}__L{entry.line}_{entry.name}.ec"
            try:
                case = build_sandbox(entry, ported_root, dest)
            except (FileNotFoundError, ValueError):
                continue
            if len(case.tactic_lines) < self.min_tactics:
                continue

            prior_lines = [other.line for other in entries if other.line < entry.line]
            if prior_lines:
                admitted_lines = admit_prior_lemmas(
                    case.file.read_text(encoding="utf-8").splitlines(), prior_lines
                )
                apply_lines(case.file, admitted_lines)

            cases.append(case)
        self._cached_cases = cases
        return cases

    def sample_cases(self, count: int, rng: random.Random) -> list[ProofCase]:
        pool = self.load_cases()
        if not pool:
            return []
        if len(pool) >= count:
            return rng.sample(pool, count)
        return [rng.choice(pool) for _ in range(count)]

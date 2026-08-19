# `ec_migrations.toml` — schema and rationale

The per-version manifest of what EasyCrypt libraries were **created, changed,
renamed or split**, expressed as rewrite rules a script can apply to a `.ec`
file *before any proof is attempted*.

```
<easycrypt clone with tags>
        │
        ▼  scripts/analyze_library_history.py     (git log per library)
output/library_history.json
        │
        ▼  scripts/build_ec_migrations.py         (+ theory sources, + curated engine rules)
ec_migrations.toml  ──read by──▶  integration/agent/import_repair.py
```

```bash
python3 proof_corpus/scripts/analyze_library_history.py   # mine git
python3 proof_corpus/scripts/build_ec_migrations.py       # emit TOML
python3 -m integration.agent.import_repair FILE.ec \
    --source-version r2022.04 --target-version r2026.07   # apply
```

**Library rules are derived from the commit history, not from
`repair_doc/*.json`.** Those files were written by reading the current sources
and the release notes; their own `caveat` says *"No true git-diff was
possible"*. `analyze_library_history.py` does the diff, so every library rule
carries a commit SHA and a release tag and can be re-checked with `git show`.

---

## Why a third representation

`repair_docs_index.json` describes the **current state** of the tree.
`changelog_index.json` describes **what changed**. Neither says **what to do to
a file**, and that gap has a concrete cost:

> Import breakage is *pre-proof*. When a `require import` no longer resolves,
> EasyCrypt cannot load the file at all, `llm -upto` returns nonzero, and
> [`repair_bootstrap.py`](../integration/experiment/repair_bootstrap.py) records
> `skip_reason="goal_unreachable"` — the trial ends before a single tactic is
> tried, and none of the changelog evidence ever gets a chance to help.

Closing that needs a rule with three parts: a **version window**, a **match**,
and an **action** precise enough to apply mechanically.

**Why TOML.** The manifest is meant to be read and extended by hand: engine
rules (§Curated) cannot be derived from anything, and a human reviewing a
derived rule needs to see it. `[[migration]]` array-of-tables is the most
editable shape, and Python reads it with stdlib `tomllib`, so nothing new is
vendored.

**What is derived vs curated.** Library rules — symbol moves, theory
creation/move/deletion, export gaps — come from `library_history.json` and the
theory sources. Parser and module-system changes (`proc *`, `declare module
X : T`, memory restrictions) are *engine* changes that no theory-file history
reveals, so they live in `CURATED_ENGINE_MIGRATIONS` in the generator — though
their version pins also come from commits, recovered by
`collect_changelog.py --git-log`.

---

## Schema

### `[[migration]]`

| Field | Meaning |
|---|---|
| `id` | unique slug |
| `kind` | `theory_split` \| `theory_renamed` \| `theory_added` \| `theory_removed` \| `symbol_moved` \| `symbol_renamed` \| `syntax_change` \| `require_semantics` |
| `breaks_at` | release where the **old** form stops working. Applies when `source_version < breaks_at <= target_version`. **Omit** when the release could not be pinned — the rule then applies whenever `source < target` |
| `confidence` | `high` \| `medium` \| `low`. `import_repair --min-confidence` gates on this |
| `summary` | what changed, in prose, for the prompt |

`[migration.match]` — **all** present conditions must hold:

| Condition | Meaning |
|---|---|
| `requires_theory` | file has `require [import] <T>` (any listed) |
| `missing_require` | file does **not** require `<T>` |
| `uses_symbols` | file references any of these tokens |
| `not_uses_symbols` | file references none of these |
| `matches_regex` | raw pattern over the file text |

`[[migration.action]]` — applied in order:

| `op` | Params | Effect |
|---|---|---|
| `add_require` | `theory`, `after?` | append `theory` to an existing require line (preferring the one naming `after`) |
| `replace_require` | `theory`, `with_theory` | swap the name inside require lines only |
| `remove_require` | `theory` | drop the name; blanks the line if it becomes empty |
| `rename_symbol` | `old`, `new` | whole-token rename across the file |
| `replace_regex` | `pattern`, `replacement` | in-place substitution |
| `add_pragma` | `pragma` | folded onto line 1 |

`[migration.provenance]` — `derived_from` (which artifact produced the rule),
`commits` (short SHAs), `changelog` (entry keys), `moved_symbol_count`, `note`.

### Line numbers are load-bearing

[`protocols.py::ProofCase`](../integration/experiment/protocols.py) records
**absolute lemma line numbers**, so every action above is line-preserving:
`add_require` extends an existing line, `add_pragma` folds onto line 1,
`remove_require` blanks rather than deletes, and the rest are in-place
substitutions. `import_repair.apply_actions` **asserts** the line count is
unchanged and raises if a rule violates it. Do not add an action that inserts
or deletes a line without also reindexing `ProofCase`.

### `[[library]]`

History summary for each tracked theory, from `library_history.json`:
`theory`, `path`, `exists_now`, `commits_examined`, `releases_touched`,
`symbol_churn` (added/removed counts per release), and `path_history`.

Tracked by default: **AllCore, Bool, Core, CoreMap, CoreReal, DBool,
DInterval, Distr, FMap, FSet, Int, Logic, Pervasive, PROM, Real, SmtMap**
(`analyze_library_history.py --library` to change the set).

### Caveat on the oldest tag

Every commit reachable from the earliest release tag is attributed to it —
including a decade of pre-tag history, since `rev-list r2022.04` reaches the
root. "Changed in r2022.04" therefore cannot be distinguished from "existed
before our window", so **no rule is keyed to that release** and none is
emitted for symbol events there.

---

## Version pins come from evidence

Several were only pinnable *after* `collect_changelog.py --git-log` recovered
EasyCrypt's four undocumented releases. None of this appears in any GitHub
release body:

| Migration | Pinned at | Evidence |
|---|---|---|
| `proc-star-removed` | **r2023.09** | commits `57be028cc` "strip out code related to proc *", `df8a2f924` "Remove parser support for `proc *` in module sigs" |
| `declare-module-ascription` | **r2023.09** | `dc50f44bf` "Forbid the usage of [declare] for concrete modules", `6d0e46493` "Enforce section restrictions on the types of declared modules" |
| `old-module-restriction-sets` | **r2024.09** | `b53230696` "simplify representation of memory restriction (#569)", `9b940b4f5` "Simplify/clean up memory/call restrictions" |
| `smtmap-symbols-moved-to-fmap-r2025.02` | **r2025.02** | **derived**: 125 declarations leave `SmtMap` and arrive in `FMap` in that release. Staged in r2024.09 by `9f9be585b` "stage the SmtMap -> FMap renaming"; completed by `6942bd0dd` "split SmtMap into SMT Array and finite map" |

The SmtMap rule is the payoff of deriving from history rather than prose: the
hand-written note in `smtmap_lib.json` names about a dozen affected operations,
the history names **all 125**, and the list is regenerable when the tree moves.

The first three are exactly what
[`corpora/elgamal.py::port_legacy_easycrypt_syntax`](../integration/experiment/corpora/elgamal.py)
hardcodes for one file. They are now manifest rules with version pins, applying
to any corpus.

---

## How the processor uses it

[`integration/agent/import_repair.py`](../integration/agent/import_repair.py):

1. **Probe.** `validate_file` on the untouched file. If it already loads, stop.
2. **Select.** Rules whose `(source, target]` window the file crosses. If either
   endpoint is unknown to the manifest, *every* rule is considered — EasyCrypt's
   releases only reach back to r2022.04, so a 2020-era proof legitimately has no
   source tag (the same fail-open convention `releases_in_range` uses).
3. **Match.** Filter by the `[migration.match]` conditions and `min_confidence`.
4. **Apply in bulk**, then probe once. If the file now loads, done — one
   EasyCrypt call for the common case.
5. **Otherwise apply incrementally**, probing after each rule and keeping it
   unless EasyCrypt stops *earlier* than before. Non-regression, not strict
   improvement: every rule is independently evidence-backed and line-preserving,
   so the question is "does this hurt?" — a rule fixing something at line 300
   shows no movement while an unrelated parse error sits at line 108.
6. **Report.** `error_line_before` / `error_line_after` measure partial progress;
   `format_for_prompt` tells the solver what was rewritten, since it is proving
   against a file the harness edited.

The source file is never modified — work happens on a copy; the caller promotes.

### Measured on the real corpus

`data/derens99-ElGamal-proof/hashedelgamal.ec` (2020-era, genuinely broken):

Re-measured 2026-07-31 against the current 15-rule manifest:

```
first error: line 108  →  line 357  →  line 453
considered 4, kept 4, rejected 0:
  smtmap-symbols-moved-to-fmap-r2025.02   (symbol_moved, 125 symbols, derived)
  proc-star-removed                       (108 → 357)
  declare-module-ascription               (no regression, still 357)
  old-module-restriction-sets             (357 → 453)
```

Only 4 of 15 rules are *considered* — the other 11 fail their
`[migration.match]` conditions on this file. `declare-module-ascription` is
kept on non-regression, not on movement: under a "strictly decreases" criterion
it would have been rolled back.

The remaining error at 453 is `invalid 'position' parameter` on a `seq 1 1 :`
**tactic** — a genuinely broken proof, not an import problem. That is the
intended boundary: import repair gets the file loadable through its
declarations and stops where tactic-level repair begins.

---

## Extending

Add rules to `CURATED_MIGRATIONS` in
[`build_ec_migrations.py`](scripts/build_ec_migrations.py) so regeneration keeps
them — or edit the TOML directly and stop regenerating. Both are valid; the
processor only ever reads TOML.

The generator round-trips its own output through `tomllib` before writing, so a
manifest that would fail to parse is caught at generation time rather than at
repair time.

---

## Known limitations

- **15 migrations** — 12 derived from git history, 3 curated engine rules.
  By kind: 9 `require_semantics`, 3 `syntax_change`, 1 each `symbol_moved` /
  `theory_added` / `theory_renamed`. Confidence: 3 high, 11 medium, 1 low.
  These are the ones with hard evidence; the manifest is designed to grow, and
  a rule with no `breaks_at` is still useful.
- **9 of the 15 are AllCore export-gap rules** of one shape. Cheap and correct,
  but the manifest's *variety* of evidence is thinner than the count suggests.
- **Only one symbol-level move.** `smtmap-symbols-moved-to-fmap-r2025.02`
  carries all 125 moved declarations, but it is the sole `symbol_moved` rule.
  `repair_docs_index.json`'s `symbol_index` (6325 symbols) has the data to
  generate more and nothing consumes it for that purpose yet.
- **`match` is AND-only.** No disjunction across condition groups; express
  alternatives as separate rules.
- **Regex actions are unanchored.** `replace_regex` applies to the whole file
  including comments and strings.
- **Progress is measured by first-error line.** A rule that fixes one error and
  introduces another at a later line reads as progress.

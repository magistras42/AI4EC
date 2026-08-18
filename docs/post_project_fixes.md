# Post-project fixes: the premise pipeline

Seven changes to the premise/retrieval path, in the order they should be done.
They came out of the §5.6/§5.7 measurement pass in
[`RESEARCH_REPORT.md`](RESEARCH_REPORT.md), which found that the "Top relevant
premises" block costs ~329k tokens across the study, is used in 1.4% of turns,
and has **never** contributed to an accepted tactic.

This document is written for an implementer picking the work up cold. Each fix
states the evidence, the exact touchpoints, the behaviour required, a sketch,
the tests, and the traps. Sketches are illustrative — read the surrounding code
before copying them.

## Before you start

* **Fix 1 first, and alone.** Fixes 2–7 are all downstream of it: until the
  parser stops dropping declarations, no ranking or rendering change can
  surface the lemmas that matter, and any A/B you run measures the parser bug
  rather than your change.
* **Never run a paid experiment yourself.** See [`AGENTS.md`](../AGENTS.md):
  DeepSeek runs require the user to type the confirmation personally. Give them
  the command and stop. Everything in fixes 1–6 can be validated offline
  against artifacts already in `integration/output/experiments/`.
* **Re-derive, don't re-type.** Any number you quote comes from
  `python3 -m integration.experiment.effort_metrics integration/output/experiments`.
  If a fix changes what the agent sees, say plainly in the report that runs
  before and after are not comparable.
* **Tests are the deliverable, not an afterthought.** This project has retracted
  published numbers twice (§6.2, §6.3), both times because a plausible-looking
  behaviour was never pinned by a test.

Environment note: the vendored fork at
`integration/extern/easycrypt/_build/default/src/ec.exe` is the only binary
with the `llm` subcommand. `AgentConfig()` resolves it; do not use the opam
`easycrypt` on `PATH` for anything involving `-premises`.

---

## Fix 1 — `parse_premises` silently drops 52% of the catalog

**Severity: critical.** This is a defect, not a design change.

### Evidence

`PREMISE_RE` in [`integration/agent/premises.py:24`](../integration/agent/premises.py#L24)
requires the statement to begin on the *same line* as the declaration. The
`llm -premises` dump wraps long declarations, so every premise whose statement
does not fit on one line is discarded.

Measured on `G2_bad_ub`'s own sandbox at its own cursor (line 684):

| | |
|---|---:|
| declaration-start lines in EasyCrypt's dump | 5,398 |
| parsed into the catalog | 2,583 |
| **dropped because the statement wraps** | **2,815 (52%)** |

The loss is not random: long statements are the ones that wrap, so the dropped
set is biased toward equivs, phoare bounds, and anything with hypotheses. For
`G2_bad_ub` it includes **every section-local lemma in scope and both adversary
losslessness axioms** — verified present in EasyCrypt's raw dump and absent
from the parsed catalog: `RO_LCDHAdv`, `G1_G2`, `G1_G2_eq`, `INDCPA_HEG_G1`,
`Adv_choose_ll`, `Adv_guess_ll`.

This is why §5.7's flinch case could never have been helped: the agent needed
`apply (RO_LCDHAdv q1_L q2_L)`, and `RO_LCDHAdv` — declared 29 lines above the
target and fully in scope — was not in the index at all. Only 14% of the names
the agent reached for in `apply`/`rewrite`/`smt()` were ever shown to it.

What the dump actually looks like:

```
axiom grexpA: forall (q1 q2 : exp), g ^ q1 ^ q2 = g ^ (q1 * q2).   <- parsed

local  lemma RO_LCDHAdv:                                            <- dropped
  forall (q1 q2 : exp),
    equiv[ RO_track.f  ~ Adv2LCDHAdv(Adv).RO_track.f :
           ...

axiom Adv_choose_ll:                                                <- dropped
  forall (RO0 <: RO{-Adv}), islossless RO0.f => islossless Adv(RO0).choose.
```

Note `local  lemma` — two spaces. The existing `\s+` handles that; the line
break is the whole problem.

**The bug has a directly attributable cost.** `G1_G2_eq` spent 71 steps and
5.0 hours circling `rewrite mem_set in H3`, and eight `search_lemmas` calls for
`mem_set` all answered "not found". `mem_set` exists: EasyCrypt's dump for that
trial declares it as `lemma mem_set ['a, 'b]:` — wrapped, therefore dropped by
the parser. `get_set_neqE`, which the agent also tried, is dropped for the same
reason. Meanwhile `mem_empty` and `mem_rng_empty`, whose statements fit on one
line, were parsed and shown — `mem_rng_empty` 1,298 times. The block was
showing the agent the short lemmas of `FMap` while hiding the ones it needed,
and the search tool, reading the same catalog, confirmed the wrong answer eight
times. The agent's belief was correct throughout; the harness contradicted it.

### Where

* [`integration/agent/premises.py`](../integration/agent/premises.py) —
  `PREMISE_RE` (L24), `parse_premises` (L56), `load_cached_embeddings` (L94),
  `save_cached_embeddings` (L119).
* [`integration/agent/proof_file.py:151`](../integration/agent/proof_file.py#L151) —
  `count_declarations_before`, the cache-invalidation counter.

### Required behaviour

1. A declaration whose statement continues on following lines is parsed, with
   the continuation joined into one whitespace-normalised statement.
2. A declaration whose statement is on the same line keeps parsing exactly as
   today — byte-identical catalog text for those entries.
3. Theory headers still switch `current_theory`, and never get absorbed into a
   preceding statement.
4. Locality (`local` / `declare`) is preserved in the catalog text, as now.

### How

Split the regex in two: a **header** match that ends at the colon, and a
**continuation** loop. Do *not* try to detect the end of a statement by looking
for a trailing `.` — qualified names (`Top.foo`), real literals and `.[ ]` map
syntax all contain dots. Terminate a block on the next line that starts a new
declaration or a theory header, which is unambiguous.

```python
# Header only: everything up to and including the colon. The statement may be
# empty here -- that means it continues on the following lines.
PREMISE_HEAD_RE = re.compile(
    r"^(?:(local|declare)\s+)?"
    r"(lemma|axiom)"
    r"(?:\s+nosmt)?"
    r"(?:\s+\[[^\]]*\])?"
    r"\s+(\w+)[^:]*:\s*(.*)$"
)


def parse_premises(premises_text: str) -> dict[str, str]:
    premises: dict[str, str] = {}
    current_theory = ""
    pending: tuple[LemmaRef, str, str, list[str]] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        ref, prefix, kind, parts = pending
        stmt = " ".join(p.strip() for p in parts if p.strip())
        if stmt:                      # a header with no statement is not usable
            theory = ref.theory
            head = f"[{theory}] " if theory else ""
            premises[ref.key] = f"{head}{prefix}{kind} {ref.name}: {stmt}"
        pending = None

    for raw_line in premises_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue                  # blank lines inside a statement are noise
        if THEORY_HEADER_RE.match(line):
            flush()
            current_theory = line.strip("= ").strip()
            continue
        match = PREMISE_HEAD_RE.match(line)
        if match:
            flush()
            locality, kind, name, first = match.groups()
            pending = (
                LemmaRef(theory=current_theory, name=name),
                f"{locality} " if locality else "",
                kind,
                [first],
            )
            continue
        if pending is not None:
            pending[3].append(line)

    flush()
    return premises
```

### Traps

* **The embedding cache will hide your fix.** `load_cached_embeddings`
  ([premises.py:94](../integration/agent/premises.py#L94)) validates on
  `mtime`, `cursor_upto`, `embed_model` and `declaration_count` — it stores
  `premises_hash` but **never checks it**. After your change the catalog roughly
  doubles while all four validated keys stay identical, so any trial with a warm
  cache keeps using embeddings for the old, truncated catalog and the fix
  appears to do nothing. Fix this in the same change: check `premises_hash` in
  `load_cached_embeddings`, or bump a cache-format version in the payload.
  Add a test that a catalog change alone invalidates the cache.
* **`count_declarations_before` has the same class of bug.** It counts lines
  starting with `"lemma "` or `"axiom "` and therefore misses `local lemma`
  entirely. It is only used for cache invalidation, so it is not urgent, but a
  file whose new declarations are all section-local will not invalidate. Fix it
  or leave a comment saying it is knowingly approximate — do not leave it
  silently wrong.
* **Embedding cost roughly doubles** at index build (2,583 → ~5,400 vectors).
  Embeddings are local (LM Studio), so this is time, not money; it will show up
  as a slower first step per trial. Measure it and note it.
* **Last-write-wins on duplicate keys** is existing behaviour. Keep it, and
  keep the qualified `Theory.name` key — collapsing to basenames would let
  same-named lemmas in different theories overwrite each other, which the
  module docstring already warns about.
* **A docstring elsewhere misattributes this bug, and must be corrected in the
  same change.** [`ec_names.py`](../integration/agent/ec_names.py) justifies
  never pre-blocking a tactic on an unknown name with: *"The Ax.all catalog is
  NOT authoritative: `rpow_hmono` is used by `G2_bad_ub`'s own original proof
  and replays successfully, yet does not appear in the catalog at that
  cursor."* Verified against the dump: `rpow_hmono` **is** there, as
  `lemma rpow_hmono:` — wrapped, therefore dropped. It is this bug, not a
  limitation of `Ax.all`. The same docstring's other four examples (`addr0`,
  `subzz`, `mem_empty`, `dtext_ll`) do parse and are absent only as *bare
  basenames*, which is the qualified-key point and is correct.

  The decision the docstring defends — make a failure informative rather than
  pre-empt it — is still right, and should stay. Only its first reason is
  wrong. Rewrite that paragraph so the next reader does not inherit the
  misdiagnosis, and note there that whether a pre-check is now viable is a
  question someone may legitimately re-open **after** the catalog is complete.

### Tests

Add to a new `integration/tests/test_premise_parsing.py` (the existing
`integration/tests/test_premises.py` is an end-to-end test of the EasyCrypt
flag itself and needs the binary; keep these pure).

Use the real shapes captured above:

```python
DUMP = """\
========== Top ==========
axiom grexpA: forall (q1 q2 : exp), g ^ q1 ^ q2 = g ^ (q1 * q2).
local  lemma RO_LCDHAdv:
  forall (q1 q2 : exp),
    equiv[ RO_track.f  ~ Adv2LCDHAdv(Adv).RO_track.f :
           arg{1} = arg{2} ==> res{1} = res{2}].
axiom Adv_choose_ll:
  forall (RO0 <: RO{-Adv}), islossless RO0.f => islossless Adv(RO0).choose.
========== FMap ==========
lemma mem_empty: forall (x : 'a), ! (x \\in empty).
"""
```

Cases to pin:

1. `RO_LCDHAdv` is present, its statement joined onto one line, and it keeps
   the `local ` prefix.
2. `grexpA` parses byte-identically to the old implementation (guard against a
   rewrite that changes existing catalog text).
3. A wrapped declaration immediately followed by a theory header does not
   absorb the header, and the following theory's lemmas get the new theory key.
4. Statement text is whitespace-normalised — no embedded newlines, no runs of
   spaces — because it is rendered into a prompt line.
5. A header with no statement at all (malformed dump) is dropped rather than
   stored with an empty body.
6. Cache: same `mtime`/`cursor_upto`/`embed_model`/`declaration_count` but a
   different catalog ⇒ `load_cached_embeddings` returns `None`.

### Validation, offline

```bash
python3 - <<'PY'
from pathlib import Path
from integration.agent.config import AgentConfig
from integration.agent.easycrypt import fetch_goal_and_premises, split_goal_and_premises
from integration.agent.premises import parse_premises
cfg = AgentConfig()
src = Path("integration/output/experiments/run-20260810T053405Z/trials/trial_010/agent_work.agent.ec")
gp = split_goal_and_premises(fetch_goal_and_premises(src, 684, cfg).stdout)
cat = parse_premises(gp.premises)
print(len(cat), "premises")
for want in ["RO_LCDHAdv", "G1_G2_eq", "Adv_choose_ll", "INDCPA_HEG_G1", "grexpA"]:
    print(want, any(k.split(".")[-1] == want for k in cat))
PY
```

**Done when** the count is ~5,400 (was 2,583) and all five names report `True`.
The sketch above was run against this exact dump before being written down: it
yields **5,396 premises, +2,813 over the current parser, with every one of the
2,583 existing entries byte-identical**. If your implementation changes any
existing entry's text, you have changed behaviour you did not intend to.

`INDCPA_Sec`, `G3_true` and `G2_G3` are *not* recoverable here and are not
casualties of this bug — they are not in EasyCrypt's dump at this cursor at
all. Do not use them as test targets.

---

## Fix 2 — always show the file's own declarations

### Evidence

Across all runs, only **4.2%** of premise slots went to a lemma from the file
being repaired; 96% went to library lemmas, led by `Number.RealField.ler_opp2`
(shown 1,459 times on a corpus about group exponentiation). Of the 71 name
references the agent made in `apply`/`rewrite`/`smt()` arguments, the majority
are file-local lemmas or section axioms (`Adv`, `INDCPA_Sec`, `Adv_choose_ll`,
`Adv_guess_ll`, `grexpA`, `inj`); the library names it wanted (`mem_rng_empty`,
`mem_empty`, `andP`) are a minority.

`G2_bad_ub`'s sandbox declares **30 lemmas and axioms**, four of them section-
local. That entire set, rendered as names plus shapes, costs about what the
current ten library premises cost.

### Where

* [`integration/agent/loop.py:334-339`](../integration/agent/loop.py#L334) —
  where `rank_by_cosine` and `top_premises` produce the block each step.
* [`integration/agent/prompt.py:1151`](../integration/agent/prompt.py#L1151) —
  the `## Top relevant premises` section.

### Required behaviour

The block has two parts, in this order: **(a)** every declaration from the file
under repair that is in scope at the cursor, always, unranked; **(b)** the
ranked library suggestions, subject to fixes 3–4. Label them differently — (a)
is "lemmas this file proves", (b) is "possibly relevant library lemmas". The
agent must be able to tell "this exists here" from "a similarity score liked
this".

### How

After Fix 1 the file's declarations are in the catalog and identifiable by
their theory: they carry the file's theory (`Top` for these sandboxes) or an
empty theory. Prefer selecting them structurally rather than by string prefix —
`ProofFile` already knows the source lines, so a declaration-name scan of the
file up to the cursor is more honest than trusting the dump's theory grouping.
Cap the always-on list (say 40) and, if it overflows, keep the ones nearest the
cursor and say how many were omitted.

### Tests

* A catalog containing both file-local and library entries renders the
  file-local ones in the always-on section regardless of cosine score.
* A declaration *after* the cursor is not shown (it is not in scope; showing it
  invites `apply` of a lemma EasyCrypt has not seen yet).
* Overflow: 60 declarations ⇒ 40 shown, and the note states 20 omitted.

**Done when** `RO_LCDHAdv` appears in the block on `G2_bad_ub`'s first step
without any change to the ranker.

---

## Fix 3 — do not show the block on program-logic goals

### Evidence

94% of the turns the block was shown on (1,247 of 1,333) had a program-logic
goal, where ambient lemmas cannot be applied. This is also where wrong-layer
failures dominate (43% of all failures), so the block is injecting name
candidates exactly where acting on them is a category error.

Concretely: `FMap.mem_rng_empty` was shown 1,298 times and is the one premise
that demonstrably *did* transfer into the agent's tactics — as `smt(mem_rng_empty)`,
which failed. The block's only measurable effect on behaviour was to plant a
wrong name.

### Where

[`integration/agent/prompt.py:207`](../integration/agent/prompt.py#L207)
(`goal_looks_program_logic`) already decides this, and
[`integration/agent/easycrypt.py:267`](../integration/agent/easycrypt.py#L267)
(`probe_is_program_logic`) can ask the checker when the display is ambiguous.

### Required behaviour

On a program-logic goal, omit the ranked library block (part (b) of Fix 2).
Keep the file's own declarations (part (a)) — `call`/`conseq`/`byequiv` take
lemma arguments and those are program-logic moves.

Do **not** silently print nothing: state one line, e.g. "library premise
suggestions are suppressed on program-logic goals — reduce with a program-logic
tactic first, or ask for a lemma by name". A blank section reads as "there are
no relevant lemmas", which is a different and false claim.

### Tests

* Program-logic goal ⇒ no ranked block, file-local block present, suppression
  note present.
* Ambient goal ⇒ ranked block present.
* The existing goal-shape fixtures in `integration/tests/test_goal_shape_hints.py`
  are the right source of realistic goal text; reuse rather than invent.

**Done when** the prompt on `G2_bad_ub` step 1 contains the file's lemmas and
no `Number.RealField.*` entries.

---

## Fix 4 — filter structurally before ranking

### Evidence

66% of shown premises share **zero** identifiers with the goal (mean overlap
0.34 identifiers). And 65% of consecutive turns show a byte-identical top-10 —
the "ranked" list barely responds to the proof state, so it is not doing the
job its name implies.

### Where

[`integration/agent/embeddings.py:95`](../integration/agent/embeddings.py#L95)
(`top_premises`) and its call site in `loop.py`.

### Required behaviour

Before cosine ranking, drop any candidate whose statement shares no non-trivial
identifier with the goal's conclusion. Non-trivial means: exclude EasyCrypt
keywords and the ubiquitous tokens (`forall`, `exists`, `res`, `glob`, `true`,
`false`, `pre`, `post`, …). Then rank the survivors as now.

This is a filter, not a new ranker. It is one comparison, it is explainable to
the reader, and on the measured data it removes two thirds of what is shown
today. If nothing survives the filter, say so — "no library lemma shares a
symbol with this goal" is useful information and is honest, where ten unrelated
lemmas are not.

### Tests

* A premise sharing an operator with the goal survives; one sharing only
  `forall` does not.
* Empty survivor set renders the explicit note, not `(none)` and not silence.
* The filter is applied before `top_k` truncation, so `top_k` still yields up
  to `k` *relevant* entries rather than k−(filtered) entries.

---

## Fix 5 — names and shapes, statements on demand

### Evidence

Statements are **80% of the block's bytes**; names are 20%. The mean block is
987 chars (~247 tokens). Meanwhile `lookup_lemma` exists and is used 28 times
across the whole study, 61% of those returning nothing because the agent was
guessing names.

Note the honest framing: the block is 1.4% of a 17k-token prompt, so this is
**not** a token-saving fix. It is a signal-to-noise fix, and it makes the
lookup path usable by giving the agent real names to look up.

### Where

`_format_premises` at
[`integration/agent/prompt.py:1241`](../integration/agent/prompt.py#L1241).

### Required behaviour

One line per premise: qualified name, kind, and a one-line shape (conclusion
head, or `equiv[...]` / `phoare[...]` marker), truncated to a fixed width.
Follow the block with a pointer that the full statement is one `lookup_lemma`
away. Keep the qualified name exactly as `apply`/`rewrite` would need it.

### Tests

* A long statement renders on one line under the width cap and is not
  mid-token truncated.
* The rendered name is the qualified key, unchanged.
* A round trip: the name rendered here is accepted by `lookup_lemma` and
  returns the full statement.

---

## Fix 6 — tag how each name may be used

### Evidence

`smt(hpre)` failed with ``cannot find lemma `hpre'`` while `hpre` was in scope
and printed in the goal — `smt(...)` takes library lemma names, and a local
hypothesis is already part of what the solver sees.
[`integration/agent/ec_context.py`](../integration/agent/ec_context.py) already
draws exactly this distinction (see its module docstring and
`unknown_name_hint`), but the premise block does not carry it.

### Required behaviour

Tag each entry with what it can be used for: `apply`-able (conclusion could
match the goal), `rewrite`-able (an equation or iff), `smt()`-able (a library
lemma, not a local hypothesis). Tags are claims about form, not advice about
strategy — do not write "try this".

### Tests

* An equation is tagged rewrite-able; a non-equational lemma is not.
* A local hypothesis from the goal context never appears with an `smt()` tag.
* Tag text is stable (a prompt-snapshot test), since prompt churn invalidates
  cross-run comparison.

---

## Fix 7 — make "not found" mean something

### Evidence

`G1_G2_eq` spent 71 steps, 5.0 hours and 27 inert moves circling `mem_set`,
with eight `search_lemmas` calls across `FMap` and `SmtMap` that all returned
*"no matches in lemma names or signatures. Try a shorter token or semantic
mode."* The agent read that as a deficient search and kept permuting syntax
instead of concluding the name does not exist here.

Two distinct defects sit on top of each other, and Fix 1 must land first:
today the negative answer was also **wrong** — `mem_set` is in EasyCrypt's dump
and the parser dropped it. Do not make this message more assertive until the
catalog is trustworthy, or the harness will state a falsehood with more
confidence than before.

### Where

[`integration/agent/lemma_search.py:286`](../integration/agent/lemma_search.py#L286)
— the empty-result message.

### Required behaviour

After Fix 1 the catalog is a defensible authority on what exists at this
cursor, so a negative result should state the fact and its scope: which index
was searched, how many entries it holds, and that a name absent from it cannot
be referenced at this proof state. Offer the retry hint *second*, not first —
today the hint is the whole message, which is why it reads as "search harder".

If a theory filter was applied and the theory itself is unknown, say that
separately: "no theory matching `SmtMap` is in scope" is a different fact from
"`mem_set` is not in `SmtMap`".

### Tests

* Empty result names the index size and the scope searched.
* Unknown-theory filter produces the distinct message.
* A name that *is* in the catalog is never reported as absent (regression guard
  against Fix 1 rotting).

---

## Fix 8 — the changelog block: score its parts, and repair the uptake metric

Unlike the premise block, this surface is **not** damaged by Fix 1: it is built
from `changelog_index.json` plus a scan of the installed EasyCrypt sources, not
from `Ax.all`. It is also the only retrieval surface in the harness that
responds to state — [`_refresh_changelog_hints`](../integration/agent/loop.py#L965)
re-aims it at each tactic failure and hops to the next release with a hit. The
problem is not that it is broken; it is that its value has never been separated
from its noise, and the metric that was supposed to do that cannot see the one
case where it plausibly worked.

### What is actually in the block

Assembled in [`repair_hints.py:640-700`](../integration/agent/repair_hints.py#L640),
1.7–3.0 KB per trial (~430–760 tokens), constant within a trial so it sits in
the cached prefix:

| part | source | character |
|---|---|---|
| import/syntax migration summary | `import_repair` | factual, per file; this is the part behind §3.2's "one line makes ten lemmas close" |
| "Where the names in this step are declared" | EC source tree scan (`ec_names`) | factual, state-derived, names only what the failing step referenced |
| "Known EasyCrypt changelog entries in range" | `changelog_index.json` | release-level prose: *"If proofs involve module glob expressions … check glob-related proof steps"* |

Part 3 is advisory rather than factual, and it is shown on the current release
range regardless of the current goal — so on the 94% of turns that are
program-logic (Fix 3) it is strategy advice with no anchor in the proof state.
That is the shape shannon-prover's paper warns about: diagnostics should report
what failed at *this* state and under what condition an action becomes
applicable, not issue global recommendations.

### Evidence

* On in every run: 8 runs record `changelog_hints=True`, 4 predate the field,
  **none record `False`**. `--no-changelog-hints` is a debugging switch — "is a
  hint block *causing* this failure", which one run answers — not the basis for
  a comparison; see [`IMPLEMENTATION_PROGRESS.md`](IMPLEMENTATION_PROGRESS.md)
  §9.1 for why this corpus cannot resolve a between-arm difference.
* `hint_uptake` by run, across the ten runs that report it: `0, 0, 0.5, 0.5,
  0.5, 0.75, 0, 0, 0, 0` — and **0/5 in the definitive run**.
* `changelog_hops` records `(no match)` for 10–11 trials per run: for roughly
  two thirds of trials part 3 matched no release at all.

### 8a — `hint_uptake` is blind to the repair it should be proudest of

`hint_uptake` asks whether an identifier a hint named turns up in a tactic
EasyCrypt accepted. The single ElGamal model repair was a **deletion**:

```
apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).   <- replay break
apply (INDCPA_Sec Adv &m).                              <- model's repair
```

Two arguments dropped after a section-restriction change — and that trial's
block carries r2023.09 *"Enforce section restrictions on the types of declared
modules"*. A hint-driven fix of the deletion or arity kind scores **zero by
construction**, so the 0.0 is a measurement gap, not evidence of no effect.
([`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §6.1 records this repair
independently as the one repeatable result of an earlier A/B.)

Change the metric to count an identifier *removed* from a failing tactic whose
replacement EasyCrypt accepted, as well as one added. If that is too fiddly,
rename the metric to what it measures (`hinted_identifier_added_rate`) and state
the blind spot where it is reported — do not keep publishing 0.0 as though it
meant "no effect".

### 8b — score the three parts separately

Today the whole block is one string and one metric, so a factual, state-derived
name resolution and a paragraph of release prose are credited or blamed
together. Tag each emitted hint with its part in the trial artifact, and report
uptake per part. The prediction worth testing is that parts 1–2 carry the value
and part 3 carries the tokens; that is answerable from artifacts alone, with no
new run.

### 8c — gate the release prose on symbol overlap

Entries already carry `overlap` (which names matched) and `theories_touched`.
Show a part-3 entry only when its overlap names a symbol present in the current
goal or in the failing tactic. Everything else is a release note about a corpus
the agent is not looking at.

**Done when** 8a–8c are in place. All three are offline work on existing
artifacts; none of them needs a run.

---

## Sequencing, and how to know it worked

1. **Fix 1** alone, with the cache-invalidation trap handled. Validate offline
   with the snippet above. Do not run a paid experiment yet.
2. **Fixes 2 + 3** together — they are the two that change what the agent sees
   most, and both are cheap.
3. Re-derive the report tables:
   `python3 -m integration.experiment.effort_metrics integration/output/experiments`
   and state clearly that pre-fix runs are not comparable to post-fix runs.
4. **Fixes 4–6**, justified on the deterministic measures — uptake, coverage of
   wanted names, empty-lookup rate. Do not reach for a paired run to decide
   them: run-to-run variance on this corpus reached **11-vs-1 accepted tactics
   under identical configuration**
   ([`IMPLEMENTATION_PROGRESS.md`](IMPLEMENTATION_PROGRESS.md) §9.1,
   [`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §6.1), which swamps any
   between-arm effect a run of this size could show. None of these fixes needs
   a solve-rate claim to be worth making.
5. **Fix 7** any time; it is independent and self-contained.
6. **Fix 8** any time; it is offline metric work and needs no run at all.

Metrics that should move if these work, all already computed by
`effort_metrics`: premise uptake (1.4% today), premises used in *accepted*
tactics (0 today), share of wanted names ever shown (14% today), lookups
returning nothing (61% today), wrong-layer share of failures (11% in the last
run).

Metrics that may well **not** move: solve rate. §5.4 records four phases of
genuine measured improvement with no change in whether proofs close. Do not
promise this round is different. The defensible claim is narrower and worth
stating exactly: the four lemmas that have never closed need file-local lemmas
that the harness has never once shown them.

## What not to do

* Do not delete the premise block outright. Fixes 1–4 change what it contains,
  so the thing measured in §5.6/§5.7 will not exist afterwards; judge the
  rebuilt block on its own coverage numbers rather than removing the surface
  and losing the comparison.
* Do not switch embedding models as part of this work. Two variables, one
  measurement, and the run-to-run spread on this corpus already reaches
  11-vs-1 accepted tactics under identical configuration
  ([`ELGAMAL_E2E_RESULTS.md`](ELGAMAL_E2E_RESULTS.md) §10.1, quoted in
  `integration/experiment/compare_runs.py`).
* Do not "improve" the ranker with a reranking model. The measured failure is
  that candidates were missing and the list was near-static; solve that with
  the parser fix and the structural filter first, and only then ask whether
  ranking quality is the binding constraint.
* Do not compare a post-fix run against the tables in §5.6/§5.7. Regenerate
  them.

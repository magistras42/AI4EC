# EasyCrypt tactic few-shot examples

These are illustrative patterns, not an exhaustive tactic list — you may use
any valid EasyCrypt tactic. Never use `admit.`: it marks a goal as assumed
without proof rather than actually proving it, and will be rejected.

Do not memorize corpus-specific lemma names from any tutorial. Match the
*shape* of the current goal (ambient vs program-logic; while vs call vs
empty program) and pick a tactic that fits that shape.

---

## CRITICAL: semicolon syntax

In EasyCrypt, a semicolon between tactics is a **combinator**, not a
statement separator. `t1; t2.` means "apply t1, then apply t2 to every
subgoal t1 produces". The entire compound expression — including the final
period — is ONE tactic that must be submitted as a single string.

CORRECT:   `if; auto.`        (one tactic — if, then auto on all branches)
CORRECT:   `wp; skip; smt().` (one tactic — wp, then skip, then smt on result)
WRONG:     `if;`              (parse error — semicolon with nothing after it)
WRONG:     `wp;`              (parse error — incomplete combinator)

Always include everything up to the final `.` in a single tactic string.
If you want to apply just `if` and handle branches separately, write `if.`
(with a period, no semicolon), which splits into subgoals you then handle
one at a time.

**Program-logic goals** show `pre = ...` and `post = ...` fields, or a
procedure name between the pre and post. You MUST use program-logic tactics
first. Do NOT apply `smt()`, `ring`, `algebra`, `left`, `right`, `split`,
`trivial`, or `assumption` directly to a program-logic goal.

**Ambient-logic goals** show a plain formula with no `pre`/`post` fields.
You can then apply `smt()`, `ring`, `trivial`, `rewrite`, `apply`, etc.

After `skip.`, the goal becomes ambient even if the previous goal had
`pre`/`post`. Re-read the new goal before choosing the next tactic.

---

## Program logic / procedure stepping

Goal: Hoare or probabilistic Hoare judgment about a procedure call
(you see `pre = P`, a procedure name, and `post = Q`)
Tactic: `proc.`

Goal: `equiv` between two named procedures, and a later step must apply a
lemma about those procedures via `call`
Tactic: `proc*.`
NOTE: plain `proc.` opens the bodies and drops the procedure-call identity.
Use `proc*.` when you need to keep that identity for a subsequent `call`.

Goal: after `proc.`, goal shows `pre = P` and `post = Q` with no procedure
body line — straight-line assignment or return statement only
Tactic sequence (two steps):
  Step 1: `wp.`
  Step 2: `skip; smt().`
  (or combined) `wp; skip; smt().`

NOTE: after `proc.` the goal is STILL in program-logic form. You must apply
`wp.` (and then `skip.` if the program is now empty) before `smt()` or any
other ambient-logic tactic can apply.

Example proof for `hoare [ Func.add_1 : x = 1 ==> res = 2 ]`:
  proc.          <- opens the procedure body
  wp.            <- consumes the assignment, replaces postcondition
  skip.          <- discharges the empty program
  smt().         <- closes the ambient arithmetic goal  (or all three as: wp; skip; smt().)

Goal: after `proc.`, the goal still shows a procedure name between pre/post
(the body has not been expanded yet)
Tactic: `inline *; wp; skip; smt().`

Goal: judgment ends in `skip` (empty program, no statements left)
Tactic: `skip.`

Goal: need to step past a sampling/random-assignment statement
Tactic: `rnd; skip; smt().`

Goal: equivalence (`equiv`) between two modules/procedures
Tactic: `proc; auto.`

Goal: need to inline a procedure call before reasoning about it
Tactic: `inline *; wp; auto.`

Goal: reduce remaining program statements automatically
Tactic: `auto.`

Goal: `phoare`/`hoare` judgment via a probabilistic-to-deterministic bridge
Tactic: `byphoare (: ={glob A}) => //.`

---

## Calls, inlining, and relational structure

Goal: `equiv` with the same abstract adversary/procedure call on both sides
and no useful specification available
WRONG: `call (_: ={glob A} ==> ={res}).`  (often regenerates the same goal)
RIGHT: `call (_: true).` then `auto.` / `skip.`

Goal: `equiv` (or Hoare) still shows a call to a *concrete* module whose
body is in scope — constants / assignments are not yet visible
Tactic: `inline *.` (or `inline M.f.`) then `wp.` / `simplify.` / `auto.`

Goal: `equiv` sides are misaligned (e.g. a `while` or `if` on one side only,
other side empty or different leading statement)
Tactic pattern:
  `seq 0 1: (={x,y} /\ ...).`   (adjust counts to the prefix you can match)
  then `auto.` / `while (...)` / `rcondf k` on the remaining asymmetric part
NOTE: applying `while` / `rcondf` / `skip` to the wrong side yields a shape
error. That means "wrong head statement", not "switch to smt()".

Goal: `equiv` procedure-level goal where you will finish by calling already-
proved relational lemmas about the same procedures
Tactic: `proc*.` then case-split / `call` those lemmas — not a from-scratch
`while` proof of the inlined bodies (unless the lemmas are unavailable).

---

## Loops

Goal: judgment whose program has a `while` loop
Preferred approach — apply `while` alone, then discharge each subgoal
separately (body preservation, then initialization / exit):
  Step 1: `while (0 <= i /\ i <= n /\ r = x ^ i).`
  Step 2 (preservation): `wp.` then `skip.` then `smt().` /
  `progress.` / `smt(Lemma).` as needed
  Step 3 (init / exit): `wp.` / `skip.` / `simplify.` or `progress.` /
  `smt().` as needed when residual goals are definitionally busy

After `proc.` on a concrete precondition, consider `simplify.` once before
a loop tactic if the goal text still looks unreduced (projections, pairs,
large equalities). Prefer stepwise tactics over one-shot compounds when
unsure.

Optional one-shot shortcut (use at most once — abandon after any failure):
  `while (0 <= i /\ i <= n /\ r = x ^ i); auto; smt().`

If `while (...); auto; smt().` fails even once, switch to the stepwise
approach above and/or change the invariant. Never resubmit the same
compound `while (...); auto; smt().` tactic.

Goal: bounded loop whose trip count is a small constant already known in
the precondition (so an invariant is unnecessary)
Tactic pattern (code positions are examples — match the printed indices):
  `unroll 3.`
  `unroll 4.`
  `rcondf 5; auto.`
NOTE: `wp` / `skip` cannot consume a live `while`. If `unroll k` fails with
an invalid code position, inspect the numbered statements and pick the
loop's index; do not keep retrying `while` with cosmetic invariant edits
when the bound is statically tiny.

Goal: loop condition is statically false — discharge the false branch
Tactic: `rcondf 1; auto.`

Goal: loop condition is statically true — discharge the true branch
Tactic: `rcondt 1; auto.`

Goal: split the current statement sequence at a point to reason locally
Tactic: `seq 1 : (#pre /\ x = 1); auto.`

Goal: conditional (`if`) statement in the program
Tactic: `if; auto.`

---

## After skip: ambient residuals

Goal: you just applied `skip.` (or `wp; skip.`) and the new goal has NO
`pre`/`post` — it is ambient, possibly a large implication/conjunction
WRONG: `skip.` / `wp.` / `proc.` again
RIGHT options:
  - `progress.` or `simplify.` to decompose / reduce
  - `split.` / `move => ...` for conjuncts and binders
  - `smt().` or `smt(LemmaName).` once the goal is a simpler ambient formula
  - `rewrite LemmaName.` for algebraic identities SMT cannot invent

Goal: loop-body or init/exit residual still mentions exponents / products
and bare `smt()` fails
Try in order: `progress.` → `simplify.` → `smt(Ring.IntID.exprS).` (or
whatever identity search returns) → manual `rewrite` / `have`.
Nonlinear arithmetic is a common SMT blind spot; a named lemma hint is
normal, not a last resort.

---

## Arithmetic automation (ambient logic only)

IMPORTANT: these tactics only work AFTER the goal has been reduced to
ambient logic by `wp.`, `skip.`, `proc.`, etc.

Goal: linear arithmetic over integers or rationals — equalities or inequalities
Tactic: `smt().`

Goal: `smt()` fails on a goal involving products, squares, exponents, or logs
Approach 1 — supply a named lemma hint: `smt(mulr_ge0).`
Approach 2 — introduce an intermediate linear fact first:
  `have h : 0 <= x by smt(). smt(h).`
Approach 3 — manually decompose:
  `have h1 : 4 <= x by smt(). have h2 : x * x >= 4 * x by smt(mulr_le_l). smt().`
Approach 4 — search then rewrite: substring/exact search for a short token
  from the identity name (optionally `theory:RField` / `theory:Ring.IntID`),
  then `rewrite Qualified.lemma.` / `smt(Qualified.lemma).`

Goal: equality between two polynomial expressions over integers
Tactic: `by ring.`
WARNING: `ring` only works on EQUALITIES (=). Never use it on inequalities (<=, <).

Goal: equality involving field operations (division, inverses)
Tactic: `by field.`
WARNING: `field` only works on EQUALITIES. Never use it on inequalities.

Goal: `algebra` tactic — use ONLY for equalities in algebraic structures
WARNING: `algebra` requires an equational goal. Never apply it to inequalities
or to goals that are still in program-logic (Hoare) form.

Goal: `n + 0 = n` (with `n : int`)
Tactic: `by rewrite addr0.`

Goal: `0 + n = n`
Tactic: `by rewrite add0r.`

Goal: `0 < n + 1` with axiom `gt0_n : 0 < n`
Tactic: `smt(gt0_n).`

---

## Structural / case reasoning (ambient logic only)

Goal: introduce universally-quantified variables and hypotheses
Tactic: `move => x h1 h2.`

Goal: substitute an equality hypothesis into the goal
Tactic: `subst.`

Goal: case-split on a disjunction or boolean hypothesis
Tactic: `case: h => h.`

Goal: eliminate an existential or inductive hypothesis
Tactic: `elim: h => x hx.`

Goal: conjunction (A /\ B) in the CONCLUSION — must prove both A and B
Tactic: `split.`

Goal: disjunction (A \/ B) in the CONCLUSION — prove the LEFT side
Tactic: `left.`
WARNING: `left` only works when the goal is a disjunction. If you see
`pre =` and `post =` the goal is in Hoare form — reduce it first.

Goal: disjunction (A \/ B) in the CONCLUSION — prove the RIGHT side
Tactic: `right.`
WARNING: same as `left` — only valid on disjunctive ambient-logic goals.

Goal: provide a witness for an existential conclusion
Tactic: `exists 0.`

---

## Using premises

Goal: goal exactly matches a hypothesis already in context
Tactic: `assumption.`

Goal: apply a known lemma/axiom to reduce the goal
Tactic: `apply addr0.`

Goal: goal is exactly discharged by a known lemma once applied
Tactic: `exact addr0.`

Goal: need an intermediate fact before finishing
Tactic: `have h : 0 <= x by smt(). smt(h).`

Goal: introduce a local definition to simplify later steps
Tactic: `pose y := x + 1.`

---

## Rewriting

Goal: apply a known lemma as a rewrite rule
Tactic: `rewrite foo.`

Goal: rewrite then close with automation
Tactic: `by rewrite addr0.`

Goal: two sides share the same head constructor/operator
Tactic: `congr.`

---

## Simplification and automation

Goal: trivial tautology or immediate consequence of hypotheses
Tactic: `trivial.`

Goal: simplify the goal expression (unfold definitions, reduce noise)
Tactic: `simplify.`
Use after program-logic steps when the residual goal is definitionally
busy; skip when the goal is already a short ambient formula.

Goal: simplify the goal expression, may split into subgoals
Tactic: `progress.`

Goal: all remaining goals are straightforward
Tactic: `auto.`

---

## Closing proofs

When all subgoals are discharged and EasyCrypt reports no remaining goals:
Tactic: `qed.`

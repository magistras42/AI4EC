# EasyCrypt tactic few-shot examples

These are illustrative patterns, not an exhaustive tactic list — you may use
any valid EasyCrypt tactic. Never use `admit.`: it marks a goal as assumed
without proof rather than actually proving it, and will be rejected.

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

---

## Program logic / procedure stepping

Goal: Hoare or probabilistic Hoare judgment about a procedure call
(you see `pre = P`, a procedure name, and `post = Q`)
Tactic: `proc.`

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

## Loops

Goal: judgment whose program has a `while` loop
Tactic: `while (0 <= i /\ i <= n); auto; smt().`

Goal: unroll a bounded loop a fixed number of times
Tactic: `unroll 1.`

Goal: loop condition is statically false — discharge the false branch
Tactic: `rcondf 1; auto.`

Goal: loop condition is statically true — discharge the true branch
Tactic: `rcondt 1; auto.`

Goal: split the current statement sequence at a point to reason locally
Tactic: `seq 1 : (#pre /\ x = 1); auto.`

Goal: conditional (`if`) statement in the program
Tactic: `if; auto.`

---

## Arithmetic automation (ambient logic only)

IMPORTANT: these tactics only work AFTER the goal has been reduced to
ambient logic by `wp.`, `skip.`, `proc.`, etc.

Goal: linear arithmetic over integers or rationals — equalities or inequalities
Tactic: `smt().`

Goal: `smt()` fails on a goal involving products, squares, or other nonlinear terms
Approach 1 — supply a named lemma hint: `smt(mulr_ge0).`
Approach 2 — introduce an intermediate linear fact first:
  `have h : 0 <= x by smt(). smt(h).`
Approach 3 — manually decompose:
  `have h1 : 4 <= x by smt(). have h2 : x * x >= 4 * x by smt(mulr_le_l). smt().`

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

Goal: simplify the goal expression, may split into subgoals
Tactic: `progress.`

Goal: all remaining goals are straightforward
Tactic: `auto.`

---

## Closing proofs

When all subgoals are discharged and EasyCrypt reports no remaining goals:
Tactic: `qed.`

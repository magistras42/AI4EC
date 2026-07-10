# EasyCrypt tactic few-shot examples

These are illustrative patterns, not an exhaustive tactic list — you may use
any valid EasyCrypt tactic. Never use `admit.`: it marks a goal as assumed
without proof rather than actually proving it, and will be rejected.

## Program logic / procedure stepping

Goal: Hoare or probabilistic Hoare judgment about a procedure call
Tactic: `proc.`

Goal: after `proc`, discharge a straight-line assignment/return
Tactic: `wp; skip; smt().`

Goal: judgment ends in `skip` (empty program)
Tactic: `skip.`

Goal: need to step past a sampling/random-assignment statement
Tactic: `rnd; skip; smt().`

Goal: equivalence between two modules/procedures
Tactic: `proc; auto.`

Goal: need to inline a procedure call before reasoning about it
Tactic: `inline *; wp; auto.`

Goal: reduce remaining program statements automatically
Tactic: `auto.`

Goal: `phoare`/`hoare` judgment via a probabilistic-to-deterministic bridge
Tactic: `byphoare (: ={glob A}) => //.`

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

## Structural / case reasoning

Goal: introduce a bound/universally-quantified variable and hypotheses
Tactic: `move => x h1 h2.`

Goal: substitute an equality hypothesis into the goal
Tactic: `subst.`

Goal: case-split on a disjunction or boolean hypothesis
Tactic: `case: h => h.`

Goal: eliminate an existential or inductive hypothesis
Tactic: `elim: h => x hx.`

Goal: conjunction in the conclusion
Tactic: `split.`

Goal: disjunction in the conclusion — prove the left side
Tactic: `left.`

Goal: disjunction in the conclusion — prove the right side
Tactic: `right.`

Goal: provide a witness for an existential conclusion
Tactic: `exists 0.`

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

## Automation & rewriting

Goal: need to apply a known lemma via rewriting
Tactic: `rewrite foo.`

Goal: rewrite then close automatically
Tactic: `by rewrite addr0.`

Goal: `n + 0 = n` (with `n : int`)
Tactic: `by rewrite addr0.`

Goal: `0 + n = n`
Tactic: `by rewrite add0r.`

Goal: simple ring equality over integers
Tactic: `by ring.`

Goal: field equality (division/inverse involved)
Tactic: `by field.`

Goal: `0 < n + 1` with axiom `gt0_n : 0 < n`
Tactic: `smt(gt0_n).`

Goal: trivial hypothesis or immediate consequence of context
Tactic: `trivial.`

Goal: simplify the goal expression before continuing
Tactic: `progress.`

Goal: normalize a goal via algebraic identities
Tactic: `algebra.`

Goal: two sides of an equality share the same head symbol
Tactic: `congr.`

## Closing proofs

When all subgoals are discharged:
Tactic: `qed.`

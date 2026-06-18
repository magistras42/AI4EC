# EasyCrypt tactic few-shot examples

## Arithmetic / ring goals

Goal: `n + 0 = n` (with `n: int`)
Tactic: `by rewrite addr0.`

Goal: `0 + n = n`
Tactic: `by rewrite add0r.`

Goal: simple ring equality over integers
Tactic: `by ring.`

## SMT on simple logical goals

Goal: `0 < n + 1` with axiom `gt0_n : 0 < n`
Tactic: `smt(gt0_n).`

## Rewriting

Goal: need to apply a known lemma `foo`
Tactic: `rewrite foo.`

Goal: rewrite then close
Tactic: `by rewrite addr0.`

## Program logic

Goal: probabilistic Hoare judgment
Tactic: `proc; auto.`

Goal: equivalence between modules
Tactic: `proc; rnd; auto.`

## Structural

Goal: conjunction in conclusion
Tactic: `split.`

Goal: trivial hypothesis
Tactic: `trivial.`

## Closing proofs

When all subgoals are discharged:
Tactic: `qed.`

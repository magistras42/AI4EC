# Experiment 003 — Incomplete Proof / Unsolved Goal

## Goal

Create an EasyCrypt proof that starts a proof block but does not apply any tactic to solve the goal.

## Broken File

`proof_repair/broken_proofs/broken_003_incomplete_proof.ec`

## Broken Code

```easycrypt
require import Int.

lemma test_add0_unsolved (x:int): 0 + x = x.
proof.
qed.

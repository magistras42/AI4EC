# Experiment 002 — Goal Shape Mismatch / Wrong Rewrite

## Goal

Create an EasyCrypt proof where the referenced lemma exists, but the current goal does not contain a matching expression for the rewrite tactic.

## Broken File

`proof_repair/broken_proofs/broken_002_goal_shape_mismatch.ec`

## Broken Code

```easycrypt
require import Int.

lemma add0_right (x:int): x + 0 = x.
proof.
  smt.
qed.

lemma test_add0_left (x:int): 0 + x = x.
proof.
  rewrite add0_right.
qed.

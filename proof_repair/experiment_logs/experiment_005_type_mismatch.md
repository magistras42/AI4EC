# Experiment 005 — Type Mismatch

## Goal

Create an EasyCrypt file where the lemma statement compares terms of incompatible types.

## Broken File

`proof_repair/broken_proofs/broken_005_type_mismatch.ec`

## Broken Code

```easycrypt
require import Int.

lemma test_type_mismatch (x:int): x = true.
proof.
  smt.
qed.

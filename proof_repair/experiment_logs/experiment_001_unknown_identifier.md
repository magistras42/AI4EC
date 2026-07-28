# Experiment 001 — Unknown Lemma Error

## Goal

Create a simple EasyCrypt proof with an intentionally invalid lemma reference and record the resulting compiler error.

## Broken File

`proof_repair/broken_proofs/broken_001_unknown_identifier.ec`

## Broken Code

```easycrypt
require import Int.

lemma test_add0 (x:int): 0 + x = x.
proof.
  rewrite nonexistent_lemma.
qed.

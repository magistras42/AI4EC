# Experiment 004 — Syntax Error / Parse Error

## Goal

Create an EasyCrypt file with a syntax error caused by a missing period after the lemma statement.

## Broken File

`proof_repair/broken_proofs/broken_004_syntax_error.ec`

## Broken Code

```easycrypt
require import Int.

lemma test_syntax_error (x:int): 0 + x = x
proof.
  smt.
qed.

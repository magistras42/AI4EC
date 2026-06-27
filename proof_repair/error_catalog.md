# EasyCrypt Error Catalog

This file records EasyCrypt proof errors, their likely causes, and possible repair strategies.

| ID | Error Category | Error Message / Symptom | Likely Cause | Repair Strategy | Example File |
|---|---|---|---|---|---|
| E001 | Unknown Identifier / Unknown Lemma | `[critical] ... unknown lemma \`nonexistent_lemma\`` | The proof tries to rewrite with a lemma that does not exist or is not available in the current imports/context | Replace the nonexistent lemma with an available lemma, import the required theory, or use a different tactic such as `smt` when appropriate | `broken_proofs/broken_001_unknown_identifier.ec` |
| E002 | Tactic Failure | A tactic cannot close or transform the current goal | The tactic does not match the current proof state | Inspect the goal and use a more appropriate tactic or intermediate lemma | TBD |
| E003 | SMT Failure | `smt` fails to prove the goal automatically | The goal requires additional assumptions, imported lemmas, or manual proof steps | Add r| E003 | SMT Failure | ` rewrite first, or decompose the goal | TBD |
| E004 | Type Mismatch | Expression has a different type than expected | The proof or statement uses incompatible terms | Check term types and adjust expression, quantifier, or lemma | TBD |
| E005 | Goal Shape Mismatch | Rewrite/apply tactic does not match the current goal | The target expression is absent or has a different syntactic form | Rewrite the goal into the expected shape or use a different lemma | TBD |

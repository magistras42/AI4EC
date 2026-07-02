# EasyCrypt Error Catalog

This file records EasyCrypt proof errors, their likely causes, and possible repair strategies.

| ID | Error Category | Error Message / Symptom | Likely Cause | Repair Strategy | Example File |
|---|---|---|---|---|---|
| E001 | Unknown Identifier / Unknown Lemma | `[critical] ... unknown lemma \`nonexistent_lemma\`` | The proof tries to rewrite with a lemma that does not exist or is not available in the current imports/context | Replace the nonexistent lemma with an available lemma, import the required theory, or use a different tactic such as `smt` when appropriate | `broken_proofs/broken_001_unknown_identifier.ec` |
| E002 | Goal Shape Mismatch / Wrong Rewrite | `[critical] ... nothing to rewrite` | The proof uses an existing lemma, but the current goal does not contain a matching expression for the rewrite tactic | Use a lemma that matches the current goal shape, rewrite the goal first, or replace the tactic with a more suitable one such as `smt` | `broken_proofs/broken_002_goal_shape_mismatch.ec` |
| E003 | Incomplete Proof / Unsolved Goal | `[critical] ... cannot save an incomplete proof` | The proof reaches `qed.` while one or more goals remain unsolved | Add a tactic that closes the goal, such as `smt`, or provide intermediate proof steps before `qed.` | `broken_proofs/broken_003_incomplete_proof.ec` |
| E004 | Syntax Error / Parse Error | `[critical] ... parse error` | The EasyCrypt file contains invalid syntax, such as a missing period after a declaration | Fix the syntax before attempting proof repair | `broken_proofs/broken_004_syntax_error.ec` |
| E005 | Type Mismatch | `[critical] ... no matching operator, named \`=\`, ... int / bool` | The statement or expression uses terms with incompatible types | Modify the statement or expression so both sides have compatible types | `broken_proofs/broken_005_type_mismatch.ec` |

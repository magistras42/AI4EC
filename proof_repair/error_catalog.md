# EasyCrypt Error Catalog

This file records EasyCrypt proof errors, their likely causes, and possible repair strategies.

| ID | Error Category | Error Message / Symptom | Likely Cause | Repair Strategy | Synthetic Example | Real-world Example |
|---|---|---|---|---|---|---|
| E001 | Unknown Identifier / Unknown Lemma | `[critical] ... unknown lemma \`nonexistent_lemma\`` | The proof refers to a lemma or identifier that is not available in the current context. | Use an existing lemma, import the missing theory, or correct the lemma name. | `broken_proofs/...` | Not yet found |
| E002 | Goal Shape Mismatch / Wrong Rewrite | `[critical] ... nothing to rewrite` | The proof uses a rewrite tactic that does not match the current goal. | Inspect the current goal and use a lemma or rewrite direction that matches the goal shape. | `broken_proofs/...` | Not yet found |
| E003 | Incomplete Proof / Unsolved Goal | `[critical] ... cannot save an incomplete proof` | The proof script ends before all goals are discharged. | Add the missing proof steps, split remaining goals, or provide stronger tactics. | `broken_proofs/...` | Searching |
| E004 | Syntax Error / Parse Error | `[critical] ... parse error` | The EasyCrypt file contains invalid syntax. | Fix malformed syntax, missing punctuation, or invalid command structure. | `broken_proofs/...` | Not yet found |
| E005 | Type Mismatch | `[critical] ... no matching operator, named \`=\`, ... int / bool` | The statement compares or combines expressions of incompatible types. | Correct the expression types or use an appropriate operator/lemma. | `broken_proofs/...` | Not yet found |
| R001 | SMT Hint / Proof Guidance | SMT call requires additional lemmas or more explicit proof steps | The proof structure is mostly correct, but the SMT solver does not receive enough guidance after proof infrastructure changes. | Add missing SMT hints and/or expand the tactic script into smaller steps. | N/A | `real_world/REAL-001.md` |
| R002 | Tactic Refinement / Redundant Proof Step | Proof script contains unnecessary tactic steps or unfolds | The proof still works conceptually, but some tactic steps are redundant or trigger warnings after infrastructure changes | Remove redundant tactics or simplify the proof script | N/A | `real_world/REAL-002.md` |
| R003 | SMT Abstraction / Opaque Operator | SMT struggles with recursive definitions or unfolds them in undesirable ways | Recursive definitions exposed directly to SMT can cause inefficient or problematic proof search | Mark definitions as `smt_opaque` and expose controlled helper lemmas | N/A | `real_world/REAL-003.md` |
| R004 | Redundant Unfold / Tactic Refinement | Proof script contains unnecessary unfolds or redundant rewrite components | The proof unfolds a definition that is no longer needed or triggers useless unfold warnings | Remove unnecessary unfolds or simplify the rewrite tactic | N/A | `real_world/REAL-004.md` |


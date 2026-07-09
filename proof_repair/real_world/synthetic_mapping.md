# Mapping Between Synthetic Error Taxonomy and Real-World Cases

## Purpose

This document maps the current synthetic EasyCrypt proof repair taxonomy to real-world EasyCrypt maintenance and repair cases collected from upstream commit histories. The goal is to evaluate whether the existing synthetic categories sufficiently cover practical proof failures and maintenance patterns.

## Synthetic Categories

| ID | Synthetic Category | Description |
|---|---|---|
| E001 | Unknown Identifier / Unknown Lemma | A proof references an identifier, lemma, or definition that is not available in the current context. |
| E002 | Goal Shape Mismatch / Wrong Rewrite | A rewrite or tactic is applied to a goal whose shape does not match the expected pattern. |
| E003 | Incomplete Proof / Unsolved Goal | A proof script leaves one or more goals unsolved. |
| E004 | Syntax Error / Parse Error | The proof script contains invalid EasyCrypt syntax. |
| E005 | Type Mismatch | A tactic or expression is applied with an incompatible type. |

## Real-World Mapping

| Real-world ID | Real-world Category | Closest Synthetic Category | Coverage | Notes |
|---|---|---|---|---|
| R001 | SMT Hint / Proof Guidance | E003 | Partial | The proof is not syntactically wrong, but SMT needs additional explicit lemmas to close the goal. |
| R002 | Tactic Refinement / Redundant Proof Step | E003 | Partial | The proof script is simplified by removing unnecessary tactic steps. |
| R003 | SMT Abstraction / Opaque Operator | E003 | Partial | The issue is not a missing proof step, but excessive SMT unfolding; this is not directly covered by the synthetic taxonomy. |
| R004 | Redundant Unfold / Tactic Refinement | E002 | Partial | Related to rewrite/unfold misuse, but the real-world case is more about removing useless unfolding than fixing a wrong rewrite. |
| R005 | SMT Call Simplification / Proof Decomposition | E003 | Partial | Broad SMT calls are replaced by explicit rewrites and smaller proof obligations. |
| R006 | Tactic Simplification / Proof Script Maintenance | E003 | Partial | Multiple tactic steps are merged into a more compact equivalent form. |
| R007 | Async While Obligation / Tactic Refinement | E003 | Partial | The proof obligation structure changed due to tactic behavior; existing synthetic categories only partially cover this. |
| R008 | Library Definition / Axiom Replacement | None | Not covered | This is a library-level maintenance case where axioms were replaced by a concrete definition. |
| R009 | Proc-change Fresh Local Binding | E004 / E005 | Partial | This relates to tactic syntax and expressiveness, but it is not simply a parse or type error. |
| R010 | Proc-change Framing / Regression Test | None | Not covered | This is a regression/maintenance case about tactic observability and framing behavior. |

## Coverage Analysis

The current synthetic taxonomy covers basic proof repair failures such as unknown identifiers, wrong rewrites, incomplete proofs, syntax errors, and type mismatches. However, the real-world cases show that practical EasyCrypt maintenance often involves more structural or tactic-specific changes.

Several real-world cases are only partially covered by the existing taxonomy. In particular, SMT-related repairs often require adding explicit lemmas, controlling unfolding, or decomposing proof obligations rather than simply fixing an incomplete proof. Similarly, tactic maintenance cases such as `proc change` and `async while` involve changes in tactic-generated obligations and framing behavior that are not directly represented by the current synthetic categories.

## Proposed Additional Categories

| Proposed ID | Category | Motivation |
|---|---|---|
| N001 | SMT Guidance / Hint Refinement | Covers cases where SMT needs additional lemmas, reduced unfolding, or more explicit proof decomposition. |
| N002 | Tactic Behavior / Obligation Change | Covers cases where a tactic generates different proof obligations across EasyCrypt versions or implementations. |
| N003 | Library Definition / Abstraction Change | Covers maintenance cases where library definitions, axioms, or opacity annotations change proof behavior. |
| N004 | Regression / Tactic Semantics Test | Covers real-world maintenance cases that add tests to preserve or document tactic behavior. |
| N005 | Compatibility / Import Migration | Covers version-related failures such as renamed theories, moved imports, or changed standard library structure. |

## Conclusion

The current five synthetic categories are useful as a starting point, but they do not fully cover the range of real-world EasyCrypt proof maintenance cases. The main gaps are SMT guidance, tactic-generated obligation changes, library abstraction changes, and compatibility-related failures. These should be represented as additional categories or as subcategories in the extended taxonomy.

# REAL-005: SMT call simplification in dynamic matrix proof

## Source

- Repository: EasyCrypt/easycrypt
- Commit: f644a86f
- File: `theories/algebra/DynMatrix.eca`
- Location: around lemma `catmr_subm`

## Category

- Error category: SMT call simplification / proof decomposition
- Repair type: Replace broad SMT calls with explicit rewrites and smaller proof obligations

## Summary

This case comes from an actual EasyCrypt upstream commit titled "simplify some smt calls." The original proof relied on several direct `smt` calls to discharge matrix size and bound obligations. The repaired version makes the proof more explicit by rewriting matrix dimensions first, using direct proof obligations such as `/#`, and reducing the number of SMT-heavy steps.

## Before / After Pattern

Before:

```easycrypt
split => [| i j bound]. 
- smt(rows_catmr cols_catmr size_subm).
```

After:

```easycrypt
rewrite rows_catmr cols_catmr 2!cols_subm 2!rows_subm /= maxzz /=.
split => [/#| i j bound].
```

## Additional Repair

Before:

```easycrypt
rewrite get_subm /=; first 2 smt(size_catmr size_subm).
```

After:

```easycrypt
rewrite get_subm /= 1,2:/# (getm0E (subm _ _ _ _ _)).
```

## Analysis

This repair demonstrates a realistic proof-maintenance case where broad SMT calls are replaced with more explicit proof steps. The overall proof strategy remains unchanged, but the repaired proof reduces reliance on SMT by exposing the relevant rewrites and decomposing the proof into simpler obligations.

## Relation to Error Catalog

- R005: SMT Call Simplification / Proof Decomposition

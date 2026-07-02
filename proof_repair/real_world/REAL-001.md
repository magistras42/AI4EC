# REAL-001: SMT hint and tactic refinement in CBC proof

## Source

- Repository: EasyCrypt/easycrypt
- Commit: b0c4c726
- File: `examples/MEE-CBC/CBC.eca`
- Location: around line 502

## Category

- Error category: SMT hint / insufficient proof guidance
- Repair type: SMT hint refinement + tactic refinement

## Summary

This case comes from an actual EasyCrypt upstream commit. The proof structure remains mostly unchanged, but several `smt` calls were repaired by adding the lemma `addbC` to the SMT hint set. One proof step was also expanded into a more explicit sequence using `split`, `congr`, and `smt()`.

## Before / After Pattern

Before:

```easycrypt
smt(addbA addbK add0b)
```

After:

```easycrypt
smt(addbA addbK addbC add0b)
```

## Additional Repair

Before:

```easycrypt
by split=> [|/#]; 1:smt(addbA addbK add0b).
```

After:

```easycrypt
by split=> [|/#];
  split;
  [congr; congr; smt(addbA addbK addbC add0b)
  | smt()].
```

## Analysis

This repair demonstrates a realistic proof-maintenance scenario. Rather than changing the overall proof strategy, the repair strengthens the SMT guidance by introducing additional lemmas and slightly refines the tactic script. The proof logic remains the same, but the updated proof becomes compatible with changes in EasyCrypt's proof infrastructure.

## Relation to Error Catalog

- R001: SMT Hint / Proof Guidance



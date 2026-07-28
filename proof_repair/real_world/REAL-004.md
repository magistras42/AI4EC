# REAL-004: Removal of unnecessary unfold in finite map proof

## Source

- Repository: EasyCrypt/easycrypt
- Commit: 6890b877
- File: `theories/datatypes/FMap.ec`
- Location: around lemma `offset_get`

## Category

- Error category: Redundant unfold / tactic refinement
- Repair type: Removal of unnecessary unfold

## Summary

This case comes from an actual EasyCrypt upstream commit that enabled warnings for useless unfolds by default. The proof structure remains unchanged, but the rewrite step was refined by removing an unnecessary unfold of `/ofset`.

## Before / After Pattern

Before:

```easycrypt
rewrite getE /ofset ofmapK.
```

After:

```easycrypt
rewrite getE ofmapK.
```

## Analysis

This repair demonstrates a realistic proof-maintenance case where the proof script contains an unnecessary unfold. The repair does not change the proof strategy, but simplifies the tactic sequence to avoid redundant proof steps and warnings.

## Relation to Error Catalog

- R004: Redundant Unfold / Tactic Refinement

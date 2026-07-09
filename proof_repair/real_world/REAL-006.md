# REAL-006: Proc-change proof simplification

## Source

- Repository: EasyCrypt/easycrypt
- Commit: 78cf6eb1
- File: `tests/procchange.ec`
- Location: around `ProcChangeFrameTest`

## Category

- Error category: Tactic simplification / proof script maintenance
- Repair type: Replace separate proof steps with a compact tactic form

## Summary

This case comes from an actual EasyCrypt upstream commit that tightened proc-change observability tests. In several proof scripts, the original sequence `proc.` followed by `simplify.` was replaced with the more compact `proc=> /=.` form. The proof intent remains the same, but the repaired script is shorter and more direct.

## Before / After Pattern

Before:

    proc.
    simplify.

After:

    proc=> /=.

## Analysis

This repair demonstrates a realistic proof-script maintenance pattern where separate tactic steps are merged into a more concise form. The proof obligation is not changed in this positive case; only the proof script is simplified.

## Relation to Error Catalog

- R006: Tactic Simplification / Proof Script Maintenance

# REAL-007: Async while proof obligation repair

## Source

- Repository: EasyCrypt/easycrypt
- Commit: 267f8273
- File: `examples/async-while.ec`
- Location: around the `async while` proof

## Category

- Error category: Async while proof obligation / tactic refinement
- Repair type: Update loop exit conditions and reorder proof obligations

## Summary

This case comes from an actual EasyCrypt upstream commit that fixed async while obligations. The original proof used loop conditions such as `i{1} < n * k` and `i{2} < n`, while the repaired proof uses their negated exit conditions directly. The proof obligations were also simplified and reordered.

## Before / After Pattern

Before:

    async while
      [ (fun r => i%r < k%r * r), (i{2} + 1)%r ]
      [ (fun r => i%r < r), (i{2} + 1)%r ]
        (i{1} < n * k /\ i{2} < n) (!(i{2} < n))

After:

    async while
      [ (fun r => i%r < k%r * r), (i{2} + 1)%r ]
      [ (fun r => i%r < r), (i{2} + 1)%r ]
        (!(i{1} < n * k)) (!(i{2} < n))

## Additional Repair

Before:

    + by move=> &1 &2 />; smt(gt0_k).
    + by move=> &1 &2 />; smt(gt0_k).
    + by move=> &2; exfalso=> &1; smt(gt0_k).
    + by move=> &2; exfalso=> &1 ?; smt(gt0_k).

After:

    + move=> &1 &2 />; smt(gt0_k).
    + move=> &2; exfalso=> &1 ? ; smt(gt0_k).
    + move=> &1; exfalso=> &2 ?; smt(gt0_k).

## Analysis

This repair demonstrates a realistic maintenance case where a tactic-generated proof obligation changed. The proof strategy remains based on `async while`, but the exit conditions and side conditions are adjusted to match the updated tactic behavior.

## Relation to Error Catalog

- R007: Async While Obligation / Tactic Refinement

# REAL-009: Proc-change repair with fresh local variable binding

## Source

- Repository: EasyCrypt/easycrypt
- Commit: 042456e8
- File: `tests/procchange.ec`
- Location: around `ProcChangeAssignEquiv`

## Category

- Error category: Tactic feature change / proc-change replacement
- Repair type: Add fresh local variable binding in replacement block

## Summary

This case comes from an actual EasyCrypt upstream commit that extended `proc change` to support binding fresh local variables. The replacement program was changed from a direct assignment to a block that introduces a fresh local variable and then assigns it to the original variable.

## Before / After Pattern

Before:

    proc change {1} [1..3] : { x <- 3; }.

After:

    proc change {1} [1..3] : [y : int] { y <- 3; x <- y; }.

## Analysis

This repair demonstrates a tactic-level maintenance case caused by a new `proc change` capability. The proof still uses the same tactic, but the replacement block becomes more expressive by explicitly binding a fresh local variable.

## Relation to Error Catalog

- R009: Proc-change Fresh Local Binding

# REAL-010: Proc-change framing regression tests

## Source

- Repository: EasyCrypt/easycrypt
- Commit: cc03b304
- File: `tests/procchange.ec`
- Location: proc-change precondition framing section

## Category

- Error category: Regression test / precondition framing
- Repair type: Add real maintenance tests for proc-change observability and framing behavior

## Summary

This case comes from an actual EasyCrypt upstream commit that improved rewrite framing with formula-level read tracking. The commit adds and reorganizes `proc change` tests to check whether preconditions can reach the changed program location.

## Before / After Pattern

Before:

    theory ProcChangeAssignHoareEquiv.

After:

    theory ProcChangeAssignHoare.

## Additional Maintenance Case

The commit also adds positive and negative framing tests such as:

    proc change 2 : {
      x <- 4;
    }; by auto.

and failure cases such as:

    fail proc change 3 : {
      x <- 4;
    }; by auto.

## Analysis

This case is not a minimal proof-script repair in the same sense as REAL-001 or REAL-005. Instead, it is a real EasyCrypt maintenance case that documents and tests tactic behavior around precondition framing. It is useful for the dataset because it captures a recurring class of failures where a tactic must respect whether a precondition can reach the modified program statement.

## Relation to Error Catalog

- R010: Proc-change Framing / Regression Test

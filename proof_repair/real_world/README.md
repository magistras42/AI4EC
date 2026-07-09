# Real-world Proof Repair Cases

This directory contains proof repair examples collected from actual EasyCrypt repositories.

Unlike manually constructed examples, these cases originate from real development history and represent realistic proof maintenance scenarios.


## Case Index

| ID | Category | Repository | Commit | File | Status |
|----|----------|------------|--------|------|--------|
| REAL-001 | SMT Hint Refinement | EasyCrypt/easycrypt | b0c4c726 | `examples/MEE-CBC/CBC.eca` | ✅ |
| REAL-002 | Tactic Simplification | EasyCrypt/easycrypt | 1c7e6d78 | `examples/SchnorrPK.ec` | ✅ |
| REAL-003 | SMT Abstraction / Opaque Operator | EasyCrypt/easycrypt | e2d50009 | `theories/algebra/Perms.ec` | ✅ |
| REAL-004 | Redundant Unfold / Tactic Refinement | EasyCrypt/easycrypt | 6890b877 | `theories/datatypes/FMap.ec` | ✅ |
| REAL-005 | SMT Call Simplification / Proof Decomposition | EasyCrypt/easycrypt | f644a86f | `theories/algebra/DynMatrix.eca` | ✅ |
| REAL-006 | Tactic Simplification / Proof Script Maintenance | EasyCrypt/easycrypt | 78cf6eb1 | `tests/procchange.ec` | ✅ |
| REAL-007 | Async While Obligation / Tactic Refinement | EasyCrypt/easycrypt | 267f8273 | `examples/async-while.ec` | ✅ |
| REAL-008 | Library Definition / Axiom Replacement | EasyCrypt/easycrypt | ecb33950 | `theories/algebra/Perms.ec` | ✅ |
| REAL-009 | Proc-change Fresh Local Binding | EasyCrypt/easycrypt | 042456e8 | `tests/procchange.ec` | ✅ |
| REAL-010 | Proc-change Framing / Regression Test | EasyCrypt/easycrypt | cc03b304 | `tests/procchange.ec` | ✅ |


## Notes

The current collection focuses on proof-script maintenance cases extracted from the official EasyCrypt repository.

Compatibility and version-migration failures (e.g., renamed imports or removed standard library modules) will be documented separately, since they represent a preprocessing stage before proof repair.


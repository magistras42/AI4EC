# Compatibility and Import Migration

## Purpose

This document defines compatibility and import migration as a separate stage from proof-script repair. Some old EasyCrypt repositories fail before proof checking begins because they rely on older EasyCrypt versions, standard-library names, theory locations, or syntax that no longer exist in the current EasyCrypt version.

These failures should not be treated as ordinary proof-script repair cases because EasyCrypt may not generate a proof obligation at all.

## Motivation

During repository mining, some old EasyCrypt files failed with hard import or versioning errors such as missing theories or renamed standard-library modules. Examples include failures related to older theories such as `Option`, `NewDistr`, or standalone `Array`.

In these cases, the mathematical proof may still be valid, but the file cannot reach the proof-checking stage under the current EasyCrypt version.

## Distinction from Proof-Script Repair

Compatibility migration and proof-script repair should be handled as separate stages.

| Stage | Goal | Example |
|---|---|---|
| Compatibility / Import Migration | Make the file load and reach proof obligation generation under the current EasyCrypt version | Rename imports, update theory paths, migrate old syntax |
| Proof-Script Repair | Repair the proof script while preserving the original theorem statement | Add SMT hints, update rewrite steps, refine tactics |

## Allowed Compatibility Repairs

Allowed compatibility repairs should be deterministic and should preserve the original proof obligation as much as possible.

Examples:

- Rename an import when a theory has moved or changed name.
- Replace an old module path with its current equivalent.
- Update syntax that changed across EasyCrypt versions.
- Record the original EasyCrypt version or dependency version when available.
- Add missing imports if they correspond to existing dependencies used by the original file.

## Disallowed Repairs

The compatibility stage should not allow changes that move the goalpost of the proof.

Disallowed changes include:

- Changing lemma or theorem statements.
- Weakening preconditions or postconditions.
- Changing the meaning of the specification being proved.
- Replacing the original theorem with a different theorem.
- Allowing the LLM to freely modify proof obligations.

## Success Criteria

A compatibility repair is successful if:

1. The file loads under the target EasyCrypt version.
2. The original theorem or lemma statement is preserved.
3. EasyCrypt can generate the same intended proof obligation, or a clearly equivalent one.
4. Any migration is documented as a deterministic version or import update.

Compilation alone is not always sufficient, because a file could compile after changing the theorem statement. Therefore, compatibility repair should be evaluated with an explicit repair-scope policy.

## Relationship to the Dataset

Compatibility cases should be recorded separately from proof-script repair cases.

Suggested ID format:

| ID Prefix | Meaning |
|---|---|
| REAL | Real-world proof-script or proof-maintenance case |
| COMPAT | Compatibility, import, or version migration case |

Examples:

| ID | Category | Description |
|---|---|---|
| COMPAT-001 | Missing Theory Import | Old file fails because a theory no longer exists under the same name. |
| COMPAT-002 | Standard Library Migration | Old proof depends on a stdlib module that was renamed, merged, or removed. |
| COMPAT-003 | Syntax Migration | Old EasyCrypt syntax must be updated before proof checking can begin. |

## Open Question

The main unresolved question is how to verify that a compatibility repair preserves the same proof obligation. For now, we restrict compatibility repair to deterministic migrations and do not allow the LLM to freely modify theorem statements or proof goals.

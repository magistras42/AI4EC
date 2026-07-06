# REAL-002: Tactic simplification in Schnorr proof

## Source

- Repository: EasyCrypt/easycrypt
- Commit: 1c7e6d78
- File: `examples/SchnorrPK.ec`
- Location: around line 126

## Category

- Error category: Tactic refinement / redundant proof step
- Repair type: Tactic simplification

## Summary

This case comes from an actual EasyCrypt upstream commit that fixed unused unfold warnings. The proof structure remains unchanged, but an unnecessary tactic sequence was simplified. The original script used `wp` and a rewrite of `/snd` before `auto`, while the repaired version directly applies `auto`.

## Before / After Pattern

Before:

```easycrypt
wp; rewrite /snd /=; auto => &hr />.
```

After:

```easycrypt
auto=> &hr />.
```

## Analysis

This repair demonstrates a realistic proof-maintenance case where the proof does not require a new strategy or additional lemma. Instead, the proof script is simplified by removing redundant proof steps. This type of repair is useful for evaluating whether an LLM can identify unnecessary tactics and produce a cleaner proof script.

## Relation to Error Catalog

- R002: Tactic Refinement / Redundant Proof Step


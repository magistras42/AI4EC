# REAL-008: Replacing axiomatized permutation operator with implementation

## Source

- Repository: EasyCrypt/easycrypt
- Commit: ecb33950
- File: `theories/algebra/Perms.ec`
- Location: near definition of `allperms_r`

## Category

- Error category: Library definition / proof maintenance
- Repair type: Replace axioms with concrete recursive definition and update proofs

## Summary

This case comes from an actual EasyCrypt upstream commit that implemented `allperms_r` instead of keeping it as an abstract operator with axioms. The repair removes axioms and hint rewrites, introduces a concrete recursive definition, and updates affected proofs accordingly.

## Before / After Pattern

Before:

    op allperms_r (n : unit list) (s : 'a list) : 'a list list.

    axiom allperms_r0 (s : 'a list) :
      allperms_r [] s = [[]].

    axiom allperms_rS (x : unit) (n : unit list) (s : 'a list) :
      allperms_r (x :: n) s = flatten (
        map (fun x => map ((::) x) (allperms_r n (rem x s))) (undup s)).

    hint rewrite ap_r : allperms_r0 allperms_rS.

After:

    op allperms_r (n : unit list) (s : 'a list) : 'a list list =
    with n = [] => [[]]
    with n = x::n => flatten (
        map (fun x => map ((::) x) (allperms_r n (rem x s))) (undup s)).

## Additional Repair

Before:

    elim: n s => [|? n ih] s; rewrite ?ap_r //.

After:

    elim: n s => [|? n ih] s; rewrite ?ap_r //=.

## Analysis

This repair demonstrates a real-world maintenance case where a previously axiomatized library component was replaced by an implementation. This required small proof-script changes because unfolding behavior and rewrite behavior changed.

## Relation to Error Catalog

- R008: Library Definition / Axiom Replacement

# REAL-003: SMT opacity repair for permutation operator

## Source

- Repository: EasyCrypt/easycrypt
- Commit: e2d50009
- File: `theories/algebra/Perms.ec`
- Location: near definition of `allperms_r`

## Category

- Error category: SMT abstraction / excessive unfolding
- Repair type: SMT opacity annotation + explicit helper lemmas

## Summary

This case comes from an actual EasyCrypt upstream commit. The recursive operator `allperms_r` was marked as `[smt_opaque]`, preventing the SMT solver from unfolding it automatically. The repair also introduced explicit lemmas, `allperms_r0` and `allperms_rS`, to expose the intended unfolding behavior in a controlled way.

## Before / After Pattern

Before:

```easycrypt
op allperms_r (n : unit list) (s : 'a list) : 'a list list =
with n = [] => [[]]
with n = x::n => flatten (
    map (fun x => map ((::) x) (allperms_r n (rem x s))) (undup s)).
```

After:

```easycrypt
op [smt_opaque] allperms_r (n : unit list) (s : 'a list) : 'a list list =
  with n = [] =>
    [[]]
  with n = _ :: n =>
    flatten (map (fun x => map ((::) x) (allperms_r n (rem x s))) (undup s)).
```

## Additional Repair

```easycrypt
lemma allperms_r0 (s : 'a list) :
  allperms_r [] s = [[]]
by done.

lemma allperms_rS (x : unit) (n : unit list) (s : 'a list) :
  allperms_r (x :: n) s = flatten (
    map (fun x => map ((::) x) (allperms_r n (rem x s))) (undup s))
by done.
```

## Analysis

This repair demonstrates a realistic SMT-related maintenance case. Instead of changing a local proof script, the repair changes how a recursive operator is exposed to SMT. Marking the operator as opaque prevents problematic or expensive automatic unfolding, while explicit lemmas preserve controlled access to the operator's defining equations.

## Relation to Error Catalog

- R003: SMT Abstraction / Opaque Operator

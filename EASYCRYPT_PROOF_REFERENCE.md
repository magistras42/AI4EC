# EasyCrypt Proof Reference for Agents

A comprehensive working reference for AI agents writing EasyCrypt proofs. It complements
[`CLAUDE.md`](CLAUDE.md) (the short LLM guide) with detailed catalogs of **tactics**, the
**standard library** (proven lemmas and theories), **worked examples and reusable proof
patterns**, and the **EasyCrypt MCP server**.

All paths are relative to the EasyCrypt repository root
`/Users/dstebila/Dev/ProofFrog/easycrypt` unless noted. Reading agents are assumed to have
access to the same paths.

> **How to read this document.** Sections 1–2 are orientation (workflow + language). Section 3
> is the tactic catalog. Sections 4–5 are the library (math/data + crypto/distributions).
> Section 6 is worked examples and copy-able patterns. Section 7 is the MCP server. Section 8
> is a pitfalls/cheatsheet. Skim the cheatsheet (§8) first if you just need idioms.

---

## 1. Workflow

EasyCrypt is a proof assistant for the security of cryptographic constructions, with support for
probabilistic computation and four program logics (HL, pHL, pRHL, eHL — see §2).

### The `llm` command (batch, agent-friendly)

```
easycrypt llm [OPTIONS] FILE.ec
```

- `-lastgoals` — on failure, print the goal state (just before the failing command) to
  **stdout**, the error to **stderr**, exit code **1**. On success, exit **0**.
- `-upto LINE` or `-upto LINE:COL` — compile up to (not including) that location, print the
  current goal state to stdout, exit **0**. Use for incremental inspection.
- Standard loader/prover options also work: `-I DIR`, `-timeout N`, `-p PROVER`, etc.
- Output conventions: **goals → stdout**, **errors → stderr**. "No active proof." means no open
  goal at the requested point.

The actual binary used in this environment is the fork wrapper
`/Users/dstebila/Dev/ProofFrog/ProofFrog/scripts/easycrypt-fork.sh` (it supports `llm` and `cli`).

### Recommended loop

1. Write a pen-and-paper proof first; identify the game hops / lemma structure.
2. Write the `.ec` file. For large proofs, lay down a **skeleton with `admit`** on each subgoal,
   then fill them in one at a time.
3. Run `easycrypt llm -lastgoals FILE.ec`. Exit 0 ⇒ done; exit 1 ⇒ read stderr (error) + stdout
   (goal state).
4. Use `-upto LINE` to inspect intermediate states without running the rest of the file.
5. Iterate. The final proof must contain **no `admit`/`admitted`**.

### Library guidelines (from `CLAUDE.md`)

- Use SMT (`smt()` / `/#`) only in *direct* mode on **simple** goals (arithmetic, pure logic).
- **Avoid unfolding operator definitions** in high-level proofs. If you need a property of an
  operator, state and prove a dedicated lemma about it rather than unfolding inline.

---

## 2. Language and proof model

### File structure

```
require import AllCore List Distr.        (* load + open theories *)
require (*--*) PKE_CPA.                    (* load a generic theory to be cloned *)

type key.                                  (* abstract type *)
op   n : int.                              (* abstract operator *)
axiom gt0_n : 0 < n.                       (* assumption *)

lemma foo : 0 < n + 1.
proof. smt(gt0_n). qed.
```

`require` loads; `import` opens names into scope; `require import` does both;
`require export` re-exports. `require (*--*) T.` loads `T` only so it can be `clone`d.

### The four program logics

A program judgment is about EasyCrypt **modules** (stateful procedures). The four flavors:

| Logic | Judgment | Meaning |
|---|---|---|
| **HL** (Hoare) | `hoare[ M.f : pre ==> post ]` | possibilistic: every terminating run from `pre` ends in `post` |
| **pHL** (probabilistic Hoare) | `phoare[ M.f : pre ==> post ] (=｜<=｜>=) bound` | `Pr` of `post` is = / ≤ / ≥ `bound` |
| **pRHL** (relational) | `equiv[ M.f ~ M.g : pre ==> post ]` | couples two programs; memories `{1}`/`{2}` = left/right |
| **eHL** (expectation Hoare) | `ehoare[ M.f : pre ==> post ]` | pre/post are `xreal` expectations |

`Pr[ M.f(args) @ &m : event ]` is the ambient-logic probability that running `M.f` from memory
`&m` satisfies `event` (where `res` is the return value). `byphoare`/`byequiv`/`byehoare`
convert a `Pr[...]` goal into a program-logic judgment; `bypr` goes the other way.

### Proof skeleton

`proof. <tactics> qed.` Tactics transform the goal; `admit` closes (unsoundly) a placeholder.

---

## 3. Tactic catalog

Two families: **ambient/logic** tactics (ordinary first-order goals) and **program-logic**
tactics (HL/pHL/pRHL/eHL). Many program tactics work only when the relevant statement is at the
**start** (`if`, `proc`) or **end** (`wp`, `rnd`) of the program — reposition with
`seq`/`sp`/`wp`/`swap` first. In relational goals most code-transforming tactics take an optional
`{side}` (`{1}` left, `{2}` right). Code positions are integers or symbolic (`^while`, `^if{2}`,
`^if{-2}`).

Only 12 tactics have dedicated reference pages: `doc/tactics/{async-while, cfold, clear,
hoare-split, if, proc, procstar, rnd, simplify-if, skip, splitwhile, swap}.rst`. The full keyword
set lives in `src/ecLexer.mll` / `src/ecParser.mly`; implementations in `src/ecHiGoal.ml`,
`src/ecLowGoal.ml`, `src/phl/ecPhl*.ml`.

### 3.1 Ambient / logic tactics

**Introduction, application, movement**

- `move` — stack management. `move=> pat` *introduces* (using intro patterns, below);
  `move: h` *reverts* (generalizes `h` into the goal); `move: x => H` reverts `x` then
  reintroduces as `H`; `move=> /view` applies a view.
- `apply e` — backward reasoning: match goal against conclusion of lemma/hyp `e`, leaving its
  premises as subgoals. `apply: e args`, `apply /e1 /e2`, `apply e in x` (forward chaining into
  hypothesis `x`).
- `exact e` / `exact: e` / `exact /e1 /e2` — like `apply` but must fully close the goal.
- `assumption` — close from a matching hypothesis; `reflexivity` — close `x = x`.

**Intro patterns** (after `=>`, in `move=>`, `case=>`, `have`, `elim`):

- `x` name it; `_` introduce-and-clear; `?` fresh name; `*` introduce all remaining.
- `->` / `<-` introduce an equation and rewrite L→R / R→L immediately.
- `//` discharge trivial goals; `/=` simplify; `//=` both; `/#` close via SMT/auto.
- `/lemma` apply a **view** (transform the top assumption via `lemma`).
- `[a b]` destructure a conjunction/pair; `[a | b]` case-split a disjunction into two goals.
- `{x y}` clear `x`, `y`; `@/foo` delta-unfold `foo` in the goal.

**Rewriting / simplification**

- `rewrite a1 a2 ...` — rewrite goal (or `... in x`) with a sequence of args. Each arg: a
  lemma/hyp (L→R); `-L` (R→L); `{i j}` occurrence selector, `{-i}` exclusive; repeat `!` (all) /
  `?` (maybe) / `n!` / `n?`; `/foo` delta-unfold (`-/foo` fold); `//` discharge trivial; `/=`
  simplify; `/#` SMT; `pr[...]` rewrite a `Pr[...]` expression.
- `simplify` (optionally `simplify beta iota zeta eta logic delta`) — reduce the goal;
  `delta l` unfolds named ops. `cbv` is the call-by-value variant. `change f` replaces by a
  convertible term.
- `subst` / `subst x` — eliminate variables defined by equations. `congr` — reduce
  `f a.. = f b..` to argument equalities. `pose x := e` — local abbreviation.
- `clear`, `clear x y`, `clear -x y` (keep only listed). (`doc/tactics/clear.rst`)

**Goal structuring**

- `have ip : f` (optionally `by tac`) — forward: assert `f`, introduce per `ip`. `have ip := pt`
  introduces the result of a proof term.
- `suff ip : f` — backward dual of `have`: reduce goal to `f`.
- `wlog : ids / f` / `wlog suff : ...` — "without loss of generality".
- `case`, `case: x`, `case=> ip`, `case /view` — case analysis / splitting on a sum/bool/disjunction.
- `elim: e`, `elim /p : e` — elimination / induction (optionally with principle `p`).
- `split` (`split n`, `split*`) — split a conjunction; `left` / `right` — choose a disjunct;
  `exists a1, a2` — provide existential witnesses.

**Algebra / decision procedures**

- `ring` / `ringeq` — commutative-ring equalities (takes optional hypothesis equations).
- `field` / `fieldeq` — field equalities (side goals for nonzero denominators).
- `algebra` — algebraic normalization.

**Automation / closing**

- `smt`, `smt()`, `smt(lemmas...)` — call SMT solvers, seeded with the listed lemmas.
- `trivial` — cheap fixed strategy (no SMT). `done` / `by []` — close by trivial reasoning.
- `by tac` — run `tac` and require the goal fully closed (a *terminator*).
- `solve` — close via hint databases. `progress` — repeatedly intro/split. `admit` — close
  unsoundly. `idtac` — no-op.

**Inspection (commands)**: `print x`, `search t1 t2 ...`, `locate name`.

### 3.2 Program-logic tactics

**Entering a procedure judgment**

- `proc` — first tactic for a procedure judgment. **Concrete:** inline the body into the
  judgment. **Abstract** (`proc I`, non-relational): reason about an abstract procedure via
  invariant `I` (obligations: `I` initially, `I ⇒ post`, each oracle preserves `I`); pHL variant
  needs bound = 1 + losslessness. **Abstract relational** `proc {bad} {inv} {inv2}`: both
  procedures must be identical; `{bad}` optional right-side bad event. (`doc/tactics/proc.rst`)
- `proc*` — replace the procedure with a *call statement* instead of inlining; use when one side
  is abstract and the other concrete. Then `inline` the concrete side. (`doc/tactics/procstar.rst`)

**Basic structural / weakest-precondition**

- `skip` — applies to **empty** program(s); reduces to a logical goal `pre ⇒ post` (pHL also
  checks weight, eHL checks post-exp ≤ pre-exp). Does not close it. (`doc/tactics/skip.rst`)
- `wp` / `wp n` / `wp n1 n2` — weakest precondition over **trailing** deterministic statements
  (assignments, conditionals), folding them into the post.
- `sp` / `sp n` / `sp n1 n2` — strongest-postcondition dual over **leading** statements.
- `seq n : (P)` (pRHL: `seq n1 n2 : (P)`) — split the program after a prefix, with `P` the
  intermediate assertion → two judgments. (`doc/tactics/hoare-split.rst` covers the related
  `hoare split`.)
- `rnd` — consume a **trailing** random sampling `x <$ d`.
  - HL: quantify post over sampled values.
  - pHL: `rnd (fun y => E)` — WP w.r.t. event `E`.
  - pRHL: `rnd f g` — couple the two samplings via bijection `f` (inverse `g`); obligations that
    `f`/`g` form a distribution isomorphism + post under the coupling. `rnd{side}` is one-sided
    (adds a losslessness obligation). (`doc/tactics/rnd.rst`)

**Control flow**

- `if` — applies when the program **starts with `if`**. HL/pHL: then/else subgoals. pRHL 2-sided:
  first prove the guards agree under the invariant, then then/else goals. pRHL one-sided
  `if{1}`/`if{2}`: split only that side. (`doc/tactics/if.rst`)
- `while (I)` / `while (I) (variant)` / `while (I) (variant) (k) (eps)` — loop with invariant `I`,
  optional decreasing `variant` (termination), optional `k`/`eps` (probability bound).
- `async while [f1,k1] [f2,k2] (L1) (L2) : (I)` — pRHL only; relate two **out-of-lockstep** loops.
  (`doc/tactics/async-while.rst`)
- `rcondt {side}? i` / `rcondf {side}? i` — assert the guard at position `i` is **true** / **false**,
  killing the dead branch (generates a side-goal proving the guard value).
- `match c {side}? i` / `match {side}? =` — reason about / align `match` statements.

**Calls and consequence**

- `call pt` — consume a **trailing** procedure call. `call (pre ==> post)` (spec), `call (inv)`
  (invariant), `call (bad, p)` / `call (bad, p, q)` (up-to-bad pair/triple). `call{side}` one-sided.
- `ecall (p tvi args)` — apply a named lemma `p` as the call specification.
- `conseq` — rule of consequence (weaken pre / strengthen post). Up to three `info` slots for the
  main + auxiliary HL/pHL judgments. `conseq <auto>` discharges entailments automatically. Common:
  `conseq (: _ ==> Q) (: _ ==> P)` to separate two post-obligations.
- `exfalso` — reduce a judgment to proving `pre` is false (vacuous).

**From program logic to probabilities**

- `byphoare {conseq}?` — reduce a `Pr[...]` goal to a **pHL** judgment.
- `byequiv [opt]? {conseq}?` / `byequiv ... : bad` — reduce a 2-probability comparison to a
  **pRHL** judgment (optional bad event for up-to reasoning).
- `byehoare` — reduce to **eHL**. `byupto` — up-to-bad step.
- `bypr` / `bypr f1 f2` — reduce a judgment back to an ambient `Pr[...]` goal.
- `phoare split b1 b2 b3?` — split a pHL bound across boolean events.
- `phoare equiv {side} pr po`, `hoare`, `hoare split` (HL only — split a conjunctive post into
  independent Hoare goals; `doc/tactics/hoare-split.rst`), `pr_bounded` (close `0 ≤ Pr ≤ 1`).

**Program transformations (semantics-preserving)**

- `swap {side}? c1 offset` / `swap {side}? [c1..c2] offset` — exchange independent adjacent
  commands/blocks (disjoint reads/writes); fails if they interfere. Used to align programs.
  (`doc/tactics/swap.rst`)
- `cfold {side}? {pos} {n}?` — constant/copy folding: propagate an assignment forward.
  (`doc/tactics/cfold.rst`)
- `splitwhile {side}? {pos} : (S)` — split `while (b){c}` into `while (b/\S){c}; while (b){c}`.
  (`doc/tactics/splitwhile.rst`)
- `unroll {side}? {pos}` / `unroll for ...` — unroll one (or all bounded) loop iterations.
- `fission` / `fusion` — split / merge loops. `alias` — introduce a fresh variable aliasing an
  expression at a position (useful before coupling samplings). `kill {side}? o ! n` — remove
  statements (obligation: dead/lossless).
- `simplify if {side}? {pos}?` — if-conversion: rewrite `if` (with assignment-only branches) into
  a conditional *expression*, avoiding WP blow-up. (`doc/tactics/simplify-if.rst`)
- `outline {side} {range} by {stmt}` / `... f` — replace a code range by an equivalent statement
  or a call to `f`, leaving an equivalence obligation.
- `rewrite equiv[ {side} cp {dir} pt ]` — rewrite a code fragment using an `equiv` lemma.

**Relational alignment / heavy machinery**

- `sim` — pRHL: automatic simulation; match two structurally-similar programs under an equality
  invariant. `do !sim` repeats. `by sim` closes.
- `transitivity {kind} h1 h2` / `transitivity{side} {code} ...` — insert an intermediate program
  (transitivity of `equiv`). `symmetry` — swap the two sides.
- `fel n cntr delta q event [oracle-specs] (inv)?` — **Failure Event Lemma**: bound `Pr[event]`
  over a loop/oracle by `bigi predT delta 0 q`. Backed by `theories/tactics/FelTactic.ec` and
  `src/phl/ecPhlFel.ml`.
- `eager [tac]` / `eager seq` / `eager if` / `eager while I` / `eager proc` / `eager call info` —
  eager/lazy sampling reasoning: move random samplings across program boundaries.
- pHL existentials: `elim*` (eliminate a leading existential in the pre), `exists* e1, e2`
  (lift existential witnesses; pair with `elim* => x`).

### 3.3 Combinators (tacticals)

- `t1; t2` — run `t2` on every subgoal produced by `t1`. `t1; [t2 | t3 | ...]` — apply per-subgoal.
- `[ t1 | t2 | ... ]` — distinct tactic per subgoal; with selectors `[1: t1 | 2..3: t2 | else t]`.
- `t1 || t2` — try `t1`, else `t2`. `try t` — run, ignore failure. `try! t` — strict variant.
- `do t` / `do! t` (≥1) / `do? t` (≥0) / `do n! t` / `do n? t` — repetition.
- `first t` / `first n t`, `last t` / `last n t`, `first last` / `last first` — rotate subgoals.
- `i: t`, `i..j: t` — focus on subgoal(s) by index/range. Leading `+`/`*`/`-` are structural bullets.
- `by t` — terminator (run + require closure). `expect n t` — assert exactly `n` subgoals remain.
- `time t` — report timing. `fail t` — expect `t` to fail. `(t1; t2)` — group.
- Post-tactic `=> ip` pipes results into intro patterns; `: gen` reverts.

`//`, `/=`, `//=`, `/#` are used pervasively after tactics: discharge-trivial / simplify / both /
SMT-or-auto, respectively. `~` is the relational separator (`equiv[M.f ~ M.g : ...]`).

---

## 4. Standard library — math and data structures

Conventions (apply throughout): suffix `_r` = raw/unbundled variant (un-suffixed is the convenient
form); `ge0_`/`gt0_`/`le0_`/`lt0_` = sign facts; `ler_`/`ltr_` = real/ring `<=`/`<`, `lez_`/`ltz_`
= int; `p`/`w` infixes = "positive"/"weak"; algebraic suffixes `A` assoc, `C` comm, `N` neg, `D`
distrib/"+", `K` cancel, `I` injective, `V`/`inv` inverse, `0`/`1` units; `E` suffix = unfolding
equation (rewrite with it); `P` suffix = reflection/characterization lemma; `[opaque]` ops need
their `fE` lemma to unfold; `hint simplify` lemmas fire under `/=`. Operators are clone-instantiated,
so `addrA`/`mulrC`/`exprS` etc. apply uniformly to `int` (`IntID`), `real` (`RField`), and any ring.

### "What do I import" cheatsheet

- General math (ints + reals + int ring API): `require import AllCore`.
- Lists: `List`. Finite sets: `FSet`. Finite maps: `FMap`. Total (SMT) maps: `SmtMap`.
- Inequalities on int/real: `require import StdOrder` then `import IntOrder` / `import RealOrder`.
- Sums/products: `require import StdBigop` then `import Bigint.BIA` / `Bigreal.BRA` (sums), `BIM`/`BRM`
  (products); `bigi P F i j` over ranges.
- Euclidean division / gcd / mod: `IntDiv`. Finiteness/enumeration: `Finite`, `FinType`.
- Probability distributions: `Distr` (+ `DBool`, `DInterval`, `DList`, `DMap`, `DProd`, ...).

### core/ and prelude/

- `AllCore.ec` = `require export Core Int Real Xint` + `export Ring.IntID`. **Importing AllCore
  gives the full int ring API** on `int`.
- `Core.ec` — options (`oget`, `omap`, `odflt`, `obind`, `oget_some`, `someI`), pairs (`pairS`,
  `fst_pair`), function/predicate extensionality (`fun_ext`, `pred_ext`), the
  injectivity/cancellation toolkit (`can_inj`, `pcan_inj`, `inj_eq`, `canLR`, `canRL`, `bij_inj`),
  predicate algebra (`predI`/`predU`/`predC`, `predIC`, `pred1E`).
- `Bool.ec` — XOR `(^^)` with `xorA`, `xorC`, `xorK : b^^b=false`.
- `prelude/Logic.ec` — the big propositional/option/combinator library: property predicates used
  as hypotheses (`injective`, `surjective`, `cancel`, `bijective`, `involutive`, `commutative`,
  `associative`, `left_id`, `left_inverse`, `morphism_2`, ...); boolean lemmas (`negP`, `negbK`,
  `negb_and`, `negb_or`, `andbA`, `orbC`, `implybE`, `if_same`, `ifT`/`ifF`, `fun_if`, contradiction
  family `contra`/`contraLR`/`absurd`); equality (`eq_sym`, `eq_trans`, `congr1`); choice
  (`choiceb`, `choicebP`); function update `f.[x<-y]` (`fupdate_eq`/`fupdate_neq`).
- `prelude/Pervasive.ec` — primitives: `bool` with `! || && => <=>`, `(=)`, `(<>)`, `int`, `real`,
  `'a distr` with `mu`, `witness : 'a` (every type inhabited).

### datatypes/Int.ec, Real.ec

- **Int**: induction `intind`/`natind`/`sintind` (strong); arithmetic `addzA/addzC/add0z/addzN`,
  `mulzA/mulzC/mulzDl`, `addzK`, `subz_add2r`; ordering `lezz`, `lez_trans`, `ltzW`,
  `lez_add2l/r`, `ltz_add2l/r`, `subz_ge0 : (0<=y-x)=(x<=y)`, `ltzS`, `ltzE`, `lezNgt`/`ltzNge`;
  `b2i b` with `b2i0`/`b2i1`/`b2i_ge0`/`b2i_le1`; `iter`/`iteri` (`iterS`, `iter0`); `odd`; `min`/`max`.
- **Real**: `(%r)` = int→real with the homomorphism lemmas `fromint0`/`fromint1`/`fromintD`/`fromintB`/
  `fromintM`/`eq_fromint`/`le_fromint`/`lt_fromint`; `RField` full field API (`mulVr`, `divff`,
  `double_half : x/2%r + x/2%r = x`); `b2r`; `floor`/`ceil` (`floor_bound`, `from_int_floor`, `isint`).

### datatypes/List.ec (reach for this constantly)

`'a list = [] | (::)`. Staple operators and their key lemmas:

- **size**: `size_ge0`, `size_eq0`, `size_cat`, `size_map`, `size_filter`, `size_range`, `size_iota`.
- **cat/cons**: `cat0s`, `cats0`, `cats1 : s++[z]=rcons s z`, `catA`, `cat_take_drop`, `eqseq_cat`.
- **nth/onth**: `nth z xs n` (default on OOB), `nth_cat`, `nth0`, `eq_from_nth` (extensionality),
  `nthP`, `mem_nth`, `nth_map`.
- **mem / `\in`** (`x \in s` = `mem s x`): `in_nil`, `in_cons`, `mem_cat`, `mem_filter`, `mem_map`,
  `mapP`, `mem_rev`, `mem_nth`.
- **count/has/all/filter**: `count_ge0`, `count_predT : count predT s = size s`, `hasP : has p s
  <=> exists x, x\in s /\ p x`, `allP : all p s <=> forall x, x\in s => p x`, `has_count`,
  `all_count`, `filter_cat`, `filter_predT`, `eq_count`/`eq_filter`/`eq_all` (+ `eq_in_*`).
- **map/foldr/foldl**: `map_cat`, `map_comp`, `eq_map`, `eq_in_map`, `map_f`, `nth_map`, `foldr_cat`,
  `foldr_map`, `flatten` (`flattenP`, `size_flatten`).
- **rev/uniq/undup/perm_eq/rem**: `rev_cons`, `revK`, `cat_uniq`, `undup_uniq`, `mem_undup`,
  `perm_eqP`, `perm_eq_refl/sym/trans`, `perm_catC`, `perm_eq_mem`, `perm_to_rem`, `size_rem`.
- **iota / range / mkseq**: `iota_ i n`, `mem_iota`; `range m n = iota_ m (n-m)` with
  `range_ltn`, `mem_range : i \in range m n <=> m<=i<n`, `size_range : max 0 (n-m)`, `nth_range`;
  `mkseq f n = map f (iota_ 0 n)` with `nth_mkseq`, `size_mkseq`, `mkseqS`.
- **assoc / zip / allpairs / subseq / sort**: `assoc`, `assocP`, `mem_assoc_uniq`; `zip`,
  `unzip1`/`unzip2`, `nth_zip`; `allpairs`, `allpairsP`; `subseqP`; `sort` (`perm_sort`,
  `sort_sorted`, `mem_sort`, `sortK`, `sorted`, `path`).

### datatypes/FSet.ec, FMap.ec, SmtMap.ec, Array.ec

- **FSet** (`'a fset`): `mem`/`(\in)`/`(\notin)`, `card`, `fsetP` (extensionality), `fset0`,
  `fset1`, `(`|`)` union, `(`&`)` intersection, `(`\`)` difference, `oflist`, `image`, `filter`,
  `rangeset`. Membership rewrites in `hint rewrite inE`: `in_fset0`, `in_fset1`, `in_fsetU`,
  `in_fsetI`, `in_fsetD`, `in_fsetU1`. Set algebra `fsetUC/fsetUA/fsetIC/fsetUIr` (distrib),
  `fsetDv : A `\` A = fset0`. Subset `(\subset)`, `subsetP`, `sub1set`. Cardinality `fcardU`,
  `fcardUI`, `fcard1`, `fcard_ge0`, `fcard_eq0`. Induction `fset_ind`. `disjoint`/`disjointP`.
- **FMap** (`('a,'b) fmap`, `m.[x]: 'b option`, `m.[x<-v]`): `dom`, `(\in)` = dom, `rng`,
  `fmap_eqP` (extensionality), `empty`/`emptyE`, `get_setE : m.[x<-b].[y] = if y=x then Some b else
  m.[y]`, `get_set_sameE`, `mem_set`, `set_setE`, `rem`/`remE`/`rem_set`. `fdom`/`mem_fdom`/
  `fdom_set`/`fdom_rem`, `frng`, `fsize`. `map`/`filter`/`(+)` join/`merge`. `eq_except X m1 m2`
  with `eq_except_setl/setr`, `eq_exceptP` (central to up-to-bad RO arguments).
- **SmtMap** (`('a,'b) map`, **total**, `m.[x]` total, `cst b` constant): `offun`/`tofun`,
  `map_eqP`, `get_setE`, `cstE`, `eq_except`, `merge`.
- **Array** (`'a array`): `mkarray`/`ofarray`, `arr.[i]` (OOB→witness), `arr.[i<-x]`, `arrayP`
  (extensionality), `get_set_if`, `set_set_eq`, `offun`, `map`.
- **IntMin.ec**: `argmin`/`argmax` (`argminP`, `argmin_min`), `pmin` (least nat in a predicate;
  `pmin_mem`, `pmin_min`).
- **Xint.ec / Xreal.ec**: extended ints (`N of int | Inf`) and nonnegative/extended reals (`realp`,
  `xreal`) used for cost/probability/expectation reasoning.

### algebra/

- **Ring.ec** — hierarchy `ZModule ⊂ ComRing ⊂ IDomain ⊂ Field`, instantiated as `IntID` (int),
  `RField` (real). ZModule: `addr0`, `addrA/addrC/addrCA`, `addrN`, `subrr : x-x=0`, `opprK`,
  `opprD`, `subrK`, `addrK`, `subr_eq0 : x-y=0 <=> x=y`. ComRing: `mulrA/mulrC/mul1r/mulrCA`,
  `mulrDl/mulrDr`, `mul0r`, `mulrN/mulNr`, `mulrBl/mulrBr`, `mulrV`/`mulVr`, `invrK`. Powers `exp`:
  `expr0`, `expr1`, `exprS : 0<=i => x^(i+1)=x*x^i`, `exprD`, `exprM`, `exprMn`. `ofint`.
- **Number.ec** (instantiated `IntOrder`, `RealOrder`) — the big ordering library: `lerr`, `ltrW`,
  `ler_trans`, `ltr_trans`, `ler_lt_trans`, `ler_anti`, `ler_eqVlt`, `ltr_neqAle`; add/sub
  `ler_add2l/r`, `ltr_add2l/r`, `subr_ge0 : 0<=x-y <=> y<=x`, `addr_ge0`, `ler_opp2`,
  `ler_subl_addr`/`ler_subr_addr` (move terms across); mul `mulr_ge0`, `mulr_gt0`, `ler_pmul2l/r`,
  `ler_wpmul2l/r` (weak), `ler_pmul`; division (field) `ler_pdivl_mulr`/`ler_pdivr_mulr` (divide both
  sides, positive divisor); norm `normr_ge0`, `normrM`, `ger0_norm`, `ler_norm_add` (triangle),
  `ler_norml : `|x|<=y <=> -y<=x<=y`; min/max `ler_maxr`, `ler_minr`. `IntOrder.signz`.
- **IntDiv.ec** — `(%/)` quotient, `(%%)` remainder, `(%|)` divides. `divz_eq : m =
  (m%/d)*d + m%%d`, `modzE`, `modz_ge0` (d≠0), `ltz_pmod`, `modz_small`, `modz1`/`divz1`, `modzz`,
  `mulzK`, `divzMDl`, `modzMDl`, `modz_mod`, `modzDml`/`modzMml` (push mod through +/*); `dvdzE`,
  `dvdzP : d%|m <=> exists q, m=q*d`, `dvdz_trans`, `eqz_mod_dvd`; `gcd` (`dvdz_gcdl/r`,
  `Bachet_Bezout`); congruence `eqm`; div-inequality `ltz_divLR`/`lez_divRL`.
- **Bigop.eca / Bigalg.ec / StdBigop.ec** — big operators. `big P F r`, `bigi P F i j = big P F
  (range i j)`. Structure: `big_nil`, `big_cons`, `big_cat`, `big_map`, `big_filter`. Congruence
  `eq_bigr`/`eq_bigl`/`eq_big`. Manipulation `big_split`, `bigID`, `bigD1` (pull out one element),
  `big_mkcond`, `exchange_big` (swap nested sums), `big_const`. Trivial `big1`, `big_pred0`. Integer
  ranges `big_ltn`/`big_geq`/`big_int_recl`/`big_int_recr` (peel from either end), `big_cat_int`.
  **Concrete instances (import these):** `Bigint.BIA` (int sum), `Bigint.BIM` (int product),
  `Bigreal.BRA` (real sum), `Bigreal.BRM` (real product); `sumr_const`, `sumr1 : BRA.big predT (fun
  _=>1%r) s = (size s)%r`, `sumidE`, `sum_pow`/`sum_pow_le` (geometric sums). Ordered: `ler_sum`
  (termwise), `sumr_ge0`, `prodr_ge0`, `b2r_big` (bound a boolean big by a real sum).
- **Group.ec** (abstract groups), **ZModP.ec** (integers mod p: `inzmod`/`asint`, `inzmodK`,
  `eq_inzmod`, full `ZModpField` clone when p prime), **Poly.ec**, **Matrix.eca**, **Binomial.ec**.

### structure/

- **Finite.ec**: `is_finite p`, `to_seq p` (canonical enumeration), `mem_to_seq`, closure
  (`finiteU`, `finiteIl`, `finiteD`), `finite_type`.
- **FinType.ec**: `abstract theory FinType` with `enum`, `card`, axiom `enum_spec`; derived
  `enumP`, `enum_uniq`, `card_gt0`. `FinProdType` for product types.
- **Subtype.eca**: carve `sT` from `T` by predicate `P`: `insub`, `val`, `insubd`, `valP : P (val
  x)`, `valK`, `val_inj`, `insubdK`. Used by `ZModP`, `BitWord`, `Xreal`.
- **WF.ec**: well-founded recursion/induction — `wf`, `wf_ind`, `lt_nat`, `wf_lex`, `wf_pre`.

---

## 5. Standard library — distributions and cryptography

For distributions: `mu1 d x = mu d (pred1 x)`, `weight d = mu d predT`, `support d x ⇔ 0%r < mu1 d
x`, `x \in d ≡ support d x`. All of `Distr` is global (just `require import Distr`).

### distributions/Distr.ec (heavily used)

- **Predicates**: `is_lossless d = weight d = 1%r`, `is_full d = forall x, x \in d`, `is_uniform`,
  `is_funiform` (uniform on all points).
- **Basic lemmas**: `ge0_mu`, `le1_mu`, `mu_bounded`, `mu0 : mu d pred0 = 0`, `mu_not`,
  `mu_or : mu d (p\/q) = mu d p + mu d q - mu d (p/\q)`, `mu_split`, `mu_disjoint`, `mu_sub`
  (monotone), `mu_le`, `mu_eq`, `eq_distr : d1=d2 <=> forall x, mu1 d1 x = mu1 d2 x` (equality
  entry point), `supportP`, `supportPn`, `weightE : weight d = sum (mu1 d)`.
- **Uniformity**: `mu1_uni`, `mu1_uni_ll`, `eq_funi`, `eq_funi_ll`, `funi_ll_full`.
- **Concrete distributions**: `dnull` (`dnull1E`, `weight_dnull=0`); `dunit x` point mass
  (`dunit1E : mu1 (dunit x) y = b2r (x=y)`, `dunit_ll`); `drat`; `duniform s` (`duniform1E_uniq`,
  `duniform_ll (s<>[])`, `supp_duniform`); `drange m n` (`drange1E`, `supp_drange`, `drange_ll
  (m<n)`); `dunifin` (uniform on a finite type — what `DBool` clones).
- **dmap** (pushforward, very common): `dmap d f`; `dmap1E d f b : mu1 (dmap d f) b = mu d (fun x =>
  f x = b)`; `dmap1E_can` (when `f` invertible); `supp_dmap`; `weight_dmap = weight d`; `dmap_id`,
  `dmap_comp`, `dmap_dunit`; `dmap_bij` (reindexing). `dfst`/`dsnd` = product marginals.
- **dlet** (monadic bind): `dlet d f`; `dlet1E`, `dletE`, `supp_dlet`, `dlet_unit`, `dlet_dlet`
  (assoc), `dlet_dunit : dlet d (dunit∘f) = dmap d f`, `dlet_swap`.
- **dprod** (`(`*`)`, independent product): `dprod1E (a,b) = mu1 da a * mu1 db b`, `dprodE`,
  `supp_dprod`, `weight_dprod`, `dprod_ll`/`dprod_uni`/`dprod_funi`, `dprodC`.
- **Other**: `djoin`/`djoinmap` (list of dists); `dscale d` (normalize); `dcond d p` (condition);
  `drestrict`; `dfun` (function-valued over a finite type — underpins `FunRO`); `dbin` (binomial);
  `p_max d`, `mode d` (`mode_ge`); couplings (`iscoupling`).
- **DBool.ec**: `dbool` = fair coin; `dbool1E = 1/2`; `dboolE`; `Biased.dbiased p` (`dbiased1E :
  mu1 (dbiased p) true = clamp p`, `dbiased_ll`).
- **DInterval.ec**: `dinter i j` = uniform on `[i..j]` closed; `dinter1E`, `supp_dinter : x \in
  dinter i j <=> i<=x<=j`, `dinter_ll (i<=j)`.
- **DList.ec**: `dlist d n` = n iid samples; `dlist0`, `dlistS`, `dlist1E`, `supp_dlist : xs \in
  dlist d n <=> size xs = n /\ all (mem d) xs`, `dlist_ll (is_lossless d, 0<=n)`. `Program` sub-theory:
  equivs between monolithic `dlist` sampling and a sampling loop (`Sample_Loop_eq`).
- **DMap.ec / DProd.ec**: `DMapSampling` (`sample` vs `map`), `ProdSampling` (sample `d1`*`d2` vs
  two samples), `DLetSampling` (sequential vs `dlet`) — standard tactics to split/merge joint
  samplings in pRHL (`SampleDLet`, `SampleDepDLet`).
- **SDist.ec** (statistical distance): `sdist d1 d2 = flub (fun E => `|mu d1 E - mu d2 E|)`;
  metric lemmas `sdist_ge0`, `sdistC`, `sdist_triangle`, `sdist_upper_bound`, `sdist0_eq`;
  `sdist_tvd` (TVD form); compatibility `sdist_dmap`, `sdist_dlist : <= n%r * sdist d1 d2`;
  `GenDist.adv_sdist`, `N1.sdist_oracleN` (N-query bound), `ROM.sdist_ROM`.
- **Mu_mem.ec**: union/membership bounds `mu_mem_le (s:'a fset) d bd : (forall x, mu1 d x <= bd)
  => mu d (mem s) <= (card s)%r * bd` (powers birthday/collision bounds).

### crypto/PROM.ec (the central programmable-RO library)

Typical clone:
```
clone import PROM.FullRO as RO with
  type in_t <- ..., type out_t <- ..., type d_in_t <- ..., type d_out_t <- ...,
  op dout <- (fun _ => duniform/dunifin/...) proof*.
```
- Module types: `RO = {init; get(x); set(x,y); rem(x); sample(x)}`, `ROmap` (+`restrK`), `FRO`
  (+`queried`,`allKnown`), `RO_Distinguisher(G:RO) = {distinguish(_)}`, `MainD(D,RO)` experiment.
- Modules: `RO` (lazy), **`LRO`** (lazy, `sample` is a no-op — the RO most proofs use), `FRO`
  (flagged, for eager arguments).
- **Headline result** (`FullEager`): `RO_LRO : MainD(D,RO).distinguish ~ MainD(D,LRO).distinguish :
  ={glob D,arg} ==> ={res,glob D}` — replace eager `RO` by lazy `LRO`.
- Collision bound `fcoll_bound` (via `fel`); `FinEager.FinRO` and `FunRO` with `pr_RO_FinRO_D` /
  `pr_FinRO_FunRO_D` to replace a lazy RO with one up-front random-function sample.

### Other crypto theories (clone-and-instantiate)

- **ROM.eca** — simpler (non-programmable) RO: `Lazy.LRO`, `FiniteEager.ERO`, `LazyEager`
  (`eq_eager_sampling`), bounded/logging wrappers (`SetLog.Log`), and **`ROM_BadCall`** (the
  standard "bad event on a single RO point" lemma: `ROM_BadCall_tight : |Pr[G0]-Pr[G1]| <=
  Pr[G_bad]`).
- **PRF.eca / PRP.eca** — `IND` games; `RF` (random function), `PseudoRF`; `RP` (random
  permutation); **`RP_RF.Conclusion`** (PRP/PRF switching: `|Pr[IND(RP,D)] - Pr[IND(PRFi,D)]| <=
  (q*(q-1))%r/2%r * mu1 dD witness`).
- **PKE.ec / PublicKeyEncryption.eca** — PKE schemes and the full game menu (`CPA`/`CPA_L`/`CPA_R`,
  `CCA`, `Correctness`; `pr_CPA_LR : |Pr[CPA_L]-Pr[CPA_R]| = 2%r*|Pr[CPA]-1/2|`; OW/IND/NM/ANO
  variants, `DeltaCorrect`). `PKE_ROM` lifts over a `ROM` clone.
- **DiffieHellman.ec** — clones a `CyclicGroup`; `DDH` (games `DDH0`/`DDH1`), `CDH`,
  `List_CDH`/`Set_CDH` with reductions. **DLog.ec** — discrete log experiments + standard reduction.
  **OW.ec** — one-way trapdoor permutations.
- **MAC.ec** (EUF-CMA), **Commitment.ec** (hiding/binding), **SigmaProtocol.ec** (completeness,
  special soundness, SHVZK), **SplitRO.ec** (split an RO by domain `SplitDom` or codomain
  `SplitCodom`), **AdvAbsVal.ec** (`abs_val` — lift a `Pr[A]-1/2` bound to `|Pr[A]-1/2|`).

### encryption/ and modules/ (proof techniques)

- **Hybrid.ec** — the general left/right hybrid argument. Main lemma `Hybrid &m p` relates the
  full `Ln`/`Rn` game advantage to `q%r ×` a single-hybrid-step advantage (`HybGame` samples the
  hybrid index `l0 <$ [0..q-1]`); division form `Hybrid_div`; restricted-adversary `Hybrid_restr`.
  Built on **Means.ec** (`Mean`/`Mean_uni`: average over a sampled parameter).
- **Indist.ec** — `IND1_INDn`, the indistinguishability variant of the hybrid argument.
- **GlobalHybrid.ec** — hybrids over an index: `hybrid_gen` (telescoping triangle inequality
  `<= bigi predT p 1 n`), `hybrid_simp` (constant step `<= (n-1)%r * p`).
- **modules/PlugAndPray.eca** — `PBound` / `PBound_mult`: guessing an index up front costs `1/card`.
- **modules/Pr_half.eca** — `equiv_not_pr_half`: symmetry forces probability 1/2.
- **modules/TotalProb.ec** — law of total probability over a sampled parameter (`total_prob`).
- **query_counting/Counter.eca, OracleBounds.ec** — call counters (`Counter`, `Count(O)`), bound
  enforcement (`Enforce`/`EnforcedAdv` clamp an adversary to ≤q oracle calls).
- **tactics/FelTactic.ec** — imports backing the native `fel` tactic (Failure Event Lemma).

### Common cloning idioms

- RO: `clone import PROM.FullRO as H with type in_t<-..., type out_t<-..., op dout<-... proof*.`
- Finite uniform type distribution: `clone import Distr.MFinite as M with type t<-T` → `M.dunifin`.
- PRP switching: `RP_RF` clones `PRF as PRFt` and `RF as PRFi`.
- Statistical-distance oracle bound: clone `SDist.N1` / `SDist.ROM` with `op d1, d2, N`.

---

## 6. Worked examples and reusable proof patterns

Files in `examples/`. Internalize the cross-cutting skeleton before the individual examples.

### Cross-cutting game-proof skeleton

1. `require import` core theories; `require (*--*) GenericTheory` for the theory to clone; often
   `pragma +implicits`.
2. **Clone-and-instantiate** the definitional theory, mapping abstract types/ops to your construction:
   ```
   clone import PKE_CPA as PKE with
     type pkey <- pkey, type skey <- skey, type ptxt <- ptxt, type ctxt <- ctxt.
   ```
3. Define the **scheme** as a module implementing the theory's module type (`Scheme`,
   `SigmaScheme`, `CommitmentScheme`, ...).
4. Define the **reduction adversary as a functor** taking the scheme adversary as a parameter
   (this is the heart of every reduction).
5. Open a `section`: `declare module A <: Adversary { -GlobalsItMustNotTouch }`; `declare axiom`
   losslessness facts (`A_ll`); use `local lemma`/`local module` per game hop; one public top-level
   `lemma` chaining the hops.
6. Close each probability claim with `byequiv` (a 2-game pRHL equivalence), `byphoare` (`Pr[G]=c`
   via single-game pHL), or `byupto`/`fel` (up-to-bad). Assemble the final bound by `rewrite`ing the
   hop lemmas together, often finished with `smt(...)`.

### elgamal.ec — ElGamal IND-CPA from DDH (the canonical reduction)

Proves `|Pr[CPA(ElGamal,A):res] - 1/2| = |Pr[DDH0(DDHAdv(A))] - Pr[DDH1(DDHAdv(A))]|`. Three hops:
(1) `CPA(ElGamal,A) = DDH0(DDHAdv(A))` (exact), (2) `DDH1(...) = Gb` (random challenge = encrypting a
random message), (3) `Pr[Gb:res] = 1/2`.

The reduction module wraps the PKE adversary's two procedures around the DDH triple:
```
module DDHAdv (A:Adversary) = {
  proc guess (gx, gy, gz) : bool = {
    var m0, m1, b, b';
    (m0, m1) <@ A.choose(gx);
    b        <$ {0,1};
    b'       <@ A.guess(gy, gz * (b ? m1 : m0));
    return b' = b;
  }
}.
```
Key idioms:
- `byequiv=> //; proc; inline *.` — the standard opener: enter the equivalence, descend into bodies,
  inline all calls.
- `swap{1} 7 -5.` — reorder per-side so corresponding sampling lines align across the two games.
- `auto; call (_:true).` — discharge straight-line code, then relate an adversary call assuming the
  trivial invariant (identical globals/args ⇒ identical results).
- **One-to-one sampling trick** (most reusable): show two samplings are equidistributed under a
  bijection by giving forward + inverse maps —
  `rnd (fun z, z + loge (if b then m1 else m0){2}) (fun z, z - loge (if b then m1 else m0){2}).`
  Then `auto; progress` leaves algebraic side-goals (`rewrite`/`algebra`).
- Proving a guess is 1/2: `byphoare=> //; proc. rnd (pred1 b')=> //=. conseq (: _ ==> true). ... by
  move=> /> b; rewrite dbool1E pred1E. islossless; [apply Ag_ll | apply Ac_ll].`

### SchnorrPK.ec — Schnorr sigma protocol

Direct pHL/pRHL judgments (not game hops): completeness (`= 1%r`, `byphoare` + `inline*; wp; auto`,
finish `algebra`), special soundness (`byphoare`; straight-line; extract witness `(z-z')/(e-e')`),
SHVZK (`byequiv`; randomness bijection to match simulator vs prover sampling). Patterns: `byphoare
(_: <pre> ==> _)` to thread the lemma's args into module state; `seq n m: (inv)` to split at differing
program-point counts; `rcondf{1} 1`/`rcondt{1} 1` for known-value guards.

### Pedersen.ec — commitment scheme

Perfect hiding (`Pr = 1/2`) via a `FakeCommit` game that is information-theoretically independent of
the secret bit (prove real = fake, then trivially 1/2); computational binding by a single `byequiv`
mapping two valid openings onto a DLog solution:
```
module DLogAttacker(B:Binder) : DL.DLog.Adversary = {
  proc guess (h: group) : exp option = {
    var x, c, m, m', d, d';
    (c, m, d, m', d') <@ B.bind(h);
    if ((c = g ^ d * h ^ m) /\ (c = g ^ d' * h ^ m') /\ (m <> m'))
      x <- Some((d - d') * inv (m' - m));
    else x <- None;
    return x;
  }
}.
```
Lesson (from the file's own comment): keep the two games' structure parallel — "useless" mirrored
lines make `call`/`rnd`/`wp` line up. `std_red_dl_bridge` shows the "standard reduction bridge"
pattern (`byequiv; sim` + `if{2}` case split) to state a bound against a canonical assumption.

### br93.ec — Bellare-Rogaway 93 PKE in the ROM (the best ROM example)

Proves `Pr[BR93_CPA(A):res] - 1/2 <= Pr[OW(I(A)):res]`. The scheme is a functor over the RO
(`module BR93 (H:Oracle) = {...}`; `clone import ROM as H; import H.Lazy`). Game sequence: replace
the challenge's RO call with fresh sampling (difference bounded by the **bad event** "adversary
queried the challenge randomness"), then make the ciphertext uniform via a masking bijection, then
the bad event implies the inverter wins.

**The up-to-bad call invariant** (the key ROM idiom — relate two oracles "except on the bad input"):
```
call (_: Game1.r \in Log.qs,
         eq_except (pred1 Game1.r{2}) LRO.m{1} LRO.m{2}).
+ exact/A_a2_ll.                          (* adversary lossless *)
+ proc; inline LRO.o. auto=> ...          (* oracles agree while !bad *)
+ move=> &2 _; ...                        (* RHS oracle lossless when bad *)
+ move=> _ /=; proc; inline *; conseq ... (* LHS preserves bad, lossless *)
```
The `call (_: BAD, INV)` (two-argument) and `call (_: BAD, INV, BADINV)` (three-argument) forms are
*the* template for "two systems agree until bad". Other patterns: a `Log(H)` wrapper recording every
query (`qs <- x::qs`) so the bad event is expressible; a domain-tracking invariant `forall x, x \in
Log.qs <=> x \in LRO.m`; modern game-hop-as-patch syntax `module Game2 = Game1 with { proc main [ ^
stmt ~ { newcode } ] }` keeps hops syntactically close.

### PRG.ec — pseudorandom generator (most advanced)

Combines up-to-bad, eager/lazy resampling, and a birthday-style `fel` bound. Patterns: `inductive
Bad ... = | Cycle of ... | Collision r of ...` to define a structured bad event; `phoare` bound
splitting `seq k: <P> <pUpper> <qUpper> ...` combined with `while (inv)` carrying counting facts; a
counting wrapper `C(A,F,P)` that guards each call with `if (cF < qF)` and increments counters; `eager
proc`/`eager call` to swap sampling order with adversary execution.

### FundamentalLemma.ec — the fundamental lemma of game playing

The abstract up-to-bad inequality: `Pr[G1: A∧!F] = Pr[G2: B∧!F] => |Pr[G1:A] - Pr[G2:B]| <= max
Pr[G1:F] Pr[G2:F]`. Foundational identity `Pr_split : Pr[G:A∧F] + Pr[G:A∧!F] = Pr[G:A]` (proved via
`Pr [mu_eq]` and `Pr [mu_disjoint]`) — these `Pr[...]` rewrite tactics are the primitives for manual
probability algebra.

### Upto.ec / upto_syntaxtic.ec — up-to-bad in practice

`upto_syntaxtic.ec` demonstrates `byupto`: when two modules differ only *after* setting a `bad`
flag, every `!bad`-conditioned probability identity (and the standard inequalities `<= Pr[..!bad] +
Pr[..bad]`, `<= max ...`, `<= |..bad - ..bad|`) is one line: `proof. byupto. qed.` `Upto.ec` packages
a general bounded-query up-to-bad theorem using a wrapper oracle (`if (cO < qO /\ !bad)`), the
three-part `call (: bad, INV, BADINV)` form, then the **`fel`** tactic to bound `Pr[bad]`:
```
fel 2 Experiment.WO.cO g qO (Experiment.WO.bad)
    [Experiment(O2,Adv).WO.f : (!Experiment.WO.bad /\ Experiment.WO.cO < qO)]
    (m (glob O2) = Experiment.WO.cO).
```
i.e. `fel <prefix-len> <counter> <bound-per-step g> <max-queries> <bad> [proc : trigger-cond] (inv)`
gives `Pr[bad] <= Σ g(k)`. Use `exists* var; elim* => x` to fix the counter before each per-query
`phoare` obligation.

### Other examples

- **global-hybrid/GlobalHybridExamp1.ec** — hybrid over a loop: parameterize the game by a start
  index, prove the two endpoints, prove a uniform per-step bound (itself up-to-bad), invoke
  `hybrid_simp`.
- **plug-and-pray/Plug_and_Pray_example.ec** — clone `Plug_and_Pray` with the index set, apply
  `PBound` with projection functions `phi` (event) / `psi` (value to guess), bridge to your concrete
  guessing game with `byequiv; sim`.
- **Dice4_6.ec** — distribution equivalence by bijection + rejection sampling (`rnd f finv`,
  `dinter1E`, `transitivity`, `rewrite equiv[{1} 1 Lemma]`, `dexcepted`).
- **WhileSampling.ec** — losslessness of a rejection loop: `while true (variant) 1 (mu sample (predC
  test))`.
- **PIR.ec** — correctness via a loop invariant over a probabilistic loop; `conseq (: _ ==> true) (:
  _ ==> res = a i0)` separates functional correctness from termination.
- **hashed_elgamal_std.ec** — a 4-hop chain reducing to *two* assumptions (DDH + Entropy Smoothing);
  final bound a sum combined by `smt(ler_dist_add)`. Template for multi-assumption reductions.

---

## 7. The EasyCrypt MCP server

Located at `~/Dev/ProofFrog/easycrypt-mcp` (`easycrypt_mcp.py`). It wraps the patched EasyCrypt
binary (with `-lastgoals`/`-upto`) behind two tool families. The intended workflow: write a proof
skeleton with `admit`ed subgoals in **compile mode**, then discharge each `admit` one by one in
**interactive mode**.

### Compile mode (stateless, one-shot subprocess)

- `ec_compile(file_path, timeout=120)` — compile a whole file. Returns `"OK"` or `"FAILED"` plus
  errors and the last unproven goals.
- `ec_print_goals(file_path, line, column?, timeout=120)` — compile up to a position and print the
  open goals there (reprocesses from scratch each call). On failure, returns the last unproven goals.
- `ec_file_outline(file_path, upto_line?)` — list top-level declarations as `LINE KIND NAME`
  (recognizes `lemma`/`axiom`/`op`/`pred`/`abbrev`/`type`/`module`/`theory`/`section`/`clone`/
  `require`/`realize`/`instance`).

### Interactive mode (persistent REPL session)

- `cli_open(file_path, line)` — start a fresh REPL, process the file up to `line`. Returns
  `[line N] <goals>`. If compilation up to `line` fails, returns the failing point + goals.
- `cli_step(input)` — send one command (e.g. `"split."`, `"trivial."`). **If accepted, it is
  appended to the file**; **if rejected, the file is not modified** (rejection = output contains
  `<tty>:` or a line starting with `[error]`/`[critical]`).
- `cli_undo(line)` — discard step-added content after `line`, restart the REPL, replay up to `line`
  (line-level granularity, clamped to the `cli_open` line; not identical to the REPL's own `undo`).
- `cli_search(pattern)` — `search PATTERN.` in the session (`_` is wildcard, e.g. `"(_ + 0)"`).
- `cli_print(name)` — `print NAME.` (e.g. `"addz0"`, `"List.map"`).
- `cli_locate(name)` — `locate NAME.` (which theory defines it).
- `cli_close()` — terminate the REPL and clear session state.

Practical notes: only one interactive session at a time; `cli_step` writes accepted commands to disk
(so the file evolves as you prove); the REPL prompt format is `[N|mode]>`; default timeout 120s.

---

## 8. Pitfalls and quick idiom cheatsheet

### Pitfalls

- **Don't unfold operator definitions** in high-level proofs; prove a named lemma about the operator
  instead (`CLAUDE.md` guideline).
- **Use SMT only on simple goals** (arithmetic / pure logic), in direct mode `smt()` or `/#`.
- Program tactics are position-sensitive: `if`/`proc` act at the **start**, `wp`/`rnd` at the
  **end**. Reposition with `seq`/`sp`/`wp`/`swap` before applying them.
- `swap` fails if the two fragments interfere (overlapping reads/writes) — it is only for
  *independent* statements.
- `skip` does **not** close the resulting logical goal; follow with `auto`/`smt`/`progress`.
- In a `section`, `declare module A <: T { -G }` forbids `A` from touching globals `G`; you usually
  also need `declare axiom`s asserting `A`'s procedures are lossless.
- `[opaque]` operators won't unfold under `delta`/`simplify` — use their `fE` lemma.
- The final proof must contain no `admit`/`admitted`.

### Idiom cheatsheet

| Goal | Idiom |
|---|---|
| Open a probability equality | `byequiv=> //; proc; inline *.` |
| Open a probability = constant | `byphoare=> //; proc.` |
| Relate adversary call, trivial inv | `call (_: true).` |
| Up-to-bad adversary call | `call (_: BAD, RELINV, BADINV).` then discharge LL + bad-preservation |
| Match two samplings (bijection) | `rnd (fun x => f x) (fun y => finv y).` |
| Consume a one-sided sample | `rnd{1}` / `rnd{2}` |
| Reorder to align lines | `swap{1} a b.` / `swap{2} [a..b] c.` |
| Split both programs at a cut | `seq n m : (invariant).` |
| Kill a known-false/true guard | `rcondf{i} k.` / `rcondt{i} k.` |
| Straight-line + sampling | `auto` (or `wp; rnd; ...`) |
| Discharge losslessness | `islossless` (uses declared `*_ll` axioms) |
| Same code on both sides | `sim` / `do !sim` / `by sim` |
| Strengthen/weaken pre/post | `conseq (: _ ==> Q) (: _ ==> P).` |
| Fix a symbolic value before phoare | `exists* v; elim* => x.` |
| Bound a failure event | `fel n ctr g q bad [proc: cond] (inv).` |
| One-line up-to-bad | `byupto.` |
| Probability algebra | `Pr [mu_split e]`, `Pr [mu_sub]`, `Pr [mu_or]`, `Pr [mu_eq]`, `smt(mu_bounded, ge0_mu)` |
| Eager/lazy sampling swap | `eager proc ...` / `eager call (...)` |
| Distribution equality | `apply eq_distr => x. rewrite !mu1...` |
| Prove `mu1 {0,1} x = 1/2` | `rewrite dbool1E.` |
| Reductions | functor `module Red (A:Adv) : Target = { proc ... = { ... <@ A.foo(...); ... } }` |
| Game hop as a patch | `module G2 = G1 with { proc main [ ^ stmt ~ { newcode } ] }` |

### Key source/doc paths

- Short LLM guide: `doc/llm/CLAUDE.md`. Tactic reference pages: `doc/tactics/*.rst`.
- Tactic implementation: `src/ecParser.mly`, `src/ecLexer.mll`, `src/ecHiGoal.ml`,
  `src/ecLowGoal.ml`, `src/phl/ecPhl*.ml`.
- Theories: `theories/{core,prelude,datatypes,algebra,structure,distributions,crypto,encryption,
  modules,query_counting,tactics}/`.
- Examples: `examples/` (notably `elgamal.ec`, `SchnorrPK.ec`, `Pedersen.ec`, `br93.ec`, `PRG.ec`,
  `FundamentalLemma.ec`, `Upto.ec`, `hashed_elgamal_std.ec`).
- MCP server: `~/Dev/ProofFrog/easycrypt-mcp/easycrypt_mcp.py`.

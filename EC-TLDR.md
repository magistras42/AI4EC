# EasyCrypt TLDR: Implementation, Application, and Comparisons

## Introduction to Proof Assistants

Proof assistants (also known as Interactive Theorem Provers or ITPs), are tools designed for developing machine-backed formal proofs. Humans guide the proof strategy and breaks down complex mathematical theorems while the computer verifies each logical step against a set of foundational axioms. 

Proof assistants are generally categorized by their underlying logic(s):

* **Calculus of Inductive Constructions (CIC) / Dependent Type Theory:** Proof assistants like **Lean** and **Coq** (Rocq) - in these systems, types can depend on values (dependent types), and proofs are treated as computational terms (via the Curry-Howard correspondence). They start from a very small trusted computing base (the kernel) and build all of mathematics from scratch (and as a result have massive libraries).
* **Higher-Order Logic (HOL):** Tools like **Isabelle/HOL** and **HOL4** rely on a simpler, non-dependent typed lambda calculus. They use an LCF-style architecture where theorems are an abstract data type, ensuring that theorems can only be constructed via a small set of logical inference rules.
* **BUT WE ARE USING SOMETHING ELSE!!! Domain-Specific Logics:** Domain-specific proof assistants like **EasyCrypt** and **CryptoVerif** integrate custom program logics—such as Probabilistic Relational Hoare Logic (pRHL)—to directly express domain-specific concepts like probabilistic games, adversarial state machines, and indistinguishability.

---

## EasyCrypt vs. Lean

### Lean
Lean is a general-purpose, foundational ITP built entirely around a type checker for dependent type theory. 
* **Proofs as Code:** In Lean, a proof is a literal code term. To prove a cryptographic property, a user must write or generate a tactic that constructs the exact proof term down to the foundational axioms of mathematics.
* **No Native Cryptographic Primitives:** Lean has no native understanding of an "adversary," a "probabilistic game," or a "random oracle." To prove cryptographic security bounds, a user must manually define a probability monad, formalize state machines, and prove the measure-theoretic properties of distributions from scratch. There is some support for "symbolic model" cryptographic proofs and some very early work on what we're interested in (computational model, which deals with probability distributions over bitstrings), but it's very limited at the moment.

### EasyCrypt
EasyCrypt is a domain-specific framework purposefully engineered for the **game-hopping** paradigm. 
* **Multi-Layered Architecture:** EasyCrypt parses a built-in imperative programming language (with constructs like `while` loops, `if` statements, and random sampling `x <$ {0,1}`).
* **Probabilistic Relational Hoare Logic (pRHL):** The core proof engine is driven by pRHL, which allows the system to formally relate the memories and output distributions of two distinct probabilistic programs. 
* **SMT Integration via Why3:** Unlike Lean, which verifies explicit proof terms, EasyCrypt delegates heavy mathematical lifting to automated theorem provers. When a user applies a relational tactic in EasyCrypt, the system generates first-order logic Verification Conditions (VCs). These VCs are dispatched to external Satisfiability Modulo Theories (SMT) solvers (like Z3, Alt-Ergo). Because SMT solvers automatically discharge tedious arithmetic and memory-aliasing conditions, the cryptographer can focus strictly on the cryptographic reductions.


There are some cryptographic proofs more suitable for Lean over EasyCrypt, but we mainly want to focus on provable security. 

* **Lean (Functional Correctness):** Cryptographic proofs are largely about **Implementation Verification**. The goal is to prove that a specific, low-level implementation (e.g., C/Rust code or zero-knowledge assembly) performs exactly as intended by a mathematical specification without bugs or memory leaks. 
* **EasyCrypt (Provable Security Reductions):** EasyCrypt is designed for **Computational Security Proofs**. It abstracts away the low-level code and focuses on algorithmic design to prove properties like IND-CPA (Indistinguishability under Chosen-Plaintext Attack). You verify that *if* an adversary can break your abstract encryption protocol (Game 0), they can be converted into an adversary that breaks a known hard mathematical problem, like Decisional Diffie-Hellman (Game N). With the publication of CryptoLine, JazzLine, etc there is now a mechanism to import proofs from other tools/proof assistants into EasyCrypt so it also has support for some functional correctness/universal composability proofs.

---

## EasyCrypt Implementation Details

EasyCrypt justifies transitions between games in two distinct phases to combine relational program logic with probability bounding.

### Phase 1: Relational Judgments (pRHL)
At the heart of the system is the Probabilistic Relational Hoare Logic (pRHL) judgment:


```easycrypt
equiv [ Game1.main ~ Game2.main : Pre ==> Post ]

```

This states that if `Game1` and `Game2` begin in states satisfying the relation `Pre`, their output sub-distributions will satisfy the relation `Post`. Pre- and Post-conditions tag variables with `{1}` or `{2}` to denote which game's memory is being referenced (e.g., `x{1} = y{2}`).

To verify these judgments, EasyCrypt implements a mixed Weakest Precondition (wp) calculus:

1. **Inlining:** Non-adversary procedure calls are aggressively inlined (`inline *`).
2. **Code Motion:** Random assignments and statements are logically moved (`swap`).
3. **Relational WP:** A relational weakest precondition is applied to the deterministic fragments.
4. **Adversary Handling:** Adversary calls are bounded by their formal interfaces.

### Phase 2: Probability Bounding (pHL and Ambient Logic)

Once relational equivalence is established, EasyCrypt uses its Ambient Logic to derive claims about exact probabilities.
If a pRHL judgment proves `Game1` and `Game2` are identical, the rule `[PrEq]` automatically derives:

```easycrypt
Pr[ Game1.main() @ &m : A ] = Pr[ Game2.main() @ &m : B ]

```

For bounding probabilities, EasyCrypt provides the `pHL` (Probabilistic Hoare Logic) engine, allowing users to assert properties like `phoare [ M.p : Pre ==> Post ] <= e`.

---

## Example: Hashed ElGamal

To truly understand how this works in EasyCrypt, let's look at the IND-CPA security proof of Hashed ElGamal in the Random Oracle Model (this is pretty much the same as what's in the tutorial - included it here but might want to walk through in more detail there instead).

### Step 5.1: Declarations and Axioms

We first define the mathematical objects, group operators, and bitstrings.

```easycrypt
type group.
cnst q : int.
cnst g : group.
cnst k : int.

type skey = int.
type pkey = group.
type message = bitstring.
type cipher = group * bitstring.

op ( * ) : group -> group -> group = mul.
op ( ^ ) : group -> int -> group = pow.
op ( ^^ ) : bitstring -> bitstring -> bitstring = xor.

axiom pow_mul : forall (x y : int), (g ^ x) ^ y = g ^ (x * y).
axiom xor_comm : forall (x y : bitstring), x ^^ y = y ^^ x.

```

### Step 5.2: Adversary and Oracle Interfaces

We model the adversary as a set of procedures that share state, and the random oracle as a procedure maintaining a list of queries.

```easycrypt
adversary A1(pk : pkey) : message * message.
adversary A2(c : cipher) : bool.

module INDCPA = {
  var L : (group, bitstring) map
  var LA : group list

  proc H(x : group) : message = {
    var h : message;
    h <$ {0,1}^k;
    if (!x \in dom(L)) { L[x] <- h; }
    return L[x];
  }

  proc Main() : bool = {
    var sk : skey;
    var pk : pkey;
    var m0, m1 : message;
    var c : cipher;
    var b, b' : bool;

    L <- empty;
    LA <- [];
    sk <$ [0..q-1];
    pk <- g ^ sk;
    
    (m0, m1) <@ A1(pk);
    b <$ {0,1};
    
    (* Encryption block inlined *)
    var y <$ [0..q-1];
    var h <@ H(pk ^ y);
    c <- (g ^ y, h ^^ (b ? m0 : m1));
    
    b' <@ A2(c);
    return (b = b');
  }
}

```

### Step 5.3: Intermediate Games and Relational Proofs

We define a transformed game `G1` (where random choices are moved upfront) and use tactics to prove them equivalent.

```easycrypt
module G1 = {
  (* Simplified representation of G1 where 'y' is sampled early *)
  proc Main() : bool = {
    var x, y : int;
    var m0, m1, hy : message;
    var b, b' : bool;
    var alpha, y_prime : group;

    L <- empty; LA <- [];
    x <$ [0..q-1]; alpha <- g ^ x;
    y <$ [0..q-1]; y_prime <- alpha ^ y;
    
    (m0, m1) <@ A1(alpha);
    b <$ {0,1};
    hy <@ H(y_prime);
    b' <@ A2((g ^ y, hy ^^ (b ? m0 : m1)));
    
    return (b = b');
  }
}

lemma Fact1 : equiv [ INDCPA.Main ~ G1.Main : true ==> ={res} ].
proof.
  inline *; auto.
  (* The 'auto' tactic generates VCs sent to SMT solvers *)
  (* SMT handles the variable equivalences and algebraic simplifications *)
qed.

```

### Step 5.4: Bounding the Probability (Ambient Logic)

Once `Fact1` is proven, we can extract the exact probability claim into the ambient logic.

```easycrypt
lemma Pr1 &m : Pr[ INDCPA.Main() @ &m : res ] = Pr[ G1.Main() @ &m : res ].
proof.
  byequiv.         (* Transition from Ambient Logic to pRHL *)
  apply Fact1.     (* Apply the proven relational equivalence *)
  auto.
qed.

```

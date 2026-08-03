pragma +old_mem_restr. require import AllCore Distr FMap DBool FSet.
require import StdOrder.  import RealOrder.

type group.

op gid: group.

op (^^): group -> group -> group.

axiom grpA (x y z : group) : x ^^ y ^^ z = x ^^ (y ^^ z).

axiom grpI (x  : group) : x ^^ gid = x.

axiom grpC (x y : group) : x ^^ y = y ^^ x.

  (* exponent definitions *)

type exp.

op ( * ) : exp -> exp -> exp.

axiom expA (x y z : exp) : x * y * z = x * (y * z).

axiom expC (x y : exp) : x * y = y * x.

op dexp : exp distr.

axiom dexp_fu : is_full dexp.

axiom dexp_uni : is_uniform dexp.

axiom dexp_ll : is_lossless dexp.

op g : group.

op (^) : group -> exp -> group.

  (* forall (x : group), unique (q : exp), s.t. x = g^q *)

axiom generated (x : group) : exists (q : exp),  x = g ^ q.

axiom generated2 (x : group) (z : exp) : exists (q : exp), x ^ z = g ^ q ^ z.

axiom grexpA (q1 q2 : exp) : (g ^ q1) ^ q2 = g ^ (q1 * q2).

op gen (q : exp) = g ^ q.
axiom inj (q1 q2 : exp) : g^q1 = g^q2 => q1 = q2.

op gen_rel (x : group)(q : exp) : bool = x = g^q.

op e : exp.

op log (x : group) : exp = choiceb (gen_rel x) e.

(* print/search removed for agent sandbox: avoids oversized goal dumps *)

lemma gen_log : cancel gen log.
    proof.
  move => q.
  rewrite /gen /log /gen_rel; move : (generated (g^q)) => [q' h_gen]; have h_cb := choicebP (fun q0 : exp => g^q = g^q0) e; have h_rel : g^q = g^(choiceb (fun q0 : exp => g^q = g^q0) e); [ apply h_cb; exists q'; exact h_gen | ]; apply inj; rewrite -h_rel.
  trivial.

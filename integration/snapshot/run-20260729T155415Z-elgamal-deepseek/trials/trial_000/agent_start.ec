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
  admit.




  qed.

lemma log_gen : cancel log gen.
    proof.
  admit.


    qed.

lemma grexpAll (x : group) (q1 q2 : exp) : (x ^ q1) ^q2 = x ^ (q1 * q2).
    proof.
  admit.




  qed.

      (* text definitions *)
type text.

op (+^) : text -> text -> text.

axiom textA (x y z : text) : x +^ y +^ z = x +^ (y +^ z).

op text0 : text.

axiom textC (x y : text) : x +^ y = y +^ x.

axiom textI (x : text) : x +^ text0 = x.

axiom textR (x : text) : x +^ x = text0.

op dtext : text distr.

axiom dtext_fu : is_full dtext.

axiom dtext_uni : is_uniform dtext.

axiom dtext_ll : is_lossless dtext.

type cipher = group * text.

module type RO = {
  proc init() : unit

  proc f(x : group) : text
}.

(* Defining Correctness *)
module type ENC (RO : RO) = {
  proc key_gen() : group * exp

  proc enc(pubk : group, t : text) : cipher {RO.f}

  proc dec(privk : exp, c : cipher) : text {RO.f}
}.


      (* Random Oracle *)


module RO : RO = {
  var mp : (group, text) fmap (* finite map, table *)

  proc init() : unit = {
    mp <- empty;  (* empty map *)
  }

  proc f(x : group) : text = {
    var y : text;
    if (x \notin mp) { (* not in mp's domain *)
      y <$ dtext;   (* updating mp so as before but value of *)
      mp.[x] <- y;  (* x is what we randomly picked *)
    }
    return oget mp.[x]; (* mp.[x] is either None or Some t *)
  }
}.

(* module to check for correctness of encryption scheme.

    is correct if it the original input = final output when run through all funcitons with probability 1 *)

module Cor (Enc : ENC) = {
  module E = Enc(RO)

  proc main(x : text) : bool = {
  var pubk : group; var privk : exp; var c : cipher; var y : text;

    (pubk, privk) <@ E.key_gen();
      c <@ E.enc(pubk, x);
      y <@ E.dec(privk, c);
    return x = y;
  }

}.


module (HEG : ENC) (RO : RO) ={
  proc key_gen() : group * exp = {
    var q : exp;
    q <$ dexp;
    return (g ^ q, q);
  }

  proc enc(pubk : group, t : text) : cipher = {
      var r : exp;
    var u : text;
    r <$ dexp;

    u <@ RO.f(pubk ^ r);

    (*pubk ^ r = (g ^ q) ^ r = g ^ (q * r)*)
    return (g ^ r, t +^ u);

  }

  proc dec(privk : exp, c : cipher) : text = {
    var u,v : text;
      var x : group;

    (x, u) <- c;
    v <@ RO.f(x ^ privk);
    return v +^ u;
    (* (g ^ r) ^ q = g ^ (r * q) = g ^ (q * r) *)
    (*t +^ u +^ u = t =^ textO = t *)
  }

}.


    (* prove encryption scheme is stateless *)

lemma enc_stateless (g1 g2 : glob HEG) : g1 = g2.
    proof.

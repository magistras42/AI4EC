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

print cancel.

lemma gen_log : cancel gen log.
    proof.
      rewrite /gen /log /cancel => q.
      have choice_g2q := choicebP ( gen_rel(g ^ q)) e.
      have /choice_g2q @/gen_rel/inj {2}-> //:
      exists(q' : exp), gen_rel (g^q) q'
      by rewrite /gen_rel; by exists q.
  qed.

lemma log_gen : cancel log gen.
    proof.
      rewrite /gen /log /cancel => x.
      have @/gen_rel <-// := choicebP ( gen_rel x) e.
      rewrite generated.
    qed.

lemma grexpAll (x : group) (q1 q2 : exp) : (x ^ q1) ^q2 = x ^ (q1 * q2).
    proof.
      have ->: x = g ^ log x.
      have ->: g ^ log x = gen (log x).
      by rewrite /gen.
      by rewrite log_gen.
     by rewrite !grexpA expA.
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
      trivial.
  qed.

      (* prove encryption scheme is correct *)
lemma correctness : phoare[Cor(HEG).main : true ==> res] = 1%r.
    proof.
      proc.
    inline*.
    seq 13: (x1 = g ^ (q * r) /\ x2 = x1 /\ t = x /\ RO.mp.[x1] = Some u /\ u0 = t +^ u).
    auto.
    seq 3: (pubk0 = g ^ q /\ privk = q).
    auto.
    auto.
    progress.
    apply dexp_ll.
    wp.
    seq 2: (pubk0 = g ^ q /\ privk = q /\ t = x).
    auto.
    auto; progress.
    apply dexp_ll.
    sp; if.
    auto.
    progress.
    apply dtext_ll.
    by rewrite grexpA.
    by rewrite !grexpA expC.
    rewrite get_set_sameE.
    by rewrite oget_some.
    auto; progress.
    by rewrite grexpA.
    by rewrite !grexpA expC.
    by rewrite get_some.
    hoare.
    simplify; auto.
    trivial.
    hoare.
    auto.
    trivial.
    rcondf 1.
    auto; progress.
    smt().
    auto; progress.
    rewrite H oget_some -textA -textC -textA textR.
    by rewrite textC textI.
    hoare.
    auto.
    seq 1: true.
    auto.
    sp.
    seq 1: (privk = q /\ pubk = g ^ q /\ pubk0 = pubk /\ t = x).
    auto.
    sp; if.
    auto; progress.
    by rewrite grexpA.
    by rewrite !grexpA expC.
    rewrite get_some.
    smt(mem_set).
    by rewrite get_set_sameE oget_some.
    auto;progress.
    rewrite grexpA.
    auto.
    by rewrite !grexpA expC.
    by rewrite get_some.
    trivial.
  qed.


  module type ADV (RO : RO) = {
    proc choose(pubk : group) : text * text {RO.f}

    proc guess(c : cipher) : bool {RO.f}
  }.


  module INDCPA (Enc : ENC, Adv : ADV) = {
    module A = Adv(RO)
    module E = Enc(RO)

    proc main() : bool = {
      var x1, x2 : text; var c : cipher; var choice, guess : bool;
      var pubk : group; var privk : exp;

      RO.init();

      (* get pubk, similar to initializing EO *)
      (pubk, privk) <@ E.key_gen();


      (x1,x2) <@ A.choose(pubk);

      choice <$ {0,1};
        c <@ E.enc(pubk, choice ? x1 : x2);

        guess <@ A.guess(c);

      return choice = guess;
    }
  }.


  module type LCDH_ADV = {
    proc main(x1 x2: group) : group fset
  }.

  module LCDH (Adv : LCDH_ADV) = {
    proc main() : bool = {
    var q1, q2 : exp;
    var ys : group fset;
    q1 <$ dexp; q2 <$ dexp;
    ys <@ Adv.main(g^q1, g^q2);
      return mem ys (g^ (q1*q2));
      }
    }.

(* based on G2 - see below *)

module Adv2LCDHAdv(Adv : ADV) = {
  module RO_track : RO = {
    var mp : (group, text) fmap (* finite map, table *)

    proc init() : unit = {
    }

    proc f(x : group) : text = {
        var y : text;
      if (x \notin mp) { (* not in mp's domain *)
        y <$ dtext;   (* updating mp so as before but value of *)
        mp.[x] <- y;  (* x is what we randomly picked *)
      }
      return oget mp.[x]; (* mp.[x] is either None or Some t *)
    }
  }

  module A = Adv(RO_track)

  proc main(grp1, grp2 : group) : group fset = {
    var x1, x2 : text; var c : cipher; var choice, guess : bool;
    var t, y : text;

    RO_track.mp <- empty;

    (x1,x2) <@ A.choose(grp1);

    choice <$ {0,1};

    t <- choice ? x1 : x2;

    y <$ dtext;
    c <- (grp2, t +^ y);

    guess <@ A.guess(c);

    return fdom RO_track.mp;
  }
}.

section.

declare module Adv <: ADV{RO, Adv2LCDHAdv}.

axiom Adv_choose_ll :
  forall (RO <: RO{Adv}),
  islossless RO.f => islossless Adv(RO).choose.

axiom Adv_guess_ll :
  forall (RO <: RO{Adv}),
  islossless RO.f => islossless Adv(RO).guess.

local module RO_track : RO = {
  var mp : (group, text) fmap (* finite map, table *)
  var badHappened : bool
  var bad_grp : group

  proc init() : unit = {
      (* init moved to be inline of games *)
  }

  proc f(x : group) : text = {
      var y : text;
      if (x = bad_grp) {
        badHappened <- true;
      }
    if (x \notin mp) { (* not in mp's domain *)
      y <$ dtext;   (* updating mp so as before but value of *)
      mp.[x] <- y;  (* x is what we randomly picked *)
    }
    return oget mp.[x]; (* mp.[x] is either None or Some t *)
  }
}.

local module G1 = {
    module A = Adv(RO_track)

    proc main() : bool = {
      var x1, x2 : text; var c : cipher; var choice, guess : bool;
      var q1, q2 : exp;
        var u,y,t : text;

      (* key_gen but inlined *)
       q1 <$ dexp;
       q2 <$ dexp;

      RO_track.mp <- empty;
      RO_track.badHappened <- false;
      RO_track.bad_grp <- g ^ (q1 * q2);

      (x1,x2) <@ A.choose(g ^ q1);

        choice <$ {0,1};

        (* enc Inlined *)

        t <- (choice ? x1 : x2);
      (* randomly choosing a u inlined *)
      if (g ^ (q1 * q2) \notin RO_track.mp) {
         y <$ dtext;
         RO_track.mp.[g ^ (q1 * q2)] <- y;
      }

      u <- oget RO_track.mp.[g ^ (q1 * q2)];

      c <- (g ^ q2, t +^ u);

      guess <@ A.guess(c);

      return choice = guess;
    }
  }.

local lemma INDCPA_HEG_G1 &m :
  Pr[INDCPA(HEG, Adv).main() @ &m : res] = Pr[G1.main() @ &m : res].
  proof.
  byequiv => //.
    proc.
    inline*.
    swap{2} 3 -2.
    swap{1} 8 -5.
    sp.
    seq 2 2 : (q{1} = q1{2} /\ r{1} = q2{2} /\ RO.mp{1} = RO_track.mp{2}).
    auto.
    sp.
    seq 1 1 : (={glob Adv, x1,x2} /\ q{1} = q1{2} /\ r{1} = q2{2} /\ RO.mp{1} = RO_track.mp{2}
      /\ pubk{1} = g^q{1}).
    call(_: (RO.mp{1} = RO_track.mp{2})).
    proc.
    if{2}.
    sp.
    if; progress.
    wp; rnd.
    auto.
    if; progress.
    wp; rnd.
    auto.
    auto.
    seq 1 1 : (={glob Adv, choice,x1,x2} /\ q{1} = q1{2} /\ r{1} = q2{2} /\
      RO.mp{1} = RO_track.mp{2} /\ pubk{1} = g^q{1}).
    auto.
    sp; if; progress.
    by rewrite -grexpA.
    by rewrite grexpA.
    seq 1 1 : (={glob Adv, choice, y, t} /\ q{1} = q1{2} /\ r{1} = q2{2} /\
      RO.mp{1} = RO_track.mp{2} /\ pubk{1} = g^q{1} /\ x{1} = pubk{1}^r{1}).
    auto.
    sp.
    call(_: (RO.mp{1} = RO_track.mp{2})).
    proc.
    if{2}.
    sp.
    if; progress.
    auto.
    if; progress.
    auto.
    auto; progress.
    rewrite grexpA //.
    rewrite grexpA //.
    sp.
    call(_: (RO.mp{1} = RO_track.mp{2})).
    proc.
    if{2}.
    sp.
    if; progress.
    auto.
    if; progress.
    auto.
    auto; progress.
    by rewrite grexpA.
  (*admits are only for the glob Adv being equal problem*)
qed.

(* to use up to bad reasoning... *)

local module G2 = {
    module A = Adv(RO_track)

    proc main() : bool = {
      var x1, x2 : text; var c : cipher; var choice, guess : bool;
      var q1, q2 : exp;
        var t, y : text;

      (* key_gen but inlined *)
       q1 <$ dexp;
       q2 <$ dexp;

      RO_track.mp <- empty;
      RO_track.badHappened <- false;
      RO_track.bad_grp <- g ^ (q1 * q2);

      (x1,x2) <@ A.choose(g ^ q1);

        choice <$ {0,1};

      t <- (choice ? x1 : x2);


      y <$ dtext;
      c <- (g ^ q2, t +^ y);

      guess <@ A.guess(c);

      return choice = guess;
    }
  }.

(* up to bad reasoning *)

local lemma G1_G2_eq :
  equiv[G1.main ~ G2.main :
        true ==>
        ={RO_track.badHappened} /\
        (! RO_track.badHappened{1} => ={res})].
proof.
 (* look at Sym encryption example, where I first use up to bad
  reasoning *)
  proc.
  inline*.
  seq 5 5 :
    (={q1, q2, RO_track.mp, RO_track.bad_grp, RO_track.badHappened} /\
     !RO_track.badHappened{1} /\ RO_track.bad_grp{1} = g ^ (q1{1} * q2{1}) /\
     RO_track.mp{1} = empty).
  auto.
  seq 3 3 :
    (={q1, q2, RO_track.mp, RO_track.bad_grp, RO_track.badHappened, t, choice, glob Adv} /\
     RO_track.bad_grp{1} = g ^ (q1{1} * q2{1}) /\
     (RO_track.badHappened{1} <=> g ^ (q1{1} * q2{1}) \in RO_track.mp{1})).
  auto.
  call
  (_ : ={RO_track.mp, RO_track.bad_grp, RO_track.badHappened} /\
       (RO_track.badHappened{1} <=> RO_track.bad_grp{1} \in RO_track.mp{1})).
  proc.
  if; progress.
  sp; if; progress.
  auto; progress.
  by rewrite mem_set.
  if; progress.
  auto; progress.
  by rewrite mem_set H3.
  smt(mem_set).
  auto; progress.
  trivial.
  smt(mem_empty).
  seq 3 2 :
    (={RO_track.bad_grp, choice, glob Adv, RO_track.badHappened} /\
     (! RO_track.badHappened{1} =>
      ={c} /\
      RO_track.bad_grp{1} \notin RO_track.mp{2} /\
      RO_track.mp{1} = RO_track.mp{2}.[RO_track.bad_grp{1} <- y{1}])).
  if{1}.
  wp; auto; progress.
  rewrite get_set_eqE.
  trivial.
  by rewrite oget_some.
  wp.
  rnd{2}.
  skip; progress.
  by rewrite dtext_ll.
  trivial.
    trivial.
  exlim y{1} => y_L.
    call(_ :
         ={RO_track.bad_grp, glob Adv, RO_track.badHappened} /\
         (! RO_track.badHappened{1} =>
          ={c} /\
          RO_track.bad_grp{1} \notin RO_track.mp{2} /\
          RO_track.mp{1} = RO_track.mp{2}.[RO_track.bad_grp{1} <- y_L]) ==>
          ={RO_track.badHappened} /\ (! RO_track.badHappened{1} => ={res})).
    proc (RO_track.badHappened) (* bad event in second game *)
         (={RO_track.badHappened, RO_track.bad_grp} /\ (* when bad event not hold *)
          RO_track.bad_grp{1} \notin RO_track.mp{2} /\
          RO_track.mp{1} = RO_track.mp{2}.[RO_track.bad_grp{1} <- y_L])
         (RO_track.badHappened{1}). (* when bad event holds *)
    progress.
    smt().
    smt().
    smt(). 
    progress.
    smt().       
    smt().
    progress.
    apply (Adv_guess_ll RO).
    apply H.
    proc.
    if; progress.             
    
    sp. if{1}. if{2}. auto. auto. if{2}. auto; progress.
    by rewrite dtext_ll.
    auto; progress.
    if; progress.
    smt(mem_set).
    smt(mem_set).
    auto; progress.
    smt.
    smt(mem_set).
    by rewrite set_set_neqE.
    auto; progress.         
    by rewrite get_set_neqE.
    progress.
    proc.
    if.
    sp; if; progress.
    auto; progress.
    by rewrite dtext_ll.
    if; progress.
    auto; progress.
    by rewrite dtext_ll.
    progress.
    proc.
    if.
    sp; if; progress.
    auto; progress.
    by rewrite dtext_ll.
    if.
    auto; progress.
    by rewrite dtext_ll.
    auto; progress.
    auto; progress.
    smt().          
qed.

local lemma RO_LCDHAdv (q1 q2 : exp) :
equiv[RO_track.f ~ Adv2LCDHAdv(Adv).RO_track.f :
   ={x} /\
   RO_track.badHappened{1} =
   (g ^ (q1 * q2) \in fdom Adv2LCDHAdv.RO_track.mp{2}) /\
     RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\ RO_track.bad_grp{1} = g ^ (q1 * q2)  ==>
     ={res} /\ RO_track.badHappened{1} = (g ^ (q1 * q2) \in fdom Adv2LCDHAdv.RO_track.mp{2}) /\
     RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\ RO_track.bad_grp{1} = g ^ (q1 * q2)].
 proof.
   proc.
   if{1}.
   sp.
   if.
   progress.
   auto.
   progress.
 search dom.
   rewrite mem_fdom mem_set //.
   skip; progress.
   by rewrite mem_fdom H.
   if.
   progress.
   auto.
   progress.
   rewrite !mem_fdom mem_set.
   smt().
   auto.
qed.

local lemma G2_bad_ub &m :
  Pr[G2.main() @ &m : RO_track.badHappened] =
  Pr[LCDH(Adv2LCDHAdv(Adv)).main() @ &m : res].
proof.
byequiv => //.
  proc.
  inline*.
  seq 5 5 : (={q1, q2} /\ RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\
    RO_track.bad_grp{1} = g ^ (q1{2} * q2{2}) /\ grp1{2} = g^q1{2} /\ grp2{2} = g^q2{2} /\
    RO_track.badHappened{1} = (g ^ (q1{2} * q2{2}) \in fdom Adv2LCDHAdv.RO_track.mp{2})).
  auto; progress.
  rewrite mem_fdom mem_empty //.
  wp.
  exlim q1{1} => q1_L.
  exlim q2{1} => q2_L.
  call(_: RO_track.badHappened{1} = (g ^ (q1_L * q2_L) \in fdom Adv2LCDHAdv.RO_track.mp{2}) /\
    RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\ RO_track.bad_grp{1} = g ^ (q1_L * q2_L)).
  apply (RO_LCDHAdv q1_L q2_L).
  auto.
  call(_: RO_track.badHappened{1} = (g ^ (q1_L * q2_L) \in fdom Adv2LCDHAdv.RO_track.mp{2}) /\
    RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\ RO_track.bad_grp{1} = g ^ (q1_L * q2_L)).
  apply (RO_LCDHAdv q1_L q2_L).
  auto.
qed.

local lemma G1_G2 &m :
  `|Pr[G1.main() @ &m : res] - Pr[G2.main() @ &m : res]| <=
  Pr[LCDH(Adv2LCDHAdv(Adv)).main() @ &m : res].
proof.
    rewrite (RealOrder.ler_trans Pr[G2.main() @ &m : RO_track.badHappened]).
    byequiv
    (_ : true ==>
     (={badHappened}(RO_track, RO_track)) /\
      (! RO_track.badHappened{2} => ={res})) :
     (RO_track.badHappened) => //.
    conseq G1_G2_eq => //.
    progress; smt().
    by rewrite RealOrder.lerr_eq (G2_bad_ub &m).
qed.

local module G3 = {
    module A = Adv(RO_track)

    proc main() : bool = {
      var x1, x2 : text; var c : cipher; var choice, guess : bool;
      var q1, q2 : exp;
      var t, y (*,t*) : text;

       q1 <$ dexp;
       q2 <$ dexp;

      RO_track.mp <- empty;
      RO_track.badHappened <- false;
      RO_track.bad_grp <- g ^ (q1 * q2);

      (x1,x2) <@ A.choose(g ^ q1);

        choice <$ {0,1};

        y <$ dtext;   

        c <- (g ^ q2, y); (* because y is one-time pad *)

        guess <@ A.guess(c);

      return choice = guess;
    }
  }.

local lemma G2_G3 &m :
  Pr[G2.main() @ &m : res] = Pr[G3.main() @ &m : res].
  proof.
  byequiv => //.
    proc.
    seq 5 5 : (={q1,q2,RO_track.bad_grp, RO_track.mp}).
    auto.
    seq 1 1 : (={glob Adv, q1,q2,RO_track.bad_grp, RO_track.mp}).
    call(_ : ={RO_track.bad_grp,RO_track.mp}).
    proc.
    if; progress.
    sp; if; progress.
    auto.
    if; progress.
    auto.
    auto.
    seq 4 3 : (={glob Adv, q1, q2, c, RO_track.bad_grp, RO_track.mp, choice}).
    wp.
    rnd(fun x => t{1} +^ x).
    auto; progress.
    by rewrite -textA textR textC textI.
    apply dtext_uni => //.
    rewrite dtext_fu.
    rewrite dtext_fu.
    by rewrite -textA textR textC textI.
    call(_ : ={RO_track.bad_grp, RO_track.mp}).
    proc.
    if; progress.
    sp; if; progress.
    auto.
    if; progress.
    auto.
    auto.
qed.

local lemma RO_track_f_ll : islossless RO_track.f.
proof.
  proc.
  if.
  sp; if.
  auto; progress; by rewrite dtext_ll.
  auto.
  if.
  auto; progress; by rewrite dtext_ll.
  auto.
qed.

local lemma G3_true &m :
  Pr[G3.main() @ &m : res] = 1%r / 2%r.
  proof.
  byphoare => //.
    proc.
    swap 7 3.
    rnd (pred1 guess).
    call(_: true).
    apply Adv_guess_ll.
    apply RO_track_f_ll.
    auto.
    call(_: true).
    apply Adv_choose_ll.
    apply RO_track_f_ll.
    auto; progress.
    by rewrite dexp_ll.
    by rewrite dtext_ll.
    smt. (* prove that {0,1}'s result is 1/2*)
    trivial.
qed.

(* sequence of games:

INDCPA(HEG, Adv) <-> G1 <-> G2 <-> G3 = 1%r/2%r
*)

lemma INDCPA_Sec &m :
  `|Pr[INDCPA(HEG, Adv).main() @ &m : res] - 1%r / 2%r| <=
  Pr[LCDH(Adv2LCDHAdv(Adv)).main() @ &m : res].
  proof.
    rewrite -(G3_true &m) -(G2_G3 &m) (INDCPA_HEG_G1 &m) (G1_G2 &m)//.
qed.

end section.

print INDCPA_Sec.

lemma INDCPA_Security (Adv <: ADV{RO, Adv2LCDHAdv}) &m :
  (forall (RO <: RO{Adv}),
   islossless RO.f => islossless Adv(RO).choose) =>
  (forall (RO <: RO{Adv}),
   islossless RO.f => islossless Adv(RO).guess) =>
  `|Pr[INDCPA(HEG, Adv).main() @ &m : res] - 1%r / 2%r| <=
  Pr[LCDH(Adv2LCDHAdv(Adv)).main() @ &m : res].
proof.
move => Adv_choose_ll Adv_guess_ll.
apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).
qed.

(* MAC.ec *)

(* Message Authentication Codes *)

(* definitions, including games for judging correctness and EU-CMA
   (Existential Unforgeability under a Chosen Message Attack) security *)

prover [""].  (* no SMT solvers *)

require import AllCore Distr DBool FSet.

(* theory parameters *)

type key.  (* tagging keys *)

type text.  (* plaintexts *)

type tag.  (* tags *)

op tag_def : tag.  (* default tagtext *)

(*
(* MAC oracle limit before game's MAC
   this says limit_pre has type int and the axiom ge0_limit_pre says
   limit_pre is non-negative *)
op limit_pre : {int | 0 <= limit_pre} as ge0_limit_pre.
(* MAC oracle limit after game's MAC *)
op limit_post : {int | 0 <= limit_post} as ge0_limit_post.
*)

(* end theory parameters *)

(* module type of MAC schemes

   an MAC scheme Mac should be stateless, meaning that

     forall (g1 g2 : glob Mac), g1 = g2 *)

module type MAC = {
  (* key generation *)
  proc key_gen() : key

  (* Tagging *)
  proc tag(k : key, x : text) : tag

  (* Verifying *)
  proc ver(k : key, x : text, t : tag) : bool
}.

(* module for checking correctness of a MAC, parameterized
   by MAC scheme

   correctness means main returns true with probability 1, without any
   assumptions about value of x *)

module Cor (Mac : MAC) = {
  proc main(x : text) : bool = {
    var k : key; var t : tag; var y : bool;
    k <@ Mac.key_gen();
    t <@ Mac.tag(k, x);
    y <@ Mac.ver(k, x, t);
    return y;
  }
}.

(* module type of MAC oracles *)

module type MO = {
  (* initialization *)
  proc * init() : unit

  (* called by adversary *)
  proc gtag(x : text) : tag

  (* called by game, only *)
  proc gver(x : text, t : tag) : bool
}.

(* standard MAC oracle, constructed from an MAC
   scheme *)

module MacO (Mac : MAC) : MO = {
  var key : key
  var seen : text fset

  proc init() : unit = {
    seen <- fset0;
    key <@ Mac.key_gen();
  }

  proc gtag(x : text) : tag = {
    var t : tag;
    seen <- seen `|` fset1 x;
    t <@ Mac.tag(key, x);
    return t;
  }

  proc gver(x : text, t : tag) : bool = {
    var b : bool;
    if (x \in seen) {
      b <- false;
    }
    else {
      b <@ Mac.ver(key, x, t);
    }
    return b;
  }
}.

(* MAC adversary, parameterized by encryption oracle, MO

   choose may only call MO.gtag *)

module type ADV (MO : MO) = {
  (* choose a pair of plaintexts, x1/x2 *)
  proc * choose() : text * tag {MO.gtag}
}.

(* EU-CPA security game, parameterized by an authentication scheme Mac
   and adversary Adv

   an authentication scheme is secure iff the probability of main
   returning true (Adv winning the game) is close to 0, i.e., Adv
   should not be able to correctly produce a valid tag for a message

   formally, we want that the absolute value of the difference between
   the probability that main returns true and 0 to be small; this also
   says that Adv should not lost with probability close to 1
   (if it could reliably lose, the addition of
   a negation would result in an adversary that could reliably win)

   because Adv can use MO to authenticate the plaintexts it chooses,
   the authentication procedure of a secure authentication scheme is
   necessarily probabilistic

   Adv may directly use Mac as much as it wants
   (and in any case could simulate it), but the security theorem must
   say it can't read/write the global variables of EncO 
   
   Additionally, unlike Encryption, Mac is not stateless. When Adv 
   submits a potentially valid message/tag pair, the pair must not 
   be one the adversary already has knowledge of before. Otherwise the game 
   will automatically return false. This prevents replay attacks*)

module MAC(Mac : MAC, Adv : ADV) = {
  module MO = MacO(Mac)        (* make MO from Mac *)
  module A = Adv(MO)           (* connect Adv to MO *)

  proc main() : bool = {
    var b : bool; var m : text; var t : tag;
    MO.init();                 (* initialize MO *)
    (m, t) <@ A.choose();      (* let A choose a mesage/tag pair *)
    b <@ MO.gver(m, t);        (* return true if valid message/tag pair *)
    return b;                  (* see if A guessed correctly, winning game *)
  }
}.


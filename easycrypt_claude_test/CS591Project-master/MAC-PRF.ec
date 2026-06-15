(* Mac-PRF.ec *)

(*EU-CMA (Existential Unforgeability under a Chosen Message attack)
   security for symmetric encryption built out of pseudorandom
   function *)

prover ["Z3"].  (* no SMT solvers *)

require import AllCore Distr DBool List SmtMap FSet Mu_mem.
require import StdBigop. import Bigreal BRA.
require import StdOrder. import RealOrder.
require import StdRing. import RField.
(*require import SmtMapAux.*)
require BitWord FelTactic.

(* require but don't import theories for authentication  and
   pseudorandom functions - then will be cloned below *)

require MAC PseudoRandFun.

(* PRF and authentication keys: bitstrings of length key_len *)

(* this says key_len has type int, and the axiom gt0_key_len says
   that key_len is positive *)
op key_len : {int | 0 < key_len} as gt0_key_len.

clone BitWord as Key with
  op n <- key_len
proof gt0_n by apply gt0_key_len.

type key = Key.word.

op key0 : key = Key.zerow.  (* all 0 key *)

(* full/uniform/lossless distribution *)

op dkey : key distr = Key.DWord.dunifin.

lemma dkey_fu : is_full dkey.
proof. apply Key.DWord.dunifin_fu. qed.

lemma dkey_uni : is_uniform dkey.
proof. apply Key.DWord.dunifin_uni. qed.

lemma dkey_ll : is_lossless dkey.
proof. apply Key.DWord.dunifin_ll. qed.

(* texts: bitstrings of length text_len *)

op text_len : {int | 0 < text_len} as gt0_text_len.

clone BitWord as Text with
  op n <- text_len
proof gt0_n by apply gt0_text_len.

type text = Text.word.

op text0 : text = Text.zerow.  (* all 0 text *)

op (+^) : text -> text -> text = Text.(+^).  (* bitwise exclusive or *)

lemma text_xorK (x : text) : x +^ x = text0.
proof. apply Text.xorwK. qed.

lemma text_xorA (x y z : text) : x +^ (y +^ z) = x +^ y +^ z.
proof. apply Text.xorwA. qed.

lemma text_xorC (x y : text) : x +^ y = y +^ x.
proof. apply Text.xorwC. qed.

lemma text_xor_rid (x : text) : x +^ text0 = x.
proof. apply Text.xorw0. qed.

lemma text_xor_lid (x : text) : text0 +^ x = x.
proof. by rewrite Text.xorwC text_xor_rid. qed.

(* full/uniform/lossless distribution *)

op dtext : text distr = Text.DWord.dunifin.

lemma dtext_fu : is_full dtext.
proof. apply Text.DWord.dunifin_fu. qed.

lemma dtext_uni : is_uniform dtext.
proof. apply Text.DWord.dunifin_uni. qed.

lemma dtext_ll : is_lossless dtext.
proof. apply Text.DWord.dunifin_ll. qed.

lemma mu1_dtext (x : text) : mu1 dtext x = 1%r / (2 ^ text_len)%r.
proof. by rewrite Text.DWord.dunifin1E Text.word_card. qed.

lemma mu_dtext_mem (xs : text fset) :
  mu dtext (mem xs) = (card xs)%r / (2 ^ text_len)%r.
proof.
apply (mu_mem _ _ (1%r / (2 ^ text_len)%r)) => x mem_xs_x.
apply mu1_dtext.
qed.


(* pseudorandom function (PRF)

   the definition of F could be spelled out, and is considered public
   -- i.e., any adversary is entitled to use F and know its
   definition *)

op F : key -> text -> text.  (* PRF *)

(* clone and import pseudorandom function and symmetric encryption
   theories, substituting for parameters, and proving the needed
   axioms *)

clone import PseudoRandFun as PRF with
  type key  <- key,
  op dkey   <- dkey,
  type text <- text,
  op dtext  <- dtext,
  op F      <- F
proof *.
realize dkey_fu. apply dkey_fu. qed.
realize dkey_uni. apply dkey_uni. qed.
realize dkey_ll. apply dkey_ll. qed.
realize dtext_fu. apply dtext_fu. qed.
realize dtext_uni. apply dtext_uni. qed.
realize dtext_ll. apply dtext_ll. qed.

clone import MAC as M with
  type key      <- key,
  type text     <- text,
  type tag      <- text, (* use tags as text definition because PRF outputs are same length as input *)
  op tag_def    <- text0
proof *.

(* definition of authentication

   key_gen and tag are probabilistic, but ver is deterministic

  The module has no state
 *)

module Mac : MAC = {
  proc key_gen() : key = {
    var k : key;
    k <$ dkey;
    return k;
  }

  proc tag(k : key, x : text) : text = {
    return (F k x);
  }

  proc ver(k : key, x : text, t : text) : bool = {
    var test : text;
    test <- F k x;
    return test = t;
  }
}.

(* prove mac scheme is stateless *)

lemma mac_stateless (g1 g2 : glob Mac) : g1 = g2.
proof. trivial. qed.

(* lemma proving correctness of mac *)

lemma correctness : phoare[Cor(Mac).main : true ==> res] = 1%r.
proof. proc; inline*; auto; progress.
  apply dkey_ll.
qed.

(* module turning an authentication adversary Adv into a random function
   adversary

   used in upper bound of EU-CMA security theorem, but to understand
   why it's defined the way it is, need to read proof

   note that it doesn't interact with any other module (except though
   its Adv and RF parameters) *)

module Adv2RFA(Adv : ADV, RF : RF) = {
  module MO : MO = {  (* uses RF.f *)

    var seen : text fset

    proc init() : unit = {
      (* RF.init will be called by GRF *)
      seen <- fset0;
    }

    
    proc gtag(x : text) : text = {
      var t : text;
      seen <- seen `|` fset1 x;
      t <@ RF.f(x);
      return t;
    }

    proc gver(x : text, t : text) : bool = {
      var test : text;
      var b : bool;
      if (x \in seen) {
        b <- false;
      }
       else {
        test <@ RF.f(x);
        b <- test = t;
      }
      return b;
    }
   
  }

  module A = Adv(MO)

  
  proc main() : bool = {
    var b : bool; var m : text; var t : text;
    MO.init();
    (m, t) <@ A.choose();
    b <@ MO.gver(m, t);
    return b;
  }
}.

(* see after section for security theorem

   *)

section.

(* declare adversary with module restrictions: Adv can't
   interact with MacO, PRF, TRF or Adv2RFA

   the scope of Adv is the rest of the section *)

declare module Adv : ADV{MacO, PRF, TRF, Adv2RFA}.

(* axiomatize losslessness (termination for all arguments) of Adv's
   procedures, for all authentication oracles whose accessible procedures
   are themselves lossless

   this is required for us to use up to bad reasoning *)

axiom Adv_choose_ll :
  forall (MO <: MO{Adv}),
  islossless MO.gtag => islossless Adv(MO).choose.

(* version of encryption oracle that takes implementation of
   RF as argument - instrumented to detect two distinct
   kind of clashes *)

local module MO_RF (RF : RF) : MO = {
  var seen : text fset

  proc init() = {
    RF.init();
    seen <- fset0;
  }

      proc gtag(x : text) : text = {
      var t : text;
      seen <- seen `|` fset1 x;
      t <@ RF.f(x);
      return t;
    }

    proc gver(x : text, t : text) : bool = {
      var test : text;
      var b : bool;
      if (x \in seen) {
        b <- false;
      }
       else {
        test <@ RF.f(x);
        b <- test = t;
      }
      return b;
    }
    

  
  
  
}.

(* game parameterized by implementation of RF, and using MO_RF *)

local module G1 (RF : RF) = {
  module M = MO_RF(RF)
  module A = Adv(M)

  proc main() : bool = {
    var b : bool; var m : text; var t : text;
    M.init();
    (m, t) <@ A.choose();      
    b <@ M.gver(m, t);        
    return b;                  
  }
}.

local lemma MO_MO_RF_PRF_gtag :
  equiv[MacO(Mac).gtag ~ MO_RF(PRF).gtag :
      ={x} /\ ={key}(MacO, PRF) /\ ={seen}(MacO, MO_RF) ==> ={res} /\ ={seen}(MacO, MO_RF)].
proof.
  proc.
  inline*.
  auto.
qed.


local lemma MO_MO_RF_PRF_gver :
  equiv[MacO(Mac).gver ~ MO_RF(PRF).gver :
        ={x, t} /\ ={key}(MacO, PRF) /\ ={seen}(MacO, MO_RF) ==> ={res}].
proof.
  proc.
  inline*.
  auto.
qed.

local lemma EUCMA_G1_PRF &m :
  Pr[MAC(Mac, Adv).main() @ &m : res] = Pr[G1(PRF).main() @ &m : res].
proof.
byequiv => //; proc.
  call MO_MO_RF_PRF_gver.
  call (_ : ={key}(MacO(Mac), PRF) /\ ={seen}(MacO, MO_RF)).
  by conseq MO_MO_RF_PRF_gtag.
  inline*.
  auto.
qed.

local lemma G1_GRF (RF <: RF{MO_RF, Adv, Adv2RFA}) &m :
  Pr[G1(RF).main() @ &m : res] =
  Pr[GRF(RF, Adv2RFA(Adv)).main() @ &m : res].
proof.
byequiv => //; proc.
inline GRF(RF, Adv2RFA(Adv)).A.main G1(RF).M.init
       Adv2RFA(Adv, RF).MO.init.
wp; sim.
qed.

local lemma EUCMA_G1_TRF &m :
  `|Pr[MAC(Mac, Adv).main() @ &m : res] -
    Pr[G1(TRF).main() @ &m : res]| =
  `|Pr[GRF(PRF, Adv2RFA(Adv)).main() @ &m : res] -
    Pr[GRF(TRF, Adv2RFA(Adv)).main() @ &m : res]|.
proof.
by rewrite (EUCMA_G1_PRF &m) (G1_GRF PRF &m) (G1_GRF TRF &m).
qed.

(* version of mac oracle using TRF, and where gver
   checks TRF.mp for previously inputted texts instead of oracle's own map *)


local module MO_O : MO = {

  proc init() = {
    TRF.init();
  }

  proc gtag(x : text) : text = {
      var t : text;
      t <@ TRF.f(x);
      return t;
  }

  proc gver(x : text, t : text) : bool = {
      var test, v : text;
      var b : bool;
      if (x \in TRF.mp) {
        b <- false;
      }
          else {
        v <$ dtext;
        TRF.mp.[x] <- v;
        b <- v = t;
      }
      return b;
  }  
}.

(* game using MO_O *)

local module G2 = {
  module A = Adv(MO_O)
    
  proc main() : bool = {
    var b : bool; var m : text; var t : text;
    MO_O.init();
    (m, t) <@ A.choose();      
    b <@ MO_O.gver(m, t);        
    return b;                  
  }
}.

    (* we use up to bad reasoning to connect G1(TRF) and G2 *)

local lemma MO_O_gtag_ll : islossless MO_O.gtag.
proof.
proc; islossless; by rewrite dtext_ll.
qed.

search fdom.
print mem_fdom.

local lemma MO_RF_TRF_MO_O_gtag :
  equiv[MO_RF(TRF).gtag ~ MO_O.gtag :
      ={x, TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1} ==> ={res, TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1}].
proof.
  proc.
  seq 1 0 : (={x, TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1}.[x{1}<-x{1}]).
  auto; progress.
  by rewrite fdom_set.
  
  inline*.
  seq 1 1 : (={x, x0, TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1}.[x{1}<-x{1}] /\ x0{1} = x{1}).
  auto.
  if.
  auto.
  auto.
  progress.  
  rewrite fdom_set; rewrite fdom_set.
  auto.
  auto.
  progress.
rewrite fsetP => x.
  rewrite fdom_set.

  rewrite in_fsetU.
  rewrite in_fset1.
  split => [[] // -> | -> //].
  rewrite mem_fdom.
  auto.
qed.

local lemma MO_MO_RF_TRF_gver :
  equiv[MO_RF(TRF).gver ~ MO_O.gver :
        ={x, t, TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1} ==> MO_RF.seen{1} = fdom TRF.mp{1} /\ ={res, TRF.mp}].
proof.
  proc.
  if.
  progress.
  by rewrite -mem_fdom.
  by rewrite mem_fdom.
  auto.
  wp.
  inline*.
  sp.
  rcondt{1} 1.
auto => />.
  rewrite mem_fdom.
  auto.
  auto; progress.
rewrite fsetP => x.
rewrite fdom_set.

  rewrite in_fsetU.
  rewrite in_fset1.
  split.
  progress.
  admit. (* both of these should be true, not sure the lemma needed to rewrite them to make it clear of the contradiction*)
  move => [].
  auto.
  progress.
  admit. (* should have contradiction here, but it's not seeing it *)
  rewrite get_set_eqE.
  auto.
  auto.
qed.



local lemma G1_TRF_G2_main :
  equiv
  [G1(TRF).main ~ G2.main :
   true  ==>
    MO_RF.seen{1} = fdom TRF.mp{1} /\ ={res}].
  proof.
    proc.
    seq 2 2 : (={m, t, TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1} /\ ={glob Adv}).
    call (_ : ={TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1}).
  
    by conseq MO_RF_TRF_MO_O_gtag.
    inline*.
    auto.
    progress.
    by rewrite fdom0.
    seq 1 1 : (={b} /\ ={TRF.mp, glob Adv} /\ MO_RF.seen{1} = fdom TRF.mp{1}).
  
    call MO_MO_RF_TRF_gver.
    auto.
    auto.
qed.

(* Want this to be proving the upper bound of an adversary choosing the output of a TRF *)
local lemma MO_O_gver_clash_up :
  phoare[MO_O.gver : true ==> res] <= (2 ^ text_len)%r.
    proof.    
      proc.    
    conseq (_ : _ ==> v \in fdom TRF.mp).
    move => />.
      progress.
      admit. (* not sure how to complete this proof *)
    
      admit.
  qed.


  (* want this to be bound of correct guess in main *)
local lemma G2_main_clash_ub :
  phoare[G2.main : true ==> res] <=
  (2 ^ text_len)%r.
  proof.
    proc.
    call MO_O_gver_clash_up.
    auto.
qed.


(* want to say that probability of G1 and G2 returning true are both less than 2 / text_len *)
local lemma G1_TRF_G2 &m :
  `|Pr[G1(TRF).main() @ &m : res] - Pr[G2.main() @ &m : res]| <=
  (2 ^ text_len)%r.

  (* in order to use the proof above, I need to convert this to a phoare form, but I'm not sure how to do that. Thought it was

 phoare[G2.main: true ==> res] <= (2 ^ text_len)%r /\ phoare[G1.TRF.main(m) : true ==> res] <= (2 ^ text_len)%r.

    but this was giving me errors *)
  
    (*apply (G2_main_clash_ub).*)
    admit.
qed.



(* uses triangular inequality to show that we have a scheme that where the likelyhood of an adversary suceeding is less than 2 ^ text_len *)
local lemma EUCMA_G2 &m :
  `|Pr[MAC(Mac, Adv).main() @ &m : res] - Pr[G2.main() @ &m : res]| <=
  `|Pr[GRF(PRF, Adv2RFA(Adv)).main() @ &m : res] -
    Pr[GRF(TRF, Adv2RFA(Adv)).main() @ &m : res]| +
  (2 ^ text_len)%r.
  proof.
rewrite
  (ler_trans
   (`|Pr[MAC(Mac, Adv).main() @ &m : res] - Pr[G1(TRF).main() @ &m : res]| +
    `|Pr[G1(TRF).main() @ &m : res] - Pr[G2.main() @ &m : res]|))
  1:ler_dist_add (EUCMA_G1_TRF &m) ler_add2l (G1_TRF_G2 &m).
qed.

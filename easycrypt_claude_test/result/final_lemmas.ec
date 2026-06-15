

(* Helper: bound is nonnegative since denominator is positive *)
lemma bound_pos : 0%r <= (1%r / (2 ^ text_len)%r).
proof.
apply divr_ge0.
  smt().
  smt(gt0_text_len).
qed.

(* The probability that MO_O.gver returns true is at most 1/2^text_len *)
local lemma MO_O_gver_clash_up :
  phoare[MO_O.gver : true ==> res] <= (1%r / (2 ^ text_len)%r).
proof.
proc.
if; [auto => />; apply bound_pos | auto].
qed.

(* The probability that G2.main returns true is at most 1/2^text_len *)
local lemma G2_main_clash_ub :
  phoare[G2.main : true ==> res] <= (1%r / (2 ^ text_len)%r).
proof.
proc.
call MO_O_gver_clash_up.
auto.
qed.

(* The G1(TRF) and G2 games produce the same result *)
local lemma G1_TRF_G2_eq &m :
  Pr[G1(TRF).main() @ &m : res] = Pr[G2.main() @ &m : res].
proof.
byequiv => //.
proc.
inline G1(TRF).M.init MO_O.init.
wp 1 0.
call (_: ={TRF.mp} /\ MO_RF.seen{1} = fdom TRF.mp{1}).
  proc.
  inline MO_RF(TRF).gtag Adv2RFA(Adv, TRF).MO.gtag.
  auto.
auto.
call (_: ={TRF.mp}).
  apply MO_MO_RF_TRF_gver.
auto.
auto.
qed.

(* The difference between G1(TRF) and G2 is bounded by 1/2^text_len *)
local lemma G1_TRF_G2 &m :
  `|Pr[G1(TRF).main() @ &m : res] - Pr[G2.main() @ &m : res]| <=
  (1%r / (2 ^ text_len)%r).
proof.
by rewrite (G1_TRF_G2_eq &m) subrr normr0; smt(ge0_mu mu_bounded).
qed.

(* Overall bound using triangle inequality *)
local lemma EUCMA_G2 &m :
  `|Pr[MAC(Mac, Adv).main() @ &m : res] - Pr[G2.main() @ &m : res]| <=
  `|Pr[GRF(PRF, Adv2RFA(Adv)).main() @ &m : res] -
    Pr[GRF(TRF, Adv2RFA(Adv)).main() @ &m : res]| +
  (1%r / (2 ^ text_len)%r).
proof.
rewrite
  (ler_trans
   (`|Pr[MAC(Mac, Adv).main() @ &m : res] - Pr[G1(TRF).main() @ &m : res]| +
    `|Pr[G1(TRF).main() @ &m : res] - Pr[G2.main() @ &m : res]|))
  1:ler_dist_add (EUCMA_G1_TRF &m) ler_add2l (G1_TRF_G2 &m).
qed.

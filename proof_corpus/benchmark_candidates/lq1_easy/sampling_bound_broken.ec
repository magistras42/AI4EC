require import AllCore Real RealExp.
require import StdOrder.
import RealOrder.

(* If forged fraction >= 1/2, then *)
(* prob all k checks land on valid positions <= (1/2)^k *)

lemma sampling_bound (k : int) (forged_frac : real) :
  0 < k =>
  1%r / 2%r <= forged_frac =>
  forged_frac <= 1%r =>
  (1%r - forged_frac) ^ (k%r) <= (1%r / 2%r) ^ (k%r).
proof.
  move => hk hfrac hle1.
  have hbase : 0%r <= 1%r - forged_frac <= 1%r / 2%r by smt().
  have hbase2 : 0%r <= 1%r / 2%r by smt().
  apply rpow_hmono.
  + smt(lt_fromint le_fromint).
qed.

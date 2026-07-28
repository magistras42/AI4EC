require import Int.

lemma add0_right (x:int): x + 0 = x.
proof.
  smt.
qed.

lemma test_add0_left (x:int): 0 + x = x.
proof.
  rewrite add0_right.
qed.

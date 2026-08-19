require import AllCore.

lemma incomplete (n : int) : n + 0 = n.
proof.
  by rewrite addr0.
qed.


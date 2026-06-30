require import AllCore.

lemma first (n : int) : n + 0 = n.
proof.
  by rewrite addr0.
qed.

lemma target (n : int) : 0 + n = n.
proof.
  by rewrite /addC.
  trivial.
qed.

lemma after (n : int) : n = n.
proof.
  trivial.
qed.

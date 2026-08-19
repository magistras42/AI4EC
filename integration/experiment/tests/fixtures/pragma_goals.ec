require import AllCore.

pragma Goals: printall.

print addC.
search (+).

lemma target (n : int) : 0 + n = n.
proof.
  by rewrite /addC.
  trivial.
qed.

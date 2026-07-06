require import Int.

lemma test_syntax_error (x:int): 0 + x = x.
proof.
  smt.
qed.

require import AllCore.

module Func1 = {
  proc add_1 (x:int) : int = { return x+1; }
}.

lemma triple1: hoare [ Func1.add_1 : x = 1 ==> res = 2].
proof.
  proc.


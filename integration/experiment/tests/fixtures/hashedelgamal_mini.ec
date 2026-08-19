require import AllCore Distr SmtMap DBool FSet.

module type RO = {
  proc * init() : unit

  proc f(x : int) : int
}.

module type ADV = {
  proc * choose() : int
}.

section.

declare module Adv : ADV{RO}.

lemma first : true.
proof.
  trivial.
qed.

lemma target : true.
proof.
  trivial.
qed.

end section.

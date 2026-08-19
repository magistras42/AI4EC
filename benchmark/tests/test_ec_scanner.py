"""Tests for ec_scanner."""

from __future__ import annotations

from benchmark.ec_scanner import scan_proofs, strip_comments


FIXTURE_SIMPLE = """\
require import AllCore.

lemma myfirstlemma (n : int) : n + 0 = n.
proof. by rewrite addr0. qed.

lemma mysecondlemma (n : int) : 0 + n = n.
proof. by rewrite add0r. qed.
"""

FIXTURE_AXIOM = """\
op n : int.
axiom gt0_n : 0 < n.

lemma foo : 0 < n + 1.
proof. smt(gt0_n). qed.
"""

FIXTURE_MULTILINE = """\
local lemma EUCMA_G2 &m :
  `|Pr[MAC(Mac, Adv).main() @ &m : res] - Pr[G2.main() @ &m : res]| <=
  `|Pr[GRF(PRF, Adv2RFA(Adv)).main() @ &m : res] -
    Pr[GRF(TRF, Adv2RFA(Adv)).main() @ &m : res]| +
  (2 ^ text_len)%r.
  proof.
rewrite foo.
qed.
"""

FIXTURE_COMMENT = """\
(* outer comment *)
lemma visible : true.
proof. trivial. qed.

lemma hidden (* inline comment *) : false.
proof. trivial. qed.
"""


def test_strip_comments_nested():
    text = "foo (* outer (* inner *) still *) bar"
    assert strip_comments(text) == "foo  bar"


def test_scan_simple_lemmas():
    decls = scan_proofs(FIXTURE_SIMPLE)
    assert len(decls) == 2
    assert decls[0].name == "myfirstlemma"
    assert decls[0].line == 3
    assert decls[0].kind == "lemma"
    assert "n + 0 = n" in decls[0].signature
    assert decls[1].name == "mysecondlemma"


def test_scan_axiom_and_lemma():
    decls = scan_proofs(FIXTURE_AXIOM)
    assert len(decls) == 2
    assert decls[0].kind == "axiom"
    assert decls[0].name == "gt0_n"
    assert decls[1].kind == "lemma"
    assert decls[1].name == "foo"


def test_scan_multiline_local_lemma():
    decls = scan_proofs(FIXTURE_MULTILINE)
    assert len(decls) == 1
    decl = decls[0]
    assert decl.name == "EUCMA_G2"
    assert decl.kind == "lemma"
    assert "local lemma EUCMA_G2 &m" in decl.signature
    assert decl.signature.endswith("%r.")
    assert "proof." not in decl.signature


def test_scan_with_comments():
    decls = scan_proofs(FIXTURE_COMMENT)
    names = {d.name for d in decls}
    assert names == {"visible", "hidden"}

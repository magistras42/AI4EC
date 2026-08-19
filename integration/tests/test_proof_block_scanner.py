"""Pins the proof-block scanner in ``proof_corpus/scripts/compute_exposure_score.py``.

The corpus ladder had two silent counting defects that survived a documented
fix, a validation writeup and two regenerations, because nothing executed the
parser against a known answer:

* ``proof.`` is optional in EasyCrypt, but extraction was anchored on the
  keyword, so ``lemma foo : P. <tactics> qed.`` was invisible -- 12% of the
  corpus, 42-57% in the repos that never write the opener.
* ``;`` was counted wherever it appeared, so an EasyCrypt list literal
  ``[a; b; c]`` read as three chained tactics.

Both are cheap to state as examples and expensive to notice in an aggregate
score, which is exactly what a test is for. The shapes below are taken from
real corpus files, not invented.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "proof_corpus" / "scripts"))

from compute_exposure_score import (  # noqa: E402
    count_tactics,
    iter_proof_blocks,
    split_statements,
    strip_comments,
)


def scan(source: str) -> list[tuple[str, int]]:
    """(name, depth) per proved obligation, the pair the ladder consumes."""
    return [
        (block["name"], count_tactics(block["body"], block["extra"]))
        for block in iter_proof_blocks(strip_comments(source))
    ]


# --- the four proof shapes EasyCrypt actually uses --------------------------

def test_explicit_proof_opener():
    assert scan("lemma foo : P.\nproof.\n  move => x.\n  by rewrite bar.\nqed.\n") == [("foo", 3)]


def test_proof_opener_omitted():
    """The dominant defect: no `proof.` keyword at all. Real shape, from
    SRI-High-Assurance-Crypto/.../JUtils.ec."""
    source = (
        "lemma modz_sub_carry k i d : 0 <= k < d =>\n"
        "   (k - i) %% d = d + (k - i).\n"
        "  move=> hk hi hd; have [_ <- //]:= euclideUl d (-1) (d + (k - i)) (k-i) _ _.\n"
        "  by rewrite -divz_eq; ring.\n"
        "qed.\n"
    )
    assert scan(source) == [("modz_sub_carry", 5)]


def test_legacy_save_terminator():
    """AutoCrypt's convention. Documented as fixed in AUTOCRYPT_VALIDATION.md
    long before the code actually did it."""
    assert scan("lemma old : P.\nproof.\n  trivial.\nsave.\n") == [("old", 1)]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("lemma inl : P by trivial.\n", [("inl", 1)]),
        ("lemma inl : P by rewrite foo; smt().\n", [("inl", 2)]),
        ("realize size_tolist by admit.\n", [("size_tolist", 1)]),
        ("realize gt0_size.\nproof.\nsmt().\nqed.\n", [("gt0_size", 1)]),
    ],
)
def test_inline_and_realize_forms(source, expected):
    assert scan(source) == expected


# --- bracket depth ----------------------------------------------------------

def test_list_literal_semicolons_are_not_tactics():
    """`[a; b; c; d; e]` is one term. Counting its separators inflated depth
    worst on the Jasmin-adjacent repos, whose extracted arrays are enormous."""
    assert scan("lemma l : P.\nproof.\nrewrite (of_list [a; b; c; d; e]).\nqed.\n") == [("l", 1)]


def test_chained_tactics_still_count():
    """The flip side: a real depth-0 chain is three tactics, not one."""
    assert scan("lemma c : P.\nproof.\nwp; skip; smt().\nqed.\n") == [("c", 3)]


def test_qualified_name_is_not_a_terminator():
    """`RealOrder.lerr_eq` has a dot with identifiers on both sides. The same
    mistake in the harness's counter invented a phantom repaired tactic."""
    source = "lemma q : P.\nproof.\nby rewrite RealOrder.lerr_eq (G2_bad_ub &m).\nqed.\n"
    assert scan(source) == [("q", 2)]


def test_module_body_is_one_statement():
    source = "module M = { proc f() = { x <- 1; y <- 2; } }.\nlemma m : P.\nproof.\ntrivial.\nqed.\n"
    assert scan(source) == [("m", 1)]


# --- declaration vs formula, and things with no proof ------------------------

def test_equiv_declaration_is_a_block_but_equiv_formula_is_not():
    """`equiv foo : ...` declares an obligation; `equiv[ ... ]` is a formula
    inside someone else's statement. Only the first opens a block."""
    assert scan("equiv e : M.f ~ M.g : true ==> true.\nproof.\nsim.\nqed.\n") == [("e", 1)]
    assert scan("lemma f : equiv[ M.f ~ M.g : true ==> true ].\nproof.\nsim.\nqed.\n") == [("f", 1)]


def test_axiom_has_no_proof_to_measure():
    assert scan("axiom a : P.\nlemma b : Q.\nproof.\ntrivial.\nqed.\n") == [("b", 1)]


def test_unproved_declaration_is_abandoned_not_extended():
    """A declaration that never reaches a terminator must not swallow the rest
    of the file into one implausibly deep lemma."""
    source = (
        "lemma dangling : P.\n"
        "op nextthing = 1.\n"
        "lemma real_one : Q.\nproof.\ntrivial.\nqed.\n"
    )
    assert scan(source) == [("real_one", 1)]


def test_commented_out_proof_is_not_counted():
    source = "(* lemma ghost : P. proof. trivial. qed. *)\nlemma live : Q.\nproof.\ntrivial.\nqed.\n"
    assert scan(source) == [("live", 1)]


def test_local_lemma_keeps_its_name():
    assert scan("local lemma loc : P.\nproof.\nsmt().\nqed.\n") == [("loc", 1)]


# --- malformed input --------------------------------------------------------

def test_unbalanced_bracket_costs_one_lemma_not_the_rest_of_the_file():
    """`eval/EasyPIR/puncturableprf.ec:134` really does open `equiv [` and
    never close it. Without the resync at a line-anchored terminator, nothing
    after it is ever at depth 0 again and the file's other 8 proofs vanish.
    This corpus is made of broken proofs, so this is the normal case.

    The lemma *containing* the stray bracket is not recoverable -- its own
    statement never terminates, so it is dropped rather than measured. What
    this pins is that the damage stops there.
    """
    source = (
        "lemma broken : equiv [M.f ~ M.g : true ==> ={res}.\n"
        "proof.\n"
        "byequiv => //.\n"
        "qed.\n"
        "lemma after_it : Q.\n"
        "proof.\n"
        "trivial.\n"
        "qed.\n"
        "lemma and_after_that : R.\n"
        "smt().\n"
        "qed.\n"
    )
    assert [name for name, _ in scan(source)] == ["after_it", "and_after_that"]


def test_split_statements_drops_an_unterminated_tail():
    text = "lemma a : P.\ntrailing prose with no terminator"
    spans = split_statements(text)
    assert [text[s:e].strip() for s, e in spans] == ["lemma a : P"]

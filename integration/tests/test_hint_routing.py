"""Retrieval is steered by the CLASSIFIED error kind (ec_errors wiring).

Before this, every failure got import-shaped evidence. On the real ElGamal
DeepSeek run all three failures were program-logic (`seq`/`rnd`/`wp`) and
every hint surfaced was about the SmtMap->FMap import split -- confident,
long, and unable to fix the failure.
"""

from __future__ import annotations

import pytest

from integration.agent.ec_errors import classify_error
from integration.agent.repair_hints import (
    _tactics_mentioned,
    get_repair_hints_text,
    get_tactic_change_hints_by_release,
)

SEQ_TACTIC = "seq 1 1 : (={glob Adv, choice, x1, x2} /\\ q{1} = q1{2})."
SEQ_ERROR = "[critical] [/x/f.ec: line 751 (0-37)] invalid 'position' parameter"
THEORY_ERROR = "[critical] [/x/f.ec: line 108 (8)] cannot find theory: `SmtMap'"

RANGE = {"source_ec_version": "r2022.04", "target_ec_version": "r2026.06"}


def test_tactics_mentioned_prefers_longer_names():
    known = {"seq", "if", "rcondt", "wp"}
    # Longest first (the more specific lookup), ties broken alphabetically.
    assert _tactics_mentioned("rcondt 1; if; wp.", known) == ["rcondt", "if", "wp"]
    assert _tactics_mentioned("no tactics here", known) == []
    assert _tactics_mentioned("seq 1 1 :", set()) == []


def test_the_elgamal_failures_classify_as_proof_level():
    assert classify_error(SEQ_ERROR).kind == "tactic_error"
    assert classify_error(SEQ_ERROR).is_in_proof
    assert classify_error(THEORY_ERROR).is_pre_proof


@pytest.mark.integration
def test_a_failing_seq_retrieves_an_entry_about_seq():
    """The whole point: tactic failures pull tactic changelog entries."""
    entries, version = get_tactic_change_hints_by_release(
        failing_tactic_text=SEQ_TACTIC, **RANGE
    )
    if not entries:
        pytest.skip("proof_corpus changelog index unavailable")
    assert version
    joined = " ".join(
        f"{e.get('title', '')} {e.get('repair_hint', '')} {e.get('reason', '')}"
        for e in entries
    )
    assert "seq" in joined.lower()


@pytest.mark.integration
def test_routing_suppresses_import_notes_for_proof_level_failures():
    kind = classify_error(SEQ_ERROR).kind
    routed, notes, _ = get_repair_hints_text(
        failing_tactic_text=SEQ_TACTIC, ec_error_text=SEQ_ERROR,
        error_kind=kind, **RANGE,
    )
    if not routed:
        pytest.skip("proof_corpus changelog index unavailable")
    # The library notes exist specifically for import repair.
    assert "IMPORT REPAIR" not in routed
    assert any("suppressed" in n for n in notes)


@pytest.mark.integration
def test_routing_keeps_import_evidence_for_load_failures():
    kind = classify_error(THEORY_ERROR).kind
    text, _, _ = get_repair_hints_text(
        failing_tactic_text="require import SmtMap.", ec_error_text=THEORY_ERROR,
        error_kind=kind, **RANGE,
    )
    if not text:
        pytest.skip("proof_corpus changelog index unavailable")
    assert "IMPORT REPAIR" in text, "a load failure must still get import notes"


@pytest.mark.integration
def test_routing_shrinks_the_prompt_block_for_tactic_failures():
    """Precision, measured: the routed block must not be the bigger one."""
    routed, _, _ = get_repair_hints_text(
        failing_tactic_text=SEQ_TACTIC, ec_error_text=SEQ_ERROR,
        error_kind=classify_error(SEQ_ERROR).kind, **RANGE,
    )
    unrouted, _, _ = get_repair_hints_text(
        failing_tactic_text=SEQ_TACTIC, ec_error_text=SEQ_ERROR,
        error_kind=None, **RANGE,
    )
    if not routed or not unrouted:
        pytest.skip("proof_corpus changelog index unavailable")
    assert len(routed) < len(unrouted)


def test_error_kind_none_preserves_the_original_behaviour():
    """Callers that do not classify must be unaffected."""
    text, notes, _ = get_repair_hints_text(
        failing_tactic_text=SEQ_TACTIC, ec_error_text=SEQ_ERROR,
        error_kind=None, **RANGE,
    )
    assert not any("proof-level" in n for n in notes)


def test_unknown_kind_falls_open_to_identifier_overlap():
    """An unrecognized error must not lose evidence it would otherwise get."""
    text, notes, _ = get_repair_hints_text(
        failing_tactic_text=SEQ_TACTIC, ec_error_text="something unrecognised",
        error_kind=classify_error("something unrecognised").kind, **RANGE,
    )
    assert not any("suppressed" in n for n in notes)

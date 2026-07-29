"""Unit tests for repair-mode session bootstrap.

extract_original_proof_tactics (core.easycrypt.lemma_extract) is pure text
processing and needs no EasyCrypt. run_repair_bootstrap
(workflow.proof_management.repair_intent) drives a live EC session and
needs `eval "$(opam env --switch=easycrypt)"` sourced first, same
prerequisite as tests/test_chain_keep_on_fail.py /
tests/test_chain_replay_dice.py; it is skipped automatically when
EasyCrypt isn't available (core.easycrypt.ec_env.check_ec_available()).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import _pathsetup  # noqa: F401  (repo root on sys.path)

from core.easycrypt.ec_env import check_ec_available
from core.easycrypt.lemma_extract import extract_original_proof_tactics


ROOT = Path(__file__).resolve().parents[1]
BROKEN_UNKNOWN_IDENTIFIER = (
    ROOT.parent / "proof_repair" / "broken_proofs" / "broken_001_unknown_identifier.ec"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ── extract_original_proof_tactics: pure text, no EasyCrypt needed ─────────


def test_extract_original_proof_tactics_proof_qed_form(tmp_path):
    ec_file = _write(tmp_path, "sample.ec", (
        "require import Int.\n"
        "\n"
        "lemma test_add0 (x:int): 0 + x = x.\n"
        "proof.\n"
        "  rewrite nonexistent_lemma.\n"
        "qed.\n"
    ))
    tactics = extract_original_proof_tactics(ec_file, "test_add0")
    assert tactics.strip() == "rewrite nonexistent_lemma."


def test_extract_original_proof_tactics_multi_tactic_body(tmp_path):
    ec_file = _write(tmp_path, "sample.ec", (
        "lemma foo: true.\n"
        "proof.\n"
        "  move=> //.\n"
        "  trivial.\n"
        "qed.\n"
    ))
    tactics = extract_original_proof_tactics(ec_file, "foo")
    assert "move=> //." in tactics
    assert "trivial." in tactics


def test_extract_original_proof_tactics_admit_form_raises(tmp_path):
    ec_file = _write(tmp_path, "sample.ec", (
        "lemma foo: true.\n"
        "admit.\n"
    ))
    with pytest.raises(ValueError, match="nothing to chain-replay"):
        extract_original_proof_tactics(ec_file, "foo")


def test_extract_original_proof_tactics_by_oneliner_raises(tmp_path):
    ec_file = _write(tmp_path, "sample.ec", (
        "lemma foo: true.\n"
        "by trivial.\n"
    ))
    with pytest.raises(ValueError, match="nothing to chain-replay"):
        extract_original_proof_tactics(ec_file, "foo")


def test_extract_original_proof_tactics_lemma_not_found_raises(tmp_path):
    ec_file = _write(tmp_path, "sample.ec", (
        "lemma foo: true.\n"
        "proof.\n"
        "  trivial.\n"
        "qed.\n"
    ))
    with pytest.raises(ValueError, match="not found"):
        extract_original_proof_tactics(ec_file, "bar")


def test_extract_original_proof_tactics_decl_line_mismatch_raises(tmp_path):
    ec_file = _write(tmp_path, "sample.ec", (
        "lemma foo: true.\n"
        "proof.\n"
        "  trivial.\n"
        "qed.\n"
    ))
    with pytest.raises(ValueError, match="does not declare"):
        extract_original_proof_tactics(ec_file, "foo", decl_line=1)


def test_extract_original_proof_tactics_broken_fixture_matches_expected():
    assert BROKEN_UNKNOWN_IDENTIFIER.is_file(), (
        f"expected fixture at {BROKEN_UNKNOWN_IDENTIFIER}"
    )
    tactics = extract_original_proof_tactics(BROKEN_UNKNOWN_IDENTIFIER, "test_add0")
    assert "nonexistent_lemma" in tactics


# ── run_repair_bootstrap: live EC session ───────────────────────────────


ec_ok, ec_msg = check_ec_available()


@pytest.mark.skipif(not ec_ok, reason=f"EasyCrypt unavailable: {ec_msg}")
def test_run_repair_bootstrap_stops_at_first_failure(tmp_path):
    from workflow.proof_management.repair_intent import run_repair_bootstrap
    from workflow.proof_management.repl_session import ReplSessionManager

    target_dir = tmp_path / "repair_target"
    target_dir.mkdir()
    target_file = target_dir / "broken_001_unknown_identifier.ec"
    shutil.copy(BROKEN_UNKNOWN_IDENTIFIER, target_file)

    manager = ReplSessionManager(
        file_path=str(target_file.relative_to(ROOT)) if target_file.is_relative_to(ROOT)
        else str(target_file),
        lemma_name="test_add0",
        include_dir="",
        session_tag="repair_bootstrap_pytest",
        node_id="repair_bootstrap_pytest",
        project_root=ROOT,
    )
    try:
        result = run_repair_bootstrap(
            manager,
            original_proof_file=target_file,
            source_ec_version="r2022.04",
            target_ec_version="r2026.07",
        )
    finally:
        manager.close()
        session_dir = ROOT / manager.session_dir
        shutil.rmtree(session_dir, ignore_errors=True)

    assert result["fully_replayed"] is False
    assert result["accepted_count"] == 0
    assert result["total_count"] == 1
    assert "nonexistent_lemma" in result["failed_tactic"]

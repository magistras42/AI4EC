"""Round-trip test: workflow.repair.write_bootstrap_resume_capsule ->
workflow.proof_node_resume.load_resume_capsule.

Confirms the manifest/directory shape produced for a chain-replayed
bootstrap prefix (workflow/repair.py, phase 1/2 seeding) actually matches
what the existing resume-capsule loader expects -- no live EasyCrypt
needed, this only exercises the two pure-filesystem/JSON functions.
"""
from __future__ import annotations

from pathlib import Path

import _pathsetup  # noqa: F401  (repo root on sys.path)

from workflow.proof_node_resume import load_resume_capsule
from workflow.repair import write_bootstrap_resume_capsule


def _fake_session_dir(tmp_path: Path, tactics: list[str]) -> Path:
    session_dir = tmp_path / ".ec_session_fake"
    session_dir.mkdir()
    (session_dir / "history.ec").write_text(
        "\n".join(tactics) + "\n", encoding="utf-8",
    )
    return session_dir


def test_write_bootstrap_resume_capsule_round_trips(tmp_path):
    tactics = ["move=> x.", "rewrite addC.", "trivial."]
    session_dir = _fake_session_dir(tmp_path, tactics)
    out_dir = tmp_path / "capsule"

    manifest_path = write_bootstrap_resume_capsule(
        session_dir=session_dir,
        out_dir=out_dir,
        target_file="eval/examples/sample.ec",
        lemma="foo",
        include_dir="",
    )
    assert manifest_path == out_dir / "resume.json"
    assert manifest_path.is_file()
    assert (out_dir / "history.ec").is_file()

    capsule = load_resume_capsule(manifest_path)
    assert capsule.target_file == "eval/examples/sample.ec"
    assert capsule.lemma == "foo"
    assert capsule.replay_prefix == tactics
    assert capsule.tactic_count == len(tactics)
    assert capsule.recorded_tactic_count == len(tactics)
    assert capsule.route_family == "repair_bootstrap"
    assert "chain_replay_bootstrap" in capsule.reasons


def test_write_bootstrap_resume_capsule_loads_from_directory(tmp_path):
    """load_resume_capsule accepts either the manifest file or its
    containing directory (`resume.json` is the default filename it looks
    for) -- exercise the directory form since that's what a caller passing
    `resume_capsules=[str(out_dir)]` would use."""
    session_dir = _fake_session_dir(tmp_path, ["smt."])
    out_dir = tmp_path / "capsule2"
    write_bootstrap_resume_capsule(
        session_dir=session_dir,
        out_dir=out_dir,
        target_file="eval/examples/sample.ec",
        lemma="bar",
        include_dir="",
    )
    capsule = load_resume_capsule(out_dir)
    assert capsule.replay_prefix == ["smt."]


def test_write_bootstrap_resume_capsule_empty_history(tmp_path):
    """An empty/missing history.ec (bootstrap failed on the very first
    tactic) still produces a loadable capsule with zero replay tactics --
    load_resume_capsule must not choke on it."""
    session_dir = tmp_path / ".ec_session_empty"
    session_dir.mkdir()  # no history.ec written at all
    out_dir = tmp_path / "capsule3"
    write_bootstrap_resume_capsule(
        session_dir=session_dir,
        out_dir=out_dir,
        target_file="eval/examples/sample.ec",
        lemma="baz",
        include_dir="",
    )
    capsule = load_resume_capsule(out_dir)
    assert capsule.replay_prefix == []
    assert capsule.tactic_count == 0

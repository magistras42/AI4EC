"""Proof-repair mode: two-phase orchestration entry point.

Opt-in and fully separate from ``workflow/orchestrator.py``, which this
module never imports for its ``run()``/``main()`` behavior and never
modifies -- the default from-scratch pipeline is unaffected by this file's
existence.

A repair run:

  1. Chain-replays the target lemma's ORIGINAL (outdated) proof against the
     currently-installed EasyCrypt until the first tactic fails
     (``workflow/proof_management/repair_intent.py``). If the whole proof
     still replays, report success immediately -- most of the original
     proof is assumed still valid, so a clean full replay costs zero LLM
     turns.
  2. Phase 1: a single-node, tightly-budgeted "localized patch" attempt
     (``tree_initial_provers=1``, capped rounds/depth) using the EXISTING
     tree-search machinery (``workflow.orchestrator.run_prover``, imported
     unmodified), seeded from a resume capsule that captures the
     chain-replayed good prefix.
  3. Phase 2 (only if phase 1 doesn't close the goal): a normal multi-root
     tree-search fallback, still seeded from the SAME bootstrap capsule
     (never from anything phase 1 explored) -- the chain-replayed prefix is
     proof-repair's one guaranteed-good asset and is never re-derived.

CLI:
    python -m workflow.repair --file <path> --lemma <name> \\
        --repair-source-file <path> \\
        --source-ec-version r2022.04 --target-ec-version r2026.07
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from workflow.orchestrator import _duplicate_lemma_error, run_prover
from workflow.progress import status, error as perror
from workflow.proof_management.repair_intent import run_repair_bootstrap
from workflow.proof_management.repl_session import ReplSessionManager, session_dir_path
from workflow.proof_node_resume import CAPSULE_KIND, CAPSULE_VERSION
from workflow.schemas.config import RunConfig


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("workflow.repair")


def write_bootstrap_resume_capsule(
    *,
    session_dir: Path,
    out_dir: Path,
    target_file: str,
    lemma: str,
    include_dir: str,
) -> Path:
    """Write a resume-capsule directory for the chain-replayed good prefix.

    Matches the manifest/directory contract ``workflow.proof_node_resume
    .load_resume_capsule`` expects: a ``resume.json`` manifest plus a copy
    of ``history.ec`` (the loader tolerates every other auxiliary file --
    ``latest_workspace_view.json``, ``route_memory.json``, etc. -- being
    absent). ``replay.tactic_count`` is computed with the exact same
    "non-blank stripped line" rule ``_history_tactics``/
    ``read_committed_tactics`` both use, so the manifest's own count can
    never disagree with what the loader recomputes from the copied file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    history_src = session_dir / "history.ec"
    history_text = (
        history_src.read_text(encoding="utf-8") if history_src.is_file() else ""
    )
    (out_dir / "history.ec").write_text(history_text, encoding="utf-8")
    tactic_count = len([line for line in history_text.splitlines() if line.strip()])

    manifest = {
        "kind": CAPSULE_KIND,
        "capsule_version": CAPSULE_VERSION,
        "target": {"file": target_file, "lemma": lemma, "include_dir": include_dir},
        "source": {"commit": "", "session_name": session_dir.name},
        "replay": {
            "history_file": "history.ec",
            "current_goal_hash": "",
            "current_goal_preview": "",
            "current_goal_file": "",
            "resume_prefix_count": tactic_count,
            "tactic_count": tactic_count,
        },
        "score": {
            "value": 1.0,
            "reasons": ["chain_replay_bootstrap"],
            "route_family": {"family": "repair_bootstrap"},
        },
        "lineage": {},
        "handoff": {
            "notes": [
                "Seeded from a repair-mode chain-replay of the target's "
                "original proof against the current EasyCrypt install: the "
                "prefix is a previously human-verified proof, not "
                "agent-explored search.",
            ],
            "recent_tactics": [],
            "verified_route_options": [],
            "route_event_facts": [],
            "resume_diversity": {},
        },
    }
    manifest_path = out_dir / "resume.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def _bootstrap(config: RunConfig, run_dir: Path) -> dict[str, Any]:
    if not config.repair_source_file:
        raise ValueError("RunConfig.repair_source_file is required when repair_mode=True")
    if not config.source_ec_version or not config.target_ec_version:
        raise ValueError(
            "RunConfig.source_ec_version and target_ec_version are required "
            "when repair_mode=True"
        )

    manager = ReplSessionManager(
        file_path=config.file,
        lemma_name=config.lemma,
        include_dir=config.include_dir,
        session_tag=f"repair_bootstrap_{config.lemma}_{int(time.time())}",
        node_id="repair_bootstrap",
        project_root=_PROJECT_ROOT,
    )
    try:
        bootstrap_result = run_repair_bootstrap(
            manager,
            original_proof_file=_PROJECT_ROOT / config.repair_source_file,
            source_ec_version=config.source_ec_version,
            target_ec_version=config.target_ec_version,
        )
        bootstrap_result["session_dir"] = str(
            session_dir_path(manager.session_dir, manager.project_root)
        )
    finally:
        manager.close()

    (run_dir / "bootstrap_result.json").write_text(
        json.dumps(bootstrap_result, indent=2), encoding="utf-8",
    )
    return bootstrap_result


def run(config: RunConfig) -> dict[str, Any]:
    """Execute one proof-repair run. See module docstring for the phases."""
    if not config.repair_mode:
        raise ValueError("workflow.repair.run() requires RunConfig.repair_mode=True")

    dup_error = _duplicate_lemma_error(config)
    if dup_error:
        perror("Repair", dup_error)
        return {"error": dup_error}

    from core.easycrypt.ec_env import check_ec_available
    ec_ok, ec_msg = check_ec_available()
    if not ec_ok:
        perror("Repair", f"EasyCrypt precheck failed: {ec_msg}")
        return {"error": ec_msg}

    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_name = f"{date_str}_{config.lemma}_repair"
    run_dir = _PROJECT_ROOT / config.output_dir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    config.save(run_dir / "config.json")

    run_start = time.time()
    status("Repair", f"Starting repair run for {config.file}:{config.lemma}")
    status("Repair", f"Run directory: {run_dir}")

    bootstrap_result = _bootstrap(config, run_dir)
    status(
        "Repair",
        f"Bootstrap replayed {bootstrap_result['accepted_count']}/"
        f"{bootstrap_result['total_count']} original tactics "
        f"(fully_replayed={bootstrap_result['fully_replayed']})",
    )

    if bootstrap_result["fully_replayed"]:
        status("Repair", "Original proof still replays verbatim -- nothing to repair.", "\033[32m")
        summary = {
            "phase": "bootstrap_only",
            "target": {"file": config.file, "lemma": config.lemma},
            "bootstrap": bootstrap_result,
            "proved": True,
            "total_elapsed_minutes": round((time.time() - run_start) / 60, 1),
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    capsule_dir = run_dir / "bootstrap_capsule"
    capsule_path = write_bootstrap_resume_capsule(
        session_dir=Path(bootstrap_result["session_dir"]),
        out_dir=capsule_dir,
        target_file=config.file,
        lemma=config.lemma,
        include_dir=config.include_dir,
    )
    status("Repair", f"Bootstrap resume capsule: {capsule_path}")

    # ── Phase 1: localized, hint-guided patch attempt ──────────────────
    # Existing NodeSupervisor/tree machinery, unmodified -- tree_initial_provers=1
    # already gives a single-root, no-branch-exploration run; the tight
    # round/depth budget bounds it to "try the local patch, then give up"
    # rather than paying full tree-search costs for what's usually a local fix.
    phase1_dir = run_dir / "phase1"
    phase1_dir.mkdir(parents=True, exist_ok=True)
    phase1_config = RunConfig(
        file=config.file,
        lemma=config.lemma,
        include_dir=config.include_dir,
        output_dir=config.output_dir,
        eval_mode=config.eval_mode,
        surface_profile=config.surface_profile,
        record_proof_bank=config.record_proof_bank,
        resume_capsules=[str(capsule_path)],
        prover=dataclasses.replace(
            config.prover,
            tree_initial_provers=1,
            max_rounds=config.prover.repair_phase1_max_rounds,
            tree_max_depth=config.prover.repair_phase1_tree_max_depth,
        ),
    )
    status("Repair", "Phase 1: localized single-node repair attempt")
    phase1_result = run_prover(phase1_config, phase1_dir)

    if phase1_result.proved and phase1_result.ec_file_verified:
        status("Repair", "Phase 1 closed the goal.", "\033[32m")
        summary = {
            "phase": "phase1",
            "target": {"file": config.file, "lemma": config.lemma},
            "bootstrap": bootstrap_result,
            "proved": True,
            "session_id": phase1_result.session_id,
            "total_elapsed_minutes": round((time.time() - run_start) / 60, 1),
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    # ── Phase 2: full tree-search fallback ──────────────────────────────
    # Still seeded from the bootstrap capsule (the chain-replayed prefix),
    # never from anything phase 1 explored -- normal multi-root ProverConfig
    # defaults otherwise, unchanged from the from-scratch pipeline.
    status("Repair", "Phase 1 did not close the goal; falling back to full tree search")
    phase2_dir = run_dir / "phase2"
    phase2_dir.mkdir(parents=True, exist_ok=True)
    phase2_config = RunConfig(
        file=config.file,
        lemma=config.lemma,
        include_dir=config.include_dir,
        output_dir=config.output_dir,
        eval_mode=config.eval_mode,
        surface_profile=config.surface_profile,
        record_proof_bank=config.record_proof_bank,
        resume_capsules=[str(capsule_path)],
        prover=dataclasses.replace(config.prover),
    )
    phase2_result = run_prover(phase2_config, phase2_dir)

    proved = bool(phase2_result.proved and phase2_result.ec_file_verified)
    status(
        "Repair",
        "Phase 2 closed the goal." if proved else "Phase 2 did not close the goal.",
        "\033[32m" if proved else "\033[31m",
    )
    summary = {
        "phase": "phase2",
        "target": {"file": config.file, "lemma": config.lemma},
        "bootstrap": bootstrap_result,
        "proved": proved,
        "session_id": phase2_result.session_id,
        "total_elapsed_minutes": round((time.time() - run_start) / 60, 1),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Run proof-repair mode: replay an existing proof against "
                     "the current EasyCrypt, then patch just the failing step(s)."
    )
    parser.add_argument("--config", type=Path, help="Path to a saved RunConfig json "
                        "(repair_mode/repair_source_file/*_ec_version already set)")
    parser.add_argument("--file", type=str, help="EC file path (relative to project root)")
    parser.add_argument("--lemma", type=str, help="Lemma name")
    parser.add_argument("--include-dir", type=str, default="")
    parser.add_argument("--repair-source-file", type=str,
                        help="Path to a copy of --file with the lemma's ORIGINAL "
                             "(outdated, intact) proof body still present")
    parser.add_argument("--source-ec-version", type=str,
                        help="EasyCrypt release tag the original proof was written against")
    parser.add_argument("--target-ec-version", type=str,
                        help="EasyCrypt release tag to repair the proof for")
    parser.add_argument("--prover-model", type=str, default=None)
    parser.add_argument("--prover-effort", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.config:
        config = RunConfig.load(args.config)
        config.repair_mode = True
    elif args.file and args.lemma and args.repair_source_file and args.source_ec_version and args.target_ec_version:
        config = RunConfig(
            file=args.file,
            lemma=args.lemma,
            include_dir=args.include_dir or "",
            repair_mode=True,
            repair_source_file=args.repair_source_file,
            source_ec_version=args.source_ec_version,
            target_ec_version=args.target_ec_version,
        )
    else:
        parser.error(
            "Either --config, or all of --file/--lemma/--repair-source-file/"
            "--source-ec-version/--target-ec-version, are required"
        )

    if args.prover_model:
        config.prover.model = args.prover_model
    if args.prover_effort:
        config.prover.effort = args.prover_effort
    if args.output_dir:
        config.output_dir = args.output_dir

    run(config)


if __name__ == "__main__":
    main()

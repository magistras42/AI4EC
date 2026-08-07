"""Shared setup for the experiment notebooks.

Three notebooks now drive the same harness against different specs
(`elgamal-changelog-repair`, `joy-tactic-repair`, `lq1-broken-repair`). The
ElGamal notebook grew its preflight, config and preview inline, and copying
that into two more notebooks would guarantee they drift: the traps recorded in
`docs/PROOF_REPAIR_NEXT_HANDOFF.md` §7 are mostly *notebook* traps -- a stale
kernel, a reused `output_dir`, a preflight that passes while the run cannot
start -- and each copy is a fresh chance to reintroduce them.

So the parts that must not drift live here, and the notebooks keep only what a
reader should actually see and change: the spec, the provider, the budget.

Nothing here starts a run or answers the paid-usage confirmation. That stays in
the notebook, in front of the human, per AGENTS.md.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

# --- repo root on the path --------------------------------------------------
# Duplicated from the notebooks' first cell rather than imported, because this
# module cannot be imported until it has run.
_ROOT = Path(os.getcwd())
if _ROOT.name == "notebooks":
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def project_root() -> Path:
    return _ROOT


def load_dotenv(root: Path | None = None) -> dict[str, bool]:
    """Load `.env` into the environment. Returns which API keys are present.

    `setdefault`, so a variable already exported in the shell that launched
    Jupyter wins -- which is also the trap: if a stale key is exported, `.env`
    will not override it and the run fails at the first call with a confusing
    auth error.
    """
    root = root or _ROOT
    env_path = root / ".env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return {
        "DEEPSEEK_API_KEY": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def verify_working_tree_is_live() -> list[str]:
    """Assert the kernel is running the code on disk, not a cached copy.

    New modules are invisible to a running kernel regardless of `%autoreload`,
    and runs C-F of the ElGamal experiment all executed a 22-hour-old `loop.py`
    while the operator believed otherwise. Every check here fails on stale
    code rather than passing quietly.
    """
    from openai import APIConnectionError, AuthenticationError

    from integration.agent.config import AgentConfig
    from integration.agent.ec_program import parse_program_block
    from integration.agent.goal_diff import format_state_diff  # noqa: F401
    from integration.agent.llm import TRANSPORT_ERRORS
    from integration.agent.prompt import (
        _seq_position_bullets,
        format_broken_tactic_repair,
        format_replayed_prefix_note,
    )
    from integration.agent.proof_file import ProofFile

    checks: list[str] = []
    fixture = (
        _ROOT / "integration" / "tests" / "fixtures" / "elgamal_equiv_block.txt"
    )
    pair = parse_program_block(fixture.read_text(encoding="utf-8"))
    txt = " ".join(_seq_position_bullets(pair))
    lad = format_broken_tactic_repair(
        "seq 4 3 : (inv).", "[critical] invalid `position' parameter"
    )

    def check(name: str, ok: bool) -> None:
        if not ok:
            checks.append(name)

    check("history_steps == 15", AgentConfig().history_steps == 15)
    check("ec_program parses 13/12", (len(pair.left), len(pair.right)) == (13, 12))
    check("seq counts are a ceiling", "can never exceed" in txt)
    check("seq wording not a false range", "N must be 0..13" not in txt)
    check("ladder cites no absent counts",
          "Re-read the instruction counts" not in lad)
    check("asymmetric-cut note", "ASYMMETRIC" in lad)
    check("transport errors retryable", APIConnectionError in TRANSPORT_ERRORS)
    check("auth still fails fast", AuthenticationError not in TRANSPORT_ERRORS)
    check("prefix note present", "already verified" in format_replayed_prefix_note(18))
    check("ProofFile has a prefix clamp",
          "protected_prefix" in ProofFile.__dataclass_fields__)
    return checks


def preflight(config: Any) -> dict[str, Any]:
    """Everything that must be true before a run can start."""
    from integration.agent.ec_version import detect_target_version
    from integration.experiment.__main__ import _embeddings_endpoint_status

    ok, detail = _embeddings_endpoint_status(config)
    target = detect_target_version(config.easycrypt_bin)
    return {
        "embeddings_ok": ok,
        "embeddings_detail": detail,
        "easycrypt_found": config.easycrypt_bin.exists(),
        "easycrypt_bin": config.easycrypt_bin,
        "ec_version": target.version,
        "ec_version_method": target.method,
        "ec_version_confidence": target.confidence,
    }


def build_config(
    *,
    spec_name: str,
    provider: str,
    model: str,
    thinking: str,
    reasoning_effort: str | None,
    embed_model: str,
    trials: int,
    adaptive_multiplier: float,
    min_steps: int,
    stuck_limit: int,
    top_k: int,
    llm_max_tokens: int,
    llm_timeout_s: int,
    cost_limit_usd: float | None,
    data_dir: Path,
    output_dir: Path | None = None,
):
    """Build the agent + experiment config for one run.

    A fresh `output_dir` is minted on every call, which is why the notebooks
    must run this cell before the run cell: reusing a stale one overwrites a
    previous run in place, and has already mixed two runs into one events file.
    """
    from integration.agent.config import (
        LLM_PROVIDER_ANTHROPIC,
        LLM_PROVIDER_DEEPSEEK,
        PAID_LLM_PROVIDERS,
        AgentConfig,
        apply_anthropic_provider,
        apply_deepseek_provider,
    )
    from integration.experiment.config import ExperimentConfig

    agent = AgentConfig(
        top_k=top_k,
        llm_max_tokens=llm_max_tokens,
        embed_model=embed_model,
        lm_studio_timeout=llm_timeout_s,
    )
    if provider == LLM_PROVIDER_DEEPSEEK:
        apply_deepseek_provider(
            agent, model=model, thinking=thinking, reasoning_effort=reasoning_effort
        )
    elif provider == LLM_PROVIDER_ANTHROPIC:
        apply_anthropic_provider(
            agent, model=model, thinking=thinking, reasoning_effort=reasoning_effort
        )
    else:
        agent.llm_model = model  # local: free, no cap needed

    exp = ExperimentConfig(
        spec_name=spec_name,
        trials=trials,
        stuck_limit=stuck_limit,
        data_dir=Path(data_dir),
        agent=agent,
        sort_by_difficulty=True,
        adaptive_steps_multiplier=adaptive_multiplier,
        min_adaptive_steps=min_steps,
        cost_limit_usd=cost_limit_usd if provider in PAID_LLM_PROVIDERS else None,
    )
    if output_dir is not None:
        exp.output_dir = Path(output_dir)
    return exp.with_agent_defaults()


def preview_cases(exp_config, data_dir: Path, corpus_cls, max_trials: int) -> list:
    """Rows of (index, name, tactic_lines, step_budget), shortest first."""
    corpus = corpus_cls(
        data_dir=Path(data_dir), sandbox_dir=exp_config.output_dir / "sandboxes"
    )
    cases = sorted(corpus.load_cases(), key=lambda c: len(c.tactic_lines))
    return [
        (i, c.name, len(c.tactic_lines), exp_config.steps_for_case(len(c.tactic_lines)))
        for i, c in enumerate(cases)
    ]


def build_spec(exp_config, data_dir: Path, changelog_hints: bool = True):
    """Resolve the spec and point its sandboxes at this run's output dir.

    `changelog_hints=False` is only meaningful for a replay_bootstrap spec;
    for every other mode there is no bootstrap hint block to suppress, so the
    flag is accepted and ignored rather than silently pretending to be an arm
    of an A/B that does not exist for that spec.
    """
    from integration.experiment.__main__ import _build_spec, _with_sandbox_dir

    spec = _with_sandbox_dir(
        _build_spec(exp_config.spec_name, Path(data_dir)),
        Path(data_dir),
        exp_config.output_dir / "sandboxes",
    )
    if spec.replay_bootstrap is not None and not changelog_hints:
        spec = replace(
            spec,
            replay_bootstrap=replace(spec.replay_bootstrap, changelog_hints=False),
        )
    return spec


def summarise(result) -> list[str]:
    """Outcome lines, separating free replays from real model repairs.

    The distinction matters more than any other number in these runs: across
    ten ElGamal runs, 93% of COMPLETE trials required no agent work at all, so
    a bare "N complete" headline mostly reports that EasyCrypt still compiles
    the corpus.
    """
    lines = [
        f"{'#':<3} {'Name':<24} {'Outcome':<10} {'Steps':>5} {'Calls':>6} "
        f"{'Cost':>10}  Route",
        "-" * 82,
    ]
    free = model = 0
    for t in result.trial_results:
        calls = t.token_usage.calls
        cost = (t.estimated_cost or {}).get("usd", 0.0)
        if t.reason == "COMPLETE" and calls == 0:
            route, free = "replay (free)", free + 1
        elif t.reason == "COMPLETE":
            route, model = "MODEL REPAIR", model + 1
        else:
            route = "model, unsolved" if calls else "—"
        lines.append(
            f"{t.trial_id:<3} {t.name:<24} {t.reason:<10} {t.steps:>5} "
            f"{calls:>6} {cost:>10.6f}  {route}"
        )
    attempted = sum(1 for t in result.trial_results if t.token_usage.calls > 0)
    lines += [
        "",
        f"Zero-LLM replays      : {free}",
        f"Repaired BY THE MODEL : {model} of {attempted} attempted",
    ]
    return lines


def retained_tactics(result) -> list[str]:
    """`net_tactics_vs_bootstrap` per trial, where the log recorded it.

    The only measure that caught the agent dismantling its own verified
    prefix: on run 20260807T031032Z two of three trials finished holding FEWER
    tactics than the bootstrap handed them (`G2_G3` 13->12,
    `INDCPA_HEG_G1` 21->9, `G1_G2_eq` 18->2) while accepted/failed/no-op
    counts all looked ordinary. Empty for specs that have no replayed prefix.
    """
    import json

    out: list[str] = []
    trials_dir = result.output_dir / "trials"
    if not trials_dir.is_dir():
        return out
    for trial in sorted(trials_dir.iterdir()):
        log = trial / "agent_log.json"
        if not log.is_file():
            continue
        events = json.loads(log.read_text(encoding="utf-8")).get("events", [])
        finish = next(
            (e for e in reversed(events) if e.get("event") == "finish"), None
        )
        if not finish or finish.get("net_tactics_vs_bootstrap") is None:
            continue
        net = finish["net_tactics_vs_bootstrap"]
        flag = "   <-- DESTROYED VERIFIED WORK" if net < 0 else ""
        out.append(
            f"  {trial.name}: bootstrap {finish['replayed_prefix']} -> "
            f"retained {finish['tactics_retained']}  net {net:+d}{flag}"
        )
    return out

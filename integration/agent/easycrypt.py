"""Subprocess wrappers around the patched EasyCrypt `llm` subcommand."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import NO_ACTIVE_PROOF, PREMISES_SEPARATOR, AgentConfig


@dataclass(frozen=True)
class LlmResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GoalAndPremises:
    goal: str
    premises: str


def run_llm(args: list[str], config: AgentConfig) -> LlmResult:
    cmd = [str(config.easycrypt_bin), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=config.easycrypt_timeout,
    )
    return LlmResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def fetch_goal(file_path: Path, cursor_upto: int, config: AgentConfig) -> LlmResult:
    return run_llm(["llm", "-upto", str(cursor_upto), str(file_path)], config)


def fetch_goal_and_premises(
    file_path: Path, cursor_upto: int, config: AgentConfig
) -> LlmResult:
    return run_llm(
        ["llm", "-upto", str(cursor_upto), "-premises", str(file_path)],
        config,
    )


def validate_file(file_path: Path, config: AgentConfig) -> LlmResult:
    return run_llm(["llm", "-lastgoals", str(file_path)], config)


def split_goal_and_premises(stdout: str) -> GoalAndPremises:
    marker = PREMISES_SEPARATOR + "\n"
    if marker in stdout:
        goal, premises = stdout.split(marker, 1)
        return GoalAndPremises(goal=goal.strip(), premises=premises)
    return GoalAndPremises(goal=stdout.strip(), premises="")


def is_no_active_proof(goal_text: str) -> bool:
    return goal_text.strip() == NO_ACTIVE_PROOF


def has_open_goals(stdout: str) -> bool:
    return "Current goal" in stdout

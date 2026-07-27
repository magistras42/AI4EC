"""Tests for persisting experiment CLI flags."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from integration.agent.config import (
    DEFAULT_DEEPSEEK_MODEL,
    LLM_PROVIDER_DEEPSEEK,
    AgentConfig,
    apply_deepseek_provider,
)
from integration.experiment.run_flags import (
    RUN_FLAGS_FILENAME,
    build_run_flags_payload,
    write_run_flags,
)
from integration.experiment.runner import ExperimentResult


def test_write_run_flags_records_argv_and_resolved(tmp_path: Path):
    args = Namespace(
        command="run",
        spec="joy-informal-repair",
        trials=7,
        stuck_limit=20,
        max_steps=50,
        seed=123,
        data_dir=Path("data"),
        output_dir=tmp_path / "out",
        deepseek=True,
        llm_model="deepseek-v4-flash",
        embed_model="nomic-embed",
        verbose=False,
    )
    agent = apply_deepseek_provider(AgentConfig(max_steps=50), model="deepseek-v4-flash")
    agent.embed_model = "nomic-embed"
    path = write_run_flags(
        tmp_path / "out",
        args=args,
        argv=["run", "--deepseek", "--trials", "7"],
        agent=agent,
    )
    assert path.name == RUN_FLAGS_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["argv"] == ["run", "--deepseek", "--trials", "7"]
    assert payload["flags"]["trials"] == 7
    assert payload["flags"]["deepseek"] is True
    assert payload["flags"]["data_dir"] == "data"
    assert payload["resolved"]["llm_provider"] == LLM_PROVIDER_DEEPSEEK
    assert payload["resolved"]["llm_model"] == DEFAULT_DEEPSEEK_MODEL
    assert payload["resolved"]["embed_model"] == "nomic-embed"


def test_build_run_flags_payload_handles_null_argv(tmp_path: Path):
    args = Namespace(command="run", trials=1)
    payload = build_run_flags_payload(
        args=args,
        argv=None,
        agent=AgentConfig(),
        output_dir=tmp_path,
    )
    assert payload["argv"] is None
    assert payload["flags"]["trials"] == 1


@patch("integration.experiment.__main__.confirm_deepseek_usage", return_value=True)
@patch("integration.experiment.__main__.run_experiment")
def test_cli_writes_run_flags_before_experiment(mock_run, _confirm, monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    out = tmp_path / "out"
    mock_run.return_value = ExperimentResult(
        spec_name="joy-tactic-repair",
        mode="mutation",
        trials_requested=2,
        trials_run=0,
        trials_skipped=0,
        successes=0,
        stuck=0,
        max_steps=0,
        errors=0,
        output_dir=out,
        trial_results=[],
    )
    from integration.experiment.__main__ import main

    argv = [
        "run",
        "--deepseek",
        "--trials",
        "2",
        "--max-steps",
        "9",
        "--data-dir",
        str(tmp_path),
        "--output-dir",
        str(out),
        "--embed-model",
        "mock-embed",
    ]
    assert main(argv) == 0
    flags_path = out / RUN_FLAGS_FILENAME
    assert flags_path.exists()
    payload = json.loads(flags_path.read_text(encoding="utf-8"))
    assert payload["argv"] == argv
    assert payload["flags"]["trials"] == 2
    assert payload["flags"]["max_steps"] == 9
    assert payload["flags"]["deepseek"] is True
    assert payload["resolved"]["llm_provider"] == LLM_PROVIDER_DEEPSEEK
    assert payload["resolved"]["embed_model"] == "mock-embed"


@patch("integration.experiment.__main__.confirm_deepseek_usage", return_value=False)
@patch("integration.experiment.__main__.run_experiment")
def test_cli_does_not_write_run_flags_when_deepseek_declined(
    mock_run, _confirm, monkeypatch, tmp_path
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    out = tmp_path / "out"
    from integration.experiment.__main__ import main

    code = main(
        [
            "run",
            "--deepseek",
            "--data-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ]
    )
    assert code == 2
    mock_run.assert_not_called()
    assert not (out / RUN_FLAGS_FILENAME).exists()

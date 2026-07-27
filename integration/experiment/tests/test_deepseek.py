"""Tests for DeepSeek provider config and human confirmation gate."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from integration.agent.config import (
    DEFAULT_DEEPSEEK_MODEL,
    LLM_PROVIDER_DEEPSEEK,
    AgentConfig,
    action_response_format_mode,
    apply_deepseek_provider,
    chat_client_kwargs,
)
from integration.experiment.deepseek_confirm import (
    CONFIRMATION_PHRASE,
    confirm_deepseek_usage,
    format_deepseek_warning,
)


def test_apply_deepseek_provider_sets_defaults():
    config = AgentConfig(llm_model="some-local-model")
    apply_deepseek_provider(config)
    assert config.llm_provider == LLM_PROVIDER_DEEPSEEK
    assert config.llm_model == DEFAULT_DEEPSEEK_MODEL
    assert config.llm_thinking == "disabled"


def test_apply_deepseek_provider_respects_explicit_model():
    config = AgentConfig()
    apply_deepseek_provider(config, model="deepseek-v4-pro")
    assert config.llm_model == "deepseek-v4-pro"


def test_chat_completion_kwargs_disables_thinking_by_default(monkeypatch):
    from integration.agent.config import chat_completion_kwargs

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(AgentConfig())
    kwargs = chat_completion_kwargs(config)
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kwargs


def test_chat_completion_kwargs_enabled_thinking_with_effort(monkeypatch):
    from integration.agent.config import chat_completion_kwargs

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(
        AgentConfig(),
        thinking="enabled",
        reasoning_effort="high",
    )
    kwargs = chat_completion_kwargs(config)
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "high"


def test_json_object_off_by_default_for_deepseek():
    config = apply_deepseek_provider(AgentConfig())
    assert action_response_format_mode(config) is None


def test_json_object_opt_in_for_deepseek():
    config = apply_deepseek_provider(AgentConfig())
    config.llm_json_mode = True
    assert action_response_format_mode(config) == "json_object"


def test_json_mode_can_be_disabled_explicitly():
    config = apply_deepseek_provider(AgentConfig())
    config.llm_json_mode = False
    assert action_response_format_mode(config) is None


def test_lm_studio_keeps_json_schema_opt_in():
    assert action_response_format_mode(AgentConfig()) is None
    assert action_response_format_mode(AgentConfig(llm_json_mode=True)) == "json_schema"


def test_resolve_thinking_adaptive_uses_failure_window():
    from integration.agent.config import resolve_thinking_for_step

    config = apply_deepseek_provider(AgentConfig(), thinking="adaptive")
    config.thinking_failure_window = 5
    assert resolve_thinking_for_step(config, []) == "disabled"
    assert (
        resolve_thinking_for_step(
            config, [{"outcome": "accepted"}, {"outcome": "search"}]
        )
        == "disabled"
    )
    assert (
        resolve_thinking_for_step(
            config,
            [
                {"outcome": "accepted"},
                {"outcome": "failed"},
                {"outcome": "accepted"},
            ],
        )
        == "enabled"
    )
    # Outside the window: ignored
    config.thinking_failure_window = 1
    assert (
        resolve_thinking_for_step(
            config, [{"outcome": "failed"}, {"outcome": "accepted"}]
        )
        == "disabled"
    )


def test_chat_completion_kwargs_adaptive_override(monkeypatch):
    from integration.agent.config import chat_completion_kwargs

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(
        AgentConfig(), thinking="adaptive", reasoning_effort="high"
    )
    off = chat_completion_kwargs(config, thinking="disabled")
    assert off["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in off
    on = chat_completion_kwargs(config, thinking="enabled")
    assert on["extra_body"] == {"thinking": {"type": "enabled"}}
    assert on["reasoning_effort"] == "high"


def test_deepseek_decide_omits_response_format_by_default(monkeypatch):
    from integration.agent.llm import LlmClient

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(AgentConfig())
    client = LlmClient(config)
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response('{"action": "tactic", "tactic": "trivial."}')

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    decision = client.decide("goal")

    assert "response_format" not in captured
    assert decision.action.kind == "tactic"
    assert "JSON" in captured["messages"][0]["content"]


def test_deepseek_decide_sends_json_object_when_opted_in(monkeypatch):
    from integration.agent.llm import LlmClient

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(AgentConfig())
    config.llm_json_mode = True
    client = LlmClient(config)
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response('{"action": "tactic", "tactic": "trivial."}')

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    decision = client.decide("goal")

    assert captured["response_format"] == {"type": "json_object"}
    assert decision.action.kind == "tactic"


def test_cli_rejects_both_json_mode_flags(tmp_path, capsys):
    from integration.experiment.__main__ import main

    code = main(
        [
            "run",
            "--llm-json-mode",
            "--no-llm-json-mode",
            "--data-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 1
    assert "mutually exclusive" in capsys.readouterr().err


def _fake_response(content: str):
    class _Message:
        def __init__(self) -> None:
            self.content = content
            self.reasoning_content = None

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()
            self.finish_reason = "stop"

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice()]
            self.usage = None

    return _Response()


def test_chat_client_kwargs_deepseek_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = apply_deepseek_provider(AgentConfig())
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        chat_client_kwargs(config)


def test_chat_client_kwargs_deepseek_uses_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    config = apply_deepseek_provider(AgentConfig())
    kwargs = chat_client_kwargs(config)
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["base_url"] == "https://api.deepseek.com"


def test_format_deepseek_warning_includes_iteration_counts():
    config = apply_deepseek_provider(AgentConfig(max_steps=50))
    text = format_deepseek_warning(config=config, trials=10, informal=True)
    assert "Trials (iterations)      : 10" in text
    assert "Max agent steps / trial  : 50" in text
    assert "10 × 50 = 500" in text
    assert "Thinking                 : disabled" in text
    assert "Informal writer" in text
    assert "MUST NEVER" in text
    assert CONFIRMATION_PHRASE in text


def test_confirm_deepseek_usage_accepts_only_exact_yes():
    config = apply_deepseek_provider(AgentConfig())
    assert confirm_deepseek_usage(
        config=config,
        trials=3,
        stdin=io.StringIO("YES\n"),
        stdout=io.StringIO(),
    )
    assert not confirm_deepseek_usage(
        config=config,
        trials=3,
        stdin=io.StringIO("yes\n"),
        stdout=io.StringIO(),
    )
    assert not confirm_deepseek_usage(
        config=config,
        trials=3,
        stdin=io.StringIO("\n"),
        stdout=io.StringIO(),
    )


@patch("integration.experiment.__main__.confirm_deepseek_usage", return_value=False)
@patch("integration.experiment.__main__.run_experiment")
def test_cli_deepseek_aborts_when_user_declines(mock_run, _confirm, monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from integration.experiment.__main__ import main

    code = main(
        [
            "run",
            "--deepseek",
            "--trials",
            "2",
            "--data-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    mock_run.assert_not_called()


@patch("integration.experiment.__main__.confirm_deepseek_usage", return_value=True)
@patch("integration.experiment.__main__.run_experiment")
def test_cli_deepseek_runs_after_confirmation(mock_run, _confirm, monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from integration.experiment.runner import ExperimentResult

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
        output_dir=tmp_path / "out",
        trial_results=[],
    )
    from integration.experiment.__main__ import main

    code = main(
        [
            "run",
            "--deepseek",
            "--trials",
            "2",
            "--data-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    mock_run.assert_called_once()
    agent = mock_run.call_args.args[1].agent
    assert agent.llm_provider == LLM_PROVIDER_DEEPSEEK
    assert agent.llm_model == DEFAULT_DEEPSEEK_MODEL


def test_cli_deepseek_requires_api_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from integration.experiment.__main__ import main

    code = main(
        [
            "run",
            "--deepseek",
            "--data-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err

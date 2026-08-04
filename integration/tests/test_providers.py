"""Provider selection, request shaping, and the Anthropic backend.

The Anthropic path is exercised against a stub client rather than the real
API: these assertions are about REQUEST SHAPE (which is what a 400 would
punish us for) and reply normalization, neither of which needs a paid call.
Per AGENTS.md the live run is a human's to launch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from integration.agent.config import (
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_DEEPSEEK,
    LLM_PROVIDER_LM_STUDIO,
    AgentConfig,
    action_response_format_mode,
    anthropic_request_kwargs,
    apply_anthropic_provider,
    apply_deepseek_provider,
    chat_client_kwargs,
    configured_thinking,
    resolve_thinking_for_step,
    validate_anthropic_thinking_effort,
    validate_reasoning_effort,
)
from integration.agent.llm import (
    AnthropicBackend,
    ChatReply,
    LlmClient,
    LlmFormatError,
    _anthropic_output_format,
    _anthropic_reply,
    build_backend,
)
from integration.agent.usage import TokenUsage


# --- config / provider selection -------------------------------------------


def test_apply_anthropic_provider_defaults_to_opus_5_adaptive_high():
    config = apply_anthropic_provider(AgentConfig())
    assert config.llm_provider == LLM_PROVIDER_ANTHROPIC
    assert config.llm_model == "claude-opus-5"
    assert config.llm_thinking == "adaptive"
    assert config.llm_reasoning_effort == "high"


def test_apply_anthropic_provider_respects_explicit_choices():
    config = apply_anthropic_provider(
        AgentConfig(), model="claude-sonnet-5", thinking="disabled", reasoning_effort="low"
    )
    assert config.llm_model == "claude-sonnet-5"
    assert config.llm_thinking == "disabled"
    assert config.llm_reasoning_effort == "low"


def test_anthropic_does_not_use_the_openai_sdk():
    config = apply_anthropic_provider(AgentConfig())
    with pytest.raises(ValueError, match="does not use the OpenAI SDK"):
        chat_client_kwargs(config)


def test_build_backend_selects_by_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert type(build_backend(AgentConfig())).__name__ == "OpenAICompatBackend"
    assert (
        type(build_backend(apply_deepseek_provider(AgentConfig()))).__name__
        == "OpenAICompatBackend"
    )
    assert (
        type(build_backend(apply_anthropic_provider(AgentConfig()))).__name__
        == "AnthropicBackend"
    )


# --- request shaping --------------------------------------------------------


def test_anthropic_request_omits_sampling_parameters():
    """Opus 5 removed temperature/top_p/top_k; sending any is a 400."""
    config = apply_anthropic_provider(AgentConfig())
    config.llm_temperature = 0.9  # must be ignored for this provider
    kwargs = anthropic_request_kwargs(config)
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


def test_anthropic_request_sends_adaptive_thinking_with_summary():
    kwargs = anthropic_request_kwargs(apply_anthropic_provider(AgentConfig()))
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "high"}


def test_anthropic_request_maps_enabled_onto_adaptive():
    config = apply_anthropic_provider(AgentConfig(), thinking="enabled")
    assert anthropic_request_kwargs(config)["thinking"]["type"] == "adaptive"


def test_anthropic_request_can_disable_thinking_at_low_effort():
    config = apply_anthropic_provider(
        AgentConfig(), thinking="disabled", reasoning_effort="medium"
    )
    assert anthropic_request_kwargs(config)["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_disabled_thinking_above_high_effort_is_rejected(effort):
    """Claude 400s on this pairing; catch it before a trial is spent."""
    with pytest.raises(ValueError, match="thinking='disabled'"):
        validate_anthropic_thinking_effort("disabled", effort)
    config = apply_anthropic_provider(
        AgentConfig(), thinking="disabled", reasoning_effort=effort
    )
    with pytest.raises(ValueError):
        anthropic_request_kwargs(config)


def test_reasoning_effort_ranges_differ_by_provider():
    validate_reasoning_effort(LLM_PROVIDER_ANTHROPIC, "xhigh")
    validate_reasoning_effort(LLM_PROVIDER_DEEPSEEK, "max")
    with pytest.raises(ValueError):
        validate_reasoning_effort(LLM_PROVIDER_DEEPSEEK, "xhigh")
    with pytest.raises(ValueError):
        validate_reasoning_effort(LLM_PROVIDER_ANTHROPIC, "turbo")


def test_anthropic_adaptive_is_not_collapsed_by_the_harness_window():
    """Claude scales thinking itself; the trajectory heuristic must not fire."""
    config = apply_anthropic_provider(AgentConfig(), thinking="adaptive")
    failures = [{"outcome": "failed"}]
    assert resolve_thinking_for_step(config, failures) == "adaptive"
    assert resolve_thinking_for_step(config, []) == "adaptive"


def test_deepseek_adaptive_still_resolves_per_step(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(AgentConfig(), thinking="adaptive")
    assert resolve_thinking_for_step(config, [{"outcome": "failed"}]) == "enabled"
    assert resolve_thinking_for_step(config, [{"outcome": "accepted"}]) == "disabled"


def test_configured_thinking_defaults_differ_by_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    deepseek = AgentConfig(llm_provider=LLM_PROVIDER_DEEPSEEK)
    anthropic = AgentConfig(llm_provider=LLM_PROVIDER_ANTHROPIC)
    assert configured_thinking(deepseek) == "disabled"
    assert configured_thinking(anthropic) == "adaptive"


def test_json_mode_selects_a_provider_appropriate_format():
    for provider, expected in (
        (LLM_PROVIDER_ANTHROPIC, "anthropic_json_schema"),
        (LLM_PROVIDER_DEEPSEEK, "json_object"),
        (LLM_PROVIDER_LM_STUDIO, "json_schema"),
    ):
        config = AgentConfig(llm_provider=provider, llm_json_mode=True)
        assert action_response_format_mode(config) == expected
    # Off by default on every provider.
    assert action_response_format_mode(AgentConfig()) is None


def test_anthropic_output_format_wraps_the_action_schema():
    fmt = _anthropic_output_format("anthropic_json_schema")
    assert fmt["format"]["type"] == "json_schema"
    assert fmt["format"]["schema"]["required"] == [
        "action", "tactic", "name", "query", "count"
    ]
    assert _anthropic_output_format("json_object") is None


# --- reply normalization ----------------------------------------------------


def _block(**fields):
    return SimpleNamespace(**fields)


def test_anthropic_reply_separates_text_from_thinking():
    message = SimpleNamespace(
        content=[
            _block(type="thinking", thinking="The goal is an equality."),
            _block(type="text", text='{"action": "tactic", "tactic": "by ring."}'),
        ],
        stop_reason="end_turn",
    )
    reply = _anthropic_reply(message)
    assert reply.text == '{"action": "tactic", "tactic": "by ring."}'
    assert reply.thought == "The goal is an equality."
    assert reply.reasoning_texts == ("The goal is an equality.",)


def test_anthropic_reply_concatenates_multiple_text_blocks():
    message = SimpleNamespace(
        content=[_block(type="text", text="a"), _block(type="text", text="b")],
        stop_reason="end_turn",
    )
    assert _anthropic_reply(message).text == "ab"


def test_anthropic_reply_never_promotes_thinking_into_text():
    """A model that 'answered' only in thinking has not committed to an action."""
    message = SimpleNamespace(
        content=[_block(type="thinking", thinking='{"action": "tactic"}')],
        stop_reason="end_turn",
    )
    assert _anthropic_reply(message).text == ""


def test_anthropic_refusal_becomes_a_recoverable_format_error():
    message = SimpleNamespace(
        content=[],
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="nope"),
    )
    with pytest.raises(LlmFormatError, match="declined"):
        _anthropic_reply(message)


# --- backend behaviour against a stub client --------------------------------


class _StubStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class _StubMessages:
    def __init__(self, message):
        self._message = message
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _StubStream(self._message)


def _stub_anthropic_backend(config, message):
    backend = AnthropicBackend.__new__(AnthropicBackend)
    backend.config = config
    backend._model = config.llm_model
    backend._client = SimpleNamespace(messages=_StubMessages(message))
    return backend


def _tactic_message(usage=None):
    return SimpleNamespace(
        content=[
            _block(type="thinking", thinking="ring should close it"),
            _block(type="text", text='{"action": "tactic", "tactic": "by ring."}'),
        ],
        stop_reason="end_turn",
        usage=usage,
    )


def test_anthropic_backend_streams_and_sends_system_separately():
    config = apply_anthropic_provider(AgentConfig())
    backend = _stub_anthropic_backend(config, _tactic_message())
    client = LlmClient(config, backend=backend)

    decision = client.decide("## Current goal\nx = x")

    assert decision.action.kind == "tactic"
    assert decision.action.tactic == "by ring."
    assert decision.thought == "ring should close it"

    sent = backend._client.messages.calls[0]
    # System prompt is a top-level parameter, not a message.
    assert "system" in sent
    assert [m["role"] for m in sent["messages"]] == ["user"]
    assert sent["model"] == "claude-opus-5"
    assert sent["max_tokens"] == config.llm_max_tokens
    assert "temperature" not in sent


def test_anthropic_backend_records_usage_in_the_shared_tracker():
    usage = TokenUsage()
    config = apply_anthropic_provider(AgentConfig())
    config.usage_tracker = usage
    message = _tactic_message(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=50,
        )
    )
    LlmClient(config, backend=_stub_anthropic_backend(config, message)).decide("g")

    assert usage.calls == 1
    # prompt_tokens must span uncached + read + write, not just input_tokens.
    assert usage.prompt_tokens == 950
    assert usage.cached_prompt_tokens == 800
    assert usage.cache_write_prompt_tokens == 50
    assert usage.cache_miss_prompt_tokens == 100
    assert usage.completion_tokens == 20


def test_anthropic_backend_sends_structured_output_when_json_mode_on():
    config = apply_anthropic_provider(AgentConfig())
    config.llm_json_mode = True
    backend = _stub_anthropic_backend(config, _tactic_message())
    LlmClient(config, backend=backend).decide("g")

    output_config = backend._client.messages.calls[0]["output_config"]
    assert output_config["format"]["type"] == "json_schema"
    # Effort must survive alongside the injected format.
    assert output_config["effort"] == "high"


def test_anthropic_backend_requires_an_explicit_model():
    config = apply_anthropic_provider(AgentConfig())
    config.llm_model = None
    backend = _stub_anthropic_backend(config, _tactic_message())
    with pytest.raises(RuntimeError, match="no Anthropic model configured"):
        backend.resolve_model()


def test_llm_client_is_provider_agnostic_over_a_fake_backend():
    """The loop only ever sees decide(); a backend is all a provider needs."""

    class FakeBackend:
        def resolve_model(self):
            return "fake-model"

        def complete(self, *, system, user, thinking, response_format_mode):
            return ChatReply(text='{"action": "undo", "count": "2"}')

    decision = LlmClient(AgentConfig(), backend=FakeBackend()).decide("goal")
    assert decision.action.kind == "undo"
    assert decision.action.count == 2


# --- malformed provider responses must be recoverable ------------------------
# A provider can return HTTP 200 with no usable payload. Before this, that
# raised TypeError from `response.choices[0]`, which is not an LlmFormatError,
# so loop.py's catch-all ended the whole trial with ExitReason.LLM_ERROR --
# observed discarding 31 and 70 iterations of real progress mid-experiment.


class _NoCallStub:
    def __init__(self, response):
        self._response = response
        self.completions = self
        self.chat = self

    def create(self, **kwargs):
        return self._response


def _openai_backend_returning(response, monkeypatch):
    from integration.agent.llm import OpenAICompatBackend

    backend = OpenAICompatBackend.__new__(OpenAICompatBackend)
    backend.config = AgentConfig(llm_model="stub")
    backend._model = "stub"
    backend._client = _NoCallStub(response)
    return backend


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(choices=None, id="resp_1"),
        SimpleNamespace(choices=[], id="resp_2"),
        SimpleNamespace(choices=[SimpleNamespace(message=None, finish_reason="length")], id="r3"),
    ],
    ids=["choices_is_none", "choices_is_empty", "message_is_none"],
)
def test_malformed_response_raises_recoverable_format_error(response, monkeypatch):
    backend = _openai_backend_returning(response, monkeypatch)
    with pytest.raises(LlmFormatError):
        backend.complete(system=None, user="goal", thinking=None,
                         response_format_mode=None)


def test_malformed_response_is_recoverable_not_terminal(monkeypatch):
    """LlmFormatError is what loop.py retries; TypeError is what kills a trial."""
    backend = _openai_backend_returning(
        SimpleNamespace(choices=None, id="x"), monkeypatch
    )
    try:
        backend.complete(system=None, user="g", thinking=None, response_format_mode=None)
    except LlmFormatError:
        pass
    except TypeError:  # pragma: no cover - the regression
        pytest.fail("must not surface as TypeError; loop.py ends the trial on it")


# --- budget-exhausted replies -------------------------------------------------
# The dominant real-world failure with thinking on: the model spends the entire
# max_tokens budget reasoning and returns NO visible content
# (finish_reason='length'). Measured across four DeepSeek trials, 79 of 85
# format errors were exactly this. Retrying with thinking off puts the budget
# back on the answer instead of wasting the call and a stuck-counter step.


class _ScriptedBackend:
    """Returns queued replies and records the thinking mode of each call."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.thinking_seen: list[str | None] = []

    def resolve_model(self):
        return "scripted"

    def complete(self, *, system, user, thinking, response_format_mode):
        self.thinking_seen.append(thinking)
        return self._replies.pop(0)


def test_truncated_empty_reply_retries_with_thinking_disabled():
    backend = _ScriptedBackend([
        ChatReply(text="", finish_reason="length"),                       # budget gone
        ChatReply(text='{"action": "tactic", "tactic": "by ring."}',      # retry works
                  finish_reason="stop"),
    ])
    decision = LlmClient(AgentConfig(), backend=backend).decide("g", thinking="enabled")

    assert decision.action.tactic == "by ring."
    assert backend.thinking_seen == ["enabled", "disabled"], (
        "the retry must turn thinking off, or the budget is spent the same way again"
    )


def test_anthropic_max_tokens_stop_reason_also_triggers_the_retry():
    backend = _ScriptedBackend([
        ChatReply(text="", finish_reason="max_tokens"),
        ChatReply(text='{"action": "undo", "count": "1"}', finish_reason="end_turn"),
    ])
    decision = LlmClient(AgentConfig(), backend=backend).decide("g", thinking="adaptive")
    assert decision.action.kind == "undo"
    assert backend.thinking_seen[-1] == "disabled"


def test_no_retry_when_thinking_was_already_disabled():
    """Retrying with the same settings would just burn another paid call."""
    backend = _ScriptedBackend([ChatReply(text="", finish_reason="length")])
    with pytest.raises(LlmFormatError, match="raise --llm-max-tokens"):
        LlmClient(AgentConfig(), backend=backend).decide("g", thinking="disabled")
    assert backend.thinking_seen == ["disabled"], "must not retry"


def test_empty_reply_that_is_not_truncated_does_not_retry():
    """A genuinely malformed reply is a formatting problem, not a budget one."""
    backend = _ScriptedBackend([ChatReply(text="", finish_reason="stop")])
    with pytest.raises(LlmFormatError, match="escape"):
        LlmClient(AgentConfig(), backend=backend).decide("g", thinking="enabled")
    assert backend.thinking_seen == ["enabled"]


def test_truncation_message_does_not_blame_backslashes():
    """The old advice was actively misleading: nothing was emitted to format."""
    backend = _ScriptedBackend([ChatReply(text="", finish_reason="length")])
    with pytest.raises(LlmFormatError) as exc:
        LlmClient(AgentConfig(), backend=backend).decide("g", thinking="disabled")
    assert "output-token cap" in str(exc.value)

r"""Provider-agnostic chat completions and tool-response parsing.

Three chat providers are supported, selected by ``AgentConfig.llm_provider``:

===============  ==========================  ====================================
Provider         Transport                   Typical use
===============  ==========================  ====================================
``lm_studio``    OpenAI SDK, local server    Local open-weight models (Gemma, ...)
``deepseek``     OpenAI SDK, DeepSeek API    Paid hosted, OpenAI-compatible
``anthropic``    Anthropic SDK, Messages API Paid hosted, Claude (Opus 5 default)
===============  ==========================  ====================================

The split is deliberately drawn at *transport only*. Everything downstream of
a reply -- action-JSON extraction, the backslash-escape repair that EasyCrypt's
``/\`` forces on us, tactic-line salvage for prover-style models, the
retrospective parser -- is provider-independent and lives once in this module.
A backend's whole job is to turn (system prompt, user prompt, thinking mode)
into a :class:`ChatReply`, so adding a fourth provider means implementing two
methods rather than duplicating the parsing stack.

The two hosted providers are NOT interchangeable at the wire level: Anthropic
is not OpenAI-compatible (different client, different message shape, different
thinking/effort parameters, and no ``temperature`` at all on Opus 5), which is
why this is a backend protocol rather than a base-URL swap.

Embeddings are unaffected by any of this and always go to LM Studio -- see
``integration/agent/embeddings.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, Union

from openai import OpenAI

from .config import (
    LLM_PROVIDER_ANTHROPIC,
    action_response_format_mode,
    anthropic_client_kwargs,
    anthropic_request_kwargs,
    chat_client_kwargs,
    chat_completion_kwargs,
)

if TYPE_CHECKING:
    from .config import AgentConfig


@dataclass(frozen=True)
class LlmDecision:
    """Parsed LLM output plus optional hidden reasoning from thinking models."""

    action: AgentAction
    thought: str | None = None
    content: str = ""


@dataclass(frozen=True)
class TacticAction:
    kind: Literal["tactic"] = "tactic"
    tactic: str = ""


@dataclass(frozen=True)
class UndoAction:
    kind: Literal["undo"] = "undo"
    # Number of trailing tactics to remove; defaults to 1.
    count: int = 1


@dataclass(frozen=True)
class LookupLemmaAction:
    kind: Literal["lookup_lemma"] = "lookup_lemma"
    name: str = ""


@dataclass(frozen=True)
class SearchLemmasAction:
    kind: Literal["search_lemmas"] = "search_lemmas"
    query: str = ""
    # semantic | substring | prefix | exact — carried in JSON ``name`` field.
    mode: str = "semantic"


AgentAction = Union[
    TacticAction,
    UndoAction,
    LookupLemmaAction,
    SearchLemmasAction,
]


class LlmFormatError(ValueError):
    """Malformed or empty LLM reply that the agent loop can recover from."""


class LlmProviderError(LlmFormatError):
    """The provider produced NO usable reply at all.

    Distinct from its parent, which means "the model answered, but the answer
    was not parseable". Here there is nothing to parse: the response carried no
    choices, no message, or was cut off mid-reasoning by the output-token cap
    before any answer was written.

    The distinction is load-bearing for the agent loop. A malformed answer is
    evidence the model is floundering and should advance the stuck counter; a
    provider failure is infrastructure and says nothing about the proof. Across
    four DeepSeek trials, 79 of 85 recoverable errors were this class, and
    counting them as "unproductive iterations" made budget exhaustion look
    exactly like a model that could not make progress.

    Subclasses ``LlmFormatError`` so existing ``except LlmFormatError`` handlers
    keep working unchanged.
    """

# Some OpenAI-compatible servers (e.g. LM Studio) reject the generic
# `{"type": "json_object"}` response_format and only support a concrete
# `json_schema`. This schema covers all action shapes in one object
# (rather than a oneOf/anyOf, which some local model runtimes don't enforce
# well) so a model that isn't a strong native tool-caller is still nudged
# into emitting well-formed, parseable JSON.
_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["tactic", "undo", "lookup_lemma", "search_lemmas"],
        },
        "tactic": {"type": "string"},
        "name": {"type": "string"},
        "query": {"type": "string"},
        # Undo only: positive integer as a string ("" or "1" = undo one step).
        "count": {"type": "string"},
    },
    "required": ["action", "tactic", "name", "query", "count"],
    "additionalProperties": False,
}

_ACTION_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "agent_action",
        "strict": True,
        "schema": _ACTION_SCHEMA,
    },
}

def action_response_format_spec() -> str:
    """Human-readable JSON template matching the API ``response_format`` schema."""
    return """\
Required response shape (all fields required; use empty strings for unused ones):

{"action": "tactic", "tactic": "by rewrite addr0.", "name": "", "query": "", "count": ""}
{"action": "undo", "tactic": "", "name": "", "query": "", "count": ""}
{"action": "undo", "tactic": "", "name": "", "query": "", "count": "3"}
{"action": "lookup_lemma", "tactic": "", "name": "my_lemma", "query": "", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "", "query": "commutative integer addition", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "substring", "query": "lnM", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "prefix", "query": "ln", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "exact", "query": "lnM", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "substring", "query": "theory:RField exprM", "count": ""}
"""

_TACTIC_KEYWORDS = (
    "by",
    "rewrite",
    "apply",
    "exact",
    "assumption",
    "smt",
    "trivial",
    "split",
    "left",
    "right",
    "ring",
    "field",
    "algebra",
    "congr",
    "progress",
    "proc",
    "wp",
    "sp",
    "skip",
    "rnd",
    "call",
    "inline",
    "auto",
    "while",
    "unroll",
    "rcondf",
    "rcondt",
    "seq",
    "if",
    "move",
    "subst",
    "case",
    "elim",
    "exists",
    "have",
    "pose",
    "conseq",
    "byphoare",
    "byequiv",
    "qed",
)

def _json_system_prompt() -> str:
    return (
        "You are an EasyCrypt tactic selector. "
        "Use your hidden reasoning channel for brief step-by-step analysis. "
        "Always finish by putting ONLY the final JSON action object in the "
        "visible assistant reply, with no markdown fences or extra text. "
        "Never put the JSON answer only in reasoning or commentary. "
        "If you find yourself repeating the same observation about the goal more "
        "than once, stop immediately, commit to the best available tactic, and "
        "emit the JSON action. The JSON object MUST appear in the visible reply, "
        "not only in your reasoning or thinking."
    )


def _strict_json_system_prompt() -> str:
    # DeepSeek's json_object mode requires the word "json" and an example of the
    # wanted shape in the prompt, so keep both here.
    return (
        "You are an EasyCrypt tactic selector. "
        "Put your final answer only in the visible reply as one json "
        "object with the keys action, tactic, name and query. Unused keys "
        "must be empty strings. Do not put the answer in reasoning or "
        "commentary, and do not wrap it in markdown fences.\n\n"
        "EXAMPLE JSON OUTPUT:\n"
        '{"action": "tactic", "tactic": "by rewrite addr0.", '
        '"name": "", "query": ""}'
    )


_TACTIC_LINE_RE = re.compile(
    r"(?:(?:" + "|".join(_TACTIC_KEYWORDS) + r")\b[^`\n]{0,200}\.)",
    re.IGNORECASE,
)


_JSON_OBJECT_RESPONSE_FORMAT = {"type": "json_object"}


def _record_usage(config: AgentConfig, response, *, anthropic: bool) -> None:
    """Book one call against the per-trial tracker and the run-level budget.

    Both are updated from the same response in one place, so a provider
    backend cannot accidentally report tokens while escaping the spend cap.
    """
    tracker = config.usage_tracker
    budget = config.spend_budget
    if anthropic:
        if tracker is not None:
            tracker.record_anthropic(response)
        if budget is not None:
            budget.record_anthropic(response)
        return
    if tracker is not None:
        tracker.record(response)
    if budget is not None:
        budget.record_openai(response)


@dataclass(frozen=True)
class ChatReply:
    """One chat completion, normalized across providers.

    ``text`` is the VISIBLE reply only. ``thought`` is hidden reasoning
    (DeepSeek's ``reasoning_content``, Claude's summarized thinking blocks,
    LM Studio's various spellings). Keeping them apart matters: the action
    JSON is only ever accepted from ``text``, because a model that "answers"
    solely inside its reasoning channel has not actually committed to an
    action -- see ``_action_text_from_message``, which reaches into reasoning
    only as a last-resort salvage.
    """

    text: str
    thought: str | None = None
    finish_reason: str | None = None
    # Every distinct reasoning-channel string the provider returned, in
    # priority order. Used only by the last-resort salvage in
    # ``_action_text_from_reply`` for models that put the JSON action in their
    # reasoning instead of the visible reply.
    reasoning_texts: tuple[str, ...] = ()


class ChatBackend(Protocol):
    """Transport for one chat provider.

    Implementations own client construction, request shaping, and usage
    accounting; they own no parsing.
    """

    def resolve_model(self) -> str:
        """Model id in use, discovering one from the server if unset."""

    def complete(
        self,
        *,
        system: str | None,
        user: str,
        thinking: str | None,
        response_format_mode: str | None,
    ) -> ChatReply:
        """Run one completion and normalize the reply."""


class OpenAICompatBackend:
    """LM Studio and DeepSeek, both over the OpenAI SDK.

    They differ only in base URL, credentials, and the per-call thinking
    parameters that :func:`chat_completion_kwargs` builds, so one backend
    covers both rather than duplicating the request/parse path.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = OpenAI(**chat_client_kwargs(config))
        self._model = config.llm_model

    def resolve_model(self) -> str:
        if self._model:
            return self._model
        models = self._client.models.list()
        if not models.data:
            raise RuntimeError("No LLM model available from the configured chat provider")
        self._model = models.data[0].id
        return self._model

    def _create(self, **kwargs):
        response = self._client.chat.completions.create(**kwargs)
        _record_usage(self.config, response, anthropic=False)
        return response

    def complete(
        self,
        *,
        system: str | None,
        user: str,
        thinking: str | None = None,
        response_format_mode: str | None = None,
    ) -> ChatReply:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        kwargs: dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": self.config.llm_temperature,
            "max_tokens": self.config.llm_max_tokens,
            **chat_completion_kwargs(self.config, thinking=thinking),
        }
        response_format = _openai_response_format(response_format_mode)
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            response = self._create(**kwargs)
        except Exception:
            # A server that rejects response_format still gets one plain
            # attempt rather than failing the whole trial.
            if response_format is None:
                raise
            kwargs.pop("response_format", None)
            response = self._create(**kwargs)

        # A provider can return a well-formed HTTP 200 whose body carries no
        # choices at all (DeepSeek does this intermittently, and more often
        # with thinking on). `response.choices[0]` then raises TypeError, which
        # is NOT an LlmFormatError, so loop.py's catch-all turns a transient
        # hiccup into a terminal LLM_ERROR -- observed killing trials after 31
        # and 70 productive iterations. Treat it as the malformed reply it is,
        # so the loop records a format error and retries the step.
        choices = getattr(response, "choices", None)
        if not choices:
            finish = getattr(response, "id", None)
            raise LlmProviderError(
                "Provider returned no choices in the response "
                f"(id={finish!r}). Reply again with one short JSON action."
            )
        choice = choices[0]
        if getattr(choice, "message", None) is None:
            raise LlmProviderError(
                "Provider returned a choice with no message. "
                "Reply again with one short JSON action."
            )
        reasoning = tuple(_iter_reasoning_candidates(choice.message))
        return ChatReply(
            text=_response_content(choice.message),
            thought=reasoning[0] if reasoning else None,
            finish_reason=getattr(choice, "finish_reason", None),
            reasoning_texts=reasoning,
        )


class AnthropicBackend:
    """Claude via the Anthropic Messages API.

    Not an OpenAI base-URL swap: the system prompt is a top-level parameter
    rather than a message, thinking/effort are first-class request fields, and
    ``temperature`` is rejected outright on Opus 5. Requests are STREAMED and
    reassembled with ``get_final_message()`` -- with adaptive thinking at high
    effort a single tactic-selection step can take minutes, and a non-streaming
    call at this ``max_tokens`` risks an idle-connection timeout that would
    lose the whole step.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install
            raise RuntimeError(
                "the anthropic package is required for --provider anthropic; "
                "install it with: pip install -r integration/agent/requirements-agent.txt"
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(**anthropic_client_kwargs(config))
        self._model = config.llm_model

    def resolve_model(self) -> str:
        # No model discovery: unlike a local LM Studio server (which usually
        # has exactly one model loaded), picking an arbitrary entry from
        # /v1/models here would silently choose the caller's spend rate.
        if not self._model:
            raise RuntimeError(
                "no Anthropic model configured; pass --llm-model or use the "
                "default applied by apply_anthropic_provider"
            )
        return self._model

    def complete(
        self,
        *,
        system: str | None,
        user: str,
        thinking: str | None = None,
        response_format_mode: str | None = None,
    ) -> ChatReply:
        kwargs: dict[str, Any] = {
            "model": self.resolve_model(),
            "max_tokens": self.config.llm_max_tokens,
            "messages": [{"role": "user", "content": user}],
            **anthropic_request_kwargs(self.config, thinking=thinking),
        }
        if system:
            kwargs["system"] = system
        output_format = _anthropic_output_format(response_format_mode)
        if output_format is not None:
            kwargs.setdefault("output_config", {}).update(output_format)

        try:
            message = self._stream_final(kwargs)
        except Exception:
            if output_format is None:
                raise
            # Same degrade-gracefully rule as the OpenAI path: a rejected
            # structured-output schema costs one retry, not the trial.
            kwargs["output_config"].pop("format", None)
            message = self._stream_final(kwargs)

        return _anthropic_reply(message)

    def _stream_final(self, kwargs: dict[str, Any]):
        with self._client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
        _record_usage(self.config, message, anthropic=True)
        return message


def build_backend(config: AgentConfig) -> ChatBackend:
    """Select the transport for the configured provider."""
    if config.llm_provider == LLM_PROVIDER_ANTHROPIC:
        return AnthropicBackend(config)
    return OpenAICompatBackend(config)


class LlmClient:
    """Provider-agnostic solver client.

    Public surface (``decide`` / ``retrospect``) is unchanged from when this
    was LM-Studio-only, so ``loop.py`` and ``informal.py`` are provider-blind.
    """

    def __init__(self, config: AgentConfig, backend: ChatBackend | None = None):
        self.config = config
        self._backend = backend if backend is not None else build_backend(config)

    @property
    def _client(self):
        """Underlying provider SDK client.

        Kept so callers and tests that reach for the transport (e.g. to
        monkeypatch ``chat.completions.create``) still work after the backend
        split. Raises for providers whose backend exposes no such attribute.
        """
        client = getattr(self._backend, "_client", None)
        if client is None:
            raise AttributeError(
                f"{type(self._backend).__name__} exposes no underlying client"
            )
        return client

    def _resolve_model(self) -> str:
        return self._backend.resolve_model()

    def decide(
        self, prompt: str, *, thinking: str | None = None
    ) -> LlmDecision:
        model = self._resolve_model()
        if self.config.llm_tactic_only or "prover" in model.lower():
            return self._decide_tactic_only(model, prompt, thinking=thinking)
        return self._decide_json(model, prompt, thinking=thinking)

    def retrospect(
        self,
        *,
        right_fix: str,
        trajectory: list[dict],
    ) -> dict:
        """Ask the same model to diagnose a timeout after revealing the fix."""
        model = self._resolve_model()
        prompt = (
            "You were attempting an EasyCrypt benchmark and did not finish "
            "within the harness limit. Review your recorded reasoning, actions, "
            "and tool feedback below. The benchmark's known right fix is now "
            "provided. Explain concretely what prevented you from reaching it "
            "and what information or capability you wish you had been given. "
            "Separate requested improvements to the prompt, tools, and EasyCrypt "
            "error presentation. Do not merely restate the fix.\n\n"
            f"RIGHT FIX:\n{right_fix}\n\n"
            "RECORDED TRAJECTORY:\n"
            f"{json.dumps(trajectory, ensure_ascii=False, indent=2)}"
        )
        reply = self._backend.complete(
            system=(
                "Return only a JSON timeout retrospective matching the "
                "requested schema. Be specific and candid."
            ),
            user=prompt,
            thinking=None,
            # The retrospective is free-form prose inside a fixed envelope, so
            # it uses the same JSON-mode switch as actions rather than a
            # second, separately-plumbed schema per provider.
            response_format_mode=action_response_format_mode(self.config),
        )
        content = reply.text
        if not content.strip():
            # Retrospectives are diagnostic, not load-bearing: a model that
            # answered only in its reasoning channel is still worth salvaging.
            for candidate in reply.reasoning_texts:
                if candidate.strip():
                    content = candidate
                    break
        return _parse_retrospective(content)

    def _decide_json(
        self, model: str, prompt: str, *, thinking: str | None = None
    ) -> LlmDecision:
        mode = action_response_format_mode(self.config)
        reply = self._backend.complete(
            system=(
                _strict_json_system_prompt()
                if mode is not None
                else _json_system_prompt()
            ),
            user=prompt,
            thinking=thinking,
            response_format_mode=mode,
        )
        action_text = _action_text_from_reply(reply)

        # Thinking ate the entire output budget and left nothing for the
        # answer. This is the DOMINANT failure mode with thinking on: measured
        # across four DeepSeek trials, 79 of 85 format errors were exactly this
        # (finish_reason='length', empty visible reply), at a mean 12.3k output
        # tokens against a 16,384 cap. It is not a formatting problem and the
        # "escape your backslashes" advice is actively misleading here -- the
        # model never emitted anything to format.
        #
        # Retry once with thinking OFF, which guarantees the budget goes to the
        # answer. Cheaper than the alternative: without this the call is 100%
        # wasted AND the step is burned against the stuck counter.
        if not action_text and _looks_truncated(reply) and thinking != "disabled":
            reply = self._backend.complete(
                system=(
                    _strict_json_system_prompt()
                    if mode is not None
                    else _json_system_prompt()
                ),
                user=prompt,
                thinking="disabled",
                response_format_mode=mode,
            )
            action_text = _action_text_from_reply(reply)

        if not action_text:
            truncated = _looks_truncated(reply)
            # Truncated => the budget ran out before an answer existed, which is
            # infrastructure. Untruncated => the model chose to say nothing,
            # which is the model's problem and counts against it.
            error_cls = LlmProviderError if truncated else LlmFormatError
            raise error_cls(
                "Empty LLM content (visible reply was empty and no JSON action "
                f"was found in reasoning fields; finish_reason={reply.finish_reason!r})."
                + (
                    " The reply hit the output-token cap while reasoning, even "
                    "with thinking disabled — raise --llm-max-tokens."
                    if truncated
                    else " Reply again with a short JSON action only; escape "
                    "backslashes in tactics (write /\\\\ for EasyCrypt /\\)."
                )
            )
        return LlmDecision(
            action=parse_action(action_text),
            thought=reply.thought,
            content=reply.text or action_text,
        )

    def _decide_tactic_only(
        self, model: str, prompt: str, *, thinking: str | None = None
    ) -> LlmDecision:
        goal_match = re.search(r"## Current goal\n(.*?)\n\n## Top relevant premises", prompt, re.DOTALL)
        goal = goal_match.group(1).strip() if goal_match else ""
        tail_match = re.search(r"## Proof script tail\n(.*?)(?:\n\n## Tool specification|\Z)", prompt, re.DOTALL)
        proof_tail = tail_match.group(1).strip() if tail_match else ""
        short_prompt = (
            "You are an EasyCrypt proof assistant. EasyCrypt tactics look like: "
            "`by rewrite addr0.`, `by ring.`, `smt().`, `trivial.`, `qed.`\n"
            "Do NOT output Lean, Coq, or JSON.\n"
            "Reply with exactly one EasyCrypt tactic line and nothing else.\n\n"
            f"Current goal:\n{goal}\n\n"
            f"Proof so far:\n{proof_tail}\n"
        )
        reply = self._backend.complete(
            system=None,
            user=short_prompt,
            thinking=thinking,
            response_format_mode=None,
        )
        content = reply.text.strip()
        thought = reply.thought
        if not content:
            raise LlmFormatError(
                "Empty LLM content from prover model "
                "(message.content was empty; reasoning is not used as a tactic)"
            )
        if content.lower() == "undo":
            return LlmDecision(action=UndoAction(), thought=thought, content=content)
        tactic = _extract_tactic_line(content) or content.splitlines()[0].strip()
        tactic = tactic.strip("`").strip()
        if not tactic:
            raise ValueError(f"Empty tactic from prover model: {content!r}")
        return LlmDecision(
            action=TacticAction(tactic=tactic),
            thought=thought,
            content=content,
        )


def _openai_response_format(mode: str | None) -> dict | None:
    """Translate the provider-neutral mode into an OpenAI ``response_format``."""
    if mode == "json_schema":
        return _ACTION_JSON_SCHEMA
    if mode == "json_object":
        return _JSON_OBJECT_RESPONSE_FORMAT
    return None


def _anthropic_output_format(mode: str | None) -> dict | None:
    """Translate the provider-neutral mode into Anthropic ``output_config``.

    Claude's structured outputs constrain the reply to the schema itself, not
    merely to "valid JSON", so this is the one provider where turning JSON
    mode on removes a real failure class rather than trading one for another.
    """
    if mode != "anthropic_json_schema":
        return None
    return {"format": {"type": "json_schema", "schema": _ACTION_SCHEMA}}


def _anthropic_reply(message) -> ChatReply:
    """Normalize an Anthropic ``Message`` into a :class:`ChatReply`.

    Claude returns a list of typed content blocks. Text blocks are the visible
    reply; thinking blocks are the reasoning channel and are kept strictly out
    of ``text`` so an action can never be read out of Claude's reasoning by
    accident. A ``refusal`` stop reason is surfaced as a recoverable format
    error rather than an empty reply, since the loop can retry the step but
    the operator needs to see why it went nowhere.
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for block in getattr(message, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif block_type == "thinking":
            thought = getattr(block, "thinking", "") or ""
            if thought.strip():
                thinking_parts.append(thought)

    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LlmFormatError(
            "Claude declined this request "
            f"(stop_reason=refusal, category={category!r}). No action was "
            "produced; the step is recorded as a format error."
        )

    reasoning = tuple(part for part in thinking_parts if part.strip())
    return ChatReply(
        text="".join(text_parts),
        thought=reasoning[0] if reasoning else None,
        finish_reason=stop_reason,
        reasoning_texts=reasoning,
    )


def parse_action(content: str) -> AgentAction:
    payload = _parse_json_object(content)
    action = payload.get("action")
    if action == "undo":
        return UndoAction(count=_parse_undo_count(payload))
    if action == "lookup_lemma":
        name = str(payload.get("name", "")).strip()
        if not name:
            raise LlmFormatError("lookup_lemma action missing name")
        return LookupLemmaAction(name=name)
    if action == "search_lemmas":
        query = str(payload.get("query", "")).strip()
        if not query:
            raise LlmFormatError("search_lemmas action missing query")
        from .lemma_search import normalize_search_mode

        mode = normalize_search_mode(str(payload.get("name", "")))
        return SearchLemmasAction(query=query, mode=mode)
    if action == "tactic":
        tactic = str(payload.get("tactic", "")).strip()
        if not tactic:
            raise LlmFormatError("Tactic action missing tactic text")
        return TacticAction(tactic=tactic)
    raise LlmFormatError(f"Unknown action: {action!r} in response: {content!r}")


def _parse_undo_count(payload: dict) -> int:
    """Parse optional undo depth; missing/empty defaults to 1."""
    if "count" not in payload:
        return 1
    raw = payload.get("count")
    if raw is None or raw == "":
        return 1
    if isinstance(raw, bool):
        raise LlmFormatError(f"undo count must be a positive integer, got {raw!r}")
    if isinstance(raw, int):
        count = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise LlmFormatError(f"undo count must be a positive integer, got {raw!r}")
        count = int(raw)
    else:
        text = str(raw).strip()
        if not text:
            return 1
        try:
            count = int(text)
        except ValueError as exc:
            raise LlmFormatError(
                f"undo count must be a positive integer, got {raw!r}"
            ) from exc
    if count < 1:
        raise LlmFormatError(f"undo count must be >= 1, got {count}")
    return count


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if not text:
        raise LlmFormatError(
            "Empty LLM content (message.content was empty; "
            "reasoning fields are never parsed as an action)"
        )

    extracted = _find_json_object(text)
    if extracted is not None:
        text = extracted

    payload = _loads_json_object(text, original=content)
    if not isinstance(payload, dict):
        raise LlmFormatError(
            f"LLM response must be a JSON object, got {type(payload).__name__}"
        )
    return payload


def _loads_json_object(text: str, *, original: str) -> object:
    # The repair runs FIRST, not as a fallback. ``\/`` is a *legal* JSON escape
    # for ``/``, so a reply carrying EasyCrypt's disjunction parses cleanly and
    # silently yields the wrong tactic -- ``smt(a \/ b).`` decodes to
    # ``smt(a / b).``. A fallback-only repair never sees that case because
    # nothing raised. Repairing up front is also idempotent for well-formed
    # input, so correct replies are unaffected.
    repaired = _repair_invalid_json_escapes(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        if repaired != text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        raise LlmFormatError(
            "LLM response is not valid JSON "
            "(often caused by unescaped backslashes in tactics with /\\). "
            "Reply with one JSON object and escape each backslash as \\\\. "
            f"Got: {original!r}"
        )


_JSON_CONTROL_ESCAPES = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _repair_invalid_json_escapes(text: str) -> str:
    """Make a model's JSON-ish reply parse the way it was *meant*, for EasyCrypt.

    Three distinct problems, all observed in real runs:

    1. **Bare backslashes.** Models emit EasyCrypt ``/\\`` as a single
       backslash, an invalid escape that fails ``json.loads``.
    2. **``\\/`` is legal JSON.** It decodes to ``/``, so EasyCrypt's
       disjunction ``\\/`` silently becomes ``/`` -- a different tactic, sent to
       EasyCrypt with no error raised anywhere. This is why ``/`` is treated as
       an *invalid* escape below: in an EasyCrypt payload ``\\/`` always means
       disjunction, and nobody deliberately escapes a forward slash.
    3. **Raw control characters.** JSON forbids literal newlines and tabs
       inside strings, but a multi-line tactic (``proc.\\nwp.\\nskip.``) is the
       natural thing to write, and it is rejected outright.

    The function is idempotent: already-correct JSON passes through unchanged.
    """
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        if ch == '"':
            out.append(ch)
            in_string = False
            i += 1
            continue
        if ch != "\\":
            # A literal control character inside a string is illegal JSON.
            # Encode it rather than lose the whole reply over a multi-line
            # tactic.
            escaped = _JSON_CONTROL_ESCAPES.get(ch)
            if escaped is not None:
                out.append(escaped)
            elif ch < " ":
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
            i += 1
            continue
        if i + 1 >= len(text):
            out.append("\\\\")
            i += 1
            continue
        nxt = text[i + 1]
        # Note: ``/`` is deliberately absent -- see the docstring. ``\/`` is
        # valid JSON but in EasyCrypt it is disjunction, so it falls through to
        # the literal-backslash branch below.
        if nxt in '"\\bfnrt':
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        if nxt == "u" and i + 5 < len(text) and all(
            c in "0123456789abcdefABCDEF" for c in text[i + 2 : i + 6]
        ):
            out.append(text[i : i + 6])
            i += 6
            continue
        # Invalid escape (e.g. ``/\`` before a space or digit): keep as literal.
        out.append("\\\\")
        i += 1
    return "".join(out)


def _parse_retrospective(content: str) -> dict:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM retrospective is not valid JSON: {content!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM retrospective must be a JSON object")
    return payload


def _find_json_object(text: str) -> str | None:
    """Extract the first JSON action object from text or fenced blocks."""
    stripped = text.strip()
    if not stripped:
        return None

    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        stripped,
        flags=re.DOTALL,
    )
    if fenced:
        candidate = fenced.group(1).strip()
        if _looks_like_action_json(candidate):
            return candidate

    if stripped.startswith("{") and _looks_like_action_json(stripped):
        return stripped

    for match in re.finditer(r"\{", stripped):
        candidate = _extract_balanced_object(stripped, match.start())
        if candidate and _looks_like_action_json(candidate):
            return candidate
    return None


def _extract_balanced_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _looks_like_action_json(text: str) -> bool:
    for candidate in (text, _repair_invalid_json_escapes(text)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "action" in payload:
            return True
    return False


def _looks_truncated(reply: ChatReply) -> bool:
    """True when the provider stopped because it ran out of output budget.

    OpenAI-compatible servers say ``length``; Anthropic says ``max_tokens``.
    Both mean the same thing here: reasoning consumed the budget and the
    visible answer never got written.
    """
    return reply.finish_reason in {"length", "max_tokens"}


def _action_text_from_message(message) -> str:
    """Message-level shim over :func:`_action_text_from_reply`.

    Retained for callers holding a raw OpenAI-shaped message rather than a
    normalized reply.
    """
    reasoning = tuple(_iter_reasoning_candidates(message))
    return _action_text_from_reply(
        ChatReply(text=_response_content(message), reasoning_texts=reasoning)
    )


def _action_text_from_reply(reply: ChatReply) -> str:
    """Visible reply if there is one, else salvage JSON from reasoning.

    The ordering is the point: an action is normally only accepted from the
    visible channel. Reaching into reasoning is a concession to models that
    reliably "decide" there and emit nothing visible, and it only ever accepts
    text that actually parses as an action object.
    """
    content = reply.text.strip()
    if content:
        return content
    for candidate in reply.reasoning_texts:
        found = _find_json_object(candidate)
        if found:
            return found
    return ""


def _extract_tactic_line(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line or line.startswith("#"):
            continue
        if re.match(
            r"^(?:" + "|".join(_TACTIC_KEYWORDS) + r")\b",
            line,
            re.IGNORECASE,
        ):
            return line if line.endswith(".") else line + "."
    match = _TACTIC_LINE_RE.search(text)
    if match:
        tactic = match.group(0).strip()
        return tactic if tactic.endswith(".") else tactic + "."
    return None


def _response_content(message) -> str:
    """Visible reply text only; never fall back to reasoning fields."""
    return getattr(message, "content", None) or ""


_REASONING_FIELD_NAMES = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "thought",
)


def _coerce_reasoning_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("content", "text", "summary"):
            nested = _coerce_reasoning_text(value.get(key))
            if nested:
                return nested
        return None
    if isinstance(value, list):
        parts = [_coerce_reasoning_text(item) for item in value]
        joined = "\n".join(part for part in parts if part)
        return joined.strip() or None
    text = str(value).strip()
    return text or None


def _iter_reasoning_candidates(message) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []

    def add(value) -> None:
        text = _coerce_reasoning_text(value)
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    for name in _REASONING_FIELD_NAMES:
        add(getattr(message, name, None))

    model_extra = getattr(message, "model_extra", None) or {}
    for name in _REASONING_FIELD_NAMES:
        add(model_extra.get(name))

    if hasattr(message, "model_dump"):
        dump = message.model_dump()
        for name in _REASONING_FIELD_NAMES:
            add(dump.get(name))

    return candidates


def _response_thought(message) -> str | None:
    """Hidden chain-of-thought from thinking models.

    Probes several provider-specific fields (``reasoning_content`` for LM
    Studio, ``reasoning`` for some OpenAI-compatible APIs, etc.) while
    keeping the visible ``content`` channel for JSON actions only.
    """
    candidates = _iter_reasoning_candidates(message)
    return candidates[0] if candidates else None

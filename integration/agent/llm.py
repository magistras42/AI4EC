"""LM Studio chat completions and tool-response parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Union

from openai import OpenAI

from .config import (
    LLM_PROVIDER_DEEPSEEK,
    action_response_format_mode,
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

_RETROSPECTIVE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "timeout_retrospective",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "prevented_by": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "wished_for": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "array", "items": {"type": "string"}},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "error_presentation": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "other": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["prompt", "tools", "error_presentation", "other"],
                    "additionalProperties": False,
                },
            },
            "required": ["summary", "prevented_by", "wished_for"],
            "additionalProperties": False,
        },
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


def _action_response_format(config: AgentConfig) -> dict | None:
    mode = action_response_format_mode(config)
    if mode == "json_schema":
        return _ACTION_JSON_SCHEMA
    if mode == "json_object":
        return _JSON_OBJECT_RESPONSE_FORMAT
    return None


class LlmClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = OpenAI(**chat_client_kwargs(config))
        self._model = config.llm_model

    def _create(self, **kwargs):
        response = self._client.chat.completions.create(**kwargs)
        if self.config.usage_tracker is not None:
            self.config.usage_tracker.record(response)
        return response

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        models = self._client.models.list()
        if not models.data:
            raise RuntimeError("No LLM model available from the configured chat provider")
        self._model = models.data[0].id
        return self._model

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
        kwargs = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only a JSON timeout retrospective matching the "
                        "requested schema. Be specific and candid."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.llm_temperature,
            "max_tokens": self.config.llm_max_tokens,
            "response_format": (
                _JSON_OBJECT_RESPONSE_FORMAT
                if self.config.llm_provider == LLM_PROVIDER_DEEPSEEK
                else _RETROSPECTIVE_SCHEMA
            ),
            **chat_completion_kwargs(self.config),
        }
        try:
            response = self._create(**kwargs)
        except Exception:
            kwargs.pop("response_format", None)
            response = self._create(**kwargs)
        content = _response_content(response.choices[0].message)
        return _parse_retrospective(content)

    def _decide_json(
        self, model: str, prompt: str, *, thinking: str | None = None
    ) -> LlmDecision:
        response_format = _action_response_format(self.config)
        messages = [
            {
                "role": "system",
                "content": (
                    _strict_json_system_prompt()
                    if response_format is not None
                    else _json_system_prompt()
                ),
            },
            {"role": "user", "content": prompt},
        ]
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": self.config.llm_temperature,
            "max_tokens": self.config.llm_max_tokens,
            **chat_completion_kwargs(self.config, thinking=thinking),
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            response = self._create(**kwargs)
        except Exception:
            # Providers that reject the response_format still get one plain
            # attempt rather than failing the whole trial.
            if response_format is None:
                raise
            kwargs.pop("response_format", None)
            response = self._create(**kwargs)
        message = response.choices[0].message
        content_raw = _response_content(message)
        thought = _response_thought(message)
        action_text = _action_text_from_message(message)
        if not action_text:
            finish_reason = response.choices[0].finish_reason
            raise LlmFormatError(
                "Empty LLM content (message.content was empty and no JSON action "
                f"was found in reasoning fields; finish_reason={finish_reason!r}). "
                "Reply again with a short JSON action only; escape backslashes in "
                "tactics (write /\\\\ for EasyCrypt /\\)."
            )
        return LlmDecision(
            action=parse_action(action_text),
            thought=thought,
            content=content_raw or action_text,
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
        response = self._create(
            model=model,
            messages=[{"role": "user", "content": short_prompt}],
            temperature=self.config.llm_temperature,
            max_tokens=self.config.llm_max_tokens,
            **chat_completion_kwargs(self.config, thinking=thinking),
        )
        message = response.choices[0].message
        content = _response_content(message).strip()
        thought = _response_thought(message)
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_invalid_json_escapes(text)
        if repaired != text:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise LlmFormatError(
            "LLM response is not valid JSON "
            "(often caused by unescaped backslashes in tactics with /\\). "
            "Reply with one JSON object and escape each backslash as \\\\. "
            f"Got: {original!r}"
        )


def _repair_invalid_json_escapes(text: str) -> str:
    """Escape bare backslashes that are not valid JSON string escapes.

    Models often emit EasyCrypt ``/\\`` as a single backslash inside JSON,
    which is an invalid escape and fails ``json.loads``.
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
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(text):
            out.append("\\\\")
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in '"\\/bfnrt':
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


def _action_text_from_message(message) -> str:
    content = _response_content(message).strip()
    if content:
        return content
    for candidate in _iter_reasoning_candidates(message):
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

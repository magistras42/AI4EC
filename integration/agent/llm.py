"""LM Studio chat completions and tool-response parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Union

from openai import OpenAI

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


@dataclass(frozen=True)
class LookupLemmaAction:
    kind: Literal["lookup_lemma"] = "lookup_lemma"
    name: str = ""


AgentAction = Union[TacticAction, UndoAction, LookupLemmaAction]

# Some OpenAI-compatible servers (e.g. LM Studio) reject the generic
# `{"type": "json_object"}` response_format and only support a concrete
# `json_schema`. This schema covers all three action shapes in one object
# (rather than a oneOf/anyOf, which some local model runtimes don't enforce
# well) so a model that isn't a strong native tool-caller is still nudged
# into emitting well-formed, parseable JSON.
_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["tactic", "undo", "lookup_lemma"],
        },
        "tactic": {"type": "string"},
        "name": {"type": "string"},
    },
    "required": ["action", "tactic", "name"],
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

{"action": "tactic", "tactic": "by rewrite addr0.", "name": ""}
{"action": "undo", "tactic": "", "name": ""}
{"action": "lookup_lemma", "tactic": "", "name": "my_lemma"}
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
        "Never put the JSON answer only in reasoning or commentary."
    )


def _strict_json_system_prompt() -> str:
    return (
        "You are an EasyCrypt tactic selector. "
        "Put your final answer only in the visible reply as one JSON "
        "object matching the required schema. Do not put the answer "
        "in reasoning or commentary."
    )


_TACTIC_LINE_RE = re.compile(
    r"(?:(?:" + "|".join(_TACTIC_KEYWORDS) + r")\b[^`\n]{0,200}\.)",
    re.IGNORECASE,
)


class LlmClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = OpenAI(
            base_url=config.lm_studio_base_url,
            api_key="lm-studio",
            timeout=config.lm_studio_timeout,
        )
        self._model = config.llm_model

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        models = self._client.models.list()
        if not models.data:
            raise RuntimeError("No LLM model available in LM Studio")
        self._model = models.data[0].id
        return self._model

    def decide(self, prompt: str) -> LlmDecision:
        model = self._resolve_model()
        if self.config.llm_tactic_only or "prover" in model.lower():
            return self._decide_tactic_only(model, prompt)
        return self._decide_json(model, prompt)

    def _decide_json(self, model: str, prompt: str) -> LlmDecision:
        use_strict_schema = self.config.llm_json_mode
        messages = [
            {
                "role": "system",
                "content": (
                    _strict_json_system_prompt()
                    if use_strict_schema
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
        }
        if use_strict_schema:
            kwargs["response_format"] = _ACTION_JSON_SCHEMA
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception:
            if not use_strict_schema:
                raise
            kwargs.pop("response_format", None)
            response = self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        content_raw = _response_content(message)
        thought = _response_thought(message)
        action_text = _action_text_from_message(message)
        if not action_text:
            finish_reason = response.choices[0].finish_reason
            raise ValueError(
                "Empty LLM content (message.content was empty and no JSON action "
                f"was found in reasoning fields; finish_reason={finish_reason!r})"
            )
        return LlmDecision(
            action=parse_action(action_text),
            thought=thought,
            content=content_raw or action_text,
        )

    def _decide_tactic_only(self, model: str, prompt: str) -> LlmDecision:
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
        response = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": short_prompt}],
            temperature=self.config.llm_temperature,
            max_tokens=self.config.llm_max_tokens,
        )
        message = response.choices[0].message
        content = _response_content(message).strip()
        thought = _response_thought(message)
        if not content:
            raise ValueError(
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
        return UndoAction()
    if action == "lookup_lemma":
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("lookup_lemma action missing name")
        return LookupLemmaAction(name=name)
    if action == "tactic":
        tactic = str(payload.get("tactic", "")).strip()
        if not tactic:
            raise ValueError("Tactic action missing tactic text")
        return TacticAction(tactic=tactic)
    raise ValueError(f"Unknown action: {action!r} in response: {content!r}")


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if not text:
        raise ValueError(
            "Empty LLM content (message.content was empty; "
            "reasoning fields are never parsed as an action)"
        )

    extracted = _find_json_object(text)
    if extracted is not None:
        text = extracted

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM response is not valid JSON: {content!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            f"LLM response must be a JSON object, got {type(payload).__name__}"
        )
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
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and "action" in payload


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

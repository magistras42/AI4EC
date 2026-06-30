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

_TACTIC_LINE_RE = re.compile(
    r"(?:(?:by|rewrite|apply|smt|trivial|split|ring|proc|move|case|elim|exists|have|pose)\b[^`\n]{0,200}\.)",
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

    def decide(self, prompt: str) -> AgentAction:
        model = self._resolve_model()
        if self.config.llm_tactic_only or "prover" in model.lower():
            return self._decide_tactic_only(model, prompt)
        return self._decide_json(model, prompt)

    def _decide_json(self, model: str, prompt: str) -> AgentAction:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an EasyCrypt tactic selector. "
                    "Reply with exactly one JSON object and nothing else."
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
        if self.config.llm_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception:
            if not self.config.llm_json_mode:
                raise
            kwargs.pop("response_format", None)
            response = self._client.chat.completions.create(**kwargs)
        content = _response_text(response.choices[0].message)
        return parse_action(content)

    def _decide_tactic_only(self, model: str, prompt: str) -> AgentAction:
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
        content = _response_text(response.choices[0].message).strip()
        if content.lower() == "undo":
            return UndoAction()
        tactic = _extract_tactic_line(content) or content.splitlines()[0].strip()
        tactic = tactic.strip("`").strip()
        if not tactic:
            raise ValueError(f"Empty tactic from prover model: {content!r}")
        return TacticAction(tactic=tactic)


def parse_action(content: str) -> AgentAction:
    payload = _extract_json(content)
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


def _extract_json(content: str) -> dict:
    text = content.strip()
    if not text:
        raise ValueError("Empty LLM response")

    fenced = re.search(r"```(?:json)?\s*(\{.*)", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).rstrip("`").strip()

    for candidate in _json_candidates(text):
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue

    loose = _parse_fields_loosely(text)
    if loose is not None:
        return loose

    tactic = _extract_tactic_line(text)
    if tactic is not None:
        return {"action": "tactic", "tactic": tactic}

    raise ValueError(f"Could not parse LLM response: {content!r}")


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    if repaired not in candidates:
        candidates.append(repaired)
    return candidates


def _parse_fields_loosely(text: str) -> dict | None:
    if re.search(r'"action"\s*:\s*"undo"', text):
        return {"action": "undo"}
    lookup_match = re.search(r'"action"\s*:\s*"lookup_lemma"', text)
    if lookup_match:
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
        if name_match:
            return {"action": "lookup_lemma", "name": name_match.group(1)}
    tactic_match = re.search(r'"tactic"\s*:\s*"([^"]+)"', text)
    if tactic_match:
        return {"action": "tactic", "tactic": tactic_match.group(1)}
    return None


def _extract_tactic_line(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line or line.startswith("#"):
            continue
        if re.match(
            r"^(by|rewrite|smt|trivial|ring|qed|proc|split|move|apply)\b",
            line,
            re.IGNORECASE,
        ):
            return line if line.endswith(".") else line + "."
    match = _TACTIC_LINE_RE.search(text)
    if match:
        tactic = match.group(0).strip()
        return tactic if tactic.endswith(".") else tactic + "."
    return None


def _response_text(message) -> str:
    content = getattr(message, "content", None) or ""
    if content.strip():
        return content
    reasoning = getattr(message, "reasoning_content", None) or ""
    return reasoning

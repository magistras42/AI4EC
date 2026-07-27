"""Structured run log for the EasyCrypt agent loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .usage import TokenUsage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentRunLog:
    path: Path
    source: Path
    work_copy: Path
    usage: TokenUsage | None = None
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def record(self, event: str, **fields: Any) -> None:
        entry = {"time": _utc_now(), "event": event, **fields}
        self._events.append(entry)
        self._flush()

    def startup(
        self,
        *,
        goal: str,
        premise_count: int,
        cursor_upto: int,
    ) -> None:
        self.record(
            "startup",
            source=str(self.source),
            work_copy=str(self.work_copy),
            goal=goal,
            premise_count=premise_count,
            cursor_upto=cursor_upto,
        )

    def iteration(
        self,
        *,
        step: int,
        goal: str,
        top_premises: dict[str, str],
        ranked_scores: list[tuple[str, float]] | None,
        action: str,
        tactic: str | None = None,
        outcome: str,
        error: str | None = None,
        lookup_name: str | None = None,
        lookup_result: str | None = None,
        search_query: str | None = None,
        search_result: str | None = None,
        undo_count: int | None = None,
        undone: int | None = None,
        thought: str | None = None,
        content: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "step": step,
            "goal": goal,
            "top_premises": top_premises,
            "ranked_scores": ranked_scores,
            "action": action,
            "tactic": tactic,
            "outcome": outcome,
            "error": error,
        }
        if lookup_name is not None:
            fields["lookup_name"] = lookup_name
        if lookup_result is not None:
            fields["lookup_result"] = lookup_result
        if search_query is not None:
            fields["search_query"] = search_query
        if search_result is not None:
            fields["search_result"] = search_result
        if undo_count is not None:
            fields["undo_count"] = undo_count
        if undone is not None:
            fields["undone"] = undone
        if thought is not None:
            fields["thought"] = thought
        if content is not None:
            fields["content"] = content
        self.record("iteration", **fields)

    def finish(self, *, reason: str, message: str, steps: int) -> None:
        self.record(
            "finish",
            reason=reason,
            message=message,
            steps=steps,
            token_usage=self.usage.as_dict() if self.usage is not None else None,
        )

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": str(self.source),
            "work_copy": str(self.work_copy),
            "events": self._events,
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

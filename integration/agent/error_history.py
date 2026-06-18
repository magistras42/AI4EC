"""Proof-state keyed error history for failed tactics."""

from __future__ import annotations

import json
import re
from pathlib import Path

ErrorRecord = tuple[str, str]


class ErrorHistory:
    def __init__(self, path: Path):
        self.path = path
        self._history: dict[str, list[ErrorRecord]] = {}
        self._load()

    def get(self, goal: str) -> list[ErrorRecord]:
        return list(self._history.get(normalize_goal(goal), []))

    def add(self, goal: str, error: str, tactic: str) -> None:
        key = normalize_goal(goal)
        records = self._history.setdefault(key, [])
        records.append((error, tactic))
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(payload, dict):
            self._history = {
                key: [tuple(item) for item in value]
                for key, value in payload.items()
            }

    def _save(self) -> None:
        serializable = {key: list(value) for key, value in self._history.items()}
        self.path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def normalize_goal(goal: str) -> str:
    text = goal.strip()
    text = re.sub(r"^Current goal\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text

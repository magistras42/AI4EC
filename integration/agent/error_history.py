"""Proof-state keyed error history for failed tactics."""

from __future__ import annotations

import json
import re
from pathlib import Path

ErrorRecord = tuple[str, str]
# (normalized_goal_key, error, tactic) — global ring for cross-goal context.
RecentRecord = tuple[str, str, str]

_RECENT_KEY = "__recent__"
_DEFAULT_RECENT_LIMIT = 8

# C-style boolean ops sometimes appear in model output; EasyCrypt uses /\\ and \\/.
_BOOL_AND = re.compile(r"&&")
_BOOL_OR = re.compile(r"\|\|")


def normalize_goal(goal: str) -> str:
    text = goal.strip()
    text = re.sub(r"^Current goal\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_tactic(tactic: str) -> str:
    """Canonical form for comparing tactics across retries.

    Collapses whitespace, trailing period differences, and ``&&``/``||`` vs
    EasyCrypt ``/\\``/``\\/`` so near-duplicate spam counts as the same failure.
    """
    text = tactic.strip()
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith("."):
        text = text[:-1].rstrip()
    text = _BOOL_AND.sub(r"/\\", text)
    text = _BOOL_OR.sub(r"\\/", text)
    # Normalize spaces around punctuation/combinators without changing tokens.
    text = re.sub(r"\s*;\s*", "; ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*\(\s*", "(", text)
    text = re.sub(r"\s*\)\s*", ")", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


class ErrorHistory:
    def __init__(self, path: Path, *, recent_limit: int = _DEFAULT_RECENT_LIMIT):
        self.path = path
        self.recent_limit = max(0, recent_limit)
        self._history: dict[str, list[ErrorRecord]] = {}
        self._recent: list[RecentRecord] = []
        self._load()

    def get(self, goal: str) -> list[ErrorRecord]:
        return list(self._history.get(normalize_goal(goal), []))

    def recent_other(self, goal: str, limit: int | None = None) -> list[ErrorRecord]:
        """Recent failures recorded under a different goal key than ``goal``.

        Used so the prompt still surfaces parse/SMT failures after an accepted
        tactic changes the displayed goal text (which resets the per-goal ban
        list). These are informational; only ``get(goal)`` entries are banned.
        """
        key = normalize_goal(goal)
        cap = self.recent_limit if limit is None else max(0, limit)
        out: list[ErrorRecord] = []
        seen: set[str] = set()
        for goal_key, error, tactic in reversed(self._recent):
            if goal_key == key:
                continue
            norm = normalize_tactic(tactic)
            if norm in seen:
                continue
            seen.add(norm)
            out.append((error, tactic))
            if len(out) >= cap:
                break
        out.reverse()
        return out

    def add(self, goal: str, error: str, tactic: str) -> None:
        key = normalize_goal(goal)
        records = self._history.setdefault(key, [])
        records.append((error, tactic))
        if self.recent_limit > 0:
            self._recent.append((key, error, tactic))
            if len(self._recent) > self.recent_limit:
                self._recent = self._recent[-self.recent_limit :]
        self._save()

    def has_failed(self, goal: str, tactic: str) -> bool:
        """True if a normalized-equal tactic already failed at this goal."""
        return self.failure_count(goal, tactic) > 0

    def failure_count(self, goal: str, tactic: str) -> int:
        key = normalize_goal(goal)
        norm = normalize_tactic(tactic)
        return sum(
            1
            for _error, prior in self._history.get(key, [])
            if normalize_tactic(prior) == norm
        )

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(payload, dict):
            return
        recent_raw = payload.pop(_RECENT_KEY, None)
        self._history = {
            key: [tuple(item) for item in value]
            for key, value in payload.items()
            if isinstance(value, list)
        }
        self._recent = []
        if isinstance(recent_raw, list) and self.recent_limit > 0:
            for item in recent_raw:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) == 3
                    and all(isinstance(part, str) for part in item)
                ):
                    self._recent.append((item[0], item[1], item[2]))
            if len(self._recent) > self.recent_limit:
                self._recent = self._recent[-self.recent_limit :]

    def _save(self) -> None:
        serializable: dict = {
            key: list(value) for key, value in self._history.items()
        }
        if self.recent_limit > 0:
            serializable[_RECENT_KEY] = [list(item) for item in self._recent]
        self.path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

"""Informal-proof repair pipeline: writer LLM, ground-truth extraction, and
red-herring lemma selection.

This experiment never lets an LLM alter or invent the proof goal. The goal
always comes from an existing, complete proof. An LLM is only asked to
redescribe (never modify) the reasoning behind that proof in natural
language, with an automated guard against leaking any EasyCrypt syntax.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openai import OpenAI

from integration.agent.config import AgentConfig, chat_client_kwargs, chat_completion_kwargs
from integration.agent.easycrypt import fetch_goal_and_premises, split_goal_and_premises
from integration.agent.embeddings import EmbeddingClient, rank_by_cosine
from integration.agent.premises import parse_premises


@dataclass(frozen=True)
class InformalConfig:
    """Tunables for the writer LLM and red-herring selection."""

    red_herring_ratio: float = 0.3
    writer_temperature: float = 0.7
    writer_model: str | None = None
    # Some local "thinking" models (observed with google/gemma-4-12b-qat via
    # LM Studio) emit a large `reasoning_content` block that counts against
    # `max_tokens` before ever writing the visible `content` answer. With a
    # small budget this silently truncates or fully starves the actual
    # informal proof (`finish_reason: "length"` with an empty or
    # mid-sentence `content`). This default is deliberately generous to
    # leave room for that hidden reasoning; `write_informal_proof` also
    # detects truncation and retries with an even larger budget.
    writer_max_tokens: int = 4096
    writer_max_retries: int = 2


# Best-effort guard: catches the most common ways an informal-proof writer
# might leak EasyCrypt syntax. Deliberately narrow and keyed to syntax that
# is highly EasyCrypt-specific (code fences, `qed.`, `smt(...)`,
# `rewrite /lemma`, `by <tactic>`), so it does not flag normal mathematical
# prose that legitimately says things like "this completes the proof" or
# "we apply the induction hypothesis" or "by a direct computation".
_CODE_TOKEN_RE = re.compile(
    r"```"
    r"|\bqed\s*\."
    r"|\bsmt\s*\("
    r"|\brewrite\s*/"
    r"|\bby\s+(?:smt|rewrite|ring|algebra|reflexivity|idtac|trivial"
    r"|assumption|congr|exact|apply|split|case|elim)\b",
    re.IGNORECASE,
)


def looks_contaminated(text: str) -> bool:
    """Heuristic check for leaked EasyCrypt syntax in the writer's output."""
    return bool(_CODE_TOKEN_RE.search(text))


class InformalWriterError(RuntimeError):
    """Raised when the writer LLM cannot produce a complete, clean informal
    proof after retries (e.g. persistent truncation or code contamination).
    Callers should treat this as a skip, not let it crash the whole run."""


def looks_truncated(text: str, finish_reason: str | None) -> bool:
    """True if the writer's response was cut off before finishing.

    Triggers on an explicit `finish_reason == "length"` from the API, an
    empty response, or a response that doesn't end with sentence-ending
    punctuation (a decent proxy when `finish_reason` isn't available, e.g.
    from a client that doesn't surface it).
    """
    stripped = text.strip()
    if not stripped:
        return True
    if finish_reason == "length":
        return True
    if finish_reason is None:
        # Unknown finish reason (e.g. a client that doesn't surface it):
        # fall back to a punctuation heuristic rather than trusting an
        # explicit "stop" we don't actually have.
        return not re.search(r"[.!?)\]]\s*$", stripped)
    return False


def fetch_premises_at_cursor(
    file: Path, cursor_line: int, config: AgentConfig
) -> dict[str, str]:
    """Globally accessible lemmas/axioms visible right before `proof.`."""
    result = fetch_goal_and_premises(file, cursor_line, config)
    split = split_goal_and_premises(result.stdout)
    return parse_premises(split.premises)


_NAME_PATTERN_CACHE: dict[str, re.Pattern] = {}


def name_boundary_pattern(name: str) -> re.Pattern:
    pattern = _NAME_PATTERN_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        _NAME_PATTERN_CACHE[name] = pattern
    return pattern


def extract_used_lemma_names(
    tactic_text: str, candidate_names: Iterable[str]
) -> list[str]:
    """Names from `candidate_names` that literally appear in the tactic text."""
    used = [
        name for name in candidate_names if name_boundary_pattern(name).search(tactic_text)
    ]
    return sorted(used)


_WRITER_SYSTEM_PROMPT = """\
You are a mathematician writing an informal, natural-language proof sketch.
You will be given a formal lemma statement and a hidden formal proof outline
(for your reference only, to ground your reasoning). Write a clear informal
proof of the SAME statement, explaining the mathematical reasoning in plain
English.

Strict rules:
- Do NOT use any EasyCrypt syntax, tactic names (e.g. `smt`, `rewrite`, `by`,
  `apply`, `trivial`, `split`, `proof.`, `qed.`), or code blocks.
- Do NOT quote or paraphrase the formal proof outline's tactic sequence.
- Write only mathematical prose, as in a textbook or paper proof.
"""


def write_informal_proof(
    signature: str,
    tactic_text: str,
    config: AgentConfig,
    informal_config: InformalConfig,
) -> str:
    """One-shot writer LLM call producing a code-free informal proof sketch."""
    client = OpenAI(**chat_client_kwargs(config))
    model = informal_config.writer_model or config.llm_model or _resolve_default_model(client)
    user_prompt = (
        f"Lemma statement:\n{signature}\n\n"
        "Hidden formal proof outline (for your reference only; do not "
        "reproduce it literally or mention EasyCrypt in your answer):\n"
        f"{tactic_text}\n\n"
        "Write the informal proof now."
    )

    def _call(extra_reminder: str, max_tokens: int) -> tuple[str, str | None]:
        messages = [
            {"role": "system", "content": _WRITER_SYSTEM_PROMPT + extra_reminder},
            {"role": "user", "content": user_prompt},
        ]
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=informal_config.writer_temperature,
            max_tokens=max_tokens,
            **chat_completion_kwargs(config),
        )
        if config.usage_tracker is not None:
            config.usage_tracker.record(response)
        choice = response.choices[0]
        content = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None)
        return content.strip(), finish_reason

    text, finish_reason = _call("", informal_config.writer_max_tokens)
    attempts = 1
    max_tokens = informal_config.writer_max_tokens
    while (
        looks_truncated(text, finish_reason) or looks_contaminated(text)
    ) and attempts <= informal_config.writer_max_retries:
        if looks_contaminated(text):
            reminder = (
                "\n\nREMINDER: your previous answer leaked EasyCrypt syntax. "
                "Rewrite it using ONLY plain mathematical English, no code."
            )
        else:
            # Some local "thinking" models spend most of the token budget
            # on hidden reasoning before ever writing the visible answer,
            # silently truncating it. Ask for a shorter final answer and
            # give substantially more room for that hidden reasoning.
            reminder = (
                "\n\nREMINDER: your previous answer was cut off before "
                "finishing. Keep your reasoning brief and write a SHORT, "
                "complete informal proof (at most a few sentences) that "
                "ends with a concluding sentence."
            )
            max_tokens = min(max_tokens * 2, 16384)
        text, finish_reason = _call(reminder, max_tokens)
        attempts += 1

    if looks_contaminated(text):
        raise InformalWriterError(
            "Writer LLM leaked EasyCrypt syntax after retries"
        )
    if looks_truncated(text, finish_reason):
        raise InformalWriterError(
            f"Writer LLM produced a truncated informal proof after "
            f"{attempts} attempt(s) (finish_reason={finish_reason!r}): "
            f"{text[:200]!r}"
        )
    return text


def _resolve_default_model(client: OpenAI) -> str:
    models = client.models.list()
    if not models.data:
        raise RuntimeError("No LLM model available in LM Studio")
    return models.data[0].id


def select_red_herrings(
    used: dict[str, str],
    catalog: dict[str, str],
    config: AgentConfig,
    ratio: float,
    rng: random.Random,
) -> dict[str, str]:
    """Pick decoy lemmas that are cosine-similar to the ones actually used.

    Returns `max(1, round(ratio * len(used)))` distractors (0 if `used` is
    empty), drawn from `catalog` minus the used names.
    """
    candidates = {name: sig for name, sig in catalog.items() if name not in used}
    if not used or not candidates:
        return {}

    target_count = max(1, round(ratio * len(used)))
    target_count = min(target_count, len(candidates))

    embedder = EmbeddingClient(config)
    candidate_index = embedder.build_index(candidates)
    used_index = embedder.build_index(used)

    best_score: dict[str, float] = {}
    for vec in used_index.values():
        ranked = rank_by_cosine(candidate_index, vec, len(candidate_index))
        for cand_name, score in ranked:
            if score > best_score.get(cand_name, float("-inf")):
                best_score[cand_name] = score

    ordered = sorted(best_score.items(), key=lambda item: item[1], reverse=True)
    chosen = [name for name, _score in ordered[:target_count]]

    if len(chosen) < target_count:
        remaining = [name for name in candidates if name not in chosen]
        rng.shuffle(remaining)
        chosen.extend(remaining[: target_count - len(chosen)])

    return {name: candidates[name] for name in chosen}


def build_lemma_manifest(
    used: dict[str, str], herrings: dict[str, str]
) -> list[tuple[str, str]]:
    """Merge real + decoy lemmas, sorted alphabetically so position leaks no signal."""
    merged: dict[str, str] = {**used, **herrings}
    return sorted(merged.items(), key=lambda item: item[0])


def build_labeled_manifest(
    used: dict[str, str], herrings: dict[str, str]
) -> dict[str, dict[str, object]]:
    """Ground-truth manifest for post-hoc analysis only; never shown to the solver."""
    labeled: dict[str, dict[str, object]] = {}
    for name, sig in used.items():
        labeled[name] = {"signature": sig, "is_real": True}
    for name, sig in herrings.items():
        labeled[name] = {"signature": sig, "is_real": False}
    return dict(sorted(labeled.items()))

"""Prompt construction for stateless LLM iterations."""

from __future__ import annotations

from pathlib import Path

EXAMPLES_PATH = Path(__file__).resolve().parent / "examples" / "tactics_fewshot.md"

BASE_TOOL_SPEC = """\
Respond with exactly one JSON object and no other text.

Tactic:
{"action": "tactic", "tactic": "by rewrite addr0."}

Undo one tactic step (never undo the lemma signature or `proof.` line):
{"action": "undo"}
"""

LOOKUP_TOOL_SPEC = """
Lookup a lemma or axiom signature by name:
{"action": "lookup_lemma", "name": "my_lemma"}
"""

TOOL_SPEC = BASE_TOOL_SPEC


def load_fewshot_examples(path: Path | None = None) -> str:
    example_path = path or EXAMPLES_PATH
    return example_path.read_text(encoding="utf-8")


def tool_spec(*, enable_lemma_lookup: bool = False) -> str:
    if enable_lemma_lookup:
        return BASE_TOOL_SPEC + LOOKUP_TOOL_SPEC
    return BASE_TOOL_SPEC


def build_prompt(
    goal: str,
    top_premises: dict[str, str],
    failed_tactics: list[tuple[str, str]],
    proof_tail: str,
    fewshot: str | None = None,
    repair_hint: str | None = None,
    lookup_notes: list[str] | None = None,
    enable_lemma_lookup: bool = False,
) -> str:
    sections = [
        "You are an EasyCrypt proof assistant agent. Choose the next tactic or undo.",
        "",
    ]
    if repair_hint:
        sections.extend(
            [
                "## Repair hint (reference broken proof)",
                repair_hint,
                "",
            ]
        )
    sections.extend(
        [
            "## Few-shot examples",
            fewshot or load_fewshot_examples(),
            "",
            "## Current goal",
            goal,
            "",
            "## Top relevant premises",
            _format_premises(top_premises),
            "",
            "## Previously failed at this goal",
            _format_failures(failed_tactics),
            "",
            "## Proof script tail",
            proof_tail,
            "",
        ]
    )
    if lookup_notes:
        sections.extend(
            [
                "## Lemma lookup results",
                "\n".join(lookup_notes) if lookup_notes else "(none)",
                "",
            ]
        )
    sections.extend(
        [
            "## Tool specification",
            tool_spec(enable_lemma_lookup=enable_lemma_lookup),
        ]
    )
    return "\n".join(sections)


def _format_premises(premises: dict[str, str]) -> str:
    if not premises:
        return "(none)"
    lines = []
    for name, text in premises.items():
        lines.append(f"- {name}: {text}")
    return "\n".join(lines)


def _format_failures(failures: list[tuple[str, str]]) -> str:
    if not failures:
        return "(none)"
    lines = []
    for error, tactic in failures:
        lines.append(f"- tactic `{tactic}` -> error: {error.strip()}")
    return "\n".join(lines)

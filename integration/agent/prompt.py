"""Prompt construction for stateless LLM iterations."""

from __future__ import annotations

from pathlib import Path

from .llm import action_response_format_spec

EXAMPLES_PATH = Path(__file__).resolve().parent / "examples" / "tactics_fewshot.md"

BASE_TOOL_SPEC = """\
Respond with exactly one JSON object and no other text.

Tactic:
{"action": "tactic", "tactic": "by rewrite addr0.", "name": ""}

Undo one tactic step (never undo the lemma signature or `proof.` line):
{"action": "undo", "tactic": "", "name": ""}

Never use the `admit` tactic: it marks a goal as assumed rather than
proving it, and will be rejected outright.
"""

LOOKUP_TOOL_SPEC = """
Lookup a lemma or axiom signature by name:
{"action": "lookup_lemma", "tactic": "", "name": "my_lemma"}
"""

TOOL_SPEC = BASE_TOOL_SPEC


def load_fewshot_examples(path: Path | None = None) -> str:
    example_path = path or EXAMPLES_PATH
    return example_path.read_text(encoding="utf-8")


def tool_spec(*, enable_lemma_lookup: bool = False) -> str:
    spec = BASE_TOOL_SPEC
    if enable_lemma_lookup:
        spec += LOOKUP_TOOL_SPEC
    return spec + "\n" + action_response_format_spec()


def build_prompt(
    goal: str,
    top_premises: dict[str, str],
    failed_tactics: list[tuple[str, str]],
    proof_tail: str,
    fewshot: str | None = None,
    repair_hint: str | None = None,
    informal_proof: str | None = None,
    lookup_notes: list[str] | None = None,
    enable_lemma_lookup: bool = False,
) -> str:
    sections = [
        "You are an EasyCrypt proof assistant agent. Choose the next tactic or undo.",
        "",
        "## Reading the current goal",
        (
            "EasyCrypt displays goals in two distinct forms that require different tactics:\n"
            "\n"
            "PROGRAM-LOGIC form — you will see 'pre = ...' and 'post = ...' fields,\n"
            "or a line like 'Func.procedure' between the pre and post. This means the\n"
            "proof is still inside Hoare/pHoare/equiv reasoning. You MUST use program-\n"
            "logic tactics (proc, wp, skip, call, inline, rnd, seq, if, while) to\n"
            "reduce this before ANY ambient-logic tactic (smt, ring, algebra, trivial)\n"
            "can apply. Typical sequence: proc. then wp; skip; smt().\n"
            "\n"
            "AMBIENT-LOGIC form — the goal shows a plain formula with no 'pre'/'post'\n"
            "fields, e.g. '0 <= x', 'a + b = b + a', or 'P => Q'. Now you can use\n"
            "smt(), ring, trivial, rewrite, apply, have, split, left, right, etc.\n"
            "Do NOT apply proc/wp/skip/call to an ambient-logic goal.\n"
            "\n"
            "ring and algebra only work on EQUALITIES (lhs = rhs). For inequalities\n"
            "or implications, use smt(). If smt() alone fails on a nonlinear goal\n"
            "(products, squares, logs), try smt(lemma_name) with a relevant lemma,\n"
            "or introduce an intermediate step: have h : fact by smt(). smt(h)."
        ),
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
    if informal_proof:
        sections.extend(
            [
                "## Informal proof sketch (natural-language reference, no code)",
                informal_proof,
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
        ]
    )
    if failed_tactics:
        sections.extend(
            [
                "IMPORTANT: do NOT repeat any tactic listed above verbatim — "
                "it is guaranteed to fail identically again at this exact "
                "goal. Choose a genuinely different tactic, or a different "
                "lemma/argument, instead."
            ]
        )
    sections.extend(
        [
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

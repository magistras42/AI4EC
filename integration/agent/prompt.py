"""Prompt construction for stateless LLM iterations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .error_history import normalize_tactic
from .llm import action_response_format_spec

EXAMPLES_PATH = Path(__file__).resolve().parent / "examples" / "tactics_fewshot.md"

_HL_JUDGMENT_RE = re.compile(r"\b(hoare|ehoare|phoare|equiv)\s*\[", re.IGNORECASE)
_IMPL_BEFORE_HL_RE = re.compile(
    r"=>\s*(hoare|ehoare|phoare|equiv)\s*\[",
    re.IGNORECASE,
)
# Module paths may include functor apps: M(A).f or Game(Adv).step ~ Game(Adv).step
_MOD_PATH = r"[A-Za-z_][\w']*(?:\([^)]*\))?(?:\.[A-Za-z_][\w']*(?:\([^)]*\))?)+"
_PROC_HEADER_RE = re.compile(
    rf"^\s*{_MOD_PATH}(?:\s*~\s*{_MOD_PATH})?\s*$"
)
_CALL_STMT_RE = re.compile(r"<@|\bcall\b", re.IGNORECASE)
_WHILE_RE = re.compile(r"\bwhile\s*\(")
_IF_RE = re.compile(r"\bif\s*\(")
_NONLINEAR_RE = re.compile(r"\^|\blog\b|\bln\b|\bexp\b|\*\s*[A-Za-z0-9_(]")

BASE_TOOL_SPEC = """\
Respond with exactly one JSON object and no other text.

Tactic:
{"action": "tactic", "tactic": "by rewrite addr0.", "name": "", "query": "", "count": ""}

Undo one or more tactic steps (never undo the lemma signature or `proof.` line).
Omit count or use "" / "1" to undo a single step; set count to undo several:
{"action": "undo", "tactic": "", "name": "", "query": "", "count": ""}
{"action": "undo", "tactic": "", "name": "", "query": "", "count": "3"}

Never use the `admit` tactic: it marks a goal as assumed rather than
proving it, and will be rejected outright.
"""

LOOKUP_TOOL_SPEC = """
Lookup a lemma or axiom by EasyCrypt-qualified path (``Theory.basename``) or bare
basename (lists all theories when ambiguous):
{"action": "lookup_lemma", "tactic": "", "name": "RField.exprM", "query": "", "count": ""}
{"action": "lookup_lemma", "tactic": "", "name": "exprM", "query": "", "count": ""}

Search lemmas. Put the mode in ``name`` (empty = semantic). Modes:
  semantic  — embedding cosine similarity (may miss exact identifiers)
  substring — case-insensitive substring over lemma paths/names, then signatures
  prefix    — lemma paths or basenames starting with the query
  exact     — exact qualified path or unique basename (case-insensitive)
Optional theory filter (any mode): include ``theory:Path`` in the query, e.g.
``theory:RField`` or ``theory:Ring.IntID``. Remaining tokens are the search text.
Catalog keys are qualified paths (``RField.exprM``, ``Ring.IntID.exprM``); use
those names in ``apply`` / ``rewrite`` / ``smt(...)``.
Examples:
{"action": "search_lemmas", "tactic": "", "name": "", "query": "natural log product", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "substring", "query": "lnM", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "prefix", "query": "ln", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "exact", "query": "lnM", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "substring", "query": "theory:RField exprM", "count": ""}
{"action": "search_lemmas", "tactic": "", "name": "substring", "query": "theory:RField", "count": ""}
If semantic search fails to surface a known identity, switch to substring/prefix/exact
with a short token from the expected lemma name. Do not repeat the same search
mode+query. The harness limits consecutive lookup/search actions.
"""

TOOL_SPEC = BASE_TOOL_SPEC

_ANTI_LOOP_RULE = (
    "## Anti-loop rule\n"
    "After any failed tactic, the next action MUST change strategy class:\n"
    "- break a compound `t1; t2.` into separate steps,\n"
    "- change the invariant / lemma arguments,\n"
    "- try a different head tactic,\n"
    "- or use lookup/search/undo.\n"
    "Whitespace, `&&` vs `/\\`, or reordering equivalent conjuncts do NOT "
    "count as a new strategy. The harness hard-rejects normalized duplicates "
    "of tactics that already failed at this goal."
)

_ROLLBACK_RULE = (
    "## Failed tactics are rolled back\n"
    "When a tactic fails (EasyCrypt error, parse error, format error, or "
    "harness rejection), the harness removes it from the proof script. "
    "The proof state does not advance, so the next prompt shows the SAME "
    "current goal on purpose — that is not a stale or repeated user "
    "message. Read the failure feedback below and choose a different "
    "tactic; do not treat an unchanged goal as missing error context."
)

_GOAL_SHAPE_RULE = (
    "## Goal shape before program-logic tactics\n"
    "Read the formula under the dashed separator carefully:\n"
    "- If it is an implication or has leading binders wrapping a Hoare/pHoare/"
    "equiv judgment (e.g. `H => hoare[...]`, `forall x, hoare[...]`), first "
    "introduce those hypotheses/binders with `move => ...` (or `move => /#`). "
    "Only then apply `proc.`, `while`, `wp`, etc. Those tactics expect a bare "
    "program-logic judgment, not `P => judgment`.\n"
    "- If it already shows `pre =` / `post =` (or a bare `hoare`/`phoare`/"
    "`equiv` judgment), do not re-introduce; use program-logic tactics.\n"
    "- If it is a plain ambient formula, use ambient tactics only.\n"
    "The prompt also adds **Active goal-shape hints** under the current goal "
    "whenever the displayed state matches a known shape — read those before "
    "choosing a tactic; do not wait for a failed attempt."
)

_PROGRAM_LOGIC_MENU_RULE = (
    "## Program-logic tactic menu (match the leading statements)\n"
    "Inspect the statement lists between `pre =` and `post =` (left and "
    "right columns for `equiv`). Choose a head tactic that matches what is "
    "actually at the front of each side:\n"
    "- Closed procedure header (`M.f` or `M.f ~ N.g`, no numbered statements "
    "yet): usually `proc.` to open bodies. Prefer `proc*.` when you still "
    "need the original procedure identity so a later `call` can apply a lemma "
    "about those procedures (plain `proc.` opens bodies and drops that link).\n"
    "- Identical abstract procedure calls on both sides of an `equiv`, with no "
    "useful specification: `call (_: true)` then `auto` / `skip` — a full "
    "`call (_: P ==> Q)` that restates the same goal is circular.\n"
    "- Concrete module/procedure bodies still wrapped behind a call: try "
    "`inline *` (or `inline M.f`) before `wp` / `auto`, so constants and "
    "assignments become visible.\n"
    "- `while` with a small, statically known trip count in the precondition: "
    "consider `unroll k.` at the loop's code position, then `rcondf` / "
    "`rcondt` when the remaining guard is obviously false/true. Do not insist "
    "on inventing an invariant when unrolling is enough.\n"
    "- `while` with unknown/large bound: use `while (invariant).`, then "
    "discharge body preservation and init/exit separately.\n"
    "- Asymmetric `equiv` (loop/`if` on one side only, other side empty or "
    "different): use `seq` to carve matching prefixes before `while` / "
    "`rcondt` / `rcondf` / `if`. Applying `while` or `rcondf` to the wrong "
    "side or wrong code position fails with a shape error — that does NOT "
    "mean the goal became ambient.\n"
    "- Empty instruction lists (no statements left): `skip.` then ambient "
    "tactics. After `skip.`, program-logic tactics (`wp`, `proc`, `while`, "
    "`call`) no longer apply.\n"
    "- Nonlinear arithmetic in invariants or residuals (`^`, products, logs): "
    "bare `smt()` often fails — use `simplify` / `progress`, named "
    "`smt(Lemma)`, `rewrite`, or search for the algebraic identity."
)

_SIMPLIFY_RULE = (
    "## When to simplify / progress\n"
    "`simplify` and `progress` clean definitional and propositional clutter; "
    "they are not substitutes for a correct invariant or lemma.\n"
    "Prefer them when:\n"
    "- After `proc.` / `wp` / `skip` / `while` / `auto`, the remaining goal is "
    "still definitionally heavy (projections, pairs, unreduced applications, "
    "large nested equalities) rather than a short ambient formula.\n"
    "- After `skip.`, the goal is now ambient (plain formula / implication). "
    "If `smt()` fails on a busy residual, try `progress.` or `simplify.` "
    "once to split conjuncts / reduce projections, then retry automation or "
    "a named lemma — do not re-apply `skip.` / `wp.`.\n"
    "- Concrete numeric or algebraic goals remain after program-logic reduction "
    "and bare `smt()` fails — try `simplify.` or `progress.` once, then retry "
    "automation or a named lemma.\n"
    "- A one-shot `while (...); auto; smt()` fails: switch to stepwise `while`, "
    "inspect each subgoal, and consider `simplify`/`progress` on busy residual "
    "goals before another SMT attempt.\n"
    "Do not spam `simplify.` when the goal is already a simple ambient formula."
)

_LEMMA_SEARCH_RULE = (
    "## Finding algebraic identities\n"
    "Semantic search often misses short lemma names. When you need a known "
    "rewrite (exponent laws, ring identities, etc.): try `substring` / "
    "`prefix` / `exact` on a short token from the name, optionally scoped "
    "with `theory:Path` (e.g. `theory:RField` or `theory:Ring.IntID`). "
    "Use the returned qualified path in `rewrite` / `apply` / `smt(...)`. "
    "Do not burn the search budget on repeated semantic queries."
)


def load_fewshot_examples(path: Path | None = None) -> str:
    example_path = path or EXAMPLES_PATH
    return example_path.read_text(encoding="utf-8")


def _goal_conclusion(goal: str) -> str:
    """Return the formula below EasyCrypt's dashed separator, if present."""
    text = goal or ""
    if "------------------------------------------------------------------------" in text:
        text = text.split("------------------------------------------------------------------------")[-1]
    return text.strip()


def goal_looks_program_logic(goal: str) -> bool:
    """True when the displayed goal still looks like Hoare/pHoare/equiv form."""
    text = goal or ""
    if "pre =" in text and "post =" in text:
        return True
    compact = re.sub(r"\s+", " ", text).lower()
    if _HL_JUDGMENT_RE.search(compact):
        return True
    # EasyCrypt often prints open statement lists without repeating `equiv[`.
    if _WHILE_RE.search(text) or _IF_RE.search(text):
        return True
    return False


def goal_is_implication_before_hl(goal: str) -> bool:
    """True when the conclusion is H => HL-judgment or forall-wrapped HL."""
    compact = re.sub(r"\s+", " ", _goal_conclusion(goal)).strip().lower()
    if not compact:
        return False
    if re.match(r"^(forall|∀)\b", compact) and _HL_JUDGMENT_RE.search(compact):
        return True
    return bool(_IMPL_BEFORE_HL_RE.search(compact))


def _program_statement_block(goal: str) -> str:
    """Extract the statement region between pre= and post= when present."""
    text = goal or ""
    if "pre =" not in text or "post =" not in text:
        return ""
    mid = text.split("pre =", 1)[1]
    mid = mid.split("post =", 1)[0]
    # Drop the precondition line itself; keep the body that follows.
    lines = mid.splitlines()
    if not lines:
        return ""
    return "\n".join(lines[1:]).strip()


def _split_equiv_columns(block: str) -> tuple[str, str]:
    """Best-effort split of EasyCrypt's two-column equiv statement dump."""
    if not block:
        return "", ""
    left_parts: list[str] = []
    right_parts: list[str] = []
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # Common dumps pad a wide left column then a right column.
        m = re.match(r"^(.*?)(?: {2,}|\t+)(\(.*)$", line)
        if m:
            left_parts.append(m.group(1))
            right_parts.append(m.group(2))
            continue
        if re.match(r"^\s*\(", line) and left_parts and not "".join(left_parts).strip():
            right_parts.append(line)
        else:
            left_parts.append(line)
    return "\n".join(left_parts), "\n".join(right_parts)


def _has_proc_header(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return False
    # A lone Module.proc / Module.f ~ Module.g line, no numbered code yet.
    if re.search(r"\(\d", stripped):
        return False
    if _WHILE_RE.search(stripped) or _IF_RE.search(stripped) or _CALL_STMT_RE.search(
        stripped
    ):
        return False
    for line in stripped.splitlines():
        if _PROC_HEADER_RE.match(line.strip()):
            return True
    return False


def _side_is_empty(side: str) -> bool:
    text = side.strip()
    if not text:
        return True
    # Column placeholders / whitespace-only dumps count as empty.
    compact = re.sub(r"[\s().\d-]+", "", text)
    return not compact


def _detect_judgment_type(goal: str) -> str:
    """Identify whether the current goal is hoare, phoare, or equiv.

    Returns a short label string or empty if undetermined.
    """
    text = goal or ""
    # Explicit judgment keywords in the goal text
    compact = re.sub(r"\s+", " ", text).lower()
    if "equiv[" in compact or "equiv [" in compact:
        return "equiv"
    if "phoare[" in compact or "phoare [" in compact:
        return "phoare"
    if "ehoare[" in compact or "ehoare [" in compact:
        return "ehoare"
    if "hoare[" in compact or "hoare [" in compact:
        return "hoare"

    # Detect from structural markers
    # Bound field indicates phoare
    if re.search(r"\bBound\s*:", text):
        return "phoare (Bound field present — use `rnd` for samplings, not `wp`)"
    # Two-column equiv markers
    if "{1}" in text or "{2}" in text or "={" in text:
        return "equiv"
    if re.search(r"\s~\s", text):
        return "equiv"
    # If we see `pre =` and `post =` without equiv markers, it's hoare
    if "pre =" in text and "post =" in text:
        return "hoare"
    return ""


def format_active_goal_shape_hints(goal: str) -> str:
    """Proactive, shape-conditioned hints for the current EasyCrypt goal.

    These are intentionally pattern-level (program-logic vs ambient, loops,
    calls, empty programs) and must not cite corpus-specific lemmas or proofs.
    """
    if not (goal or "").strip():
        return ""

    bullets: list[str] = [
        "These hints are selected from the *current* goal text. Prefer them "
        "over generic trial-and-error; a matching tactic now beats failing "
        "first and reading an error hint later.",
    ]

    if goal_is_implication_before_hl(goal):
        bullets.append(
            "Detected: binders/implication wrapping a Hoare/pHoare/equiv "
            "judgment. First `move => ...` (or `move => /#`) to expose the "
            "bare judgment; only then `proc.` / `while` / `wp`."
        )
        return _render_hint_block(bullets)

    if goal_looks_program_logic(goal):
        bullets.append(
            "Detected: PROGRAM-LOGIC goal (`pre`/`post` or Hoare/equiv "
            "judgment). Use program-logic tactics only until statements are "
            "gone. Do not apply `smt` / `ring` / `trivial` / `split` yet."
        )
        # Identify the specific judgment type to help the agent choose tactics.
        _jtype = _detect_judgment_type(goal)
        if _jtype:
            bullets.append(f"Judgment type: **{_jtype}**.")

        block = _program_statement_block(goal)
        left, right = _split_equiv_columns(block)
        is_equiv = (
            bool(re.search(r"\s~\s", block))
            or "{1}" in goal
            or "{2}" in goal
            or "={" in goal  # relational equality sugar, e.g. ={x,y}
            or (
                bool(left.strip())
                and bool(right.strip())
                and left.strip() != right.strip()
            )
        )

        if _has_proc_header(block):
            bullets.append(
                "Detected: closed procedure header. Start with `proc.` to open "
                "bodies. If this is an `equiv` and a later step must `call` a "
                "lemma about those same procedures, prefer `proc*.` so the "
                "procedure identity is preserved."
            )
        if _CALL_STMT_RE.search(block):
            bullets.append(
                "Detected: procedure call(s) in the statement list. For "
                "identical abstract calls on both sides of an `equiv` with no "
                "usable spec, try `call (_: true)` then `auto`. If the callee "
                "is a concrete module in scope, try `inline *` (or "
                "`inline M.f`) before `wp`/`auto`."
            )
        if _WHILE_RE.search(block):
            bullets.append(
                "Detected: `while` in the program. Either (a) give an "
                "invariant with `while (Inv).` and discharge body / init-exit "
                "separately, or (b) if the trip count is a small constant in "
                "the precondition, `unroll k.` at the loop position then "
                "`rcondf`/`rcondt` when the guard is statically false/true. "
                "`wp` alone will not eliminate the loop."
            )
            if is_equiv:
                left_while = bool(_WHILE_RE.search(left))
                right_while = bool(_WHILE_RE.search(right))
                if left_while != right_while or _side_is_empty(left) or _side_is_empty(
                    right
                ):
                    bullets.append(
                        "Detected: asymmetric `equiv` statements (loop/`if` "
                        "or emptiness differs across sides). Use `seq` to "
                        "align prefixes before `while` / `rcondt` / `rcondf` / "
                        "`if`. A shape error here means the wrong head "
                        "statement — not that the goal is ambient."
                    )
        elif _IF_RE.search(block) and is_equiv:
            bullets.append(
                "Detected: conditional in an `equiv` body. Consider `if` / "
                "`rcondt` / `rcondf` at the correct code position, or `seq` "
                "if the sides are not aligned."
            )

        if block and not _WHILE_RE.search(block) and not _IF_RE.search(block):
            # Possibly empty or straight-line only
            if not block.strip() or (
                not _has_proc_header(block)
                and not _CALL_STMT_RE.search(block)
                and not re.search(r"<-", block)
            ):
                bullets.append(
                    "Detected: little or no code left under `pre`/`post`. "
                    "Apply `skip.` to move to an ambient goal, then use "
                    "ambient tactics (`smt`, `progress`, `rewrite`, ...)."
                )
            elif re.search(r"<-", block) and not _CALL_STMT_RE.search(block):
                bullets.append(
                    "Detected: straight-line assignments remain. Typical "
                    "finish: `wp.` then `skip.` then ambient automation."
                )
        return _render_hint_block(bullets)

    # Ambient
    conclusion = _goal_conclusion(goal)
    bullets.append(
        "Detected: AMBIENT-LOGIC goal (plain formula, no `pre`/`post`). "
        "Use ambient tactics only (`smt`, `rewrite`, `apply`, `progress`, "
        "`trivial`, ...). Do not apply `proc` / `wp` / `skip` / `while` / "
        "`call`."
    )
    if "=>" in conclusion or conclusion.lower().startswith("forall"):
        bullets.append(
            "Detected: ambient implication or quantifiers. Introduce with "
            "`move => ...` / `split` as needed before automation, or use "
            "`progress.` to decompose a busy residual."
        )
    if _NONLINEAR_RE.search(conclusion):
        bullets.append(
            "Detected: nonlinear operators in the formula. Bare `smt()` often "
            "fails — try `simplify.` / `progress.`, `smt(LemmaName)`, or "
            "`rewrite` with a lemma from substring/exact search."
        )
    return _render_hint_block(bullets)


def _render_hint_block(bullets: list[str]) -> str:
    if not bullets:
        return ""
    header, *rest = bullets
    lines = [header]
    lines.extend(f"- {item}" for item in rest)
    return "\n".join(lines)


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
    informal_proof_is_formal: bool = False,
    migration_hints: str | None = None,
    lookup_notes: list[str] | None = None,
    enable_lemma_lookup: bool = False,
    past_steps: list[dict[str, Any]] | None = None,
    search_warning: str | None = None,
    recent_failures: list[tuple[str, str]] | None = None,
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
            "logic tactics (proc, proc*, wp, skip, call, inline, rnd, seq, if, while,\n"
            "unroll, rcondt, rcondf) to reduce this before ANY ambient-logic tactic\n"
            "(smt, ring, algebra, trivial) can apply.\n"
            "\n"
            "AMBIENT-LOGIC form — the goal shows a plain formula with no 'pre'/'post'\n"
            "fields, e.g. '0 <= x', 'a + b = b + a', or 'P => Q'. Now you can use\n"
            "smt(), ring, trivial, rewrite, apply, have, split, left, right, etc.\n"
            "Do NOT apply proc/wp/skip/call to an ambient-logic goal.\n"
            "\n"
            "Transition: after `skip.` (empty program), the next goal is ambient even\n"
            "if the previous one had pre/post. Read the newly displayed goal; if there\n"
            "is no pre/post, switch to ambient tactics immediately.\n"
            "\n"
            "ring and algebra only work on EQUALITIES (lhs = rhs). For inequalities\n"
            "or implications, use smt(). If smt() alone fails on a nonlinear goal\n"
            "(products, squares, logs, exponents), try smt(lemma_name) with a relevant\n"
            "lemma, simplify/progress, or introduce an intermediate step:\n"
            "have h : fact by smt(). smt(h)."
        ),
        "",
        _GOAL_SHAPE_RULE,
        "",
        _PROGRAM_LOGIC_MENU_RULE,
        "",
        _SIMPLIFY_RULE,
        "",
        _LEMMA_SEARCH_RULE,
        "",
        _ANTI_LOOP_RULE,
        "",
        _ROLLBACK_RULE,
        "",
    ]
    if search_warning:
        sections.extend(["## Search budget warning", search_warning, ""])
    if repair_hint:
        sections.extend(
            [
                "## Repair hint (reference broken proof)",
                repair_hint,
                "",
            ]
        )
    if migration_hints:
        sections.extend(
            [
                "## Version migration guide (compatibility changes)",
                migration_hints,
                "",
            ]
        )
    if informal_proof:
        if informal_proof_is_formal:
            heading = (
                "## Broken formal proof (your primary strategy)\n"
                "This proof *used to compile* on an older EasyCrypt version. "
                "Follow it step-by-step as closely as possible. When a tactic "
                "from this script fails, diagnose the specific error and adapt "
                "minimally (e.g. fix a variable name, adjust an index, add a "
                "missing qualifier) rather than abandoning the strategy. The "
                "overall proof structure, tactic ordering, and invariants are "
                "almost certainly correct — only minor syntax or API drift may "
                "need repair."
            )
        else:
            heading = "## Informal proof sketch (natural-language reference, no code)"
        sections.extend([heading, informal_proof, ""])
    sections.extend(
        [
            "## Few-shot examples",
            fewshot or load_fewshot_examples(),
            "",
            "## Current goal",
            goal,
            "",
        ]
    )
    active_hints = format_active_goal_shape_hints(goal)
    if active_hints:
        sections.extend(
            [
                "## Active goal-shape hints",
                active_hints,
                "",
            ]
        )
    sections.extend(
        [
            "## Top relevant premises",
            _format_premises(top_premises),
            "",
            "## Previously failed at this goal",
            _format_failures(failed_tactics),
        ]
    )
    if failed_tactics:
        banned = _banned_tactic_strings(failed_tactics)
        sections.extend(
            [
                "IMPORTANT: the harness will REJECT any tactic that matches a "
                "banned entry above after normalization (whitespace, trailing "
                "'.', and `&&`/`||` vs `/\\`/`\\/` do not create a new tactic). "
                "It is guaranteed to fail identically again at this exact goal. "
                "Choose a genuinely different head tactic, invariant, lemma "
                "argument, or break a compound into separate steps.",
                "Banned tactics at this goal:",
                *[f"- `{tactic}`" for tactic in banned],
            ]
        )
    prior = _format_recent_other_failures(recent_failures or [], failed_tactics)
    if prior:
        sections.extend(
            [
                "",
                "## Recent failures at earlier goals",
                (
                    "The goal text changed after an accepted tactic, so the "
                    "per-goal ban list above was reset. These recent failures "
                    "are still relevant context (informational — not hard-banned "
                    "at the new goal unless they also appear above):"
                ),
                prior,
            ]
        )
    sections.extend(
        [
            "",
            "## Recent reasoning and outcomes",
            _format_past_steps(past_steps or []),
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
    # Dedup by normalized tactic so repeated spam does not bloat the prompt
    # or reinforce the failing string. Keep first occurrence order.
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for error, tactic in failures:
        key = normalize_tactic(tactic)
        if key not in grouped:
            grouped[key] = {"tactic": tactic, "error": error, "count": 0}
            order.append(key)
        grouped[key]["count"] += 1
        # Prefer the first substantive EasyCrypt error over later reject notices.
        if grouped[key]["count"] == 1:
            grouped[key]["error"] = error
    lines = []
    for key in order:
        item = grouped[key]
        count = item["count"]
        prefix = f"{count}x " if count > 1 else ""
        lines.append(
            f"- {prefix}tactic `{item['tactic']}` -> error: {str(item['error']).strip()}"
        )
    return "\n".join(lines)


def _format_recent_other_failures(
    recent_failures: list[tuple[str, str]],
    current_failures: list[tuple[str, str]],
) -> str:
    """Render recent failures from earlier goals, skipping current-goal dupes."""
    if not recent_failures:
        return ""
    current_keys = {normalize_tactic(tactic) for _error, tactic in current_failures}
    lines: list[str] = []
    seen: set[str] = set()
    for error, tactic in recent_failures:
        key = normalize_tactic(tactic)
        if key in current_keys or key in seen:
            continue
        seen.add(key)
        err = str(error).strip()
        if len(err) > 800:
            err = err[:800] + "\n[truncated]"
        lines.append(f"- tactic `{tactic}` -> error: {err}")
    return "\n".join(lines)


def _banned_tactic_strings(failures: list[tuple[str, str]]) -> list[str]:
    seen: set[str] = set()
    banned: list[str] = []
    for _error, tactic in failures:
        key = normalize_tactic(tactic)
        if key in seen:
            continue
        seen.add(key)
        banned.append(tactic.strip())
    return banned


def _format_past_steps(steps: list[dict[str, Any]]) -> str:
    """Render bounded agent memory without replaying full prompts."""
    if not steps:
        return "(none)"
    lines: list[str] = []
    for item in steps:
        step = item.get("step", "?")
        action = item.get("action", "unknown")
        detail = (
            item.get("tactic")
            or item.get("lookup_name")
            or item.get("search_query")
            or ""
        )
        outcome = item.get("outcome", "unknown")
        lines.append(
            f"- step {step}: {action}"
            + (f" `{detail}`" if detail else "")
            + f" -> {outcome}"
        )
        thought = str(item.get("thought") or "").strip()
        if thought:
            if len(thought) > 2000:
                thought = thought[:2000] + "\n[truncated]"
            lines.append(f"  reasoning: {thought}")
        error = str(item.get("error") or "").strip()
        if error:
            if len(error) > 4000:
                error = error[:4000] + "\n[truncated]"
            lines.append(f"  feedback: {error}")
    return "\n".join(lines)

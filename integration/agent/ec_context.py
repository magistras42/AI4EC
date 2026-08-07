"""What names are in scope, and what kind each one is.

EasyCrypt prints the ambient context above the dashed rule of every goal::

    Current goal (remaining: 2)

    Type variables: <none>

    &m: {}
    q1_L: exp
    q2_L: exp
    &1: {choice, guess : bool, q1, q2 : exp, ...}
    &2: {choice, guess : bool, grp1, grp2 : group, ...}
    hpre: q2_L = q2{1} /\\
          q1_L = q1{1} /\\ ...
    ------------------------------------------------------------------------
    <the goal>

The model is shown this and does not act on it. Measured over 426 failures,
16 are name/scope errors and **10 of those are `an hypothesis or variable
named 'X' already exists`** -- the model re-introducing `&1`/`&2` or a
hypothesis that the block above already lists.

The remaining cases are a namespace confusion rather than a missing name. On
`G2_bad_ub` the model wrote `smt(hpre)` and got ``cannot find lemma `hpre'``
-- yet `hpre` WAS in scope, with its full statement printed. `smt(...)` takes
*library lemma* names; a local hypothesis is already part of the goal the
solver sees and must not be named there.

So this module does not fetch anything. Every fact it reports is already on
screen; it just states it in a form the model can act on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MEMORY = "memory"
VARIABLE = "variable"
HYPOTHESIS = "hypothesis"

_HEADER_RE = re.compile(
    r"^(?:Current goal(?:\s*\(remaining:\s*\d+\))?|No more goals)\s*$"
)
_TYPE_VARS_RE = re.compile(r"^Type variables:")
_RULE_RE = re.compile(r"^-{4,}\s*$")
#: `name: rest`, where name is an identifier or a memory. Memories are `&1`,
#: `&2`, `&m` -- the digit forms matter and an `&?[A-Za-z_]...` pattern misses
#: them, which silently folded `&1:`/`&2:` into the PREVIOUS entry's statement
#: and lost exactly the two names most often re-introduced by mistake.
_ENTRY_RE = re.compile(
    r"^(&[A-Za-z0-9_']+|[A-Za-z_][A-Za-z0-9_']*)\s*:\s*(.*)$"
)


@dataclass(frozen=True)
class ContextEntry:
    name: str
    kind: str
    statement: str

    @property
    def is_memory(self) -> bool:
        return self.kind == MEMORY


def _classify(name: str, statement: str) -> str:
    """Memory, program variable, or proof hypothesis.

    A memory is spelled `&…`. Otherwise the split is by what the statement
    looks like: a type expression (`exp`, `int`, `group fset`) is a variable,
    and anything carrying a logical connective or a memory projection is a
    hypothesis. Deliberately crude -- the consumer only needs "may I introduce
    this name" and "is this a lemma or a local fact", and both survive a
    misclassification between the two non-memory kinds.
    """
    if name.startswith("&"):
        return MEMORY
    if re.search(r"[=<>]|/\\|\\/|=>|\bforall\b|\bexists\b|\{[12]\}", statement):
        return HYPOTHESIS
    return VARIABLE


def parse_context(goal: str) -> list[ContextEntry]:
    """Names in scope for the ACTIVE goal, in the order EasyCrypt prints them.

    Everything between the `Type variables:` line and the dashed rule. Entries
    wrap across lines (a long hypothesis is indented under its name), so a line
    that does not start a new `name:` binding continues the previous one.
    """
    text = goal or ""
    if not text.strip():
        return []
    lines = text.splitlines()
    # Only the first block: after the rule comes the goal itself, and a second
    # rule would belong to an inactive subgoal.
    try:
        end = next(i for i, l in enumerate(lines) if _RULE_RE.match(l.strip()))
    except StopIteration:
        return []

    entries: list[ContextEntry] = []
    pending: tuple[str, list[str]] | None = None

    def flush() -> None:
        if pending is None:
            return
        name, parts = pending
        statement = " ".join(p.strip() for p in parts).strip()
        entries.append(ContextEntry(name, _classify(name, statement), statement))

    for raw in lines[:end]:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or _HEADER_RE.match(stripped) or _TYPE_VARS_RE.match(stripped):
            continue
        match = _ENTRY_RE.match(stripped)
        # A continuation line is INDENTED and does not open a new binding.
        if match and not (line[:1].isspace() and pending is not None
                          and not _ENTRY_RE.match(line.lstrip())):
            flush()
            pending = (match.group(1), [match.group(2)])
        elif pending is not None:
            pending[1].append(stripped)
    flush()
    return entries


def format_context_note(goal: str) -> str:
    """Tell the model which names exist, and which it must not re-introduce.

    Returns "" when the context is empty -- there is nothing to say, and an
    empty section on every ambient goal would be noise.
    """
    entries = parse_context(goal)
    if not entries:
        return ""

    memories = [e for e in entries if e.kind == MEMORY]
    hypotheses = [e for e in entries if e.kind == HYPOTHESIS]
    variables = [e for e in entries if e.kind == VARIABLE]

    lines = ["## Names already in scope", ""]
    if memories:
        lines.append(
            "Memories: " + ", ".join(f"`{e.name}`" for e in memories) + ". "
            "These are ALREADY introduced — `move => "
            + " ".join(e.name for e in memories if e.name != "&m")
            + "` will fail with `an hypothesis or variable named ... already "
            "exists`. Introduce only names NOT listed here."
        )
    if variables:
        lines.append(
            "Variables: "
            + ", ".join(f"`{e.name}` : {e.statement[:40]}" for e in variables[:8])
            + "."
        )
    if hypotheses:
        lines.append(
            "Hypotheses already available: "
            + ", ".join(f"`{e.name}`" for e in hypotheses[:8])
            + ". You can `apply`, `rewrite` or `case` on these by name. Do NOT "
            "pass them to `smt(...)` — its arguments are LIBRARY lemma names, "
            "and a local hypothesis there gives `cannot find lemma`. Local "
            "hypotheses are already visible to a bare `smt()`."
        )
    return "\n".join(lines)


def unknown_name_hint(goal: str, name: str) -> str:
    """Explain a `cannot find lemma 'X'` when X is actually in scope.

    The distinction the error message does not make: the name exists, it is
    just not a lemma. Returns "" when the name genuinely is not in context, so
    the caller falls back to a lookup suggestion.
    """
    entry = next((e for e in parse_context(goal) if e.name == name), None)
    if entry is None:
        return ""
    if entry.kind == HYPOTHESIS:
        return (
            f"`{name}` IS in scope — it is a local hypothesis, not a library "
            f"lemma, so `smt({name})` cannot resolve it. Local hypotheses are "
            f"already visible to a bare `smt()`. To use it explicitly, "
            f"`apply {name}`, `rewrite {name}` or `case: {name}`."
        )
    return (
        f"`{name}` IS in scope as a {entry.kind}, not a lemma. `smt(...)` "
        f"takes library lemma names only."
    )

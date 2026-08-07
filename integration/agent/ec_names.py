"""Resolve the names a tactic references, and suggest alternatives.

The model invents lemma names (`andP`, `hemma`) and confuses namespaces
(`smt(hpre)` where `hpre` is a local hypothesis). EasyCrypt reports both as
``cannot find lemma `X'``, which does not say which it is, so the model
searches the catalog for something that was never a lemma.

**This never blocks a tactic, and that is a measured decision rather than
caution.** The Ax.all catalog is NOT authoritative: `rpow_hmono` is used by
`G2_bad_ub`'s own original proof and replays successfully, yet does not appear
in the catalog at that cursor. A pre-check that rejected unknown names would
have blocked a tactic that works. Bare basenames are the same story -- the
catalog is keyed by qualified path, so `addr0`, `subzz`, `mem_empty` and
`dtext_ll` are all absent as keys while being perfectly usable.

So the job here is to make a failure *informative*, not to pre-empt it:

* a name in the goal's context is a local fact, not a lemma -- say so;
* a name in neither context nor catalog gets the nearest catalog entries, so
  a dead end becomes a correction;
* everything else stays silent.

Cost is a handful of dict lookups per tactic. Measured over every run, a
tactic that references a lemma-position name references a median of 1 (max 2),
so there is nothing exhaustive about it.
"""

from __future__ import annotations

import difflib
import re

from .ec_context import HYPOTHESIS, parse_context

#: Identifiers in a position where EasyCrypt expects a LEMMA. Deliberately
#: narrow: a tactic is full of variable names, constructors and binders, and
#: checking every token would produce noise about names that were never meant
#: to resolve to lemmas.
_LEMMA_POSITION_RE = re.compile(
    r"\b(?:apply|exact|rewrite)\b\s*[/:!-]*\s*([A-Za-z_][\w.']*)"
    r"|\bsmt\s*\(([^)]*)\)"
)
_IDENT_RE = re.compile(r"[A-Za-z_][\w.']*")
#: Tactic keywords that can follow `rewrite`/`apply` and are not lemma names.
_NOT_LEMMAS = frozenset({
    "in", "at", "by", "with", "if", "then", "else", "fun", "let", "and",
    "true", "false", "forall", "exists",
})


def referenced_lemma_names(tactic: str) -> list[str]:
    """Names the tactic uses where a lemma is expected, in order, deduped."""
    found: list[str] = []
    for match in _LEMMA_POSITION_RE.finditer(tactic or ""):
        if match.group(1):
            found.append(match.group(1))
        if match.group(2):
            found.extend(_IDENT_RE.findall(match.group(2)))
    out: list[str] = []
    for name in found:
        # EasyCrypt statements end in `.`; the capture takes it with them.
        name = name.rstrip(".")
        if not name or name in _NOT_LEMMAS or name in out:
            continue
        out.append(name)
    return out


def resolves(name: str, catalog: dict[str, str] | None) -> bool:
    """Is `name` in the catalog, qualified or as a bare basename?

    A False here means "the catalog does not know it", NOT "it does not
    exist" -- see the module docstring.
    """
    if not catalog:
        return False
    if name in catalog:
        return True
    base = name.split(".")[-1]
    return any(key.split(".")[-1] == base for key in catalog)


def suggest(name: str, catalog: dict[str, str] | None, limit: int = 5) -> list[str]:
    """Closest catalog entries to `name`, best first.

    Matched on the BASENAME. The model writes bare names and the catalog is
    keyed by qualified path, so comparing whole keys would rank on the theory
    prefix -- which is the part the model did not write.
    """
    if not catalog:
        return []
    target = name.split(".")[-1].lower()
    by_base: dict[str, str] = {}
    for key in catalog:
        by_base.setdefault(key.split(".")[-1].lower(), key)
    close = difflib.get_close_matches(target, list(by_base), n=limit, cutoff=0.6)
    hits = [by_base[c] for c in close]
    if len(hits) < limit:
        # Substring fallback: `mem_rng_empty` should still reach `mem_empty`
        # even when the edit distance is too large for the ratio cutoff.
        for base, key in by_base.items():
            if len(hits) >= limit:
                break
            if key not in hits and (target in base or base in target):
                hits.append(key)
    return hits


#: Errors that actually mean "this name did not resolve". The catalog-miss
#: advice is gated on one of these appearing, because the catalog has false
#: negatives: `apply rpow_hmono.` is absent from Ax.all yet is part of
#: `G2_bad_ub`'s original proof and replays successfully. Ungated, it would be
#: told its working tactic used a bad name -- and a prompt that states
#: something false gets believed, as the `smt(a, b)` comma showed.
_NAME_ERROR_RE = re.compile(
    r"cannot find (?:lemma|symbol)|unknown (?:lemma|symbol|operator)|"
    r"unbound|not found",
    re.IGNORECASE,
)


def is_name_error(error: str) -> bool:
    return bool(_NAME_ERROR_RE.search(error or ""))


def name_advice(
    tactic: str,
    goal: str,
    catalog: dict[str, str] | None,
    error: str = "",
) -> str:
    """One advisory line per name that will not resolve as a lemma, or "".

    Called after a failure rather than before applying: EasyCrypt costs ~1.5s
    and the catalog has false negatives, so pre-empting would trade a cheap
    call for a wrong rejection.

    The in-scope explanation is always safe -- it reads the goal, not the
    catalog. The catalog-miss suggestion needs `error` to look like a name
    error, or it fires on working tactics.
    """
    names = referenced_lemma_names(tactic)
    if not names:
        return ""
    context = {entry.name: entry for entry in parse_context(goal)}
    lines: list[str] = []
    for name in names:
        entry = context.get(name)
        if entry is not None:
            kind = "local hypothesis" if entry.kind == HYPOTHESIS else entry.kind
            lines.append(
                f"`{name}` is a {kind} already in scope, not a library lemma. "
                f"`smt(...)` takes lemma names only — local facts are already "
                f"visible to a bare `smt()`; to use it explicitly try "
                f"`apply {name}` / `rewrite {name}` / `case: {name}`."
            )
            continue
        # No catalog means no basis for ANY claim about the name -- saying
        # "nothing similar exists" would be asserting a fact we cannot check.
        if not catalog or resolves(name, catalog) or not is_name_error(error):
            continue
        options = suggest(name, catalog)
        if options:
            lines.append(
                f"`{name}` is not in the lemma catalog and not in scope. "
                f"Closest catalog entries: {', '.join(options)}. "
                f"Use `search_lemmas` if none of these is right."
            )
        else:
            lines.append(
                f"`{name}` is not in the lemma catalog and not in scope, and "
                f"nothing similar is either. Do not retry it — use "
                f"`search_lemmas` to find the real name first."
            )
    return "\n".join(lines)

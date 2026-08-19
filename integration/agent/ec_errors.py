"""Classify EasyCrypt failures by *kind*, not just by line number.

Roadmap item W4 step 1 in [`docs/PROOF_REPAIR_HANDOFF.md`](../../docs/PROOF_REPAIR_HANDOFF.md).
``import_repair.py`` previously extracted only the first error's line number
(``_ERROR_LINE_RES``), so rule selection could not be driven by what actually
went wrong: a missing theory, an unknown symbol, a parse failure and a broken
tactic were all just "an error at line N".

The distinction that matters most to this project is **pre-proof vs
in-proof**. A file that will not LOAD (unknown theory, parse error, unknown
symbol in a declaration) is an import-repair problem and the changelog
knowledge base is the right tool. A file that loads fine but whose tactic
fails is a proof problem, and no amount of import rewriting will help --
that boundary is exactly where §4.4's ElGamal measurement stops (first error
moves 108 -> 357 -> 453, and 453 is a bad `seq 1 1 :` argument, i.e. a
genuinely broken proof).

Patterns are matched against EasyCrypt's real output. Two output shapes exist
in the wild and both are handled here, the same way ``_ERROR_LINE_RES``
handles both after the bug described in W4 step 4::

    path.ec:108: parse error
    [critical] [path.ec: line 108 (8)] cannot find theory: `SmtMap'

Unrecognized text classifies as ``unknown`` rather than raising: this is a
heuristic layer over human-readable compiler output, and a caller must never
lose a real failure because the wording changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# --- error kinds ------------------------------------------------------------
# Pre-proof kinds block the file from loading at all; in-proof kinds mean the
# declarations are fine and a tactic is at fault.
KIND_UNKNOWN_THEORY = "unknown_theory"
KIND_UNKNOWN_SYMBOL = "unknown_symbol"
KIND_PARSE_ERROR = "parse_error"
KIND_TYPE_ERROR = "type_error"
KIND_TACTIC_ERROR = "tactic_error"
KIND_PROOF_INCOMPLETE = "proof_incomplete"
KIND_UNKNOWN = "unknown"

PRE_PROOF_KINDS = frozenset(
    {KIND_UNKNOWN_THEORY, KIND_UNKNOWN_SYMBOL, KIND_PARSE_ERROR, KIND_TYPE_ERROR}
)
IN_PROOF_KINDS = frozenset({KIND_TACTIC_ERROR, KIND_PROOF_INCOMPLETE})

# `path.ec:108:` and `[critical] [path.ec: line 108 (8)]`. Both shapes appear
# depending on which EasyCrypt entry point produced the message.
_ERROR_LINE_RES = (
    re.compile(r"^\s*\[?[^\s\]]*\.eca?:(\d+)", re.MULTILINE),
    re.compile(r"\[[^\]]*\.eca?:\s*line\s+(\d+)", re.IGNORECASE),
)

# Ordered most-specific first: an "unknown theory" message also contains the
# word "cannot", so a looser pattern must never win the race.
_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        KIND_UNKNOWN_THEORY,
        re.compile(
            r"cannot find (?:the )?theory|unknown theory|theory .* not found|"
            r"cannot find file for theory|can not find theory",
            re.IGNORECASE,
        ),
    ),
    (
        KIND_UNKNOWN_SYMBOL,
        re.compile(
            r"unknown (?:operator|symbol|identifier|predicate|type|module|lemma|axiom)|"
            r"cannot find (?:operator|symbol|identifier|lemma)|"
            r"unbound (?:identifier|variable)|undeclared identifier",
            re.IGNORECASE,
        ),
    ),
    (
        KIND_PARSE_ERROR,
        re.compile(r"parse error|syntax error|lexical error", re.IGNORECASE),
    ),
    (
        KIND_TYPE_ERROR,
        re.compile(
            r"type error|does not (?:have|match) type|"
            r"the term has type|cannot unify|ill-typed|invalid type",
            re.IGNORECASE,
        ),
    ),
    (
        KIND_PROOF_INCOMPLETE,
        re.compile(
            r"cannot prove goal|proof is incomplete|incomplete proof|"
            r"the proof is not closed|remaining goals",
            re.IGNORECASE,
        ),
    ),
    (
        KIND_TACTIC_ERROR,
        # EasyCrypt quotes names Lisp-style -- `position' -- so the quote
        # character class must accept a backtick. Without it every
        # ``invalid `position' parameter`` fell through to `unknown`, which is
        # a *pre-proof* answer: the failure that most clearly belongs to the
        # solver was being reported as one that might belong to import repair.
        re.compile(
            r"invalid\s+[`'\"]?(?:goal shape|position|argument|tactic)|"
            r"tactic failure|no (?:such|more) goal|"
            # "expecting a `memory', not a `formula'" -- a tactic argument of
            # the wrong syntactic class. In-proof despite reading like a type
            # error, and `type_error` is pre-proof.
            r"expecting an? [`'\"]?\w+['\"]?,\s*not an? |"
            r"cannot apply|not applicable|unable to apply",
            re.IGNORECASE,
        ),
    ),
)

# Symbols named inside quotes/backticks in the message, e.g. ``SmtMap'`` or
# `dmap`. These are what get handed to the symbol->theory index.
_QUOTED_NAME_RES = (
    re.compile(r"`([A-Za-z_][A-Za-z0-9_.']*)'"),
    re.compile(r"\"([A-Za-z_][A-Za-z0-9_.']*)\""),
)


@dataclass(frozen=True)
class ClassifiedError:
    """One EasyCrypt failure, classified."""

    kind: str
    line: int
    message: str
    identifiers: tuple[str, ...] = ()

    @property
    def is_pre_proof(self) -> bool:
        """True when the file failed to LOAD, i.e. import repair is in scope."""
        return self.kind in PRE_PROOF_KINDS

    @property
    def is_in_proof(self) -> bool:
        """True when declarations loaded and a tactic is at fault."""
        return self.kind in IN_PROOF_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "line": self.line,
            "is_pre_proof": self.is_pre_proof,
            "identifiers": list(self.identifiers),
            "message": self.message[:500],
        }


def first_error_line(output: str) -> int:
    """Line of EasyCrypt's first complaint, or -1 when none was reported."""
    for pattern in _ERROR_LINE_RES:
        match = pattern.search(output or "")
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return -1


def extract_identifiers(output: str) -> tuple[str, ...]:
    """Quoted names EasyCrypt blamed, de-duplicated, order preserved."""
    seen: list[str] = []
    for pattern in _QUOTED_NAME_RES:
        for match in pattern.finditer(output or ""):
            name = match.group(1).rstrip("'")
            if name and name not in seen:
                seen.append(name)
    return tuple(seen)


def classify_error(output: str) -> ClassifiedError:
    """Classify EasyCrypt's output into one :class:`ClassifiedError`.

    Always returns a value. Text that matches no known pattern comes back as
    ``KIND_UNKNOWN``, which callers must treat as "could be anything" and
    therefore fail open rather than skipping work.
    """
    text = output or ""
    kind = KIND_UNKNOWN
    for candidate, pattern in _KIND_PATTERNS:
        if pattern.search(text):
            kind = candidate
            break

    message = _first_meaningful_line(text)
    return ClassifiedError(
        kind=kind,
        line=first_error_line(text),
        message=message,
        identifiers=extract_identifiers(text),
    )


def _first_meaningful_line(text: str) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


_WARNING_LINE_RE = re.compile(r"^\s*\[warning\]", re.IGNORECASE)


def strip_warning_lines(output: str) -> str:
    """Drop ``[warning]`` lines from EasyCrypt output.

    EasyCrypt re-emits every file-level warning on *every* invocation, so the
    same notices ride along with each tactic failure. Measured over the
    captured runs these were 106 of 193 error lines -- 55% -- and they are
    identical every time (``global axiom Adv_choose_ll in section`` and its
    twin), carrying no information about why the tactic failed.

    They are noise for a reader, but the reason to remove them is sharper than
    tidiness: this text is the agent's only feedback signal, and burying the
    one ``[critical]`` line under repeated unrelated notices invites the model
    to "fix" a warning instead of the failure.

    If a message consists solely of warnings, it is returned unchanged --
    something is better than nothing.
    """
    text = output or ""
    kept = [line for line in text.splitlines() if not _WARNING_LINE_RE.match(line)]
    if not any(line.strip() for line in kept):
        return text
    return "\n".join(kept).strip()


def is_load_failure(output: str) -> bool:
    """Whether this output means "the file did not load".

    ``KIND_UNKNOWN`` counts as a load failure when the caller already knows
    EasyCrypt exited nonzero before any goal was reachable -- callers pass
    output they obtained in exactly that situation.
    """
    classified = classify_error(output)
    return classified.is_pre_proof or classified.kind == KIND_UNKNOWN

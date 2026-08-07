"""Read EasyCrypt's two-column program dump into positioned statements.

Position errors -- the right tactic aimed at the wrong place -- are ~45% of
measured tactic failures, and the biggest single contributor is a `seq N M`
with indices that do not exist. The prompt has always reported instruction
counts for this, and it counted the wrong thing: it counted *lines that look
like instructions*, which conflates a statement with the sub-statements nested
inside it. On a real ElGamal goal the model was told ``left: 15, right: 15``
directly above the sentence "N/M must not exceed these counts", when the true
top-level counts were 13 and 12. Both stated maxima were out of range.

What EasyCrypt actually prints is a shared index column between the two
programs::

    x <- pubk0 ^ r                    ( 9--)  if (g ^ (q1 * q2) \\notin RO.mp) {
                                      ( 9.1)    y <$ dtext
                                      ( 9.2)    RO.mp <-
                                      (    )      RO.mp.[g ^ (q1 * q2) <- y]
    if (x \\notin RO.mp) {            (10--)  u <- oget RO.mp.[g ^ (q1 * q2)]

``( N--)`` (or ``( N)`` when no statement on either side has a body) opens
top-level statement N. ``( N.k)`` is a sub-statement inside it, and ``(    )``
is a wrapped continuation line. Only the first form is a `seq` position, so
counting rows by shape instead of by index is what produced the 15s.

The matched-call cut point below is ported from
`shannon-prover/core/easycrypt/analysis/ec_goal_parser.py::_compute_seq_suggestions`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

SAMPLE = "SAMPLE"
ASSIGN = "ASSIGN"
CALL = "CALL"
WHILE = "WHILE"
IF = "IF"
OTHER = "OTHER"

# A numbered marker: a top-level position (`( 9--)` / `( 9)`) or a
# sub-statement (`( 9.1)`). Only these vote on where the index column is --
# program text is full of parens, and an EMPTY pair is the worst of them: a
# no-argument call `M.f()` would otherwise cast a vote on every row it appears
# in, and two such rows outvote nothing.
_NUMBERED_RE = re.compile(r"\(\s*(\d+)(--|\.\d+)?\s*\)")
# A wrapped continuation line, `(    )`. At least one space, so `()` is not one.
_BLANK_RE = re.compile(r"\(\s+\)")

_SAMPLE_RE = re.compile(r"<\$")
_CALL_RE = re.compile(r"<@")
_ASSIGN_RE = re.compile(r"<-")
_WHILE_RE = re.compile(r"\bwhile\s*\(")
_IF_RE = re.compile(r"\bif\s*\(")


@dataclass(frozen=True)
class Statement:
    """One top-level instruction, at the position `seq` would address."""

    position: int
    kind: str
    text: str
    procedure: str | None = None


@dataclass(frozen=True)
class SeqCandidate:
    left_pos: int
    right_pos: int
    procedure: str
    tactic: str


@dataclass(frozen=True)
class ProgramPair:
    left: tuple[Statement, ...] = ()
    right: tuple[Statement, ...] = ()
    #: False when EasyCrypt printed no index column, so no position is known.
    #: Callers must not state counts in that case -- a wrong maximum is what
    #: this module exists to stop.
    indexed: bool = False

    @property
    def is_equiv(self) -> bool:
        return bool(self.left) and bool(self.right)


def _classify(text: str) -> str:
    """Name one instruction.

    Assignment is tested before the block forms on purpose: `t <- if choice
    then x1 else x2` is an assignment whose right-hand side happens to contain
    the word `if`, and calling it a conditional would send the model looking
    for an `rcondt` position that is not there.
    """
    if not text.strip():
        return OTHER
    if _SAMPLE_RE.search(text):
        return SAMPLE
    if _CALL_RE.search(text):
        return CALL
    if _ASSIGN_RE.search(text):
        return ASSIGN
    if _WHILE_RE.search(text):
        return WHILE
    if _IF_RE.search(text):
        return IF
    return OTHER


def _callee(text: str) -> str | None:
    """Base name of the procedure a `<@` call targets.

    `guess <@ INDCPA(HEG, Adv).A.guess(c)` is `guess`. The functor arguments
    make a plain `split(".")` wrong, so the argument list is removed by
    matching parens first and the qualifier split only looks outside them.

    Returns None when the dump truncated the callee -- EasyCrypt clips the left
    column to its width, and `(x1, x2) <@` with nothing after it is a real and
    common case, not a parse failure.
    """
    if "<@" not in text:
        return None
    target = text.split("<@", 1)[1].strip()
    if not target:
        return None
    # Drop a trailing argument list by walking back from its closing paren.
    if target.endswith(")"):
        depth = 0
        for i in range(len(target) - 1, -1, -1):
            if target[i] == ")":
                depth += 1
            elif target[i] == "(":
                depth -= 1
                if depth == 0:
                    target = target[:i]
                    break
    depth = 0
    last_dot = -1
    for i, char in enumerate(target):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "." and depth == 0:
            last_dot = i
    name = target[last_dot + 1:].strip()
    return name or None


def _marker_column(lines: list[str]) -> int | None:
    """Character offset of the index column, by majority vote across rows.

    Voting rather than taking the first match because program text carries
    parenthesised digits of its own -- `(x1, x2)`, `(g ^ q, q)` -- and a lone
    candidate on one row is not evidence. EasyCrypt pads every row to the same
    width, so the true column is the modal one.
    """
    votes: Counter[int] = Counter()
    for line in lines:
        for match in _NUMBERED_RE.finditer(line):
            votes[match.start()] += 1
    if not votes:
        return None
    column, count = votes.most_common(1)[0]
    # Two rows agreeing is a column. One row cannot corroborate itself, so a
    # lone candidate is only trusted when there is a single row to read --
    # 138 of 683 real blocks are exactly that, one statement wide.
    if count >= 2 or len(lines) == 1:
        return column
    return None


def parse_program_block(block: str) -> ProgramPair:
    """Split a statement block into positioned statements per side."""
    lines = [ln for ln in (block or "").splitlines() if ln.strip()]
    if not lines:
        return ProgramPair()

    column = _marker_column(lines)
    if column is None:
        return ProgramPair()

    left: list[Statement] = []
    right: list[Statement] = []
    for line in lines:
        match = _NUMBERED_RE.match(line, column) or _BLANK_RE.match(line, column)
        if match is None:
            continue
        # Only a bare number opens a top-level statement; `9.1` is nested and
        # a blank marker is a wrapped line. Both still had to be matched, so
        # their text is dropped rather than read as the next statement.
        if match.re is _BLANK_RE:
            continue
        number, suffix = match.group(1), match.group(2)
        if suffix and suffix != "--":
            continue
        position = int(number)
        left_text = line[:column].strip()
        right_text = line[match.end():].strip()
        if left_text:
            left.append(
                Statement(position, _classify(left_text), left_text,
                          _callee(left_text))
            )
        if right_text:
            right.append(
                Statement(position, _classify(right_text), right_text,
                          _callee(right_text))
            )

    return ProgramPair(left=tuple(left), right=tuple(right), indexed=True)


def seq_candidates(pair: ProgramPair) -> list[SeqCandidate]:
    """Cut points where both sides call the same procedure.

    Ported from `_compute_seq_suggestions`. When the two programs diverge in
    length but share a call to the same procedure, that call is the cut the
    model is otherwise guessing at: `seq L R : (inv)` splits the prefixes so
    `call`/`auto` can discharge them together.

    Each right-hand call is consumed once, so a procedure called twice pairs up
    in order rather than proposing the same cut repeatedly.
    """
    left_calls = [s for s in pair.left if s.kind == CALL and s.procedure]
    right_calls = [s for s in pair.right if s.kind == CALL and s.procedure]
    used: set[int] = set()
    out: list[SeqCandidate] = []
    for left_stmt in left_calls:
        for right_stmt in right_calls:
            if right_stmt.position in used:
                continue
            if left_stmt.procedure != right_stmt.procedure:
                continue
            out.append(
                SeqCandidate(
                    left_pos=left_stmt.position,
                    right_pos=right_stmt.position,
                    procedure=left_stmt.procedure or "",
                    tactic=(
                        f"seq {left_stmt.position} {right_stmt.position} "
                        ": (<invariant>)."
                    ),
                )
            )
            used.add(right_stmt.position)
            break
    return out


def common_prefix_length(pair: ProgramPair) -> int:
    """How many leading statements agree in kind and callee.

    Where this stops is where the two programs start to differ, which is the
    other natural `seq` cut and the one to use when no call is shared.
    """
    length = 0
    for left_stmt, right_stmt in zip(pair.left, pair.right):
        if left_stmt.kind != right_stmt.kind:
            break
        if left_stmt.procedure != right_stmt.procedure:
            break
        length += 1
    return length

# ---------------------------------------------------------------------------
# EasyCrypt's own position limit
# ---------------------------------------------------------------------------

#: `invalid split index: ^<5` -- EasyCrypt naming the largest index it will
#: accept, exclusive.
_SPLIT_LIMIT_RE = re.compile(r"invalid split index:\s*\^<\s*(\d+)")


def split_index_limit(error: str) -> int | None:
    """The exclusive upper bound EasyCrypt reported, or None.

    The statement counts this module derives are a CEILING, not an admissible
    range: 11 of 12 failed `seq` attempts on `INDCPA_HEG_G1` used indices
    inside the counts and were rejected anyway. Twice EasyCrypt named its own
    far smaller limit -- `^<5` and `^<4` against counts of 13 and 12 -- which
    is strictly better information than anything computed here, and was being
    discarded with the rest of the error text.
    """
    match = _SPLIT_LIMIT_RE.search(error or "")
    return int(match.group(1)) if match else None

"""What did the last tactic structurally do to the goal?

Ported from `shannon-prover/core/easycrypt/analysis/ec_state_diff.py` and
adapted to our goal format. Compares the goal before and after one accepted
tactic along structural metrics -- subgoal count first, then `Pr[..]` terms,
top-level quantifiers, module-chain nesting and top-level connectives -- plus a
small set of *cosmetic noise* patterns (beta-redex, unreduced `glob` chains,
eta wrappers, iota match-redexes), and names the transition:

    PROGRESS                       the proof closed, or a metric came down
    PROGRESS_DECOMPOSITION         subgoal count went UP and the text moved
    PROGRESS_WITH_COSMETIC_NOISE   progress, but the text got uglier with it
    REGRESSION                     subgoal count went up on a no-op body
    NEUTRAL_OR_NO_CHANGE           the goal text is byte-identical
    UNCLASSIFIED                   the text moved but no metric did

Why this exists: ~50% of the tactics the model proposes change nothing, and
detecting that after the fact never made the next proposal better. The harness
tells the model *that* a tactic was inert but never what a productive one did,
so `PROGRESS_DECOMPOSITION` in particular -- a `seq`/`case`/`if` that triples
the subgoal count -- reads to a model exactly like a regression. This module
supplies the missing description.

WHAT THIS IS NOT
----------------
It is **not** an inertness oracle, and it must not be wired into
`loop.confirm_noop` as one. Two measurements say so:

* On 525 accepted transitions harvested from real runs, the number where the
  goal text was byte-identical but the subgoal count moved is **0**. Subgoal
  count is strictly implied by text equality, so it can never rescue a tactic
  that text comparison already condemns.
* The converse is not rare: **113** of those 525 moved the goal text with every
  structural metric flat. A count-first rule calls all 113 inert.

The upstream module returns ``NEUTRAL_OR_NO_CHANGE`` for that second case ("no
discriminating signal"), which is safe when the verdict only decides whether to
print a hint and unsafe when it decides whether to delete a line. We return
``UNCLASSIFIED`` instead and say nothing. See
`tests/test_goal_diff.py::test_a_moved_goal_with_flat_metrics_is_not_called_inert`
and the `skip.` counterexample in `test_noop_tactics.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .prompt import active_goal_text

PROGRESS = "PROGRESS"
PROGRESS_DECOMPOSITION = "PROGRESS_DECOMPOSITION"
PROGRESS_WITH_COSMETIC_NOISE = "PROGRESS_WITH_COSMETIC_NOISE"
REGRESSION = "REGRESSION"
NEUTRAL_OR_NO_CHANGE = "NEUTRAL_OR_NO_CHANGE"
UNCLASSIFIED = "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------

_PROMPT_RE = re.compile(r"^\[\d+\|[^\]]*\]>\s*$")
_HEADER_RE = re.compile(
    r"^(?:Current goal(?:\s*\(remaining:\s*\d+\))?|No more goals)\s*$"
)
_TYPE_VARS_RE = re.compile(r"^Type variables:.*$")
_RULE_RE = re.compile(r"^-{4,}\s*$")


def _strip_chrome(text: str, *, drop_header: bool) -> str:
    """Drop the session furniture EasyCrypt prints around every goal.

    `drop_header` decides the fate of the `Current goal (remaining: N)` line,
    and the two callers want opposite things. Body metrics must not see it --
    it is not part of the formula. Text comparison must, because it carries the
    subgoal count: strip it and a step that discharged two of three subgoals
    while leaving the active one alone compares byte-identical, i.e. reads as
    inert when it was the opposite.
    """
    if not text:
        return ""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if (
            _PROMPT_RE.match(stripped)
            or _TYPE_VARS_RE.match(stripped)
            or _RULE_RE.match(stripped)
        ):
            continue
        if drop_header and _HEADER_RE.match(stripped):
            continue
        out.append(line)
    return "\n".join(out)


def _goal_body(goal: str) -> str:
    """Body of the ACTIVE subgoal only.

    Scoping matters more here than upstream: EasyCrypt prints every open goal,
    and on our runs 90% of prompts carry more than one while 70% of the text
    belongs to an inactive one. Metrics over the whole dump would move whenever
    any goal moved, and attribute it to the tactic.
    """
    return _strip_chrome(active_goal_text(goal or ""), drop_header=True)


def _normalize(text: str) -> str:
    """The whole displayed state, up to whitespace.

    Keeps the goal header, so equality here is strictly stronger than equality
    of the subgoal count -- the property the module docstring relies on when it
    says the count can never rescue a tactic this comparison condemns.
    """
    lines = [
        ln.rstrip()
        for ln in _strip_chrome(text or "", drop_header=False).splitlines()
    ]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_REMAINING_RE = re.compile(r"Current goal\s*\(remaining:\s*(\d+)\)")
_NO_MORE_RE = re.compile(r"No more goals")
_BARE_GOAL_RE = re.compile(r"Current goal(?!\s*\(remaining)")


def subgoal_count(goal: str) -> int:
    """How many goals are open, per the LAST state marker in the text.

    Later markers win: a goal dump can carry earlier ones as replay chatter.
    ``No more goals`` is 0, ``(remaining: N)`` is N, and a bare ``Current
    goal`` is 1 -- 16% of our real goals print the bare form, so treating an
    absent `remaining:` as "unknown" would blind the metric on a sixth of the
    corpus.

    Differs from `prompt.count_subgoals`, which is a display helper and reports
    1 for any non-empty text: here ``No more goals`` must come out 0, because
    "the proof closed" is the one unambiguous PROGRESS signal.
    """
    if not goal:
        return 0
    if not goal.strip():
        return 0
    found: list[tuple[int, int]] = []
    for match in _REMAINING_RE.finditer(goal):
        found.append((match.start(), int(match.group(1))))
    for match in _BARE_GOAL_RE.finditer(goal):
        found.append((match.start(), 1))
    for match in _NO_MORE_RE.finditer(goal):
        found.append((match.start(), 0))
    if not found:
        return 1
    found.sort()
    return found[-1][1]


_PR_OPEN_RE = re.compile(r"\bPr\s*\[")


def _count_top_level_pr(body: str) -> int:
    """Count `Pr[...]` terms that are not nested inside another `Pr[...]`."""
    if not body:
        return 0
    count = 0
    i = 0
    length = len(body)
    while i < length:
        match = _PR_OPEN_RE.search(body, i)
        if not match:
            break
        count += 1
        open_idx = body.find("[", match.start())
        if open_idx < 0:
            break
        depth = 0
        j = open_idx
        while j < length:
            if body[j] == "[":
                depth += 1
            elif body[j] == "]":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        i = j
    return count


def _count_top_level_quantifiers(body: str) -> int:
    """Count `forall`/`exists` outside any `fun .. =>` body and any `[...]`.

    The bracket exclusion keeps a quantifier inside a `Pr[A : forall x, P x]`
    event spec from counting: it is part of the probability expression, not
    structure the model can attack with `move=>`.
    """
    if not body:
        return 0
    count = 0
    paren_depth = 0
    bracket_depth = 0
    fun_depths: list[int] = []
    i = 0
    length = len(body)
    while i < length:
        char = body[i]
        if char == "(":
            paren_depth += 1
            i += 1
            continue
        if char == ")":
            while fun_depths and fun_depths[-1] >= paren_depth:
                fun_depths.pop()
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue
        if char == "[":
            bracket_depth += 1
            i += 1
            continue
        if char == "]":
            bracket_depth = max(0, bracket_depth - 1)
            i += 1
            continue
        if char.isalpha() or char == "_":
            j = i
            while j < length and (body[j].isalnum() or body[j] == "_"):
                j += 1
            word = body[i:j]
            if word == "fun":
                fun_depths.append(paren_depth)
            elif word in ("forall", "exists"):
                if not fun_depths and bracket_depth == 0:
                    count += 1
            i = j
            continue
        i += 1
    return count


_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")


def _max_module_chain_depth(body: str) -> int:
    """Deepest `M(N(O(..)))` functor chain in the body, 0 if there is none.

    Only the leading argument is descended into, so an ordinary application
    `f(x, y)` stays at depth 1 and `RO_Pair(A.RO, B.RO)` does not read as
    deeper than it is.
    """
    if not body:
        return 0
    deepest = 0
    pos = 0
    length = len(body)
    while pos < length:
        match = _NAME_RE.match(body, pos)
        if not match:
            pos += 1
            continue
        end = match.end()
        if end < length and body[end] == "(":
            deepest = max(deepest, _scan_chain_depth(body, end))
            pos = end + 1
        else:
            pos = end
    return deepest


def _scan_chain_depth(body: str, lparen_idx: int) -> int:
    """Chain depth (>=1) starting at the `(` that follows a name."""
    length = len(body)
    depth = 1
    i = lparen_idx + 1
    while i < length:
        while i < length and body[i].isspace():
            i += 1
        if i >= length:
            break
        name = _NAME_RE.match(body, i)
        if not name:
            break
        i = name.end()
        if i < length and body[i] == "(":
            depth += 1
            i += 1
            continue
        break
    return depth


_CONNECTIVES = ("/\\", "\\/", "=>", "<=", "=")


def _count_top_connectives(body: str) -> int:
    """Count EasyCrypt's logical connectives at paren and bracket depth 0.

    Longest-match order matters: `=>` and `<=` must be consumed whole or each
    would also score as a bare `=`.
    """
    if not body:
        return 0
    count = 0
    paren_depth = 0
    bracket_depth = 0
    i = 0
    length = len(body)
    while i < length:
        char = body[i]
        if char == "(":
            paren_depth += 1
            i += 1
            continue
        if char == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue
        if char == "[":
            bracket_depth += 1
            i += 1
            continue
        if char == "]":
            bracket_depth = max(0, bracket_depth - 1)
            i += 1
            continue
        if paren_depth == 0 and bracket_depth == 0:
            for token in _CONNECTIVES:
                if body.startswith(token, i):
                    count += 1
                    i += len(token)
                    break
            else:
                i += 1
        else:
            i += 1
    return count


# ---------------------------------------------------------------------------
# Cosmetic noise
# ---------------------------------------------------------------------------

_BETA_RE = re.compile(r"\(\s*fun\b[^()]*=>[^()]*\)\s*[(\w]")


def _detect_beta_redex(body: str) -> bool:
    """True when an applied lambda `(fun x => ..) arg` is present."""
    if not body:
        return False
    if _BETA_RE.search(body):
        return True
    # The regex above cannot see a lambda whose body contains parens, so walk
    # balanced parens as a backstop.
    i = 0
    length = len(body)
    while True:
        idx = body.find("(fun", i)
        if idx < 0:
            return False
        if idx + 4 < length and (body[idx + 4].isalnum() or body[idx + 4] == "_"):
            i = idx + 1
            continue
        depth = 1
        j = idx + 1
        saw_arrow = False
        while j < length and depth > 0:
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            elif body[j] == "=" and j + 1 < length and body[j + 1] == ">":
                saw_arrow = True
            j += 1
        if depth != 0 or not saw_arrow:
            i = idx + 1
            continue
        k = j + 1
        while k < length and body[k] in " \t":
            k += 1
        if k < length and (body[k] == "(" or body[k].isalnum() or body[k] == "_"):
            return True
        i = j + 1


_GLOB_HEAD_RE = re.compile(r"\(\s*glob\s+")


def _detect_unreduced_glob(body: str) -> bool:
    """True when a `(glob M(N(O..)))` chain is still three or more deep.

    The module expression is captured by walking balanced parens rather than by
    regex. Upstream matches it with `[A-Za-z_]\\w*(?:\\([^()]*\\))?`, which
    forbids a nested paren -- so the deepest chain it can ever capture is
    `M(N)`, depth 2, and the `>= 3` test below could not fire at all.
    """
    if not body:
        return False
    length = len(body)
    for match in _GLOB_HEAD_RE.finditer(body):
        i = match.end()
        name = _NAME_RE.match(body, i)
        if not name:
            continue
        end = name.end()
        if end < length and body[end] == "(":
            depth = 1
            j = end + 1
            while j < length and depth > 0:
                if body[j] == "(":
                    depth += 1
                elif body[j] == ")":
                    depth -= 1
                j += 1
            end = j
        if _max_module_chain_depth(body[i:end]) >= 3:
            return True
    return False


_ETA_RE = re.compile(r"\bfun\s+([A-Za-z_]\w*)\s*=>\s*([A-Za-z_][\w.]*)\s+\1\b")


def _detect_eta_expansion(body: str) -> bool:
    """True when a `fun x => f x` wrapper is present (`fun x => x x` is not)."""
    if not body:
        return False
    return any(m.group(1) != m.group(2) for m in _ETA_RE.finditer(body))


_IOTA_RE = re.compile(r"\bmatch\b[^.]*?\bwith\b[^.]*?\bend\b", re.DOTALL)


def _detect_iota_redex(body: str) -> bool:
    if not body:
        return False
    return bool(_IOTA_RE.search(body))


_NOISE_CHECKS = (
    ("beta_redex", _detect_beta_redex),
    ("unreduced_glob", _detect_unreduced_glob),
    ("eta_expansion", _detect_eta_expansion),
    ("iota_redex", _detect_iota_redex),
)

_NOISE_FIX = {
    "beta_redex": (
        "an applied lambda `(fun .. => ..) arg` -- reduce it with `simplify`, "
        "`=> /=`, or close with `auto => />`"
    ),
    "unreduced_glob": (
        "a `(glob M(N(O..)))` chain -- it usually disappears once `proc` or "
        "`byequiv` lifts the goal into the program"
    ),
    "eta_expansion": (
        "an eta-expanded `fun x => f x` -- `simplify` or `congr` collapses it"
    ),
    "iota_redex": (
        "a `match` on a known constructor -- `simplify` reduces it"
    ),
}


def _new_noise(post_body: str, pre_body: str) -> list[str]:
    """Noise tags the tactic INTRODUCED.

    Pre-existing noise is excluded: reporting it would blame this tactic for
    something it neither created nor cleaned up.
    """
    return [
        tag
        for tag, detect in _NOISE_CHECKS
        if detect(post_body) and not detect(pre_body)
    ]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

_METRIC_LABELS = (
    ("subgoals_count", "subgoals"),
    ("pr_terms_count", "Pr[] terms"),
    ("quantifiers_count", "quantifiers"),
    ("module_depth_max", "module depth"),
    ("top_connectives_count", "connectives"),
)

# Tactics whose whole job is to split one goal into several. Upstream dropped
# this list because a name heuristic kept missing `congr` / `call (_: Inv)` /
# `have h : P` / `while (I)`, and let any count increase on changed text read
# as decomposition. We keep the list for the REGRESSION arm only -- see
# `_verdict` -- so a miss costs a missing warning, never a wrong one.
_DECOMPOSERS = (
    "seq", "case", "if", "split", "transitivity", "elim", "exists", "while",
    "call", "congr", "have", "rcondt", "rcondf", "progress", "byequiv",
    "byphoare", "conseq", "sp", "swap", "inline", "auto", "rnd", "wp", "proc",
)


@dataclass(frozen=True)
class GoalMetrics:
    subgoals_count: int = 0
    pr_terms_count: int = 0
    quantifiers_count: int = 0
    module_depth_max: int = 0
    top_connectives_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key, _ in _METRIC_LABELS}


@dataclass(frozen=True)
class StateDiff:
    verdict: str
    pre: GoalMetrics
    post: GoalMetrics
    tactic: str = ""
    cosmetic_noise: tuple[str, ...] = ()
    text_unchanged: bool = False

    @property
    def deltas(self) -> dict[str, int]:
        pre, post = self.pre.as_dict(), self.post.as_dict()
        return {key: post[key] - pre[key] for key in pre}

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "tactic": self.tactic,
            "pre": self.pre.as_dict(),
            "post": self.post.as_dict(),
            "deltas": self.deltas,
            "cosmetic_noise": list(self.cosmetic_noise),
            "text_unchanged": self.text_unchanged,
        }


def metrics(goal: str) -> GoalMetrics:
    """Structural metrics for one goal dump."""
    body = _goal_body(goal)
    return GoalMetrics(
        subgoals_count=subgoal_count(goal),
        pr_terms_count=_count_top_level_pr(body),
        quantifiers_count=_count_top_level_quantifiers(body),
        module_depth_max=_max_module_chain_depth(body),
        top_connectives_count=_count_top_connectives(body),
    )


def _is_decomposer(tactic: str) -> bool:
    head = (tactic or "").strip().lstrip("(").lstrip()
    head = re.split(r"[\s.;:(){}\[\]]", head, maxsplit=1)[0].lower()
    return head in _DECOMPOSERS


def _verdict(
    pre: GoalMetrics, post: GoalMetrics, noise: list[str], tactic: str,
    text_unchanged: bool,
) -> str:
    """Name the transition.

    Order is deliberate. Closure and byte-identity are the two unambiguous
    readings and come first. Everything after them is a description, and the
    last arm is the important one: when the text moved but no metric did, we
    say ``UNCLASSIFIED`` and print nothing rather than claim no change --
    that case is 113 of 525 real accepted transitions, and calling it neutral
    is what would make this module unsafe to reuse as an inertness test.
    """
    if post.subgoals_count == 0 and pre.subgoals_count > 0:
        return PROGRESS

    if text_unchanged:
        return NEUTRAL_OR_NO_CHANGE

    if post.subgoals_count > pre.subgoals_count:
        # Text moved and the count went up. For a tactic that decomposes, that
        # IS the confirmation it landed; for anything else it is worth naming,
        # because a speculative `apply` with loose unification looks the same.
        return PROGRESS_DECOMPOSITION if _is_decomposer(tactic) else REGRESSION

    if post.subgoals_count < pre.subgoals_count:
        return PROGRESS_WITH_COSMETIC_NOISE if noise else PROGRESS

    if any(delta < 0 for delta in _deltas(pre, post).values()):
        return PROGRESS_WITH_COSMETIC_NOISE if noise else PROGRESS

    return UNCLASSIFIED


def _deltas(pre: GoalMetrics, post: GoalMetrics) -> dict[str, int]:
    pre_d, post_d = pre.as_dict(), post.as_dict()
    return {key: post_d[key] - pre_d[key] for key in pre_d}


def compute_state_diff(pre_goal: str, post_goal: str, tactic: str = "") -> StateDiff:
    """Classify what `tactic` did between `pre_goal` and `post_goal`."""
    pre_metrics = metrics(pre_goal)
    post_metrics = metrics(post_goal)
    noise = _new_noise(_goal_body(post_goal), _goal_body(pre_goal))
    text_unchanged = _normalize(pre_goal) == _normalize(post_goal)
    return StateDiff(
        verdict=_verdict(
            pre_metrics, post_metrics, noise, tactic, text_unchanged
        ),
        pre=pre_metrics,
        post=post_metrics,
        tactic=(tactic or "").strip(),
        cosmetic_noise=tuple(noise),
        text_unchanged=text_unchanged,
    )


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _movement(diff: StateDiff, *, improved: bool) -> list[str]:
    deltas = diff.deltas
    pre, post = diff.pre.as_dict(), diff.post.as_dict()
    out = []
    for key, label in _METRIC_LABELS:
        delta = deltas[key]
        if delta == 0 or (delta < 0) != improved:
            continue
        out.append(f"{label} {pre[key]}->{post[key]}")
    return out


def format_state_diff(pre_goal: str, post_goal: str, tactic: str) -> str:
    """A prompt section describing the last tactic's structural effect.

    Returns "" when there is nothing worth saying -- an unclassified move (the
    goal changed but no metric captures how) or a no-change the harness already
    reports through its own no-op section. Silence is the default: a block on
    every step would be noise on the majority of them.
    """
    if not (pre_goal or "").strip() or not (post_goal or "").strip():
        return ""
    if not (tactic or "").strip():
        return ""

    diff = compute_state_diff(pre_goal, post_goal, tactic)
    if diff.verdict in (UNCLASSIFIED, NEUTRAL_OR_NO_CHANGE):
        return ""

    lines = [
        "",
        "## What your last tactic did to the goal",
        f"`{diff.tactic}` -- {diff.verdict}",
    ]

    if diff.verdict == PROGRESS_DECOMPOSITION:
        moved = ", ".join(_movement(diff, improved=False)) or "new subgoals"
        lines.append(
            f"The goal SPLIT: {moved}. More subgoals here is the tactic "
            "working, not a regression -- each one is a smaller obligation. "
            "Work the active (first) subgoal; the rest stay queued."
        )
    elif diff.verdict == REGRESSION:
        pre_n, post_n = diff.pre.subgoals_count, diff.post.subgoals_count
        lines.append(
            f"Subgoal count went {pre_n}->{post_n} on a tactic that does not "
            "normally split a goal. Either it unified too loosely and spawned "
            "speculative obligations, or it genuinely decomposed. Check the "
            "new subgoals before continuing; if they are not ones you can "
            "discharge, undo and pick a tactic with tighter arguments."
        )
    else:
        moved = ", ".join(_movement(diff, improved=True)) or "the goal simplified"
        lines.append(f"Progress: {moved}.")

    if diff.cosmetic_noise:
        fixes = "; ".join(
            _NOISE_FIX.get(tag, tag) for tag in diff.cosmetic_noise
        )
        lines.append(
            f"It also left {fixes}. That is cosmetic -- the goal is not worse "
            "than it looks, and reduction clears it."
        )

    return "\n".join(lines)

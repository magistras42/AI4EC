# Incremental replay-repair loop — design

**Status:** proposed, not implemented.
**Audience:** an LLM implementing this in a later session, with no memory of the
conversation that produced it. Everything needed is stated here or cited by
file and symbol.

---

## 1. What exists today

`integration/experiment/repair_bootstrap.py::run_replay_bootstrap_trial` does:

```
replay original tactics one at a time
  └─ first failure at index `accepted_count`
       └─ hand the ENTIRE remainder to the agent loop as reference text
            └─ agent free-runs until COMPLETE / STUCK / MAX_STEPS
```

Concretely (`repair_bootstrap.py:205-230`, `:307-340`):

- `for tactic in tactics:` appends, calls `validate_file`, breaks on the first
  nonzero return code. `accepted_count` is how many replayed.
- `remaining_original = tactics[accepted_count:]` is written to
  `informal_proof.md` and passed as `informal_proof` on the agent config, with
  a heading saying the first line is the failing tactic and the rest are
  untested.
- Control passes to `integration/agent/loop.py::run_agent_loop`, which never
  replays an original tactic again. Every subsequent tactic is model-authored.

**There is exactly one handoff.** After it, the original script is only a
reference document.

## 2. Why change it

> **Read §2.1 before quoting any number here.** An earlier revision of this
> document justified the design with a "**82% of accepted tactics were verbatim
> from the original**" figure. That figure was an artifact and does not
> survive scrutiny. The design may still be worth building, but *not* for the
> reason originally given.

### 2.1 The retracted premise

The original measurement asked "is this accepted tactic a line that appears
somewhere in the original script?" — set membership. On
`run-20260804T164111Z` trial_012 that gave 46/56 = 82%, which was read as *the
model is re-typing the proof it was handed*.

It is not. The original remaining script for that trial is **33 lines but only
16 distinct**, and is dominated by generic one-word tactics:

```
6x  auto        4x  sp        4x  if; progress        2x  proc
```

Of the 46 "reused" tactics, the number that were **distinctive** — unique in
the original, non-generic, longer than 8 characters — is **zero**. Every match
was `auto` matching `auto`. Any EasyCrypt proof would score similarly against
any other. Measured across all three trials that produced accepted tactics:

| Lemma | accepted | set-match ("82%") | **distinctive** | LCS | original |
|---|---:|---:|---:|---:|---:|
| `G2_G3` | 9 | 5 | **0** | 3 | 17 |
| `INDCPA_HEG_G1` | 56 | 46 | **0** | 14 | 33 |
| `G1_G2_eq` | 7 | 6 | **0** | 5 | 83 |

LCS (longest common subsequence — how much of the original survives *in order*)
is the fairest of the three, and it says 14/33, 3/17, 5/83. Consistent with the
model playing common tactics in a plausible order, not with it transcribing
the reference text.

Order analysis says the same: of 46 matches in trial_012, only **4** were the
next-expected original tactic; 42 were out of order.

### 2.2 What is still true

Two things survive, and neither depends on the retracted figure:

1. **Replay is free and repair is not.** In run C, 11 of 15 lemmas replayed
   verbatim at zero cost. Wherever the original script *does* still apply,
   replaying beats paying — that is arithmetic, not an empirical claim.
2. **A single broken tactic can be a single cheap repair.** Trial_002
   (`INDCPA_Security`) is the existence proof: bootstrap broke at 1/2 tactics,
   the model changed
   `apply (INDCPA_Sec Adv Adv_choose_ll Adv_guess_ll &m).` to
   `apply (INDCPA_Sec Adv &m).` — dropping two now-implicit section axioms —
   and the proof closed. **2 calls, $0.0058.** That is exactly the loop in §3
   running once.

### 2.3 What is now unknown

The design assumes that after repairing a break, *the rest of the original
script still applies*. Run C provides *no* evidence for that, and the LCS
numbers are weak evidence against it. The honest position: the mechanism is
sound and cheap in the one-break case, and unproven in the many-break case.

This changes the priority, not the validity. Build it for the trial_002 shape
— a proof with a small number of isolated version-drift breaks — and let §3.3's
divergence fallback handle everything else. Do **not** promise a cost
reduction on the hard game-hopping proofs; nothing measured supports that.

Correctness consequence: every model-authored re-typing of a working tactic is
a chance to corrupt it. Replay cannot corrupt what already compiled.

## 3. Target design

```
cursor = 0
while cursor < len(tactics):
    # PHASE A — replay, free
    advance cursor while tactics[cursor] validates
    if cursor == len(tactics): break            # proof complete

    # PHASE B — repair, paid
    failing = tactics[cursor]
    hints   = changelog/repair_doc hints aimed at THIS failure
    repair  = agent_loop(goal, failing, hints, budget=repair_budget)
    if repair is None: break                    # give up, report partial
    splice repair into the script in place of `failing`
    cursor += len(repair.accepted_tactics)
    # loop back to PHASE A — the rest of the ORIGINAL script is still ahead
```

The essential difference from today: **Phase A is re-entered after every
repair.** The original script is a live artifact being edited, not a reference
document that was consumed once.

### 3.1 Why this is not just "resume the loop"

After a repair, the proof *state* differs from what the original script
expected. Three outcomes, all of which the loop must handle:

1. **State matches.** The next original tactic applies. Common — a repair that
   substitutes an equivalent tactic leaves the same goal.
2. **State is close.** The next original tactic fails, so Phase B fires again
   with a *fresh* failure. This is the normal case and needs no special
   handling; it is just the next iteration.
3. **State has diverged.** The repair changed the goal structure (e.g. split
   into more subgoals), and a long run of subsequent original tactics is now
   meaningless. Detect via a **divergence counter**: if Phase B fires on N
   consecutive tactics with zero replay progress between them, stop trusting
   the remainder and fall back to today's behaviour — hand the rest over
   wholesale. Suggested N = 3, configurable.

Case 3 is the reason this is a design doc and not a one-line change. Do not
skip the fallback; without it a structural divergence turns into a long
sequence of doomed paid calls, which is strictly worse than today.

## 4. Implementation plan

### 4.1 New: `ReplayCursor`

A small class owning the script and the position. Suggested home:
`integration/experiment/replay_cursor.py`.

```python
@dataclass
class ReplayCursor:
    tactics: list[str]           # the working script, mutated by repairs
    index: int = 0               # next tactic to attempt
    repairs: list[RepairRecord] = field(default_factory=list)

    def advance(self, proof: ProofFile, config) -> ReplayOutcome:
        """Append tactics until one fails or the script is exhausted."""

    def splice(self, replacement: list[str]) -> None:
        """Replace tactics[index] with `replacement`; index += len(replacement)."""
```

`advance` is the existing loop body from `repair_bootstrap.py:208-216` lifted
verbatim — appending, `validate_file`, `remove_lines` on failure. Do not
rewrite it; move it.

### 4.2 Changed: `run_replay_bootstrap_trial`

Wrap the current body in the `while` of §3. The existing single-shot path
becomes the `divergence` fallback, so it must stay reachable and tested.

Keep writing `bootstrap_result.json`, but extend it — see §6.

### 4.3 Changed: the agent loop's exit contract

`run_agent_loop` currently runs until the whole proof is discharged. Phase B
needs it to stop as soon as **the current breakage is repaired**, not when the
proof is done. Add a stopping condition:

- New `AgentConfig` field, e.g. `stop_when_goal_changes: bool = False`.
- When set, the loop exits with a new `ExitReason.REPAIRED` once a tactic is
  accepted and the resulting goal differs from the goal at entry.
- The accepted tactics are returned so the caller can splice them.

This is the largest single change and the one most likely to break existing
tests. `ExitReason` is in `integration/agent/loop.py`; `AgentResult` needs an
`accepted_tactics: tuple[str, ...]` field.

**Budget:** give Phase B its own step budget (suggest `max(5, remaining/4)`),
separate from the trial budget. A repair that cannot be done in a few steps is
better handled by the §3.3 fallback than by grinding.

### 4.4 Hint targeting already works

`loop.py::_refresh_changelog_hints` (W3) already re-aims the changelog block at
each new failure. Phase B inherits this for free. Do not rebuild it.

## 5. Interaction with the goal-scoping work

`prompt.py::active_goal_text` / `count_subgoals` (added 2026-08-04) scope hints
and the rendered goal to the ACTIVE subgoal. This matters here because a repair
that splits a goal into subgoals is exactly case 3 above. When implementing the
divergence detector, `count_subgoals` is the cheapest signal that structure
changed: a jump in open-goal count between Phase B entry and exit is strong
evidence the remainder of the script no longer lines up.

## 6. Telemetry — required, not optional

The whole justification is economic, so the implementation must make the claim
falsifiable. Extend `bootstrap_result.json` per trial:

```json
{
  "total_count": 52,
  "replayed_free": 27,
  "repaired": 6,
  "repair_calls": 14,
  "diverged": false,
  "phases": [
    {"phase": "replay", "from": 0,  "to": 21, "cost_usd": 0.0},
    {"phase": "repair", "at": 21, "calls": 3, "cost_usd": 0.02,
     "original": "seq 1 1 : (...)", "replacement": ["seq 1 1 : (...)"]}
  ]
}
```

`replayed_free` vs `repaired` is the headline number. If `repair_calls`
approaches today's total call count, the design has not paid off and should be
reverted rather than tuned.

## 7. Test plan

Write these *before* the implementation; all run offline with no API spend.

| Test | Asserts |
|---|---|
| script replays fully | zero LLM calls, unchanged behaviour from today |
| one broken tactic mid-script | Phase B fires once, replay resumes, remaining originals are reused |
| repair enables the very next original tactic | no second Phase B |
| three consecutive failures with no progress | divergence fallback engages, matches today's single-handoff path |
| repair splits the goal | `count_subgoals` rises, divergence detected |
| splice bookkeeping | a repair emitting 2 tactics for 1 advances the index by 2 |

There is **no** `test_repair_bootstrap.py` today — `run_replay_bootstrap_trial`
is currently untested in isolation, which is itself worth fixing as step 0.
For the fake-EasyCrypt pattern, follow
`integration/experiment/tests/test_runner.py`, which already builds trials
without invoking a real binary. A stub `validate_file` keyed on tactic text is
enough.

## 8. Explicit non-goals

- **Do not** re-verify already-replayed tactics after a repair. If a repair
  invalidated earlier work, the next `validate_file` catches it.
- **Do not** let the model edit tactics before the cursor. Only the failing
  tactic is replaceable.
- **Do not** remove the single-handoff path. It is the divergence fallback.

## 9. Known risk — and the check that already fired

The original §9 said the 82%-reuse figure rested on one trial and demanded a
re-measurement across several trials before §4.3 was built, because it "would
either confirm the premise or kill the project cheaply."

**It ran, and it killed the premise.** See §2.1. The re-measurement cost
nothing and happened before any code was written, which is the outcome that
process was for. Preserved here rather than deleted, because the next person
should trust the same instinct.

### 9.1 What to check before building §4.3 now

The economic case has been withdrawn, so §4.3 (the invasive `run_agent_loop`
change) should **not** be built on the strength of this document alone. The
remaining question is narrow and answerable offline:

> Across the corpus, how many broken lemmas have a **small number of isolated
> breaks** (the trial_002 shape) versus **one early break followed by wholesale
> divergence** (the trial_014 shape: bootstrap died at 18/85)?

Run C's four paid trials split 1 / 3 against that question — one cheap isolated
repair, three that broke early and never recovered. That is a discouraging
ratio, and it is the number that should gate the work.

A cheap way to get it without any model: for each lemma, replay the original
script, and at the first failure **admit** the failing tactic and keep
replaying. Count how many further failures occur. Lemmas with 1–2 failures are
the population this design serves; lemmas with many are the population it
cannot help. That experiment needs EasyCrypt only, costs no API spend, and is
strictly more informative than anything measured so far.

### 9.2 Standing caution

Run-to-run variance in this harness has reached 11-vs-1 accepted tactics under
identical configuration. Any future figure quoted in this document should say
how many trials it came from and whether the matching criterion could be
satisfied by generic tactics — that is precisely how §2.1's figure went wrong.

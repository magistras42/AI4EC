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

Measured on `run-20260804T164111Z`, trial_012 (`INDCPA_HEG_G1`):

| | |
|---|---:|
| Original tactics replayed by bootstrap | 21 / 52 |
| Tactics remaining, handed over as text | 33 |
| Tactics the agent then got accepted | 33 |
| ...**verbatim from the original remaining script** | **27 (82%)** |
| ...actually newly written | 6 |

The model spent ~19.6k reasoning tokens per call re-deriving lines it had been
handed as literal text. Only ~6 of 33 needed genuine adaptation.

Cost consequence: that trial burned ~2 hours and a large share of the run's
$1.00 budget. An incremental loop would replay the 27 for free and spend model
calls only on the ~6 breakages.

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

## 9. Known risk

The 82%-reuse figure is from **one trial**. It is a strong signal but a single
observation, and run-to-run variance in this harness has reached 11-vs-1
accepted under identical configuration. Before building §4.3 (the invasive
part), re-measure reuse across several completed trials — the analysis is a
dozen lines against `agent_log.json` and `informal_proof.md`, costs nothing,
and would either confirm the premise or kill the project cheaply.

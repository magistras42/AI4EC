# Bug: False Proof Completion Due to Stale `qed.` Lines in Sandbox Files

**Severity**: Critical — invalidates all COMPLETE outcomes across all experiment runs to date  
**Status**: Unresolved  
**Affected runs**: All runs (confirmed in run-20260704T010124Z and cross-checked in run-20260704T192235Z, run-20260704T195550Z)

---

## Summary

The agent loop incorrectly reports `ExitReason.COMPLETE` for proofs that EasyCrypt has not actually verified. The working copy files for all reported completions end without a `qed.` closing the target lemma. Direct verification confirms EasyCrypt rejects these files as incomplete proofs.

---

## Root Cause

The bug is in `_probe_qed_discharge` in `integration/agent/easycrypt.py`, specifically in how `insert_at` is computed via `ProofFile.insert_point()`.

### The probe mechanism

`_probe_qed_discharge` checks whether the current proof state is complete by:
1. Taking the current lines of the working copy
2. Inserting a `qed.` line at `proof.insert_point()`
3. Writing this to a temporary file
4. Running `ec.exe llm -upto <qed_line>` on the temp file
5. Returning `True` if EasyCrypt reports no active proof at that line

### The defect in `insert_point()`

```python
def insert_point(self) -> int:
    bounds = self.bounds()
    lines = self.read_lines()
    if bounds.qed_line is not None:
        return bounds.qed_line - 1   # <-- BUG: uses the FIRST qed. found
    return bounds.last_line
```

`bounds.qed_line` is populated by `_find_qed_after`, which scans forward from `proof_start_line` for the **first** `qed.` in the file:

```python
def _find_qed_after(lines: list[str], proof_start_line: int) -> int | None:
    if proof_start_line == 0:
        return None
    for i in range(proof_start_line, len(lines) + 1):
        if QED_RE.search(lines[i - 1]):
            return i
    return None
```

### Why this causes false positives

The sandbox files used in experiments are truncated at the target lemma's own `qed.`, but they retain all **earlier lemmas** from the same source file, complete with their own `proof.` / `qed.` pairs. For example, `triple3`'s sandbox contains:

```
lemma triple1: ...      <- earlier lemma
proof.
  proc.
  wp; skip; smt().
qed.                    <- line 99: this is the FIRST qed. after any proof.

lemma triple2: ...
proof.
  ...
qed.                    <- line 151

lemma triple3: ...      <- TARGET lemma
proof.
  proc.                 <- agent appended this
                        <- proof is open here, no qed.
```

When `bounds()` is computed on the working copy:
- `_find_proof_start` scans **backward** from `last_line` and finds `proof.` for `triple3` (correct)
- `_find_qed_after` scans **forward** from that `proof_start_line` and finds the `qed.` from **`triple1`** at line 99 (wrong — it belongs to an earlier, already-closed lemma)

So `bounds.qed_line = 99` (an earlier lemma's `qed.`), and `insert_point()` returns 98.

The probe then inserts `qed.` at index 98 (before line 99), creating a file where `qed.` appears mid-file, inside what was `triple1`'s closed proof. EasyCrypt processes up to that line and finds... no active proof at that position, since `triple1` was already closed before it. `is_proof_discharged` returns `True`, and the agent declares success.

The target lemma `triple3` was never closed at all.

### Why the working copies confirm this

All "completed" working copies end with a bare `proof.` followed by one accepted tactic (typically `proc.`) and no `qed.`:

```
lemma triple3: hoare [ Func2.x_sq : 4 <= x ==> 16 <= res ].
proof.
  proc.
                <- file ends here, no qed.
```

Direct EasyCrypt verification confirms these files are rejected:

```
$ easycrypt llm -lastgoals <working_copy>.ec
[critical] cannot save an incomplete proof
EXIT CODE: 1
```

---

## Conditions for the Bug to Fire

The false positive requires all three of the following:

1. **Multiple lemmas in the sandbox file** — the target lemma is not the first lemma in the file
2. **At least one earlier lemma has a closed `qed.` after the target's `proof.` line** — this is always true for non-first lemmas in the Joy corpus sandboxes
3. **The last tactic accepted is not `proc.`** — actually it fires regardless of the tactic; `proc.` just happened to be the tactic that triggered the check in most observed cases

In practice this means **every non-trivial Joy corpus lemma** is affected, since the sandbox construction always includes all preceding lemmas from the chapter file.

---

## Why Some Runs Escaped Detection

The runs on July 6–9 (`run-20260706T231618Z`, `run-20260707T160616Z`, `run-20260707T162739Z`, `run-20260709T142319Z`) reported no completions — not because the bug was fixed, but because the local model failed before ever producing an accepted tactic (context overflow, runaway reasoning, `LLM_ERROR`). These runs have no false positives simply because they never reached the completion-check code path.

---

## Impact on Experiment Results

| Run | Reported COMPLETE | Actual COMPLETE |
|---|---|---|
| run-20260704T010124Z | 21 / 30 | 0 |
| run-20260704T192235Z | 4 / 4 | 0 |
| run-20260704T195550Z | 2 / 5 | 0 |
| run-20260706T231618Z | 0 / 4 | 0 |
| run-20260707T160616Z | 0 / 1 | 0 |
| run-20260707T162739Z | 0 / 0 | 0 |
| run-20260709T142319Z | 0 / 0 | 0 |

No verified proof completions exist in any experiment run to date.

---

## Fix Direction

The fix needs to be in `_find_qed_after` or in `insert_point`. The core requirement is that the `qed.` found must belong to the **target lemma's open proof**, not any earlier closed proof.

The cleanest fix is to change `_find_qed_after` to only return a `qed.` that falls **after** the proof region's own `proof.` line and is not preceded by another intervening `proof.`/`qed.` pair — i.e., it should find the `qed.` that would close the current open proof, not any `qed.` in the file. Alternatively, since the agent-start files are constructed by `strip_tactics`, which explicitly strips all tactic lines and the trailing `qed.` of the target lemma (leaving the proof open), the working copy should in principle never have a `qed.` for the target lemma — so `_find_qed_after` returning any result at all on a working copy indicates it found an earlier lemma's `qed.`, and `qed_line` should be `None` in that case.

A simple guard: after finding a candidate `qed.` line, scan backwards from it to verify the nearest preceding `proof.` matches `proof_start_line`. If not, discard and continue searching.

---

## Workaround for the Progress Report

Since no verified completions exist, the progress report should not cite any completion rates. The results section should focus exclusively on the failure taxonomy (context overflow, runaway reasoning, degenerate tactic selection, nonlinear arithmetic failures) which is documented from valid, unambiguous data in the agent logs of the July 6–9 runs.

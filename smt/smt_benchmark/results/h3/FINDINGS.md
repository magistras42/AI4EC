# H3 pilot — "can the winning solver be predicted from file contents alone?"

**Setup:** the 3 single-winner files (serial retime, final). Each file's
contents go into a blind prompt (no answer included); an LLM predicts the
winning solver; predictions are scored against ground truth. `h3_demo.py`
handles the dataset/prompts/scoring.

## Results

| file | answer | LLM prediction | hit |
|---|---|---|---|
| `examples/ehoare/adversary.ec` | Z3 | Z3 | ✅ |
| `examples/ehoare/qselect/qselect.ec` | CVC5 | Z3 | ❌ |
| `the-joy-of-easycrypt/04-hoare-logic/hoare-logic.ec` | Alt-Ergo | Z3 | ❌ |

**Accuracy: 1/3 = 33% = the 3-way random baseline.**

## Interpretation (key points)

- **The model predicted Z3 for all three files (mode collapse).** Its
  rationale was identical every time: "there is nonlinear real/int
  arithmetic, and Z3's nlsat is strongest at it." The reasoning is plausible
  but has **no discriminative power** — it assigned the same label to every
  file.
- The two misses refute that heuristic head-on: qselect (nonlinear integer
  arithmetic with squares) was solved only by **CVC5**, and the tutorial
  hoare-logic (an exponential recurrence) only by **Alt-Ergo**. "Nonlinear ⇒
  Z3" as a surface feature disagrees with the actual winners.
- **Pilot conclusion:** surface file features (imported theories, kind of
  arithmetic) are not enough to predict the winning solver. With n=3 no
  statistical claim is possible, but the direction — "easy signals don't
  work" — is clear.

## Implications (if H3 were pursued in earnest)

- Likely needs the call-level approach: give the model the **text of the
  actually-failing goal** (the subgoal at the first failure point), not the
  file surface. Same conclusion as the file-level-proxy limitation.
- The data is too small (3 single winners, 13 multi-disagreements). Reframing
  from winner prediction to **"predict the set of succeeding solvers"**
  (multi-label over 13+3=16 files) could expose more signal.
- Mitigating mode collapse should come first: sample several times and take
  a majority, or use prompts that force winner diversity (stating each
  solver's strengths).

## Reproduction

```bash
python3 h3_demo.py build                                   # dataset.json + prompts/
# (a blind LLM answers each prompts/*.txt → write predictions.json)
python3 h3_demo.py score --preds results/h3/predictions.json
```

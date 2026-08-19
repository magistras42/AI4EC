# AutoCrypt Corpus Parsing Validation

## Purpose

This validation examined why AutoCrypt was assigned zero parsed proofs
and zero proof complexity by the proof-repair difficulty ladder.

## Repository

- Repository: JHUISI/autocrypt
- Last commit date detected by the scoring script: 2013-10-01
- Classification: Predates the available EasyCrypt changelog history

## Observed Compatibility Issue

AutoCrypt uses an older EasyCrypt proof-block convention in which explicit
proofs are terminated with `save.`:

```easycrypt
proof.
  ...
save.
```

The initial proof extraction regular expression recognized only `qed.`,
`abort.`, and `admit.` as proof terminators. Consequently, valid
`proof. ... save.` blocks were omitted from the proof-complexity calculation.

## Fix

The explicit proof-block parser was extended to recognize `save.` as a
legacy proof terminator:

```python
PROOF_BLOCK_RE = re.compile(
    r"\bproof\.(.*?)\b(?:qed|save|abort|admit)\.",
    re.DOTALL,
)
```

Because `estimate_repair_difficulty.py` imports this regular expression
from `compute_exposure_score.py`, the change applies consistently to both
repository-level and per-proof difficulty analysis.

## Validation Results

| Metric | Before Fix | After Fix |
|---|---:|---:|
| Parsed proofs | 0 | 5 |
| Parsed proof depths | - | 10, 4, 16, 8, 6 |
| Proof complexity score | 0.00 | 12.02 |
| Predates-changelog penalty | Minimum-scaled | 16.03 |
| Total exposure score | Underestimated | 1301.61 |

The updated complexity score was successfully propagated into the
complexity-scaled predates-changelog penalty and final exposure score.

## Cross-Repository Sanity Check

Two additional repositories were checked with the same parser:

| Repository | Parsed Proofs | Result |
|---|---:|---|
| LQ-1 | 25 | Parsed normally |
| CS591 Project | 27 | Parsed normally |

This supports the conclusion that AutoCrypt was the primary repository
affected by the unsupported legacy `save.` syntax among the repositories
examined.

## Taxonomy Mapping

- Stage: Corpus preprocessing and compatibility handling
- Category: Compatibility migration / historical syntax evolution
- Symptom: Valid proofs omitted from corpus-level complexity analysis
- Repair type: Parser compatibility update
- Impact: Corrected benchmark difficulty and ranking inputs

## Remaining Limitations

The extraction logic remains heuristic rather than a complete EasyCrypt
parser. AutoCrypt contains many `equiv` declarations, including inline
forms such as `equiv ... by ...`, while the current inline parser primarily
targets `lemma ... by ...`. Therefore, the five recovered proofs should be
treated as an improvement in coverage rather than complete extraction of
all proof obligations.

A future implementation could report parsing coverage or confidence by
comparing recognized proof blocks with the number of proof-related
declarations in each repository.
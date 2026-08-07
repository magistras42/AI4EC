# LQ-1 Easy Repair Candidate

## Source

- Repository: LQ-1
- Source file: `theorem63/lemma3.ec`
- Lemma: `sampling_bound`
- License: MIT
- Documented solvers: Z3 4.8.12 and Alt-Ergo 2.4.3

## Benchmark Construction

The original verified proof was copied without changing the theorem
statement or assumptions.

The broken version removes the final `smt()` step after applying
`rpow_hmono`, leaving one proof obligation unresolved.

## Taxonomy

- Benchmark type: Controlled synthetic repair
- Failure category: Incomplete proof
- Repair scope: Proof script only
- Expected repair: Restore a tactic that closes the remaining side goal

## Validation Status

Local EasyCrypt execution has not yet been performed because the current
development environment does not have the `easycrypt` binary installed.

Expected outcomes:

- `sampling_bound_original.ec`: should verify successfully based on the
  source repository's documented status
- `sampling_bound_broken.ec`: should fail because the final proof obligation
  remains unresolved

The candidate should be validated in the team's pinned EasyCrypt environment
before inclusion in the final benchmark results.


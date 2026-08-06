# Proof-Repair Benchmark Selection Notes

## Purpose

This document summarizes candidate repositories for the final proof-repair
evaluation and distinguishes automated ladder scores from observed agent
repair difficulty.

The automated ladder estimates repository-level maintenance risk using
version exposure, change density, proof complexity, and toolchain evidence.
It should not be interpreted as a direct measure of whether an LLM agent
can repair an individual proof.

## Candidate Summary

| Repository | Intended Benchmark Level | New Ladder Score | Parsed Proof Evidence | Current Interpretation |
|---|---|---:|---|---|
| LQ-1 | Easy | 186.35 | 25 proofs; complexity 12.9 | Recent verified corpus; suitable for controlled synthetic repair rather than historical compatibility repair |
| CS591 Project | Medium | 447.84 | 27 proofs; complexity 10.2 | High score is driven largely by uncertain historical version rather than proof complexity |
| Comparison Non-Malleability Unsat | Medium | 416.84 | 26 proofs; complexity 59.5 | Older repository with moderate proof complexity and explicit historical solver requirements |
| Zksync Verification excerpts | Medium-Hard | 400.91 | 953 proofs; complexity 204.1 | Large production-style development; selected excerpts are more appropriate than full-repository evaluation |
| ElGamal | Hard | 494.92 | 69 proofs; complexity 35.2 | Empirical agent evaluation showed difficulty on proofs requiring more than approximately ten steps |
| AutoCrypt | Compatibility validation | 409.70 in generated ladder | Generated ladder reported 0 proofs; parser fix recovered 5 | Historical syntax validation case rather than a primary repair benchmark |

## Ladder Revision

The new ladder substantially reduced the default penalty applied to
repositories that predate the available changelog. This lowered the scores
of repositories with unknown historical versions, including AutoCrypt,
CS591 Project, and ElGamal.

The revised score is more balanced, but repositories with unknown version
information still receive a substantial historical-exposure penalty.

## AutoCrypt Output Staleness

The generated `new_ladder.md` still reports zero parsed AutoCrypt proofs.
This output predates the parser update that added support for the legacy
`proof. ... save.` syntax.

After the update, five explicit proof blocks were recovered with estimated
proof depths of 10, 4, 16, 8, and 6. Therefore, the ladder should be
regenerated after the parser fix is merged rather than edited manually.

## Selection Rationale

### LQ-1: Easy

LQ-1 is a recent EasyCrypt development committed on March 29, 2026.
It contains mechanized proofs for BFT soundness, ORCA serializability,
and supporting UC-security definitions for the LQ-1 post-quantum
blockchain protocol.

The repository documents successful verification with Z3 4.8.12 and
Alt-Ergo 2.4.3, although it does not pin a specific EasyCrypt version
or include an `easycrypt.project`, OPAM, Makefile, or container
configuration.

The repository is not itself a naturally broken historical development:
its README states that all proofs should complete without errors or
admitted lemmas. Therefore, LQ-1 is best used as an easy controlled
reconstruction or synthetic-repair benchmark. A verified proof can be
mutated while preserving the original theorem statement, allowing the
agent's repair output to be checked against a known-valid target.

Candidate files range from 16 to 108 lines. The short theorem and lemma
files provide bounded tasks, while `theorem4/orca.ec` offers a larger
follow-up case. The repository uses the MIT license and is suitable for
publishing benchmark inputs and repaired outputs.

#### Selected Easy Candidate

- File: `theorem63/lemma3.ec`
- Lemma: `sampling_bound`
- Original status: Verified proof
- Benchmark type: Controlled synthetic repair
- Mutation: Remove the final subgoal-closing `smt()` step after applying
  `rpow_hmono`
- Expected failure: Incomplete proof with one remaining side condition
- Taxonomy category: Incomplete proof
- Repair scope: Proof script only; theorem statement and assumptions remain
  unchanged

This candidate was selected instead of `theorem63.ec` and
`theorem63/lemma1.ec` because those two files contain the same
`half_pow_bound` proof. Using both would introduce duplicate benchmark
content.

### CS591 Project: Medium

CS591 Project has low measured proof complexity but a high repository-level
score because its source version predates the available changelog and
cannot be identified confidently. Its benchmark classification should be
based on the selected repair case and existing partial agent progress, not
on the aggregate ladder score alone.

### Comparison Non-Malleability Unsat: Medium

This repository has higher measured proof complexity than CS591 Project
and records an older solver toolchain. It is suitable for evaluating
historical compatibility and nontrivial proof repair while remaining more
bounded than the production-scale repositories.

### Zksync Verification: Medium-Hard

Zksync Verification contains a large number of proofs and has the highest
measured proof complexity among the selected candidates. Evaluation should
use carefully selected excerpts rather than treat the entire repository
as a single task.

### ElGamal: Hard

ElGamal is retained as the hard benchmark based on empirical agent
behavior. Existing evaluation results indicate that the agent struggles
on proofs extending beyond approximately ten accepted steps. This
observed behavior is stronger evidence of task difficulty than its
repository-level complexity score alone.

## Remaining Information Needed

- Exact selected proof or excerpt for each benchmark
- Original compilation or proof failure
- Expected EasyCrypt and solver version
- Current repair outcome
- Mapping to the existing repair taxonomy
- Permission constraints for publishing inputs and repaired outputs

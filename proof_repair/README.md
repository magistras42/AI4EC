# Proof Repair and Error Feedback

This directory contains notes, examples, and experiments for EasyCrypt proof repair.

## Scope

- Collect EasyCrypt proof errors
- Categorize EasyCrypt error messages
- Build a broken proof dataset
- Design LLM prompts for proof repair
- Evaluate whether repaired proofs compile successfully

## Directory Structure

- `minimal_tests/`: small EasyCrypt files used to verify the local setup
- `broken_proofs/`: intentionally broken EasyCrypt proofs
- `fixed_proofs/`: repaired versions of broken proofs
- `error_catalog.md`: categorized EasyCrypt errors and repair strategies
- `experiment_logs/`: records of compile and repair experiments
- `prompt_templates/`: prompt templates for LLM-based proof repair

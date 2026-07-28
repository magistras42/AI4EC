---
description: Repair an outdated EasyCrypt proof against the current EasyCrypt with Shannon Prover
argument-hint: <LemmaName> <path-to-file-with-original-proof> <source-ec-version> <target-ec-version>
allowed-tools: Bash, Grep, Glob, Read
---

Run Shannon Prover's proof-repair mode on a single EasyCrypt lemma for the user.

Unlike `/prove` (from-scratch construction against an admitted goal), this replays
the lemma's EXISTING, outdated proof against the current EasyCrypt install until the
first tactic fails, then tries a localized, changelog/repair-doc-hinted patch before
falling back to full tree search. See `workflow/repair.py` for the two-phase flow.

Input: `$ARGUMENTS`
- token 1 = lemma name (required), e.g. `PIR_correct`.
- token 2 = path to a copy of the target `.ec` file that still has the lemma's
  ORIGINAL, outdated, intact `proof. ... qed.` body (required — the live checkout's
  copy may already be stripped/admitted).
- token 3 = source EasyCrypt version tag the original proof was written against
  (required), e.g. `r2022.04`.
- token 4 = target EasyCrypt version tag to repair the proof for (required; usually
  the version currently installed), e.g. `r2026.07`.

Substitute `<LEMMA>` / `<ORIGINAL_FILE>` / `<SOURCE_VER>` / `<TARGET_VER>` below from
the input as you go.

Steps:

1. Environment — EasyCrypt must run outside any OS sandbox so `why3server` can start
   (it needs the `nice()` syscall):

   ```bash
   eval "$(opam env --switch=easycrypt)" && mkdir -p tmp
   ```

2. Locate the target lemma's CURRENT source under `eval/examples/` (or wherever the
   live checkout lives) if it is not obvious:

   ```bash
   grep -rln "lemma <LEMMA>\|equiv <LEMMA>\|hoare <LEMMA>" eval/examples --include=*.ec
   ```

3. Confirm `<ORIGINAL_FILE>` exists and still contains an intact `proof. ... qed.`
   block for `<LEMMA>` (not `admit.`, not already stripped):

   ```bash
   grep -n "lemma <LEMMA>" -A 5 <ORIGINAL_FILE>
   ```

4. Run repair mode:

   ```bash
   uv run python -m workflow.repair \
       --file <path to current .ec file, relative to project root> \
       --lemma <LEMMA> \
       --repair-source-file <ORIGINAL_FILE> \
       --source-ec-version <SOURCE_VER> \
       --target-ec-version <TARGET_VER> \
       2>&1 | tee tmp/<LEMMA>_repair.log
   ```

   On a Claude subscription with no provider API key, prefix the run with
   `env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u DEEPSEEK_API_KEY`
   so the Claude CLI uses the logged-in subscription.

5. Report the verdict from `workflow/runs/<TS>_<LEMMA>_repair/summary.json`:
   - `phase`: `bootstrap_only` (original proof still replayed verbatim — nothing to
     repair), `phase1` (localized patch closed it), or `phase2` (needed full tree
     search fallback).
   - `bootstrap`: how many of the original tactics replayed before the first failure,
     and what failed.
   - `proved`: whether the run closed the goal with no `admit.` remaining.

Do not hand-edit the checkout to "help" the proof — repair mode's bootstrap replay is
the whole point: it establishes exactly how much of the original proof still holds
before any patching happens.

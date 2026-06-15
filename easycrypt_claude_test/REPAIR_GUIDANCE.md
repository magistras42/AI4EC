# Repair guidance

This task is primarily a proof-repair exercise, not a redesign exercise. Keep
changes focused and preserve the intended security statement.

## Acceptance criteria

- The project verifies with EasyCrypt.
- No `admit` or `admitted` remains.
- No debugging commands such as `search ...` or `print ...` remain in the final
  `.ec` files.
- Do not add unsupported axioms just to close a proof.
- Do not delete the core PRF-to-MAC security reasoning.
- Avoid vacuous security bounds. In particular, a forgery probability bounded
  by `(2 ^ text_len)%r` is mathematically easy but cryptographically useless.

## Suggested repair strategy

1. Start by making the files parse and compile up to the first real proof
   failure. Fix syntax and library/import compatibility before changing proof
   architecture.

2. Use MCP goal inspection aggressively. Work one failing lemma at a time. Do
   not continue editing downstream lemmas while an earlier lemma is still
   unverified.

3. Treat the PRF-to-random-function game hop separately from the true-random
   forgery bound. The first part is mostly equivalence/game-connection proof;
   the second part is a probability bound for guessing a fresh random tag.

4. For map-domain facts, search for and reuse existing `fdom`, `mem_fdom`,
   `fdom_set`, and finite-set extensionality lemmas. Avoid hand-expanding maps
   unless necessary.

5. Be careful with invariants involving `seen = fdom TRF.mp`. This relation is
   useful before oracle calls and after tagging calls. It may be too strong as a
   final postcondition after a verification call that samples a new fresh value.
   If an invariant is only needed before the final call, weaken the final
   postcondition to what downstream lemmas actually need.

6. If two procedures are structurally hard to align in pRHL, introduce a small
   intermediate lemma or intermediate module with more similar code rather than
   forcing a large direct equivalence. Keep the intermediate statement narrow:
   same result, same random-function map if needed, and only the minimal
   invariant required by callers.

7. The true-random verification bound should come from the fact that a fresh
   random text equals an adversarially chosen tag with probability about
   `1 / 2^text_len`, using the existing `mu1_dtext` / uniform distribution
   facts. A bound of `2^text_len` should not be considered a meaningful repair.

8. When a command is rejected by Claude Code permissions, rewrite the command
   into simpler allowed commands. Prefer `rg` and `rg --files`. Do not use
   `find -exec`, `xargs`, shell loops, or semicolon-chained command groups.

## Useful local checks

Run simple checks separately:

```bash
rg -n "admit|admitted|search |print " .
rg -n "2 \\^ text_len" MAC-PRF.ec
rg -n "local lemma|lemma " MAC-PRF.ec
```

Use EasyCrypt MCP compile/goal tools for authoritative verification.

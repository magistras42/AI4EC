# Usage

An example run of retrieve_entries.py includes:

```
python3 retrieve_relevant_entries.py \
  --changelog changelog.yaml \
  --proof path/to/broken_proof.ec \
  --source-version r2025.02 \
  --target-version r2026.07 \
  --top-n 12
```

--changelog changelog.yaml: output of the previous step.
--proof: the actual broken .ec file you're trying to repair.
--source-version / --target-version: must exactly match tag_name values from your collected data. From your earlier collect_changelog.py output, valid tags include r2022.04, r2023.09, r2024.01, r2024.09, r2025.02, r2025.03, r2025.08, r2025.10, r2025.11, r2026.02, r2026.03, r2026.05, r2026.06, r2026.07 — pick the pair that actually brackets your proof's original vs. target version. If you typo one, the script warns and falls back to the full range rather than silently returning nothing, so watch stderr.
--top-n 12: reasonable starting point; bump it up if your repair-time LLM prompt has budget to spare, or down if you're seeing irrelevant entries make the cut. There's no fixed "right" value — it trades off completeness against prompt size, so I'd tune it empirically against a few known repair cases.
Add --out relevant_entries.json once you're happy with the setup, so you can pipe that file straight into whatever assembles your repair prompt, instead of parsing stdout each time.

# Retrieval/filtering logic outline

The goal at repair time: given a broken proof script (source version → target version), pull only the changelog entries plausibly relevant, out of what could be dozens of releases' worth of noise.

1. Build an identifier index from the proof script.
Parse the failing EasyCrypt proof (or just tokenize it — you don't need a full parser for this) into three buckets:

Tactic names used (rewrite, match, smt, cloning-related keywords, etc.) — these are a small, mostly-fixed vocabulary you can hardcode/regex against.
Identifiers referenced (lemma names, theory/module names, e.g. nth0, List) — anything that looks like an EasyCrypt identifier token.
The version range being bridged (source tag → target tag).

2. Slice by version range first.
Since your structured changelog is indexed by version, cheaply filter to just the releases strictly between source and target — no point scanning the whole project history. This alone usually cuts things by 90%+.

3. Within that range, filter by relevance + identifier overlap, in tiers:

Tier A (always include): entries with kind: mechanism_change and relevance: high. These are structural (cloning, imports, module systems) and can break proofs that don't textually reference anything that changed — so identifier matching alone would miss them. Keep this list short by relying on your LLM classification pass to be conservative about what counts as mechanism_change.
Tier B (include on match): entries where identifiers (or tactic/theory fields) intersect the proof script's identifier set. Do simple set intersection, not fuzzy string matching, to start — EasyCrypt identifiers are exact tokens, so nth0 in the script must literally match nth0 in the changelog entry. Case-sensitive, whole-token match (avoid substring matches like map matching map1).
Tier C (drop): everything else, especially kind: internal, documentation, and anything with relevance: low.

4. Rank what's left, don't just concatenate.
If Tier A + Tier B together are still large, rank by:

Exact identifier match count (more shared identifiers = more likely relevant) — a simple TF-style scoring, or even just "number of overlapping identifiers," is enough; you don't need embeddings here since the vocabulary is small and exact.
Proximity to target version (entries in the release closest to the target version are marginally more likely to be the actual cause, since intermediate changes may have already been superseded).

Take the top N (start with N≈10–15 and tune empirically) so you're not blowing your repair prompt's context budget on marginal matches.

5. Inject into the repair prompt as the repair_hint field only, not the raw entry — that's the whole point of separating summary/repair_hint from bookkeeping fields (id, kind) at generation time. The repair-time LLM wants "if this is the cause, try X," not PR metadata.

6. Optional: two-pass repair. First pass: ask the LLM to repair using only the error message + proof script (no changelog) and see if it succeeds. Only if that fails, retry with the filtered changelog slice injected. This keeps your changelog retrieval off the hot path for repairs that don't actually need version-specific knowledge (e.g. proofs broken by a simple, generic Coq/EC syntax typo), saving both latency and the risk of the changelog data being a red herring.

Good instinct to build a ladder rather than treat all repos as equally hard — difficulty varies enormously in practice. Here are the factors I'd add, then how I'd weight everything.

## Additional factors to consider

**1. Breaking-change count and type crossed, not just time elapsed**
"How outdated" (calendar time or version count) is a weak proxy for what actually matters: how many of the *intervening changes were actually breaking*. A repo last touched 3 years ago that happens to sit entirely in a quiet period is easier than one 6 months old that straddled a `mechanism_change` release. Since you already have the changelog tooling, you can compute this directly per repo: count `tactic_change`/`mechanism_change`/`lemma_renamed`/`lemma_removed` entries (weighted by `relevance`) between the repo's pinned version and your target version. This is a much sharper signal than raw staleness and should probably replace "how outdated" as your primary axis rather than sit alongside it.

**2. Module system / cloning usage**
EasyCrypt's cloning and realization mechanism is disproportionately fragile across versions — recall the changelog entries we found earlier (`Rework import mechanism`, `cloning: do not allow realizing a non-axiomatic lemma`). Repos that heavily clone theories with renamings, or use abstract modules with functor-style instantiation, are structurally more exposed to breakage than ones with flat, self-contained proofs — independent of how deep any single lemma is.

**3. Dependency fan-out / cascade depth**
Not just "how many tactics to close *this* goal" but how many *other* lemmas transitively depend on the one that breaks. A broken low-level lemma in a shared library file can cascade into dozens of downstream proof failures from a single root cause; a broken leaf lemma affects one thing. This matters a lot for a repair benchmark specifically, since it changes whether "repair" means one localized patch or a chain of patches.

**4. Automation reliance (`smt`/`auto`/`algebra`) vs. explicit manual tactics**
Proofs leaning on automation are exposed to a different failure mode — SMT backend version drift (Alt-Ergo/Z3 changes) rather than EasyCrypt engine changes — which can masquerade as an EasyCrypt version issue but isn't really one. Worth tracking separately so it doesn't contaminate your labeling of "hard because of EasyCrypt changes" vs. "hard because of solver changes."

**5. Proof-hopping structure (game-hopping density)**
For crypto proofs specifically, the number of intermediate games/hybrids in a security reduction is a better difficulty signal than raw cryptographic complexity in the abstract — a proof with 10 tightly-coupled game hops has more surface area for one hop's equivalence lemma to break than a single-reduction proof of an equally "complex" primitive.

**6. Presence of `admit`/incomplete proofs**
Worth flagging and probably excluding or bucketing separately — these aren't repair targets in the normal sense, and including them will skew your benchmark's difficulty statistics.

## Weighting recommendation

For an ordinal ladder (not necessarily a numeric score), I'd weight in this order:

1. **Breaking-change severity crossed** (heaviest) — this is the most direct causal factor and the one you can most precisely compute from data you already have tooling for.
2. **Dependency cascade depth** — determines whether "repair" is one fix or many; disproportionately affects actual repair effort versus proof depth alone.
3. **Module/cloning complexity** — a strong secondary signal correlated with #1 but worth tracking independently since it predicts *which* repos are exposed even before you check the changelog.
4. **Proof depth/tactic count** — matters, but mostly as a multiplier on the above (a deep proof with zero breaking changes crossed is still easy; a shallow proof hit by a mechanism change can still be hard).
5. **Crypto complexity** — I'd weight this lower than your original framing suggests, specifically *for a repair ladder* (as opposed to a general benchmark). Crypto complexity mostly matters for whether an LLM needs semantic understanding to find the *right* patch, but doesn't strongly predict *how much* needs to change — a simple primitive's proof can still require large mechanical rewrites if it heavily used cloning, while a complex primitive's proof might need only a one-line tactic swap if it dodged all the breaking changes.
6. **Automation reliance** — lowest weight for the ladder itself, but valuable as a categorical tag for error analysis after the fact.

If it'd help, I can extend `retrieve_relevant_entries.py` (or write a new script) to actually compute a per-repo "breaking-change exposure score" against your changelog data — that would give you factor #1 as a hard number rather than a qualitative judgment.

SmtMap had a structural break in r2025.02 — split into a separate SMT-Array theory and a finite-map theory. Anything importing SmtMap pre-r2025.02 may need updating.
FMap gained range-after-update lemmas the same release.
Distr had a scoping bugfix in r2026.06 (distribution-tagging could previously succeed without Distr in scope).
DList gained one new lemma in r2026.03.
AllCore, DBool, DInterval, FSet, PROM — I found no changelog entries naming these libraries directly in this window. I said so plainly rather than padding it out, and noted the one adjacent trap (FSet vs. the separate MSet/Multisets library, which did change substantially) so it doesn't get conflated.

CoreInt/CoreReal/CoreMap are not meant to be used directly — each says so via an explicit comment in the source. They're raw SMT-binding primitives; Int.ec, Real.ec, and SmtMap.ec are the actual user-facing theories built on top, using friendlier infix names.
Bool.ec is where bool's FinType instance actually lives (clone FinType as BoolFin), not in FinType.ec itself — useful to know if you're chasing down where a FinType-based lemma about booleans comes from.
Real.ec clones Ring.Field to give real its ring/field structure (this is what makes the ring/field tactics work on real-number goals), and separately carries a block of lemmas explicitly commented as being for the "reasoning up to a bad event" proof pattern — the same machinery PROM's collision bound leans on.
All nine showed zero changelog-confirmed version deltas in the r2022.04–r2026.07 window — consistent with them being the most stable part of the tree, though (as always) that reflects the "no PR title named it" caveat rather than a confirmed diff.
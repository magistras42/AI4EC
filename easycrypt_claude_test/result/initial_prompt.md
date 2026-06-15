Your task is to repair the EasyCrypt formal proof project in this isolated
directory so that the main proof project builds/verifies.

Materials in this directory:
- MAC-PRF.ec
- MAC.ec
- PseudoRandFun.ec
- EASYCRYPT_PROOF_REFERENCE.md
- easycrypt_llm_guide.md
- mcp_tools.md
- repair_guidance.md

Constraints:
- Use only local files, local commands, and the already configured EasyCrypt MCP tools.
- Do not use internet search, WebFetch, WebSearch, dependency downloads, repository cloning, or external references.
- You may edit files in this directory.
- Do not modify files outside this directory.
- Do not fake the repair by adding admit/admitted, unsupported axioms, meaningless theorem weakening, or deleting core security claims.
- Preserve the intended project as much as possible. Refactor intermediate lemmas and proof structure only when needed.
- Prefer rg and rg --files for searching. Do not use find -exec, xargs, shell loops, or semicolon-chained command groups; run simple commands one at a time.

Suggested workflow:
1. Read the local guide documents and .ec files.
2. Read repair_guidance.md for task-specific constraints and triage advice.
3. Use EasyCrypt MCP compile/goals/interactive capabilities to locate the first real failure.
4. Repair proofs incrementally and re-verify.
5. Before finishing, confirm there are no admit/admitted statements or leftover debugging commands.
6. Report the verification result and the files changed.

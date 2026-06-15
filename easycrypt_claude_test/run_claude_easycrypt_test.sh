#!/usr/bin/env bash
set -euo pipefail

# Create a fresh EasyCrypt proof-repair challenge directory and start Claude
# Code using the same minimal launch pattern as start_claude_challenge.sh.
#
# Usage:
#   ./run_claude_easycrypt_test.sh [new-session-dir]
#
# Expected local paths:
#   ~/easycrypt/doc/llm/CLAUDE.md
#   ~/easycrypt-mcp/easycrypt_mcp.py
#
# MCP is expected to be configured in the user's Claude Code environment.
# This script keeps startup minimal because extra project setup flows can make
# Claude Code exit immediately in some environments.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_DIR="${1:-$SCRIPT_DIR/claude_easycrypt_repair_$(date +%Y%m%d_%H%M%S)}"
REAL_HOME="$HOME"
EASYCRYPT_DIR="${EASYCRYPT_DIR:-"$REAL_HOME/easycrypt"}"
EASYCRYPT_MCP_DIR="${EASYCRYPT_MCP_DIR:-"$REAL_HOME/easycrypt-mcp"}"

if [[ -e "$SESSION_DIR" ]]; then
  echo "Target directory already exists: $SESSION_DIR" >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude command not found in PATH." >&2
  exit 1
fi

CLAUDE_GUIDE="$EASYCRYPT_DIR/doc/llm/CLAUDE.md"
if [[ ! -f "$CLAUDE_GUIDE" ]]; then
  echo "ERROR: could not find EasyCrypt LLM guide: $CLAUDE_GUIDE" >&2
  exit 1
fi

mkdir -p "$SESSION_DIR"

cp "$SCRIPT_DIR/CS591Project-master/"*.ec "$SESSION_DIR"/
cp "$SCRIPT_DIR/EASYCRYPT_PROOF_REFERENCE.md" "$SESSION_DIR"/
cp "$SCRIPT_DIR/REPAIR_GUIDANCE.md" "$SESSION_DIR/repair_guidance.md"
cp "$CLAUDE_GUIDE" "$SESSION_DIR/easycrypt_llm_guide.md"

cat > "$SESSION_DIR/mcp_tools.md" <<EOF
# MCP tools

The EasyCrypt MCP server should already be configured in Claude Code.

Expected local MCP server path:

\`\`\`
$EASYCRYPT_MCP_DIR/easycrypt_mcp.py
\`\`\`

Use the configured EasyCrypt MCP tools for compile, goal inspection, and
interactive proof work. Do not use web search or external downloads.

Shell search rules:
- Prefer rg for source search.
- Do not use find -exec, xargs, shell loops, or semicolon-chained command groups.
- Example: rg -l "proc \\*" /path/to/easycrypt/theories -g "*.ec" | head -10
EOF

cat > "$SESSION_DIR/initial_prompt.md" <<'EOF'
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
EOF

cd "$SESSION_DIR"

# Keep Claude Code and JS toolchain state inside this challenge directory
# instead of writing dotfiles such as ~/.npmrc, ~/.claude.json, ~/.yarnrc, etc.
SESSION_HOME="$SESSION_DIR/home"
mkdir -p "$SESSION_HOME" "$SESSION_DIR/.config" "$SESSION_DIR/.cache" "$SESSION_DIR/.local/share"

for item in \
  ".claude" \
  ".claude.json" \
  ".config/claude" \
  ".config/claude-code"
do
  if [[ -e "$REAL_HOME/$item" && ! -e "$SESSION_HOME/$item" ]]; then
    mkdir -p "$SESSION_HOME/$(dirname "$item")"
    cp -aL "$REAL_HOME/$item" "$SESSION_HOME/$item"
  fi
done

export HOME="$SESSION_HOME"
export USERPROFILE="$SESSION_HOME"
export XDG_CONFIG_HOME="$SESSION_DIR/.config"
export XDG_CACHE_HOME="$SESSION_DIR/.cache"
export XDG_DATA_HOME="$SESSION_DIR/.local/share"
export NPM_CONFIG_USERCONFIG="$SESSION_HOME/.npmrc"
export NPM_CONFIG_CACHE="$SESSION_DIR/.cache/npm"
export NPM_CONFIG_PREFIX="$SESSION_DIR/.local/npm-prefix"
export npm_config_userconfig="$NPM_CONFIG_USERCONFIG"
export npm_config_cache="$NPM_CONFIG_CACHE"
export npm_config_prefix="$NPM_CONFIG_PREFIX"
export COREPACK_HOME="$SESSION_DIR/.cache/corepack"
export YARN_CACHE_FOLDER="$SESSION_DIR/.cache/yarn"
export PNPM_HOME="$SESSION_DIR/.local/pnpm-home"
export PNPM_STORE_DIR="$SESSION_DIR/.local/pnpm-store"
export BUN_INSTALL="$SESSION_DIR/.local/bun"
export BUN_CONFIG_FILE="$SESSION_HOME/.bunfig.toml"
export NETRC="$SESSION_HOME/.netrc"
export GIT_CONFIG_GLOBAL="$SESSION_HOME/.gitconfig"
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONIOENCODING=utf-8
export NO_COLOR=1
export CLICOLOR=0
export FORCE_COLOR=0

mkdir -p "$SESSION_HOME/.claude" "$SESSION_DIR/.claude"
cat > "$SESSION_HOME/.claude/settings.json" <<'EOF'
{
  "permissions": {
    "allow": [
      "Read",
      "Edit",
      "Write",
      "Grep",
      "Glob",
      "Bash(pwd)",
      "Bash(ls *)",
      "Bash(find *)",
      "Bash(rg *)",
      "Bash(grep *)",
      "Bash(cat *)",
      "Bash(sed *)",
      "Bash(head *)",
      "Bash(tail *)",
      "Bash(wc *)",
      "Bash(diff *)",
      "Bash(which *)",
      "Bash(command -v *)",
      "Bash(python3 *)",
      "Bash(*easycrypt*)",
      "mcp__easycrypt__*"
    ],
    "deny": [
      "WebFetch",
      "WebSearch",
      "Bash(curl *)",
      "Bash(wget *)",
      "Bash(find * -exec *)",
      "Bash(xargs *)",
      "Bash(ssh *)",
      "Bash(scp *)",
      "Bash(git clone *)",
      "Bash(git fetch *)",
      "Bash(git pull *)",
      "Bash(git push *)",
      "Bash(pip install *)",
      "Bash(python3 -m pip *)",
      "Bash(npm install *)",
      "Bash(apt *)",
      "Bash(apt-get *)",
      "Bash(sudo *)",
      "Bash(docker *)"
    ],
    "defaultMode": "acceptEdits"
  }
}
EOF
cp "$SESSION_HOME/.claude/settings.json" "$SESSION_HOME/.claude/settings.local.json"
cp "$SESSION_HOME/.claude/settings.json" "$SESSION_DIR/.claude/settings.local.json"

TRANSCRIPT="$SESSION_DIR/claude_terminal.log"
echo "Claude terminal transcript: $TRANSCRIPT"

if command -v script >/dev/null 2>&1; then
  exec script -q -f -c 'claude "$(cat initial_prompt.md)"' "$TRANSCRIPT"
else
  echo "WARNING: 'script' command not found; full terminal transcript will not be captured."
  exec claude "$(cat initial_prompt.md)"
fi

#!/usr/bin/env bash
set -euo pipefail

# Create a fresh EasyCrypt environment-check directory and start Claude Code
# using the same minimal launch pattern as start_claude_challenge.sh.
#
# Usage:
#   ./run_claude_easycrypt_env_check.sh [new-session-dir]
#
# Expected local paths:
#   ~/easycrypt/doc/llm/CLAUDE.md
#   ~/easycrypt-mcp/easycrypt_mcp.py
#
# MCP is expected to be configured in the user's Claude Code environment.
# This script keeps startup minimal because extra project setup flows can make
# Claude Code exit immediately in some environments.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_DIR="${1:-$SCRIPT_DIR/claude_easycrypt_env_check_$(date +%Y%m%d_%H%M%S)}"
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
cp "$CLAUDE_GUIDE" "$SESSION_DIR/easycrypt_llm_guide.md"

cat > "$SESSION_DIR/mcp_tools.md" <<EOF
# MCP tools

The EasyCrypt MCP server should already be configured in Claude Code.

Expected local MCP server path:

\`\`\`
$EASYCRYPT_MCP_DIR/easycrypt_mcp.py
\`\`\`

Use the configured EasyCrypt MCP tools for compile, goal inspection, and
interactive proof checks. Do not use web search or external downloads.

Shell search rules:
- Prefer rg for source search.
- Do not use find -exec, xargs, shell loops, or semicolon-chained command groups.
- Example: rg -l "proc \\*" /path/to/easycrypt/theories -g "*.ec" | head -10
EOF

cat > "$SESSION_DIR/initial_prompt.md" <<'EOF'
Your task is to check whether this isolated EasyCrypt + Claude Code test
environment is healthy.

Do not repair the proof. Do not edit the .ec source files unless a tiny local
scratch file is needed for an environment check.

Materials in this directory:
- MAC-PRF.ec
- MAC.ec
- PseudoRandFun.ec
- EASYCRYPT_PROOF_REFERENCE.md
- easycrypt_llm_guide.md
- mcp_tools.md

Check and report:
1. The copied project files and guide files are present.
2. The local EasyCrypt checkout exists at EASYCRYPT_DIR_PLACEHOLDER, especially EASYCRYPT_DIR_PLACEHOLDER/doc/llm/CLAUDE.md.
3. The EasyCrypt MCP server exists at EASYCRYPT_MCP_DIR_PLACEHOLDER/easycrypt_mcp.py.
4. The configured EasyCrypt MCP tools are available.
5. A local EasyCrypt compile/check command or MCP compile/check call can run against the copied .ec files.
6. Network/web access is not needed and must not be used.

Use only local files, local commands, and configured MCP tools. Finish with a
concise health report: PASS, PARTIAL, or FAIL, plus the exact failing checks if any.

Shell rules:
- Prefer rg and rg --files for searching.
- Do not use find -exec. Claude Code treats find -exec as arbitrary command execution and it cannot be auto-allowed by a broad find permission.
- Do not use xargs, shell loops, or semicolon-chained command groups for inspection. Run simple commands one at a time.
EOF

sed -i.bak \
  -e "s|EASYCRYPT_DIR_PLACEHOLDER|$EASYCRYPT_DIR|g" \
  -e "s|EASYCRYPT_MCP_DIR_PLACEHOLDER|$EASYCRYPT_MCP_DIR|g" \
  "$SESSION_DIR/initial_prompt.md"
rm -f "$SESSION_DIR/initial_prompt.md.bak"

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

#!/usr/bin/env bash
set -euo pipefail

# Clean a raw Claude terminal transcript captured by script(1).
#
# Usage:
#   ./clean_claude_log.sh path/to/claude_terminal.log [output.log]
#   ./clean_claude_log.sh path/to/session-dir
#
# If a directory is provided, this script reads:
#   <dir>/claude_terminal.log
# and writes:
#   <dir>/claude_terminal.clean.log

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 path/to/claude_terminal.log [output.log]" >&2
  echo "   or: $0 path/to/session-dir" >&2
  exit 1
fi

if [[ -d "$TARGET" ]]; then
  INPUT="$TARGET/claude_terminal.log"
  OUTPUT="${2:-"$TARGET/claude_terminal.clean.log"}"
else
  INPUT="$TARGET"
  if [[ -n "${2:-}" ]]; then
    OUTPUT="$2"
  else
    base="${INPUT%.*}"
    if [[ "$base" == "$INPUT" ]]; then
      OUTPUT="$INPUT.clean"
    else
      OUTPUT="$base.clean.${INPUT##*.}"
    fi
  fi
fi

if [[ ! -f "$INPUT" ]]; then
  echo "ERROR: input log not found: $INPUT" >&2
  exit 1
fi

python3 - "$INPUT" "$OUTPUT" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
data = src.read_bytes().decode("utf-8", errors="replace")

# Remove script(1) header/footer lines when present.
data = re.sub(r"^Script started on .*\n", "", data)
data = re.sub(r"\nScript done on .*$", "\n", data, flags=re.S)

# Strip common ANSI/VT control sequences captured from the interactive TTY.
data = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", data)
data = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", data)
data = re.sub(r"\x1b[()][A-Za-z0-9]", "", data)

# Normalize carriage returns from terminal redraws.
data = data.replace("\r\n", "\n").replace("\r", "\n")

# Remove remaining non-printing control characters except tabs/newlines.
data = "".join(ch for ch in data if ch == "\n" or ch == "\t" or ord(ch) >= 32)

# Collapse excessive redraw blank lines without summarizing content.
data = re.sub(r"\n{4,}", "\n\n\n", data)

dst.write_text(data, encoding="utf-8")
print(dst)
PY

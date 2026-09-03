#!/bin/bash
# Picks an interpreter. Changing it here does NOT recompile the bundle, so it never
# changes the cdhash and never drops permissions already granted.
BASE="${DESKTOP_MCP_HOME:-$HOME/.local/share/desktop-mcp}"
for py in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$py" ] && exec "$py" "$BASE/server.py" serve
done
echo "no python3 found" >&2
exit 1

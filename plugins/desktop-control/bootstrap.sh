#!/usr/bin/env bash
# Entry point used by the Claude Code plugin. Compiles the macOS helpers the first
# time and then execs the server in stdio mode. On Linux there is nothing to build.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${DESKTOP_MCP_HOME:-$HOME/.local/share/desktop-mcp}"

if [ "$(uname -s)" = Darwin ] && [ ! -x "$HOME_DIR/bin/helper" ]; then
  mkdir -p "$HOME_DIR/bin"
  /usr/bin/clang -O2 -o "$HOME_DIR/bin/helper" "$DIR/macos/helper.c" \
      -framework ApplicationServices >&2 || true
  /usr/bin/clang -O2 -fobjc-arc -o "$HOME_DIR/bin/overlay" "$DIR/macos/overlay.m" \
      -framework Cocoa -framework QuartzCore >&2 || true
fi

export DESKTOP_MCP_HOME="$HOME_DIR"
exec python3 "$DIR/server.py" stdio

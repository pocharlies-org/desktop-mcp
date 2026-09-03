#!/bin/bash
# Starts and supervises the daemon VIA LAUNCHSERVICES. `open` is mandatory: it places
# the process in the user's Aqua session, the only one with a WindowServer connection.
# A direct exec from launchd or over SSH captures nothing and fails with
# "could not create image from display".
BASE="${DESKTOP_MCP_HOME:-$HOME/.local/share/desktop-mcp}"
APP="$BASE/DesktopMCP.app"
PORT="${DESKTOP_MCP_PORT:-8811}"
up() { /usr/bin/nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; }

if ! up; then
  /usr/bin/open -a "$APP" || exit 1
  for _ in $(seq 1 20); do up && break; sleep 1; done
fi
up || exit 1

# Exit non-zero when the server dies so KeepAlive brings it back.
while up; do sleep 10; done
exit 1

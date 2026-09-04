#!/usr/bin/env bash
# SessionStart hook: announce the effective backend inside the session itself.
#
# Runs as a child of the CLI, so it inherits the session's real environment.
# That is the point: the model name in the picker is what Claude Code believes
# it is talking to, and it can disagree with where the request actually lands
# (a row labelled "local" answered from the subscription once). ANTHROPIC_BASE_URL
# in this process is the same value that decides the destination, so it cannot lie.
cat >/dev/null 2>&1   # drain the hook payload

if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
  host=${ANTHROPIC_BASE_URL#*://}; host=${host%%/*}
  msg="🟢 LOCAL · requests go to ${host} — your subscription is not being used"
else
  msg="🔵 ANTHROPIC · subscription. Local models are NOT active in this tab"
fi

python3 -c 'import json,sys; print(json.dumps({"systemMessage": sys.argv[1]}))' "$msg"

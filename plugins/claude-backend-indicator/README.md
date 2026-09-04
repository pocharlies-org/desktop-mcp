# claude-backend-indicator

Run some Claude Code tabs against your Anthropic subscription and others against a
local LiteLLM gateway — and always know which one you are in.

## Why

Claude Code speaks the Anthropic Messages API, and a LiteLLM gateway exposes the same
`/v1/messages`, so pointing `ANTHROPIC_BASE_URL` at your gateway is enough to serve a
session from a self-hosted model. The catch is telling the two apart afterwards.

The model picker persists your choice, and the CLI reports the model it *believes* it is
using. Those two agree with the actual destination only if something sets the environment
before the process starts. They can diverge: a row labelled `tooling (LiteLLM local)`
answered from the subscription, with the tab cheerfully identifying itself as Opus.

So this plugin does not read the model name. Its hook runs as a child of the CLI and reads
`ANTHROPIC_BASE_URL` from the session's own process — the same value that decides where the
request lands. Every session opens with one line:

    🟢 LOCAL · requests go to litellm.lan.e-dani.com — your subscription is not being used
    🔵 ANTHROPIC · subscription. Local models are NOT active in this tab

Per tab, not per window: with five tabs open each one states its own backend.

## Install

    /plugin marketplace add pocharlies-org/desktop-mcp
    /plugin install claude-backend-indicator@pocharlies-plugins

The banner works from the next session you open. Nothing else is required for it.

## Routing (optional)

The banner reports the backend; `bin/vscode-wrapper.sh` is what selects it. Point the
VS Code extension at the wrapper and it reads your picker choice from
`~/.claude/settings.json` — where the picker persists it — and loads the gateway
environment when the model looks local (`qwen*`, `tooling*`, `or-*`):

    // VS Code: ~/.vscode-server/data/User/settings.json  (or User/settings.json locally)
    "claudeCode.claudeProcessWrapper": "/path/to/plugins/claude-backend-indicator/bin/vscode-wrapper.sh"

This one setting is not something a Claude plugin can write for you — it belongs to the
VS Code extension, and on a remote host it must go in `data/User/`, not `data/Machine/`,
or it is silently ignored.

Gateway credentials live in `~/.config/claude-local/env` (mode 600), sourced by the wrapper:

    export ANTHROPIC_BASE_URL="https://litellm.your.lan"
    export ANTHROPIC_AUTH_TOKEN="sk-..."           # a virtual key, not your subscription
    export ANTHROPIC_SMALL_FAST_MODEL="small-model" # background tasks; must be allowed for the key
    export ANTHROPIC_DEFAULT_OPUS_MODEL="your-local-model"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="your-local-model"
    export CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144    # the engine's real window

Add rows to the picker so the local models are selectable (user settings only —
`modelPicker` is ignored from a project checkout):

    "modelPicker": { "options": [
      { "model": "opus",   "label": "Opus (Anthropic)" },
      { "model": "tooling", "label": "tooling (local)", "behavesAs": "claude-sonnet-5" }
    ]}

`behavesAs` names a model this release already knows, so a model outside the catalog still
gets sane capability defaults instead of being refused.

`bin/claude-default` edits the defaults for *new* tabs without touching JSON by hand:

    claude-default              # show state
    claude-default local        # new tabs -> local model
    claude-default opus         # new tabs -> Anthropic
    claude-default agent NAME   # default agent
    claude-default subagents sonnet|opus|inherit

## What this cannot do

**Switching backend inside an open tab.** The environment is fixed at spawn; the picker's
mid-session `set_model` travels over the control stream and changes the model name, not the
host. Open a new tab — *Reopen Closed Session* carries the history over.

Sessions on the gateway also lose claude.ai connectors: an explicit auth source takes
precedence over your login, and the CLI says so on startup. Subscription tabs keep them.

With a process wrapper configured the extension stops auto-updating (it skips the update
check by design), so update it yourself now and then.

## Verifying, when you distrust the banner

Ask the server, not the client:

    kubectl logs -n litellm deploy/litellm --since=5m | grep -c "POST /v1/messages"

Type something in the tab and re-run it. `0` means the turn went to Anthropic. Two
independent sources — the client's environment and the gateway's own log — agreeing is the
guarantee; the picker label is not evidence.

## If local rows start returning 500

A gateway alias only resolves while its backend is resident. If your models are served by
a profile that can be switched away (a shared GPU arbiter, a scaled-down deployment), the
Service loses its endpoints and requests fail in a way that reads like a routing bug. Check
the backend is actually up before debugging the wrapper.

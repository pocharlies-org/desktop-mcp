# desktop-mcp

Full desktop control over MCP: screenshots, mouse, keyboard, applications,
AppleScript/JXA and the macOS accessibility tree. One tool surface, two backends —
**macOS** and **Linux/X11**.

Works with [Claude Code](https://claude.com/claude-code) (as a plugin or a plain MCP
server) and with [opencode](https://opencode.ai). No third-party Python packages;
stdlib only, Python 3.9+.

While the desktop is under control it draws a **pulsing blue glow around every screen
and a banner naming the session that is driving it**. Whoever is sitting at the machine
should never have to guess.

## Install

```sh
git clone https://github.com/pocharlies-org/desktop-mcp.git
cd desktop-mcp
./install.sh
```

That builds the native helpers, registers the server with every client it finds
(Claude Code and opencode) and runs a self-test. Flags: `--claude`, `--opencode`,
`--no-register`, `--daemon`, `--uninstall`.

### As a Claude Code plugin

```
/plugin marketplace add pocharlies-org/desktop-mcp
/plugin install desktop-control@pocharlies-plugins
```

The plugin compiles the macOS helpers on first run.

### Dependencies

| | |
|---|---|
| macOS | [`cliclick`](https://github.com/BlueM/cliclick) (`brew install cliclick`) and the Xcode Command Line Tools |
| Linux | `xdotool`, `imagemagick`, `wmctrl`, `xrandr` — **X11 only**, no Wayland |

## macOS permissions

Both are granted to the process that **runs** the server — your terminal in stdio mode,
or `DesktopMCP.app` with `--daemon`:

- **System Settings → Privacy & Security → Screen Recording**
- **System Settings → Privacy & Security → Accessibility**

Restart the client afterwards: macOS reads the grants at process start.

Call `request_permissions` to register the app in both panes and raise the dialogs.
Screen Recording can be granted from its dialog; **Accessibility cannot** — the dialog
only opens Settings, the switch has to be flipped by a human.

## Tools

| Tool | What it does |
|---|---|
| `screen_info` | displays (origin, logical size, pixels), lock state, foreground app, whether input works |
| `screenshot` | capture a display and return it as an image |
| `click` / `move` / `drag` / `scroll` | pointer, with modifiers |
| `type_text` / `press_key` | keyboard, with an `expect_app` guard |
| `app` | list / open / focus / quit / frontmost |
| `applescript` | AppleScript or JXA (macOS) |
| `ui_elements` | accessibility tree with each control's center (macOS); window list (X11) |
| `set_control_context` | declare who is controlling and what for — this is the on-screen banner |
| `overlay` | force or inspect the on-screen notice |
| `request_permissions` | macOS: register in the privacy panes and raise the dialogs |

### Coordinates

`screenshot` returns an image; the coordinates you pass back are **pixels of that
image** (`space: "image"`, the default) and the server converts them to global logical
points. That is what stops a Retina 2× display from doubling every click offset. Pass
`space: "screen"` for global logical points — which is what `ui_elements` gives you, and
aiming by accessibility tree is far more reliable than aiming by vision.

### Typing goes where focus is

`type_text` and `press_key` take `expect_app` and refuse to run unless that app is in
the foreground. Use it whenever you open something first: a script that fails to open
what it claims leaves focus untouched, and your text lands in whatever the human had
open.

## Remote use (daemon mode)

`./install.sh --daemon` installs a LaunchAgent (macOS) or a `systemd --user` unit
(Linux) that runs a loopback TCP daemon speaking newline-delimited JSON-RPC. Drive it
from another machine:

```sh
ssh somehost /usr/bin/nc 127.0.0.1 8811
```

SSH is the whole authentication story — the daemon binds `127.0.0.1` only and has no
tokens. Register it in a client as a stdio server whose command is that `ssh` line.

## Limitations, plainly

- **Linux is X11 only.** Wayland has no equivalent of `xdotool`'s input injection.
- **The consent overlay is macOS only** so far. On Linux `overlay` reports
  `available: false`; an X11 version needs an override-redirect window with an input
  shape for click-through.
- **A locked screen means nothing works** — no capture, no input. On Linux, a session
  that is logged in but not on the active VT (a greeter in front) captures solid black;
  the server reports that as locked rather than handing you a black image.
- **Accessibility tree is macOS only.** On X11 `ui_elements` lists windows.

## macOS gotchas worth knowing

These cost real time to find:

- `could not create image from display` means **three different things**: launched over
  SSH (no WindowServer connection — no permission fixes that), launched by launchd
  without a GUI session, or the screen is locked. The only way into the Aqua session
  from launchd or SSH is `open -W -a Foo.app`.
- **`execv` from a bundle's binary destroys its TCC identity.** The process stops being
  the bundle and becomes the interpreter, and grants made to the app no longer apply.
  The launcher here `fork`s so the bundle stays alive and the child inherits it as
  responsible process.
- **An ad-hoc signature pins the grant to the binary's hash.** Recompiling leaves the
  switch enabled in System Settings and the permission dead. That is why `--daemon`
  warns you not to re-run the installer after granting.
- **Without Accessibility, input helpers exit 0 and the event is dropped** — no error.
  The server checks `AXIsProcessTrusted()` and fails loudly instead of reporting a move
  that never happened.
- Check what is actually granted with
  `sqlite3 "/Library/Application Support/com.apple.TCC/TCC.db" "select service,client,auth_value from access"`.

## License

MIT

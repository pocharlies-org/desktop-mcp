#!/usr/bin/env bash
#
# desktop-mcp installer.
#
#   ./install.sh                    build + register with every client found
#   ./install.sh --claude           register with Claude Code only
#   ./install.sh --opencode         register with opencode only
#   ./install.sh --no-register      build only
#   ./install.sh --daemon           macOS: also install the LaunchAgent (remote use)
#   ./install.sh --uninstall        remove everything this script installed
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${DESKTOP_MCP_HOME:-$HOME/.local/share/desktop-mcp}"
APP="$HOME_DIR/DesktopMCP.app"
LABEL="io.github.pocharlies-org.desktop-mcp"
OS="$(uname -s)"

DO_CLAUDE=auto
DO_OPENCODE=auto
DO_DAEMON=no
DO_BUILD=yes

for arg in "$@"; do
  case "$arg" in
    --claude)      DO_CLAUDE=yes; DO_OPENCODE=no ;;
    --opencode)    DO_OPENCODE=yes; DO_CLAUDE=no ;;
    --no-register) DO_CLAUDE=no; DO_OPENCODE=no ;;
    --daemon)      DO_DAEMON=yes ;;
    --uninstall)   DO_BUILD=uninstall ;;
    -h|--help)     sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------- uninstall
if [ "$DO_BUILD" = uninstall ]; then
  if [ "$OS" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
    pkill -f "desktop-mcp/server.py" 2>/dev/null || true
  else
    systemctl --user disable --now desktop-mcp.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/desktop-mcp.service"
    systemctl --user daemon-reload 2>/dev/null || true
  fi
  rm -rf "$HOME_DIR"
  command -v claude >/dev/null 2>&1 && claude mcp remove desktop --scope user 2>/dev/null || true
  say "removed. The opencode entry in ~/.config/opencode/opencode.json must be deleted by hand."
  exit 0
fi

# ---------------------------------------------------------------- dependencies
say "checking dependencies ($OS)"
missing=()
if [ "$OS" = Darwin ]; then
  [ -x /opt/homebrew/bin/cliclick ] || [ -x /usr/local/bin/cliclick ] || missing+=(cliclick)
  [ -x /usr/bin/clang ] || { echo "Xcode Command Line Tools are required: xcode-select --install" >&2; exit 1; }
  if [ ${#missing[@]} -gt 0 ]; then
    if command -v brew >/dev/null 2>&1; then
      say "installing: ${missing[*]}"
      brew install "${missing[@]}"
    else
      echo "missing ${missing[*]} and Homebrew is not installed. See https://brew.sh" >&2
      exit 1
    fi
  fi
else
  for b in xdotool import convert wmctrl xrandr; do
    command -v "$b" >/dev/null 2>&1 || missing+=("$b")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo "missing: ${missing[*]}" >&2
    echo "  Debian/Ubuntu: sudo apt install xdotool imagemagick wmctrl x11-xserver-utils" >&2
    echo "  Fedora:        sudo dnf install xdotool ImageMagick wmctrl xrandr" >&2
    echo "  Arch:          sudo pacman -S xdotool imagemagick wmctrl xorg-xrandr" >&2
    exit 1
  fi
  if [ "${XDG_SESSION_TYPE:-x11}" = wayland ]; then
    warn "Wayland detected: this only works on X11."
  fi
fi

# ---------------------------------------------------------------- build
say "installing into $HOME_DIR"
mkdir -p "$HOME_DIR/bin"
install -m 0755 "$REPO/plugins/desktop-control/server.py" "$HOME_DIR/server.py"

if [ "$OS" = Darwin ]; then
  say "compiling helper + overlay"
  /usr/bin/clang -O2 -o "$HOME_DIR/bin/helper" "$REPO/plugins/desktop-control/macos/helper.c" \
      -framework ApplicationServices
  /usr/bin/clang -O2 -fobjc-arc -o "$HOME_DIR/bin/overlay" "$REPO/plugins/desktop-control/macos/overlay.m" \
      -framework Cocoa -framework QuartzCore
fi

# ---------------------------------------------------------------- daemon (optional)
if [ "$DO_DAEMON" = yes ]; then
  if [ "$OS" = Darwin ]; then
    say "building DesktopMCP.app and loading the LaunchAgent"
    mkdir -p "$APP/Contents/MacOS"
    cp -f "$REPO/plugins/desktop-control/macos/Info.plist" "$APP/Contents/Info.plist"
    install -m 0755 "$REPO/plugins/desktop-control/macos/boot.sh" "$HOME_DIR/boot.sh"
    install -m 0755 "$REPO/plugins/desktop-control/macos/run.sh" "$HOME_DIR/run.sh"
    /usr/bin/clang -O2 -o "$APP/Contents/MacOS/DesktopMCP" "$REPO/plugins/desktop-control/macos/launcher.c" \
        -framework ApplicationServices
    /usr/bin/codesign --force --sign - "$APP" >/dev/null 2>&1 || true
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$HOME/Library/LaunchAgents/$LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$HOME_DIR/run.sh</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>DESKTOP_MCP_HOME</key><string>$HOME_DIR</string>
    <key>DESKTOP_MCP_PORT</key><string>${DESKTOP_MCP_PORT:-8811}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>ProcessType</key><string>Interactive</string>
  <key>WorkingDirectory</key><string>$HOME_DIR</string>
</dict>
</plist>
PLIST
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist"
    warn "do NOT re-run this script once you have granted the permissions:"
    warn "the bundle is ad-hoc signed, so recompiling invalidates them while the"
    warn "switch in System Settings still looks enabled."
  else
    say "installing the systemd --user unit"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/desktop-mcp.service" <<UNIT
[Unit]
Description=desktop-mcp - desktop control over MCP
After=graphical-session.target

[Service]
Type=simple
Environment=DISPLAY=${DISPLAY:-:0}
Environment=XAUTHORITY=${XAUTHORITY:-$HOME/.Xauthority}
Environment=DESKTOP_MCP_HOME=$HOME_DIR
Environment=DESKTOP_MCP_PORT=${DESKTOP_MCP_PORT:-8811}
ExecStart=/usr/bin/env python3 $HOME_DIR/server.py serve
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now desktop-mcp.service
  fi
fi

# ---------------------------------------------------------------- clients
PY="$(command -v python3)"

# Each of these returns 0 explicitly: under `set -e` a bare `return` inherits the
# status of the last test, so a "not installed" branch would abort the whole script.
register_claude() {
  local bin
  bin="$(command -v claude || true)"
  [ -n "$bin" ] || { [ -x "$HOME/.local/bin/claude" ] && bin="$HOME/.local/bin/claude"; }
  [ -n "$bin" ] || { [ -x "$HOME/.claude/local/claude" ] && bin="$HOME/.claude/local/claude"; }
  if [ -z "$bin" ]; then
    [ "$DO_CLAUDE" = yes ] && warn "claude CLI not found"
    return 0
  fi
  say "registering with Claude Code (user scope)"
  "$bin" mcp remove desktop --scope user >/dev/null 2>&1 || true
  "$bin" mcp add desktop --scope user -- "$PY" "$HOME_DIR/server.py" stdio \
      || warn "claude mcp add failed"
  return 0
}

register_opencode() {
  local cfg="$HOME/.config/opencode/opencode.json"
  if [ ! -f "$cfg" ]; then
    [ "$DO_OPENCODE" = yes ] && warn "opencode config not found at $cfg"
    return 0
  fi
  say "registering with opencode ($cfg)"
  "$PY" - "$cfg" "$PY" "$HOME_DIR/server.py" <<'PYEOF'
import json, shutil, sys
cfg, py, script = sys.argv[1], sys.argv[2], sys.argv[3]
shutil.copyfile(cfg, cfg + ".bak-desktop-mcp")
with open(cfg) as f:
    d = json.load(f)
d.setdefault("mcp", {})["desktop"] = {
    "type": "local", "command": [py, script, "stdio"], "enabled": True}
with open(cfg, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
print("  backup at %s.bak-desktop-mcp" % cfg)
PYEOF
  return 0
}

if [ "$DO_CLAUDE" != no ]; then register_claude; fi
if [ "$DO_OPENCODE" != no ]; then register_opencode; fi

# ---------------------------------------------------------------- done
say "self-test"
"$PY" "$HOME_DIR/server.py" selftest || true

cat <<EOF

Installed. $( [ "$OS" = Darwin ] && echo "

macOS needs two permissions, and they are granted to the process that RUNS the
server -- your terminal in stdio mode, or DesktopMCP.app with --daemon:

  System Settings > Privacy & Security > Screen Recording
  System Settings > Privacy & Security > Accessibility

Restart the client afterwards: macOS reads the grants at process start. Without
Accessibility, mouse and keyboard calls are dropped SILENTLY -- the server checks
for it and returns an explicit error instead." )
EOF

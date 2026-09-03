#!/usr/bin/env python3
"""desktop-mcp — full desktop control over MCP: screen, mouse, keyboard, apps.

One tool surface, two backends:
  macOS       cliclick + screencapture + AppleScript/JXA + accessibility tree
  Linux/X11   xdotool + ImageMagick + wmctrl

Two transports:
  stdio   (default) the MCP client spawns this process directly. Simplest, and on
          macOS the process inherits the terminal's privacy permissions.
  serve   a loopback TCP daemon speaking newline-delimited JSON-RPC, for driving a
          desktop from another machine over `ssh host nc 127.0.0.1 <port>`.

No third-party Python packages. Stdlib only, Python 3.9+.
"""
import base64
import json
import os
import re
import shlex
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

IS_MAC = sys.platform == "darwin"
PROTOCOL_VERSION = "2024-11-05"
VERSION = "1.0.0"
PORT = int(os.environ.get("DESKTOP_MCP_PORT", "8811"))
OVERLAY_IDLE = float(os.environ.get("DESKTOP_MCP_OVERLAY_IDLE", "15"))


def _home():
    """Where install.sh put the compiled helpers. Falls back to a build/ dir next to
    this file so the repo can be run straight from a checkout."""
    env = os.environ.get("DESKTOP_MCP_HOME")
    if env:
        return env
    default = os.path.join(os.path.expanduser("~"), ".local", "share", "desktop-mcp")
    if os.path.isdir(os.path.join(default, "bin")):
        return default
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "build")


BASE = _home()
HELPER = os.path.join(BASE, "bin", "helper")
OVERLAY = os.path.join(BASE, "bin", "overlay")
LOG = os.path.join(BASE, "desktop-mcp.log")

# Who is driving this machine and why. Shown on the controlled screen.
CONTROL = {"session": None, "purpose": None}

MODS = {"cmd", "alt", "ctrl", "shift", "fn"}
ALIASES = {
    "escape": "esc", "command": "cmd", "super": "cmd", "win": "cmd",
    "option": "alt", "control": "ctrl", "up": "arrow-up", "down": "arrow-down",
    "left": "arrow-left", "right": "arrow-right", "backspace": "delete",
    "del": "fwd-delete", "pgup": "page-up", "pgdn": "page-down", "ret": "return",
    "enter": "return", "esc": "esc",
}
KEYNAMES = {
    "arrow-down", "arrow-left", "arrow-right", "arrow-up", "brightness-down",
    "brightness-up", "delete", "end", "esc", "fwd-delete", "home", "mute",
    "page-down", "page-up", "play-next", "play-pause", "play-previous",
    "return", "space", "tab", "volume-down", "volume-up",
}
KEYNAMES |= set("f%d" % i for i in range(1, 17))


class ToolError(Exception):
    pass


def run(cmd, timeout=30, check=True, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
    if check and p.returncode != 0:
        raise ToolError("%s -> rc=%d %s" % (" ".join(shlex.quote(c) for c in cmd),
                                            p.returncode,
                                            (p.stderr or p.stdout).strip()[:400]))
    return p.stdout


def logline(msg):
    try:
        d = os.path.dirname(LOG)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(LOG, "a") as f:
            f.write("%s pid=%d %s\n" % (time.strftime("%F %T"), os.getpid(), msg))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Consent overlay: while the desktop is under remote control, a pulsing blue glow
# is drawn around every screen with a banner naming the session and its purpose.
# A person sitting at the machine should never have to guess.
# ---------------------------------------------------------------------------
class OverlayManager(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.text = None
        self.deadline = 0.0
        self.thread = None

    def available(self):
        return os.path.exists(OVERLAY)

    def banner(self):
        return ("This computer is being controlled by the MCP session "
                "«%s» for «%s»"
                % (CONTROL["session"] or "UNIDENTIFIED",
                   CONTROL["purpose"] or "undeclared purpose"))

    def _kill(self):
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None
        self.text = None

    def _watch(self):
        while True:
            time.sleep(1)
            with self.lock:
                if self.proc is None:
                    return
                if time.time() >= self.deadline:
                    self._kill()
                    return

    def touch(self, seconds=None):
        if not self.available():
            return False
        with self.lock:
            self.deadline = time.time() + (seconds or OVERLAY_IDLE)
            text = self.banner()
            if self.proc is None or self.proc.poll() is not None or self.text != text:
                self._kill()
                self.proc = subprocess.Popen([OVERLAY, "--text", text],
                                             stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                self.text = text
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._watch)
                self.thread.daemon = True
                self.thread.start()
            return True

    def stop(self):
        with self.lock:
            self.deadline = 0.0
            self._kill()

    def status(self):
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None
            return {"available": self.available(), "visible": alive,
                    "banner": self.text or self.banner(),
                    "seconds_left": max(0, round(self.deadline - time.time(), 1)) if alive else 0,
                    "session": CONTROL["session"], "purpose": CONTROL["purpose"]}


OVERLAY_MGR = OverlayManager()


# ---------------------------------------------------------------------------
# macOS backend
# ---------------------------------------------------------------------------
UI_JXA = r"""
function run(argv) {
  const se = Application('System Events');
  let proc = argv[0] ? se.processes.byName(argv[0])
                     : se.processes.whose({frontmost: true})[0];
  const deadline = Date.now() + 12000;
  const out = [];
  function walk(el, depth, path) {
    if (out.length > 250 || Date.now() > deadline || depth > 4) return;
    let role='', name='', val='', pos=null, size=null, sub='';
    try { role = el.role(); } catch (e) {}
    try { name = el.name() || el.description() || el.title() || ''; } catch (e) {}
    try { sub = el.subrole() || ''; } catch (e) {}
    try { const v = el.value(); if (typeof v === 'string') val = v.slice(0,120); } catch (e) {}
    try { pos = el.position(); size = el.size(); } catch (e) {}
    const clickable = /Button|MenuItem|CheckBox|RadioButton|PopUpButton|TextField|TextArea|Link|Tab|Row|ComboBox|Slider|Cell/.test(role);
    if ((name || val || clickable) && pos && size) {
      out.push({ path: path, role: role, subrole: sub, name: name, value: val,
                 center: [Math.round(pos[0]+size[0]/2), Math.round(pos[1]+size[1]/2)],
                 size: size });
    }
    let kids = [];
    try { kids = el.uiElements(); } catch (e) { return; }
    for (let i = 0; i < kids.length && i < 60; i++) walk(kids[i], depth+1, path + '/' + i);
  }
  let wins = [];
  try { wins = proc.windows(); } catch (e) {}
  for (let w = 0; w < wins.length && w < 3; w++) walk(wins[w], 0, 'w' + w);
  return JSON.stringify({ app: proc.name(), windows: wins.length,
                          truncated: out.length > 250, elements: out }, null, 1);
}
"""


class DarwinBackend(object):
    name = "darwin"
    CLICLICK = os.environ.get("CLICLICK", "/opt/homebrew/bin/cliclick")
    SCREENCAPTURE = "/usr/sbin/screencapture"
    SIPS = "/usr/bin/sips"
    OSASCRIPT = "/usr/bin/osascript"

    def __init__(self):
        if not os.path.exists(self.CLICLICK):
            for alt in ("/usr/local/bin/cliclick", "/opt/local/bin/cliclick"):
                if os.path.exists(alt):
                    self.CLICLICK = alt
                    break

    def displays(self):
        return json.loads(run([HELPER, "displays"]))

    def locked(self):
        out = subprocess.run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-r"],
                             capture_output=True, text=True).stdout
        # The key only exists while the screen IS locked, so testing for the key
        # alone always says "locked". Test the value.
        return "CGSSessionScreenIsLocked" in out and \
            "Yes" in out.split("CGSSessionScreenIsLocked")[1][:20]

    def input_ready(self):
        try:
            return json.loads(run([HELPER, "trusted"]))["accessibility_trusted"]
        except Exception:
            return False

    def frontmost(self):
        try:
            return run([self.OSASCRIPT, "-e",
                        'tell application "System Events" to get name of first process '
                        'whose frontmost is true'], timeout=10).strip()
        except Exception:
            return "?"

    def capture(self, idx, path):
        run([self.SCREENCAPTURE, "-x", "-o", "-D", str(idx), "-t", "png", path], timeout=25)

    def dims(self, path):
        out = run([self.SIPS, "-g", "pixelWidth", "-g", "pixelHeight", path], timeout=15)
        w = h = 0
        for line in out.splitlines():
            if "pixelWidth:" in line:
                w = int(line.split(":")[1])
            elif "pixelHeight:" in line:
                h = int(line.split(":")[1])
        return w, h

    def downscale(self, path, max_w):
        run([self.SIPS, "-Z", str(max_w), path], timeout=25)

    def _mods_cmd(self, mods, actions):
        cmd = [self.CLICLICK]
        if mods:
            cmd.append("kd:" + ",".join(mods))
        cmd += actions
        if mods:
            cmd.append("ku:" + ",".join(mods))
        return cmd

    def click(self, x, y, button, count, mods):
        act = "rc" if button == "right" else ("tc" if count >= 3 else
                                              ("dc" if count == 2 else "c"))
        run(self._mods_cmd(mods, ["m:%g,%g" % (x, y), "w:60", "%s:%g,%g" % (act, x, y)]))

    def move(self, x, y):
        run([self.CLICLICK, "m:%g,%g" % (x, y)])

    def drag(self, x1, y1, x2, y2, mods):
        run(self._mods_cmd(mods, ["m:%g,%g" % (x1, y1), "w:80", "dd:%g,%g" % (x1, y1),
                                  "w:80", "m:%g,%g" % (x2, y2), "w:80",
                                  "du:%g,%g" % (x2, y2)]))

    def scroll(self, direction, amount, x, y):
        step = amount * 40
        dx, dy = {"down": (0, -step), "up": (0, step),
                  "left": (step, 0), "right": (-step, 0)}[direction]
        run([HELPER, "scroll", str(dx), str(dy), str(x), str(y)])

    def type_text(self, text, delay_ms):
        run([self.CLICLICK, "-w", str(delay_ms), "t:" + text], timeout=180)

    def press(self, mods, key):
        action = ["kp:" + key] if key in KEYNAMES else ["t:" + key]
        run(self._mods_cmd(mods, action))

    def app_list(self):
        out = run([self.OSASCRIPT, "-e",
                   'tell application "System Events" to get name of every process '
                   'whose background only is false'], timeout=20)
        return sorted(x.strip() for x in out.strip().split(","))

    def app_open(self, name):
        run(["/usr/bin/open", "-a", name], timeout=30)

    def app_focus(self, name):
        run([self.OSASCRIPT, "-e", "tell application %s to activate" % json.dumps(name)],
            timeout=20)

    def app_quit(self, name):
        run([self.OSASCRIPT, "-e", "tell application %s to quit" % json.dumps(name)],
            timeout=20)

    def script(self, src, lang, timeout):
        cmd = [self.OSASCRIPT] + (["-l", "JavaScript"] if lang in ("jxa", "javascript") else [])
        return run(cmd + ["-e", src], timeout=timeout).strip() or "(no output)"

    def ui_elements(self, app):
        cmd = [self.OSASCRIPT, "-l", "JavaScript", "-e", UI_JXA]
        if app:
            cmd.append(app)
        try:
            return json.loads(run(cmd, timeout=40))
        except ToolError as e:
            raise ToolError(str(e) + "  (missing Accessibility permission?)")

    def request_permissions(self):
        out = {}
        for cmd in ("screenaccess", "axprompt"):
            try:
                out[cmd] = json.loads(run([HELPER, cmd], timeout=60))
            except Exception as e:
                out[cmd] = {"error": str(e)}
        return out


# ---------------------------------------------------------------------------
# Linux / X11 backend
# ---------------------------------------------------------------------------
XKEYS = {
    "return": "Return", "esc": "Escape", "space": "space", "tab": "Tab",
    "delete": "BackSpace", "fwd-delete": "Delete", "home": "Home", "end": "End",
    "page-up": "Prior", "page-down": "Next", "arrow-up": "Up", "arrow-down": "Down",
    "arrow-left": "Left", "arrow-right": "Right",
    "volume-up": "XF86AudioRaiseVolume", "volume-down": "XF86AudioLowerVolume",
    "mute": "XF86AudioMute", "play-pause": "XF86AudioPlay",
    "play-next": "XF86AudioNext", "play-previous": "XF86AudioPrev",
    "brightness-up": "XF86MonBrightnessUp", "brightness-down": "XF86MonBrightnessDown",
}
XKEYS.update(dict(("f%d" % i, "F%d" % i) for i in range(1, 17)))
XMODS = {"cmd": "super", "alt": "alt", "ctrl": "ctrl", "shift": "shift", "fn": "super"}


class LinuxBackend(object):
    name = "linux-x11"

    def __init__(self):
        self.env = {"DISPLAY": os.environ.get("DISPLAY", ":0"),
                    "XAUTHORITY": os.environ.get(
                        "XAUTHORITY", os.path.join(os.path.expanduser("~"), ".Xauthority"))}

    def _run(self, cmd, **kw):
        kw.setdefault("env", self.env)
        return run(cmd, **kw)

    def displays(self):
        out = self._run(["xrandr", "--listmonitors"], timeout=15)
        res = []
        for line in out.splitlines():
            m = re.match(r"\s*(\d+):\s+\S*?([\w-]+)\s+(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)",
                         line)
            if not m:
                continue
            i, nm, w, h, x, y = m.groups()
            res.append({"index": int(i) + 1, "id": nm, "main": "*" in line.split()[1],
                        "origin": [int(x), int(y)], "logical": [int(w), int(h)],
                        "pixels": [int(w), int(h)]})
        if res and not any(d["main"] for d in res):
            res[0]["main"] = True
        return res

    def locked(self):
        # Two different things count as "not on screen": a locking screensaver
        # (LockedHint) and the session not being the active VT — with a greeter in
        # front, captures come back solid black while the windows still exist.
        try:
            out = self._run(["loginctl", "list-sessions", "--no-legend"], timeout=10)
            for line in out.splitlines():
                sid = line.split()[0]
                p = self._run(["loginctl", "show-session", sid, "-p", "Display",
                               "-p", "LockedHint", "-p", "Active"], timeout=10)
                kv = dict(l.split("=", 1) for l in p.splitlines() if "=" in l)
                if kv.get("Display") != self.env["DISPLAY"]:
                    continue
                return kv.get("LockedHint") == "yes" or kv.get("Active") != "yes"
        except Exception:
            pass
        return False

    def input_ready(self):
        try:
            self._run(["xdotool", "getmouselocation"], timeout=10)
            return True
        except Exception:
            return False

    def frontmost(self):
        try:
            wid = self._run(["xdotool", "getactivewindow"], timeout=10).strip()
            return self._run(["xdotool", "getwindowname", wid], timeout=10).strip()
        except Exception:
            return "?"

    def capture(self, idx, path):
        d = next((x for x in self.displays() if x["index"] == idx), None)
        if not d:
            raise ToolError("display %d does not exist" % idx)
        geo = "%dx%d+%d+%d" % (d["logical"][0], d["logical"][1],
                               d["origin"][0], d["origin"][1])
        self._run(["import", "-window", "root", "-crop", geo, "+repage", "png:" + path],
                  timeout=30)

    def dims(self, path):
        w, h = run(["identify", "-format", "%w %h", path], timeout=15).split()[:2]
        return int(w), int(h)

    def downscale(self, path, max_w):
        run(["convert", path, "-resize", "%dx" % max_w, path], timeout=30)

    def _xmods(self, mods):
        return [XMODS[m] for m in mods]

    def click(self, x, y, button, count, mods):
        btn = "3" if button == "right" else "1"
        cmd = ["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))]
        for m in self._xmods(mods):
            cmd += ["keydown", m]
        cmd += ["click", "--repeat", str(max(1, count)), "--delay", "80", btn]
        for m in self._xmods(mods):
            cmd += ["keyup", m]
        self._run(cmd, timeout=30)

    def move(self, x, y):
        self._run(["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))])

    def drag(self, x1, y1, x2, y2, mods):
        cmd = ["xdotool", "mousemove", "--sync", str(int(x1)), str(int(y1))]
        for m in self._xmods(mods):
            cmd += ["keydown", m]
        cmd += ["mousedown", "1", "sleep", "0.1",
                "mousemove", "--sync", str(int(x2)), str(int(y2)), "sleep", "0.1",
                "mouseup", "1"]
        for m in self._xmods(mods):
            cmd += ["keyup", m]
        self._run(cmd, timeout=30)

    def scroll(self, direction, amount, x, y):
        btn = {"up": "4", "down": "5", "left": "6", "right": "7"}[direction]
        cmd = ["xdotool"]
        if x >= 0 and y >= 0:
            cmd += ["mousemove", "--sync", str(int(x)), str(int(y))]
        cmd += ["click", "--repeat", str(max(1, amount)), "--delay", "60", btn]
        self._run(cmd, timeout=30)

    def type_text(self, text, delay_ms):
        self._run(["xdotool", "type", "--delay", str(delay_ms), "--", text], timeout=180)

    def press(self, mods, key):
        combo = "+".join(self._xmods(mods) + [XKEYS.get(key, key)])
        self._run(["xdotool", "key", "--clearmodifiers", combo], timeout=20)

    def _windows(self):
        wins = []
        for line in self._run(["wmctrl", "-lG"], timeout=15).splitlines():
            p = line.split(None, 7)
            if len(p) < 8:
                continue
            wins.append({"id": p[0], "x": int(p[2]), "y": int(p[3]),
                         "w": int(p[4]), "h": int(p[5]), "title": p[7]})
        return wins

    def app_list(self):
        return sorted(set(w["title"] for w in self._windows()))

    def app_open(self, name):
        subprocess.Popen(["setsid", "-f"] + shlex.split(name),
                         env=dict(os.environ, **self.env),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)

    def app_focus(self, name):
        self._run(["wmctrl", "-a", name], timeout=15)

    def app_quit(self, name):
        self._run(["wmctrl", "-c", name], timeout=15)

    def script(self, src, lang, timeout):
        raise ToolError("applescript is macOS only; on Linux use 'app' or the "
                        "mouse/keyboard tools")

    def ui_elements(self, app):
        wins = self._windows()
        if app:
            wins = [w for w in wins if app.lower() in w["title"].lower()]
        return {"app": self.frontmost(), "windows": len(wins), "truncated": False,
                "note": "X11 has no accessibility tree here: listing windows instead",
                "elements": [{"path": w["id"], "role": "AXWindow", "subrole": "",
                              "name": w["title"], "value": "",
                              "center": [w["x"] + w["w"] // 2, w["y"] + w["h"] // 2],
                              "size": [w["w"], w["h"]]} for w in wins]}

    def request_permissions(self):
        return {"note": "no permissions to grant on Linux/X11"}


BE = DarwinBackend() if IS_MAC else LinuxBackend()


# ---------------------------------------------------------------------------
# Coordinates. The model reads pixels off the returned PNG; the server converts
# them to global logical points. That is what keeps Retina 2x from doubling every
# click offset — never hand raw screenshot pixels to the pointer.
# ---------------------------------------------------------------------------
class Session(object):
    def __init__(self):
        self.last = None

    def to_screen(self, x, y, space):
        if space == "screen":
            return float(x), float(y)
        c = self.last
        if not c:
            raise ToolError("no previous capture: call screenshot before using "
                            "space='image', or pass space='screen'")
        sx = c["origin"][0] + (float(x) / c["out_w"]) * c["logical"][0]
        sy = c["origin"][1] + (float(y) / c["out_h"]) * c["logical"][1]
        return round(sx, 1), round(sy, 1)


def require_input():
    """Without the Accessibility permission the input helpers exit 0 and the event is
    silently dropped: the tool would answer "moved to X,Y" with the pointer still
    where it was. Fail loudly instead."""
    if not BE.input_ready():
        raise ToolError(
            "mouse/keyboard control is DENIED — without the Accessibility permission "
            "events are dropped silently. Enable this app under System Settings > "
            "Privacy & Security > Accessibility, then restart the server."
            if IS_MAC else
            "mouse/keyboard control is not responding: check DISPLAY / XAUTHORITY")


def check_expect_app(a):
    """The keyboard types wherever focus is. Without this check, a script that fails
    to open what it claims leaves focus untouched and the text lands in whatever the
    human had open."""
    want = a.get("expect_app")
    if not want:
        return
    front = BE.frontmost()
    if want.lower() not in (front or "").lower():
        raise ToolError("refusing to type: expected '%s' in the foreground, found '%s'"
                        % (want, front))


def _mods(a):
    m = [ALIASES.get(str(x).lower(), str(x).lower()) for x in (a.get("modifiers") or [])]
    bad = [x for x in m if x not in MODS]
    if bad:
        raise ToolError("invalid modifiers: %s (valid: %s)" % (bad, sorted(MODS)))
    return m


def t_screen_info(_s, _a):
    return {"platform": BE.name, "displays": BE.displays(), "screen_locked": BE.locked(),
            "frontmost_app": BE.frontmost(), "input_ready": BE.input_ready(),
            "overlay": OVERLAY_MGR.status(),
            "note": "'screen' coordinates are global logical points; 'image' are pixels "
                    "of the last screenshot"}


def t_screenshot(s, a):
    ds = BE.displays()
    if not ds:
        raise ToolError(
            "no displays visible to the server (screen_locked=%s, platform=%s). On "
            "macOS: unlock the session; if it persists the process is not in the GUI "
            "session. On Linux: check DISPLAY / XAUTHORITY." % (BE.locked(), BE.name))
    idx = int(a.get("display") or next((d["index"] for d in ds if d["main"]), ds[0]["index"]))
    d = next((x for x in ds if x["index"] == idx), None)
    if not d:
        raise ToolError("display %d does not exist (there are %d)" % (idx, len(ds)))
    delay = float(a.get("delay") or 0)
    if delay:
        time.sleep(min(delay, 10))
    max_w = int(a.get("max_width") or 1400)
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "s.png")
        BE.capture(idx, raw)
        if not os.path.exists(raw):
            raise ToolError("capture produced no image (screen recording permission "
                            "not granted?)")
        pre_w, pre_h = BE.dims(raw)
        mismatch = pre_w != d["pixels"][0]
        out_w, out_h = pre_w, pre_h
        if max_w and pre_w > max_w:
            BE.downscale(raw, max_w)
            out_w, out_h = BE.dims(raw)
        data = base64.b64encode(open(raw, "rb").read()).decode()
    s.last = {"origin": d["origin"], "logical": d["logical"], "out_w": out_w,
              "out_h": out_h, "display": idx}
    warn = ""
    if mismatch:
        warn = ("  WARNING captured width (%d px) does not match what the system "
                "reports for display %d (%d px): the capture index and the enumeration "
                "index disagree" % (pre_w, idx, d["pixels"][0]))
    text = ("[%s] display %d (%s) - image %dx%d px - logical screen %dx%d at origin "
            "%d,%d - foreground: %s%s%s"
            % (BE.name, idx, d["id"], out_w, out_h, d["logical"][0], d["logical"][1],
               d["origin"][0], d["origin"][1], BE.frontmost(),
               "  WARNING SCREEN LOCKED" if BE.locked() else "", warn))
    return [{"type": "text", "text": text},
            {"type": "image", "data": data, "mimeType": "image/png"}]


def t_click(s, a):
    require_input()
    x, y = s.to_screen(a["x"], a["y"], a.get("space", "image"))
    btn = (a.get("button") or "left").lower()
    n = int(a.get("count") or 1)
    BE.click(x, y, btn, n, _mods(a))
    return "%s click x%d at %g,%g (global points)" % (btn, n, x, y)


def t_move(s, a):
    require_input()
    x, y = s.to_screen(a["x"], a["y"], a.get("space", "image"))
    BE.move(x, y)
    return "pointer at %g,%g" % (x, y)


def t_drag(s, a):
    require_input()
    x1, y1 = s.to_screen(a["x1"], a["y1"], a.get("space", "image"))
    x2, y2 = s.to_screen(a["x2"], a["y2"], a.get("space", "image"))
    BE.drag(x1, y1, x2, y2, _mods(a))
    return "dragged %g,%g -> %g,%g" % (x1, y1, x2, y2)


def t_scroll(s, a):
    require_input()
    direction = (a.get("direction") or "down").lower()
    if direction not in ("up", "down", "left", "right"):
        raise ToolError("direction must be up|down|left|right")
    amount = int(a.get("amount") or 5)
    px = py = -1
    if a.get("x") is not None and a.get("y") is not None:
        px, py = s.to_screen(a["x"], a["y"], a.get("space", "image"))
    BE.scroll(direction, amount, px, py)
    return "scrolled %s x%d" % (direction, amount)


def t_type_text(_s, a):
    require_input()
    check_expect_app(a)
    text = a.get("text")
    if not isinstance(text, str) or text == "":
        raise ToolError("text is empty")
    BE.type_text(text, int(a.get("delay_ms") or 12))
    return "typed (%d chars)" % len(text)


def t_press_key(_s, a):
    require_input()
    check_expect_app(a)
    keys = a.get("keys")
    combos = [keys] if isinstance(keys, str) else list(keys or [])
    if not combos:
        raise ToolError("keys is empty")
    for combo in combos:
        parts = [ALIASES.get(p.strip().lower(), p.strip().lower())
                 for p in str(combo).split("+") if p.strip()]
        mods = [p for p in parts if p in MODS]
        rest = [p for p in parts if p not in MODS]
        if len(rest) != 1:
            raise ToolError("combo '%s' must have exactly one non-modifier key" % combo)
        BE.press(mods, rest[0])
        time.sleep(0.05)
    return "keys: %s" % ", ".join(str(c) for c in combos)


def t_app(_s, a):
    action = (a.get("action") or "frontmost").lower()
    name = a.get("name")
    if action == "frontmost":
        return BE.frontmost()
    if action == "list":
        return BE.app_list()
    if not name:
        raise ToolError("'name' is required")
    if action == "open":
        BE.app_open(name)
        time.sleep(1.2)
        return "opened: %s" % name
    if action == "focus":
        BE.app_focus(name)
        time.sleep(0.5)
        return "focused: %s" % name
    if action == "quit":
        BE.app_quit(name)
        return "quit: %s" % name
    raise ToolError("action must be list|open|focus|quit|frontmost")


def t_applescript(_s, a):
    src = a.get("script")
    if not src:
        raise ToolError("script is empty")
    return BE.script(src, (a.get("language") or "applescript").lower(),
                     float(a.get("timeout") or 60))


def t_ui_elements(_s, a):
    return BE.ui_elements(a.get("app"))


def t_set_control_context(_s, a):
    session = (a.get("session") or "").strip()
    purpose = (a.get("purpose") or "").strip()
    if not session or not purpose:
        raise ToolError("'session' and 'purpose' are required: they are what the person "
                        "sitting at the machine sees on screen")
    CONTROL["session"] = session[:80]
    CONTROL["purpose"] = purpose[:120]
    if OVERLAY_MGR.status()["visible"]:
        OVERLAY_MGR.touch()
    return {"banner": OVERLAY_MGR.banner()}


def t_overlay(_s, a):
    action = (a.get("action") or "status").lower()
    if action == "show":
        if not OVERLAY_MGR.touch(float(a.get("seconds") or 20)):
            raise ToolError("the consent overlay is macOS only for now "
                            "(bin/overlay not found)")
    elif action == "hide":
        OVERLAY_MGR.stop()
    elif action != "status":
        raise ToolError("action must be show|hide|status")
    return OVERLAY_MGR.status()


def t_request_permissions(_s, _a):
    out = BE.request_permissions()
    out["next"] = ("Look at the controlled screen: accept the dialogs, or enable this "
                   "app under System Settings > Privacy & Security > Screen Recording "
                   "and Accessibility. Restart the server afterwards — permissions are "
                   "read at process start.")
    return out


TOOLS = [
    ("screen_info", "Desktop state: platform, displays (origin, logical size, pixels), "
                    "whether the screen is locked, foreground app and whether input "
                    "control is working.", {"type": "object", "properties": {}},
     t_screen_info),
    ("screenshot", "Capture a whole display and return it as an image. Coordinates you "
                   "then pass to click/move/drag are interpreted as pixels OF THAT "
                   "IMAGE (space='image', the default).",
     {"type": "object", "properties": {
         "display": {"type": "integer", "description": "1-based index; defaults to the main one"},
         "max_width": {"type": "integer", "description": "max width in px (default 1400)"},
         "delay": {"type": "number", "description": "seconds to wait before capturing"}}},
     t_screenshot),
    ("click", "Mouse click. x,y are pixels of the last screenshot unless space='screen'.",
     {"type": "object", "required": ["x", "y"], "properties": {
         "x": {"type": "number"}, "y": {"type": "number"},
         "space": {"type": "string", "enum": ["image", "screen"]},
         "button": {"type": "string", "enum": ["left", "right"]},
         "count": {"type": "integer", "description": "1 single, 2 double, 3 triple"},
         "modifiers": {"type": "array", "items": {"type": "string"},
                       "description": "cmd (= super on Linux), alt, ctrl, shift, fn"}}},
     t_click),
    ("move", "Move the pointer (hover).", {"type": "object", "required": ["x", "y"],
     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                    "space": {"type": "string", "enum": ["image", "screen"]}}}, t_move),
    ("drag", "Drag from (x1,y1) to (x2,y2).",
     {"type": "object", "required": ["x1", "y1", "x2", "y2"], "properties": {
         "x1": {"type": "number"}, "y1": {"type": "number"},
         "x2": {"type": "number"}, "y2": {"type": "number"},
         "space": {"type": "string", "enum": ["image", "screen"]},
         "modifiers": {"type": "array", "items": {"type": "string"}}}}, t_drag),
    ("scroll", "Mouse wheel. Optionally park the pointer at x,y first.",
     {"type": "object", "properties": {
         "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
         "amount": {"type": "integer", "description": "notches, default 5"},
         "x": {"type": "number"}, "y": {"type": "number"},
         "space": {"type": "string", "enum": ["image", "screen"]}}}, t_scroll),
    ("type_text", "Type literal text into the focused window.",
     {"type": "object", "required": ["text"], "properties": {
         "text": {"type": "string"},
         "delay_ms": {"type": "integer", "description": "ms between keys, default 12"},
         "expect_app": {"type": "string", "description": "refuse to type unless this app "
                        "is in the foreground — always use it after opening something"}}},
     t_type_text),
    ("press_key", "Press keys or combinations: 'cmd+space', 'return', 'ctrl+shift+t', or "
                  "a list of combos in order.",
     {"type": "object", "required": ["keys"], "properties": {
         "keys": {"anyOf": [{"type": "string"},
                            {"type": "array", "items": {"type": "string"}}]},
         "expect_app": {"type": "string", "description": "refuse unless this app is in "
                        "the foreground"}}}, t_press_key),
    ("app", "Manage applications / windows: list | open | focus | quit | frontmost.",
     {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "open", "focus", "quit", "frontmost"]},
         "name": {"type": "string"}}}, t_app),
    ("applescript", "Run AppleScript or JXA (macOS only). The most reliable way to drive "
                    "an app through its object model instead of by pixels.",
     {"type": "object", "required": ["script"], "properties": {
         "script": {"type": "string"},
         "language": {"type": "string", "enum": ["applescript", "jxa"]},
         "timeout": {"type": "number"}}}, t_applescript),
    ("ui_elements", "macOS: accessibility tree of the foreground app, with each control's "
                    "center in global points — use those with click space='screen', far "
                    "more reliable than aiming by vision. Linux/X11: window list.",
     {"type": "object", "properties": {"app": {"type": "string"}}}, t_ui_elements),
    ("set_control_context", "Declare WHO is controlling and WHAT FOR. This is the text "
                            "shown on the controlled screen. Call it before touching "
                            "anything.",
     {"type": "object", "required": ["session", "purpose"], "properties": {
         "session": {"type": "string", "description": "identifier of the controlling session"},
         "purpose": {"type": "string", "description": "what for, in one short sentence"}}},
     t_set_control_context),
    ("overlay", "Consent overlay on the controlled machine: pulsing blue glow around every "
                "screen plus a banner on top. It turns itself on with any desktop action "
                "and off after a few idle seconds; this forces or inspects it.",
     {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["show", "hide", "status"]},
         "seconds": {"type": "number", "description": "how long to keep it with 'show'"}}},
     t_overlay),
    ("request_permissions", "macOS: register the app in the privacy panes and raise the "
                            "Screen Recording / Accessibility dialogs.",
     {"type": "object", "properties": {}}, t_request_permissions),
]
TOOL_MAP = dict((n, f) for n, _d, _s, f in TOOLS)

# Tools that only read the server's own state never light up the overlay; a status
# check should not flash lights at whoever is sitting there.
NO_OVERLAY = ("screen_info", "request_permissions", "set_control_context", "overlay")


def handle(session, req):
    m = req.get("method")
    if m == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                "serverInfo": {"name": "desktop-mcp", "version": VERSION}}
    if m == "ping":
        return {}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s}
                          for n, d, s, _f in TOOLS]}
    if m == "tools/call":
        p = req.get("params") or {}
        name = p.get("name")
        fn = TOOL_MAP.get(name)
        if not fn:
            raise ToolError("unknown tool: %s" % name)
        if name not in NO_OVERLAY:
            OVERLAY_MGR.touch()
        res = fn(session, p.get("arguments") or {})
        if isinstance(res, list):
            return {"content": res}
        if isinstance(res, str):
            return {"content": [{"type": "text", "text": res}]}
        return {"content": [{"type": "text",
                             "text": json.dumps(res, ensure_ascii=False, indent=1)}]}
    raise ToolError("unsupported method: %s" % m)


def dispatch(session, raw):
    """One JSON-RPC line in, one response dict out (or None for notifications).
    Tool failures come back as isError content, not protocol errors, so the model
    can read and react to them."""
    try:
        req = json.loads(raw)
    except Exception:
        return None
    rid = req.get("id")
    try:
        result = handle(session, req)
        if rid is None:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except Exception as e:
        if rid is None:
            return None
        if req.get("method") == "tools/call":
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"isError": True,
                               "content": [{"type": "text", "text": str(e)}]}}
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32603, "message": str(e)}}


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
def serve_stdio():
    session = Session()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        resp = dispatch(session, raw)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


class Handler(socketserver.StreamRequestHandler):
    timeout = 3600

    def handle(self):
        session = Session()
        for raw in self.rfile:
            raw = raw.strip()
            if not raw:
                continue
            resp = dispatch(session, raw)
            if resp is not None:
                self.wfile.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_tcp():
    srv = None
    # A relaunching predecessor can still hold the port for a moment; retry rather
    # than dying and leaving the service down.
    for i in range(40):
        try:
            srv = Server(("127.0.0.1", PORT), Handler)
            break
        except OSError as e:
            logline("bind %d failed (attempt %d): %s" % (PORT, i + 1, e))
            time.sleep(1.5)
    if srv is None:
        logline("giving up: could not bind %d" % PORT)
        sys.exit(1)
    logline("listening on 127.0.0.1:%d backend=%s" % (PORT, BE.name))
    try:
        srv.serve_forever()
    finally:
        logline("exiting")


USAGE = """desktop-mcp — desktop control over MCP

  server.py stdio     MCP over stdin/stdout (default). Use this from Claude Code,
                      opencode or any MCP client on the same machine.
  server.py serve     loopback TCP daemon on $DESKTOP_MCP_PORT (default 8811),
                      newline-delimited JSON-RPC. Drive it from another machine with
                      `ssh host nc 127.0.0.1 8811`.
  server.py selftest  print screen_info and exit.
"""

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if mode in ("-h", "--help", "help"):
        print(USAGE)
    elif mode == "selftest":
        print(json.dumps(t_screen_info(Session(), {}), indent=1, ensure_ascii=False))
    elif mode == "serve":
        serve_tcp()
    elif mode == "stdio":
        serve_stdio()
    else:
        sys.stderr.write(USAGE)
        sys.exit(2)

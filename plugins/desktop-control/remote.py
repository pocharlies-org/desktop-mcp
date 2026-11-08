#!/usr/bin/env python3
"""Lazy stdio client for a remote daemon: `remote.py ssh host /usr/bin/nc 127.0.0.1 8811`.

MCP clients connect to every registered server when they start. This answers `initialize`
and `tools/list` (from the last list it saw) without running the transport command, and
runs it on the first request that needs the daemon. With no cached list yet, `tools/list`
connects too. Each connection refreshes the cache, so a new tool shows up next session.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading

PROTOCOL_VERSION = "2024-11-05"  # what server.py speaks
CMD = sys.argv[1:]
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "desktop-mcp",
                     hashlib.sha256("\0".join(CMD).encode()).hexdigest()[:16] + ".tools.json")
out_lock = threading.Lock()
proc = None
pending = set()  # ids sent to the daemon and not yet answered


def emit(msg):
    with out_lock:
        pending.discard(msg.get("id"))
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def cached_tools():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_tools(result):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f)
    os.replace(tmp, CACHE)


def send(p, msg):
    p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()


def pump(p):
    """Remote → client. Our own requests (ids starting with `lazy-`) are consumed here."""
    for line in p.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") == "lazy-tools":
            if "result" in msg:
                save_tools(msg["result"])
            continue
        emit(msg)
    for rid in list(pending):  # the daemon went away: answer what it never will
        emit({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "connection closed"}})


def connect():
    p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    send(p, {"jsonrpc": "2.0", "id": "lazy-init", "method": "initialize",
             "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "desktop-mcp-remote", "version": "1"}}})
    for line in p.stdout:
        try:
            if json.loads(line).get("id") == "lazy-init":
                break
        except ValueError:
            continue
    else:
        err = p.stderr.read().strip()
        p.wait()
        raise ConnectionError(f"{' '.join(CMD)} exited {p.returncode}: {err or 'no output'}")
    send(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    send(p, {"jsonrpc": "2.0", "id": "lazy-tools", "method": "tools/list"})
    p.pump = threading.Thread(target=pump, args=(p,), daemon=True)
    p.pump.start()
    return p


def handle(msg):
    global proc
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        emit({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
            "serverInfo": {"name": "desktop-remote", "version": "1"}}})
        return
    if rid is None and proc is None:  # notifications before connecting have nobody to go to
        return
    if method == "tools/list" and proc is None and (tools := cached_tools()) is not None:
        emit({"jsonrpc": "2.0", "id": rid, "result": tools})
        return
    if proc is None and method == "ping":
        emit({"jsonrpc": "2.0", "id": rid, "result": {}})
        return
    # The daemon serves tools only; probes such as `server/discover` must not open the connection.
    if proc is None and method not in ("tools/call", "tools/list"):
        emit({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}})
        return
    if proc is None or proc.poll() is not None:
        try:
            proc = connect()
        except OSError as e:  # ConnectionError included
            proc = None
            if rid is not None:
                emit({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}})
            return
    if rid is not None:
        pending.add(rid)
    try:
        send(proc, msg)
    except OSError as e:  # the connection died between calls: the next request reconnects
        proc = None
        if rid is not None:
            emit({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": f"connection lost: {e}"}})


def main():
    if not CMD:
        sys.exit("usage: remote.py <command that reaches the daemon, e.g. ssh host /usr/bin/nc 127.0.0.1 8811>")
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        handle(msg)
    if proc is not None:  # client gone: let in-flight answers drain, then hang up
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
        proc.pump.join(timeout=5)


if __name__ == "__main__":
    main()

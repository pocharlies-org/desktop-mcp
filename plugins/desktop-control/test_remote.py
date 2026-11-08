#!/usr/bin/env python3
"""Self-check for remote.py against a fake daemon: `python3 test_remote.py`."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = r'''
import json, os, sys
open(os.environ["MARK"], "a").write("started\n")
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "tools/call" and m["params"]["name"] == "die":
        sys.exit(1)
    r = {"initialize": {"protocolVersion": "2024-11-05"},
         "tools/list": {"tools": [{"name": "echo"}]}}.get(m["method"], {"echo": m.get("params")})
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''


def session(env, cmd, *msgs):
    """Run remote.py over msgs; return its answers by id."""
    stdin = "".join(json.dumps(m) + "\n" for m in msgs)
    out = subprocess.run([sys.executable, os.path.join(HERE, "remote.py"), *cmd], input=stdin,
                         capture_output=True, text=True, env=env, timeout=20).stdout
    return {m["id"]: m for m in map(json.loads, out.splitlines())}


def req(i, method, **params):
    return {"jsonrpc": "2.0", "id": i, "method": method, "params": params}


with tempfile.TemporaryDirectory() as tmp:
    mark = os.path.join(tmp, "mark")
    env = {**os.environ, "XDG_CACHE_HOME": tmp, "MARK": mark}
    fake = os.path.join(tmp, "fake.py")
    open(fake, "w").write(FAKE)
    cmd = [sys.executable, fake]
    started = lambda: os.path.exists(mark) and open(mark).read().count("started")
    init = [req(1, "initialize"), {"jsonrpc": "2.0", "method": "notifications/initialized"}]

    # No cache yet: tools/list has to connect, and leaves the cache behind.
    r = session(env, cmd, *init, req(2, "tools/list"))
    assert r[2]["result"] == {"tools": [{"name": "echo"}]}, r
    assert started() == 1

    # With the cache, starting up never runs the transport command...
    os.remove(mark)
    r = session(env, cmd, req(0, "server/discover"), *init, req(2, "tools/list"), req(3, "ping"))
    assert r[0]["error"]["code"] == -32601, r
    assert r[1]["result"]["capabilities"] == {"tools": {}} and r[2]["result"]["tools"][0]["name"] == "echo", r
    assert not started()

    # ...the first tool call does, once.
    r = session(env, cmd, *init, req(2, "tools/list"), req(3, "tools/call", name="echo", arguments={"x": 1}),
                req(4, "tools/call", name="echo", arguments={}))
    assert r[3]["result"]["echo"]["arguments"] == {"x": 1} and 4 in r, r
    assert started() == 1

    # A transport that fails answers with an error, not silence.
    r = session(env, ["false"], *init, req(2, "tools/call", name="echo", arguments={}))
    assert r[2]["error"]["code"] == -32000, r

    # A daemon dying mid-call answers the pending call.
    r = session(env, cmd, *init, req(2, "tools/call", name="die", arguments={}))
    assert r[2]["error"]["message"] == "connection closed", r
print("ok")

"""Headless smoke test for the console's HTTP layer — runs in CI, no browser.

Starts the console server on an ephemeral port against a fresh checkpointer, then
asserts the live read view, the approval endpoint, and input validation.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runtime as R

# Fresh checkpointer so the smoke test is deterministic regardless of prior runs.
for suffix in ("", "-wal", "-shm"):
    p = R.DB + suffix
    if os.path.exists(p):
        os.remove(p)

import console as C  # imports after the wipe; builds graph + checkpointer

C._seed_if_empty()

srv = ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def post(path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


fails = []


def check(name, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        fails.append(name)


status, snap = get("/api/units")
check("GET /api/units returns 200", status == 200)
check("snapshot exposes fleet + cap fields",
      {"units", "spent", "cap", "may_spawn", "idle_count"} <= snap.keys())
check("seeded units are parked at the gate",
      len(snap["units"]) >= 1 and all(u["node"] == "gate" for u in snap["units"]))

status, page = None, None
with urllib.request.urlopen(base + "/", timeout=5) as r:
    status, page = r.status, r.read().decode()
check("GET / serves the console page", status == 200 and "THE GOVERNOR" in page)

target = snap["units"][0]["unit_id"]
status, after = post("/api/resume", {"unit_id": target, "decision": "approve"})
resolved = {u["unit_id"]: u for u in after["units"]}[target]
check("POST /api/resume approve -> published/done",
      status == 200 and resolved["status"] == "done"
      and resolved["log"][-1] == "approved -> published")

status, _ = post("/api/resume", {"unit_id": "whatever", "decision": "maybe"})
check("bad decision is rejected with 400", status == 400)

srv.shutdown()
print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)

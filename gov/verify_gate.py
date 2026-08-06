"""Acceptance for the gate service — the multi-tenant claims, checked over real HTTP.

Nothing is mocked below the HTTP client. A real server is started on a real port,
real starter repositories are provisioned with `git init`, a real fleet works them,
and a real merge happens when a guest approves. The scripted solver stands in for
the model — that is the point: it makes the machinery deterministic so the failures
this suite catches are the harness's, not a model's bad day.

The claims it exists to defend:

  * A guest can only ever act on their own workspace. This is the one that matters —
    the gate performs an irreversible act, so an isolation bug here merges one
    stranger's code on another stranger's say-so.
  * A cookie cannot be forged or edited into somebody else's session.
  * A page on another origin cannot spend a guest's session.
  * Approving really merges and rejecting really destroys nothing of the base.
  * Every sortie lands in the ledger, kept or not, and a run distils a lesson.

Run:  python3 gov/verify_gate.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# A private data directory: this suite writes real sessions, gate rows and metrics,
# and must never be able to disturb a running instance's.
DATA = tempfile.mkdtemp(prefix="phoenix-gate-data-")
os.environ["GOV_DATA_DIR"] = DATA
os.environ["GATE_SECRET"] = "test-secret-not-a-real-deployment-key"

import anchor                                                          # noqa: E402
import builder as B                                                    # noqa: E402
import gate                                                            # noqa: E402
import guests                                                          # noqa: E402
import metrics as M                                                    # noqa: E402
import starter                                                         # noqa: E402
import worktree as WT                                                  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def section(title):
    print(f"\n{title}")


# ── the scripted fleet ───────────────────────────────────────────────────────
#
# One correct implementation per starter. The solver returns the whole file, which
# is the envelope contract in solver.py. `misses` makes the first N attempts return
# something the oracle will reject, so the retry ladder and the ledger's failure
# outcomes are exercised rather than assumed.

FIXES = {
    "orders.py": '''"""Order pricing."""

TAX = 0.08
RATE_PER_KG = 1.5
EXPRESS_MULTIPLIER = 2
KM_PER_DAY = 167


def shipping(weight_kg, express=False):
    base = weight_kg * RATE_PER_KG
    return base * EXPRESS_MULTIPLIER if express else base


def eta_days(distance_km, express=False):
    if express:
        return 1
    return max(1, round(distance_km / KM_PER_DAY))


def discount(code, subtotal):
    if code == "SAVE10":
        return round(subtotal * 0.10, 2)
    return 0.0


def total(subtotal, weight_kg, code="", express=False):
    taxed = (subtotal - discount(code, subtotal)) * (1 + TAX)
    return round(taxed + shipping(weight_kg, express), 2)
''',
    "inventory.py": '''"""Stock control helpers."""


def reorder_point(daily_usage, lead_time_days, safety_stock=0):
    return daily_usage * lead_time_days + safety_stock


def is_low(stock, point):
    return stock <= point


def restock_qty(stock, target):
    return max(0, target - stock)
''',
    "textkit.py": '''"""Small text helpers."""

import re

ELLIPSIS = "\\u2026"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit - 1] + ELLIPSIS


def wrap_words(text, width):
    lines, current = [], ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
''',
}


def scripted(misses=0, seen=None):
    """A solver that fails `misses` times before getting it right."""
    state = {"n": 0}

    def solve(req):
        if seen is not None:
            seen.append(dict(req))
        path = next(iter(req.get("files") or {}), "")
        state["n"] += 1
        if state["n"] <= misses:
            # a change the oracle can see but will not pay for: it breaks nothing and
            # fixes nothing, which is the commonest real failure mode
            return {"files": {path: (req["files"][path] + "\n# considered\n")}, "tokens": 1200}
        fix = FIXES.get(os.path.basename(path))
        return {"files": {path: fix} if fix else {}, "tokens": 1800}

    return solve


# ── an HTTP client with a cookie jar ─────────────────────────────────────────

PORT = 0


def http(method, path, body=None, cookie="", ctype="application/json",
         origin=None, raw=False):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    if cookie:
        req.add_header("Cookie", cookie)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            set_cookie = r.headers.get("Set-Cookie", "")
            if raw:
                return r.status, payload, set_cookie
            return r.status, json.loads(payload or b"{}"), set_cookie
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {"raw": payload[:200].decode("utf-8", "replace")}
        return e.code, parsed, e.headers.get("Set-Cookie", "")


def jar(set_cookie: str) -> str:
    return set_cookie.split(";")[0] if set_cookie else ""


def wait_for_run(cookie, timeout=180):
    """Poll until the background run finishes. A test that raced the fleet would be
    flaky in exactly the way this whole system exists to avoid."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, state, _ = http("GET", "/api/state", cookie=cookie)
        if not state.get("run", {}).get("active"):
            return state
        time.sleep(0.4)
    raise TimeoutError("run did not finish")


# ── the service under test ───────────────────────────────────────────────────

srv, service = gate.build(host="127.0.0.1", port=0, serve_guests=True,
                          solve=scripted(misses=1))
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

print(f"gate service on 127.0.0.1:{PORT}  ·  data {DATA}")

# ─────────────────────────────────────────────────────────────────────────────
section("Sessions — identity without accounts")

status, page, cookie_a_raw = http("GET", "/", raw=True)
cookie_a = jar(cookie_a_raw)
check("the landing page is served at /", status == 200 and b"<html" in page.lower())
check("a first visit is issued a guest session", cookie_a.startswith(guests.COOKIE + "="))
check("the session cookie is HttpOnly and SameSite=Lax",
      "HttpOnly" in cookie_a_raw and "SameSite=Lax" in cookie_a_raw)

status, state_a, _ = http("GET", "/api/state", cookie=cookie_a)
label_a = state_a["session"]["label"]
check("state comes back for that session", status == 200 and state_a["connected"])
check("the guest has a readable label", label_a.startswith("guest-"), label_a)
check("a guest starts with no workspace", state_a["workspace"]["ready"] is False)

_, _, cookie_b_raw = http("GET", "/api/state")
cookie_b = jar(cookie_b_raw)
_, state_b, _ = http("GET", "/api/state", cookie=cookie_b)
check("a second visitor gets a different session",
      state_b["session"]["label"] != label_a, f"{label_a} vs {state_b['session']['label']}")

# a cookie whose signature has been edited must not resolve to the session it names
sid_a = cookie_a.split("=", 1)[1].split(".")[0]
forged = f"{guests.COOKIE}={sid_a}.{'0' * 32}"
_, forged_state, forged_set = http("GET", "/api/state", cookie=forged)
check("a tampered signature is refused and a fresh session issued",
      forged_state["session"]["label"] != label_a and forged_set != "")
check("an unsigned session id is refused",
      guests.unsign(sid_a) == "" and guests.unsign(guests.sign(sid_a)) == sid_a)

# ─────────────────────────────────────────────────────────────────────────────
section("Workspaces — a real repository each")

status, made, _ = http("POST", "/api/workspace", {"starter": "orders"}, cookie=cookie_a)
ws_a = made.get("workspace", {})
check("a guest can provision a workspace", status == 200 and ws_a.get("repo"))
check("it is a real git repository with commits",
      WT.is_git(ws_a["repo"]) and WT.has_commits(ws_a["repo"]))
check("its suite really fails at the initial commit", len(ws_a.get("red", [])) >= 4,
      f"{len(ws_a.get('red', []))} red")
check("provisioning verifies redness rather than claiming it",
      sorted(t.split(".")[-1] for t in ws_a["red"]) == sorted(starter.STARTERS["orders"]["red"]))

status, made_b, _ = http("POST", "/api/workspace", {"starter": "inventory"}, cookie=cookie_b)
ws_b = made_b.get("workspace", {})
check("a second guest gets a different repository on disk",
      ws_b.get("repo") and ws_b["repo"] != ws_a["repo"])
check("each workspace directory is named by hash, not by session id",
      sid_a not in ws_a["repo"])

_, state_a, _ = http("GET", "/api/state", cookie=cookie_a)
check("the console reports the live suite state",
      state_a["workspace"]["passed"] >= 0 and state_a["workspace"]["total"] >= 6,
      f"{state_a['workspace']['passed']}/{state_a['workspace']['total']}")
check("and names the failing tests", len(state_a["workspace"]["failing"]) >= 4)

# ─────────────────────────────────────────────────────────────────────────────
section("Running a fleet")

status, started, _ = http("POST", "/api/run", {"kind": "builder"}, cookie=cookie_a)
check("a run starts", status == 200 and started.get("ok"), started.get("message", ""))

status, again, _ = http("POST", "/api/run", {"kind": "builder"}, cookie=cookie_a)
check("a second concurrent run for the same session is refused",
      status == 409 and "already" in again.get("message", ""))

state_a = wait_for_run(cookie_a)
check("the run finishes and the log survives", len(state_a["run"]["log"]) > 0,
      f"{len(state_a['run']['log'])} lines")
check("work parked at the gate", len(state_a["pending"]) >= 1,
      f"{len(state_a['pending'])} dossier(s)")

dossier = state_a["pending"][0] if state_a["pending"] else {}
check("the dossier carries a real diff", "diff --git" in dossier.get("diff", ""))
check("it carries the oracle's delta, not a claim",
      dossier.get("delta", {}).get("after", 0) > dossier.get("delta", {}).get("before", -1),
      f"{dossier.get('delta', {}).get('before')} -> {dossier.get('delta', {}).get('after')}")
check("it names the files it touched", dossier.get("files"))
check("the agent id is namespaced to the session",
      dossier.get("agent", "").startswith(label_a), dossier.get("agent", ""))

_, state_b, _ = http("GET", "/api/state", cookie=cookie_b)
check("the other guest sees none of it", state_b["pending"] == [])

# ─────────────────────────────────────────────────────────────────────────────
section("Isolation — the claim that matters")

gate_id_a = dossier.get("id")
status, denied, _ = http("POST", "/api/gate/approve", {"id": gate_id_a}, cookie=cookie_b)
check("another guest cannot approve this branch", status == 403,
      denied.get("error", ""))
status, denied, _ = http("POST", "/api/gate/reject",
                         {"id": gate_id_a, "reason": "not mine"}, cookie=cookie_b)
check("nor reject it", status == 403, denied.get("error", ""))

still = B.gate_get(gate_id_a)
check("and the dossier is untouched by the attempt", still["status"] == "pending")

status, denied, _ = http("POST", "/api/gate/approve", {"id": gate_id_a})
check("a session with no workspace cannot approve anything", status == 403)

status, nope, _ = http("POST", "/api/gate/approve", {"id": 99999}, cookie=cookie_a)
check("an id that does not exist is refused as unowned, not 500", status == 403)

# ─────────────────────────────────────────────────────────────────────────────
section("Cross-origin and content type")

status, _, _ = http("POST", "/api/gate/approve", {"id": gate_id_a}, cookie=cookie_a,
                    ctype="application/x-www-form-urlencoded")
check("a form-encoded POST is refused (a form can be sent cross-site)", status == 415)

status, _, _ = http("POST", "/api/gate/approve", {"id": gate_id_a}, cookie=cookie_a,
                    origin="https://evil.example")
check("a POST from another origin is refused", status == 403)

status, _, _ = http("POST", "/api/gate/reject", {"id": gate_id_a}, cookie=cookie_a)
check("rejecting without a reason is refused", status == 400)

# ─────────────────────────────────────────────────────────────────────────────
section("Deciding — the irreversible act")

repo_a = ws_a["repo"]
base_before = WT._git(repo_a, "rev-parse", "HEAD").strip()
status, approved, _ = http("POST", "/api/gate/approve", {"id": gate_id_a}, cookie=cookie_a)
check("the owner can approve", status == 200 and approved.get("ok"),
      approved.get("message", ""))

base_after = WT._git(repo_a, "rev-parse", "HEAD").strip()
check("the base branch actually moved", base_before != base_after,
      f"{base_before[:8]} -> {base_after[:8]}")

red_now = starter._red_tests(repo_a)
check("the tests the agent fixed are green in the base repository now",
      len(red_now) < len(ws_a["red"]), f"{len(ws_a['red'])} red -> {len(red_now)} red")

decided = B.gate_get(gate_id_a)
check("the dossier records who decided it", decided["status"] == "merged"
      and decided["decided_by"] == label_a, decided["decided_by"])

status, twice, _ = http("POST", "/api/gate/approve", {"id": gate_id_a}, cookie=cookie_a)
check("approving twice is refused", status == 409, twice.get("message", ""))

# a rejection, on the second guest's workspace
http("POST", "/api/run", {"kind": "builder"}, cookie=cookie_b)
state_b = wait_for_run(cookie_b)
check("the second guest's fleet also parks work", len(state_b["pending"]) >= 1)

if state_b["pending"]:
    gate_id_b = state_b["pending"][0]["id"]
    repo_b = ws_b["repo"]
    branch_b = state_b["pending"][0]["branch"]
    head_before = WT._git(repo_b, "rev-parse", "HEAD").strip()
    status, rejected, _ = http("POST", "/api/gate/reject",
                               {"id": gate_id_b, "reason": "solves it the wrong way"},
                               cookie=cookie_b)
    check("the owner can reject with a reason", status == 200 and rejected.get("ok"))
    check("rejecting leaves the base commit exactly where it was",
          WT._git(repo_b, "rev-parse", "HEAD").strip() == head_before)
    branches = WT._git(repo_b, "branch", "--list", branch_b, check=False)
    check("and the branch is gone", branch_b not in branches)
    lessons = [s["lesson"] for s in anchor.skills_top(40)]
    check("the reason is filed as a lesson the next run is handed",
          any("solves it the wrong way" in l for l in lessons))

# ─────────────────────────────────────────────────────────────────────────────
section("The ledger — what the agents actually did")

perf = M.summary(repo=repo_a)
check("every sortie was recorded, kept or not", perf["sorties"] >= 2,
      f"{perf['sorties']} sorties")
check("rejected attempts are counted, not hidden", perf["rejected"] >= 1,
      f"{perf['rejected']} rejected of {perf['sorties']}")
check("tests gained is measured", perf["tests_gained"] >= 1)

outcomes = M.outcomes(repo=repo_a)
check("outcomes use the closed vocabulary", set(outcomes) == set(M.OUTCOMES))
check("the deliberate miss was classified as a rejection",
      outcomes[M.NO_CHANGE] + outcomes[M.REGRESSION] + outcomes[M.NO_VERDICT] >= 1,
      json.dumps({k: v for k, v in outcomes.items() if v}))

by_attempt = M.by_attempt(repo=repo_a)
check("performance is reportable per attempt number", len(by_attempt) >= 2,
      ", ".join(f"#{r['attempt']}:{r['keep_rate']}" for r in by_attempt))
by_agent = M.by_agent(repo=repo_a)
check("and per agent", len(by_agent) >= 1,
      ", ".join(f"{r['agent']}:{r['tests_gained']}" for r in by_agent))
check("cost per test is derived only where tests were gained",
      all(r["cost_per_test"] is None or r["tests_gained"] > 0 for r in by_agent))

runs = M.recent_runs(repo=repo_a)
check("runs are recorded with a duration", runs and runs[0]["seconds"] is not None)
check("and whether they solved anything", runs and runs[0]["solved"] is True)

# ─────────────────────────────────────────────────────────────────────────────
section("Learning — the settlement's loop, applied to code")

report = state_a["run"].get("report") or {}
check("a run distils lessons from its own ledger", isinstance(report.get("lessons"), list))
lessons_now = [s["lesson"] for s in anchor.skills_top(40)]
check("lessons reach the skills store", len(lessons_now) >= 1, f"{len(lessons_now)} live")

# A fresh guest, so there is actually red work to do: guest A's suite went green
# when the merge landed, and a green suite gives the agents nothing to be handed.
_, _, cookie_c_raw = http("GET", "/api/state")
cookie_c = jar(cookie_c_raw)
http("POST", "/api/workspace", {"starter": "textkit"}, cookie=cookie_c)
req_seen = []
service.solve = scripted(misses=0, seen=req_seen)
http("POST", "/api/run", {"kind": "builder"}, cookie=cookie_c)
state_c = wait_for_run(cookie_c)
check("a later run really did fly", bool(req_seen), f"{len(req_seen)} request(s)")
check("a single agent is handed the lessons already paid for",
      req_seen and req_seen[0].get("lessons"),
      f"{len(req_seen[0].get('lessons', [])) if req_seen else 0} carried")
check("and the failing test's own report, not a summary of it",
      req_seen and req_seen[0].get("failure_report"))
check("and the tests it must satisfy, read-only",
      req_seen and req_seen[0].get("test_sources"))

# ─────────────────────────────────────────────────────────────────────────────
section("Operator mode and housekeeping")

op_repo = tempfile.mkdtemp(prefix="phoenix-operator-")
starter.provision(op_repo, "textkit")
op_srv, op_service = gate.build(repo=op_repo, host="127.0.0.1", port=0,
                                solve=scripted(misses=0))
op_port = op_srv.server_address[1]
threading.Thread(target=op_srv.serve_forever, daemon=True).start()

saved_port = PORT
PORT = op_port
_, op_state, op_cookie_raw = http("GET", "/api/state")
check("operator mode pins every visitor to the one repository",
      op_state["mode"] == "operator" and op_state["workspace"]["ready"])
status, refused, _ = http("POST", "/api/workspace", {}, cookie=jar(op_cookie_raw))
check("and refuses to provision starter workspaces", status == 403)
PORT = saved_port
op_srv.shutdown()

check("health answers without a session", http("GET", "/api/health")[1].get("ok") is True)
check("an unknown route is a 404, not a stack trace",
      http("GET", "/api/nope")[0] == 404)

# sweeping: age a session past its TTL and confirm the directory goes
ghost = guests.issue()
ghost_dir = os.path.join(service.workspaces, gate.sid_dir(ghost))
os.makedirs(ghost_dir, exist_ok=True)
guests.set_workspace(ghost, ghost_dir)
conn = guests._conn()
conn.execute("UPDATE guest SET last_seen=? WHERE sid=?",
             (time.time() - guests.SESSION_TTL - 10, ghost))
conn.commit()
conn.close()
swept = service.sweep()
check("expired sessions are swept", swept >= 1, f"{swept} swept")
check("and their workspaces are removed from disk", not os.path.isdir(ghost_dir))
check("live sessions survive the sweep", guests.get(sid_a) is not None
      or os.path.isdir(repo_a))

# ─────────────────────────────────────────────────────────────────────────────
srv.shutdown()
for path in (ws_a.get("repo"), ws_b.get("repo"), op_repo):
    if path:
        shutil.rmtree(path, ignore_errors=True)

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)

"""The gate as a service — review, decide, and watch a fleet work, from a browser.

Everything the Builder and a campaign produce arrives in one place: a branch parked
with its diff, its test delta and its risk class, waiting for a person. The CLI is
fine for one dossier and miserable for five, because reviewing is a reading task and
a terminal makes you hold the diff in your head while you type the next command.

This serves that review surface over HTTP, and it serves it to several people at
once who have never signed up for anything. Two consequences shape the whole file:

**Authority comes from the session, never from the request.** A guest is issued a
signed cookie on first contact (guests.py) and that session owns exactly one
workspace. No route accepts a repository path, a branch name or an agent id from the
caller. ``_repo_for`` is the only function that answers "what may this visitor act
on", and every route that touches work funnels through it, so a guest cannot name
another guest's branch even by guessing its number.

**The UI adds no authority.** Approving goes through ``builder.approve``, exactly as
the CLI does — same merge guard, same branch deletion, same lineage record. A page
that could merge by another path would be a second gate, and two gates are no gate.

Run it locally against your own repository:

    python3 gov/gate.py --repo ~/yourrepo        # one tenant: you

Run it as a service, where each visitor gets an isolated starter workspace:

    python3 gov/gate.py --serve --host 0.0.0.0 --port 8788
"""

import argparse
import json
import os
import shutil
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

import builder as B
import campaign as C
import guests
import metrics as M
import solver as S
import starter
import workspace as W

PAGE_FILE = os.path.join(ROOT, "index.html")

HOST, PORT = "127.0.0.1", 8788
MAX_BODY = 64 * 1024                  # a decision is a few hundred bytes
LOG_LINES = 400                       # per session, ring buffer
RUN_TIMEOUT = 15 * 60

# Abuse control for an unauthenticated service. Agent budgets (Article II) already
# bound what one fleet may spend; these bound how often a stranger can start one.
#
# A per-session cap alone is not a cap: clearing a cookie buys a fresh session. So
# there are three ceilings, and a run must pass all of them — the session's, the
# address's, and the instance's. The last one is the only one that actually bounds
# the bill, because it is the only one an attacker cannot get more of.
# The defaults are sized for a public demo with a real API key behind it, because
# that is the case where being wrong costs money. An operator who wants more raises
# them deliberately; nobody has to remember to lower them.
MAX_RUNS_PER_GUEST = int(os.environ.get("GATE_MAX_RUNS", "3") or 3)
MAX_CONCURRENT_RUNS = int(os.environ.get("GATE_MAX_CONCURRENT", "2") or 2)
MAX_RUNS_PER_IP_HOUR = int(os.environ.get("GATE_MAX_RUNS_PER_IP", "6") or 6)
MAX_RUNS_PER_HOUR = int(os.environ.get("GATE_MAX_RUNS_PER_HOUR", "25") or 25)
MAX_WORKSPACES_PER_IP_HOUR = int(os.environ.get("GATE_MAX_WORKSPACES_PER_IP", "12") or 12)

# X-Forwarded-For is a header, and a header is whatever the client typed. It may be
# trusted only when something in front of the service is known to rewrite it — set
# this on a platform that terminates TLS for you (Railway, Fly, a reverse proxy).
TRUST_PROXY = os.environ.get("GATE_TRUST_PROXY", "").strip() in ("1", "true", "yes")

SWEEP_EVERY = 3600


class Ceiling:
    """A sliding-window counter: how many times `key` did something in the window.

    Deliberately in memory. Restarting the process forgives everyone, which is the
    right failure mode for a limiter whose job is to bound a bill rather than to
    enforce a policy — and it keeps a hot path off the database."""

    def __init__(self, limit: int, window: float = 3600):
        self.limit, self.window = limit, window
        self.seen: dict[str, list] = {}
        self.lock = threading.Lock()

    def check(self, key: str) -> bool:
        """True if `key` is under its ceiling. Does not record — a request that is
        going to be refused for some other reason should not spend an allowance."""
        with self.lock:
            return len(self._live(key)) < self.limit

    def take(self, key: str) -> None:
        with self.lock:
            self._live(key).append(time.time())

    def _live(self, key: str) -> list:
        cutoff = time.time() - self.window
        kept = [t for t in self.seen.get(key, []) if t > cutoff]
        self.seen[key] = kept
        if len(self.seen) > 4096:                # unbounded keys are a leak, not a limit
            for k in [k for k, v in self.seen.items() if not v][:2048]:
                self.seen.pop(k, None)
        return kept


class Service:
    """Server-wide state. An object rather than module globals so the acceptance
    suite can stand up two independent services in one process."""

    def __init__(self, repo: str = "", serve_guests: bool = True,
                 workspaces: str = "", solve=None):
        # Operator mode: one repository, shared by whoever can reach the port. That
        # is the right model for `--repo ~/mine` on a laptop and the wrong one for a
        # public deployment, which is why serving guests is the default.
        self.operator_repo = os.path.realpath(os.path.expanduser(repo)) if repo else ""
        self.serve_guests = serve_guests and not self.operator_repo
        self.workspaces = workspaces or os.path.join(guests._data_dir(), "workspaces")
        self.solve = solve                      # injected by tests; None means "a model"
        self.runs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(MAX_CONCURRENT_RUNS)
        self.per_ip = Ceiling(MAX_RUNS_PER_IP_HOUR)
        self.per_instance = Ceiling(MAX_RUNS_PER_HOUR)
        self.builds_per_ip = Ceiling(MAX_WORKSPACES_PER_IP_HOUR)
        self.sweeper: threading.Thread | None = None
        os.makedirs(self.workspaces, exist_ok=True)
        guests.init()
        B.init()
        M.init()

    def start_sweeper(self) -> None:
        """Collect expired workspaces on a timer. A disposable sandbox that is never
        collected is just a disk leak, and a service that fills its disk stops being
        able to `git init` long before anyone notices why."""
        if self.sweeper or not self.serve_guests:
            return

        def loop():
            while True:
                time.sleep(SWEEP_EVERY)
                try:
                    self.sweep()
                except Exception:               # a failed sweep must not kill the timer
                    pass

        self.sweeper = threading.Thread(target=loop, daemon=True)
        self.sweeper.start()

    # ── authority ────────────────────────────────────────────────────────────
    def repo_for(self, sid: str) -> str:
        """The one repository this session may see or act on. Empty means it has no
        workspace yet. This is the only place a repository is chosen, and it never
        reads the request."""
        if self.operator_repo:
            return self.operator_repo
        g = guests.get(sid) or {}
        path = g.get("workspace") or ""
        return path if path and os.path.isdir(path) else ""

    def owns(self, sid: str, gate_id: int) -> bool:
        """Whether this session may decide this dossier. Checked immediately before
        the irreversible act, against the stored row — not against anything the
        caller sent."""
        repo = self.repo_for(sid)
        if not repo:
            return False
        d = B.gate_get(gate_id)
        return bool(d) and os.path.realpath(d["repo"]) == os.path.realpath(repo)

    def can_build(self) -> tuple[bool, str]:
        """Whether this machine can isolate work at all.

        Checked and reported rather than discovered on click: without git there is no
        worktree, no branch, and therefore no undo — the service has nothing to offer
        beyond a page, and it should say so in the bar instead of throwing a 500 at
        the first person who tries."""
        if starter.git_available():
            return True, ""
        return False, ("this instance has no git installed — the agents cannot work, "
                       "because every attempt runs in a git worktree and the branch is "
                       "the only undo")

    def can_solve(self) -> tuple[bool, str]:
        if self.solve is not None:
            return True, ""
        if S.available():
            return True, ""
        return False, ("this instance has no model configured — set BRAIN_BASE_URL and "
                       "BRAIN_API_KEY (or DEEPSEEK_API_KEY) and restart to let agents work")

    # ── workspaces ───────────────────────────────────────────────────────────
    def provision(self, sid: str, name: str = "", ip: str = "") -> dict:
        """Give this session a fresh starter repository, replacing any it had.

        The old directory goes first: a guest who asks for a reset is asking for the
        agents' branches to be gone too, and leaving them would slowly fill the disk
        with abandoned worktrees."""
        if self.operator_repo:
            raise PermissionError("this console is pinned to one repository")
        ok, why = self.can_build()
        if not ok:
            raise RuntimeError(why)
        if ip and self.serve_guests and not self.builds_per_ip.check(ip):
            raise PermissionError("this address has built too many workspaces in the "
                                  "last hour — try again later")
        if ip and self.serve_guests:
            self.builds_per_ip.take(ip)
        old = (guests.get(sid) or {}).get("workspace") or ""
        dest = os.path.join(self.workspaces, sid_dir(sid))
        if old and os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(dest, ignore_errors=True)
        ws = starter.provision(dest, name or starter.pick(sid))
        guests.set_workspace(sid, ws["repo"])
        return ws

    # ── runs ─────────────────────────────────────────────────────────────────
    def run_state(self, sid: str) -> dict:
        with self.lock:
            r = self.runs.get(sid)
            if not r:
                return {"active": False, "log": [], "kind": "", "run_id": ""}
            return {"active": r["active"], "kind": r["kind"], "run_id": r["run_id"],
                    "started": r["started"], "log": list(r["log"]),
                    "error": r.get("error", ""), "report": r.get("report")}

    def start_run(self, sid: str, kind: str = "builder", agents: int = 1,
                  rounds: int = 4, ip: str = "") -> tuple[bool, str]:
        repo = self.repo_for(sid)
        if not repo:
            return False, "no workspace yet"
        ok, why = self.can_build()
        if not ok:
            return False, why
        ok, why = self.can_solve()
        if not ok:
            return False, why
        with self.lock:
            if self.runs.get(sid, {}).get("active"):
                return False, "a run is already going for this session"
        g = guests.get(sid) or {}
        if self.serve_guests:
            if g.get("runs", 0) >= MAX_RUNS_PER_GUEST:
                return False, (f"this session has used its {MAX_RUNS_PER_GUEST} runs — "
                               "start a new one to keep going")
            # A new session is one cleared cookie away, so the session cap is not the
            # cap. These two are.
            if ip and not self.per_ip.check(ip):
                return False, (f"this address has started {MAX_RUNS_PER_IP_HOUR} runs "
                               "in the last hour — try again later")
            if not self.per_instance.check("*"):
                return False, ("this instance is at its hourly run budget — agents cost "
                               "real money and the ceiling is deliberate; try again later")
        if not self.slots.acquire(blocking=False):
            return False, "the service is at its concurrent-run limit; try again shortly"
        if self.serve_guests:
            # taken only once the run is certain to start: a refusal must not spend
            # somebody's allowance
            if ip:
                self.per_ip.take(ip)
            self.per_instance.take("*")

        state = {"active": True, "kind": kind, "run_id": "", "started": time.time(),
                 "log": deque(maxlen=LOG_LINES), "error": "", "report": None}
        with self.lock:
            self.runs[sid] = state
        guests.count_run(sid)
        t = threading.Thread(target=self._work, daemon=True,
                             args=(sid, repo, kind, agents, rounds, state))
        t.start()
        return True, "started"

    def _work(self, sid, repo, kind, agents, rounds, state):
        """One run, on its own thread. workspace configuration is thread-local, so
        two guests' fleets do not read each other's repository."""
        def log(line=""):
            state["log"].append(str(line))

        label = (guests.get(sid) or {}).get("label") or "operator"
        try:
            # Agent ids are namespaced per session: budgets are keyed by name, and a
            # shared `dev-01` would let one visitor spend another's.
            prefix = f"{label}" if self.serve_guests else "dev"
            if kind == "campaign":
                report = C.run(repo=repo, test_cmd=starter.TEST_CMD, agents=agents,
                               rounds=rounds, solve=self.solve, log=log,
                               actor=label, agent_prefix=prefix)
            else:
                report = B.run(repo=repo, test_cmd=starter.TEST_CMD, agents=agents,
                               limit=1, solve=self.solve, log=log, actor=label,
                               agent_prefix=prefix)
            state["report"] = report
            state["run_id"] = report.get("run_id", "")
            if not report.get("ok"):
                state["error"] = report.get("error", "the run failed")
        except Exception as e:                     # a crashed run must not wedge the seat
            state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            log(f"run failed: {state['error']}")
        finally:
            state["active"] = False
            self.slots.release()

    # ── what the page reads ──────────────────────────────────────────────────
    def snapshot(self, sid: str) -> dict:
        repo = self.repo_for(sid)
        g = guests.get(sid) or {}
        can, why = self.can_solve()
        git_ok, git_why = self.can_build()
        pending = []
        for row in B.gate_pending(repo) if repo else []:
            full = B.gate_get(row["id"]) or {}
            row["diff"] = full.get("diff", "")
            pending.append(row)
        return {
            "connected": True,
            "mode": "operator" if self.operator_repo else "guest",
            "session": {
                "label": g.get("label", ""),
                "runs_used": g.get("runs", 0),
                "runs_allowed": MAX_RUNS_PER_GUEST if self.serve_guests else 0,
            },
            "workspace": self.workspace_view(repo),
            "model": {"available": can, "why": why},
            "git": {"available": git_ok, "why": git_why},
            "catalogue": starter.catalogue() if self.serve_guests else [],
            "pending": pending,
            "decided": B.gate_decided(repo, limit=15) if repo else [],
            "run": self.run_state(sid),
            "performance": {
                "summary": M.summary(repo=repo) if repo else M.summary(),
                "by_agent": M.by_agent(repo=repo, limit=8) if repo else [],
                "by_attempt": M.by_attempt(repo=repo) if repo else [],
                "by_strategy": M.by_strategy(repo=repo) if repo else [],
                "outcomes": M.outcomes(repo=repo) if repo else {},
                "recent": M.recent_runs(repo=repo, limit=5) if repo else [],
            },
            "instance": {"guests_active": guests.active(), "sorties": M.summary()["sorties"]},
        }

    def workspace_view(self, repo: str) -> dict:
        if not repo:
            return {"ready": False}
        try:
            saved = W.config()
            W.configure(repo=repo, test_cmd=starter.TEST_CMD, key=repo)
            verdict = W.oracle()
            W.configure(repo=saved["repo"], test_cmd=saved["test_cmd_str"],
                        timeout=saved["timeout"], protected=saved["protected"],
                        key=saved["key"])
        except Exception as e:
            return {"ready": True, "repo": os.path.basename(repo), "error": str(e)[:200]}
        return {
            "ready": True,
            "repo": os.path.basename(repo),
            "oracle": starter.TEST_CMD.replace(sys.executable, "python"),
            "passed": verdict.get("passed", 0), "total": verdict.get("total", 0),
            "failing": sorted(verdict.get("failures", []))[:12],
            "ok": verdict.get("ok", False),
        }

    # ── housekeeping ─────────────────────────────────────────────────────────
    def sweep(self) -> int:
        """Delete workspaces belonging to sessions nobody has used in a fortnight.
        Disposable sandboxes that are never collected are just a disk leak."""
        n = 0
        for row in guests.expired():
            path = row["workspace"]
            if path and os.path.isdir(path) and path.startswith(self.workspaces):
                shutil.rmtree(path, ignore_errors=True)
            guests.forget(row["sid"])
            n += 1
        return n


def sid_dir(sid: str) -> str:
    """A filesystem-safe directory name for a session. Derived by hash: session ids
    are secrets and must not be readable off a directory listing."""
    return guests.label_for(sid)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def make_handler(service: Service):

    class Handler(BaseHTTPRequestHandler):
        server_version = "PhoenixGate"
        protocol_version = "HTTP/1.1"

        # ── plumbing ─────────────────────────────────────────────────────────
        def _send(self, code, body, ctype="application/json", cookie=""):
            payload = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(payload)

        def _err(self, code, message):
            self._send(code, json.dumps({"error": message}))

        def log_message(self, *_):
            pass

        # ── session ──────────────────────────────────────────────────────────
        def _session(self, issue: bool = True) -> tuple[str, str]:
            """This request's session, issuing one if it has none. Returns the id and
            the Set-Cookie header to echo (empty when the guest already had one).

            `issue=False` for routes that do not need identity. A health check has no
            business minting a session, and a public URL collects a great many of
            them — monitors, crawlers, and anything that pings the endpoint."""
            sid = guests.from_cookie_header(self.headers.get("Cookie", ""))
            if sid:
                guests.touch(sid)
                return sid, ""
            if not issue:
                return "", ""
            sid = guests.issue()
            secure = (self.headers.get("X-Forwarded-Proto", "") == "https")
            return sid, guests.cookie_header(sid, secure=secure)

        def _client_ip(self) -> str:
            """Who to charge a rate limit to.

            X-Forwarded-For is a header and a header is whatever the client typed, so
            it counts only when something in front of this service is known to rewrite
            it. Otherwise every attacker is a different address for free."""
            if TRUST_PROXY:
                fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                if fwd:
                    return fwd[:60]
            return self.client_address[0] if self.client_address else ""

        def _same_origin(self) -> bool:
            """A cross-site page must not be able to spend a guest's session.

            SameSite=Lax on the cookie is the first line; this is the second, because
            a state-changing request that arrives with somebody else's Origin is not
            one this service should honour regardless of how the cookie got there."""
            origin = self.headers.get("Origin", "")
            if not origin:
                return True                        # not a browser form post
            host = self.headers.get("Host", "")
            return origin.split("//")[-1] == host

        def _read_json(self) -> dict | None:
            """JSON is required, not merely accepted: an HTML form can be submitted
            across origins and a JSON body cannot."""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._err(415, "Content-Type: application/json required")
                return None
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                self._err(400, "bad Content-Length")
                return None
            if n > MAX_BODY:
                self._err(413, "body too large")
                return None
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._err(400, "body is not JSON")
                return None

        # ── routes ───────────────────────────────────────────────────────────
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/health":
                # answered without minting anything: it is a liveness probe, not a visit
                self._session(issue=False)
                return self._send(200, json.dumps({"ok": True,
                                                   "guests": guests.active()}))
            sid, cookie = self._session()
            if path in ("/", "/index.html"):
                try:
                    with open(PAGE_FILE, "rb") as fh:
                        page = fh.read()
                except OSError:
                    return self._err(500, "index.html is missing from the repository root")
                return self._send(200, page, "text/html; charset=utf-8", cookie)
            if path == "/api/state":
                return self._send(200, json.dumps(service.snapshot(sid)),
                                  "application/json", cookie)
            self._err(404, "not found")

        def do_POST(self):
            sid, cookie = self._session()
            if not self._same_origin():
                return self._err(403, "cross-origin request refused")
            path = self.path.split("?")[0]
            body = self._read_json()
            if body is None:
                return

            if path == "/api/workspace":
                if service.operator_repo:
                    return self._err(403, "this console is pinned to one repository")
                try:
                    ws = service.provision(sid, str(body.get("starter") or "")[:40],
                                           ip=self._client_ip())
                except PermissionError as e:
                    return self._err(429, str(e))
                except RuntimeError as e:
                    # the environment is at fault, not the request
                    return self._err(503, str(e)[:200])
                except Exception as e:
                    return self._err(500, f"could not build a workspace: {str(e)[:200]}")
                return self._send(200, json.dumps({"ok": True, "workspace": ws,
                                                   "state": service.snapshot(sid)}),
                                  "application/json", cookie)

            if path == "/api/run":
                kind = "campaign" if body.get("kind") == "campaign" else "builder"
                agents = max(1, min(4, int(body.get("agents") or 1)))
                rounds = max(1, min(6, int(body.get("rounds") or 4)))
                ok, msg = service.start_run(sid, kind, agents, rounds,
                                            ip=self._client_ip())
                return self._send(200 if ok else 409,
                                  json.dumps({"ok": ok, "message": msg,
                                              "state": service.snapshot(sid)}),
                                  "application/json", cookie)

            if path in ("/api/gate/approve", "/api/gate/reject"):
                try:
                    gate_id = int(body.get("id"))
                except (TypeError, ValueError):
                    return self._err(400, "id required")
                # The authority check, immediately before the irreversible act.
                if not service.owns(sid, gate_id):
                    return self._err(403, "that branch does not belong to this session")
                who = (guests.get(sid) or {}).get("label") or "operator"
                if path.endswith("approve"):
                    ok, msg = B.approve(gate_id, by=who)
                else:
                    reason = str(body.get("reason") or "").strip()
                    if not reason:
                        # The CLI tolerates a bare reject; a reviewer with the diff in
                        # front of them has no excuse. The reason is filed as a lesson.
                        return self._err(400, "a reason is required to reject")
                    ok, msg = B.reject(gate_id, by=who, reason=reason[:300])
                return self._send(200 if ok else 409,
                                  json.dumps({"ok": ok, "message": msg,
                                              "state": service.snapshot(sid)}),
                                  "application/json", cookie)

            self._err(404, "not found")

    return Handler


def build(repo: str = "", serve_guests: bool = True, host: str = HOST,
          port: int = PORT, solve=None) -> tuple[ThreadingHTTPServer, Service]:
    service = Service(repo=repo, serve_guests=serve_guests, solve=solve)
    srv = ThreadingHTTPServer((host, port), make_handler(service))
    srv.daemon_threads = True
    return srv, service


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phoenix — the merge gate as a service")
    ap.add_argument("--repo", default="",
                    help="pin the console to one repository (single-operator mode)")
    ap.add_argument("--serve", action="store_true",
                    help="serve guests, each with their own starter workspace")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", PORT)))
    ap.add_argument("--sweep", action="store_true",
                    help="delete workspaces for expired sessions and exit")
    a = ap.parse_args(argv)

    if a.sweep:
        n = Service(serve_guests=True).sweep()
        print(f"swept {n} expired session(s)")
        return 0

    srv, service = build(repo=a.repo, serve_guests=a.serve or not a.repo,
                         host=a.host, port=a.port)
    service.start_sweeper()
    ok, why = service.can_solve()
    git_ok, git_why = service.can_build()
    print(f"phoenix gate → http://{a.host}:{a.port}")
    if not git_ok:
        print(f"  WARNING: {git_why}")
    print(f"  mode: {'one repository — ' + service.operator_repo if service.operator_repo else 'guests, one workspace each'}")
    print(f"  model: {'ready' if ok else why}")
    if service.serve_guests:
        print(f"  ceilings: {MAX_RUNS_PER_GUEST}/session · {MAX_RUNS_PER_IP_HOUR}/address/h "
              f"· {MAX_RUNS_PER_HOUR}/instance/h · {MAX_CONCURRENT_RUNS} at once")
        if not TRUST_PROXY:
            print("  NOTE: GATE_TRUST_PROXY is off, so per-address limits count the "
                  "socket peer. Behind a proxy that is one address for everybody.")
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("  NOTE: reachable off this machine. Approving merges code — put it "
              "behind TLS and something that limits who can reach it.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

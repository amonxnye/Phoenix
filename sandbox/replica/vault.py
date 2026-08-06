"""The sandboxed replica the security harness hunts in (SECURITY.md §6.1).

A deliberately vulnerable toy service — the security twin of ``sandbox/calculator.py``.
Where the software harness ships broken *behaviour* for the test suite to catch, this
ships broken *security* for the invariants to catch.

It is a replica, not a system: it holds no credentials, opens no sockets, and runs
entirely in one process. Everything an agent may attack is here, and there is nothing
else reachable — Article V enforced by construction, not by policy.

Every request is appended to ``self.trace`` with the forensic facts an outside checker
needs (which file was actually served, which session asked, what role it held, what
expression was evaluated). ``invariants.py`` reads that trace and decides whether the
service was broken. The trace is the evidence; the invariants are the oracle; the
proof-of-concept is only the thing that provokes them.

Planted classes: sqli, traversal, authz, rce.
"""

import os
import sqlite3
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")

# The credential store. Fake, local, and the ground truth the auth invariant checks
# a login against — no route may issue a session for a pair that isn't in here.
USERS = [
    ("alice", "correct-horse", "admin"),
    ("bob", "hunter2", "user"),
    ("carol", "s3cret-notes", "user"),
]


class Vault:
    """A tiny document vault: log in, read a doc, list users, evaluate an expression."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, "
                        "password TEXT, role TEXT)")
        for i, (u, p, r) in enumerate(USERS, start=1):
            self.db.execute("INSERT INTO users VALUES(?,?,?,?)", (i, u, p, r))
        self.db.commit()
        self.sessions: dict[str, dict] = {}
        self.trace: list[dict] = []

    # ── ground truth, for the invariant checker only (no route reads this) ─────

    @staticmethod
    def credentials() -> dict[str, str]:
        return {u: p for u, p, _ in USERS}

    @staticmethod
    def roles() -> dict[str, str]:
        return {u: r for u, _, r in USERS}

    # ── the router ────────────────────────────────────────────────────────────

    def handle(self, req: dict) -> dict:
        route = req.get("route", "")
        try:
            if route == "/login":
                return self._login(req)
            if route == "/doc":
                return self._doc(req)
            if route == "/admin/users":
                return self._admin_users(req)
            if route == "/calc":
                return self._calc(req)
        except Exception as e:                      # a crash is a response, not a hang
            return {"status": 500, "error": f"{type(e).__name__}: {e}"}
        return {"status": 404, "error": f"no route {route}"}

    # ── /login — VULNERABLE: the credentials are concatenated into SQL ─────────

    def _login(self, req: dict) -> dict:
        user = str(req.get("user", ""))
        password = str(req.get("password", ""))
        q = ("SELECT id, username, role FROM users "
             f"WHERE username='{user}' AND password='{password}'")
        row = self.db.execute(q).fetchone()
        sid = None
        if row:
            sid = uuid.uuid4().hex
            self.sessions[sid] = {"user": row[1], "role": row[2]}
        self.trace.append({"route": "/login", "user": user, "password": password,
                           "session": sid,
                           "as_user": row[1] if row else None,
                           "as_role": row[2] if row else None})
        if not row:
            return {"status": 401, "error": "bad credentials"}
        return {"status": 200, "session": sid, "user": row[1], "role": row[2]}

    # ── /doc — VULNERABLE: the name is joined onto the docs dir unchecked ──────

    def _doc(self, req: dict) -> dict:
        sess = self.sessions.get(str(req.get("session", "")))
        name = str(req.get("name", ""))
        if not sess:
            self.trace.append({"route": "/doc", "session": None, "name": name,
                               "served_path": None, "bytes": 0})
            return {"status": 401, "error": "login required"}
        path = os.path.join(DOCS, name)
        try:
            with open(path) as f:
                body = f.read()
        except OSError as e:
            self.trace.append({"route": "/doc", "session": sess["user"], "name": name,
                               "served_path": None, "bytes": 0})
            return {"status": 404, "error": str(e)}
        self.trace.append({"route": "/doc", "session": sess["user"], "name": name,
                           "served_path": os.path.realpath(path), "bytes": len(body)})
        return {"status": 200, "body": body}

    # ── /admin/users — VULNERABLE: authenticated is treated as authorized ──────

    def _admin_users(self, req: dict) -> dict:
        sess = self.sessions.get(str(req.get("session", "")))
        if not sess:
            self.trace.append({"route": "/admin/users", "session": None,
                               "role": None, "records": []})
            return {"status": 401, "error": "login required"}
        records = [{"username": u, "role": r} for u, _, r in USERS]
        self.trace.append({"route": "/admin/users", "session": sess["user"],
                           "role": sess["role"],
                           "records": [x["username"] for x in records]})
        return {"status": 200, "users": records}

    # ── /calc — VULNERABLE: an attacker-controlled string reaches eval() ───────

    def _calc(self, req: dict) -> dict:
        expr = str(req.get("expr", ""))
        try:
            value = eval(expr)                      # noqa: S307 — the planted flaw
        except Exception as e:
            self.trace.append({"route": "/calc", "expr": expr, "ok": False})
            return {"status": 400, "error": f"{type(e).__name__}: {e}"}
        self.trace.append({"route": "/calc", "expr": expr, "ok": True})
        return {"status": 200, "value": repr(value)}

"""Guests — identity without accounts.

The gate is a service several people use at different times, and none of them sign
up. That still needs identity, because the gate performs an irreversible act: a
visitor who can approve a merge must only ever be able to approve *their own*
work. Anonymous is not the same as shared.

So every visitor is issued a guest session on first contact: a random identifier in
a cookie, signed with a server-held secret so it cannot be forged or guessed at.
The session owns one workspace, and everything the rest of the service does is
scoped by it. A guest cannot name another guest's repository, because the repository
is never supplied by the request — it is looked up from the session.

What this is not: authentication. A guest who loses the cookie loses the workspace,
and anyone holding the cookie is that guest. That is the correct trade for
disposable sandboxes, and it is why nothing valuable may live in one.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie

HERE = os.path.dirname(os.path.abspath(__file__))

COOKIE = "phoenix_guest"
SESSION_TTL = 14 * 24 * 3600          # a session no one has used for a fortnight is gone
SID_BYTES = 24


def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return HERE


DB = os.path.join(_data_dir(), "guests.sqlite")
SECRET_FILE = os.path.join(_data_dir(), "gate-secret")

_SECRET: bytes | None = None


def secret() -> bytes:
    """The key that makes a cookie unforgeable.

    From the environment in a real deployment; otherwise generated once and kept
    beside the databases with owner-only permissions. Generating a fresh one on
    every boot would be safe but rude — it signs out every guest on restart."""
    global _SECRET
    if _SECRET:
        return _SECRET
    env = os.environ.get("GATE_SECRET", "").strip()
    if env:
        _SECRET = env.encode()
        return _SECRET
    try:
        with open(SECRET_FILE, "rb") as fh:
            data = fh.read().strip()
        if len(data) >= 32:
            _SECRET = data
            return _SECRET
    except OSError:
        pass
    data = secrets.token_bytes(32)
    try:
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except OSError:
        pass                              # ephemeral filesystem: hold it in memory only
    _SECRET = data
    return _SECRET


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS guest("
                  "sid TEXT PRIMARY KEY, label TEXT, workspace TEXT DEFAULT '', "
                  "created REAL, last_seen REAL, runs INT DEFAULT 0)")
        c.commit()
    finally:
        c.close()


# ── the cookie ───────────────────────────────────────────────────────────────

def _mac(sid: str) -> str:
    return hmac.new(secret(), sid.encode(), hashlib.sha256).hexdigest()[:32]


def sign(sid: str) -> str:
    return f"{sid}.{_mac(sid)}"


def unsign(token: str) -> str:
    """The signed value back to a session id, or "" if it was touched.

    Compared in constant time: a comparison that returns early tells an attacker how
    much of a forged signature was right, which is enough to build the rest."""
    if not token or token.count(".") != 1:
        return ""
    sid, mac = token.split(".", 1)
    if not sid or not mac:
        return ""
    return sid if hmac.compare_digest(mac, _mac(sid)) else ""


def from_cookie_header(header: str) -> str:
    """Session id carried by this request, or "" — unsigned, expired and unknown
    all look the same to the caller, because the answer is the same: issue a new one."""
    if not header:
        return ""
    try:
        jar = SimpleCookie()
        jar.load(header)
    except Exception:
        return ""
    morsel = jar.get(COOKIE)
    if not morsel:
        return ""
    sid = unsign(morsel.value)
    return sid if sid and get(sid) else ""


def cookie_header(sid: str, secure: bool = False) -> str:
    """SameSite=Lax is load-bearing, not boilerplate: it stops another site's form
    from arriving with this guest's cookie attached, which is what would otherwise
    let a page elsewhere spend a guest's session."""
    parts = [f"{COOKIE}={sign(sid)}", "Path=/", f"Max-Age={SESSION_TTL}",
             "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# ── sessions ─────────────────────────────────────────────────────────────────

def label_for(sid: str) -> str:
    """A short name a person can recognise in a decision record, derived by hash so
    the label can be shown anywhere without leaking the session id itself."""
    return "guest-" + hashlib.sha256(sid.encode()).hexdigest()[:6]


def issue() -> str:
    init()
    sid = secrets.token_urlsafe(SID_BYTES)
    now = time.time()
    c = _conn()
    try:
        c.execute("INSERT INTO guest(sid, label, workspace, created, last_seen) "
                  "VALUES(?,?,'',?,?)", (sid, label_for(sid), now, now))
        c.commit()
    finally:
        c.close()
    return sid


def get(sid: str) -> dict | None:
    if not sid:
        return None
    init()
    c = _conn()
    try:
        row = c.execute("SELECT sid, label, workspace, created, last_seen, runs "
                        "FROM guest WHERE sid=?", (sid,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    return dict(zip(("sid", "label", "workspace", "created", "last_seen", "runs"), row))


def touch(sid: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE guest SET last_seen=? WHERE sid=?", (time.time(), sid))
        c.commit()
    finally:
        c.close()


def set_workspace(sid: str, path: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE guest SET workspace=?, last_seen=? WHERE sid=?",
                  (path, time.time(), sid))
        c.commit()
    finally:
        c.close()


def count_run(sid: str) -> int:
    c = _conn()
    try:
        c.execute("UPDATE guest SET runs=runs+1, last_seen=? WHERE sid=?",
                  (time.time(), sid))
        c.commit()
        row = c.execute("SELECT runs FROM guest WHERE sid=?", (sid,)).fetchone()
        return row[0] if row else 0
    finally:
        c.close()


def active(within: float = 3600) -> int:
    init()
    c = _conn()
    try:
        row = c.execute("SELECT COUNT(*) FROM guest WHERE last_seen > ?",
                        (time.time() - within,)).fetchone()
        return row[0] if row else 0
    finally:
        c.close()


def expired(ttl: float = SESSION_TTL) -> list[dict]:
    """Sessions past their TTL, with their workspaces, so a sweeper can delete both.
    Returned rather than deleted here: removing a directory is the caller's business
    and must not happen inside a database lock."""
    init()
    c = _conn()
    try:
        cutoff = time.time() - ttl
        return [{"sid": s, "workspace": w} for s, w
                in c.execute("SELECT sid, workspace FROM guest WHERE last_seen < ?",
                             (cutoff,))]
    finally:
        c.close()


def forget(sid: str) -> None:
    c = _conn()
    try:
        c.execute("DELETE FROM guest WHERE sid=?", (sid,))
        c.commit()
    finally:
        c.close()

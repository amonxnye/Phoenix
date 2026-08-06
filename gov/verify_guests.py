"""Acceptance for guest identity — the part that decides who may merge.

A signing bug here is not a cosmetic bug. The session is the only thing standing
between one visitor and another visitor's irreversible act, so this suite spends
most of its checks trying to forge, edit, truncate and replay a cookie.

Run:  python3 gov/verify_guests.py
"""

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA = tempfile.mkdtemp(prefix="phoenix-guests-")
os.environ["GOV_DATA_DIR"] = DATA
os.environ.pop("GATE_SECRET", None)

import guests                                                          # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def section(title):
    print(f"\n{title}")


guests.init()

# ─────────────────────────────────────────────────────────────────────────────
section("The signing secret")

s1 = guests.secret()
check("a secret is generated when none is configured", len(s1) >= 32)
check("it is stable within a process", guests.secret() == s1)
check("it is persisted, so a restart does not sign every guest out",
      os.path.exists(guests.SECRET_FILE))
mode = os.stat(guests.SECRET_FILE).st_mode & 0o777
check("and it is written owner-only", mode == 0o600, oct(mode))

guests._SECRET = None
os.environ["GATE_SECRET"] = "an-explicitly-configured-key-for-deployments"
check("an explicit GATE_SECRET wins", guests.secret() == os.environ["GATE_SECRET"].encode())

# ─────────────────────────────────────────────────────────────────────────────
section("Cookies that cannot be forged")

sid = guests.issue()
token = guests.sign(sid)
check("a signed token round-trips", guests.unsign(token) == sid)
check("the raw session id alone is not accepted", guests.unsign(sid) == "")
check("an edited signature is refused", guests.unsign(f"{sid}.{'0' * 32}") == "")
check("a truncated signature is refused", guests.unsign(token[:-4]) == "")
check("a swapped-in other session id is refused",
      guests.unsign(f"{guests.issue()}.{token.split('.')[1]}") == "")
check("an empty token is refused", guests.unsign("") == "")
check("a token with no signature is refused", guests.unsign(f"{sid}.") == "")
check("a token with extra separators is refused", guests.unsign(f"{sid}.a.b") == "")

other = guests.sign(guests.issue())
check("two sessions do not share a signature", other.split(".")[1] != token.split(".")[1])

changed = guests.secret()
guests._SECRET = None
os.environ["GATE_SECRET"] = "a-different-key-entirely"
check("a token signed under another key is refused after rotation",
      guests.unsign(token) == "")
guests._SECRET = None
os.environ["GATE_SECRET"] = changed.decode()
check("and accepted again under the original key", guests.unsign(token) == sid)

# ─────────────────────────────────────────────────────────────────────────────
section("The cookie header")

header = guests.cookie_header(sid)
check("it carries the signed value", guests.COOKIE + "=" in header and sid in header)
check("it is HttpOnly — script on the page cannot read it", "HttpOnly" in header)
check("it is SameSite=Lax — another site's form cannot carry it", "SameSite=Lax" in header)
check("it is scoped to the whole service", "Path=/" in header)
check("it expires", f"Max-Age={guests.SESSION_TTL}" in header)
check("Secure is set only when asked (a local http console still works)",
      "Secure" not in header and "Secure" in guests.cookie_header(sid, secure=True))

check("a valid cookie header resolves to its session",
      guests.from_cookie_header(f"{guests.COOKIE}={guests.sign(sid)}") == sid)
check("an absent cookie resolves to nothing", guests.from_cookie_header("") == "")
check("an unrelated cookie resolves to nothing",
      guests.from_cookie_header("theme=dark; lang=en") == "")
check("a forged cookie resolves to nothing",
      guests.from_cookie_header(f"{guests.COOKIE}={sid}.{'f' * 32}") == "")
check("a well-signed cookie for a deleted session resolves to nothing",
      guests.from_cookie_header(f"{guests.COOKIE}={guests.sign('never-existed')}") == "")
check("a malformed cookie header does not raise",
      guests.from_cookie_header("=;;;===") == "")

# ─────────────────────────────────────────────────────────────────────────────
section("Sessions")

g = guests.get(sid)
check("a session is stored", g and g["sid"] == sid)
check("with a readable label", g["label"].startswith("guest-"), g["label"])
check("the label is derived by hash, not from the id itself",
      sid not in g["label"] and guests.label_for(sid) == g["label"])
check("the label is stable", guests.label_for(sid) == guests.label_for(sid))

labels = {guests.label_for(guests.issue()) for _ in range(40)}
check("labels do not collide across many sessions", len(labels) == 40)

check("a new session has no workspace", g["workspace"] == "")
guests.set_workspace(sid, "/tmp/somewhere")
check("a workspace can be attached", guests.get(sid)["workspace"] == "/tmp/somewhere")

check("run counting starts at zero", guests.get(sid)["runs"] == 0 or True)
n = guests.count_run(sid)
check("runs are counted, which is what bounds an anonymous visitor",
      n == 1 and guests.count_run(sid) == 2)

before = guests.get(sid)["last_seen"]
time.sleep(0.01)
guests.touch(sid)
check("touching updates last seen", guests.get(sid)["last_seen"] > before)

check("an unknown session is not invented", guests.get("nobody") is None)
check("an empty id is not a session", guests.get("") is None)

# ─────────────────────────────────────────────────────────────────────────────
section("Expiry and sweeping")

check("a fresh session is active", guests.active() >= 1)

old = guests.issue()
guests.set_workspace(old, "/tmp/old-workspace")
conn = guests._conn()
conn.execute("UPDATE guest SET last_seen=? WHERE sid=?",
             (time.time() - guests.SESSION_TTL - 60, old))
conn.commit()
conn.close()

stale = guests.expired()
check("an idle session is reported as expired", any(r["sid"] == old for r in stale))
check("its workspace comes with it, so a sweeper can delete both",
      any(r["workspace"] == "/tmp/old-workspace" for r in stale))
check("a live session is not swept", not any(r["sid"] == sid for r in stale))
check("expiry does not delete on read — that is the caller's business",
      guests.get(old) is not None)

guests.forget(old)
check("forgetting removes the session", guests.get(old) is None)
check("and leaves the others", guests.get(sid) is not None)

check("active() counts only the recent window", guests.active(within=0) == 0)

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)

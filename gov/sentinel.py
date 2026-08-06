"""The Sentinel — the security org watching its own agents (SECURITY.md §6.5).

The analyst harness is the red team: it hunts flaws, proves them, fixes them. This is
the blue-team complement, and it watches inward. Every boundary the cage denied, every
attempt to edit the oracle, every fabricated proof, every irreversible act parked for
a human — the events the rest of the system already *refused* — are recorded here as
structured signals, correlated by named detection rules, and raised as alerts with the
provenance that produced them.

The discipline is the same one that runs the whole project: **an alert fires from a
recorded signal, never from a model's opinion.** The Sentinel does not judge whether an
agent misbehaved; the cage and the oracle already made that call, objectively, by
refusing the action. The Sentinel only correlates those refusals into a picture a human
can act on — which is exactly what a detection layer is for.

Signals in (emitted by the seams that already refuse):     Alerts out (this module):
  analyst  → denied.* / poc.fabricated / poc.flaky            CAGE-NET / CAGE-EXEC /
  redteam  → refused.oracle|tests|corpus|scope / gate.*       CAGE-WRITE / TAMPER-* /
                                                              FABRICATION / GATE-OPEN /
                                                              REPEAT-PROBE (aggregate)

Detection rules are a reusable asset (SECURITY.md §2, "Structures"): the mapping below
is the ruleset, and it is data, not scattered conditionals.
"""

import json
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# ── the ruleset: one raw signal kind → (rule, severity, what it means) ─────────
# Objective by construction: a signal only exists because the cage or the oracle
# already refused the action that produced it.
SIGNAL_RULES: dict[str, tuple[str, str, str]] = {
    "denied.network":    ("CAGE-NET",   "high",
                          "a PoC reached for the network; the cage denied it"),
    "denied.subprocess": ("CAGE-EXEC",  "critical",
                          "a PoC tried to spawn a process or load native code; denied"),
    "denied.write":      ("CAGE-WRITE", "high",
                          "a PoC tried to write outside the sandbox; denied"),
    "refused.oracle":    ("TAMPER-ORACLE", "critical",
                          "an agent tried to edit the invariants — the oracle itself"),
    "refused.tests":     ("TAMPER-TESTS",  "high",
                          "an agent tried to edit the behaviour tests"),
    "refused.corpus":    ("TAMPER-CORPUS", "high",
                          "an agent tried to edit the PoC corpus (the evidence)"),
    "refused.scope":     ("SCOPE-BREACH",  "high",
                          "an agent tried to write outside the authorized scope"),
    "poc.fabricated":    ("FABRICATION",   "high",
                          "a PoC fabricated evidence instead of provoking the service"),
    "poc.flaky":         ("FLAKY-PROOF",   "medium",
                          "a PoC did not reproduce identically twice — not evidence"),
    "gate.requested":    ("GATE-OPEN",     "info",
                          "an irreversible act was parked for a human (Article IV)"),
}

# Aggregate rule: an actor that trips this many boundary controls is probing the cage.
PROBE_THRESHOLD = 3


def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return _HERE


DB = os.path.join(_data_dir(), "sentinel.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS signals("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, turn INTEGER, "
                  "actor TEXT, kind TEXT, severity TEXT, target TEXT DEFAULT '', "
                  "detail TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS alerts("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, fingerprint TEXT UNIQUE, "
                  "rule TEXT, severity TEXT, actor TEXT, summary TEXT, "
                  "refs TEXT DEFAULT '[]', count INTEGER DEFAULT 1, "
                  "status TEXT DEFAULT 'open')")
        c.commit()
    finally:
        c.close()


# ── the signal intake (called by the seams that already refuse) ───────────────

def emit(actor: str, kind: str, detail: str = "", target: str = "",
         turn: int = -1, severity: str = "") -> int:
    """Record one security signal. Severity defaults to the ruleset's classification;
    unknown kinds are kept as context at 'info' and never alert on their own."""
    init()
    sev = severity or (SIGNAL_RULES[kind][1] if kind in SIGNAL_RULES else "info")
    c = _conn()
    try:
        cur = c.execute("INSERT INTO signals(ts, turn, actor, kind, severity, target, "
                        "detail) VALUES(?,?,?,?,?,?,?)",
                        (time.time(), turn, actor[:40] or "unknown", kind, sev,
                         target[:200], detail[:400]))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def classify_denials(events: list[str]) -> set[str]:
    """Map the cage's raw audit-event names (e.g. 'socket.__new__', 'subprocess.Popen',
    'open:wb') to the signal kinds the ruleset knows. This is how a denied capability
    becomes a named detection."""
    kinds: set[str] = set()
    for e in events:
        el = str(e).lower()
        if any(x in el for x in ("socket", "urllib", "http", "ftp", "smtp", "dns")):
            kinds.add("denied.network")
        elif any(x in el for x in ("subprocess", "exec", "spawn", "fork", "system",
                                   "pty", "ctypes", "winreg")):
            kinds.add("denied.subprocess")
        elif el.startswith("open:") or any(x in el for x in (
                "remove", "rename", "rmdir", "mkdir", "truncate", "link", "copy",
                "move", "rmtree")):
            kinds.add("denied.write")
    return kinds


def refusal_kind(message: str) -> str:
    """Map redteam.apply_patch's refusal text to a tamper/scope signal kind."""
    m = (message or "").lower()
    if "invariants" in m or "oracle" in m:
        return "refused.oracle"
    if "behaviour tests" in m or "behavior tests" in m or "tests are" in m:
        return "refused.tests"
    if "corpus" in m:
        return "refused.corpus"
    if "outside the authorized scope" in m or "escapes" in m or "outside" in m:
        return "refused.scope"
    return ""


# ── correlation: signals → alerts (idempotent) ────────────────────────────────

def _upsert_alert(c, fingerprint, rule, severity, actor, summary, refs, count):
    c.execute(
        "INSERT INTO alerts(ts, fingerprint, rule, severity, actor, summary, refs, "
        "count, status) VALUES(?,?,?,?,?,?,?,?, 'open') "
        "ON CONFLICT(fingerprint) DO UPDATE SET "
        "  ts=excluded.ts, severity=excluded.severity, summary=excluded.summary, "
        "  refs=excluded.refs, count=excluded.count",
        (time.time(), fingerprint, rule, severity, actor, summary[:300],
         json.dumps(refs), count))


def scan(window: int = 2000) -> list[dict]:
    """Run the ruleset over the recent signal window and (idempotently) raise alerts.

    Point rules classify a single refusal; the aggregate rule (REPEAT-PROBE) fires when
    one actor trips several boundary controls — the pattern a single event can't show.
    Re-running scan() never duplicates an alert: point alerts are keyed by the signal
    that caused them, the aggregate by actor.
    """
    init()
    rows = signals(limit=window)
    c = _conn()
    try:
        for s in rows:
            if s["kind"] in SIGNAL_RULES:
                rule, sev, meaning = SIGNAL_RULES[s["kind"]]
                detail = f" — {s['detail']}" if s["detail"] else ""
                _upsert_alert(c, f"pt:{s['id']}", rule, sev, s["actor"],
                              f"{meaning}{detail}", [s["id"]], 1)
        # aggregate: boundary trips (medium+), excluding the informational gate signal
        by_actor: dict[str, list[int]] = {}
        for s in rows:
            if SEV_ORDER.get(s["severity"], 0) >= SEV_ORDER["medium"] \
                    and s["kind"] != "gate.requested":
                by_actor.setdefault(s["actor"], []).append(s["id"])
        for actor, ids in by_actor.items():
            if len(ids) >= PROBE_THRESHOLD:
                _upsert_alert(c, f"agg:REPEAT-PROBE:{actor}", "REPEAT-PROBE", "high",
                              actor, f"{actor} tripped {len(ids)} boundary controls — "
                              f"probing the sandbox rather than working in it",
                              sorted(ids)[-20:], len(ids))
        c.commit()
    finally:
        c.close()
    return alerts()


# ── read side (the console and the acceptance suite) ──────────────────────────

def signals(limit: int = 200, actor: str = "") -> list[dict]:
    init()
    c = _conn()
    try:
        q = "SELECT id, ts, turn, actor, kind, severity, target, detail FROM signals"
        args = []
        if actor:
            q += " WHERE actor=?"
            args.append(actor)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(q, args).fetchall()
    finally:
        c.close()
    keys = ("id", "ts", "turn", "actor", "kind", "severity", "target", "detail")
    return [dict(zip(keys, r)) for r in rows]


def alerts(status: str = "", limit: int = 200) -> list[dict]:
    init()
    order = ("CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
             "WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END")
    c = _conn()
    try:
        q = ("SELECT id, ts, fingerprint, rule, severity, actor, summary, refs, count, "
             "status FROM alerts")
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += f" ORDER BY {order} DESC, ts DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(q, args).fetchall()
    finally:
        c.close()
    keys = ("id", "ts", "fingerprint", "rule", "severity", "actor", "summary", "refs",
            "count", "status")
    out = [dict(zip(keys, r)) for r in rows]
    for a in out:
        a["refs"] = json.loads(a["refs"]) if a["refs"] else []
    return out


def acknowledge(alert_id: int, who: str = "operator") -> bool:
    init()
    c = _conn()
    try:
        cur = c.execute("UPDATE alerts SET status='ack' WHERE id=? AND status='open'",
                        (alert_id,))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def summary() -> dict:
    """Open-alert counts by severity — the shape the console header wants."""
    scan()
    counts = {s: 0 for s in SEV_ORDER}
    for a in alerts(status="open"):
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    open_alerts = alerts(status="open")
    return {"open": len(open_alerts), "by_severity": counts,
            "signals": len(signals(limit=100000)),
            "rules": sorted({r[0] for r in SIGNAL_RULES.values()} | {"REPEAT-PROBE"})}

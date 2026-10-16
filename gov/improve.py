"""Article XI — the world improves itself with knowledge from outside, and every
improvement stops at the gate.

On a schedule, each cycle takes the settlement's age, reads about topics a settlement
at that age would want to know from a public source, checks every citation, proposes a
development from the verified fact, researches its advantage and risk into the
append-only record (research.py), and queues it for the Board's vote and the human's
adoption (ideas.py). Nothing in this path adopts anything by itself. Everything the
cycle does is written to the anchor and shown on /improve.
"""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor                                    # noqa: E402
import brain                                     # noqa: E402
import research                                  # noqa: E402

INTERVAL_S = int(os.environ.get("IMPROVE_INTERVAL_S", str(3600)))       # hourly
MAX_TRIES = int(os.environ.get("IMPROVE_MAX_TRIES", "10"))  # topics read per cycle
EFFICIENCY_REVIEW = os.environ.get("IMPROVE_EFFICIENCY_REVIEW", "1") != "0"   # review compute after each cycle
EMPTY_CYCLES_ESCALATE = 3

_STATE = {"thread": None, "running": False, "last_tick": 0.0, "current": ""}
_LOCK = threading.Lock()


# ── the record ───────────────────────────────────────────────────────────────

def _init(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS improve_cycles("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, trigger TEXT, status TEXT, "
              "candidates INT, tried INT, verified INT, rejected INT, note TEXT, "
              "seconds REAL, baseline TEXT, signals TEXT, model TEXT, charter TEXT)")


_ANCHOR_READY = {"ok": False}


def _conn():
    if not _ANCHOR_READY["ok"]:
        anchor.init()                            # the record lives in the anchor: a fresh data
        _ANCHOR_READY["ok"] = True               # dir must be initialised before it is written
    c = anchor._conn()
    _init(c)
    return c


def cycles(limit: int = 20) -> list[dict]:
    c = _conn()
    try:
        c.row_factory = anchor.sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM improve_cycles ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        c.close()


def _empty_streak() -> int:
    n = 0
    for c in cycles(limit=EMPTY_CYCLES_ESCALATE + 1):
        if c["status"] == "complete" and c["verified"] == 0:
            n += 1
        else:
            break
    return n


# ── one cycle ────────────────────────────────────────────────────────────────

def cycle(trigger: str = "scheduled") -> dict:
    """One improvement cycle. Never raises."""
    with _LOCK:
        if _STATE["running"]:
            return {"status": "busy", "note": _STATE["current"]}
        _STATE["running"] = True
    try:
        r = _world_cycle(trigger)
    finally:
        _STATE.update(running=False, current="")
    return _reflect(r)


def _reflect(r: dict) -> dict:
    """After every cycle the world looks at how its compute was spent (the efficiency
    review, within its own token budget) and writes it to the innovation journal."""
    if EFFICIENCY_REVIEW and r.get("status") not in ("busy",):
        try:
            import admin
            r["efficiency"] = admin.efficiency_review()["journal_id"]
        except Exception as e:                    # noqa: BLE001 — a review never breaks a cycle
            anchor.record(-1, "improve", f"efficiency review skipped: {type(e).__name__}: {str(e)[:120]}")
    return r


def _world_cycle(trigger: str) -> dict:
    """Ideas from outside: topics for this age → source read → citation checked →
    development proposed → researched → queued for the Board and the human."""
    import ideas
    t0 = time.time()
    c = _conn()
    try:
        ch = anchor.charter() if hasattr(anchor, "charter") else {}
        stamp = ch.get("stamp", "") if isinstance(ch, dict) else ""
        cur = c.execute("INSERT INTO improve_cycles(ts, trigger, status, candidates, tried, verified, "
                        "rejected, note, seconds, baseline, signals, model, charter) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t0, trigger, "running", 0, 0, 0, 0, "mode: world", 0, "{}", "{}",
                         brain.brain_name(), stamp))
        cid = cur.lastrowid
        c.commit()
    finally:
        c.close()
    counts = {"candidates": 0, "tried": 0, "verified": 0, "rejected": 0, "queued": 0}
    note, status = "", "complete"
    try:
        import sim
        w = sim.world()
        _STATE["current"] = f"choosing topics for the {w.get('age')}"
        # a topic read once is not read again; one that could NOT be read, whose model went
        # silent, or whose research a restart cut short is tried again later
        ideas.reap_stale()
        rows_ = ideas.rows(limit=1000)
        already = ideas.skip_topics(rows_)
        tops = ideas.topics(w, already, MAX_TRIES)
        counts["candidates"] = len(tops)
        situation = (f"The settlement is in the {w.get('age')} with food {w.get('food')}, wood {w.get('wood')}, "
                     f"gold {w.get('gold')}; the next leap costs {sim.advance_cost(w.get('age'))}.")
        for topic in tops:
            counts["tried"] += 1
            _STATE["current"] = f"reading about {topic!r} and checking the citation"
            r = ideas.consider(cid, topic, w, situation)
            if r["status"] == "queued":
                counts["verified"] += 1; counts["queued"] += 1
            elif r["status"] in ("unverified", "unread", "model-silent", "unresearched"):
                counts["rejected"] += 1
        note = (f"mode: world — {counts['tried']} topics read, {counts['queued']} sourced and researched "
                f"developments queued for the Board, {counts['rejected']} unread or uncited"
                if tops else ("mode: world — every topic for this age is settled or cooling after a failure; "
                              "nothing new to consider"))
    except Exception as e:                        # noqa: BLE001 — closed, not abandoned
        status, note = "halted", f"{type(e).__name__}: {str(e)[:200]}"
    c = _conn()
    try:
        c.execute("UPDATE improve_cycles SET status=?, candidates=?, tried=?, verified=?, rejected=?, "
                  "note=?, seconds=? WHERE id=?",
                  (status, counts["candidates"], counts["tried"], counts["verified"], counts["rejected"],
                   note, round(time.time() - t0, 1), cid))
        c.commit()
    finally:
        c.close()
    return {"cycle_id": cid, "status": status, "note": note, "seconds": round(time.time() - t0, 1), **counts}


# ── the scheduler ────────────────────────────────────────────────────────────

def enabled() -> bool:
    return os.environ.get("IMPROVE", "1").strip() != "0"


def due(now: float | None = None) -> bool:
    now = now or time.time()
    last = cycles(limit=1)
    return not last or (now - last[0]["ts"]) >= INTERVAL_S


def _loop():
    while True:
        try:
            _STATE["last_tick"] = time.time()
            if enabled() and due() and not _STATE["running"]:
                cycle("scheduled")
        except Exception:                          # noqa: BLE001 — the loop must outlive one bad cycle
            pass
        time.sleep(60)


def reap() -> int:
    """A cycle still marked running at boot was cut off by a restart — a deploy, most
    likely. Marked interrupted, never left looking alive."""
    c = _conn()
    try:
        cur = c.execute("UPDATE improve_cycles SET status='interrupted', "
                        "note='the process restarted during the cycle (a deploy?) — ' || note "
                        "WHERE status='running'")
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def start() -> bool:
    if _STATE["thread"] and _STATE["thread"].is_alive():
        return False
    reap()
    if not enabled():
        return False
    t = threading.Thread(target=_loop, daemon=True, name="phoenix-improve")
    t.start()
    _STATE["thread"] = t
    return True


def _ideas_summary() -> dict:
    import ideas
    return ideas.summary()


def status() -> dict:
    last = cycles(limit=1)
    return {"enabled": enabled(), "running": _STATE["running"], "current": _STATE["current"],
            "thread": bool(_STATE["thread"] and _STATE["thread"].is_alive()),
            "interval_s": INTERVAL_S, "max_tries": MAX_TRIES,
            "last_cycle": last[0] if last else None,
            "next_due_in_s": max(0, int(INTERVAL_S - (time.time() - last[0]["ts"]))) if last else 0,
            "empty_streak": _empty_streak(), "research": research.verify_chain(),
            "ideas": _ideas_summary(),
            "token_required": bool(os.environ.get("CONSOLE_TOKEN", "").strip())}

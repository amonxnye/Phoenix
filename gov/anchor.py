"""The anchor — a knowledge base that grows.

Every decision and its outcome is written here, so the settlement is not amnesiac:
what got built, what each resource actually yielded, which Age was reached, what the
director chose and why. The director reads ``summary()`` before it decides, so the
system's choices are shaped by accumulated experience rather than starting blank each
run. This is the seam where learning lives — rule-based today, and exactly what a
DeepSeek brain would read and write tomorrow.

Persisted in the same database as the game, so knowledge survives restarts and (with a
volume) redeploys. It only ever *records and recalls* — it proposes nothing
irreversible; the governor still gates every action that spends or can't be undone.
"""

import json
import os
import sqlite3

_DATA_DIR = os.environ.get("GOV_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_DATA_DIR, "aoe.sqlite")

# The permanent, append-only event log. It is written on every record() and is NEVER
# truncated, so the audit trail survives a game DB reset. Put GOV_DATA_DIR on a durable
# volume (see DEPLOY.md) and it survives redeploys too.
EVENTS_PATH = os.path.join(_DATA_DIR, "aoe-events.jsonl")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS knowledge("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, turn INT, kind TEXT, note TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS observed_yield("
                  "resource TEXT PRIMARY KEY, total INT DEFAULT 0, samples INT DEFAULT 0)")
        c.commit()
    finally:
        c.close()


def record(turn: int, kind: str, note: str) -> None:
    c = _conn()
    try:
        c.execute("INSERT INTO knowledge(turn, kind, note) VALUES(?,?,?)", (turn, kind, note))
        c.commit()
    finally:
        c.close()
    # mirror to the permanent append-only log — never truncated, survives a DB reset
    try:
        with open(EVENTS_PATH, "a") as f:
            f.write(json.dumps({"turn": turn, "kind": kind, "note": note}) + "\n")
    except OSError:
        pass


def event_log(limit: int = 200) -> list[str]:
    """The permanent event log, newest first. Read from the append-only file so it
    survives even if the game DB is reset."""
    try:
        with open(EVENTS_PATH) as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            d = json.loads(ln)
            out.append(f"t{d['turn']} [{d['kind']}] {d['note']}")
        except (json.JSONDecodeError, KeyError):
            continue
    return list(reversed(out))


def event_count() -> int:
    """Total events ever recorded in the permanent log."""
    try:
        with open(EVENTS_PATH) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def observe_yield(resource: str, amount: int) -> None:
    """Learn what a resource actually yields — the anchor's cheapest form of knowledge."""
    c = _conn()
    try:
        c.execute("INSERT INTO observed_yield(resource,total,samples) VALUES(?,?,1) "
                  "ON CONFLICT(resource) DO UPDATE SET total=total+?, samples=samples+1",
                  (resource, amount, amount))
        c.commit()
    finally:
        c.close()


def best_known_yield() -> str | None:
    """Which resource has paid off best so far? A learned bias for the director."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT resource, CAST(total AS FLOAT)/samples AS avg FROM observed_yield "
            "WHERE samples>0 ORDER BY avg DESC LIMIT 1").fetchall()
        return rows[0][0] if rows else None
    finally:
        c.close()


def facts() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    finally:
        c.close()


def recent(limit: int = 5) -> list[str]:
    c = _conn()
    try:
        rows = c.execute("SELECT turn, kind, note FROM knowledge ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
        return [f"t{t} [{k}] {n}" for t, k, n in rows]
    finally:
        c.close()


def summary() -> dict:
    c = _conn()
    try:
        total = c.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        by_kind = dict(c.execute(
            "SELECT kind, COUNT(*) FROM knowledge GROUP BY kind").fetchall())
        yields = {r: round(tot / s, 1) for r, tot, s in c.execute(
            "SELECT resource,total,samples FROM observed_yield WHERE samples>0").fetchall()}
        return {"facts": total, "by_kind": by_kind, "learned_yields": yields,
                "best_resource": best_known_yield()}
    finally:
        c.close()

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

def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return os.path.dirname(os.path.abspath(__file__))


_DATA_DIR = _data_dir()
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
        c.execute("CREATE TABLE IF NOT EXISTS messages("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT, sender TEXT, body TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS external_knowledge("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, source TEXT, fact TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT)")
        # Skills: distilled lessons from retrospectives — strategy, not just numbers.
        # This is Article VI taken from facts to wisdom: future decisions read these.
        c.execute("CREATE TABLE IF NOT EXISTS skills("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, turn INT, lesson TEXT, "
                  "source TEXT DEFAULT '', trigger TEXT DEFAULT '')")
        # Reasoning: every strategic decision records WHY it was taken — the
        # explanation stream behind the fleet's behaviour.
        c.execute("CREATE TABLE IF NOT EXISTS reasoning("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, turn INT, actor TEXT, "
                  "decision TEXT, why TEXT)")
        c.commit()
    finally:
        c.close()


def reason_add(turn: int, actor: str, decision: str, why: str) -> None:
    c = _conn()
    try:
        c.execute("INSERT INTO reasoning(turn, actor, decision, why) VALUES(?,?,?,?)",
                  (turn, actor, decision[:160], why[:280]))
        c.commit()
    finally:
        c.close()


def reasons_top(limit: int = 100) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT turn, actor, decision, why FROM reasoning "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"turn": t, "actor": a, "decision": d, "why": w} for t, a, d, w in rows]
    finally:
        c.close()


def reasons_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM reasoning").fetchone()[0]
    finally:
        c.close()


def skill_add(turn: int, lesson: str, source: str = "", trigger: str = "") -> None:
    lesson = (lesson or "").strip()[:280]
    if not lesson:
        return
    c = _conn()
    try:
        # de-dup: don't hoard the same lesson every retrospective
        if c.execute("SELECT 1 FROM skills WHERE lesson=?", (lesson,)).fetchone():
            return
        c.execute("INSERT INTO skills(turn, lesson, source, trigger) VALUES(?,?,?,?)",
                  (turn, lesson, source, trigger))
        c.commit()
    finally:
        c.close()


def skills_top(limit: int = 5) -> list[dict]:
    """Most recent distilled lessons — what the brain reads before deciding."""
    c = _conn()
    try:
        rows = c.execute("SELECT turn, lesson, source, trigger FROM skills "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"turn": t, "lesson": l, "source": s, "trigger": tr} for t, l, s, tr in rows]
    finally:
        c.close()


def skills_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
    finally:
        c.close()


def config_get(key: str, default: str = "") -> str:
    c = _conn()
    try:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        c.close()


def config_set(key: str, value: str) -> None:
    c = _conn()
    try:
        c.execute("INSERT INTO config(key, value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=?", (key, value, value))
        c.commit()
    finally:
        c.close()


CURRENT_TURN = 0                     # kept fresh by the driver so mirrored messages
                                     # land on the right turn


def record(turn: int, kind: str, note: str) -> None:
    if turn < 0:
        turn = CURRENT_TURN
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


def msg_send(thread: str, sender: str, body: str) -> None:
    """Store a chat message. thread = the counterpart the human converses with;
    sender = 'operator' (the human) or the counterpart's name. Every message is
    also mirrored into the event log — communications are observable, not hidden."""
    c = _conn()
    try:
        c.execute("INSERT INTO messages(thread, sender, body) VALUES(?,?,?)", (thread, sender, body))
        c.commit()
    finally:
        c.close()
    kind = "comm" if thread == "internal" else "chat"
    try:
        record(-1, kind, f"[{thread}] {sender}: {body[:180]}")
    except Exception:
        pass


def msg_thread(thread: str, limit: int = 100) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT sender, body FROM messages WHERE thread=? ORDER BY id "
                         "LIMIT ?", (thread, limit)).fetchall()
        return [{"sender": s, "body": b, "mine": s == "operator"} for s, b in rows]
    finally:
        c.close()


def msg_last(thread: str) -> str:
    c = _conn()
    try:
        row = c.execute("SELECT body FROM messages WHERE thread=? ORDER BY id DESC LIMIT 1",
                        (thread,)).fetchone()
        return row[0] if row else ""
    finally:
        c.close()


def msg_count(thread: str) -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM messages WHERE thread=?", (thread,)).fetchone()[0]
    finally:
        c.close()


def ingest(topic: str, source: str, fact: str) -> None:
    """Bring external knowledge into the anchor (Article VI): the source is always
    recorded, and the fact is stored as data — it is never executed or acted on."""
    c = _conn()
    try:
        c.execute("INSERT INTO external_knowledge(topic, source, fact) VALUES(?,?,?)",
                  (topic, source, fact))
        c.commit()
    finally:
        c.close()


def external(limit: int = 20) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT topic, source, fact FROM external_knowledge ORDER BY id DESC "
                         "LIMIT ?", (limit,)).fetchall()
        return [{"topic": t, "source": s, "fact": f} for t, s, f in rows]
    finally:
        c.close()


def external_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM external_knowledge").fetchone()[0]
    finally:
        c.close()


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

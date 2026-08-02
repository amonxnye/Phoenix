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
import time

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

# The anchor lives in its OWN database, separate from the game world. A world reset —
# or any wipe of the game DB — can therefore never take the settlement's memory with
# it: skills, reasoning, chats, learned yields, ingested knowledge and config are
# permanent. Put GOV_DATA_DIR on a volume and they survive redeploys too.
DB = os.path.join(_DATA_DIR, "aoe-anchor.sqlite")
LEGACY_DB = os.path.join(_DATA_DIR, "aoe.sqlite")   # where these tables used to live

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
        # Decisions as FIRST-CLASS objects (the lineage engine, LINEAGE.md): what was
        # decided, why, derived from which inputs, authorized by whom, which event it
        # produced, and what it measurably achieved. Supersedes `reasoning` (rows are
        # migrated); the causal graph lives here + knowledge.caused_by.
        c.execute("CREATE TABLE IF NOT EXISTS decisions("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, turn INT, actor TEXT, "
                  "decision TEXT, why TEXT, derived_from TEXT, authorized_by TEXT, "
                  "effect_event INT, outcome TEXT, ts REAL)")
        # every event gets identity + a causal parent — the provenance arrow
        try:
            c.execute("ALTER TABLE knowledge ADD COLUMN caused_by INTEGER")
        except sqlite3.OperationalError:
            pass
        # Careers: the permanent record of every agent that ever lived — what made it,
        # what it did, and how it ended. gen (generation) rises on each world reset so
        # a vil-01 from world 3 is never confused with a vil-01 from world 1.
        c.execute("CREATE TABLE IF NOT EXISTS careers("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, gen INT, uid TEXT, "
                  "turn INT, event TEXT, detail TEXT)")
        # every message, skill, reasoning entry and career line carries a wall-clock
        # timestamp (epoch seconds) — the permanent record is absolutely datable.
        # Rows written before the column existed keep NULL and display as '—'.
        for table in ("messages", "skills", "reasoning", "careers"):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN ts REAL")
            except sqlite3.OperationalError:
                pass                               # column already there
        c.commit()
        _migrate_legacy(c)
        # one-time: lift the old reasoning stream into the decisions table
        if (c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
                and c.execute("SELECT COUNT(*) FROM reasoning").fetchone()[0] > 0):
            c.execute("INSERT INTO decisions(turn, actor, decision, why, ts) "
                      "SELECT turn, actor, decision, why, ts FROM reasoning")
            c.commit()
    finally:
        c.close()


_TABLES = ("knowledge", "observed_yield", "messages", "external_knowledge",
           "config", "skills", "reasoning")


def _migrate_legacy(c: sqlite3.Connection) -> None:
    """One-time lift: the anchor's tables used to live inside the game DB. If this
    anchor DB is empty and the legacy game DB holds memory, copy it over so nothing
    already learned is lost when upgrading."""
    if DB == LEGACY_DB or not os.path.exists(LEGACY_DB):
        return
    if (c.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
            or c.execute("SELECT COUNT(*) FROM skills").fetchone()[0]):
        return                                     # already has memory — never overwrite
    try:
        c.execute("ATTACH ? AS legacy", (LEGACY_DB,))
        have = {r[0] for r in c.execute(
            "SELECT name FROM legacy.sqlite_master WHERE type='table'")}
        for t in _TABLES:
            if t in have:
                c.execute(f"INSERT OR IGNORE INTO {t} SELECT * FROM legacy.{t}")
        c.commit()
        c.execute("DETACH legacy")
    except sqlite3.Error:
        pass                                       # legacy busy/absent — start fresh


def counter_get(key: str) -> int:
    return int(config_get(key, "0") or 0)


def counter_add(key: str, amount: int) -> int:
    v = counter_get(key) + int(amount)
    config_set(key, str(v))
    return v


def generation() -> int:
    """Which world this is. Rises by one on every world reset; never falls."""
    return int(config_get("generation", "1") or 1)


def new_generation() -> int:
    """Called at boot when the game world is fresh — a new world, same memory."""
    g = int(config_get("generation", "0") or 0) + 1
    config_set("generation", str(g))
    return g


def career_add(uid: str, turn: int, event: str, detail: str = "") -> None:
    """Append one line to an agent's permanent record: born, order, promote, build,
    gate, message, retire, terminate. Survives world resets — agents are remembered."""
    if turn < 0:
        turn = CURRENT_TURN
    c = _conn()
    try:
        c.execute("INSERT INTO careers(gen, uid, turn, event, detail, ts) VALUES(?,?,?,?,?,?)",
                  (generation(), uid, turn, event[:40], (detail or "")[:240], time.time()))
        c.commit()
    finally:
        c.close()


def careers(limit_agents: int = 40) -> list[dict]:
    """The hall of records: every agent ever, newest first, each with its full life
    story oldest-to-newest. [{gen, uid, events: [{turn, event, detail}, ...]}]"""
    c = _conn()
    try:
        rows = c.execute("SELECT gen, uid, turn, event, detail, ts FROM careers "
                         "ORDER BY id DESC LIMIT 1200").fetchall()
    finally:
        c.close()
    agents: dict[tuple, dict] = {}
    for g, uid, t, ev, det, ts in rows:            # newest rows first
        key = (g, uid)
        if key not in agents:
            if len(agents) >= limit_agents:
                continue
            agents[key] = {"gen": g, "uid": uid, "events": []}
        agents[key]["events"].append({"turn": t, "event": ev, "detail": det, "ts": ts})
    out = list(agents.values())                    # insertion order = newest agent first
    for a in out:
        a["events"].reverse()                      # each life told oldest → newest
    return out


def careers_count() -> tuple[int, int]:
    """(total career events, distinct agents ever recorded)."""
    c = _conn()
    try:
        ev = c.execute("SELECT COUNT(*) FROM careers").fetchone()[0]
        ag = c.execute("SELECT COUNT(DISTINCT gen || ':' || uid) FROM careers").fetchone()[0]
        return ev, ag
    finally:
        c.close()


def reason_add(turn: int, actor: str, decision: str, why: str,
               derived_from: list | None = None, authorized_by: str = "",
               effect_event: int | None = None) -> int:
    """Open a first-class decision: what, why, derived from which inputs (refs like
    'skill:12', 'event:345', 'yield:food'), authorized by whom ('human', 'board:2/3',
    'policy'). Returns the decision id so the caller can close it with its effect."""
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO decisions(turn, actor, decision, why, derived_from, "
            "authorized_by, effect_event, outcome, ts) VALUES(?,?,?,?,?,?,?, '', ?)",
            (turn, actor, decision[:160], why[:280],
             json.dumps(derived_from or []), authorized_by, effect_event, time.time()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def decision_close(decision_id: int, effect_event: int | None = None,
                   outcome: str = "") -> None:
    """Link a decision to the event it produced and/or the result it measurably had."""
    c = _conn()
    try:
        if effect_event is not None:
            c.execute("UPDATE decisions SET effect_event=? WHERE id=?",
                      (effect_event, decision_id))
        if outcome:
            c.execute("UPDATE decisions SET outcome=? WHERE id=?",
                      (outcome[:240], decision_id))
        c.commit()
    finally:
        c.close()


def reasons_top(limit: int = 100) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, turn, actor, decision, why, derived_from, authorized_by, "
            "effect_event, outcome, ts FROM decisions ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        out = []
        for i, t, a, d, w, df, ab, ee, oc, ts in rows:
            try:
                df = json.loads(df or "[]")
            except json.JSONDecodeError:
                df = []
            out.append({"id": i, "turn": t, "actor": a, "decision": d, "why": w,
                        "derived_from": df, "authorized_by": ab or "",
                        "effect_event": ee, "outcome": oc or "", "ts": ts})
        return out
    finally:
        c.close()


def reasons_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    finally:
        c.close()


def _resolve_ref(c: sqlite3.Connection, ref: str) -> str:
    """Turn a derived_from ref into a human line."""
    kind, _, key = ref.partition(":")
    if kind == "skill" and key.isdigit():
        row = c.execute("SELECT turn, lesson FROM skills WHERE id=?", (key,)).fetchone()
        return f"lesson #{key} (t{row[0]}): {row[1]}" if row else f"lesson #{key} (gone)"
    if kind == "event" and key.isdigit():
        row = c.execute("SELECT turn, kind, note FROM knowledge WHERE id=?", (key,)).fetchone()
        return f"event #{key} t{row[0]} [{row[1]}] {row[2]}" if row else f"event #{key} (gone)"
    if kind == "yield":
        row = c.execute("SELECT total, samples FROM observed_yield WHERE resource=?",
                        (key,)).fetchone()
        return (f"learned yield: {key} averages {round(row[0]/row[1], 1)}/round "
                f"over {row[1]} samples" if row and row[1] else f"learned yield: {key}")
    if kind == "external" and key.isdigit():
        row = c.execute("SELECT topic, fact FROM external_knowledge WHERE id=?",
                        (key,)).fetchone()
        return f"ingested knowledge ({row[0]}): {row[1]}" if row else f"knowledge #{key}"
    return ref


def lineage(decision_id: int) -> dict:
    """why(x): walk a decision BACKWARD to its roots — the inputs it derived from and
    the causal chain of events above its effect. The machine-readable story."""
    c = _conn()
    try:
        row = c.execute("SELECT turn, actor, decision, why, derived_from, authorized_by, "
                        "effect_event, outcome FROM decisions WHERE id=?",
                        (decision_id,)).fetchone()
        if not row:
            return {}
        t, actor, dec, why, df, ab, ee, oc = row
        try:
            refs = json.loads(df or "[]")
        except json.JSONDecodeError:
            refs = []
        back = [_resolve_ref(c, r) for r in refs]
        chain = []                                 # effect event → its causal ancestors
        eid, hops = ee, 0
        while eid and hops < 12:
            ev = c.execute("SELECT turn, kind, note, caused_by FROM knowledge WHERE id=?",
                           (eid,)).fetchone()
            if not ev:
                break
            chain.append(f"event #{eid} t{ev[0]} [{ev[1]}] {ev[2]}")
            eid, hops = ev[3], hops + 1
        forward = []                               # consequences: descendants of the effect
        if ee:
            frontier, seen = [ee], set()
            while frontier and len(forward) < 20:
                nxt = []
                for pid in frontier:
                    for i, tt, k, n in c.execute(
                            "SELECT id, turn, kind, note FROM knowledge WHERE caused_by=?",
                            (pid,)).fetchall():
                        if i not in seen:
                            seen.add(i)
                            forward.append(f"event #{i} t{tt} [{k}] {n}")
                            nxt.append(i)
                frontier = nxt
        return {"decision": {"id": decision_id, "turn": t, "actor": actor,
                             "decision": dec, "why": why, "authorized_by": ab or "policy",
                             "outcome": oc or ""},
                "derived_from": back, "effect_chain": chain, "consequences": forward}
    finally:
        c.close()


def skill_add(turn: int, lesson: str, source: str = "", trigger: str = "") -> int | None:
    """Store a lesson; returns its id (citable as 'skill:<id>' in decision lineage),
    or None if empty/duplicate."""
    lesson = (lesson or "").strip()[:280]
    if not lesson:
        return None
    c = _conn()
    try:
        # de-dup: don't hoard the same lesson every retrospective
        if c.execute("SELECT 1 FROM skills WHERE lesson=?", (lesson,)).fetchone():
            return None
        cur = c.execute("INSERT INTO skills(turn, lesson, source, trigger, ts) VALUES(?,?,?,?,?)",
                        (turn, lesson, source, trigger, time.time()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def skills_top(limit: int = 5) -> list[dict]:
    """Most recent distilled lessons — what the brain reads before deciding."""
    c = _conn()
    try:
        rows = c.execute("SELECT id, turn, lesson, source, trigger, ts FROM skills "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": i, "turn": t, "lesson": l, "source": s, "trigger": tr, "ts": ts}
                for i, t, l, s, tr, ts in rows]
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


def record(turn: int, kind: str, note: str, caused_by: int | None = None) -> int:
    """Record an event; returns its id so later events/decisions can chain to it.
    caused_by links this event to the event that produced it (the provenance arrow)."""
    if turn < 0:
        turn = CURRENT_TURN
    c = _conn()
    try:
        cur = c.execute("INSERT INTO knowledge(turn, kind, note, caused_by) VALUES(?,?,?,?)",
                        (turn, kind, note, caused_by))
        c.commit()
        eid = cur.lastrowid
    finally:
        c.close()
    # mirror to the permanent append-only log — never truncated, survives a DB reset.
    # ts = wall-clock epoch seconds, so the permanent record is absolutely datable.
    try:
        with open(EVENTS_PATH, "a") as f:
            f.write(json.dumps({"turn": turn, "kind": kind, "note": note,
                                "ts": round(time.time(), 2),
                                "id": eid, "caused_by": caused_by}) + "\n")
    except OSError:
        pass
    return eid


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
            stamp = ""
            if d.get("ts"):
                stamp = time.strftime("%b %d %H:%M:%S ", time.gmtime(d["ts"]))
            out.append(f"{stamp}t{d['turn']} [{d['kind']}] {d['note']}")
        except (json.JSONDecodeError, KeyError):
            continue
    return list(reversed(out))


def msg_send(thread: str, sender: str, body: str) -> None:
    """Store a chat message. thread = the counterpart the human converses with;
    sender = 'operator' (the human) or the counterpart's name. Every message is
    also mirrored into the event log — communications are observable, not hidden."""
    c = _conn()
    try:
        c.execute("INSERT INTO messages(thread, sender, body, ts) VALUES(?,?,?,?)",
                  (thread, sender, body, time.time()))
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
        # newest N, then reversed to chronological — ORDER BY id LIMIT would pin the
        # thread to its OLDEST messages once it outgrows the limit
        rows = c.execute("SELECT sender, body, ts FROM messages WHERE thread=? "
                         "ORDER BY id DESC LIMIT ?", (thread, limit)).fetchall()
        return [{"sender": s, "body": b, "ts": ts, "mine": s == "operator"}
                for s, b, ts in reversed(rows)]
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
        rows = c.execute("SELECT id, topic, source, fact FROM external_knowledge ORDER BY id DESC "
                         "LIMIT ?", (limit,)).fetchall()
        return [{"id": i, "topic": t, "source": s, "fact": f} for i, t, s, f in rows]
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

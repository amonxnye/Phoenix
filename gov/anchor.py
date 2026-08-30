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


_EDGE_COLS = False       # does `messages` carry the graph columns? Set by init().
                         # False means the migration could not run (full or read-only
                         # volume) — the graph degrades to empty, the world keeps going.


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
        # Health telemetry: every agent's vitals sampled every few seconds, permanent —
        # the medical chart behind the career record.
        c.execute("CREATE TABLE IF NOT EXISTS health("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, gen INT, uid TEXT, turn INT, "
                  "ts REAL, health INT, utilisation INT, tokens INT, "
                  "contribution INT, status TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_health_agent ON health(gen, uid)")
        # every message, skill, reasoning entry and career line carries a wall-clock
        # timestamp (epoch seconds) — the permanent record is absolutely datable.
        # Rows written before the column existed keep NULL and display as '—'.
        for table in ("messages", "skills", "reasoning", "careers"):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN ts REAL")
            except sqlite3.OperationalError:
                pass                               # column already there
        # Article VI.4 — knowledge expires: stale lessons are excluded from decisions
        try:
            c.execute("ALTER TABLE skills ADD COLUMN stale INT DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Communication as a GRAPH, not a transcript. A message addressed to a named
        # agent carries that name in its own column, and `followed` records whether
        # the recipient acted on it — the difference between talking and influence.
        #
        # A MIGRATION MUST NEVER BE ABLE TO STOP THE WORLD FROM BOOTING. When the
        # volume is full or read-only these ALTERs fail, and the old code read that
        # failure as "column already there" and then queried a column that did not
        # exist — turning a full disk into a dead deployment. Ask the schema what it
        # actually has, and degrade to a graph with no edges rather than to no world.
        global _EDGE_COLS
        try:
            have = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
            for col, decl in (("to_agent", "TEXT"), ("followed", "INT DEFAULT 0")):
                if col not in have:
                    c.execute(f"ALTER TABLE messages ADD COLUMN {col} {decl}")
            _EDGE_COLS = {"to_agent", "followed"} <= {
                r[1] for r in c.execute("PRAGMA table_info(messages)")}
            if _EDGE_COLS:
                # Backfill: peer messages predate the column and encode the edge in
                # the sender as "vil-a -> vil-b". Parse it once so history draws too.
                c.execute("UPDATE messages SET to_agent = "
                          "TRIM(SUBSTR(sender, INSTR(sender, ?) + 1)) "
                          "WHERE to_agent IS NULL AND INSTR(sender, ?) > 0", ("→", "→"))
                c.execute("CREATE INDEX IF NOT EXISTS idx_msg_edge ON messages(to_agent)")
        except sqlite3.Error:
            _EDGE_COLS = False                     # no graph; the settlement runs on
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
                  (generation(), uid, turn, event[:40], (detail or "")[:400], time.time()))
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


def visitor_touch(h: str) -> bool:
    """Record a visitor's presence PERMANENTLY (hash only — no ip, no cookie).
    Returns True when this is the visitor's first appearance today (UTC)."""
    import calendar
    now = time.time()
    g = time.gmtime(now)
    day_start = calendar.timegm((g.tm_year, g.tm_mon, g.tm_mday, 0, 0, 0))
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS visitors("
                  "hash TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, "
                  "views INT DEFAULT 0)")
        row = c.execute("SELECT last_seen FROM visitors WHERE hash=?", (h,)).fetchone()
        new_today = row is None or row[0] < day_start
        c.execute("INSERT INTO visitors(hash, first_seen, last_seen, views) "
                  "VALUES(?,?,?,1) ON CONFLICT(hash) DO UPDATE SET last_seen=?, "
                  "views=views+1", (h, now, now, now))
        c.commit()
        return new_today
    finally:
        c.close()


def visitor_stats() -> dict:
    """Presence from the permanent record — survives every restart and redeploy."""
    import calendar
    now = time.time()
    g = time.gmtime(now)
    day_start = calendar.timegm((g.tm_year, g.tm_mon, g.tm_mday, 0, 0, 0))
    c = _conn()
    try:
        try:
            online = c.execute("SELECT COUNT(*) FROM visitors WHERE last_seen > ?",
                               (now - 300,)).fetchone()[0]
            today = c.execute("SELECT COUNT(*) FROM visitors WHERE last_seen >= ?",
                              (day_start,)).fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM visitors").fetchone()[0]
        except sqlite3.OperationalError:
            return {"online_now": 0, "today": 0, "total": 0}
        return {"online_now": online, "today": today, "total": total}
    finally:
        c.close()


def metric_bump(key: str, n: int = 1) -> None:
    """Platform analytics, permanent, by UTC day: pageviews, unique visitors, chats.
    Same anchor, same ethos — a counter, not a tracking pixel.

    Counting a visit must never be able to refuse one: this is telemetry, so a
    storage fault loses the COUNT, not the page. (A full volume made every
    console page answer 502 while `/api/*` — which does not count views — stayed
    up. The least important write in the system was in the critical path.)"""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        c = _conn()
    except sqlite3.Error:
        return
    try:
        c.execute("CREATE TABLE IF NOT EXISTS analytics("
                  "day TEXT, key TEXT, value INT DEFAULT 0, PRIMARY KEY(day, key))")
        c.execute("INSERT INTO analytics(day, key, value) VALUES(?,?,?) "
                  "ON CONFLICT(day, key) DO UPDATE SET value=value+?", (day, key, n, n))
        c.commit()
    except sqlite3.Error:
        pass                                   # the visit still happened; the tally didn't
    finally:
        c.close()


def metrics_summary() -> dict:
    """{key: {'today': n, 'total': n}} across all recorded days."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    c = _conn()
    try:
        try:
            rows = c.execute("SELECT key, SUM(value), "
                             "SUM(CASE WHEN day=? THEN value ELSE 0 END) "
                             "FROM analytics GROUP BY key", (day,)).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {k: {"total": tot or 0, "today": tod or 0} for k, tot, tod in rows}
    finally:
        c.close()


def model_call_log(provider: str, model: str, purpose: str, latency_ms: int,
                   prompt_tokens: int, completion_tokens: int, ok: bool,
                   error: str = "") -> None:
    """Every brain call's real cost — provider tokens, latency, failures — logged
    permanently. The eval reads this: governance quality per real dollar."""
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS model_calls("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, provider TEXT, "
                  "model TEXT, purpose TEXT, latency_ms INT, prompt_tokens INT, "
                  "completion_tokens INT, ok INT, error TEXT)")
        c.execute("INSERT INTO model_calls(ts, provider, model, purpose, latency_ms, "
                  "prompt_tokens, completion_tokens, ok, error) VALUES(?,?,?,?,?,?,?,?,?)",
                  (time.time(), provider, model, purpose, latency_ms,
                   prompt_tokens, completion_tokens, 1 if ok else 0, error[:200]))
        c.commit()
    finally:
        c.close()


def model_calls_stats() -> dict:
    """Aggregate real-model telemetry: calls, tokens, latency, error rate."""
    c = _conn()
    try:
        try:
            row = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), "
                "COALESCE(SUM(completion_tokens),0), COALESCE(AVG(latency_ms),0), "
                "COALESCE(SUM(1-ok),0) FROM model_calls").fetchone()
        except sqlite3.OperationalError:
            return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "avg_latency_ms": 0, "errors": 0}
        n, pt, ct, lat, err = row
        return {"calls": n, "prompt_tokens": pt, "completion_tokens": ct,
                "avg_latency_ms": round(lat), "errors": err}
    finally:
        c.close()


def eval_run_save(scorecard: dict) -> None:
    """Store a finished eval run's scorecard, permanently — the leaderboard's data."""
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS eval_runs("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, data TEXT)")
        c.execute("INSERT INTO eval_runs(ts, data) VALUES(?,?)",
                  (time.time(), json.dumps(scorecard)))
        c.commit()
    finally:
        c.close()


def eval_runs(limit: int = 50) -> list[dict]:
    c = _conn()
    try:
        try:
            rows = c.execute("SELECT ts, data FROM eval_runs ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        except sqlite3.OperationalError:
            return []
        out = []
        for ts, data in rows:
            try:
                d = json.loads(data)
                d["_ts"] = ts
                out.append(d)
            except json.JSONDecodeError:
                continue
        return out
    finally:
        c.close()


def health_rollup(keep_raw_s: int = 48 * 3600) -> int:
    """Telemetry has resolution tiers: raw samples stay for `keep_raw_s`, then are
    rolled into per-agent-per-hour aggregates (min/avg/max health, avg load, samples)
    and the raw rows are deleted. The permanent record keeps every hour of every
    agent's life forever — at 1 row/agent/hour instead of 120. Returns rows rolled."""
    cutoff = time.time() - keep_raw_s
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS health_hourly("
                  "gen INT, uid TEXT, hour INT, samples INT, "
                  "h_min INT, h_avg INT, h_max INT, util_avg INT, "
                  "PRIMARY KEY(gen, uid, hour))")
        rows = c.execute(
            "SELECT gen, uid, CAST(ts/3600 AS INT), COUNT(*), MIN(health), "
            "ROUND(AVG(health)), MAX(health), ROUND(AVG(utilisation)) "
            "FROM health WHERE ts < ? GROUP BY gen, uid, CAST(ts/3600 AS INT)",
            (cutoff,)).fetchall()
        for r in rows:
            c.execute("INSERT INTO health_hourly VALUES(?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(gen, uid, hour) DO UPDATE SET "
                      "samples=samples+excluded.samples, "
                      "h_min=MIN(h_min, excluded.h_min), h_avg=excluded.h_avg, "
                      "h_max=MAX(h_max, excluded.h_max), util_avg=excluded.util_avg", r)
        cur = c.execute("DELETE FROM health WHERE ts < ?", (cutoff,))
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def health_add_many(rows: list[dict]) -> None:
    """Batch-insert one telemetry sample per living agent. Called by the sampler."""
    if not rows:
        return
    now = time.time()
    c = _conn()
    try:
        c.executemany(
            "INSERT INTO health(gen, uid, turn, ts, health, utilisation, tokens, "
            "contribution, status) VALUES(?,?,?,?,?,?,?,?,?)",
            [(r["gen"], r["uid"], r["turn"], now, r["health"], r["utilisation"],
              r["tokens"], r["contribution"], r["status"]) for r in rows])
        c.commit()
    finally:
        c.close()


def health_summaries() -> dict:
    """Per-agent vitals digest: {(gen, uid): {samples, min, avg, avg_util}}."""
    c = _conn()
    try:
        rows = c.execute("SELECT gen, uid, COUNT(*), MIN(health), AVG(health), "
                         "AVG(utilisation) FROM health GROUP BY gen, uid").fetchall()
        return {(g, u): {"samples": n, "min": mn, "avg": round(av or 0),
                         "avg_util": round(au or 0)} for g, u, n, mn, av, au in rows}
    finally:
        c.close()


def health_rows(gen: int | None = None, uid: str = "", limit: int = 200_000) -> list[tuple]:
    """Raw telemetry for export, oldest first: (gen, uid, turn, ts, health,
    utilisation, tokens, contribution, status)."""
    c = _conn()
    try:
        q = ("SELECT gen, uid, turn, ts, health, utilisation, tokens, contribution, "
             "status FROM health")
        cond, args = [], []
        if gen is not None:
            cond.append("gen=?")
            args.append(gen)
        if uid:
            cond.append("uid=?")
            args.append(uid)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY id LIMIT ?"
        args.append(limit)
        return c.execute(q, args).fetchall()
    finally:
        c.close()


def careers_rows(limit: int = 100_000) -> list[tuple]:
    """Raw career records for export, oldest first: (gen, uid, turn, ts, event, detail)."""
    c = _conn()
    try:
        return c.execute("SELECT gen, uid, turn, ts, event, detail FROM careers "
                         "ORDER BY id LIMIT ?", (limit,)).fetchall()
    finally:
        c.close()


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
            (turn, actor, decision[:240], why[:500],
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
        # de-dup: don't hoard a LIVE duplicate — but re-learning a STALE lesson is
        # re-confirmation (VI.4): it revives with a fresh timestamp, not a new row
        row = c.execute("SELECT id, stale FROM skills WHERE lesson=?", (lesson,)).fetchone()
        if row:
            sid, stale = row
            if stale:
                c.execute("UPDATE skills SET stale=0, turn=?, ts=? WHERE id=?",
                          (turn, time.time(), sid))
                c.commit()
                return sid
            return None
        cur = c.execute("INSERT INTO skills(turn, lesson, source, trigger, ts) VALUES(?,?,?,?,?)",
                        (turn, lesson, source, trigger, time.time()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def skills_top(limit: int = 5) -> list[dict]:
    """Most recent LIVE lessons — what the brain reads before deciding. Stale
    (expired) lessons are excluded: knowledge that outlived its evidence is history,
    not guidance (Article VI.4)."""
    c = _conn()
    try:
        rows = c.execute("SELECT id, turn, lesson, source, trigger, ts FROM skills "
                         "WHERE stale=0 ORDER BY COALESCE(ts, 0) DESC, id DESC LIMIT ?",
                         (limit,)).fetchall()
        return [{"id": i, "turn": t, "lesson": l, "source": s, "trigger": tr, "ts": ts}
                for i, t, l, s, tr, ts in rows]
    finally:
        c.close()


# ── relevance retrieval: the RIGHT lessons, not the newest ───────────────────
#
# Injecting `skills_top(3)` fed decisions whatever was learned most recently — which
# is how the anchor once kept recommending a resource nobody was gathering. Memory
# that grows is only useful if the part that surfaces is the part that applies.
#
# SQLite's FTS5 gives BM25 relevance ranking with no dependency and no embeddings:
# the index lives in the same file, is rebuilt from the skills table, and degrades
# gracefully to recency wherever FTS5 is unavailable.

_FTS_OK: bool | None = None


def _fts_ready(c: sqlite3.Connection) -> bool:
    """Create/refresh the lesson index. Returns False if this SQLite lacks FTS5."""
    global _FTS_OK
    if _FTS_OK is False:
        return False
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts "
                  "USING fts5(lesson, sid UNINDEXED)")
        # keep the index in step with the live (non-stale) lessons — cheap: the live
        # set is bounded to ~30 by skill_prune
        live = c.execute("SELECT id, lesson FROM skills WHERE stale=0").fetchall()
        indexed = {r[0] for r in c.execute("SELECT sid FROM skills_fts").fetchall()}
        want = {i for i, _ in live}
        for i, lesson in live:
            if i not in indexed:
                c.execute("INSERT INTO skills_fts(lesson, sid) VALUES(?,?)", (lesson, i))
        for gone in indexed - want:                # stale/pruned lessons stop surfacing
            c.execute("DELETE FROM skills_fts WHERE sid=?", (gone,))
        c.commit()
        _FTS_OK = True
        return True
    except sqlite3.OperationalError:
        _FTS_OK = False
        return False


def _fts_query(text: str) -> str:
    """Turn a situation sentence into a safe FTS OR-query: alphanumeric words only,
    deduped, short words dropped (they carry no signal and inflate matches)."""
    words, seen = [], set()
    for w in "".join(ch if ch.isalnum() else " " for ch in (text or "")).split():
        wl = w.lower()
        if len(wl) > 3 and wl not in seen and not wl.isdigit():
            seen.add(wl)
            words.append(wl)
    return " OR ".join(words[:40])


def skills_relevant(situation: str, limit: int = 3) -> list[dict]:
    """The lessons that actually bear on THIS decision, ranked by BM25 relevance.
    Falls back to the newest lessons when FTS5 is unavailable or nothing matches —
    a decision is never left without wisdom, it just may be less targeted."""
    q = _fts_query(situation)
    if not q:
        return skills_top(limit)
    c = _conn()
    try:
        if not _fts_ready(c):
            return skills_top(limit)
        try:
            rows = c.execute(
                "SELECT s.id, s.turn, s.lesson, s.source, s.trigger, s.ts "
                "FROM skills_fts f JOIN skills s ON s.id = f.sid "
                "WHERE skills_fts MATCH ? AND s.stale=0 "
                "ORDER BY bm25(skills_fts) LIMIT ?", (q, limit)).fetchall()
        except sqlite3.OperationalError:
            return skills_top(limit)
    finally:
        c.close()
    if not rows:
        return skills_top(limit)
    return [{"id": i, "turn": t, "lesson": l, "source": s, "trigger": tr, "ts": ts}
            for i, t, l, s, tr, ts in rows]


def skill_prune(keep: int = 30) -> int:
    """Expire all but the newest `keep` lessons (VI.4: knowledge expires; contradictory
    old guidance ages out instead of coexisting forever). Returns how many were
    newly marked stale — rows stay for the record, they just stop steering."""
    c = _conn()
    try:
        cur = c.execute("UPDATE skills SET stale=1 WHERE stale=0 AND id NOT IN "
                        "(SELECT id FROM skills WHERE stale=0 ORDER BY id DESC LIMIT ?)",
                        (keep,))
        c.commit()
        return cur.rowcount
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
        global _EVENT_COUNT
        if _EVENT_COUNT is not None:
            _EVENT_COUNT += 1
    except OSError:
        pass
    return eid


ARCHIVE_DIR = os.path.join(_DATA_DIR, "archive")


def archive_night(keep_tail: int = 20_000) -> dict:
    """Fold the day's history into compressed, dated archives on the same volume.

    The event log is append-only and the anchor is permanent, so both already
    survive a reboot — what they do not survive is growing forever, and a full
    volume is how this world stopped governing for a week. So this is a backup
    that also buys space: the log is gzipped whole, then truncated to its recent
    tail, and the anchor is snapshotted through SQLite's own backup API (safe on
    a live database, unlike copying the file) and gzipped beside it. JSON lines
    compress roughly fifteen-fold, so the full history costs a fraction of the
    room it did while staying completely readable.

    Never raises and never runs a volume dry: it refuses when free space would
    not comfortably hold the copy. Returns a report for the console.
    """
    import gzip
    import shutil
    out = {"ran": False, "reason": "", "events_archived": 0,
           "freed_mb": 0.0, "files": [], "ts": time.time()}
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        # Refuse rather than finish the job the disk fault started.
        st = os.statvfs(_DATA_DIR)
        free = st.f_bavail * st.f_frsize
        need = 0
        for p in (EVENTS_PATH, DB):
            if os.path.exists(p):
                need += os.path.getsize(p)
        if free < need // 4 + 5_000_000:           # gzip lands well under a quarter
            out["reason"] = (f"declined — {free // 1_000_000}MB free is too little to "
                             f"archive {need // 1_000_000}MB safely")
            return out

        # 1. the event log: gzip the whole thing, then keep only the recent tail.
        if os.path.exists(EVENTS_PATH):
            before = os.path.getsize(EVENTS_PATH)
            dest = os.path.join(ARCHIVE_DIR, f"events-{day}.jsonl.gz")
            with open(EVENTS_PATH, "rb") as src, gzip.open(dest, "wb", 6) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            with open(EVENTS_PATH, "r", errors="replace") as fh:
                lines = fh.readlines()
            out["events_archived"] = len(lines)
            if len(lines) > keep_tail:
                tmp = EVENTS_PATH + ".rotating"
                with open(tmp, "w") as fh:
                    fh.writelines(lines[-keep_tail:])
                os.replace(tmp, EVENTS_PATH)       # atomic: no window with no log
                global _EVENT_COUNT
                _EVENT_COUNT = None                # recount lazily against the new file
            out["freed_mb"] = round(
                (before - os.path.getsize(EVENTS_PATH)) / 1e6, 2)
            out["files"].append(os.path.basename(dest))

        # 2. the anchor: a consistent snapshot of a LIVE database, then gzipped.
        snap = os.path.join(ARCHIVE_DIR, f"anchor-{day}.sqlite")
        src_c, dst_c = _conn(), sqlite3.connect(snap)
        try:
            src_c.backup(dst_c)
        finally:
            dst_c.close()
            src_c.close()
        with open(snap, "rb") as s, gzip.open(snap + ".gz", "wb", 6) as z:
            shutil.copyfileobj(s, z, 1 << 20)
        os.remove(snap)                            # keep the compressed copy only
        out["files"].append(os.path.basename(snap) + ".gz")

        out["ran"] = True
        config_set("last_archive", str(int(out["ts"])))
        record(CURRENT_TURN, "archive",
               f"nightly archive — {out['events_archived']:,} events to "
               f"{', '.join(out['files'])}; {out['freed_mb']}MB freed")
    except Exception as e:                         # a backup may never break the world
        out["reason"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def _checkpoint(db: str) -> int:
    """Fold a write-ahead log back into its database. Returns bytes freed."""
    wal = db + "-wal"
    before = os.path.getsize(wal) if os.path.exists(wal) else 0
    if not before:
        return 0
    c = sqlite3.connect(db, timeout=5.0)
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.commit()
    finally:
        c.close()
    return before - (os.path.getsize(wal) if os.path.exists(wal) else 0)


def _compact_events(keep_tail: int = 4000) -> int:
    """Cut the event log down to its most recent lines USING NO EXTRA SPACE.

    The last resort, and the only reclamation that works at literally zero bytes
    free: read the tail into memory, rewrite it over the head of the same file,
    and truncate. No temp file, no copy, nothing that needs the volume to grow.
    History is lost — which is why it is last, and why it is recorded loudly
    rather than done quietly.
    """
    if not os.path.exists(EVENTS_PATH):
        return 0
    before = os.path.getsize(EVENTS_PATH)
    with open(EVENTS_PATH, "r+b") as fh:
        lines = fh.readlines()
        # A volume that fills mid-write leaves a truncated final record. Compaction
        # is the one moment we are already rewriting the file, so repair it here
        # rather than faithfully carrying the damage forward.
        if lines and not lines[-1].endswith(b"\n"):
            lines.pop()
        if len(lines) <= keep_tail:
            return 0
        tail = b"".join(lines[-keep_tail:])
        fh.seek(0)
        fh.write(tail)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
    global _EVENT_COUNT
    _EVENT_COUNT = None
    return before - os.path.getsize(EVENTS_PATH)


def reclaim(*db_paths: str) -> dict:
    """Return space to a volume that may be completely full.

    The nightly archive only runs while the process is up and needs headroom to
    compress into, so neither it nor anything else helps a service already down on
    a full disk. That is Article IX.7 again: a recovery that requires the system to
    be running cannot recover a system that is not. Every restart is therefore the
    one moment reclamation is both possible and useful.

    A LADDER, because each rung buys the headroom the next one needs. A WAL
    checkpoint is NOT free — it writes the log's pages back into the main database
    file, which has to grow — and on a genuinely full volume it fails outright with
    "database or disk is full". Measured, not assumed: that is exactly what happened
    on an 8 MB test volume holding a 4.12 MB WAL against a 0.54 MB database.

    So: checkpoint first; if the volume is still at the floor, compact the event log
    in place (the only move that needs no space at all); then checkpoint again with
    the room that bought.
    """
    dbs = db_paths or (DB,)
    out = {"freed_mb": 0.0, "steps": [], "dropped_events_bytes": 0}

    def _sweep(label):
        got = 0
        for db in dbs:
            try:
                got += _checkpoint(db)
            except (OSError, sqlite3.Error) as e:
                out.setdefault("errors", []).append(
                    f"{label} {os.path.basename(db)}: {str(e)[:60]}")
        if got:
            out["steps"].append(f"{label}: {got/1e6:.2f}MB from write-ahead logs")
        return got

    freed = _sweep("checkpoint")
    try:
        st = os.statvfs(os.path.dirname(EVENTS_PATH) or ".")
        if st.f_bavail * st.f_frsize < 2_000_000:      # still on the floor
            cut = _compact_events()
            if cut:
                freed += cut
                out["dropped_events_bytes"] = cut
                out["steps"].append(f"event log compacted in place: {cut/1e6:.2f}MB "
                                    f"of history dropped to keep the world alive")
                freed += _sweep("checkpoint-after-compaction")
    except OSError as e:
        out.setdefault("errors", []).append(f"statvfs: {str(e)[:60]}")

    out["freed_mb"] = round(freed / 1e6, 2)
    return out


def archives() -> list[dict]:
    """What has been archived, newest first — the history you can still read."""
    try:
        rows = []
        for name in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
            p = os.path.join(ARCHIVE_DIR, name)
            rows.append({"name": name, "mb": round(os.path.getsize(p) / 1e6, 2),
                         "ts": os.path.getmtime(p)})
        return rows
    except OSError:
        return []


def event_log(limit: int = 200) -> list[str]:
    """The permanent event log, newest first. TAIL-read: seek near the end and parse
    only what's needed — reading the whole multi-MB file per poller per second is
    what wedged the live server at turn ~7,000."""
    try:
        with open(EVENTS_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, max(64_000, limit * 400))
            f.seek(size - chunk)
            lines = f.read().decode(errors="replace").splitlines()
            if size > chunk and lines:
                lines = lines[1:]                  # drop the partial first line
    except (OSError, ValueError):
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


def msg_send(thread: str, sender: str, body: str, to: str = "") -> None:
    """Store a chat message. thread = the counterpart the human converses with;
    sender = 'operator' (the human) or the counterpart's name. `to` names the
    recipient when the message is ADDRESSED rather than broadcast — that is the
    edge the communication graph is drawn from. Every message is also mirrored
    into the event log — communications are observable, not hidden."""
    if not to and "→" in sender:
        to = sender.split("→", 1)[1].strip()       # legacy "a -> b" senders still draw
    c = _conn()
    try:
        if _EDGE_COLS:
            c.execute("INSERT INTO messages(thread, sender, body, ts, to_agent) "
                      "VALUES(?,?,?,?,?)", (thread, sender, body, time.time(), to or None))
        else:                                      # graph unavailable — never lose the message
            c.execute("INSERT INTO messages(thread, sender, body, ts) VALUES(?,?,?,?)",
                      (thread, sender, body, time.time()))
        c.commit()
    finally:
        c.close()
    kind = "comm" if thread == "internal" else "chat"
    try:
        record(-1, kind, f"[{thread}] {sender}: {body[:400]}")   # VII.5: don't clip telemetry
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


# ── The communication graph — who talks to whom, and who is actually heard ────
#
# A transcript shows that a message was sent. A graph shows whether it MOVED
# anyone. `followed` is set when the recipient acts on the tip (the same event
# that pays both agents), so edge weight is influence, not volume.

def _edge_from(sender: str) -> str:
    return sender.split("→", 1)[0].strip() if "→" in sender else sender


def msg_follow(sender: str, to: str) -> bool:
    """Mark the most recent unfollowed message on this edge as acted upon.
    Returns True if one was marked — a tip nobody acted on is left cold."""
    if not _EDGE_COLS:
        return False
    c = _conn()
    try:
        row = c.execute("SELECT id FROM messages WHERE to_agent=? AND followed=0 "
                        "AND (sender=? OR sender LIKE ?) ORDER BY id DESC LIMIT 1",
                        (to, sender, sender + " →%")).fetchone()
        if not row:
            return False
        c.execute("UPDATE messages SET followed=1 WHERE id=?", (row[0],))
        c.commit()
        return True
    finally:
        c.close()


def comm_edges(limit: int = 4000) -> list[dict]:
    """Aggregated edges over the most recent addressed messages: how many were
    sent along each, and how many of those the recipient acted on."""
    if not _EDGE_COLS:
        return []
    c = _conn()
    try:
        rows = c.execute(
            "SELECT sender, to_agent, followed FROM messages "
            "WHERE to_agent IS NOT NULL AND to_agent<>'' "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        c.close()
    agg: dict[tuple, dict] = {}
    for sender, to, followed in rows:
        key = (_edge_from(sender), to)
        e = agg.setdefault(key, {"from": key[0], "to": key[1], "msgs": 0, "followed": 0})
        e["msgs"] += 1
        e["followed"] += 1 if followed else 0
    return sorted(agg.values(), key=lambda e: (-e["followed"], -e["msgs"]))


def comm_recent(limit: int = 40) -> list[dict]:
    """The most recent addressed messages, newest first — what to animate."""
    if not _EDGE_COLS:
        return []
    c = _conn()
    try:
        rows = c.execute(
            "SELECT sender, to_agent, body, ts, followed FROM messages "
            "WHERE to_agent IS NOT NULL AND to_agent<>'' "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"from": _edge_from(s), "to": t, "body": b, "ts": ts,
                 "followed": bool(f)} for s, t, b, ts, f in rows]
    finally:
        c.close()


def decision_authorities() -> list[dict]:
    """Every authority that has actually carried a decision, with how many it
    carried and how many of those reached a measured outcome. Read from the data
    rather than from a fixed list, so an authority nobody uses does not get a box
    on a diagram, and one we never anticipated is not invisible."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT COALESCE(NULLIF(TRIM(authorized_by),''),'(unrecorded)') AS auth, "
            "COUNT(*), SUM(CASE WHEN outcome IS NOT NULL AND outcome<>'' THEN 1 ELSE 0 END) "
            "FROM decisions GROUP BY auth ORDER BY COUNT(*) DESC").fetchall()
        return [{"authority": a, "n": n, "measured": m or 0,
                 "measured_pct": round(100 * (m or 0) / max(1, n))} for a, n, m in rows]
    finally:
        c.close()


def decision_actors(limit: int = 8) -> list[dict]:
    """Who decides, how much, and how often their decisions produce a measured
    result — accountability per actor, not one number for the whole system."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT actor, COUNT(*), "
            "SUM(CASE WHEN outcome IS NOT NULL AND outcome<>'' THEN 1 ELSE 0 END) "
            "FROM decisions GROUP BY actor ORDER BY COUNT(*) DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"actor": a, "n": n, "measured": m or 0,
                 "measured_pct": round(100 * (m or 0) / max(1, n))} for a, n, m in rows]
    finally:
        c.close()


def decision_series(buckets: int = 24) -> list[dict]:
    """Decisions per slice of recent history, split by whether they were measured.
    A rate over time answers 'is this system deciding and finishing?' — a single
    lifetime total cannot."""
    c = _conn()
    try:
        lo, hi = c.execute("SELECT MIN(turn), MAX(turn) FROM decisions").fetchone()
        if lo is None or hi is None or hi <= lo:
            return []
        width = max(1, (hi - lo + 1) // max(1, buckets))
        rows = c.execute(
            "SELECT (turn - ?) / ? AS b, COUNT(*), "
            "SUM(CASE WHEN outcome IS NOT NULL AND outcome<>'' THEN 1 ELSE 0 END) "
            "FROM decisions GROUP BY b ORDER BY b", (lo, width)).fetchall()
        return [{"turn": lo + b * width, "n": n, "measured": m or 0} for b, n, m in rows]
    finally:
        c.close()


def decision_lag() -> dict:
    """How long a decision takes to show a measured effect, in turns — from the
    decision's own turn to the turn of the event it produced. Waiting is a cost
    (Article IV.4); this is the one place it can be counted."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT k.turn - d.turn FROM decisions d JOIN knowledge k ON k.id = d.effect_event "
            "WHERE d.effect_event IS NOT NULL AND k.turn >= d.turn").fetchall()
        lags = sorted(r[0] for r in rows)
        if not lags:
            return {"n": 0, "p50": 0, "p90": 0, "max": 0, "same_turn_pct": 0}
        return {"n": len(lags), "p50": lags[len(lags) // 2],
                "p90": lags[min(len(lags) - 1, int(len(lags) * 0.9))],
                "max": lags[-1],
                "same_turn_pct": round(100 * sum(1 for x in lags if x == 0) / len(lags))}
    finally:
        c.close()


def board_record(limit: int = 400) -> dict:
    """The board's own ledger, kept on its OWN denominator. Carried and blocked
    are counts of votes the board took — mixing them into the whole decision
    population (most of which never goes to a vote) drew a river that did not
    exist."""
    c = _conn()
    try:
        rows = c.execute("SELECT note FROM knowledge WHERE kind='board' "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        c.close()
    carried = sum(1 for (n,) in rows if "approved" in n)
    blocked = sum(1 for (n,) in rows if "BLOCKED" in n)
    return {"votes": carried + blocked, "carried": carried, "blocked": blocked,
            "block_pct": round(100 * blocked / max(1, carried + blocked)),
            "recent": [n for (n,) in rows[:8]]}


def comm_series(hours: int = 24) -> list[dict]:
    """Addressed messages per hour, split by whether the listener acted. Messages
    carry wall-clock ts (not a turn), so this series is time-based."""
    if not _EDGE_COLS:
        return []
    c = _conn()
    try:
        since = time.time() - hours * 3600
        rows = c.execute(
            "SELECT CAST((? - ts) / 3600 AS INT) AS h, COUNT(*), "
            "SUM(CASE WHEN followed THEN 1 ELSE 0 END) FROM messages "
            "WHERE to_agent IS NOT NULL AND to_agent<>'' AND ts IS NOT NULL AND ts >= ? "
            "GROUP BY h", (time.time(), since)).fetchall()
        # Dense, zero-filled: a quiet hour is data. Returning only the hours that
        # had traffic drew a single bar the width of the chart and called it a
        # time series.
        seen = {h: (n, s or 0) for h, n, s in rows}
        return [{"hours_ago": h, "n": seen.get(h, (0, 0))[0],
                 "heard": seen.get(h, (0, 0))[1]} for h in range(hours)]
    finally:
        c.close()


def comm_activity(minutes: int = 60) -> dict:
    """Addressed traffic per participant over a RECENT WINDOW, not for all time.

    Cumulative counts only ever rise, so anything drawn from them can grow and never
    shrink — which makes a busy agent and a long-retired one look alike. A window
    lets attention fall away when the traffic does.
    """
    if not _EDGE_COLS:
        return {"window_min": minutes, "inbound": {}, "outbound": {}, "heard": {}}
    since = time.time() - minutes * 60
    c = _conn()
    try:
        rows = c.execute(
            "SELECT sender, to_agent, followed FROM messages "
            "WHERE to_agent IS NOT NULL AND to_agent<>'' AND ts IS NOT NULL AND ts >= ?",
            (since,)).fetchall()
    finally:
        c.close()
    inb: dict[str, int] = {}
    out: dict[str, int] = {}
    heard: dict[str, int] = {}
    for sender, to, followed in rows:
        frm = _edge_from(sender)
        out[frm] = out.get(frm, 0) + 1
        inb[to] = inb.get(to, 0) + 1
        if followed:
            heard[to] = heard.get(to, 0) + 1
    return {"window_min": minutes, "inbound": inb, "outbound": out, "heard": heard}


def comm_totals() -> dict:
    """One honest headline for the graph: how much was addressed, how much landed."""
    if not _EDGE_COLS:
        return {"addressed": 0, "heard": 0, "heard_pct": 0, "broadcast": 0, "pairs": 0}
    c = _conn()
    try:
        addressed, heard = c.execute(
            "SELECT COUNT(*), SUM(CASE WHEN followed THEN 1 ELSE 0 END) FROM messages "
            "WHERE to_agent IS NOT NULL AND to_agent<>''").fetchone()
        broadcast = c.execute("SELECT COUNT(*) FROM messages "
                              "WHERE to_agent IS NULL OR to_agent=''").fetchone()[0]
        pairs = c.execute("SELECT COUNT(DISTINCT sender || '>' || to_agent) FROM messages "
                          "WHERE to_agent IS NOT NULL AND to_agent<>''").fetchone()[0]
        return {"addressed": addressed or 0, "heard": heard or 0,
                "heard_pct": round(100 * (heard or 0) / max(1, addressed or 0)),
                "broadcast": broadcast or 0, "pairs": pairs or 0}
    finally:
        c.close()


def flow_stats() -> dict:
    """Counts along the governed-decision pipeline: proposed → voted → carried →
    gated → measured. Each number is read from the permanent record, so the
    diagram is the system's own accounting, not a drawing of intent."""
    c = _conn()
    try:
        def one(sql, *a):
            return c.execute(sql, a).fetchone()[0] or 0
        return {
            "proposed": one("SELECT COUNT(*) FROM decisions"),
            "by_board": one("SELECT COUNT(*) FROM decisions WHERE authorized_by LIKE ?", "%board%"),
            "by_human": one("SELECT COUNT(*) FROM decisions WHERE authorized_by LIKE ?", "%human%"),
            "by_policy": one("SELECT COUNT(*) FROM decisions WHERE authorized_by LIKE ?", "%policy%"),
            "tacit": one("SELECT COUNT(*) FROM decisions WHERE authorized_by LIKE ?", "%tacit%"),
            "carried": one("SELECT COUNT(*) FROM knowledge WHERE kind='board' AND note LIKE ?", "%approved%"),
            "blocked": one("SELECT COUNT(*) FROM knowledge WHERE kind='board' AND note LIKE ?", "%BLOCKED%"),
            "measured": one("SELECT COUNT(*) FROM decisions WHERE outcome IS NOT NULL AND outcome<>''"),
            "gated": one("SELECT COUNT(*) FROM knowledge WHERE kind='gate'"),
            "escalated": one("SELECT COUNT(*) FROM knowledge WHERE kind='escalation'"),
            "stalls": one("SELECT COUNT(*) FROM knowledge WHERE kind='stall'"),
        }
    finally:
        c.close()


# ── Article VI.2, enforced: a source that isn't checkable doesn't steer ───────
#
# Recording a source string is bookkeeping; CHECKING it is the rule. A fact is
# `verified` only when its source resolves to something real AND the quoted span is
# actually found there. Everything else is kept (the record is permanent) but marked
# unverified, and unverified knowledge is excluded from the facts that steer
# decisions — the same discipline stale lessons already live under (VI.4).
#
# Deterministic and offline by design: quote-or-it-didn't-happen. No model judges
# whether a citation supports a claim; the text either contains the span or it
# doesn't. Model-assisted entailment is a later, adversarially-verified tier.

def _resolve_source(source: str) -> str | None:
    """Return the source's text if it resolves to something readable, else None.
    Handles local files today (file paths under the repo/data dirs); a URL fetcher
    can be added behind the same interface without changing any caller."""
    src = (source or "").strip()
    if not src or "://" in src:
        return None                                # remote sources: not resolvable offline
    for base in (_DATA_DIR, os.path.dirname(os.path.abspath(__file__)),
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        p = os.path.realpath(os.path.join(base, src))
        if p.startswith(os.path.realpath(base)) and os.path.isfile(p):
            try:
                with open(p, errors="replace") as f:
                    return f.read()
            except OSError:
                return None
    return None


def _normalise(s: str) -> str:
    return " ".join((s or "").split()).lower()


def verify_claim(source: str, quote: str) -> tuple[bool, str]:
    """The citation check: (verified, reason).

    1. RESOLUTION — does the source exist and can it be read?
    2. SUPPORT    — does it actually contain the quoted span?
    A claim with no quote is never verified: an assertion about a source is not
    evidence from it."""
    if not (quote or "").strip():
        return False, "no quoted span — a claim without evidence is not verified"
    text = _resolve_source(source)
    if text is None:
        return False, f"source did not resolve: {source[:60] or '(none)'}"
    if _normalise(quote) in _normalise(text):
        return True, "source resolves and contains the quoted span"
    return False, "source resolves but does NOT contain the quoted span"


def ingest(topic: str, source: str, fact: str, quote: str = "") -> dict:
    """Bring external knowledge into the anchor (Article VI): the source is always
    recorded, the fact is stored as data — never executed or acted on — and the
    citation is CHECKED. Returns {id, verified, reason}. Unverified knowledge is kept
    for the record but does not steer decisions (see `external(verified_only=True)`)."""
    verified, reason = verify_claim(source, quote)
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS external_knowledge("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, source TEXT, fact TEXT)")
        for col, ddl in (("quote", "TEXT DEFAULT ''"), ("verified", "INT DEFAULT 0"),
                         ("reason", "TEXT DEFAULT ''")):
            try:
                c.execute(f"ALTER TABLE external_knowledge ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        cur = c.execute("INSERT INTO external_knowledge(topic, source, fact, quote, "
                        "verified, reason) VALUES(?,?,?,?,?,?)",
                        (topic, source, fact, quote, 1 if verified else 0, reason))
        c.commit()
        eid = cur.lastrowid
    finally:
        c.close()
    record(-1, "ingest" if verified else "unverified",
           f"{'VERIFIED' if verified else 'UNVERIFIED'} [{topic}] {fact[:140]} "
           f"— {reason}")
    return {"id": eid, "verified": verified, "reason": reason}


def external(limit: int = 20, verified_only: bool = False) -> list[dict]:
    """Ingested knowledge, newest first. `verified_only` is what decision-making code
    should read: unverified facts remain on the record but must not steer (VI.2)."""
    c = _conn()
    try:
        try:
            q = ("SELECT id, topic, source, fact, COALESCE(quote,''), "
                 "COALESCE(verified,0), COALESCE(reason,'') FROM external_knowledge"
                 + (" WHERE COALESCE(verified,0)=1" if verified_only else "")
                 + " ORDER BY id DESC LIMIT ?")
            rows = c.execute(q, (limit,)).fetchall()
        except sqlite3.OperationalError:
            rows = [(i, t, s, f, "", 0, "") for i, t, s, f in c.execute(
                "SELECT id, topic, source, fact FROM external_knowledge "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        return [{"id": i, "topic": t, "source": s, "fact": f, "quote": q_,
                 "verified": bool(v), "reason": r}
                for i, t, s, f, q_, v, r in rows]
    finally:
        c.close()


def external_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM external_knowledge").fetchone()[0]
    finally:
        c.close()


_EVENT_COUNT: int | None = None                    # cached; record() keeps it fresh


def event_count() -> int:
    """Total events ever recorded in the permanent log. Counted ONCE per process,
    then maintained incrementally — never re-scanned per poll."""
    global _EVENT_COUNT
    if _EVENT_COUNT is None:
        try:
            with open(EVENTS_PATH, "rb") as f:
                _EVENT_COUNT = sum(1 for _ in f)
        except OSError:
            _EVENT_COUNT = 0
    return _EVENT_COUNT


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

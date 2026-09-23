"""The operator's view of everything — /admin. Article VII: nothing runs unseen.

Readouts only, apart from three operator actions that the console gates:
  * an EFFICIENCY REVIEW — the world looks at how its own compute is spent and writes
    what it sees, and one innovation it proposes, into the innovation journal;
  * a full EXPORT — every table of every database and the live event log, as one
    JSON document (the nightly archives are listed; they are gzipped copies on disk);
  * a FULL RESET — exports first, then wipes every database and restarts. Guarded by
    ADMIN_TOKEN (never open to the public, even when the console token is unset).

The innovation journal is the world's lab notebook: ideas read from outside, research
entries, civic designs and projects, improvement cycles, lessons learned, free-will
choices, efficiency reviews — one timeline, exportable as Markdown to write papers from.
Its own entries (efficiency reviews, operator notes) are append-only.
"""

import json
import os
import re
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import anchor                                    # noqa: E402
import brain                                     # noqa: E402
import economy                                   # noqa: E402
import sim                                       # noqa: E402

BOOT_TS = time.time()
BAD_KINDS = ("error", "escalation", "waste", "unverified", "improve-rejected", "improve-refused",
             "improve-unresearched", "civic-budget", "cap", "decay", "idea-unread", "reap")
BAD_OUTCOME = re.compile(r"fail|reject|block|refus|waste|halt|denied|stale|over budget", re.I)
EFFICIENCY_TOKENS_PER_DAY = int(os.environ.get("EFFICIENCY_TOKENS_PER_DAY", "50000"))
ASK = None                                       # replaceable by the suite: (prompt) -> str


def _q(sql: str, args=(), db: str | None = None) -> list[tuple]:
    c = sqlite3.connect(db or anchor.DB, timeout=5.0)
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def _size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def databases() -> dict:
    """Every database the world keeps, by name."""
    out = {"world": sim.DB, "anchor": anchor.DB}
    try:
        import workspace
        out["workspace"] = workspace.DB
    except Exception:                              # noqa: BLE001
        pass
    try:
        from mechanic import store
        out["mechanic"] = store.DB()
    except Exception:                              # noqa: BLE001
        pass
    return out


# ── systems ──────────────────────────────────────────────────────────────────

def systems(live: dict | None = None) -> dict:
    live = live or {}
    now = time.time()
    w = sim.world()
    h1 = _q("SELECT COUNT(*), COALESCE(SUM(1-ok),0), COALESCE(AVG(latency_ms),0) FROM model_calls WHERE ts>=?",
            (now - 3600,))
    calls, errs, lat = (h1[0] if h1 else (0, 0, 0))
    out = {
        "process": {"uptime_s": int(now - BOOT_TS), "pid": os.getpid(), "python": sys.version.split()[0],
                    "commit": (os.environ.get("PHOENIX_COMMIT") or os.environ.get("SOURCE_COMMIT", ""))[:12]},
        "world": {"age": sim.canonical_age(w["age"]), "food": w["food"], "wood": w["wood"], "gold": w["gold"],
                  "pop_cap": w["pop_cap"], "turn": live.get("turn"), "agents": live.get("agents"),
                  "goal_met": live.get("goal_met"), "difficulty": sim.difficulty()},
        "brain": {"model": brain.brain_name(), "calls_1h": calls, "errors_1h": errs,
                  "avg_latency_ms_1h": int(lat or 0)},
        "utopia": sim.utopia_state(),
    }
    try:
        import netretry
        out["network"] = {"stats": dict(netretry.STATS), "breakers": netretry.breakers()}
    except Exception as e:                        # noqa: BLE001
        out["network"] = {"error": str(e)[:120]}
    try:
        import improve
        s = improve.status()
        out["improve"] = {k: s.get(k) for k in ("enabled", "running", "current", "mode", "interval_s", "parked",
                                                "empty_streak", "isolation", "github")}
        out["improve"]["last_cycle"] = {k: (s.get("last_cycle") or {}).get(k) for k in ("ts", "status", "note")}
        out["research"] = s.get("research")
    except Exception as e:                        # noqa: BLE001
        out["improve"] = {"error": str(e)[:120]}
    try:
        from mechanic import store, watch
        out["mechanic"] = {"summary": store.summary(), "watch": watch.status()}
    except Exception as e:                        # noqa: BLE001
        out["mechanic"] = {"error": str(e)[:120]}
    dd = anchor._DATA_DIR
    try:
        du = shutil.disk_usage(dd)
        disk = {"total": du.total, "used": du.used, "free": du.free, "used_pct": round(100 * du.used / du.total, 1)}
    except OSError:
        disk = {}
    out["storage"] = {"data_dir": dd, "disk": disk,
                      "files": {k: _size(p) for k, p in databases().items()} | {"events_log": _size(anchor.EVENTS_PATH)}}
    return out


# ── compute: how tokens are used, over time, and to what effect ──────────────

def compute(hours: int = 48) -> dict:
    now = time.time()
    since = now - hours * 3600
    rows = _q("SELECT CAST(ts/3600 AS INT)*3600, purpose, COUNT(*), COALESCE(SUM(1-ok),0), "
              "COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), COALESCE(AVG(latency_ms),0), "
              "COALESCE(SUM(CASE WHEN ok=0 THEN prompt_tokens ELSE 0 END),0) "
              "FROM model_calls WHERE ts>=? GROUP BY 1, 2", (since,))
    think = {r[0]: (r[1] or 0, r[2] or 0) for r in _q(
        "SELECT CAST(ts/3600 AS INT)*3600, SUM(reasoning_chars), SUM(content_chars) FROM model_calls "
        "WHERE ts>=? GROUP BY 1", (since,))} if _has_col("model_calls", "reasoning_chars") else {}
    hourly: dict = {}
    purposes: dict = {}
    for h, purpose, n, err, pt, ct, lat, wasted in rows:
        e = hourly.setdefault(h, {"hour": h, "calls": 0, "errors": 0, "prompt": 0, "completion": 0,
                                  "wasted_prompt": 0, "agent_compute": 0, "contribution": 0})
        e["calls"] += n; e["errors"] += err; e["prompt"] += pt; e["completion"] += ct; e["wasted_prompt"] += wasted
        p = purposes.setdefault(purpose or "?", {"purpose": purpose or "?", "calls": 0, "errors": 0,
                                                 "tokens": 0, "wasted": 0, "avg_latency_ms": 0, "_lat": 0})
        p["calls"] += n; p["errors"] += err; p["tokens"] += pt + ct; p["wasted"] += wasted; p["_lat"] += lat * n
    # the simulated compute the agents burn, and the contribution it bought, per hour
    for h, uid, dtok, dcon in _q(
            "SELECT CAST(ts/3600 AS INT)*3600, uid, MAX(tokens)-MIN(tokens), MAX(contribution)-MIN(contribution) "
            "FROM health WHERE ts>=? GROUP BY 1, 2", (since,)):
        e = hourly.setdefault(h, {"hour": h, "calls": 0, "errors": 0, "prompt": 0, "completion": 0,
                                  "wasted_prompt": 0, "agent_compute": 0, "contribution": 0})
        e["agent_compute"] += max(0, dtok or 0); e["contribution"] += max(0, dcon or 0)
    for h, e in hourly.items():
        rc, cc = think.get(h, (0, 0))
        e["thinking_share"] = round(rc / (rc + cc), 3) if (rc + cc) else 0.0
        e["model_tokens"] = e["prompt"] + e["completion"]
        e["contribution_per_1k_compute"] = round(1000 * e["contribution"] / e["agent_compute"], 1) if e["agent_compute"] else 0.0
    total_model = sum(p["tokens"] for p in purposes.values())
    for p in purposes.values():
        p["share_pct"] = round(100 * p["tokens"] / total_model, 1) if total_model else 0.0
        p["avg_latency_ms"] = int(p.pop("_lat") / p["calls"]) if p["calls"] else 0
    outcomes = {
        "research_entries": _count("SELECT COUNT(*) FROM research WHERE ts>=?", since),
        "ideas_queued": _count("SELECT COUNT(*) FROM ideas WHERE ts>=? AND status IN ('queued','voting','adopted')", since),
        "developments_adopted": _count("SELECT COUNT(*) FROM decisions WHERE ts>=? AND outcome LIKE '%adopt%'", since),
        "civic_works": sum(1 for d in sim.custom_devs() if d["kind"] == "utopia"),
    }
    produced = max(1, outcomes["research_entries"] + outcomes["ideas_queued"] + outcomes["developments_adopted"])
    agent_c = sum(e["agent_compute"] for e in hourly.values())
    contrib = sum(e["contribution"] for e in hourly.values())
    wasted = sum(p["wasted"] for p in purposes.values())
    return {"hours": hours, "hourly": [hourly[k] for k in sorted(hourly)],
            "purposes": sorted(purposes.values(), key=lambda p: -p["tokens"]),
            "totals": {"model_tokens": total_model, "wasted_tokens": wasted,
                       "waste_pct": round(100 * wasted / total_model, 1) if total_model else 0.0,
                       "agent_compute": agent_c, "contribution": contrib,
                       "contribution_per_1k_compute": round(1000 * contrib / agent_c, 1) if agent_c else 0.0,
                       "model_tokens_per_outcome": int(total_model / produced),
                       "lifetime_agent_compute": anchor.counter_get("lifetime_spend")},
            "outcomes": outcomes}


def _count(sql: str, since: float) -> int:
    r = _q(sql, (since,))
    return int(r[0][0]) if r else 0


def _has_col(table: str, col: str) -> bool:
    return any(r[1] == col for r in _q(f"PRAGMA table_info({table})"))


# ── actors: who produces, who wastes, who disrupts, which decisions went wrong ──

def actors(hours: int = 168) -> dict:
    since = time.time() - hours * 3600
    roster = {r["agent"]: r for r in economy.roster(alive_only=False)}
    ev = {}
    for uid, event, n in _q("SELECT uid, event, COUNT(*) FROM careers GROUP BY uid, event"):
        ev.setdefault(uid, {})[event] = n
    last_tok = {uid: tok for uid, tok in _q(
        "SELECT uid, MAX(tokens) FROM health WHERE ts>=? GROUP BY uid", (since,))}
    agents = []
    for uid, r in roster.items():
        e = ev.get(uid, {})
        tok = last_tok.get(uid) or 0
        agents.append({"agent": uid, "rank": r["role"], "alive": r["alive"], "contribution": r["contribution"],
                       "season_compute": tok,
                       "per_1k": round(1000 * r["contribution"] / tok, 1) if tok else None,
                       "renewals": e.get("renewed", 0) + e.get("revived", 0), "free_will": e.get("free-will", 0),
                       "projects": e.get("project", 0), "mentored": e.get("mentored", 0), "mentor": e.get("mentor", 0),
                       "promotions": e.get("promote", 0)})
    effs = sorted(a["per_1k"] for a in agents if a["per_1k"] is not None)
    median = effs[len(effs) // 2] if effs else 0
    for a in agents:
        flags = []
        if a["alive"] and a["per_1k"] is not None and median and a["per_1k"] < median / 4:
            flags.append("low efficiency (under a quarter of the fleet median)")
        if a["alive"] and a["contribution"] == 0 and a["season_compute"]:
            flags.append("spends compute, contributes nothing")
        if a["renewals"] >= 5:
            flags.append(f"revived/renewed {a['renewals']}× — its threads keep ending")
        a["flags"] = flags
    kinds = ",".join("?" * len(BAD_KINDS))
    incidents = _q(f"SELECT id, turn, kind, note FROM knowledge WHERE kind IN ({kinds}) ORDER BY id DESC LIMIT 400",
                   BAD_KINDS)
    by_source: dict = {}
    for _, _, kind, note in incidents:
        m = re.match(r"\s*((?:vil|herald|dev)-[\w-]+|#\d+|[A-Za-z][\w./:-]*)", note or "")
        src = m.group(1) if m else "?"
        s = by_source.setdefault(src, {"source": src, "incidents": 0, "kinds": {}})
        s["incidents"] += 1; s["kinds"][kind] = s["kinds"].get(kind, 0) + 1
    bad_dec = [{"id": i, "turn": t, "actor": a, "decision": d, "why": (w or "")[:240], "outcome": o,
                "authorized_by": ab, "ts": ts}
               for i, t, a, d, w, o, ab, ts in _q(
                   "SELECT id, turn, actor, decision, why, outcome, authorized_by, ts FROM decisions "
                   "WHERE outcome IS NOT NULL AND outcome != '' ORDER BY id DESC LIMIT 1500")
               if BAD_OUTCOME.search(o or "")][:100]
    humans = {"operator_actions": _count("SELECT COUNT(*) FROM knowledge WHERE kind='operator' AND id>?", -1),
              "visitors": (_q("SELECT COUNT(*) FROM visitors") or [(0,)])[0][0],
              "public_chats": sum(v for (v,) in _q("SELECT value FROM analytics WHERE key='public_chats'"))}
    return {"agents": sorted(agents, key=lambda a: (not a["flags"], -(a["contribution"] or 0)))[:200],
            "fleet_median_per_1k": median,
            "chaos": sorted(by_source.values(), key=lambda s: -s["incidents"])[:30],
            "incidents": [{"id": i, "turn": t, "kind": k, "note": (n or "")[:240]} for i, t, k, n in incidents[:80]],
            "questionable_decisions": bad_dec, "humans": humans,
            "note": ("Enforcement is not built in: if the world needs watchmen, the agents may propose it — "
                     "an order-quality civic work, a development, or an amendment — and it goes to the Board and you.")}


# ── the innovation journal ───────────────────────────────────────────────────

def _jconn():
    c = anchor._conn()
    c.execute("CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, "
              "source TEXT, title TEXT, body TEXT, refs TEXT)")
    return c


def note(kind: str, source: str, title: str, body: str, refs: str = "") -> int:
    """Append a thought to the journal. There is no edit and no delete."""
    c = _jconn()
    try:
        cur = c.execute("INSERT INTO journal(ts, kind, source, title, body, refs) VALUES(?,?,?,?,?,?)",
                        (time.time(), kind, source, title[:200], body[:4000], refs[:400]))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def journal(limit: int = 400) -> list[dict]:
    """One timeline of every thought the world has recorded about improving itself."""
    out = []
    c = _jconn(); c.close()
    for i, ts, kind, src, title, body, refs in _q(
            "SELECT id, ts, kind, source, title, body, refs FROM journal ORDER BY id DESC LIMIT ?", (limit,)):
        out.append({"ts": ts, "kind": kind, "source": src, "title": title, "body": body, "ref": refs or f"J{i}"})
    for i, ts, topic, title, source, status, note_, prop in _q(
            "SELECT id, ts, topic, title, source, status, note, proposal FROM ideas ORDER BY id DESC LIMIT ?", (limit,)):
        try:
            p = json.loads(prop or "null") or {}
        except ValueError:
            p = {}
        out.append({"ts": ts, "kind": "idea", "source": source or "outside", "title": f"{topic} → {title or 'unread'}",
                    "body": (f"proposed {p.get('name')}: {p.get('why', '')}. " if p else "") + f"status {status}. {note_ or ''}",
                    "ref": f"idea {i}"})
    for i, ts, pid, title, file, body in _q(
            "SELECT id, ts, proposal_id, title, file, body FROM research ORDER BY id DESC LIMIT ?", (limit,)):
        try:
            b = json.loads(body or "{}")
        except ValueError:
            b = {}
        out.append({"ts": ts, "kind": "research", "source": file, "title": title,
                    "body": f"Advantage: {b.get('advantage', '')} Risk: {b.get('risk', '')} Confidence: {b.get('confidence', '')}",
                    "ref": f"R{i}"})
    for i, ts, trig, status, note_, model in _q(
            "SELECT id, ts, trigger, status, note, model FROM improve_cycles ORDER BY id DESC LIMIT ?", (limit // 4,)):
        out.append({"ts": ts, "kind": "improvement-cycle", "source": model or "", "title": f"cycle {i} ({trig}, {status})",
                    "body": note_ or "", "ref": f"cycle {i}"})
    for i, ts, actor, dec, why, outcome in _q(
            "SELECT id, ts, actor, decision, why, outcome FROM decisions WHERE decision LIKE 'propose development%' "
            "ORDER BY id DESC LIMIT ?", (limit // 2,)):
        out.append({"ts": ts, "kind": "proposal", "source": actor, "title": dec, "body": f"{why} → {outcome or 'pending'}",
                    "ref": f"D{i}"})
    for i, turn, lesson, source in _q(
            "SELECT id, turn, lesson, source FROM skills ORDER BY id DESC LIMIT ?", (limit // 4,)):
        out.append({"ts": 0, "kind": "lesson", "source": source or "retrospective", "title": f"lesson (turn {turn})",
                    "body": lesson, "ref": f"S{i}"})
    for i, turn, note_ in _q("SELECT id, turn, note FROM knowledge WHERE kind='free-will' ORDER BY id DESC LIMIT 60"):
        out.append({"ts": 0, "kind": "free-will", "source": (note_ or "").split(" ")[0], "title": f"a free choice (turn {turn})",
                    "body": note_, "ref": f"E{i}"})
    out.sort(key=lambda e: -(e["ts"] or 0))
    return out[:limit]


def journal_markdown() -> str:
    """The journal as a document to write papers from: grouped by kind, oldest first,
    every entry with its date, source and reference."""
    entries = journal(limit=5000)
    kinds: dict = {}
    for e in entries:
        kinds.setdefault(e["kind"], []).append(e)
    lines = ["# Phoenix — Innovation Journal", "",
             f"Exported {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. Every thought the world recorded "
             "about improving itself: ideas read from outside, research, proposals and civic designs, improvement "
             "cycles, lessons, free choices and efficiency reviews. Each entry cites its record.", ""]
    order = ["efficiency", "idea", "research", "proposal", "improvement-cycle", "lesson", "free-will", "note"]
    for k in order + [k for k in kinds if k not in order]:
        if k not in kinds:
            continue
        lines += [f"## {k.replace('-', ' ').title()} ({len(kinds[k])})", ""]
        for e in sorted(kinds[k], key=lambda e: e["ts"] or 0):
            when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["ts"])) if e["ts"] else "—"
            lines += [f"### {e['title']}", f"*{when} · {e['source']} · {e['ref']}*", "", str(e["body"] or ""), ""]
    return "\n".join(lines)


# ── the world reviews its own compute ────────────────────────────────────────

def _efficiency_spent_today() -> int:
    r = _q("SELECT COALESCE(SUM(prompt_tokens + completion_tokens),0) FROM model_calls "
           "WHERE purpose='efficiency-review' AND ts>=?", (time.time() - 86400,))
    return int(r[0][0]) if r else 0


def efficiency_review() -> dict:
    """Measure how compute was spent in the last day, state what the numbers say, and
    propose ONE innovation to spend it better. Rules speak from the measurements; the
    brain, within its own daily budget, adds an interpretation. Written to the journal."""
    c = compute(24)
    t = c["totals"]
    obs = []
    top = c["purposes"][:3]
    if top:
        obs.append("Most model tokens went to " + ", ".join(f"{p['purpose']} ({p['share_pct']}%)" for p in top) + ".")
    ts = [h["thinking_share"] for h in c["hourly"] if h["model_tokens"]]
    if ts and sum(ts) / len(ts) > 0.5:
        obs.append(f"On average {round(100 * sum(ts) / len(ts))}% of generated text was hidden reasoning — a "
                   "non-thinking model would cut latency and tokens for these structured prompts.")
    if t["waste_pct"] > 5:
        obs.append(f"{t['waste_pct']}% of model tokens went to calls that failed.")
    slow = [p for p in c["purposes"] if p["avg_latency_ms"] > 30000]
    if slow:
        obs.append("Slow purposes (over 30 s a call): " + ", ".join(p["purpose"] for p in slow[:4]) + ".")
    if t["agent_compute"]:
        obs.append(f"Agents bought {t['contribution_per_1k_compute']} contribution per 1,000 compute.")
    obs.append(f"Each research entry, queued idea or adopted development cost about {t['model_tokens_per_outcome']:,} model tokens.")
    innovation, how = "", "rules"
    if brain.available() or ASK is not None:
        if _efficiency_spent_today() < EFFICIENCY_TOKENS_PER_DAY:
            prompt = ("Role: the Phoenix world's efficiency analyst. From these measurements of how the world spent "
                      "its compute in the last 24 hours, propose ONE concrete innovation that would get more value per "
                      "token, and say how its effect would be measured.\n"
                      f"MEASUREMENTS: {json.dumps({'totals': t, 'purposes': c['purposes'][:8], 'outcomes': c['outcomes']})[:3500]}\n"
                      'Reply with ONLY JSON: {"observation": str, "innovation": str, "measure": str}')
            try:
                out = ASK(prompt) if ASK is not None else brain._chat(
                    [{"role": "user", "content": prompt}], 700, 0.4, "efficiency-review")
                m = re.search(r"\{.*\}", out or "", re.S)
                d = json.loads(m.group(0)) if m else {}
                if d.get("innovation"):
                    obs.append(str(d.get("observation", ""))[:600])
                    innovation = f"{d['innovation'][:800]} — measured by: {str(d.get('measure', ''))[:300]}"
                    how = "rules + brain"
            except Exception as e:                # noqa: BLE001 — the rules' reading stands
                how = f"rules (brain failed: {type(e).__name__})"
        else:
            how = "rules (efficiency budget spent for today)"
    if not innovation:
        innovation = ("Route the highest-share purpose to a faster, non-thinking model and compare contribution per "
                      "1,000 compute before and after." if ts and sum(ts) / len(ts) > 0.5 else
                      "Cache repeated prompts for the highest-share purpose and measure tokens per outcome weekly.")
    body = " ".join(o for o in obs if o) + f"\n\nInnovation: {innovation}"
    jid = note("efficiency", how, f"efficiency review — {t['model_tokens']:,} model tokens, "
                                  f"{t['agent_compute']:,} agent compute in 24 h", body)
    return {"journal_id": jid, "observations": obs, "innovation": innovation, "how": how, "totals": t}


# ── export and reset ─────────────────────────────────────────────────────────

def export_all(path: str) -> dict:
    """Every table of every database, and the permanent event log, into one JSON file.
    Written incrementally so a large world never has to fit in memory twice."""
    counts = {}
    with open(path, "w", encoding="utf-8") as f:
        try:
            arch = sorted(os.listdir(anchor.ARCHIVE_DIR))
        except OSError:
            arch = []
        f.write('{"exported_at": %s, "archives": %s, "databases": {' % (
            json.dumps(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), json.dumps(arch)))
        first_db = True
        for name, db in databases().items():
            if not os.path.exists(db):
                continue
            f.write(("" if first_db else ",") + json.dumps(name) + ": {")
            first_db = False
            c = sqlite3.connect(db, timeout=10.0)
            try:
                tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                  "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                for ti, tname in enumerate(tables):
                    cur = c.execute(f'SELECT * FROM "{tname}"')
                    cols = [d[0] for d in cur.description]
                    f.write(("," if ti else "") + json.dumps(tname) + ": {\"columns\": " + json.dumps(cols) + ", \"rows\": [")
                    n = 0
                    for row in cur:
                        vals = [v.hex() if isinstance(v, (bytes, bytearray)) else v for v in row]
                        f.write(("," if n else "") + json.dumps(vals, default=str))
                        n += 1
                    f.write("]}")
                    counts[f"{name}.{tname}"] = n
            finally:
                c.close()
            f.write("}")
        f.write('}, "events": [')
        n = 0
        try:
            with open(anchor.EVENTS_PATH, encoding="utf-8", errors="replace") as ev:
                for line in ev:
                    line = line.strip()
                    if line.startswith("{"):
                        f.write(("," if n else "") + line)
                        n += 1
        except OSError:
            pass
        counts["events"] = n
        f.write("]}")
    return {"path": path, "bytes": _size(path), "counts": counts}


def full_reset() -> dict:
    """Export everything, then delete every database and the event log (the nightly
    archives are moved into the exports folder, not deleted). The caller restarts the
    process. The export is the undo."""
    d = os.path.join(anchor._DATA_DIR, "exports")
    os.makedirs(d, exist_ok=True)
    snap = export_all(os.path.join(d, time.strftime("world-%Y%m%d-%H%M%S.json", time.gmtime())))
    removed = []
    if os.path.isdir(anchor.ARCHIVE_DIR):          # the nightly archives move beside the export
        dest = os.path.join(d, "archive-" + os.path.basename(snap["path"])[6:-5])
        try:
            shutil.move(anchor.ARCHIVE_DIR, dest)
            removed.append("archive/ → " + os.path.relpath(dest, anchor._DATA_DIR))
        except OSError:
            pass
    for p in list(databases().values()) + [anchor.EVENTS_PATH]:
        for q in (p, p + "-wal", p + "-shm"):
            try:
                os.remove(q)
                removed.append(os.path.basename(q))
            except OSError:
                pass
    return {"export": snap["path"], "export_bytes": snap["bytes"], "removed": removed}

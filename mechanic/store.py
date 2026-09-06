"""The record — many repositories, many runs, and everything that was decided.

Multi-repo from the first line, because the product is a platform that watches a
fleet of projects, not a script pointed at one. A repo is registered once; every
analysis of it is a run; every run's findings, refusals and decisions persist.

Append-only where it matters: a dropped finding and the reason it was dropped are kept,
because the record of what was REJECTED is a large part of what a compliance reader is
buying (SRS §13). Nothing here is ever edited in place except a finding's upstream
status, which is news from the outside world rather than a revision of our own claim.

Serial IDs follow the house `ripa-` convention (SRS §15).
"""

import json
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Where the fleet's record lives. In order: MECHANIC_DATA_DIR; the settlement's
    mounted volume (GOV_DATA_DIR/mechanic), so a redeploy does not wipe the history —
    the first production run came back as ripa-run-0001 twice, because it had; then
    the package directory, for a local checkout with nothing configured."""
    for d in (os.environ.get("MECHANIC_DATA_DIR", "").strip(),
              os.path.join(os.environ.get("GOV_DATA_DIR", "").strip() or "\x00", "mechanic")):
        if d and not d.startswith("\x00"):
            try:
                os.makedirs(d, exist_ok=True)
                if os.access(d, os.W_OK):
                    return d
            except OSError:
                continue                          # an unwritable volume falls through
    d = os.path.join(_HERE, "data")
    os.makedirs(d, exist_ok=True)
    return d


def DB() -> str:
    return os.path.join(data_dir(), "mechanic.sqlite")


def boot_marker() -> dict:
    """{since, boots}: written at first sight, incremented per process. If `since` is
    always "just now" and `boots` is always 1, the record is not surviving deploys —
    whatever the environment variables say."""
    p = os.path.join(data_dir(), "record.json")
    try:
        with open(p) as f:
            m = json.load(f)
    except (OSError, json.JSONDecodeError):
        m = {"since": time.time(), "boots": 0}
    if not _BOOTED.get("done"):
        m["boots"] = int(m.get("boots", 0)) + 1
        _BOOTED["done"] = True
        try:
            with open(p, "w") as f:
                json.dump(m, f)
        except OSError:
            pass
    return {"since": float(m.get("since", 0)), "boots": int(m.get("boots", 1))}


_BOOTED: dict = {}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB(), timeout=10.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS repos(
            id TEXT PRIMARY KEY, url TEXT, name TEXT, local_path TEXT,
            languages TEXT, loc INT DEFAULT 0, added_at REAL, notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY, repo_id TEXT, commit_sha TEXT, charter TEXT,
            started_at REAL, finished_at REAL, status TEXT DEFAULT 'queued',
            budget_cents INT DEFAULT 0, spend_cents INT DEFAULT 0,
            unit_count INT DEFAULT 0, symbol_count INT DEFAULT 0, note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS units(
            id TEXT PRIMARY KEY, run_id TEXT, module TEXT, file TEXT, loc INT,
            symbols INT, centrality INT, dynamic TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS findings(
            id TEXT PRIMARY KEY, run_id TEXT, repo_id TEXT, unit_id TEXT,
            category TEXT, severity TEXT, confidence REAL, basis TEXT,
            title TEXT, description TEXT, recommendation TEXT, evidence TEXT,
            disclosure TEXT DEFAULT 'public', upstream TEXT DEFAULT 'open',
            created_at REAL);
        CREATE TABLE IF NOT EXISTS gaps(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, scope TEXT,
            reason TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, finding_id TEXT,
            stage TEXT, actor TEXT, action TEXT, rationale TEXT,
            model TEXT DEFAULT '', charter TEXT DEFAULT '', ts REAL);
        CREATE INDEX IF NOT EXISTS idx_find_run ON findings(run_id);
        CREATE INDEX IF NOT EXISTS idx_find_repo ON findings(repo_id);
        CREATE INDEX IF NOT EXISTS idx_gap_run ON gaps(run_id);
        CREATE INDEX IF NOT EXISTS idx_dec_run ON decisions(run_id);
        """)
        # Columns added for the swarm and the watch. Migrated with the same discipline
        # the settlement learned the hard way: ask the schema what it has, never assume,
        # and a migration that cannot run costs the FEATURE, never the record.
        global _EXT
        try:
            _EXT = True
            for table, cols in _EXTRA.items():
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols:
                    if col not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                _EXT = _EXT and all(col in have for col, _ in cols)
        except sqlite3.Error:
            _EXT = False
        c.commit()
    finally:
        c.close()


_EXT = False
_EXTRA = {
    "repos": [("watch", "INT DEFAULT 0"), ("interval_s", "INT DEFAULT 21600"),
              ("last_sha", "TEXT DEFAULT ''"), ("last_checked", "REAL DEFAULT 0"),
              ("halts", "INT DEFAULT 0")],
    "runs": [("trigger", "TEXT DEFAULT 'manual'"), ("kill_rate", "REAL DEFAULT 0"),
             ("stage_costs", "TEXT DEFAULT ''")],
    "findings": [("fingerprint", "TEXT DEFAULT ''"), ("proposed_by", "TEXT DEFAULT ''"),
                 ("challenge", "TEXT DEFAULT ''"), ("challenge_reason", "TEXT DEFAULT ''"),
                 ("rank", "INT DEFAULT 0"), ("first_seen_run", "TEXT DEFAULT ''"),
                 ("seen_runs", "INT DEFAULT 1"), ("patch", "TEXT DEFAULT ''"),
                 ("patch_status", "TEXT DEFAULT ''"), ("patch_note", "TEXT DEFAULT ''"),
                 ("assessment", "TEXT DEFAULT ''")],
}


def reap_open_runs() -> int:
    """At boot: any run still 'indexing' or 'analysing' belonged to a process that no
    longer exists. Close it as halted, with the reason. A run nobody will ever close
    is the same defect as an unanswered request (Constitution IX)."""
    c = _conn()
    try:
        cur = c.execute("UPDATE runs SET status='halted', finished_at=?, "
                        "note='interrupted by a restart before it finished' "
                        "WHERE status IN ('indexing','analysing','reviewing','adjudicating')",
                        (time.time(),))
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def _next_id(c: sqlite3.Connection, table: str, prefix: str) -> str:
    n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
    while c.execute(f"SELECT 1 FROM {table} WHERE id=?", (f"{prefix}{n:04d}",)).fetchone():
        n += 1
    return f"{prefix}{n:04d}"


# ── repositories: the fleet under watch ──────────────────────────────────────

def repo_add(url: str, name: str = "", local_path: str = "") -> str:
    """Register a repository. Idempotent on url — re-adding returns the same id, so a
    scheduled sweep can declare its fleet every time without duplicating it."""
    c = _conn()
    try:
        row = c.execute("SELECT id FROM repos WHERE url=?", (url,)).fetchone()
        if row:
            if local_path:
                c.execute("UPDATE repos SET local_path=? WHERE id=?", (local_path, row[0]))
                c.commit()
            return row[0]
        rid = _next_id(c, "repos", "ripa-repo-")
        c.execute("INSERT INTO repos(id, url, name, local_path, added_at) VALUES(?,?,?,?,?)",
                  (rid, url, name or url.rstrip("/").split("/")[-1], local_path, time.time()))
        c.commit()
        return rid
    finally:
        c.close()


def repo_set(repo_id: str, **fields) -> None:
    if not fields:
        return
    c = _conn()
    try:
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE repos SET {cols} WHERE id=?", (*fields.values(), repo_id))
        c.commit()
    finally:
        c.close()


def repos() -> list[dict]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM repos ORDER BY name")]
    finally:
        c.close()


# ── runs ─────────────────────────────────────────────────────────────────────

def run_open(repo_id: str, commit_sha: str, charter: str, budget_cents: int = 0,
             trigger: str = "manual") -> str:
    c = _conn()
    try:
        rid = _next_id(c, "runs", "ripa-run-")
        if _EXT:
            c.execute("INSERT INTO runs(id, repo_id, commit_sha, charter, started_at, "
                      "status, budget_cents, trigger) VALUES(?,?,?,?,?,'indexing',?,?)",
                      (rid, repo_id, commit_sha, charter, time.time(), budget_cents, trigger))
        else:
            c.execute("INSERT INTO runs(id, repo_id, commit_sha, charter, started_at, "
                      "status, budget_cents) VALUES(?,?,?,?,?,'indexing',?)",
                      (rid, repo_id, commit_sha, charter, time.time(), budget_cents))
        c.commit()
        return rid
    finally:
        c.close()


def run_set(run_id: str, **fields) -> None:
    c = _conn()
    try:
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        c.commit()
    finally:
        c.close()


def run_close(run_id: str, status: str = "complete", note: str = "") -> None:
    run_set(run_id, status=status, finished_at=time.time(), note=note)


def runs(repo_id: str = "", limit: int = 50) -> list[dict]:
    c = _conn()
    try:
        q = "SELECT * FROM runs"
        args: list = []
        if repo_id:
            q += " WHERE repo_id=?"
            args.append(repo_id)
        return [dict(r) for r in c.execute(q + " ORDER BY started_at DESC LIMIT ?",
                                           (*args, limit))]
    finally:
        c.close()


def run(run_id: str) -> dict:
    c = _conn()
    try:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else {}
    finally:
        c.close()


# ── units ────────────────────────────────────────────────────────────────────

def units_add(run_id: str, units: list[dict]) -> list[str]:
    c = _conn()
    try:
        ids = []
        n = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        for i, u in enumerate(units, start=n + 1):
            uid = f"ripa-unt-{i:05d}"
            c.execute("INSERT OR REPLACE INTO units(id, run_id, module, file, loc, "
                      "symbols, centrality, dynamic) VALUES(?,?,?,?,?,?,?,?)",
                      (uid, run_id, u["module"], u.get("file", ""), u.get("loc", 0),
                       u.get("symbols", 0), u.get("centrality", 0), u.get("dynamic", "")))
            ids.append(uid)
        c.commit()
        return ids
    finally:
        c.close()


def units(run_id: str) -> list[dict]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM units WHERE run_id=? ORDER BY centrality DESC", (run_id,))]
    finally:
        c.close()


# ── findings: named, evidenced, and carrying a proposed fix ──────────────────

def finding_add(run_id: str, repo_id: str, f: dict) -> str:
    """Store one finding. `evidence` is a list of {file, line_range, reason} — the
    Charter admits nothing without it (§2), so this refuses an empty one rather than
    letting an unevidenced claim into the record."""
    if not f.get("evidence"):
        raise ValueError(f"finding {f.get('title')!r} has no evidence — inadmissible "
                         f"under Charter §2")
    c = _conn()
    try:
        fid = _next_id(c, "findings", "ripa-fnd-")
        cols = ["id", "run_id", "repo_id", "unit_id", "category", "severity", "confidence",
                "basis", "title", "description", "recommendation", "evidence", "disclosure",
                "created_at"]
        vals = [fid, run_id, repo_id, f.get("unit_id", ""), f["category"], f["severity"],
                float(f.get("confidence", 1.0)), f.get("basis", "judged"), f["title"],
                f.get("description", ""), f.get("recommendation", ""),
                json.dumps(f["evidence"]), f.get("disclosure", "public"), time.time()]
        if _EXT:
            cols += ["fingerprint", "proposed_by", "challenge", "challenge_reason", "rank",
                     "first_seen_run", "seen_runs", "assessment"]
            vals += [f.get("fingerprint", ""), f.get("proposed_by", ""),
                     f.get("challenge", ""), f.get("challenge_reason", ""),
                     int(f.get("rank") or 0), f.get("first_seen_run", ""),
                     int(f.get("seen_runs") or 1), f.get("assessment", "")]
        c.execute(f"INSERT INTO findings({','.join(cols)}) VALUES({','.join('?' * len(vals))})",
                  vals)
        c.commit()
        return fid
    finally:
        c.close()


_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def findings(run_id: str = "", repo_id: str = "", basis: str = "",
             limit: int = 500) -> list[dict]:
    """Findings, machine-verified first and then by severity — the order the Charter
    requires (§3), not the order they were produced."""
    c = _conn()
    try:
        q, args = "SELECT * FROM findings WHERE 1=1", []
        for col, val in (("run_id", run_id), ("repo_id", repo_id), ("basis", basis)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        rows = [dict(r) for r in c.execute(q + " LIMIT ?", (*args, limit))]
    finally:
        c.close()
    for r in rows:
        try:
            r["evidence"] = json.loads(r["evidence"] or "[]")
        except json.JSONDecodeError:
            r["evidence"] = []
    rows.sort(key=lambda r: (0 if r["basis"] == "machine-verified" else 1,
                             _SEV.get(r["severity"], 9), -r["confidence"]))
    return rows


def finding_set(finding_id: str, **fields) -> None:
    """The one in-place edit the record allows: news from outside (upstream status) and
    the cycle bookkeeping the watch keeps. Removed once as unreachable — correctly, it
    had no caller then. The watch's carry-over is its caller now."""
    if not fields:
        return
    c = _conn()
    try:
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE findings SET {cols} WHERE id=?", (*fields.values(), finding_id))
        c.commit()
    finally:
        c.close()


# ── refusals and decisions ───────────────────────────────────────────────────

def gap_add(run_id: str, scope: str, reason: str) -> None:
    """A refusal: something the analysis declined to conclude, and why. Charter §6 —
    gaps are visible rather than silent, so a reader knows the limits of the report."""
    c = _conn()
    try:
        c.execute("INSERT INTO gaps(run_id, scope, reason, created_at) VALUES(?,?,?,?)",
                  (run_id, scope, reason, time.time()))
        c.commit()
    finally:
        c.close()


def gaps(run_id: str = "", limit: int = 500) -> list[dict]:
    c = _conn()
    try:
        q, args = "SELECT * FROM gaps", []
        if run_id:
            q += " WHERE run_id=?"
            args.append(run_id)
        return [dict(r) for r in c.execute(q + " ORDER BY id LIMIT ?", (*args, limit))]
    finally:
        c.close()


def decision_add(run_id: str, stage: str, actor: str, action: str, rationale: str,
                 finding_id: str = "", model: str = "", charter: str = "") -> None:
    """Append-only. Never edited, never deleted (SRS §15.3)."""
    c = _conn()
    try:
        c.execute("INSERT INTO decisions(run_id, finding_id, stage, actor, action, "
                  "rationale, model, charter, ts) VALUES(?,?,?,?,?,?,?,?,?)",
                  (run_id, finding_id, stage, actor, action, rationale, model, charter,
                   time.time()))
        c.commit()
    finally:
        c.close()


def decisions(run_id: str = "", limit: int = 500) -> list[dict]:
    c = _conn()
    try:
        q, args = "SELECT * FROM decisions", []
        if run_id:
            q += " WHERE run_id=?"
            args.append(run_id)
        return [dict(r) for r in c.execute(q + " ORDER BY id LIMIT ?", (*args, limit))]
    finally:
        c.close()


def summary() -> dict:
    """Fleet-wide headline for the landing page."""
    c = _conn()
    try:
        one = lambda q, *a: c.execute(q, a).fetchone()[0]          # noqa: E731
        return {
            "repos": one("SELECT COUNT(*) FROM repos"),
            "runs": one("SELECT COUNT(*) FROM runs"),
            "findings": one("SELECT COUNT(*) FROM findings"),
            "machine_verified": one("SELECT COUNT(*) FROM findings WHERE basis=?",
                                    "machine-verified"),
            "gaps": one("SELECT COUNT(*) FROM gaps"),
            "fixed": one("SELECT COUNT(*) FROM findings WHERE upstream=?", "fixed"),
            "judged": one("SELECT COUNT(*) FROM findings WHERE basis=?", "judged"),
            "spend_cents": one("SELECT COALESCE(SUM(spend_cents),0) FROM runs"),
            "watched": one("SELECT COUNT(*) FROM repos WHERE watch=1") if _EXT else 0,
            "slop": one("SELECT COUNT(*) FROM findings WHERE category=?", "slop"),
            "patches": one("SELECT COUNT(*) FROM findings WHERE patch_status=?",
                           "applies-and-parses") if _EXT else 0,
        }
    finally:
        c.close()

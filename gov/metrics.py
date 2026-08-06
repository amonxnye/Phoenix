"""What the agents actually did — the record the claims are checked against.

Everything else in this system is a rule about what agents may do. This is the
ledger of what they did do: one row per sortie, written whether the attempt was kept
or thrown away, and one row per run. Failures are the point. A store that only kept
successes would make every fleet look brilliant and would answer none of the
questions worth asking — which strategy is worth its tokens, whether attempt four
ever pays, what a rejected patch costs on average.

Two properties this store must have to be worth trusting:

  * It is written by the caller that ran the oracle, never by the agent. An agent
    cannot report its own outcome here any more than it can at the gate.
  * It records the oracle's verdict, not a description of it. `kept` means the suite
    improved and nothing regressed, because that is the only thing that sets it.

Aggregation is deliberately plain SQL over a small table. A performance number you
cannot re-derive by hand is a number you cannot defend.
"""

import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# outcomes, ordered worst to best — the vocabulary is closed on purpose so that
# aggregation never has to guess what a free-text status meant
REFUSED = "refused"                  # a protected path: the exam is read-only
NO_VERDICT = "no-verdict"            # the suite stopped reporting; not payable
REGRESSION = "regression"            # something that passed now fails
NO_CHANGE = "no-change"              # nothing the oracle can see
ERROR = "executor-error"             # the solver itself failed
PARTIAL = "partial"                  # kept, but the target test is still red
KEPT = "kept"                        # kept, and the target went green
OUTCOMES = (REFUSED, NO_VERDICT, REGRESSION, NO_CHANGE, ERROR, PARTIAL, KEPT)
PAYING = (KEPT, PARTIAL)


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


DB = os.path.join(_data_dir(), "metrics.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS sortie("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT, "
                  "repo TEXT, agent TEXT, strategy TEXT DEFAULT '', round INT DEFAULT 0, "
                  "attempt INT DEFAULT 1, outcome TEXT, tests_before INT, tests_after INT, "
                  "tests_total INT, newly TEXT DEFAULT '[]', files INT DEFAULT 0, "
                  "cost INT DEFAULT 0, duration_ms INT DEFAULT 0, detail TEXT DEFAULT '', "
                  "created REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS run("
                  "run_id TEXT PRIMARY KEY, kind TEXT, repo TEXT, actor TEXT DEFAULT '', "
                  "agents INT DEFAULT 1, rounds INT DEFAULT 0, before INT DEFAULT 0, "
                  "after INT DEFAULT 0, total INT DEFAULT 0, solved INT DEFAULT 0, "
                  "gate_ids TEXT DEFAULT '[]', started REAL, ended REAL DEFAULT 0, "
                  "note TEXT DEFAULT '')")
        c.execute("CREATE INDEX IF NOT EXISTS sortie_run ON sortie(run_id)")
        c.execute("CREATE INDEX IF NOT EXISTS sortie_repo ON sortie(repo)")
        c.execute("CREATE INDEX IF NOT EXISTS run_repo ON run(repo)")
        c.commit()
    finally:
        c.close()


# ── writing ──────────────────────────────────────────────────────────────────

def run_start(run_id: str, kind: str, repo: str, *, actor: str = "",
              agents: int = 1, before: int = 0, total: int = 0) -> str:
    init()
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO run(run_id, kind, repo, actor, agents, "
                  "before, total, started) VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, kind, os.path.realpath(repo) if repo else "", actor,
                   agents, before, total, time.time()))
        c.commit()
    finally:
        c.close()
    return run_id


def run_end(run_id: str, *, after: int = 0, total: int = 0, rounds: int = 0,
            solved: bool = False, gate_ids: list | None = None, note: str = "") -> None:
    c = _conn()
    try:
        c.execute("UPDATE run SET after=?, total=COALESCE(NULLIF(?,0), total), "
                  "rounds=?, solved=?, gate_ids=?, ended=?, note=? WHERE run_id=?",
                  (after, total, rounds, 1 if solved else 0,
                   json.dumps(gate_ids or []), time.time(), note[:300], run_id))
        c.commit()
    finally:
        c.close()


def sortie(run_id: str, *, kind: str, repo: str, agent: str, outcome: str,
           strategy: str = "", round_no: int = 0, attempt: int = 1,
           tests_before: int = 0, tests_after: int = 0, tests_total: int = 0,
           newly: list | None = None, files: int = 0, cost: int = 0,
           duration_ms: int = 0, detail: str = "") -> None:
    """One agent, one attempt, one verdict. Called after the oracle has spoken."""
    init()
    c = _conn()
    try:
        c.execute("INSERT INTO sortie(run_id, kind, repo, agent, strategy, round, "
                  "attempt, outcome, tests_before, tests_after, tests_total, newly, "
                  "files, cost, duration_ms, detail, created) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, kind, os.path.realpath(repo) if repo else "", agent,
                   strategy, round_no, attempt, outcome, tests_before, tests_after,
                   tests_total, json.dumps(newly or []), files, cost, duration_ms,
                   detail[:300], time.time()))
        c.commit()
    finally:
        c.close()


def classify(kept: bool, outcome_text: str, target_green: bool) -> str:
    """Map the builder's own words for what happened onto the closed vocabulary.

    The builder already decides these cases; this reads its verdict rather than
    re-deriving one, so the ledger cannot disagree with the gate about what
    happened."""
    text = (outcome_text or "").lower()
    if kept:
        return KEPT if target_green else PARTIAL
    if "refused" in text or "protected" in text:
        return REFUSED
    # "the suite stopped running" is what try_patch actually says when the oracle
    # gives no verdict; matching on the caller's real wording rather than on wording
    # that seemed likely is the difference between a ledger and a guess.
    if ("no verdict" in text or "stopped running" in text
            or "stopped reporting" in text or "timed out" in text):
        return NO_VERDICT
    if "broke" in text or "regress" in text:
        return REGRESSION
    # An executor that crashed and one that replied with nothing usable are the same
    # failure from here: the seam produced no patch to judge. Worth separating from a
    # patch that was judged and found wanting, because the fix is different — reply
    # format rather than approach.
    if "executor" in text or "returned no files" in text:
        return ERROR
    return NO_CHANGE


# ── reading ──────────────────────────────────────────────────────────────────

def _rows(query: str, args=()) -> list[tuple]:
    init()
    c = _conn()
    try:
        return list(c.execute(query, args))
    finally:
        c.close()


def _where(repo: str = "", since: float = 0) -> tuple[str, list]:
    clauses, args = [], []
    if repo:
        clauses.append("repo=?")
        args.append(os.path.realpath(repo))
    if since:
        clauses.append("created>=?")
        args.append(since)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def by_agent(repo: str = "", since: float = 0, limit: int = 50) -> list[dict]:
    where, args = _where(repo, since)
    rows = _rows(
        "SELECT agent, COUNT(*), SUM(outcome IN ('kept','partial')), "
        "SUM(outcome='kept'), SUM(tests_after-tests_before), SUM(cost), "
        "AVG(duration_ms) FROM sortie" + where +
        " GROUP BY agent ORDER BY SUM(tests_after-tests_before) DESC, COUNT(*) DESC "
        "LIMIT ?", args + [limit])
    return [_perf_row("agent", *r) for r in rows]


def by_strategy(repo: str = "", since: float = 0) -> list[dict]:
    where, args = _where(repo, since)
    extra = " AND strategy!=''" if where else " WHERE strategy!=''"
    rows = _rows(
        "SELECT strategy, COUNT(*), SUM(outcome IN ('kept','partial')), "
        "SUM(outcome='kept'), SUM(tests_after-tests_before), SUM(cost), "
        "AVG(duration_ms) FROM sortie" + where + extra +
        " GROUP BY strategy ORDER BY SUM(tests_after-tests_before) DESC", args)
    return [_perf_row("strategy", *r) for r in rows]


def by_attempt(repo: str = "", since: float = 0) -> list[dict]:
    """Does attempt four ever pay? The retry ladder is expensive and this is the
    only honest way to find out whether it earns its tokens."""
    where, args = _where(repo, since)
    rows = _rows(
        "SELECT attempt, COUNT(*), SUM(outcome IN ('kept','partial')), "
        "SUM(outcome='kept'), SUM(tests_after-tests_before), SUM(cost), "
        "AVG(duration_ms) FROM sortie" + where +
        " GROUP BY attempt ORDER BY attempt", args)
    return [_perf_row("attempt", *r) for r in rows]


def _perf_row(key, name, n, paying, kept, gained, cost, avg_ms) -> dict:
    n = n or 0
    return {
        key: name, "sorties": n,
        "paying": paying or 0, "kept": kept or 0,
        "keep_rate": round((paying or 0) / n, 3) if n else 0.0,
        "tests_gained": gained or 0,
        "cost": cost or 0,
        # Only where tests were actually gained. An agent that went backwards has no
        # meaningful cost per test, and a negative one would read as a bargain.
        "cost_per_test": round((cost or 0) / gained, 1) if (gained or 0) > 0 else None,
        "avg_ms": int(avg_ms or 0),
    }


def outcomes(repo: str = "", since: float = 0) -> dict:
    where, args = _where(repo, since)
    rows = _rows("SELECT outcome, COUNT(*) FROM sortie" + where + " GROUP BY outcome", args)
    counts = {o: 0 for o in OUTCOMES}
    for outcome, n in rows:
        counts[outcome] = n
    return counts


def summary(repo: str = "", since: float = 0) -> dict:
    """The numbers the console leads with. Every one of them is a COUNT or a SUM over
    the sortie table — nothing here is an estimate."""
    where, args = _where(repo, since)
    row = _rows("SELECT COUNT(*), SUM(outcome IN ('kept','partial')), "
                "SUM(tests_after-tests_before), SUM(cost), SUM(duration_ms) "
                "FROM sortie" + where, args)
    sorties, paying, gained, cost, ms = (row[0] if row else (0, 0, 0, 0, 0))
    sorties = sorties or 0
    paying = paying or 0

    rwhere, rargs = _where(repo, 0)
    rwhere = rwhere.replace("created>=?", "started>=?")
    if since:
        rwhere = (rwhere + " AND started>=?") if rwhere else " WHERE started>=?"
        rargs = rargs + [since]
    rrow = _rows("SELECT COUNT(*), SUM(solved), SUM(after-before) FROM run" + rwhere, rargs)
    runs, solved, run_gain = (rrow[0] if rrow else (0, 0, 0))

    return {
        "sorties": sorties,
        "paying": paying,
        "rejected": sorties - paying,
        "reject_rate": round((sorties - paying) / sorties, 3) if sorties else 0.0,
        "tests_gained": gained or 0,
        "cost": cost or 0,
        "seconds": round((ms or 0) / 1000.0, 1),
        "runs": runs or 0,
        "runs_solved": solved or 0,
        "run_tests_gained": run_gain or 0,
    }


def recent_runs(repo: str = "", limit: int = 10) -> list[dict]:
    where, args = _where(repo, 0)
    rows = _rows("SELECT run_id, kind, actor, agents, rounds, before, after, total, "
                 "solved, started, ended, note FROM run" + where +
                 " ORDER BY started DESC LIMIT ?", args + [limit])
    keys = ("run_id", "kind", "actor", "agents", "rounds", "before", "after",
            "total", "solved", "started", "ended", "note")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["solved"] = bool(d["solved"])
        d["seconds"] = round(max(0.0, (d["ended"] or 0) - d["started"]), 1) if d["ended"] else None
        out.append(d)
    return out


def run_sorties(run_id: str) -> list[dict]:
    rows = _rows("SELECT agent, strategy, round, attempt, outcome, tests_before, "
                 "tests_after, tests_total, newly, cost, duration_ms, detail "
                 "FROM sortie WHERE run_id=? ORDER BY id", (run_id,))
    keys = ("agent", "strategy", "round", "attempt", "outcome", "tests_before",
            "tests_after", "tests_total", "newly", "cost", "duration_ms", "detail")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        try:
            d["newly"] = json.loads(d["newly"])
        except (TypeError, ValueError):
            d["newly"] = []
        out.append(d)
    return out

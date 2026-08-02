"""The Workspace world — real work under the same constitution (REALWORK.md §3.1).

Where sim.py's world is resources and ages, this world is a codebase and its test
suite. The mapping is exact and the safety law survives translation:

    tests are the oracle; the merge is the gate.

- ``oracle()`` runs the acceptance tests in the sandbox and counts pass/fail.
  No model judges whether work is done — the test runner does.
- Each failing test is an open TASK. An agent's measured contribution is the tests
  it turns green (fed into the same economy: promotion, budgets, reaps, careers).
- ``apply_patch`` writes only inside the sandbox and REFUSES the tests directory —
  an agent cannot pass its exam by editing the exam (reward-hacking guard).
- The test subprocess runs with a stripped environment: no API keys, no tokens
  (Article V: remove the capability, don't police it).
"""

import os
import re
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.path.dirname(_HERE), "sandbox")
TESTS_DIR = "tests"


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


DB = os.path.join(_data_dir(), "workspace.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS tasks("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, test TEXT UNIQUE, "
                  "title TEXT, status TEXT DEFAULT 'open', assigned_to TEXT DEFAULT '', "
                  "solved_by TEXT DEFAULT '')")
        c.commit()
    finally:
        c.close()


# ── the oracle ────────────────────────────────────────────────────────────────

def oracle() -> dict:
    """Run the acceptance suite in the sandbox with a STRIPPED environment and
    return the verdict: {passed, failed, total, failures: [test names]}.
    This is the score — objective, cheap, and instant to read (Article I.3)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": SANDBOX, "LANG": "C.UTF-8"}     # no keys, no tokens, no proxies
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", TESTS_DIR, "-v"],
        cwd=SANDBOX, env=env, capture_output=True, text=True, timeout=60)
    out = proc.stderr + proc.stdout
    passed, failures = 0, []
    for m in re.finditer(r"^(test_\w+) \([^)]*\) \.\.\. (\w+)", out, re.M):
        name, verdict = m.group(1), m.group(2)
        if verdict == "ok":
            passed += 1
        else:
            failures.append(name)
    return {"passed": passed, "failed": len(failures),
            "total": passed + len(failures), "failures": sorted(failures)}


def world() -> dict:
    """The workspace's world-state — same shape of truth the game world provided."""
    o = oracle()
    c = _conn()
    try:
        open_n = c.execute("SELECT COUNT(*) FROM tasks WHERE status='open'").fetchone()[0]
        done_n = c.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
    finally:
        c.close()
    progress = round(100 * o["passed"] / o["total"]) if o["total"] else 0
    return {"tests_passing": o["passed"], "tests_total": o["total"],
            "tasks_open": open_n, "tasks_done": done_n, "progress_pct": progress}


# ── tasks: derived from the oracle, never invented ────────────────────────────

def sync_tasks() -> list[dict]:
    """Every failing test is an open task; every task whose test now passes is done.
    Tasks come from the oracle, so the backlog can't drift from reality."""
    o = oracle()
    c = _conn()
    try:
        for name in o["failures"]:
            c.execute("INSERT OR IGNORE INTO tasks(test, title) VALUES(?,?)",
                      (name, f"make {name} pass"))
            # a redeploy can reset the code while the task DB persists — a test that
            # fails again REOPENS its task; the backlog tracks the oracle, always
            c.execute("UPDATE tasks SET status='open', solved_by='' "
                      "WHERE test=? AND status='done'", (name,))
        if o["failures"]:
            qmarks = ",".join("?" * len(o["failures"]))
            c.execute(f"UPDATE tasks SET status='done' WHERE status!='done' "
                      f"AND test NOT IN ({qmarks})", o["failures"])
        else:
            c.execute("UPDATE tasks SET status='done' WHERE status!='done'")
        c.commit()
    finally:
        c.close()
    return tasks()


def tasks(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = "SELECT id, test, title, status, assigned_to, solved_by FROM tasks"
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        return [{"id": i, "test": te, "title": ti, "status": st,
                 "assigned_to": a, "solved_by": sb}
                for i, te, ti, st, a, sb in c.execute(q + " ORDER BY id", args)]
    finally:
        c.close()


def assign(task_id: int, agent: str) -> bool:
    c = _conn()
    try:
        cur = c.execute("UPDATE tasks SET status='assigned', assigned_to=? "
                        "WHERE id=? AND status='open'", (agent, task_id))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def mark_solved(test: str, agent: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE tasks SET status='done', solved_by=? WHERE test=?",
                  (agent, test))
        c.commit()
    finally:
        c.close()


# ── the only write path into the code ─────────────────────────────────────────

def read_file(rel_path: str) -> str:
    p = _safe_path(rel_path)
    with open(p) as f:
        return f.read()


def apply_patch(rel_path: str, content: str) -> tuple[bool, str]:
    """Write a file inside the sandbox. Refuses anything outside it, and refuses the
    tests directory outright — the exam cannot be edited to pass the exam."""
    try:
        p = _safe_path(rel_path)
    except ValueError as e:
        return False, str(e)
    rel = os.path.relpath(p, SANDBOX)
    if rel.split(os.sep)[0] == TESTS_DIR:
        return False, "REFUSED: agents may not edit the tests — the oracle is not writable"
    with open(p, "w") as f:
        f.write(content)
    return True, f"wrote {rel} ({len(content)} bytes)"


def _safe_path(rel_path: str) -> str:
    p = os.path.realpath(os.path.join(SANDBOX, rel_path))
    if not p.startswith(os.path.realpath(SANDBOX) + os.sep):
        raise ValueError(f"REFUSED: {rel_path} escapes the sandbox")
    return p

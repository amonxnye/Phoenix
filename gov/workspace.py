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
- The test subprocess runs with a stripped environment (no API keys, no tokens) AND,
  where the platform allows it, inside a private network namespace so egress is
  impossible rather than merely unconfigured (Article V: remove the capability, don't
  police it). ``sandbox_mode()`` reports which level is actually in force — a claim
  the system cannot enforce is reported as unenforced, never asserted.
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
        # first-sight snapshot of every module, so the training ground can be reset
        c.execute("CREATE TABLE IF NOT EXISTS pristine(path TEXT PRIMARY KEY, content TEXT)")
        for name in os.listdir(SANDBOX):
            if name.endswith(".py"):
                with open(os.path.join(SANDBOX, name)) as f:
                    c.execute("INSERT OR IGNORE INTO pristine(path, content) VALUES(?,?)",
                              (name, f.read()))
        c.commit()
    finally:
        c.close()


def reset_sandbox() -> list[str]:
    """Restore every module to its first-seen state — the training tasks reopen.
    (Tests are untouched; they were never writable.)"""
    c = _conn()
    try:
        rows = c.execute("SELECT path, content FROM pristine").fetchall()
    finally:
        c.close()
    restored = []
    for path, content in rows:
        with open(os.path.join(SANDBOX, path), "w") as f:
            f.write(content)
        restored.append(path)
    sync_tasks()                                   # failing again → tasks reopen
    return restored


# ── the sandbox: Article V, enforced where the platform permits ──────────────

_SANDBOX: tuple[list, str] | None = None          # (argv prefix, human-readable mode)


def _sandbox() -> tuple[list, str]:
    """Probe ONCE for real isolation and cache it.

    Preference order: a private network namespace (`unshare -rn`) — no route out
    exists, so an agent's code cannot phone home even if it tries — else the
    credential-stripped subprocess we have always had. The probe actually RUNS the
    wrapper (availability of the binary proves nothing inside a restricted
    container), so the reported mode is measured, not assumed."""
    global _SANDBOX
    if _SANDBOX is not None:
        return _SANDBOX
    if os.environ.get("SANDBOX_NETNS", "").strip() == "0":
        _SANDBOX = ([], "credential-stripped only (netns disabled by env)")
        return _SANDBOX
    try:
        probe = subprocess.run(["unshare", "-rn", sys.executable, "-c", "print('ok')"],
                               capture_output=True, text=True, timeout=15)
        if probe.returncode == 0 and "ok" in probe.stdout:
            _SANDBOX = (["unshare", "-rn"], "network-isolated (private netns)")
            return _SANDBOX
    except (OSError, subprocess.SubprocessError):
        pass
    _SANDBOX = ([], "credential-stripped only (no netns on this platform)")
    return _SANDBOX


def sandbox_mode() -> str:
    """What isolation is ACTUALLY in force — surfaced on the console and in the
    constitution's terms. Article VII.4: a property that is merely asserted is not
    governed, so the honest label ships either way."""
    return _sandbox()[1]


# ── the oracle ────────────────────────────────────────────────────────────────

def oracle() -> dict:
    """Run the acceptance suite in the sandbox with a STRIPPED environment and
    return the verdict: {passed, failed, total, failures: [test names]}.
    This is the score — objective, cheap, and instant to read (Article I.4)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": SANDBOX, "LANG": "C.UTF-8"}     # no keys, no tokens, no proxies
    prefix, _mode = _sandbox()                     # + no network, where permitted
    proc = subprocess.run(
        prefix + [sys.executable, "-m", "unittest", "discover", "-s", TESTS_DIR, "-v"],
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
            "tasks_open": open_n, "tasks_done": done_n, "progress_pct": progress,
            "sandbox": sandbox_mode()}


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
    """Write a file inside the sandbox.

    Article IX.7 applied to the oracle: the judge must not depend on anything the
    judged can write. The sandbox is the oracle's working directory, and a working
    directory is `sys.path[0]` for `python -m`, so ANY new file there is on the
    import path of the process that grades the work.

    This was a denylist (refuse `tests/`, allow the rest) and it was demonstrably
    breakable: writing `unittest.py` into the sandbox shadowed the stdlib module the
    runner itself imports, and the oracle reported a green suite while six tests were
    genuinely failing. Enumerating what is forbidden always leaves the rest permitted.

    It is now an allowlist. An agent may modify a file that is already part of the
    exercise; it may not introduce one. New files are the entire attack surface, and
    no legitimate fix to an existing module requires creating one.
    """
    try:
        p = _safe_path(rel_path)
    except ValueError as e:
        return False, str(e)
    rel = os.path.relpath(p, SANDBOX)
    if rel.split(os.sep)[0] == TESTS_DIR:
        return False, "REFUSED: agents may not edit the tests — the oracle is not writable"
    if not os.path.isfile(p):
        return False, (f"REFUSED: {rel} does not exist — agents may modify the module "
                       f"under test, never add a file to the oracle's import path")
    stem = os.path.basename(rel)[:-3] if rel.endswith(".py") else ""
    if stem in sys.stdlib_module_names:            # belt and braces, and states the intent
        return False, (f"REFUSED: {rel} shadows the standard library module '{stem}' "
                       f"that the test runner itself imports")
    with open(p, "w") as f:
        f.write(content)
    return True, f"wrote {rel} ({len(content)} bytes)"


def _safe_path(rel_path: str) -> str:
    p = os.path.realpath(os.path.join(SANDBOX, rel_path))
    if not p.startswith(os.path.realpath(SANDBOX) + os.sep):
        raise ValueError(f"REFUSED: {rel_path} escapes the sandbox")
    return p

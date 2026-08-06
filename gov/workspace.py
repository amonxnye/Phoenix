"""The Workspace world — real work under the same constitution (REALWORK.md §3.1).

Where sim.py's world is resources and ages, this world is a codebase and its test
suite. The mapping is exact and the safety law survives translation:

    tests are the oracle; the merge is the gate.

- ``oracle()`` runs the repo's own test command and counts pass/fail. No model
  judges whether work is done — the test runner does.
- Each failing test is an open TASK. An agent's measured contribution is the tests
  it turns green (fed into the same economy: promotion, budgets, reaps, careers).
- ``apply_patch`` writes only inside the repo and REFUSES every protected path —
  tests, conftest, CI config, packaging. An agent cannot pass its exam by editing
  the exam, nor by editing the invigilator (reward-hacking guard).
- The test subprocess runs with a stripped environment: no API keys, no tokens
  (Article V: remove the capability, don't police it).

BUILDER.md §5.1 — the repo and its test command are configuration, so the same
machinery that ran on ``sandbox/calculator.py`` runs on a real repository:

    WORKSPACE_REPO=/path/to/repo  WORKSPACE_TEST_CMD="python -m pytest -q" ...

Defaults are the toy sandbox, so nothing that worked before changes.
"""

import fnmatch
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)
TOY_REPO = os.path.join(_PROJECT, "sandbox")
TESTS_DIR = "tests"

# Paths an agent may never write. The first three protect the oracle itself (the
# tests, their fixtures, the runner's configuration); the rest protect the repo's
# identity and CI. Any path COMPONENT matching one of these globs is refused, so
# `pkg/tests/x.py` and `.github/workflows/ci.yml` are covered too.
DEFAULT_PROTECTED = (
    "tests", "test", "spec", "testing",
    "conftest.py", "pytest.ini", "tox.ini", "noxfile.py", "setup.cfg",
    "pyproject.toml", "setup.py", "Makefile",
    ".git", ".github", ".gitlab-ci.yml", ".circleci",
)

DEFAULT_TEST_CMD = [sys.executable, "-m", "unittest", "discover", "-s", TESTS_DIR, "-v"]
DEFAULT_TIMEOUT = 300
OUTPUT_TAIL = 6000            # how much test output the worker is allowed to read

# Environment names that are never passed to the test subprocess, whatever the
# operator lists in WORKSPACE_ENV_PASSTHROUGH. Article V is not negotiable by config.
_SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION"
                        r"|AUTH|PROXY|_URL$|DSN", re.I)


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


# ── configuration: which repo, which test command ─────────────────────────────

_CFG: dict = {}


def _env_list(name: str) -> tuple:
    raw = os.environ.get(name, "").strip()
    return tuple(p.strip() for p in raw.split(",") if p.strip()) if raw else ()


def configure(repo: str = "", test_cmd: str = "", timeout: int = 0,
              protected: tuple = ()) -> dict:
    """Point the workspace at a repository. Every argument falls back to the
    matching WORK_* environment variable, then to the toy sandbox defaults."""
    global _CFG
    repo = repo or os.environ.get("WORKSPACE_REPO", "").strip() or TOY_REPO
    repo = os.path.realpath(os.path.expanduser(repo))
    cmd = test_cmd or os.environ.get("WORKSPACE_TEST_CMD", "").strip()
    argv = shlex.split(cmd) if cmd else list(DEFAULT_TEST_CMD)
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable            # the interpreter running us, not $PATH's
    try:
        tmo = int(timeout or os.environ.get("WORKSPACE_TEST_TIMEOUT", "") or DEFAULT_TIMEOUT)
    except ValueError:
        tmo = DEFAULT_TIMEOUT
    prot = tuple(protected) or _env_list("WORKSPACE_PROTECTED") or DEFAULT_PROTECTED
    _CFG = {"repo": repo, "test_cmd": argv, "timeout": max(5, tmo),
            "protected": prot, "passthrough": _env_list("WORKSPACE_ENV_PASSTHROUGH"),
            "is_toy": repo == os.path.realpath(TOY_REPO)}
    return config()


def config() -> dict:
    if not _CFG:
        configure()
    c = dict(_CFG)
    c["test_cmd_str"] = " ".join(shlex.quote(a) for a in c["test_cmd"])
    c["repo_name"] = os.path.basename(c["repo"].rstrip(os.sep)) or c["repo"]
    return c


def repo_root() -> str:
    return config()["repo"]


def _repo_key() -> str:
    """Tasks and snapshots are scoped to a repo, so re-pointing the workspace at a
    different codebase can never mix two backlogs."""
    return repo_root()


def __getattr__(name):
    """`SANDBOX` used to be a constant. It is now whichever repo the workspace is
    pointed at — resolved on access (PEP 562) so older callers read the right path."""
    if name == "SANDBOX":
        return repo_root()
    raise AttributeError(name)


# ── storage ───────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def _columns(c: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _migrate(c: sqlite3.Connection) -> None:
    """v1 keyed tasks and snapshots by path alone, which only works for one repo.
    Re-key both by (repo, path). v1 task ids were bare method names; map them onto
    the new fully-qualified ids where the suffix matches, so `solved_by` survives."""
    cols = _columns(c, "tasks")
    if cols and "repo" not in cols:
        old = c.execute("SELECT test, title, status, assigned_to, solved_by "
                        "FROM tasks").fetchall()
        known = set(_oracle_ids())         # read the oracle BEFORE taking a write lock
        c.execute("DROP TABLE tasks")
        _create_tasks(c)
        for test, title, status, assigned, solved in old:
            match = next((i for i in known if i == test or i.endswith("." + test)), test)
            c.execute("INSERT OR IGNORE INTO tasks(repo, test, title, status, "
                      "assigned_to, solved_by) VALUES(?,?,?,?,?,?)",
                      (_repo_key(), match, title, status, assigned, solved))
    cols = _columns(c, "pristine")
    if cols and "repo" not in cols:
        old = c.execute("SELECT path, content FROM pristine").fetchall()
        c.execute("DROP TABLE pristine")
        _create_pristine(c)
        for path, content in old:
            c.execute("INSERT OR IGNORE INTO pristine(repo, path, content) VALUES(?,?,?)",
                      (_repo_key(), path, content))


def _create_tasks(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS tasks("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL DEFAULT '', "
              "test TEXT, title TEXT, status TEXT DEFAULT 'open', "
              "assigned_to TEXT DEFAULT '', solved_by TEXT DEFAULT '', "
              "UNIQUE(repo, test))")


def _create_pristine(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS pristine("
              "repo TEXT NOT NULL DEFAULT '', path TEXT, content TEXT, "
              "PRIMARY KEY(repo, path))")


def init() -> None:
    config()
    c = _conn()
    try:
        _create_tasks(c)
        _create_pristine(c)
        _migrate(c)
        if _CFG["is_toy"]:
            # The toy training ground is small and disposable: snapshot it eagerly so
            # "reset" reopens the practice tasks. A real repo is snapshotted lazily —
            # see apply_patch — and its real undo is git.
            for name in sorted(os.listdir(_CFG["repo"])):
                if name.endswith(".py"):
                    with open(os.path.join(_CFG["repo"], name)) as f:
                        c.execute("INSERT OR IGNORE INTO pristine(repo, path, content) "
                                  "VALUES(?,?,?)", (_repo_key(), name, f.read()))
        c.commit()
    finally:
        c.close()


def reset_workspace() -> list[str]:
    """Restore every file the fleet has touched to its first-seen state — the tasks
    that were solved reopen. (Tests are untouched; they were never writable.)"""
    c = _conn()
    try:
        rows = c.execute("SELECT path, content FROM pristine WHERE repo=?",
                         (_repo_key(),)).fetchall()
    finally:
        c.close()
    restored = []
    for path, content in rows:
        try:
            with open(_safe_path(path), "w") as f:
                f.write(content)
            restored.append(path)
        except (OSError, ValueError):
            continue
    sync_tasks()                                   # failing again → tasks reopen
    return restored


reset_sandbox = reset_workspace                    # the console's older name


# ── the oracle ────────────────────────────────────────────────────────────────

def _test_env() -> dict:
    """A stripped environment: no keys, no tokens, no proxies. An operator may name
    extra variables a real repo needs (VIRTUAL_ENV, PYTHONPATH…) — never a secret."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": repo_root(), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    for name in config()["passthrough"]:
        if name in env or _SECRETISH.search(name):
            continue
        val = os.environ.get(name)
        if val is not None:
            env[name] = val
    return env


_PYTEST_LINE = re.compile(
    r"^(?P<id>[^\s:]+(?:\.py)?::\S+?)\s+(?P<v>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b",
    re.M)
_PYTEST_SUMMARY = re.compile(          # the short-summary section, when -v was not used
    r"^(?P<v>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(?P<id>[^\s()]+\.py(?:::\S+)?)",
    re.M)
_UNITTEST_LINE = re.compile(
    r"^(?P<name>\w+) \((?P<path>[\w.]+)\)(?: \([^)]*\))?(?: \.\.\.)?\s*"
    r"(?P<v>ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b", re.M)

_FAILED_VERDICTS = {"FAILED", "ERROR", "FAIL", "unexpected success"}
_SKIP_VERDICTS = {"SKIPPED", "skipped", "XFAIL", "expected failure"}


def _merge(results: dict, test_id: str, verdict: str) -> None:
    """A test named twice (verbose line + short summary) keeps its worst verdict."""
    if verdict in _FAILED_VERDICTS or test_id not in results:
        results[test_id] = verdict


def parse_results(out: str) -> dict:
    """Turn a test runner's output into {test id: verdict}. Understands pytest node
    ids and unittest's verbose lines — the two runners this fleet meets in practice.
    Both formats are parsed unconditionally: a repo may run either, and a mixed
    command (`pytest` over a unittest suite) still reports pytest node ids."""
    results: dict = {}
    for m in _PYTEST_LINE.finditer(out):
        _merge(results, m.group("id"), m.group("v"))
    for m in _PYTEST_SUMMARY.finditer(out):
        _merge(results, m.group("id"), m.group("v"))
    for m in _UNITTEST_LINE.finditer(out):
        path, name = m.group("path"), m.group("name")
        # 3.11 renders the dotted path already ending in the method name
        test_id = path if path.split(".")[-1] == name else f"{path}.{name}"
        _merge(results, test_id, m.group("v"))
    return results


def _oracle_ids() -> list[str]:
    try:
        return sorted(parse_results(_run_tests()[0]))
    except Exception:
        return []


def _run_tests() -> tuple[str, int]:
    cfg = config()
    proc = subprocess.run(cfg["test_cmd"], cwd=cfg["repo"], env=_test_env(),
                          capture_output=True, text=True, timeout=cfg["timeout"])
    return proc.stdout + proc.stderr, proc.returncode


def oracle() -> dict:
    """Run the repo's test command with a STRIPPED environment and return the
    verdict: {ok, passed, failed, total, failures, output, error}.
    This is the score — objective, cheap, and instant to read (Article I.3).

    ``ok`` is the honest-failure flag: a command that times out, crashes, or
    collects nothing produced NO VERDICT. Callers must not read 'zero failures'
    as 'everything passes' — a broken import would otherwise pay an agent for
    deleting the suite."""
    cfg = config()
    started = time.time()
    try:
        out, code = _run_tests()
    except subprocess.TimeoutExpired:
        return _verdict([], "", f"test command timed out after {cfg['timeout']}s",
                        time.time() - started)
    except (OSError, ValueError) as e:
        return _verdict([], "", f"test command could not run: {str(e)[:200]}",
                        time.time() - started)
    results = parse_results(out)
    error = ""
    if not results:
        error = (f"the test command produced no test results (exit {code}); "
                 f"the oracle has no verdict")
    return _verdict(results, out, error, time.time() - started)


def _verdict(results, out: str, error: str, secs: float) -> dict:
    if isinstance(results, dict):
        passed = sorted(t for t, v in results.items()
                        if v not in _FAILED_VERDICTS and v not in _SKIP_VERDICTS)
        failures = sorted(t for t, v in results.items() if v in _FAILED_VERDICTS)
        skipped = sorted(t for t, v in results.items() if v in _SKIP_VERDICTS)
    else:
        passed, failures, skipped = [], [], []
    return {"ok": not error, "error": error,
            "passed": len(passed), "failed": len(failures),
            "total": len(passed) + len(failures), "failures": failures,
            "skipped": len(skipped), "passing": passed,
            "output": out[-OUTPUT_TAIL:], "duration_s": round(secs, 2)}


def world() -> dict:
    """The workspace's world-state — same shape of truth the game world provided."""
    o = oracle()
    c = _conn()
    try:
        open_n = c.execute("SELECT COUNT(*) FROM tasks WHERE repo=? AND status='open'",
                           (_repo_key(),)).fetchone()[0]
        done_n = c.execute("SELECT COUNT(*) FROM tasks WHERE repo=? AND status='done'",
                           (_repo_key(),)).fetchone()[0]
    finally:
        c.close()
    progress = round(100 * o["passed"] / o["total"]) if o["total"] else 0
    cfg = config()
    return {"tests_passing": o["passed"], "tests_total": o["total"],
            "tasks_open": open_n, "tasks_done": done_n, "progress_pct": progress,
            "oracle_ok": o["ok"], "oracle_error": o["error"],
            "repo": cfg["repo_name"], "repo_path": cfg["repo"],
            "test_cmd": cfg["test_cmd_str"]}


# ── tasks: derived from the oracle, never invented ────────────────────────────

def sync_tasks() -> list[dict]:
    """Every failing test is an open task; every task whose test now passes is done.
    Tasks come from the oracle, so the backlog can't drift from reality.

    A run with no verdict (``ok`` False) changes nothing: an oracle that could not
    speak must not be able to close the entire backlog."""
    o = oracle()
    if not o["ok"]:
        return tasks()
    repo = _repo_key()
    c = _conn()
    try:
        for name in o["failures"]:
            c.execute("INSERT OR IGNORE INTO tasks(repo, test, title) VALUES(?,?,?)",
                      (repo, name, f"make {name.split('::')[-1]} pass"))
            # a redeploy can reset the code while the task DB persists — a test that
            # fails again REOPENS its task; the backlog tracks the oracle, always
            c.execute("UPDATE tasks SET status='open', solved_by='' "
                      "WHERE repo=? AND test=? AND status='done'", (repo, name))
        if o["failures"]:
            qmarks = ",".join("?" * len(o["failures"]))
            c.execute(f"UPDATE tasks SET status='done' WHERE repo=? AND status!='done' "
                      f"AND test NOT IN ({qmarks})", [repo] + o["failures"])
        else:
            c.execute("UPDATE tasks SET status='done' WHERE repo=? AND status!='done'",
                      (repo,))
        c.commit()
    finally:
        c.close()
    return tasks()


def tasks(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = ("SELECT id, test, title, status, assigned_to, solved_by FROM tasks "
             "WHERE repo=?")
        args = [_repo_key()]
        if status:
            q += " AND status=?"
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
                        "WHERE id=? AND repo=? AND status='open'",
                        (agent, task_id, _repo_key()))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def mark_solved(test: str, agent: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE tasks SET status='done', solved_by=? WHERE repo=? AND test=?",
                  (agent, _repo_key(), test))
        c.commit()
    finally:
        c.close()


# ── the only write path into the code ─────────────────────────────────────────

def read_file(rel_path: str) -> str:
    with open(_safe_path(rel_path)) as f:
        return f.read()


def protected_reason(rel_path: str) -> str:
    """Why this path may not be written, or '' if it may."""
    try:
        p = _safe_path(rel_path)
    except ValueError as e:
        return str(e)
    rel = os.path.relpath(p, repo_root())
    for part in rel.split(os.sep):
        for pattern in config()["protected"]:
            if fnmatch.fnmatch(part, pattern):
                return (f"REFUSED: {rel} is protected ('{pattern}') — agents may not "
                        f"edit the tests, their configuration, or the CI that runs them")
    return ""


def apply_patch(rel_path: str, content: str) -> tuple[bool, str]:
    """Write a file inside the repo. Refuses anything outside it, and refuses every
    protected path outright — the exam cannot be edited to pass the exam."""
    why = protected_reason(rel_path)
    if why:
        return False, why
    p = _safe_path(rel_path)
    rel = os.path.relpath(p, repo_root())
    _snapshot(rel, p)                              # first-seen state, for reset
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    return True, f"wrote {rel} ({len(content)} bytes)"


def restore(rel_path: str, content: str | None) -> tuple[bool, str]:
    """Put a file back the way it was. ``content`` of None means it did not exist
    before the patch, so the undo is removing it — a worker that creates a file and
    breaks the suite must not leave it behind."""
    why = protected_reason(rel_path)
    if why:
        return False, why
    p = _safe_path(rel_path)
    if content is None:
        try:
            os.remove(p)
        except OSError as e:
            return False, f"could not remove {rel_path}: {e}"
        return True, f"removed {rel_path}"
    with open(p, "w") as f:
        f.write(content)
    return True, f"restored {rel_path}"


def _snapshot(rel: str, abs_path: str) -> None:
    """Remember a file's content the first time the fleet touches it. On a real repo
    git is the undo; this is what makes `reset` work anywhere, git or not."""
    if not os.path.exists(abs_path):
        return
    try:
        with open(abs_path) as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return
    c = _conn()
    try:
        _create_pristine(c)
        c.execute("INSERT OR IGNORE INTO pristine(repo, path, content) VALUES(?,?,?)",
                  (_repo_key(), rel, content))
        c.commit()
    finally:
        c.close()


def _safe_path(rel_path: str) -> str:
    root = os.path.realpath(repo_root())
    p = os.path.realpath(os.path.join(root, rel_path))
    if p != root and not p.startswith(root + os.sep):
        raise ValueError(f"REFUSED: {rel_path} escapes the workspace")
    return p


# ── finding the code behind a failing test ────────────────────────────────────

_SOURCE_SKIP_DIRS = {".git", ".github", "__pycache__", ".venv", "venv", "node_modules",
                     ".tox", ".mypy_cache", ".pytest_cache", "build", "dist"}


def list_sources(limit: int = 400) -> list[str]:
    """Every file in the repo an agent is allowed to edit — the writable surface."""
    root = repo_root()
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SOURCE_SKIP_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if not protected_reason(rel):
                out.append(rel)
                if len(out) >= limit:
                    return out
    return out


_TEST_PREFIXES = ("test_", "check_", "spec_")
_TEST_SUFFIXES = ("_test", "_spec")


def _test_module_name(test_id: str) -> str:
    """The name of the module a test lives in, from either id shape:
    `tests/test_calc.py::TestC::test_x` → test_calc,  `pkg.test_calc.TestC.test_x`
    → test_calc. For a dotted path, everything up to the first Class-like part is
    the module path; failing that, the method name is dropped."""
    head = test_id.split("::")[0]
    if head.endswith(".py"):
        return os.path.basename(head)[:-3]
    parts = [p for p in head.split(".") if p]
    if len(parts) > 1:
        cut = next((i for i, p in enumerate(parts) if p[:1].isupper()), len(parts) - 1)
        parts = parts[:cut] or parts[:1]
    return parts[-1] if parts else ""


def find_source_for(test_id: str) -> str:
    """Best guess at the module a failing test exercises: `tests/test_calc.py::test_x`
    and `test_calc.TestCalc.test_x` both point at `calc.py`. Returns '' when the
    guess has no match in the repo — the caller then asks for an explicit target."""
    stem = _test_module_name(test_id)
    for prefix in _TEST_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    else:
        for suffix in _TEST_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
    if not stem:
        return ""
    sources = list_sources()
    target = stem + ".py"
    exact = [s for s in sources if os.path.basename(s) == target]
    if exact:
        return min(exact, key=lambda s: s.count(os.sep))
    near = [s for s in sources if stem in os.path.basename(s)]
    return min(near, key=lambda s: s.count(os.sep)) if near else ""


def find_test_file(test_id: str) -> str:
    """The file the failing test lives in, relative to the repo — readable (so a
    worker can see what it must satisfy) but never writable."""
    head = test_id.split("::")[0]
    root = repo_root()
    if head.endswith(".py") and os.path.exists(os.path.join(root, head)):
        return head
    parts = [p for p in head.split(".") if p]
    for i in range(len(parts), 0, -1):
        candidate = os.path.join(*parts[:i]) + ".py"
        for base in ("", TESTS_DIR):
            rel = os.path.join(base, candidate) if base else candidate
            if os.path.exists(os.path.join(root, rel)):
                return rel
    wanted = _test_module_name(test_id) + ".py"    # a suite living somewhere else
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SOURCE_SKIP_DIRS]
        if wanted in filenames:
            return os.path.relpath(os.path.join(dirpath, wanted), root)
    return ""


def failure_report(test_id: str, verdict: dict | None = None, limit: int = 2500) -> str:
    """The runner's own words about why a test failed — the signal a worker needs
    and the one thing it cannot fabricate."""
    o = verdict or oracle()
    out = o.get("output", "")
    name = test_id.split("::")[-1].split(".")[-1]
    blocks = [b for b in re.split(r"\n(?=(?:={10,}|_{10,})\n)", out) if name in b]
    text = "\n".join(blocks) if blocks else out
    return text[-limit:]

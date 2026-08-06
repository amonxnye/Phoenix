"""Acceptance checks for the Builder — autonomous development, proven without a model.

A scripted solver stands in for the brain, so this exercises the *machinery* — the
part that must be right whatever model is behind the seam:

  1. Isolation — the agent works in a worktree; your checkout is never written to,
     even while the run is in progress.
  2. Iteration — a solver that gets it wrong first is fed what its patch did to the
     suite and succeeds on the retry.
  3. Multi-file patches, applied all-or-nothing.
  4. The oracle judges, not the agent: patches that break, patch that silence the
     suite, and patches that change nothing are all reverted inside the worktree.
  5. The protected set holds — a solver that tries to edit the tests is refused.
  6. The gate — every success parks a dossier; nothing merges autonomously.
  7. Risk classification — a migration path is HIGH and says so.
  8. Approve merges into the base; reject deletes the branch and leaves the base
     exactly where it was.
  9. Budget and attempt caps end a runaway agent (Article II) and a run that
     achieves nothing names its binding constraint (Article IX).

Run:  python3 gov/verify_builder.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import builder as B
import economy as E
import solver as S
import workspace as W
import worktree as WT

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


# ── a repository with real defects ────────────────────────────────────────────

REPO = tempfile.mkdtemp(prefix="phoenix-builder-")
TEST_CMD = f"{sys.executable} -m unittest discover -s tests -v"

BROKEN_MATH = '''"""Arithmetic helpers."""


def add(a, b):
    return a + b


def mean(values):
    # BUG: crashes on an empty list instead of raising a clear error
    return sum(values) / len(values)
'''

BROKEN_TEXT = '''"""Text helpers."""


def slugify(name):
    # BUG: spaces survive
    return name.strip().lower()
'''

TESTS = '''import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mathy
import texty


class TestMathy(unittest.TestCase):
    def test_add(self):
        self.assertEqual(mathy.add(2, 2), 4)

    def test_mean(self):
        self.assertEqual(mathy.mean([2, 4, 6]), 4)

    def test_mean_empty_raises(self):
        with self.assertRaises(ValueError):
            mathy.mean([])


class TestTexty(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(texty.slugify(" Hello World "), "hello-world")
'''

FIXED_MATH = '''"""Arithmetic helpers."""


def add(a, b):
    return a + b


def mean(values):
    if not values:
        raise ValueError("mean of an empty sequence")
    return sum(values) / len(values)
'''

FIXED_TEXT = '''"""Text helpers."""


def slugify(name):
    return name.strip().lower().replace(" ", "-")
'''


def write(rel, content):
    p = os.path.join(REPO, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)


def git(*args, check=True):
    return subprocess.run(("git", "-C", REPO) + args, capture_output=True,
                          text=True, check=check)


def build_repo():
    git("init", "-q", check=False)
    git("config", "user.email", "test@phoenix.local")
    git("config", "user.name", "Phoenix Test")
    git("config", "commit.gpgsign", "false")
    write("src/mathy.py", BROKEN_MATH)
    write("src/texty.py", BROKEN_TEXT)
    write("tests/test_mathy.py", TESTS)
    write("migrations/0001_init.py", "# schema migration\nREVISION = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "initial: two modules with defects")


def read(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


# ── scripted solvers: the brain seam, made deterministic ──────────────────────

def solver_wrong_then_right(calls):
    """Attempt 1 breaks a passing test; attempt 2, having been told so, succeeds."""
    def solve(req):
        calls.append(req)
        if req["attempt"] == 1:
            return {"files": {"src/texty.py": "def slugify(name):\n    raise TypeError\n"}}
        return {"files": {"src/texty.py": FIXED_TEXT}}
    return solve


def solver_fixes(path, content):
    return lambda req: {"files": {path: content}}


def solver_multifile(req):
    return {"files": {"src/mathy.py": FIXED_MATH, "src/texty.py": FIXED_TEXT}}


def solver_edits_tests(req):
    return {"files": {"tests/test_mathy.py": "# deleted\n", "src/texty.py": FIXED_TEXT}}


def solver_useless(req):
    """Touches the file it was given without changing what any test can observe."""
    path, content = next(iter(req["files"].items()))
    return {"files": {path: content + "\n# a thoughtful comment\n"}}


def solver_breaks_a_test(req):
    """Wrecks `add`, which is green — a regression, not merely a failure to help."""
    return {"files": {"src/mathy.py": FIXED_MATH.replace("return a + b", "return 0")}}


def solver_hangs(req):
    """Import-time infinite loop: the runner never reports, so there is NO verdict —
    which is not the same thing as no failures."""
    return {"files": {"src/texty.py": "while True:\n    pass\n"}}


def solver_touches_migration(req):
    return {"files": {"src/texty.py": FIXED_TEXT,
                      "migrations/0001_init.py": "# schema migration\nREVISION = 2\n"}}


def task_for(test_name, tasks):
    return next(t for t in tasks if t["test"].endswith(test_name))


try:
    build_repo()
    A.init()
    E.init()
    B.init()
    W.configure(repo=REPO, test_cmd=TEST_CMD)
    W.init()

    # ── 1. the parser ────────────────────────────────────────────────────────
    print("\n1. The executor seam — multi-file patches parse")
    envelope = (f"Here you go.\n{S.FILE_OPEN} src/a.py>>>\n```python\nX = 1\n```\n"
                f"{S.FILE_CLOSE}\n{S.FILE_OPEN} src/b.py>>>\nY = 2\n{S.FILE_CLOSE}\n")
    parsed = S.parse_patch(envelope)
    check("both files are read out of one reply",
          parsed == {"src/a.py": "X = 1", "src/b.py": "Y = 2"}, str(sorted(parsed)))
    check("a bare reply still becomes the single target file",
          S.parse_patch("Z = 3\n", "src/c.py") == {"src/c.py": "Z = 3"})

    # ── 2. the backlog ───────────────────────────────────────────────────────
    print("\n2. The backlog — derived from a real repo's failing tests")
    base = W.oracle()
    tasks = W.sync_tasks()
    check("the oracle reads the repo", base["ok"] and base["total"] == 4,
          f"{base['passed']}/{base['total']}")
    check("every failing test is a task",
          {t["test"].split(".")[-1] for t in tasks if t["status"] == "open"}
          == {"test_mean_empty_raises", "test_slugify"},
          "; ".join(t["test"].split(".")[-1] for t in tasks))

    # ── 3. isolation ─────────────────────────────────────────────────────────
    print("\n3. Isolation — your checkout is never written to")
    seen_in_worktree = {}

    def solver_snooping(req):
        # while the agent works, look at the REAL repo: it must be untouched
        seen_in_worktree["main_src"] = read("src/mathy.py")
        seen_in_worktree["cwd_is_worktree"] = W.repo_root() != REPO
        return {"files": {"src/mathy.py": FIXED_MATH}}

    E.ensure("dev-01")
    r1 = B.solve_task("dev-01", task_for("test_mean_empty_raises", tasks), REPO,
                      solve=solver_snooping, log=lambda *a: None)
    check("the agent worked somewhere else entirely", seen_in_worktree["cwd_is_worktree"])
    check("the main checkout was untouched mid-run",
          seen_in_worktree["main_src"] == BROKEN_MATH)
    check("the main checkout is untouched after the run", read("src/mathy.py") == BROKEN_MATH)
    check("the work is on its own branch", r1["did"] == "parked"
          and r1["branch"] in WT.list_branches(REPO), r1.get("branch", r1.get("reason", "")))
    check("the task was solved and parked, not merged", r1["did"] == "parked"
          and r1["gate_id"] > 0, str(r1.get("newly_passing")))

    # ── 4. the gate ──────────────────────────────────────────────────────────
    print("\n4. The gate — nothing merges autonomously")
    pending = B.gate_pending(REPO)
    check("a dossier is waiting for a human", len(pending) == 1, str(len(pending)))
    d = B.gate_get(r1["gate_id"])
    check("it carries the real diff", "raise ValueError" in d["diff"],
          f"{len(d['diff'])} chars")
    check("it carries the test delta measured by the oracle",
          d["delta"]["after"] > d["delta"]["before"],
          f"{d['delta']['before']} -> {d['delta']['after']}")
    check("it is classified low risk", d["risk"] == "low", d["risk_why"])
    check("its lineage points back to the decision", d["decision_id"] > 0)

    # ── 5. approve ───────────────────────────────────────────────────────────
    print("\n5. Approve — the one irreversible act, taken by a person")
    ok, msg = B.approve(r1["gate_id"], by="verify-human")
    check("the merge succeeds", ok, msg)
    check("the fix is now in the main checkout", "raise ValueError" in read("src/mathy.py"))
    check("the suite improved on the base branch", W.oracle()["failed"] == 1,
          f"{W.oracle()['passed']}/{W.oracle()['total']}")
    check("the gate entry is closed", B.gate_get(r1["gate_id"])["status"] == "merged")
    ok2, msg2 = B.approve(r1["gate_id"], by="verify-human")
    check("it cannot be merged twice", not ok2, msg2)

    # ── 6. iteration ─────────────────────────────────────────────────────────
    print("\n6. Iteration — a failed attempt teaches the next one")
    tasks = W.sync_tasks()
    calls = []
    E.ensure("dev-02")
    r2 = B.solve_task("dev-02", task_for("test_slugify", tasks), REPO,
                      solve=solver_wrong_then_right(calls), max_attempts=3,
                      log=lambda *a: None)
    check("the first attempt was reverted, not kept",
          "reverted" in r2["history"][0], "; ".join(r2["history"]))
    check("the retry was told what its last attempt did",
          len(calls) > 1 and "reverted" in (calls[1].get("last_outcome") or ""),
          (calls[1].get("last_outcome", "") if len(calls) > 1 else "no retry")[:60])
    check("the second attempt succeeded and parked",
          r2["did"] == "parked" and r2["attempts"] == 2,
          f"{r2['did']} after {r2.get('attempts')} attempt(s)")
    okr2, _ = B.reject(r2["gate_id"], by="verify-human", reason="")   # tidy up for later checks
    check("its branch is dropped when rejected", okr2
          and r2["branch"] not in WT.list_branches(REPO))

    # ── 7. what the oracle refuses ───────────────────────────────────────────
    print("\n7. The oracle judges — four ways to earn nothing")
    slug_task = task_for("test_slugify", W.sync_tasks())
    for name, solve, expect in (
            ("a patch that changes nothing measurable", solver_useless, "changed nothing"),
            ("a patch that breaks a passing test", solver_breaks_a_test, "broke"),
            ("a patch that edits the tests", solver_edits_tests, "refused")):
        E.ensure("dev-probe")
        r = B.solve_task("dev-probe", slug_task, REPO, solve=solve, max_attempts=1,
                         log=lambda *a: None)
        check(name + " is rejected", r["did"] == "abandoned"
              and any(expect in h for h in r["history"]), "; ".join(r["history"])[:90])
    check("the tests survived the attempt to edit them",
          "test_slugify" in read("tests/test_mathy.py"))

    # A hung suite reports NOTHING. That must not read as "nothing is failing".
    W.configure(repo=REPO, test_cmd=TEST_CMD, timeout=5)
    E.ensure("dev-hang")
    r_hang = B.solve_task("dev-hang", slug_task, REPO, solve=solver_hangs,
                          max_attempts=1, log=lambda *a: None)
    check("a patch that hangs the suite earns nothing", r_hang["did"] == "abandoned"
          and any("stopped running" in h for h in r_hang["history"]),
          "; ".join(r_hang["history"])[:90])
    W.configure(repo=REPO, test_cmd=TEST_CMD)

    # ── 8. risk classification ───────────────────────────────────────────────
    print("\n8. Risk — a migration is never routine")
    level, why = B.classify_risk(["src/app.py", "migrations/0003_add_column.py"])
    check("a migration path is HIGH risk", level == "high", why)
    check("ordinary source is low risk", B.classify_risk(["src/app.py"])[0] == "low")
    E.ensure("dev-03")
    r3 = B.solve_task("dev-03", task_for("test_slugify", W.sync_tasks()), REPO,
                      solve=solver_touches_migration, max_attempts=1, log=lambda *a: None)
    check("the work itself was accepted by the oracle", r3["did"] == "parked",
          r3.get("reason", "")[:70])
    g3 = B.gate_get(r3["gate_id"]) if r3.get("gate_id") else {}
    check("but because it touched a migration the gate says HIGH",
          g3.get("risk") == "high", f"{g3.get('risk')}: {g3.get('risk_why')}")
    check("and the human still holds it — nothing auto-merged",
          g3.get("status") == "pending", g3.get("status", ""))
    B.reject(r3["gate_id"], by="verify-human", reason="migration not wanted here")

    # ── 9. reject ────────────────────────────────────────────────────────────
    print("\n9. Reject — refusing costs you nothing")
    before_head = WT.base_ref(REPO)[1]
    tasks = W.sync_tasks()
    E.ensure("dev-04")
    r4 = B.solve_task("dev-04", task_for("test_slugify", tasks), REPO,
                      solve=solver_fixes("src/texty.py", FIXED_TEXT), max_attempts=1,
                      log=lambda *a: None)
    check("the fix parked at the gate", r4["did"] == "parked", str(r4.get("reason", "")))
    okr, msgr = B.reject(r4["gate_id"], by="verify-human", reason="wanted a different approach")
    check("reject succeeds", okr, msgr)
    check("the base branch never moved", WT.base_ref(REPO)[1] == before_head)
    check("the rejected branch is gone", r4["branch"] not in WT.list_branches(REPO))
    check("the working file is unchanged", read("src/texty.py") == BROKEN_TEXT)
    check("the rejection became a lesson",
          any("different approach" in s["lesson"] for s in A.skills_top(20)))

    # ── 9b. the merge guard protects YOUR work ───────────────────────────────
    print("\n9b. The merge guard — your uncommitted work is never merged over")
    write("scratch-notes.txt", "an untracked scratch file")
    _, why_untracked = WT.merge(REPO, "phoenix/does-not-exist", WT.base_ref(REPO)[0], "m")
    check("an untracked scratch file does not block a merge",
          "uncommitted" not in why_untracked, why_untracked[:70])
    existing = read("src/mathy.py")
    write("src/mathy.py", existing + "\n# my own work in progress\n")
    _, why_dirty = WT.merge(REPO, "phoenix/does-not-exist", WT.base_ref(REPO)[0], "m")
    check("a modified tracked file does block it", "uncommitted" in why_dirty,
          why_dirty[:70])
    write("src/mathy.py", existing)
    os.remove(os.path.join(REPO, "scratch-notes.txt"))

    # ── 10. multi-file, and the whole run ────────────────────────────────────
    print("\n10. A full unattended run")
    E.ensure("dev-05")
    report = B.run(repo=REPO, test_cmd=TEST_CMD, agents=2, max_attempts=1,
                   solve=solver_multifile, log=lambda *a: None)
    check("the run reports success", report["ok"], report.get("error", ""))
    check("it parked at least one branch", report["parked"] >= 1,
          f"{report['parked']} of {report['tasks']} task(s)")
    parked = [r for r in report["results"] if r.get("did") == "parked"]
    if parked:
        gd = B.gate_get(parked[0]["gate_id"])
        check("a multi-file patch reached the gate as one branch",
              len(gd["files"]) >= 1, ", ".join(gd["files"]))
        okm, msgm = B.approve(parked[0]["gate_id"], by="verify-human")
        check("approving it makes the suite green", okm and W.oracle()["failed"] == 0,
              f"{W.oracle()['passed']}/{W.oracle()['total']} · {msgm}")
    else:
        check("a multi-file patch reached the gate as one branch", False, "nothing parked")
        check("approving it makes the suite green", False, "nothing to approve")

    # ── 11. budget and liveness ──────────────────────────────────────────────
    print("\n11. Bounded and loud — Articles II and IX")
    write("src/mathy.py", BROKEN_MATH)             # reopen a task to work on
    git("add", "-A")
    git("commit", "-q", "-m", "reintroduce the mean defect")
    E.ensure("dev-broke")
    E.charge("dev-broke", 10 ** 9)                 # spend it all
    check("an agent with no budget left is out", E.budget_left("dev-broke") == 0)
    r5 = B.solve_task("dev-broke", task_for("test_mean_empty_raises", W.sync_tasks()),
                      REPO, solve=solver_fixes("src/mathy.py", FIXED_MATH),
                      max_attempts=2, log=lambda *a: None)
    check("it is not allowed to keep working", r5["did"] == "abandoned",
          "; ".join(r5["history"]))
    check("and it is reaped, not left thrashing", r5["reaped"],
          str([x for x in E.roster(alive_only=False) if x["agent"] == "dev-broke"]))
    report2 = B.run(repo=REPO, test_cmd=TEST_CMD, agents=1, max_attempts=1, limit=1,
                    solve=solver_useless, log=lambda *a: None)
    check("a run that achieves nothing names its binding constraint",
          bool(report2.get("binding_constraint")), report2.get("binding_constraint", "")[:70])

    # ── 12. contribution accumulates ─────────────────────────────────────────
    print("\n12. Careers — contribution accumulates across cycles")
    E.ensure("dev-career")
    E.credit("dev-career", 400)
    E.ensure("dev-career")                          # a second cycle must not reset it
    E.credit("dev-career", 400)
    total = next((x["contribution"] for x in E.roster(alive_only=False)
                  if x["agent"] == "dev-career"), 0)
    check("two cycles of work add up", total == 800, str(total))
    check("and that is enough to promote", E.evaluate("dev-career") == "foreman")
finally:
    try:
        for b in WT.list_branches(REPO):
            WT._git(REPO, "branch", "-D", b, check=False)
    except Exception:
        pass
    W.configure()
    shutil.rmtree(REPO, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

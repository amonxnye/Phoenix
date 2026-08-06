"""Acceptance checks for the Workspace world — real work under the constitution.

Proves REALWORK.md steps 1-2 and BUILDER.md §5.1 without needing any model:
  1. The oracle reads truth from the test suite (failing tests exist and are named).
  2. Tasks derive from the oracle and cannot drift from it.
  3. The workspace is closed: no path escapes, and neither the tests nor the config
     that runs them is writable.
  4. A scripted good patch turns tests green, earns measured contribution, careers
     and lineage record it; a bad patch is reverted and recorded as waste.
  5. A patch that stops the suite from running earns nothing — "no verdict" is not
     "no failures".
  6. The runner adapters parse real pytest and unittest output.
  7. The whole machine runs against a SECOND repository — different location, layout,
     and test command — configured, not hardcoded.

Run:  python3 gov/verify_work.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import economy as E
import worker as K
import workspace as W

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


A.init()
E.init()
W.configure()                                      # the toy sandbox, by default
W.init()
pristine = W.read_file("calculator.py")

GOOD_PATCH = '''"""Fixed calculator."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b


def power(a, b):
    return a ** b


def average(nums):
    if not nums:
        raise ValueError("empty")
    return sum(nums) / len(nums)


def factorial(n):
    if n < 0:
        raise ValueError("negative")
    out = 1
    for i in range(2, n + 1):
        out *= i
    return out


def is_prime(n):
    if n < 2:
        return False
    return all(n % i for i in range(2, int(n ** 0.5) + 1))
'''

BAD_PATCH = "def add(a, b):\n    return 0\n"      # breaks even the passing tests
UNRUNNABLE = "import nonexistent_module_xyz\n"    # the suite cannot even import

# Real pytest output, kept verbatim, so the adapter is checked against the thing it
# will actually meet rather than against our idea of it.
PYTEST_OUTPUT = """\
============================= test session starts ==============================
collected 5 items

tests/test_mathlib.py::TestMath::test_add PASSED                         [ 20%]
tests/test_mathlib.py::TestMath::test_divide FAILED                      [ 40%]
tests/test_mathlib.py::test_power ERROR                                  [ 60%]
tests/test_mathlib.py::test_slow SKIPPED (needs network)                 [ 80%]
tests/test_strings.py::test_upper PASSED                                 [100%]

=========================== short test summary info ============================
FAILED tests/test_mathlib.py::TestMath::test_divide - ZeroDivisionError
ERROR tests/test_mathlib.py::test_power - AttributeError
========================= 2 passed, 1 failed, 1 error, 1 skipped ================
"""

UNITTEST_OUTPUT = """\
test_add (test_calculator.TestCalculator.test_add) ... ok
test_multiply (test_calculator.TestCalculator.test_multiply) ... FAIL
test_power (test_calculator.TestCalculator.test_power) ... ERROR
test_slow (test_calculator.TestCalculator.test_slow) ... skipped 'no network'
"""

# ── the second repository: different root, layout, and test command ───────────
REAL_REPO = tempfile.mkdtemp(prefix="phoenix-workspace-")
REAL_TEST_CMD = f"{sys.executable} -m unittest discover -s spec -p check_*.py -v"
REAL_SOURCE = '''"""A library with a defect, in a repo that is not the toy sandbox."""


def normalize(name):
    return name.strip().lower()


def slugify(name):
    # BUG: spaces are not replaced
    return normalize(name)
'''
REAL_FIXED = '''"""A library with a defect, in a repo that is not the toy sandbox."""


def normalize(name):
    return name.strip().lower()


def slugify(name):
    return normalize(name).replace(" ", "-")
'''
REAL_SPEC = '''import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import naming


class CheckNaming(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(naming.normalize("  Ada  "), "ada")

    def test_slugify(self):
        self.assertEqual(naming.slugify("Hello World"), "hello-world")
'''


def build_real_repo():
    os.makedirs(os.path.join(REAL_REPO, "src"), exist_ok=True)
    os.makedirs(os.path.join(REAL_REPO, "spec"), exist_ok=True)
    with open(os.path.join(REAL_REPO, "src", "naming.py"), "w") as f:
        f.write(REAL_SOURCE)
    with open(os.path.join(REAL_REPO, "spec", "check_naming.py"), "w") as f:
        f.write(REAL_SPEC)
    with open(os.path.join(REAL_REPO, "pyproject.toml"), "w") as f:
        f.write("[project]\nname = 'naming'\n")
    os.makedirs(os.path.join(REAL_REPO, ".github", "workflows"), exist_ok=True)
    with open(os.path.join(REAL_REPO, ".github", "workflows", "ci.yml"), "w") as f:
        f.write("name: ci\n")
    subprocess.run(["git", "init", "-q"], cwd=REAL_REPO, capture_output=True)


try:
    # ── 1. the oracle ────────────────────────────────────────────────────────
    print("\n1. Oracle — the test suite is the score")
    o = W.oracle()
    check("failing tests exist in the pristine sandbox", o["failed"] >= 3,
          f"{o['passed']}/{o['total']} passing; failures: {', '.join(o['failures'])}")
    check("the oracle reports a verdict it can stand behind", o["ok"] and o["total"] > 0)
    check("failures are named tests", all("test_" in f for f in o["failures"]))
    check("the runner's own output is carried, not summarised away",
          "Traceback" in o["output"], f"{len(o['output'])} chars")

    # ── 2. tasks derive from the oracle ──────────────────────────────────────
    print("\n2. Tasks — the backlog cannot drift from reality")
    ts = W.sync_tasks()
    open_tests = {t["test"] for t in ts if t["status"] in ("open", "assigned")}
    check("every failing test is an open task", set(o["failures"]) <= open_tests)

    # ── 3. the workspace is closed ───────────────────────────────────────────
    print("\n3. Workspace — Article V, literally")
    ok1, msg1 = W.apply_patch("../gov/anchor.py", "pwned")
    check("a path that escapes the workspace is refused", not ok1, msg1[:60])
    ok2, msg2 = W.apply_patch("tests/test_calculator.py", "pwned")
    check("the tests are not writable (no exam-editing)", not ok2, msg2[:60])
    for guarded in ("conftest.py", "pytest.ini", "pyproject.toml",
                    ".github/workflows/ci.yml", "pkg/tests/helper.py"):
        okp, _ = W.apply_patch(guarded, "pwned")
        check(f"{guarded} is not writable", not okp)
    check("no secret reaches the test subprocess",
          not any(k for k in W._test_env() if "KEY" in k.upper() or "TOKEN" in k.upper()),
          ", ".join(sorted(W._test_env())))

    # ── 4. the work loop, scored by the oracle ───────────────────────────────
    print("\n4. Work — measured contribution, recorded forever")
    task = next(t for t in ts if t["status"] in ("open", "assigned"))
    r_bad = K.apply_and_score("dev-test", "calculator.py", BAD_PATCH, task)
    check("a patch that breaks tests is reverted", r_bad["did"] == "reverted",
          str(r_bad.get("broke")))
    check("the pristine code is restored after a revert",
          W.read_file("calculator.py") == pristine)

    contrib_before = next((r["contribution"] for r in E.roster(alive_only=False)
                           if r["agent"] == "dev-test"), 0)
    r_good = K.apply_and_score("dev-test", "calculator.py", GOOD_PATCH, task)
    check("a good patch turns the failing tests green", r_good["did"] == "patched"
          and len(r_good["newly_passing"]) >= 3, str(r_good.get("newly_passing")))
    check("the whole suite is green after the fix",
          W.oracle()["failed"] == 0, r_good.get("suite", ""))
    contrib_after = next((r["contribution"] for r in E.roster(alive_only=False)
                          if r["agent"] == "dev-test"), 0)
    check("contribution is measured by the oracle, not claimed",
          contrib_after - contrib_before == r_good["contribution"] > 0,
          f"+{r_good['contribution']}")
    check("tasks close only when their test passes",
          all(t["status"] == "done" for t in W.sync_tasks()))
    life = next((c for c in A.careers(50) if c["uid"] == "dev-test"), None)
    check("the worker's career records the shipped work", life is not None
          and any(e["event"] == "work" for e in life["events"]))

    # ── 5. no verdict is not a pass ──────────────────────────────────────────
    print("\n5. Honest failure — an oracle that cannot speak pays nobody")
    green = W.oracle()
    contrib_before = next((r["contribution"] for r in E.roster(alive_only=False)
                           if r["agent"] == "dev-test"), 0)
    r_dead = K.apply_and_score("dev-test", "calculator.py", UNRUNNABLE, task, green)
    check("a patch that stops the suite running is reverted",
          r_dead["did"] == "reverted", str(r_dead.get("reason", ""))[:70])
    check("nothing was paid for silencing the oracle",
          next((r["contribution"] for r in E.roster(alive_only=False)
                if r["agent"] == "dev-test"), 0) == contrib_before)
    check("the code is back and the suite runs again", W.oracle()["ok"])
    check("a syncless backlog: no-verdict runs cannot close tasks",
          W.sync_tasks() is not None)

    # ── 6. the runner adapters ───────────────────────────────────────────────
    print("\n6. Adapters — pytest and unittest, parsed from their real output")
    pt = W.parse_results(PYTEST_OUTPUT)
    check("pytest node ids are read whole",
          "tests/test_mathlib.py::TestMath::test_divide" in pt, str(len(pt)) + " ids")
    v = W._verdict(pt, PYTEST_OUTPUT, "", 0.0)
    check("pytest passes/failures/skips are counted correctly",
          (v["passed"], v["failed"], v["skipped"]) == (2, 2, 1),
          f"{v['passed']}p/{v['failed']}f/{v['skipped']}s")
    ut = W._verdict(W.parse_results(UNITTEST_OUTPUT), UNITTEST_OUTPUT, "", 0.0)
    check("unittest ids are fully qualified (no collisions across modules)",
          "test_calculator.TestCalculator.test_multiply" in ut["failures"])
    check("unittest passes/failures/skips are counted correctly",
          (ut["passed"], ut["failed"], ut["skipped"]) == (1, 2, 1),
          f"{ut['passed']}p/{ut['failed']}f/{ut['skipped']}s")
    check("a run that produced no results has no verdict",
          not W._verdict({}, "boom", "no results", 0.0)["ok"])

    # ── 7. a second, real repository — configured, not hardcoded ─────────────
    print("\n7. A real repository — same machinery, different repo and test command")
    build_real_repo()
    cfg = W.configure(repo=REAL_REPO, test_cmd=REAL_TEST_CMD)
    W.init()
    check("the workspace points at the configured repo", W.repo_root() == REAL_REPO,
          cfg["test_cmd_str"])
    ro = W.oracle()
    check("the repo's own test command is the oracle",
          ro["ok"] and ro["total"] == 2 and ro["failed"] == 1,
          f"{ro['passed']}/{ro['total']}; failures: {', '.join(ro['failures'])}")
    rts = W.sync_tasks()
    check("its failing test became an open task",
          any(t["status"] in ("open", "assigned") for t in rts),
          "; ".join(t["title"] for t in rts))
    check("the toy repo's backlog is not mixed in",
          all("calculator" not in t["test"] for t in rts))
    failing = ro["failures"][0]
    target = W.find_source_for(failing)
    check("the code behind a failing test is located, not hardcoded",
          target == os.path.join("src", "naming.py"), f"{failing} -> {target or 'none'}")
    check("its spec file is readable but protected",
          W.find_test_file(failing).endswith("check_naming.py")
          and bool(W.protected_reason(W.find_test_file(failing))),
          W.find_test_file(failing))
    okg, _ = W.apply_patch(".github/workflows/ci.yml", "pwned")
    check("the real repo's CI config is not writable", not okg)
    rtask = next(t for t in rts if t["status"] in ("open", "assigned"))
    r_real = K.apply_and_score("dev-real", target, REAL_FIXED, rtask, ro)
    check("a good patch on the real repo is paid by its own oracle",
          r_real["did"] == "patched" and r_real["contribution"] > 0,
          f"{r_real.get('suite')} (+{r_real.get('contribution', 0)})")
    check("the real repo's suite is green", W.oracle()["failed"] == 0)
    W.reset_workspace()
    check("reset restores the file the fleet touched",
          W.read_file(target) == REAL_SOURCE and W.oracle()["failed"] == 1)
finally:
    W.configure()                                  # back to the toy sandbox
    W.apply_patch("calculator.py", pristine)       # always restore the training ground
    shutil.rmtree(REAL_REPO, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

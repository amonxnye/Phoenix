"""Acceptance checks for the Workspace world — real work under the constitution.

Proves REALWORK.md steps 1-2 without needing any model:
  1. The oracle reads truth from the test suite (failing tests exist and are named).
  2. Tasks derive from the oracle and cannot drift from it.
  3. The sandbox is closed: no path escapes, and the tests are not writable.
  4. A scripted good patch turns tests green, earns measured contribution, careers
     and lineage record it; a bad patch is reverted and recorded as waste.

Run:  python3 gov/verify_work.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import economy as E
import worker as K
import workspace as W

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))          # a truthy non-bool would print PASS and then
                                      # crash the tally — the reporting path must not
                                      # depend on the shape of what it reports (IX.7)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


A.init()
E.init()
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

try:
    # ── 1. the oracle ────────────────────────────────────────────────────────
    print("\n1. Oracle — the test suite is the score")
    o = W.oracle()
    check("failing tests exist in the pristine sandbox", o["failed"] >= 3,
          f"{o['passed']}/{o['total']} passing; failures: {', '.join(o['failures'])}")
    check("failures are named tests", all(f.startswith("test_") for f in o["failures"]))

    # ── 2. tasks derive from the oracle ──────────────────────────────────────
    print("\n2. Tasks — the backlog cannot drift from reality")
    ts = W.sync_tasks()
    open_tests = {t["test"] for t in ts if t["status"] in ("open", "assigned")}
    check("every failing test is an open task", set(o["failures"]) <= open_tests)

    # ── 3. the sandbox is closed ─────────────────────────────────────────────
    print("\n3. Sandbox — Article V, literally")
    ok1, msg1 = W.apply_patch("../gov/anchor.py", "pwned")
    check("a path that escapes the sandbox is refused", not ok1, msg1[:60])
    ok2, msg2 = W.apply_patch("tests/test_calculator.py", "pwned")
    check("the tests are not writable (no exam-editing)", not ok2, msg2[:60])

    # Article IX.7 applied to the ORACLE: the judge must not depend on anything the
    # judged can write. The sandbox is the oracle's working directory, and a working
    # directory is sys.path[0] for `python -m`. With a denylist that refused only
    # `tests/`, writing `unittest.py` there shadowed the module the runner itself
    # imports and the oracle reported a GREEN suite while six tests were failing.
    truth = W.oracle()
    for name, why in (("unittest.py", "shadows the stdlib module the runner imports"),
                      ("sitecustomize.py", "an interpreter start-up hook"),
                      ("helper.py", "any new file on the oracle's import path")):
        okx, msgx = W.apply_patch(name, "import sys\nraise SystemExit(0)\n")
        check(f"a new file in the oracle's import path is refused — {name}", not okx, why)
    check("the oracle's verdict is unchanged by the attempt",
          W.oracle() == truth, f"{truth['passed']}/{truth['total']} passing, "
                               f"{truth['failed']} failing — as before")
    check("but the module under test is still writable (real work still works)",
          W.apply_patch("calculator.py", pristine)[0])

    # Article V: the network claim is TESTED, not asserted. Where the platform grants
    # namespaces we prove egress is impossible; where it doesn't we prove the system
    # says so honestly instead of claiming isolation it cannot enforce.
    mode = W.sandbox_mode()
    prefix, _ = W._sandbox()
    probe = subprocess.run(
        prefix + [sys.executable, "-c",
                  "import socket;socket.create_connection(('1.1.1.1',53),timeout=4)"],
        capture_output=True, text=True, timeout=30)
    if prefix:
        check("sandboxed code cannot reach the network (netns enforced)",
              probe.returncode != 0, mode)
    else:
        check("no isolation is claimed that the platform can't enforce",
              "credential-stripped only" in mode, mode)

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

    # ── 5. effort is not progress, and retrying is not effort ───────────────
    print("\n5. Governance — Articles I.2 and IX.8, in the coding domain")
    W.apply_patch("calculator.py", pristine)       # back to a failing suite
    tasks = [t for t in W.sync_tasks() if t["status"] in ("open", "assigned")]
    t0 = tasks[0]
    A.config_set(f"attempts:{t0['id']}", "0")
    base = W.oracle()

    # A patch that applies, breaks nothing, and moves no test is not work. It used
    # to be filed as `[work]` with a contribution of zero — motion recorded as
    # productivity, the same defect the settlement committed for 12,000 turns.
    noop = pristine + "\n# a comment changes nothing measurable\n"
    r_noop = K.apply_and_score("dev-test", "calculator.py", noop, t0, base)
    check("a patch that moves no test is waste, not work", r_noop["did"] == "no-op",
          f"suite unchanged at {r_noop.get('suite')}")
    check("it earns no contribution", r_noop.get("contribution") == 0)
    check("and it spends an attempt from the task's budget",
          A.counter_get(f"attempts:{t0['id']}") == 1)

    # IX.8: repeated failure on one task is a loop, not effort. Exhaust the budget
    # and the worker must stop and escalate rather than re-pick it forever.
    for _ in range(K.MAX_ATTEMPTS):
        A.counter_add(f"attempts:{t0['id']}", 1)
    for t in tasks[1:]:
        A.config_set(f"attempts:{t['id']}", str(K.MAX_ATTEMPTS))
    stuck = K.work_cycle("dev-test")
    check("an exhausted workboard stops instead of looping", stuck["did"] == "stuck",
          f"every open task at {K.MAX_ATTEMPTS} attempts")
    check("and it escalates rather than failing quietly",
          any("WORKBOARD STUCK" in e for e in A.event_log(40)))
    for t in tasks:                                # leave the board workable again
        A.config_set(f"attempts:{t['id']}", "0")
finally:
    W.apply_patch("calculator.py", pristine)       # always restore the toy repo

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

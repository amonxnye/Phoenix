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
    results.append(ok)
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
finally:
    W.apply_patch("calculator.py", pristine)       # always restore the toy repo

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

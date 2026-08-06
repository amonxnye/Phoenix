"""Acceptance for the ledger and the starter workspaces.

Two things are checked here that nothing else can check.

**The ledger must not flatter the fleet.** Every rejected attempt has to be present
and correctly classified, and the classification has to be driven by the *actual*
strings the builder produces — a vocabulary that matches wording somebody imagined
would silently file real failures under the wrong heading and make the fleet look
better than it is. So the outcome cases below are the literal messages from
``builder.try_patch`` and ``campaign._sortie``.

**Aggregation must be scoped.** Several people use one instance, and a performance
number that quietly mixed two workspaces would be worse than no number at all.

Run:  python3 gov/verify_metrics.py
"""

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA = tempfile.mkdtemp(prefix="phoenix-metrics-")
os.environ["GOV_DATA_DIR"] = DATA

import metrics as M                                                    # noqa: E402
import starter                                                         # noqa: E402
import worktree as WT                                                  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def section(title):
    print(f"\n{title}")


M.init()
REPO_A = tempfile.mkdtemp(prefix="metrics-a-")
REPO_B = tempfile.mkdtemp(prefix="metrics-b-")

# ─────────────────────────────────────────────────────────────────────────────
section("Classifying what the oracle said")

# These are the literal outcome strings produced by builder.try_patch and
# campaign._sortie. If either changes its wording, this suite must fail.
CASES = [
    (True, "kept src/orders.py — 3 newly green, suite 4/7", True, M.KEPT),
    (True, "kept src/orders.py — 1 newly green, suite 2/7", False, M.PARTIAL),
    (False, "refused — tests/test_orders.py is protected", False, M.REFUSED),
    (False, "reverted — the suite stopped running (ImportError)", False, M.NO_VERDICT),
    (False, "no verdict at the champion: timed out after 120s", False, M.NO_VERDICT),
    (False, "reverted — broke test_total, test_eta", False, M.REGRESSION),
    (False, "reverted — src/orders.py changed nothing the oracle can see", False, M.NO_CHANGE),
    (False, "executor error: connection reset", False, M.ERROR),
    # An empty reply is an executor failure, not a patch that missed: there was
    # nothing for the oracle to judge, and the fix is the reply format.
    (False, "the executor returned no files", False, M.ERROR),
]
for kept, text, green, want in CASES:
    got = M.classify(kept, text, green)
    check(f"{want:<12} ← {text[:52]}", got == want, "" if got == want else f"got {got}")

check("every classification lands in the closed vocabulary",
      all(M.classify(k, t, g) in M.OUTCOMES for k, t, g in
          [(False, "something nobody anticipated", False)] + [(c[0], c[1], c[2]) for c in CASES]))
check("kept outcomes are the only paying ones", set(M.PAYING) == {M.KEPT, M.PARTIAL})

# ─────────────────────────────────────────────────────────────────────────────
section("Writing the ledger")

M.run_start("run-a", "builder", REPO_A, actor="guest-aaa", agents=2, before=1, total=7)
M.sortie("run-a", kind="builder", repo=REPO_A, agent="a-01", outcome=M.NO_CHANGE,
         attempt=1, tests_before=1, tests_after=1, tests_total=7, cost=1000,
         duration_ms=900, detail="nothing the oracle can see")
M.sortie("run-a", kind="builder", repo=REPO_A, agent="a-01", outcome=M.KEPT,
         attempt=2, tests_before=1, tests_after=5, tests_total=7, newly=["t1", "t2"],
         cost=2000, duration_ms=1100, files=1)
M.sortie("run-a", kind="builder", repo=REPO_A, agent="a-02", outcome=M.REGRESSION,
         attempt=1, tests_before=1, tests_after=0, tests_total=7, cost=1500,
         duration_ms=700)
M.run_end("run-a", after=5, total=7, rounds=2, solved=True, gate_ids=[1],
          note="1 parked")

rows = M.run_sorties("run-a")
check("every sortie is stored, kept or not", len(rows) == 3, f"{len(rows)} rows")
check("the rejected ones are there too",
      sum(1 for r in rows if r["outcome"] not in M.PAYING) == 2)
check("newly-green test names survive the round trip",
      any(r["newly"] == ["t1", "t2"] for r in rows))
check("costs are recorded per attempt", sum(r["cost"] for r in rows) == 4500)
check("durations are recorded", all(r["duration_ms"] > 0 for r in rows))

# ─────────────────────────────────────────────────────────────────────────────
section("Aggregation arithmetic")

by_agent = {r["agent"]: r for r in M.by_agent(repo=REPO_A)}
check("per-agent sortie counts are right", by_agent["a-01"]["sorties"] == 2)
check("keep rate is paying over total", by_agent["a-01"]["keep_rate"] == 0.5,
      str(by_agent["a-01"]["keep_rate"]))
check("an agent that only broke things has a zero keep rate",
      by_agent["a-02"]["keep_rate"] == 0.0)
check("tests gained is summed from the oracle's numbers",
      by_agent["a-01"]["tests_gained"] == 4, str(by_agent["a-01"]["tests_gained"]))
check("a regression counts against the total, it is not clamped",
      by_agent["a-02"]["tests_gained"] == -1, str(by_agent["a-02"]["tests_gained"]))
check("cost per test is only derived where tests were gained",
      by_agent["a-01"]["cost_per_test"] == round(3000 / 4, 1)
      and by_agent["a-02"]["cost_per_test"] is None)

by_attempt = {r["attempt"]: r for r in M.by_attempt(repo=REPO_A)}
check("attempts are broken out separately", set(by_attempt) == {1, 2})
check("first attempts here paid nothing", by_attempt[1]["keep_rate"] == 0.0)
check("the retry is what paid", by_attempt[2]["keep_rate"] == 1.0)

counts = M.outcomes(repo=REPO_A)
check("outcome counts cover the whole vocabulary", set(counts) == set(M.OUTCOMES))
check("and count what was written",
      counts[M.KEPT] == 1 and counts[M.NO_CHANGE] == 1 and counts[M.REGRESSION] == 1)

s = M.summary(repo=REPO_A)
check("summary counts sorties", s["sorties"] == 3)
check("summary counts rejections", s["rejected"] == 2)
check("reject rate is measured, not estimated", s["reject_rate"] == round(2 / 3, 3))
check("summary reports a run count", s["runs"] == 1 and s["runs_solved"] == 1)

# ─────────────────────────────────────────────────────────────────────────────
section("Scoping — one instance, several people")

M.run_start("run-b", "campaign", REPO_B, actor="guest-bbb", agents=1, before=0, total=5)
M.sortie("run-b", kind="campaign", repo=REPO_B, agent="b-01", strategy="direct",
         round_no=1, outcome=M.KEPT, tests_before=0, tests_after=5, tests_total=5,
         cost=9000, duration_ms=1500)
M.run_end("run-b", after=5, total=5, rounds=1, solved=True)

check("another workspace's sorties do not enter the first's totals",
      M.summary(repo=REPO_A)["sorties"] == 3)
check("nor its costs", M.summary(repo=REPO_A)["cost"] == 4500)
check("the second workspace has its own", M.summary(repo=REPO_B)["sorties"] == 1)
check("agents are not mixed across workspaces",
      {r["agent"] for r in M.by_agent(repo=REPO_B)} == {"b-01"})
check("the unscoped view sees everything", M.summary()["sorties"] == 4)

by_strategy = M.by_strategy()
check("strategies are reported where they were recorded",
      [r["strategy"] for r in by_strategy] == ["direct"], str(by_strategy))
check("builder sorties carry no strategy and are excluded",
      all(r["strategy"] for r in by_strategy))

runs = M.recent_runs()
check("runs come back newest first", runs[0]["run_id"] == "run-b")
check("with a wall-clock duration", runs[0]["seconds"] is not None)
check("and the actor who asked for them", runs[0]["actor"] == "guest-bbb")
check("scoped runs are scoped too", len(M.recent_runs(repo=REPO_A)) == 1)

# ─────────────────────────────────────────────────────────────────────────────
section("Edge cases that would otherwise divide by zero")

empty = M.summary(repo="/nowhere/at/all")
check("an unknown repository summarises to zeroes, not an exception",
      empty["sorties"] == 0 and empty["reject_rate"] == 0.0)
check("its aggregates are empty lists", M.by_agent(repo="/nowhere/at/all") == [])
check("a run with no sorties yields no rows", M.run_sorties("never-happened") == [])
check("init is idempotent", M.init() is None and M.summary()["sorties"] == 4)
check("since-filters exclude older rows",
      M.summary(since=time.time() + 60)["sorties"] == 0)

# ─────────────────────────────────────────────────────────────────────────────
section("Starter workspaces")

cat = starter.catalogue()
check("the catalogue lists every starter", len(cat) == len(starter.ORDER))
check("each entry says how much is broken", all(c["red"] >= 4 for c in cat))
check("each has a title and a blurb", all(c["title"] and c["blurb"] for c in cat))

check("a guest is given a stable starter", starter.pick("abc") == starter.pick("abc"))
check("different guests spread across the catalogue",
      len({starter.pick(f"session-{i}") for i in range(30)}) > 1)
check("an empty seed still picks something valid", starter.pick("") in starter.STARTERS)

built = {}
for name in starter.ORDER:
    dest = tempfile.mkdtemp(prefix=f"starter-{name}-")
    ws = starter.provision(dest, name)
    built[name] = ws
    claimed = sorted(starter.STARTERS[name]["red"])
    actual = sorted(t.split(".")[-1] for t in ws["red"])
    check(f"{name}: provisions a git repo with commits",
          WT.is_git(ws["repo"]) and WT.has_commits(ws["repo"]))
    check(f"{name}: the tests it claims are red really are", claimed == actual,
          f"{len(actual)} red")
    check(f"{name}: it reports the branch to merge into", ws["base_branch"])

check("an unknown starter name falls back rather than failing",
      starter.provision(tempfile.mkdtemp(prefix="starter-x-"), "nonsense")["starter"]
      == starter.ORDER[0])

# a starter whose suite passes is a broken starter, and must be refused
green = {"title": "Already fine", "blurb": "nothing to do", "red": [],
         "files": {"src/fine.py": "def ok():\n    return True\n",
                   "tests/test_fine.py": "import os\nimport sys\nimport unittest\n\n"
                                         "sys.path.insert(0, os.path.join("
                                         "os.path.dirname(__file__), '..', 'src'))\n\n"
                                         "import fine\n\n\n"
                                         "class T(unittest.TestCase):\n"
                                         "    def test_ok(self):\n"
                                         "        self.assertTrue(fine.ok())\n"}}
starter.STARTERS["_green"] = green
raised = ""
try:
    starter.provision(tempfile.mkdtemp(prefix="starter-green-"), "_green")
except RuntimeError as e:
    raised = str(e)
check("a starter that provisions green is refused, not handed out",
      "green" in raised.lower(), raised[:70])
del starter.STARTERS["_green"]

check("the oracle's configuration is restored after provisioning",
      starter._red_tests(built["orders"]["repo"]) and True)

for ws in built.values():
    shutil.rmtree(ws["repo"], ignore_errors=True)
shutil.rmtree(REPO_A, ignore_errors=True)
shutil.rmtree(REPO_B, ignore_errors=True)

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)

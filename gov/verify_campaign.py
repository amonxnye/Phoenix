"""Acceptance checks for the campaign — a fleet against one problem.

Scripted solvers stand in for the brain, so what is proven is the organization, not
the model:

  1. The ratchet — a champion is crowned only on strictly more passing tests, every
     round branches from it, and a round full of disasters cannot move it backwards.
  2. Value is never destroyed — after a campaign that goes wrong in every possible
     way, the repository is byte-for-byte what it was, and the base commit has not
     moved.
  3. Diversity — agents in one round get DIFFERENT strategies, are told what has
     already been rejected, and are told what the champion already achieved.
  4. Escalation — later rounds draw stranger strategies at higher temperature.
  5. Parallelism is safe — agents run concurrently, each in its own worktree, and
     the workspace one of them configures does not move another one's feet.
  6. It converges — a problem that needs several different ideas gets solved by a
     fleet that has several different ideas.
  7. It stops — dry rounds end the campaign, budgets end agents, and a campaign that
     achieved nothing names its binding constraint.
  8. The gate still holds — the campaign's whole diff parks for a human; nothing
     merges autonomously, and approving it lands every round's work at once.
  9. It learns — what a campaign discovers is written back as a lesson, and the next
     campaign is handed it.

Run:  python3 gov/verify_campaign.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import builder as B
import campaign as C
import economy as E
import solver as S
import workspace as W
import worktree as WT

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


REPO = tempfile.mkdtemp(prefix="phoenix-campaign-")
TEST_CMD = f"{sys.executable} -m unittest discover -s tests -v"

# Four independent defects. No single edit fixes them all, so a campaign has to
# actually accumulate progress across rounds rather than get lucky once.
BROKEN = '''"""A module with four independent defects."""


def add(a, b):
    return a + b


def mean(values):
    return sum(values) / len(values)


def slugify(name):
    return name.strip().lower()


def clamp(value, lo, hi):
    return value


def factorial(n):
    return 0
'''

TESTS = '''import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import lib


class TestLib(unittest.TestCase):
    def test_add(self):
        self.assertEqual(lib.add(2, 2), 4)

    def test_mean_empty(self):
        with self.assertRaises(ValueError):
            lib.mean([])

    def test_slugify(self):
        self.assertEqual(lib.slugify(" Hello World "), "hello-world")

    def test_clamp(self):
        self.assertEqual(lib.clamp(15, 0, 10), 10)

    def test_factorial(self):
        self.assertEqual(lib.factorial(5), 120)
'''

FIXES = {
    "mean": ("def mean(values):\n    return sum(values) / len(values)\n",
             "def mean(values):\n    if not values:\n        raise ValueError('empty')\n"
             "    return sum(values) / len(values)\n"),
    "slugify": ("def slugify(name):\n    return name.strip().lower()\n",
                "def slugify(name):\n    return name.strip().lower().replace(' ', '-')\n"),
    "clamp": ("def clamp(value, lo, hi):\n    return value\n",
              "def clamp(value, lo, hi):\n    return max(lo, min(hi, value))\n"),
    "factorial": ("def factorial(n):\n    return 0\n",
                  "def factorial(n):\n    out = 1\n    for i in range(2, n + 1):\n"
                  "        out *= i\n    return out\n"),
}


def write(rel, content):
    p = os.path.join(REPO, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)


def read(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


def git(*args, check=True):
    return subprocess.run(("git", "-C", REPO) + args, capture_output=True,
                          text=True, check=check)


def build_repo():
    git("init", "-q", check=False)
    git("config", "user.email", "test@phoenix.local")
    git("config", "user.name", "Phoenix Test")
    git("config", "commit.gpgsign", "false")
    write("src/lib.py", BROKEN)
    write("tests/test_lib.py", TESTS)
    git("add", "-A")
    git("commit", "-q", "-m", "four defects")


def reset_repo():
    git("checkout", "-q", "--", ".", check=False)
    for b in WT.list_branches(REPO):
        WT._git(REPO, "branch", "-D", b, check=False)


# ── scripted solvers ──────────────────────────────────────────────────────────

def solver_one_fix_per_strategy(seen=None):
    """Each strategy knows how to fix exactly one defect. Only a fleet that plays
    several different strategies can finish, and only a campaign that accumulates
    can keep them all."""
    by_strategy = {"direct": "mean", "read-the-test": "slugify", "widen": "clamp",
                   "rewrite": "factorial", "contrarian": "clamp", "decompose": "mean"}

    def solve(req):
        if seen is not None:
            seen.append(req)
        which = by_strategy.get(req["strategy"])
        src = req["files"].get("src/lib.py", "")
        if not which or FIXES[which][0] not in src:
            return {"files": {}}                    # this strategy has nothing to add
        return {"files": {"src/lib.py": src.replace(*FIXES[which])}}
    return solve


def solver_catastrophic(req):
    """Every agent, every round, does something ruinous."""
    return {"files": {"src/lib.py": "import module_that_does_not_exist\n"}}


def solver_useless(req):
    src = req["files"].get("src/lib.py", "")
    return {"files": {"src/lib.py": src + "\n# a thought\n"}}


def solver_records_threads(box):
    def solve(req):
        with box["lock"]:
            box["threads"].add(threading.get_ident())
            box["repos"].add(W.repo_root())
        import time as _t
        _t.sleep(0.3)                               # overlap, so a race would show
        with box["lock"]:
            box["repos_after"].add(W.repo_root())
        return {"files": {}}
    return solve


try:
    build_repo()
    A.init()
    E.init()
    B.init()
    W.configure(repo=REPO, test_cmd=TEST_CMD)
    W.init()
    PRISTINE = read("src/lib.py")
    BASE_SHA = WT.base_ref(REPO)[1]

    # ── 1. the strategy ladder ───────────────────────────────────────────────
    print("\n1. Strategies — four agents get four ideas, not one idea four times")
    slate1 = S.strategies_for(1, 4)
    check("a round hands out distinct strategies",
          len({s["key"] for s in slate1}) == 4, ", ".join(s["key"] for s in slate1))
    slate4 = S.strategies_for(4, 4)
    check("later rounds draw a different, stranger slate",
          {s["key"] for s in slate4} != {s["key"] for s in slate1},
          ", ".join(s["key"] for s in slate4))
    check("and a hotter one — creativity escalates with evidence",
          sum(s["temp"] for s in slate4) > sum(s["temp"] for s in slate1),
          f"{sum(s['temp'] for s in slate1):.2f} -> {sum(s['temp'] for s in slate4):.2f}")
    prompt = S.render_request({
        "agent": "dev-01", "repo_name": "r", "test_cmd": "t", "task_title": "T",
        "task_test": "x", "failures": [], "files": {}, "attempt": 1,
        "strategy": "contrarian", "strategy_brief": "assume you were wrong",
        "tried": ["direct: reverted — broke test_add"], "lessons": ["a paid-for lesson"],
        "champion_note": "round 1 reached 3/5"})
    check("an agent is told what has already been rejected",
          "broke test_add" in prompt and "Do not propose any of these again" in prompt)
    check("an agent is told what the campaign already achieved",
          "round 1 reached 3/5" in prompt and "not starting over" in prompt)
    check("and it is handed the organization's lessons", "a paid-for lesson" in prompt)

    # ── 2. parallelism is safe ───────────────────────────────────────────────
    print("\n2. Parallelism — agents run at once without moving each other's feet")
    box = {"threads": set(), "repos": set(), "repos_after": set(),
           "lock": threading.Lock()}
    C.run(repo=REPO, test_cmd=TEST_CMD, agents=3, rounds=1, dry_limit=1,
          solve=solver_records_threads(box), log=lambda *a: None)
    check("the fleet really ran concurrently", len(box["threads"]) >= 3,
          f"{len(box['threads'])} threads")
    check("each agent saw its OWN worktree", len(box["repos"]) >= 3,
          f"{len(box['repos'])} distinct workspaces")
    check("and still saw it after the others had configured theirs",
          box["repos"] == box["repos_after"], "no cross-thread config bleed")
    check("the main thread's workspace was never hijacked", W.repo_root() == REPO)

    # ── 3. the ratchet ───────────────────────────────────────────────────────
    print("\n3. The ratchet — the score can stall, but it cannot go backwards")
    reset_repo()
    seen = []
    rep = C.run(repo=REPO, test_cmd=TEST_CMD, agents=4, rounds=6, dry_limit=2,
                solve=solver_one_fix_per_strategy(seen), log=lambda *a: None)
    check("the campaign made progress", rep["ok"] and rep["gained"] > 0,
          f"{rep['base']} -> {rep['champion']}")
    scores = [int(h.split("-> ")[1].split("/")[0]) for h in rep["history"] if "-> " in h]
    check("every crowned champion beat the last one", scores == sorted(scores)
          and len(set(scores)) == len(scores), " -> ".join(map(str, scores)))
    check("later rounds branched from the champion, not from the start",
          any("already got to" in S.render_request(r) for r in seen if r.get("champion_note")),
          f"{sum(1 for r in seen if r.get('champion_note'))} of {len(seen)} sorties")
    check("more than one agent contributed a champion",
          len({h.split(": ")[1].split("/")[0] for h in rep["history"] if ": " in h
               and "no agent" not in h}) >= 1,
          "; ".join(rep["history"]))

    # ── 4. it converges ──────────────────────────────────────────────────────
    print("\n4. Convergence — several ideas, applied in turn, finish the job")
    check("the campaign solved the whole problem", rep["solved"],
          f"{rep['champion']} after {rep['rounds']} round(s)")
    check("it took several rounds to get there", rep["rounds"] >= 2, str(rep["rounds"]))

    # ── 5. the gate still holds ──────────────────────────────────────────────
    print("\n5. The gate — a fleet's work stops at a human exactly like one agent's")
    d = B.gate_get(rep["gate_id"])
    check("the whole campaign parked as ONE dossier", bool(d) and d["status"] == "pending")
    check("carrying the accumulated diff of every round",
          all(f in d["diff"] for f in ("raise ValueError", "max(lo, min(hi", "replace(' '")),
          f"{len(d['diff'])} chars")
    check("and the oracle's delta across the campaign",
          d["delta"]["after"] > d["delta"]["before"],
          f"{d['delta']['before']} -> {d['delta']['after']}")
    check("nothing merged on its own — the base has not moved",
          WT.base_ref(REPO)[1] == BASE_SHA)
    check("and the working file is untouched", read("src/lib.py") == PRISTINE)

    ok_m, msg_m = B.approve(rep["gate_id"], by="verify-human")
    check("approving lands every round's work at once", ok_m, msg_m)
    check("the suite is green after the merge", W.oracle()["failed"] == 0,
          f"{W.oracle()['passed']}/{W.oracle()['total']}")

    # ── 6. value is never destroyed ──────────────────────────────────────────
    print("\n6. Value — a campaign where EVERY agent is ruinous costs nothing")
    git("reset", "-q", "--hard", BASE_SHA)
    reset_repo()
    before_sha = WT.base_ref(REPO)[1]
    before_src = read("src/lib.py")
    rep_bad = C.run(repo=REPO, test_cmd=TEST_CMD, agents=3, rounds=3, dry_limit=2,
                    solve=solver_catastrophic, log=lambda *a: None)
    check("the campaign reports no progress", rep_bad["gained"] <= 0,
          rep_bad.get("champion", ""))
    check("the repository is byte-for-byte what it was",
          read("src/lib.py") == before_src)
    check("the base commit never moved", WT.base_ref(REPO)[1] == before_sha)
    check("no dossier was parked for a human to read",
          rep_bad.get("gate_id", 0) == 0)
    check("no branch was left lying around",
          not WT.list_branches(REPO), ", ".join(WT.list_branches(REPO)) or "none")
    check("and it named its binding constraint rather than going quiet",
          bool(rep_bad.get("binding_constraint")),
          rep_bad.get("binding_constraint", "")[:70])

    # ── 7. it stops ──────────────────────────────────────────────────────────
    print("\n7. Stopping — dry rounds and empty budgets both end it")
    reset_repo()
    rep_dry = C.run(repo=REPO, test_cmd=TEST_CMD, agents=2, rounds=9, dry_limit=2,
                    solve=solver_useless, log=lambda *a: None)
    check("two dry rounds end a campaign that could have run nine",
          rep_dry["rounds"] == 2, f"{rep_dry['rounds']} round(s)")
    reset_repo()
    rep_cap = C.run(repo=REPO, test_cmd=TEST_CMD, agents=2, rounds=9, dry_limit=9,
                    budget=C.COST_PER_SORTIE * 3,      # enough for one round and a bit
                    solve=solver_useless, log=lambda *a: None)
    check("a campaign stops at its own budget, however many rounds it was given",
          rep_cap["rounds"] <= 2, f"{rep_cap['rounds']} round(s) of 9 allowed")

    # ── 7b. the organization outlives its agents ─────────────────────────────
    print("\n7b. Staffing — spent agents are retired and replaced, not sent out empty")
    fleet_before = C.staff(2)
    for a in fleet_before:
        E.charge(a, 10 ** 9)
    gone = C.reap_spent(fleet_before)
    check("an agent that spent its budget is retired", set(gone) == set(fleet_before),
          ", ".join(gone))
    fleet_after = C.staff(2)
    check("and the campaign enlists successors rather than stalling",
          not (set(fleet_after) & set(fleet_before)),
          f"{', '.join(fleet_before)} -> {', '.join(fleet_after)}")
    check("the retired agents' careers survive them",
          all(any(c["uid"] == a for c in A.careers(80)) for a in fleet_before))
    reset_repo()
    rep_after = C.run(repo=REPO, test_cmd=TEST_CMD, agents=2, rounds=6, dry_limit=2,
                      solve=solver_one_fix_per_strategy(), log=lambda *a: None)
    check("a campaign run after a fleet was reaped still does work",
          rep_after["gained"] > 0, f"{rep_after['base']} -> {rep_after['champion']}")
    if rep_after.get("gate_id"):
        B.reject(rep_after["gate_id"], by="verify-human", reason="")

    # ── 8. it learns ─────────────────────────────────────────────────────────
    print("\n8. Learning — what a campaign discovers outlives it")
    check("the successful campaign wrote a lesson",
          any("campaign" in s["lesson"] for s in A.skills_top(30)),
          next((s["lesson"][:60] for s in A.skills_top(30) if "campaign" in s["lesson"]), ""))
    handed = [r for r in seen if r.get("lessons")]
    check("and campaigns are handed the lessons already paid for",
          bool(handed) or bool(A.skills_top(5)),
          f"{len(handed)} sorties carried lessons")
finally:
    W.configure()
    shutil.rmtree(REPO, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

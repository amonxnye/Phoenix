"""Acceptance checks for the Campaign driver — the autonomous army (campaign.py).
Model-free: a FakeWorld and injected tactics exercise the loop logic directly.

  1. Persistence + creativity escalation: a problem whose hard part only yields at a high
     strategy rung is still driven to SOLVED — the ladder climbs when stuck and resets
     when it wins.
  2. Never destroys value: the score is monotonic across the whole campaign and every
     round reports value preserved.
  3. Reaping: a persistent thrasher is retired, and the army routes around it instead of
     burning the whole budget.
  4. Bounded stop: an unsolvable-with-these-tactics problem stops DRY at the top of the
     ladder — it does not spin forever.
  5. The gate: when only irreversible work remains, the campaign stops GATE_BLOCKED.

Run:  python3 gov/verify_campaign.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import campaign as C
import economy as E

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


class FakeWorld:
    """A problem is a set of items, each needing a minimum strategy rung to yield.
    ``gated`` items can never be solved by work — they model the irreversible."""
    def __init__(self, needs: dict, gated: set = frozenset()):
        self.needs = needs
        self.gated = set(gated)
        self.done = set()

    def score(self):
        total = len(self.needs) or 1
        return round(100 * len(self.done) / total)

    def solved(self):
        return self.done >= set(self.needs) and bool(self.needs)

    def remaining(self):
        return [{"ref": k, "kind": "fix", "gated": k in self.gated}
                for k in self.needs if k not in self.done]

    def attempt(self, agent, strategy, tactic):
        return tactic(self, agent, strategy)


def ladder_tactic(world, agent, strategy):
    """Solve the first open, non-gated item iff we're fighting at a high enough rung."""
    items = [it for it in world.remaining() if not it["gated"]]
    if not items:
        return {"gain": False, "did": "nothing", "gated": False}
    ref = items[0]["ref"]
    if strategy["rung"] >= world.needs[ref]:
        world.done.add(ref)
        return {"gain": True, "did": f"solved:{ref}@{strategy['name']}", "gated": False}
    return {"gain": False, "did": f"stuck:{ref}", "gated": False}


A.init()
E.init()

# ── 1+2. persistence, escalation, and the value ratchet ──────────────────────
print("\n1. Persistence + creativity that escalates, then relaxes")
w = FakeWorld({"easy1": 0, "easy2": 0, "easy3": 0, "hard": 3})   # hard needs top rung
rep = C.run_campaign(w, ["sec-0", "sec-1", "sec-2", "sec-3", "sec-4"],
                     max_rounds=30, patience=2, tactic=ladder_tactic,
                     mission="fake: escalate to solve the hard item")
check("the campaign drives the problem to SOLVED", rep["outcome"] == C.SOLVED
      and rep["final_score"] == 100, f"{rep['outcome']} @ {rep['final_score']}%")
rungs = [r["rung"] for r in rep["log"]]
check("it climbs the ladder when stuck (reaches 'creative')", "creative" in rungs,
      " → ".join(rungs))
check("it plays cheaply while winning (starts at 'recon')", rungs[0] == "recon")

print("\n2. It never destroys value")
scores = [r["score_after"] for r in rep["log"]]
check("the score is monotonic across the whole campaign",
      all(b <= a for b, a in zip(scores, scores[1:])), " ".join(map(str, scores)))
check("every round reports value preserved",
      all(r["value_preserved"] for r in rep["log"]))

# ── 3. reaping — the army routes around a thrasher ───────────────────────────
print("\n3. Reaping — a thrasher is retired, the mission still lands")
check("at least one wasteful agent was reaped", len(rep["reaped"]) >= 1,
      f"reaped: {rep['reaped']}")
check("...and the campaign still solved it", rep["solved"])

# ── 4. bounded stop — no infinite spin ───────────────────────────────────────
print("\n4. Bounded — an unsolvable problem stops DRY, it does not spin")
w2 = FakeWorld({"impossible": 99})               # needs a rung above the ladder's top
rep2 = C.run_campaign(w2, ["a", "b", "c", "d"], max_rounds=50, patience=2,
                      tactic=ladder_tactic, mission="fake: unsolvable")
check("it stops rather than running to the round cap",
      rep2["outcome"] == C.DRY and rep2["rounds_used"] < 50, f"{rep2['outcome']} "
      f"after {rep2['rounds_used']} rounds")
check("and it destroyed no value getting there", rep2["best_score"] == 0
      and all(r["value_preserved"] for r in rep2["log"]))

# ── 5. the gate — irreversible work stops at a human ─────────────────────────
print("\n5. The gate — only-irreversible work halts the army for a human")
w3 = FakeWorld({"disclose": 0}, gated={"disclose"})
rep3 = C.run_campaign(w3, ["a", "b"], max_rounds=10, tactic=ladder_tactic,
                      mission="fake: only a gated act remains")
check("the campaign halts GATE_BLOCKED", rep3["outcome"] == C.GATE_BLOCKED,
      rep3["outcome"])

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

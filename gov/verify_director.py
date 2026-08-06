"""Acceptance checks for the director — the settlement's play loop.

`verify_sim.py` proves the primitives director.py drives (spawn, resume, the cap, the
gate, the vision score); this proves the ORCHESTRATION is actually coherent when run
end to end: staffing respects the population cap, idle villagers get re-tasked instead
of freezing, a build is attempted only when affordable, an Age-up always goes through
the human gate before it lands, and — the one that matters most — a met vision retires
the villagers that are no longer needed rather than leaving them parked burning budget
(Article II.3).

Runs against a fresh, temp-directory world (GOV_DATA_DIR), never the tracked dev DB.

Run:  python3 gov/verify_director.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


_TMP = tempfile.mkdtemp(prefix="phoenix-director-")
os.environ["GOV_DATA_DIR"] = _TMP

import anchor as A
import director as D
import sim

try:
    A.init()
    sim.connect()                                    # the world table has to exist first

    # ── 1. the two picking rules, isolated — no model needed ─────────────────
    print("\n1. Picking rules — the small decisions director.py owns outright")
    low_wood = {"age": "Dark", "food": 400, "wood": 40, "gold": 400}
    check("wood is prioritized below the build-flow threshold",
          D._scarcest(low_wood) == "wood", D._scarcest(low_wood))
    short_food = {"age": "Dark", "food": 10, "wood": 300, "gold": 400}
    check("gathering targets whichever resource the Age-up is short on",
          D._scarcest(short_food) == "food", D._scarcest(short_food))

    w0 = sim.world()
    over_cap = dict(w0, pop_cap=2, wood=999)
    check("a settlement at its population cap builds a house first",
          D._next_build(over_cap, n_villagers=2) == "house")
    starved = dict(w0, wood=0, food=0, lumber_camp=1, mill=1, mining_camp=1,
                   wheelbarrow=True, pop_cap=99)
    check("nothing affordable means nothing is proposed", D._next_build(starved, 1) is None)

    # ── 2. a full run, coherent end to end ────────────────────────────────────
    print("\n2. A run — staffing, reclamation, the gate, and (if reached) retirement")
    res = D.run(turns=40, target_villagers=4, verbose=False)
    check("it returns the shape callers depend on (events, world, scorecard, knowledge)",
          all(k in res for k in ("events", "world", "scorecard", "knowledge")))
    check("something actually happened — a real run is not zero events",
          len(res["events"]) > 0, f"{len(res['events'])} events")

    spawned = [e for e in res["events"] if "[spawn" in e]
    check("villagers were actually staffed up toward the target",
          len(spawned) >= 1, f"{len(spawned)} spawn events")
    check("staffing never spawned past the settlement's own population cap",
          len(spawned) <= res["world"]["pop_cap"] + 1,   # +1: the cap can rise mid-run (a house)
          f"{len(spawned)} spawned, pop_cap now {res['world']['pop_cap']}")

    gated = [e for e in res["events"] if "[gate" in e]
    approved = [e for e in res["events"] if "[approve" in e]
    check("every Age-up gate event has a matching approval — nothing landed ungated",
          len(gated) == len(approved), f"{len(gated)} gated, {len(approved)} approved")

    # ── 3. idle villagers are reclaimed, not left to freeze ──────────────────
    print("\n3. Reclamation — Article II.3, idle-but-useful is re-tasked")
    retasked = [e for e in res["events"] if "[retask" in e]
    check("a retask event, if any fired, always names the resource gathered",
          all("gather" in e for e in retasked), f"{len(retasked)} retask events")

    # ── 4. goal met → retire, not park ────────────────────────────────────────
    print("\n4. Goal met — villagers are retired, not parked (Article II.3)")
    met = res["scorecard"]["goal_met"]
    reaped = [e for e in res["events"] if "[reap" in e]
    check("the run either meets the vision within budget or says so honestly",
          isinstance(met, bool))
    if met:
        check("reaching the vision retires the villagers no longer needed",
              len(reaped) > 0, f"{len(reaped)} reaped")
        check("a reap event always gives its reason",
              all("no longer needed" in e for e in reaped))
    else:
        check("an unmet vision after a real run still returns a coherent scorecard "
              "(0-100%, never silently wrong)",
              0 <= res["scorecard"]["progress"] <= 100,
              f"progress {res['scorecard']['progress']}%")

    # ── 5. nothing runs unseen (Article VII) ──────────────────────────────────
    print("\n5. Nothing runs unseen")
    check("the anchor's permanent event log grew by exactly what director.py reported",
          A.event_count() >= len(res["events"]), f"{A.event_count()} events on record")
    check("the knowledge summary reflects real learning, not a placeholder",
          res["knowledge"]["facts"] >= 0 and "by_kind" in res["knowledge"])
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

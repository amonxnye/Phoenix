"""Acceptance checks for the Logistics world — a governed supply network.

Proves LOGISTICS.md steps 1-4 without needing any model:
  1. The oracle is deterministic, and the holdout it scores on is NOT readable.
  2. A sane reorder-point policy beats doing nothing.
  3. An absurd policy (order everything) wins on service and LOSES on capital — the
     score cannot be gamed on one axis.
  4. Disruption scenarios are a second tier: every plan is judged on its worst day.
  5. The v1 bar: twenty policies proposed and scored on held-out demand AND every
     disruption, the best beating the naive baseline on both service and capital.
  6. Contribution is measured by the oracle, and careers and lineage record it.
  7. The gate: a commitment is a dossier priced by its cost of waiting, tacit consent
     is refused outside the pre-approved envelope, and no order can ever be placed.

Run:  python3 gov/verify_logistics.py
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import board as B
import economy as E
import logistics_world as L
import planner as P

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        p = L.DB + suffix
        if os.path.exists(p):
            os.remove(p)


A.init()
E.init()
fresh_db()
L.init()

# ── 1. the oracle ─────────────────────────────────────────────────────────────
print("\n1. Oracle — the simulator is the score, and the holdout is not readable")
naive = L.naive_reorder_point()
v1 = L.oracle(naive)
v2 = L.oracle(L.naive_reorder_point())
check("the same policy scores identically twice (deterministic, fixed seed)",
      json.dumps(v1, sort_keys=True) == json.dumps(v2, sort_keys=True),
      f"score {v1['score']}")
check("the score is a JOINT scorecard, not one number in a trench coat",
      {"fill_rate", "working_capital", "expedite_spend", "waste_cost",
       "a_list_fill"} <= set(v1["nominal"]),
      f"fill {v1['nominal']['fill_rate']:.1%}, capital "
      f"${v1['nominal']['working_capital']:,.0f}")

train = L.demand_history()
check("the readable window is the training period only",
      all(len(s) == L.TRAIN[1] - L.TRAIN[0] + 1 for s in train.values()),
      f"days {L.TRAIN[0]}-{L.TRAIN[1]}")
try:
    L.demand_history(window=L.HOLDOUT)
    refused = False
except ValueError as e:
    refused = "REFUSED" in str(e)
check("the holdout demand is REFUSED to any reader", refused,
      "a planner that can read its exam is not being examined")
prompt = P.prompt_for({"policy": naive, "score": v1["score"], "id": 0})
holdout_numbers = [str(v1["nominal"]["working_capital"]),
                   f"{v1['nominal']['fill_rate']:.1%}",
                   str(v1["nominal"]["waste_cost"])]
check("the planner's prompt carries no holdout figures",
      not any(h in prompt for h in holdout_numbers),
      "built from train_stats() and the train-window score alone")

# Stronger than "it doesn't mention them": rewrite the holdout underneath the planner
# and prove the prompt is byte-identical, while the score is not. The split is real in
# both directions — the planner cannot see the exam, and the exam is what marks it.
_real_demand = L.demand
try:
    L.demand = lambda sku, t, scenario=None: (
        9_999 if t > L.TRAIN[1] else _real_demand(sku, t, scenario))
    prompt_b = P.prompt_for({"policy": naive, "score": v1["score"], "id": 0})
    score_b = L.oracle(naive, with_scenarios=False)["score"]
finally:
    L.demand = _real_demand
check("replacing the entire holdout leaves the prompt byte-identical", prompt_b == prompt,
      f"{len(prompt)} bytes, unchanged — nothing downstream of TRAIN reaches a model")
check("...and moves the score, so the holdout is what actually marks the plan",
      score_b != v1["nominal"]["score"],
      f"{v1['nominal']['score']:.1f} → {score_b:.1f} on rewritten demand")

# ── 2. a sane policy beats doing nothing ──────────────────────────────────────
print("\n2. The floor — a sane reorder point beats doing nothing")
nothing = L.oracle(L.do_nothing())
check("the naive reorder point beats do-nothing", v1["score"] > nothing["score"],
      f"{v1['score']:.1f} vs {nothing['score']:.1f}")
check("do-nothing is punished where it actually fails — service and emergency freight",
      nothing["nominal"]["fill_rate"] < v1["nominal"]["fill_rate"]
      and nothing["nominal"]["expedite_spend"] > v1["nominal"]["expedite_spend"],
      f"fill {nothing['nominal']['fill_rate']:.1%}, freight "
      f"${nothing['nominal']['expedite_spend']:,.0f}")

# ── 3. the score cannot be gamed on one axis ──────────────────────────────────
print("\n3. Joint scoring — one axis cannot buy off the others")
absurd = L.oracle(L.order_everything())
check("'order everything' WINS on service",
      absurd["nominal"]["fill_rate"] >= v1["nominal"]["fill_rate"],
      f"fill {absurd['nominal']['fill_rate']:.1%}")
check("'order everything' LOSES on capital and waste",
      absurd["nominal"]["working_capital"] > v1["nominal"]["working_capital"]
      and absurd["nominal"]["waste_cost"] > v1["nominal"]["waste_cost"],
      f"capital ${absurd['nominal']['working_capital']:,.0f}, waste "
      f"${absurd['nominal']['waste_cost']:,.0f}")
check("and so it scores WORSE overall than the sane baseline",
      absurd["score"] < v1["score"], f"{absurd['score']:.1f} vs {v1['score']:.1f}")

# ── 4. disruption scenarios: the second oracle tier ───────────────────────────
print("\n4. Robustness — every plan is judged on its worst day, not its average")
check("every disruption scenario is scored", set(v1["scenarios"]) == set(L.SCENARIOS),
      ", ".join(sorted(L.SCENARIOS)))
check("the verdict is worse than the nominal run when a disruption bites",
      v1["robust_score"] <= v1["nominal"]["score"] and v1["score"] <= v1["nominal"]["score"],
      f"nominal {v1['nominal']['score']:.1f} → worst {v1['robust_score']:.1f} "
      f"({v1['worst']})")
lane = L.simulate(naive, L.HOLDOUT, L.SCENARIOS["lane_closed"])
check("a closed lane costs service the plan did not budget for",
      lane["fill_rate"] < v1["nominal"]["fill_rate"],
      f"fill {v1['nominal']['fill_rate']:.1%} → {lane['fill_rate']:.1%}")

# ── 5. the v1 bar ─────────────────────────────────────────────────────────────
print(f"\n5. The planner — {P.SEARCH_ROUNDS} policies proposed, scored, and paid for")
E.enlist("plan-test")              # a fresh life for the test agent: the ledger persists
t0 = time.time()                   # across runs, this suite's arithmetic should not
run = P.run_search("plan-test", P.SEARCH_ROUNDS)
check(f"{P.SEARCH_ROUNDS} policies proposed and scored on the holdout",
      L.policy_count() >= P.SEARCH_ROUNDS,
      f"{L.policy_count()} on the board in {time.time() - t0:.1f}s")
best = L.incumbent()["verdict"]["nominal"]
check("the best beats the naive baseline on SERVICE", run["beats_baseline_on_service"],
      f"fill {v1['nominal']['fill_rate']:.1%} → {best['fill_rate']:.1%}")
check("the best beats the naive baseline on CAPITAL", run["beats_baseline_on_capital"],
      f"${v1['nominal']['working_capital']:,.0f} → ${best['working_capital']:,.0f}")
check("and it is still standing after the worst disruption",
      L.incumbent()["robust_score"] > nothing["score"],
      f"worst-case {L.incumbent()['robust_score']:.1f}")
check("the mandate is reachable, and this plan reaches it", run["mandate_met"],
      f"fill ≥ {L.TARGET_FILL:.0%}, capital ≤ ${L.CAPITAL_CEILING:,.0f}, A-list fill "
      f"{best['a_list_fill']:.1%} ≥ {L.A_LIST_FILL_FLOOR:.0%}")

# ── 6. the economy: paid for what the oracle measured ─────────────────────────
print("\n6. Economy — contribution is measured, never claimed")
paid = next((r for r in E.roster(alive_only=False) if r["agent"] == "plan-test"), None)
check("the planner is paid in measured score, not in proposals",
      paid is not None and paid["contribution"] == run["contribution"] > 0,
      f"+{run['contribution']} over {run['adopted']} adopted of {run['proposed']}")
check("a policy that does not beat the incumbent is never adopted",
      run["adopted"] < run["proposed"],
      f"{run['proposed'] - run['adopted']} proposals rejected")
life = next((c for c in A.careers(50) if c["uid"] == "plan-test"), None)
check("the career records the shipped plan", life is not None
      and any(e["event"] == "work" for e in life["events"]))
lin = A.lineage(run["last"]["decision_id"]) if run.get("last") else {}
check("the adopted policy walks back to the plan it replaced", bool(lin.get("decision")),
      "why do we hold this stock? → the decision, its evidence, its predecessor")

# ── 7. the gate ───────────────────────────────────────────────────────────────
print("\n7. The gate — the purchase order stops at a human")
d = P.commit("plan-test", "FRESH-01")
check("a commitment is a DOSSIER, not an order",
      {"qty", "vendor", "value", "forecast", "rollback", "cancel_fee"} <= set(d),
      f"{d['qty']}× {d['sku']} from {d['vendor']} (${d['value']:,.0f})")
check("it carries the rollback: what can be undone, by when, at what fee",
      "cancellable" in d["rollback"] and d["cancel_fee"] > 0,
      f"${d['cancel_fee']:,.2f} cancellation fee")
check("it is priced by its cost of waiting (Article IV.4)", d["wait_cost_per_day"] > 0,
      f"${d['wait_cost_per_day']:,.2f}/day of silence")
check("the board voted on it, from disjoint logistics evidence",
      set(d["board"]["ballots"]) == set(B.GOVERNORS),
      "; ".join(f"{g}: {r[:38]}" for g, r in d["board"]["reasons"].items()))
check("it is parked, not done", d["status"] == "pending"
      and any(c["id"] == d["id"] for c in L.commitments("pending")))

os.environ.pop("GOV_LOGISTICS_ENVELOPE", None)
check("with no pre-approved envelope, tacit consent is refused",
      not d["tacit_consent_eligible"], d["tacit_consent_why"])
c = L._conn()                      # age it past the hour: the IV.7 clock, without waiting
try:
    c.execute("UPDATE commitments SET ts=? WHERE id=?",
              (time.time() - L.TACIT_CONSENT_S - 60, d["id"]))
    c.commit()
finally:
    c.close()
swept = P.sweep()
check("an hour of silence still does not buy anything outside the envelope",
      swept and swept[0]["action"] == "still waiting"
      and L.commitments()[0]["status"] == "pending", swept[0]["why"] if swept else "")
waited = next(x for x in L.commitments() if x["id"] == d["id"])
check("and the price of that silence is on the record and rising",
      waited["cost_of_waiting"] > 0,
      f"${waited['cost_of_waiting']:,.2f} after {waited['waited_days']:.2f} days")

os.environ["GOV_LOGISTICS_ENVELOPE"] = json.dumps(
    {"skus": ["FRESH-01"], "vendors": ["AgriCo"], "max_value": d["value"] + 1})
swept2 = P.sweep()
check("inside an envelope the human set, the board may take it by tacit consent (IV.7)",
      swept2 and swept2[0]["action"] in ("approved by tacit consent", "stood down"),
      f"{swept2[0]['action']} — board {swept2[0].get('tally', '-')}")
os.environ.pop("GOV_LOGISTICS_ENVELOPE", None)

d2 = P.commit("plan-test", "STAPLE-02")
ok_dec, msg = L.decide(d2["id"], "approve", "human@phoenix", "looks right")
placed, why = L.place_order(d2["id"])
check("even an APPROVED commitment cannot be placed by the system", not placed,
      why[:72])
check("because the capability does not exist at all (Article V)",
      L.PROCUREMENT_ADAPTER is None and ok_dec,
      f"{msg} — but no ERP client, no vendor credential, no path to acquire one")
check("a decided commitment cannot be decided twice",
      not L.decide(d2["id"], "reject", "someone-else")[0])

print(f"\n{sum(results)}/{len(results)} checks passed\n")
print(L.render_world())
sys.exit(0 if all(results) else 1)

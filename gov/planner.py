"""The planner agent — a villager whose resource is service level (LOGISTICS.md §5.2).

One planning cycle: read the READABLE past, propose one replenishment policy through
the ONE brain seam, and let the simulator score it on demand the planner never saw.
Measured contribution = points of the joint score won on the holdout — fed into the
SAME economy (credit, promotion, budget, reap) and the SAME permanent record (careers,
lineage) as the game fleet and the code fleet.

Three disciplines are enforced here rather than asked for:

  * **The holdout is unreadable.** The prompt is built from ``train_stats()`` and the
    incumbent's TRAIN-window score alone. Holdout numbers exist only after the policy
    is sealed, and go to the anchor — never back into a prompt. ``prompt_for`` is a
    pure function so the acceptance suite can check that, every run.
  * **Adopting a policy is reversible, so it runs free.** It moves no goods and spends
    no money. Only a purchase order stops at the gate.
  * **The planner cannot buy anything.** ``commit`` builds a dossier and parks it for a
    human; there is no procurement capability in the sandbox to grant it (Article V).

Run one cycle by hand:   python3 gov/planner.py --agent plan-01
Run the search with no model at all:  python3 gov/planner.py --agent plan-01 --rounds 20
Park a commitment for a human:        python3 gov/planner.py --agent plan-01 --commit FRESH-01
"""

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import board
import brain
import economy
import logistics_world as L

CREDIT_PER_POINT = 40        # contribution per point of joint score won on the holdout
SEARCH_ROUNDS = 20           # the v1 bar: twenty policies proposed and scored


# ── the incumbent: the plan we are actually running ───────────────────────────

def ensure_incumbent() -> dict:
    """Every planner is measured against a plan that already works. The naive
    reorder-point policy is seeded as policy #1 so the first proposal has to beat a
    real baseline, not an empty table."""
    L.init()
    inc = L.incumbent()
    if inc:
        return inc
    base = L.naive_reorder_point()
    pid = L.record_policy("baseline", base, L.oracle(base),
                          note="naive reorder point — lead-time cover + two weeks")
    L.adopt(pid)
    return L.incumbent()


# ── the proposal ──────────────────────────────────────────────────────────────

def prompt_for(incumbent: dict) -> str:
    """Everything the planner is allowed to know. Built ONLY from the readable window,
    so a model cannot fit the period it is scored on (LOGISTICS.md §2.1)."""
    train = L.simulate(incumbent["policy"], L.TRAIN)
    stats = L.train_stats()
    return (
        "You are a supply chain planner. Propose ONE replenishment policy for a two-node "
        "network: a vendor ships to the DC, the DC transfers to the STORE, and all demand "
        "lands at the STORE.\n\n"
        f"Each SKU at each node has a reorder point s and an order-up-to level S.\n"
        f"DC → store transfer lead time: {L.DC_LEAD} days. Storage caps (units): "
        f"{json.dumps(L.NODE_CAPACITY)}.\n"
        f"Stock older than its shelf life is scrapped. A stockout on an A-list SKU "
        f"triggers emergency freight, which is expensive and arrives a day late.\n\n"
        f"The mandate: fill rate ≥ {L.TARGET_FILL:.0%}, average working capital ≤ "
        f"${L.CAPITAL_CEILING:,.0f}, zero stockouts on the A-list "
        f"({', '.join(L.A_LIST)}), waste under ${L.WASTE_BUDGET:,.0f} and emergency "
        f"freight under ${L.EXPEDITE_BUDGET:,.0f}.\n\n"
        f"--- what the readable history says (days {L.TRAIN[0]}-{L.TRAIN[1]}) ---\n"
        f"{json.dumps(stats, indent=1)}\n\n"
        f"--- the policy running today ---\n{json.dumps(incumbent['policy'], indent=1)}\n"
        f"On the readable window it delivers fill {train['fill_rate']:.1%}, working "
        f"capital ${train['working_capital']:,.0f}, waste ${train['waste_cost']:,.0f}, "
        f"emergency freight ${train['expedite_spend']:,.0f}.\n"
        f"It will be judged on a later period you cannot see, and on disruption "
        f"scenarios: {', '.join(s['label'] for s in L.SCENARIOS.values())}.\n\n"
        "Reply with ONLY a JSON object of the form "
        '{"SKU": {"dc": {"s": 0, "S": 0}, "store": {"s": 0, "S": 0}}, ...} '
        "covering every SKU. No prose, no fences."
    )


Z_STEPS = (-0.6, -0.3, -0.15, 0.15, 0.3, 0.6)          # safety-stock moves
COVER_STEPS = (-3.0, -1.5, -0.5, 0.5, 1.5, 3.0)        # order-quantity moves


def propose_rule_based(incumbent: dict, round_no: int) -> tuple[dict, dict]:
    """The no-model proposer: inventory theory, then coordinate search.

    Round 0 lays down the textbook plan — safety stock from the observed variability,
    order quantity capped under the shelf life. Every later round moves ONE SKU at one
    node, so a good move is not buried under seven bad ones. It exists so the whole
    machinery — score, economy, careers, lineage, gate — is provable with no API key at
    all, exactly as verify_work.py injects a scripted patch. Returns (policy, knobs).
    """
    if round_no == 0 or not incumbent.get("knobs"):
        knobs = L.textbook_knobs()
        if round_no == 0:
            return L.build_policy(knobs), knobs
    else:
        knobs = {sku: {n: dict(incumbent["knobs"][sku][n]) for n in L.NODES}
                 for sku in L.SKUS}
    coords = [(sku, node) for sku in L.SKUS for node in L.NODES]
    sku, node = coords[round_no % len(coords)]
    r = random.Random(f"{L.SEED}|search|{round_no}")
    k = knobs[sku][node]
    knobs[sku][node] = {"z": max(0.0, k["z"] + r.choice(Z_STEPS)),
                        "cover": max(0.5, k["cover"] + r.choice(COVER_STEPS))}
    return L.build_policy(knobs), knobs


def propose(incumbent: dict, round_no: int = 0) -> tuple[dict, dict, str]:
    """Ask the model if one is configured; fall back to the search if it is not, or if
    it answers with something that is not a policy. Returns (policy, knobs, how)."""
    if not brain.available():
        return (*propose_rule_based(incumbent, round_no), "search")
    try:
        raw = brain._chat([{"role": "user", "content": prompt_for(incumbent)}],
                          1200, 0.4, "planner-policy")
    except Exception as e:
        anchor.record(-1, "error", f"planner could not reach the model: {str(e)[:120]}")
        return (*propose_rule_based(incumbent, round_no), "search (model unreachable)")
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:]
    start, end = text.find("{"), text.rfind("}")
    try:
        got = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return (*propose_rule_based(incumbent, round_no), "search (unparseable reply)")
    return L.normalize_policy(got), {}, "model"


# ── the scored cycle ──────────────────────────────────────────────────────────

def evaluate_and_score(agent: str, policy: dict, how: str = "search",
                       knobs: dict | None = None) -> dict:
    """Seal the policy, score it on held-out demand AND every disruption, and pay for
    what the oracle measured — never for what the planner claimed. Split out so the
    machinery is testable without a model."""
    inc = ensure_incumbent()
    verdict = L.oracle(policy)
    n = verdict["nominal"]
    gain = round(verdict["score"] - inc["score"], 2)
    pid = L.record_policy(agent, policy, verdict, note=how, knobs=knobs)

    if gain <= 0:
        anchor.record(-1, "waste", f"{agent} policy #{pid} scored {verdict['score']:.1f} "
                                   f"vs incumbent {inc['score']:.1f} — not adopted")
        return {"agent": agent, "did": "rejected", "policy_id": pid,
                "score": verdict["score"], "incumbent_score": inc["score"], "gain": gain}

    if not economy.enlisted(agent):     # a planner lives across many cycles; enlisting
        economy.enlist(agent)           # twice would wipe the contribution it earned
    got = max(0, int(round(CREDIT_PER_POINT * gain)))
    if got:
        economy.credit(agent, got)
    L.adopt(pid)
    did = anchor.reason_add(
        -1, agent, f"adopt policy #{pid}",
        f"held-out fill {n['fill_rate']:.1%} at ${n['working_capital']:,.0f} working "
        f"capital; worst disruption {verdict['robust_score']:.1f} "
        f"({verdict['worst'] or 'none'}); joint score {inc['score']:.1f} → "
        f"{verdict['score']:.1f}",
        derived_from=[f"policy:{inc['id']}"], authorized_by="policy")
    ev = anchor.record(-1, "work",
                       f"{agent} adopted policy #{pid} — score {inc['score']:.1f}→"
                       f"{verdict['score']:.1f}, fill {n['fill_rate']:.1%}, capital "
                       f"${n['working_capital']:,.0f}, mandate "
                       f"{'MET' if n['mandate_met'] else 'unmet'}")
    anchor.decision_close(did, ev, outcome=f"+{got} contribution; robust "
                                           f"{verdict['robust_score']:.1f}")
    anchor.career_add(agent, -1, "work", f"policy #{pid}: +{gain:.1f} score on the "
                                         f"holdout (+{got} contribution)")
    _learn(n)
    promo = economy.evaluate(agent)
    if promo:
        anchor.record(-1, "promote", f"{agent} promoted -> {promo}")
        anchor.career_add(agent, -1, "promote", "earned promotion by measured service")
    return {"agent": agent, "did": "adopted", "policy_id": pid, "how": how,
            "score": verdict["score"], "incumbent_score": inc["score"], "gain": gain,
            "contribution": got, "fill_rate": n["fill_rate"],
            "working_capital": n["working_capital"],
            "robust_score": verdict["robust_score"], "worst": verdict["worst"],
            "mandate_met": n["mandate_met"], "promoted": promo, "decision_id": did}


def _learn(n: dict) -> None:
    """A lesson outlives the agent that paid for it (Article VI). Only the two failures
    this domain actually repeats are worth the anchor's space."""
    if n["waste_cost"] > L.WASTE_BUDGET:
        anchor.skill_add(-1, f"cover beyond a SKU's shelf life is not safety stock, it is "
                             f"waste — ${n['waste_cost']:,.0f} scrapped in the holdout",
                         source="logistics oracle", trigger="waste over budget")
    if n["expedite_spend"] > L.EXPEDITE_BUDGET:
        anchor.skill_add(-1, f"emergency freight lands a day late: it pays for the next "
                             f"sale, never the lost one (${n['expedite_spend']:,.0f} spent)",
                         source="logistics oracle", trigger="expedite over budget")


def plan_cycle(agent: str, round_no: int = 0) -> dict:
    inc = ensure_incumbent()
    policy, knobs, how = propose(inc, round_no)
    return evaluate_and_score(agent, policy, how, knobs)


def run_search(agent: str, rounds: int = SEARCH_ROUNDS) -> dict:
    """The v1 bar: propose and score `rounds` policies, keeping whatever beats the
    incumbent. Deterministic and model-free by default."""
    adopted, best = [], None
    for i in range(rounds):
        r = plan_cycle(agent, i)
        if r["did"] == "adopted":
            adopted.append(r)
            best = r
    inc = L.incumbent()
    base = L.oracle(L.naive_reorder_point())["nominal"]
    n = inc["verdict"]["nominal"]
    return {"agent": agent, "proposed": rounds, "adopted": len(adopted),
            "best_policy_id": inc["id"], "score": inc["score"],
            "robust_score": inc["robust_score"],
            "beats_baseline_on_service": n["fill_rate"] > base["fill_rate"],
            "beats_baseline_on_capital": n["working_capital"] < base["working_capital"],
            "mandate_met": n["mandate_met"],
            "contribution": sum(a["contribution"] for a in adopted),
            "last": best}


# ── the board, reading logistics evidence ─────────────────────────────────────

def board_context(value: float = 0.0) -> dict:
    """The same three governors, the same disjoint-evidence rule, this domain's numbers:
    Ledger reads working capital against the ceiling, Prudence reads the waste and
    freight budgets, Growth reads whether service is actually moving (Article VIII)."""
    inc = ensure_incumbent()
    n = inc["verdict"]["nominal"]
    wc = n["working_capital"]
    daily_burn = (n["waste_cost"] + n["expedite_spend"] + n["holding_cost"]) / \
        max(n["periods"], 1)
    hist = L.leaderboard(50)
    prev = [p["score"] for p in hist if p["id"] != inc["id"]]
    return {
        "affordable": (wc + value) <= L.CAPITAL_CEILING,
        "spent": int(wc), "cap": int(L.CAPITAL_CEILING),
        "burn_per_turn": int(daily_burn) or 1,
        "within_budget": (n["waste_cost"] <= L.WASTE_BUDGET
                          and n["expedite_spend"] <= L.EXPEDITE_BUDGET),
        "progress_delta": int(round(inc["score"] - max(prev))) if prev else None,
        "understaffed": n["fill_rate"] < L.TARGET_FILL,
    }


# ── the gate ──────────────────────────────────────────────────────────────────

def commit(agent: str, sku: str, node: str = "dc", qty: int = 0) -> dict:
    """Package one irreversible act — a purchase order — with the evidence behind it and
    PARK it for a human. The board votes first, on disjoint logistics evidence, and its
    tally rides with the dossier; a board NO does not stop the human seeing it, because
    Article VIII.4 forbids withholding the fact that a power was blocked.

    Nothing this function returns can move goods. ``L.place_order`` refuses always."""
    inc = ensure_incumbent()
    if qty <= 0:
        # The policy's own cycle quantity (order-up-to minus reorder point), floored at
        # the lead-time cover — nobody raises a purchase order for less than the time it
        # takes to arrive.
        p = inc["policy"][sku][node]
        lead = L.SKUS[sku]["lead"] if node == "dc" else L.DC_LEAD
        qty = max(p["S"] - p["s"], int(round(L.train_stats()[sku]["mean_daily"] * lead)))
    value = qty * L.SKUS[sku]["cost"]
    v = board.vote(f"commit {qty}× {sku} from {L.SKUS[sku]['vendor']} (${value:,.0f})",
                   board_context(value))
    did = anchor.reason_add(
        -1, agent, f"propose PO: {qty}× {sku}",
        f"policy #{inc['id']} orders up to {inc['policy'][sku][node]['S']} at the {node}; "
        f"board {v['tally']} — " + "; ".join(f"{g}: {r}" for g, r in v["reasons"].items()),
        derived_from=[f"policy:{inc['id']}"],
        authorized_by="human" if not v["approved"] else "board")
    d = L.propose_commitment(agent, sku, qty, node, decision_id=did)
    d["board"] = v
    ok, why = L.within_envelope(d)
    d["tacit_consent_eligible"] = ok
    d["tacit_consent_why"] = why
    anchor.record(-1, "gate", f"{agent} parked a commitment: {qty}× {sku} from "
                              f"{d['vendor']} (${d['value']:,.0f}) — board {v['tally']}, "
                              f"waiting costs ${d['wait_cost_per_day']:,.2f}/day")
    return d


def sweep() -> list[dict]:
    """Article IV.7 in this domain: an hour of silence sends a parked commitment back to
    the Board — but only inside the human's pre-approved envelope, which is empty by
    default. Outside it, silence never buys anything."""
    return L.tacit_sweep(board.vote, board_context())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="plan-01")
    ap.add_argument("--rounds", type=int, default=0,
                    help="run the model-free search for N rounds instead of one cycle")
    ap.add_argument("--commit", default="", metavar="SKU",
                    help="park a purchase-order dossier for this SKU")
    ap.add_argument("--sweep", action="store_true", help="run the Article IV.7 sweep")
    a = ap.parse_args()
    anchor.init()
    economy.init()
    L.init()
    if a.commit:
        print(json.dumps(commit(a.agent, a.commit), indent=2, default=str))
    elif a.sweep:
        print(json.dumps(sweep(), indent=2))
    elif a.rounds:
        print(json.dumps(run_search(a.agent, a.rounds), indent=2))
    else:
        print(json.dumps(plan_cycle(a.agent), indent=2))
    print("\n" + L.render_world())

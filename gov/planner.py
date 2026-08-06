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
import time

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
        f"{L.m(L.CAPITAL_CEILING)}, A-list fill ≥ {L.A_LIST_FILL_FLOOR:.0%} "
        f"({', '.join(L.A_LIST)}), waste under {L.m(L.WASTE_BUDGET)} and emergency "
        f"freight under {L.m(L.EXPEDITE_BUDGET)}.\n\n"
        f"--- what the readable history says (days {L.TRAIN[0]}-{L.TRAIN[1]}) ---\n"
        f"{json.dumps(stats, indent=1)}\n\n"
        f"--- the policy running today ---\n{json.dumps(incumbent['policy'], indent=1)}\n"
        f"On the readable window it delivers fill {train['fill_rate']:.1%}, working "
        f"capital {L.m(train['working_capital'])}, waste {L.m(train['waste_cost'])}, "
        f"emergency freight {L.m(train['expedite_spend'])}.\n"
        f"It will be judged on a later period you cannot see, and on disruption "
        f"scenarios: {', '.join(s['label'] for s in L.SCENARIOS.values())}.\n\n"
        + (f"--- what this fleet has already paid to learn ---\n"
           f"{lessons_for_prompt()}\n\n" if lessons_for_prompt() else "")
        + f"The thinnest axis right now: {binding_constraint()['why']}.\n\n"
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
        f"held-out fill {n['fill_rate']:.1%} at {L.m(n['working_capital'])} working "
        f"capital; worst disruption {verdict['robust_score']:.1f} "
        f"({verdict['worst'] or 'none'}); joint score {inc['score']:.1f} → "
        f"{verdict['score']:.1f}",
        derived_from=[f"policy:{inc['id']}"], authorized_by="policy")
    ev = anchor.record(-1, "work",
                       f"{agent} adopted policy #{pid} — score {inc['score']:.1f}→"
                       f"{verdict['score']:.1f}, fill {n['fill_rate']:.1%}, capital "
                       f"{L.m(n['working_capital'])}, mandate "
                       f"{'MET' if n['mandate_met'] else 'unmet'}")
    anchor.decision_close(did, ev, outcome=f"+{got} contribution; robust "
                                           f"{verdict['robust_score']:.1f}")
    anchor.career_add(agent, -1, "work", f"policy #{pid}: +{gain:.1f} score on the "
                                         f"holdout (+{got} contribution)")
    _learn(n, inc["verdict"]["nominal"])
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


def _learn(n: dict, previous: dict | None = None) -> None:
    """A lesson outlives the agent that paid for it (Article VI).

    The settlement's rule is the one worth copying: a lesson is only worth storing if
    it would change a later decision, and it must carry the evidence that bought it.
    Every lesson below is written from the ORACLE'S numbers at the moment they were
    measured — none is a maxim someone typed in — and `lessons_for_prompt` reads them
    back into the next proposal, because a lesson nobody reads is a diary entry.
    """
    if n["waste_cost"] > L.WASTE_BUDGET:
        anchor.skill_add(-1, f"cover beyond a SKU's shelf life is not safety stock, it is "
                             f"waste — {L.m(n['waste_cost'])} scrapped in the holdout",
                         source="logistics oracle", trigger="waste over budget")
    if n["expedite_spend"] > L.EXPEDITE_BUDGET:
        anchor.skill_add(-1, f"emergency freight lands a day late: it pays for the next "
                             f"sale, never the lost one ({L.m(n['expedite_spend'])} spent)",
                         source="logistics oracle", trigger="expedite over budget")
    if n["working_capital"] > L.CAPITAL_CEILING and n["fill_rate"] >= L.TARGET_FILL:
        anchor.skill_add(-1, f"service was already met at {n['fill_rate']:.1%} — the "
                             f"{L.m(n['working_capital'] - L.CAPITAL_CEILING)} above the "
                             f"ceiling bought nothing the scorecard rewards",
                         source="logistics oracle", trigger="capital over ceiling")
    if n["a_list_fill"] < L.A_LIST_FILL_FLOOR <= n["fill_rate"] / 1.0:
        anchor.skill_add(-1, f"the average fill hid the A-list: {n['a_list_fill']:.1%} on "
                             f"the lines that matter against {n['fill_rate']:.1%} overall",
                         source="logistics oracle", trigger="a-list below floor")
    # Success teaches too, and a fleet that only learns from breaches learns nothing on
    # the days it works. But it must fire on the CROSSING, not on the state: recording
    # "the mandate is met" on every adoption while it stays met wrote four near-identical
    # lessons in one run, which is the firehose the settlement's data-diet audit warned
    # about — the anchor's dedup cannot catch them because the numbers differ slightly.
    if n["mandate_met"] and not (previous or {}).get("mandate_met", False):
        anchor.skill_add(-1, f"the mandate is reachable at {L.m(n['working_capital'])} of "
                             f"capital: {n['fill_rate']:.1%} fill, A-list "
                             f"{n['a_list_fill']:.1%}, {L.m(n['waste_cost'])} waste",
                         source="logistics oracle", trigger="mandate met")


def _learn_constraint_moved(before: dict, after: dict) -> None:
    """The most valuable thing a search can report is that the SHAPE of the problem
    changed — the axis that was binding no longer is. One lesson per move, never per
    proposal: the settlement's own audit showed a firehose of activity buries the
    signal it was meant to carry."""
    if before.get("axis") and after.get("axis") and before["axis"] != after["axis"]:
        anchor.skill_add(-1, f"the binding constraint moved from {before['axis']} to "
                             f"{after['axis']} — {after['why']}",
                         source="logistics oracle", trigger="binding constraint moved")


def lessons_for_prompt(limit: int = 4) -> str:
    """What this fleet has already paid to find out. Fed back into the next proposal so
    the next planner starts where the last one stopped (Article VI) — the settlement's
    finding was that lessons only pay when they are read at the point of decision."""
    try:
        got = anchor.skills_top(limit)
    except Exception:
        return ""
    if not got:
        return ""
    return "\n".join(f"- {s['lesson']}" for s in got)


def plan_cycle(agent: str, round_no: int = 0) -> dict:
    inc = ensure_incumbent()
    policy, knobs, how = propose(inc, round_no)
    return evaluate_and_score(agent, policy, how, knobs)


def run_search(agent: str, rounds: int = SEARCH_ROUNDS) -> dict:
    """The v1 bar: propose and score `rounds` policies, keeping whatever beats the
    incumbent. Deterministic and model-free by default, and measured throughout —
    a run that produced nothing has to say so, and say what stopped it."""
    # Seed the baseline BEFORE the clock starts, or the first run credits itself with
    # the naive plan's score and every efficiency number is inflated.
    started = ensure_incumbent()["score"]
    constraint_before = binding_constraint()
    t0 = time.perf_counter()
    results = []
    for i in range(rounds):
        r0 = time.perf_counter()
        r = plan_cycle(agent, i)
        r["seconds"] = round(time.perf_counter() - r0, 3)
        results.append(r)
    elapsed = time.perf_counter() - t0
    _learn_constraint_moved(constraint_before, binding_constraint())
    adopted = [r for r in results if r["did"] == "adopted"]
    inc = L.incumbent()
    base = L.oracle(L.naive_reorder_point())["nominal"]
    n = inc["verdict"]["nominal"]
    out = {"agent": agent, "proposed": rounds, "adopted": len(adopted),
           "best_policy_id": inc["id"], "score": inc["score"],
           "robust_score": inc["robust_score"],
           "beats_baseline_on_service": n["fill_rate"] > base["fill_rate"],
           "beats_baseline_on_capital": n["working_capital"] < base["working_capital"],
           "mandate_met": n["mandate_met"],
           "contribution": sum(a["contribution"] for a in adopted),
           "performance": performance(results, elapsed, inc["score"] - started),
           "binding_constraint": binding_constraint(),
           "last": adopted[-1] if adopted else None}
    if not adopted:
        # Article IX in this domain: a run that changed nothing is not a quiet success.
        # It names what stopped it, on the record, where a human will see it.
        anchor.record(-1, "stall", f"{agent} proposed {rounds} policies and moved nothing "
                                   f"— binding constraint: {out['binding_constraint']['why']}")
    return out


# ── measuring the agents, the way the settlement learned to ───────────────────
#
# The settlement's production audit found the failure that matters is not a wrong
# answer, it is a fleet that talks: 21% of turns changed the world, and the talk-to-
# action ratio was 3.78:1. The fix was to measure the right things and put them where
# a human reads them. The same three questions transfer exactly:
#
#   * did the world actually change?        → moved_pct (the liveness rate)
#   * how much talking bought that?         → talk_to_action (rejected per adoption)
#   * what did it cost per point won?       → points_per_second, seconds_per_proposal
#
# A planner that proposes twenty policies and adopts none is the logistics twin of a
# fleet reaching consensus and producing nothing, and it is reported as a stall.

def performance(results: list[dict], elapsed: float, points: float) -> dict:
    n = len(results) or 1
    adopted = [r for r in results if r["did"] == "adopted"]
    sims = n * (1 + len(L.SCENARIOS))          # every proposal is scored on all tiers
    return {
        "proposals": len(results),
        "adopted": len(adopted),
        "adoption_rate": round(len(adopted) / n, 3),
        "moved_pct": round(100.0 * len(adopted) / n, 1),
        "talk_to_action": round((len(results) - len(adopted)) / max(len(adopted), 1), 2),
        "points_won": round(points, 2),
        "points_per_proposal": round(points / n, 3),
        "points_per_second": round(points / elapsed, 3) if elapsed > 0 else 0.0,
        "seconds_total": round(elapsed, 2),
        "seconds_per_proposal": round(elapsed / n, 3),
        "simulations": sims,
        "simulations_per_second": round(sims / elapsed, 1) if elapsed > 0 else 0.0,
    }


def binding_constraint() -> dict:
    """What is actually stopping this plan from scoring better — named, not guessed.

    Article IX says a stall must name its binding constraint; the settlement's audit
    showed that reports which name it are the ones that get acted on. Here the answer
    is always available because the scorecard is an explicit weighted sum: the binding
    constraint is the axis with the least score left in it, and if every axis is full
    on an average day then the binding constraint is the worst disruption.
    """
    inc = ensure_incumbent()
    n, v = inc["verdict"]["nominal"], inc["verdict"]
    parts = L.components(n)
    axis = min(parts, key=lambda k: parts[k])
    detail = {
        "service": f"fill {n['fill_rate']:.1%} against a {L.TARGET_FILL:.0%} target",
        "capital": f"{L.m(n['working_capital'])} held against a "
                   f"{L.m(L.CAPITAL_CEILING)} ceiling",
        "waste": f"{L.m(n['waste_cost'])} scrapped against a {L.m(L.WASTE_BUDGET)} budget",
        "expedite": f"{L.m(n['expedite_spend'])} of emergency freight against a "
                    f"{L.m(L.EXPEDITE_BUDGET)} budget",
    }[axis]
    if parts[axis] >= 0.999:
        worst = v.get("worst") or ""
        return {"axis": "robustness", "score": v["robust_score"],
                "why": f"every axis is full on an average day — what is left is the "
                       f"worst disruption ({worst or 'none scored'}), which scores "
                       f"{v['robust_score']:.1f} against a nominal {n['score']:.1f}"}
    return {"axis": axis, "score": round(parts[axis], 3),
            "why": f"{axis} is the thinnest axis at {parts[axis]:.0%} of its weight — "
                   f"{detail}"}


# ── the board, reading logistics evidence ─────────────────────────────────────

def board_context(value: float = 0.0, planned_value: float = 0.0) -> dict:
    """The same three governors, the same disjoint-evidence rule, this domain's numbers.

    Each seat has to read something that MOVES, or it is a label wearing a robe
    (Article VIII.1). The obvious mappings are the degenerate ones, so:

      * **Ledger — is this order new capital?** Not "does the plan's capital plus the
        order exceed the ceiling": once a planner optimises up to the ceiling that is
        permanently false, and Ledger becomes a constant NO. A replenishment the plan
        already provides for is priced into the simulated working capital; only the
        part BEYOND the plan's own cycle quantity is capital we have not accounted for,
        and that is what gets judged against the headroom.
      * **Prudence — how long can we burn before the ceiling?** Runway from the money
        actually burning (waste and emergency freight), not from holding cost, which is
        a consequence of the plan rather than a leak in it. A wasteful plan has no
        runway; a clean one has plenty. It varies with plan quality, which is the point.
      * **Growth — is service the blocker?** When fill is under the mandate, more stock
        is the investment that moves the score; when service is already met, Growth
        voting against more stock is a correct answer, not a stuck one.
    """
    inc = ensure_incumbent()
    n = inc["verdict"]["nominal"]
    wc = n["working_capital"]
    headroom = L.CAPITAL_CEILING - wc
    unplanned = max(0.0, value - planned_value)
    burning = (n["waste_cost"] + n["expedite_spend"]) / max(n["periods"], 1)
    prev = [p["score"] for p in L.leaderboard(50) if p["id"] != inc["id"]]
    return {
        "affordable": unplanned <= headroom,
        "spent": int(wc), "cap": int(L.CAPITAL_CEILING),
        "burn_per_turn": max(1, int(round(burning))),
        "within_budget": (n["waste_cost"] <= L.WASTE_BUDGET
                          and n["expedite_spend"] <= L.EXPEDITE_BUDGET),
        "progress_delta": int(round(inc["score"] - max(prev))) if prev else None,
        "understaffed": (n["fill_rate"] < L.TARGET_FILL
                         or n["a_list_fill"] < L.A_LIST_FILL_FLOOR),
    }


# ── the gate ──────────────────────────────────────────────────────────────────

def planned_quantity(sku: str, node: str = "dc", policy: dict | None = None) -> int:
    """The plan's own order quantity: order-up-to minus reorder point, floored at the
    lead-time cover — nobody raises a purchase order for less than the time it takes to
    arrive. ONE definition, because the gate prices an order against it and the console
    shows it: two answers to "what does the plan provide for?" would be one too many."""
    policy = policy or ensure_incumbent()["policy"]
    p = policy[sku][node]
    lead = L.SKUS[sku]["lead"] if node == "dc" else L.DC_LEAD
    return max(p["S"] - p["s"], int(round(L.train_stats()[sku]["mean_daily"] * lead)))


def commit(agent: str, sku: str, node: str = "dc", qty: int = 0, note: str = "") -> dict:
    """Package one irreversible act — a purchase order — with the evidence behind it and
    PARK it for a human. The board votes first, on disjoint logistics evidence, and its
    tally rides with the dossier; a board NO does not stop the human seeing it, because
    Article VIII.4 forbids withholding the fact that a power was blocked.

    Nothing this function returns can move goods. ``L.place_order`` refuses always."""
    inc = ensure_incumbent()
    # What the plan already provides for is what Ledger measures an oversized order
    # against: the part beyond it is capital nobody has accounted for.
    planned = planned_quantity(sku, node, inc["policy"])
    qty = planned if qty <= 0 else int(qty)
    value = qty * L.SKUS[sku]["cost"]
    v = board.vote(f"commit {qty}× {sku} from {L.SKUS[sku]['vendor']} (${value:,.0f})",
                   board_context(value, planned * L.SKUS[sku]["cost"]))
    did = anchor.reason_add(
        -1, agent, f"propose PO: {qty}× {sku}",
        f"policy #{inc['id']} orders up to {inc['policy'][sku][node]['S']} at the {node}, "
        f"a cycle of {planned}; "
        + (f"asked for {qty} — {qty - planned} beyond the plan; " if qty > planned else "")
        + (f"note: {note.strip()[:120]}; " if note.strip() else "")
        + f"board {v['tally']} — "
        + "; ".join(f"{g}: {r}" for g, r in v["reasons"].items()),
        derived_from=[f"policy:{inc['id']}"],
        authorized_by="human" if not v["approved"] else "board")
    d = L.propose_commitment(agent, sku, qty, node, decision_id=did, board=v)
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
    default. Outside it, silence never buys anything. Each parked commitment is voted
    on against its own value, not one blurred average."""
    return L.tacit_sweep(board.vote, lambda d: board_context(d["value"], d["value"]))


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

"""The Logistics world — a supply network under the same constitution (LOGISTICS.md).

Where sim.py's world is resources and ages, and workspace.py's world is a codebase and
its test suite, this world is a small supply network and its simulator:

    the simulator is the oracle; the purchase order is the gate.

- ``oracle()`` replays real-shaped demand the planner never saw (the HOLDOUT window)
  and returns a JOINT scorecard — fill rate, stockouts, working capital, expedite
  spend and waste, together. A policy that hits 100% fill by drowning in stock is a
  failure, and the score says so, so the planner cannot game one axis.
- ``demand_history()`` serves the TRAIN window only and REFUSES the holdout — the
  quant harness's discipline, because forecasting invites the same overfitting.
- Disruption scenarios (a lane closes, the lead time doubles, demand spikes, the
  vendor's capacity is cut) are a second oracle tier: robustness, not just averages.
- Nothing here can buy anything. Commitments are DOSSIERS parked for a human;
  ``place_order`` refuses always because no procurement capability exists in this
  sandbox at all (Article V: remove the capability, don't police it).

The whole world is deterministic from a fixed seed: same policy, same numbers, every
run, on every machine.
"""

import json
import math
import os
import random
import sqlite3
import time

# ── the network ───────────────────────────────────────────────────────────────

SEED = "phoenix-logistics-v1"
PERIODS = 180                       # days simulated end to end
TRAIN = (0, 119)                    # readable history — what a planner may study
HOLDOUT = (120, 179)                # the planner never sees this demand, only its score

NODES = ("dc", "store")             # vendor → dc → store; demand lands at the store
DC_LEAD = 2                         # days, dc → store transfer
NODE_CAPACITY = {"dc": 5_000, "store": 1_400}       # units of storage, hard walls

# 4 SKUs, deliberately unalike: a perishable A-list mover, a cheap staple, a slow
# high-value line, and a lumpy spare. Shelf life is spoilage and obsolescence in one
# mechanic — the settlement's rot, wearing a suit.
SKUS = {
    "FRESH-01": {"cost": 4.0,  "shelf_life": 7,   "base": 40, "cv": 0.35, "weekly": 0.30,
                 "trend": 0.10, "spike_p": 0.04, "spike_x": 2.5, "a_list": True,
                 "vendor": "AgriCo",  "lead": 4},
    "STAPLE-02": {"cost": 2.0, "shelf_life": 60,  "base": 60, "cv": 0.20, "weekly": 0.15,
                  "trend": 0.05, "spike_p": 0.02, "spike_x": 2.0, "a_list": True,
                  "vendor": "BulkCo",  "lead": 7},
    "LUX-03":   {"cost": 45.0, "shelf_life": 120, "base": 6,  "cv": 0.80, "weekly": 0.40,
                 "trend": 0.00, "spike_p": 0.05, "spike_x": 3.0, "a_list": False,
                 "vendor": "Atelier", "lead": 14},
    "SPARE-04": {"cost": 18.0, "shelf_life": 180, "base": 3,  "cv": 1.20, "weekly": 0.05,
                 "trend": -0.05, "spike_p": 0.06, "spike_x": 4.0, "a_list": False,
                 "vendor": "PartsCo", "lead": 21},
}
A_LIST = tuple(k for k, v in SKUS.items() if v["a_list"])

# Units per vendor per day. Deliberately generous — roughly 6-16x mean demand — because
# in THIS network supply is not the binding constraint: shelf life and demand
# variability are, and a plan is judged on how it handles those. That choice has a
# consequence worth knowing before changing it: tighten these toward ~2x demand and the
# naive baseline's central weakness (over-ordering perishables) is masked, because the
# supplier throttles the over-ordering for it. Its score climbs from 74 to 98 and the
# separation the whole harness rests on disappears. If capacity should be the binding
# constraint, that is a different network, not a smaller number here.
VENDOR_CAPACITY = {"AgriCo": 400, "BulkCo": 900, "Atelier": 40, "PartsCo": 60}

CURRENCY = os.environ.get("GOV_CURRENCY", "$")      # one symbol, one place to change it


def m(x: float, dp: int = 0) -> str:
    """Money, formatted once. Every human-facing figure in this harness — the console,
    the dossier, the CLI — goes through here, so a deployment in another currency is one
    environment variable and not a grep."""
    return f"{CURRENCY}{x:,.{dp}f}"


HOLDING_RATE = 0.0008               # per unit of stock value per day (~30%/yr)
PO_COST = 60.0                      # fixed cost of raising a purchase order
TRANSFER_COST = 15.0                # fixed cost of an internal dc → store transfer
EXPEDITE_FIXED = 150.0              # emergency air-freight, per shipment
EXPEDITE_PREMIUM = 0.35             # + this fraction of the goods value
MARGIN = 0.30                       # gross margin, used to price a lost sale (IV.4)

# ── the mandate (the Vision, in this domain) ──────────────────────────────────
# "98% fill rate at or below the capital ceiling, and the A-list all but never out."
#
# LOGISTICS.md asked for ZERO A-list stockouts. The simulator says that costs a safety
# factor of z≈4 and ~$21,500 of working capital — 50% above any ceiling worth calling a
# ceiling. A mandate that cannot be met at any affordable capital is not a mandate, it
# is a slogan, so the A-list clause is a 99% fill floor: demanding, and reachable. The
# raw stockout count stays on the scorecard, because that is the number a human asks
# about first.
TARGET_FILL = 0.98
A_LIST_FILL_FLOOR = 0.99
CAPITAL_CEILING = 15_000.0          # average working capital, at cost — a binding ceiling
WASTE_BUDGET = 600.0                # spoilage + obsolescence allowed over the window
EXPEDITE_BUDGET = 1_200.0           # emergency freight allowed over the window
WEIGHTS = {"service": 0.50, "capital": 0.25, "waste": 0.125, "expedite": 0.125}
# A mandate is a floor, not a slope: missing 98% fill by a third of the way to zero
# costs ALL the service points. Without this, a policy that simply never buys anything
# scores well on the three cost axes and calls a 60% fill rate "most of the marks".
SERVICE_SLOPE = 3.0

# ── disruption scenarios: the second oracle tier ──────────────────────────────
SCENARIOS = {
    "lane_closed":      {"label": "the dc → store lane closes for a fortnight",
                         "lane_closed": (133, 146)},
    "lead_time_doubled": {"label": "every vendor lead time doubles",
                          "vendor_lead_x": 2},
    "demand_spike":     {"label": "demand runs 2.2x for two weeks",
                         "demand_x": 2.2, "window": (150, 163)},
    # 0.1, not 0.5. A HALVED capacity is not a disruption in this network — the tuned
    # plan does not notice it at all (identical fill, identical score), which made this
    # scenario decoration: a robustness test that never tests anything, the same defect
    # as a governor whose vote never varies. A vendor losing 90% of its output — a fire,
    # an export ban — is a disruption, and it costs the tuned plan real service.
    # `test_every_scenario_actually_bites` keeps it that way.
    "capacity_cut":     {"label": "a vendor loses 90% of its capacity",
                         "vendor_cap_x": 0.1},
}


def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return os.path.dirname(os.path.abspath(__file__))


DB = os.path.join(_data_dir(), "logistics.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS policies("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent TEXT, "
                  "policy TEXT, score REAL, robust REAL, scorecard TEXT, "
                  "incumbent INT DEFAULT 0, note TEXT DEFAULT '', knobs TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS commitments("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent TEXT, sku TEXT, "
                  "node TEXT, qty INT, vendor TEXT, value REAL, eta INT, forecast TEXT, "
                  "rollback TEXT, cancel_by REAL, cancel_fee REAL, wait_cost_per_day REAL, "
                  "status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', "
                  "decided_ts REAL DEFAULT 0, why TEXT DEFAULT '', decision_id INT DEFAULT 0, "
                  "board TEXT DEFAULT '')")
        c.commit()
    finally:
        c.close()


# ── demand: deterministic, and the holdout is not readable ────────────────────

def _rng(*parts) -> random.Random:
    """Seeded on the *content* of the request, so any (sku, period) is reproducible
    in any order, on any machine, forever. No global state, no wall clock."""
    return random.Random("|".join(str(p) for p in parts))


def demand(sku: str, t: int, scenario: dict | None = None) -> int:
    s = SKUS[sku]
    r = _rng(SEED, sku, t)
    season = 1.0 + s["weekly"] * math.sin(2 * math.pi * (t % 7) / 7.0)
    trend = 1.0 + s["trend"] * (t / PERIODS)
    d = s["base"] * season * trend * (1.0 + r.gauss(0.0, s["cv"]))
    if r.random() < s["spike_p"]:
        d *= s["spike_x"]
    d = max(0, int(round(d)))
    if scenario and scenario.get("demand_x"):
        lo, hi = scenario.get("window", (0, PERIODS))
        if lo <= t <= hi:
            d = int(round(d * scenario["demand_x"]))
    return d


def demand_history(sku: str = "", window: tuple = TRAIN) -> dict:
    """The readable past. REFUSES the holdout — a planner that could read the period
    it is scored on would be scoring itself (LOGISTICS.md §2.1)."""
    if window[1] > TRAIN[1]:
        raise ValueError("REFUSED: the holdout is not readable — the oracle scores on "
                         "demand the planner never saw")
    skus = [sku] if sku else list(SKUS)
    return {k: [demand(k, t) for t in range(window[0], window[1] + 1)] for k in skus}


def train_stats() -> dict:
    """What a planner is allowed to know: the shape of the readable past, per SKU."""
    out = {}
    for k, series in demand_history().items():
        n = len(series)
        mean = sum(series) / n
        var = sum((x - mean) ** 2 for x in series) / n
        s = SKUS[k]
        # Every figure here is derived from the ROUNDED mean, so a planner reading
        # "41.42/day over a 4-day lead" can do the multiplication and get the number
        # printed next to it. Reported arithmetic that does not reconcile is a bug
        # report waiting to happen.
        mean_daily = round(mean, 2)
        out[k] = {"mean_daily": mean_daily, "stdev": round(var ** 0.5, 2),
                  "peak": max(series), "lead_days": s["lead"], "vendor": s["vendor"],
                  "unit_cost": s["cost"], "shelf_life": s["shelf_life"],
                  "a_list": s["a_list"],
                  "lead_demand": round(mean_daily * s["lead"], 1)}
    return out


# ── policies ──────────────────────────────────────────────────────────────────

def normalize_policy(p: dict) -> dict:
    """Every policy is (s, S) per SKU per node. Anything missing, malformed or absurd
    is clamped rather than trusted — a model's JSON is an input, not an instruction."""
    out = {}
    for sku in SKUS:
        node_p = (p or {}).get(sku, {}) if isinstance(p, dict) else {}
        out[sku] = {}
        for node in NODES:
            raw = node_p.get(node, {}) if isinstance(node_p, dict) else {}
            try:
                s = max(0, int(round(float(raw.get("s", 0)))))
                big = max(0, int(round(float(raw.get("S", 0)))))
            except (TypeError, ValueError):
                s, big = 0, 0
            s = min(s, NODE_CAPACITY[node])
            big = min(max(big, s), NODE_CAPACITY[node])
            out[sku][node] = {"s": s, "S": big}
    return out


# The planner's proposal language. A raw (s, S) table is 16 free numbers with no
# meaning; KNOBS are two per SKU per node that a planner can actually reason about —
# `z`, how many standard deviations of lead-time demand to hold as safety stock, and
# `cover`, how many days of demand to order at a time. build_policy compiles them into
# the (s, S) table the simulator runs, and caps every order-up-to level below the SKU's
# shelf life, so a plan cannot buy stock it has no chance of selling before it rots.
SHELF_TURN = 0.6                    # never order up to more than 60% of shelf life in cover
DEFAULT_KNOBS = {"z": 1.28, "cover": 5.0}


def textbook_knobs(z: float = DEFAULT_KNOBS["z"],
                   cover: float = DEFAULT_KNOBS["cover"]) -> dict:
    return {sku: {node: {"z": z, "cover": cover} for node in NODES} for sku in SKUS}


def build_policy(knobs: dict) -> dict:
    """Compile knobs into (s, S) using the readable history only."""
    st = train_stats()
    p = {}
    for sku in SKUS:
        mu, sd, life = st[sku]["mean_daily"], st[sku]["stdev"], SKUS[sku]["shelf_life"]
        p[sku] = {}
        shelf_ceiling = mu * life * SHELF_TURN
        for node in NODES:
            lead = DC_LEAD if node == "store" else st[sku]["lead_days"]
            k = (knobs.get(sku, {}) or {}).get(node, {}) or DEFAULT_KNOBS
            z = max(0.0, float(k.get("z", DEFAULT_KNOBS["z"])))
            cover = max(0.0, float(k.get("cover", DEFAULT_KNOBS["cover"])))
            # Shelf life binds the REORDER POINT as well as the order-up-to level. It
            # is a physical wall, not a preference: without this, a large enough safety
            # factor pushes s above the ceiling and S is dragged up with it, and the
            # plan quietly holds more perishable stock than it can ever sell.
            s = min(mu * lead + z * sd * math.sqrt(lead), shelf_ceiling)
            big = min(s + cover * mu, shelf_ceiling)
            p[sku][node] = {"s": s, "S": max(big, s)}
    return normalize_policy(p)


def do_nothing() -> dict:
    """The floor: never replenish anything."""
    return normalize_policy({})


def naive_reorder_point() -> dict:
    """The baseline every proposal must beat: reorder at lead-time demand, order up to
    two more weeks of it. Computed from the TRAIN window only, like any honest plan."""
    st = train_stats()
    p = {}
    for sku, s in st.items():
        store_s = s["mean_daily"] * DC_LEAD
        dc_s = s["mean_daily"] * s["lead_days"]
        p[sku] = {"store": {"s": store_s, "S": store_s + 14 * s["mean_daily"]},
                  "dc": {"s": dc_s, "S": dc_s + 14 * s["mean_daily"]}}
    return normalize_policy(p)


def order_everything() -> dict:
    """The absurd policy: keep every node brim-full. Wins on service, loses on capital
    and waste — kept in the module because the acceptance suite proves the score
    cannot be gamed on one axis."""
    p = {}
    for sku in SKUS:
        p[sku] = {n: {"s": NODE_CAPACITY[n] // len(SKUS),
                      "S": NODE_CAPACITY[n] // len(SKUS)} for n in NODES}
    return normalize_policy(p)


# ── the simulator ─────────────────────────────────────────────────────────────

def simulate(policy: dict, window: tuple = HOLDOUT, scenario: dict | None = None) -> dict:
    """Replay the network day by day and score only the periods inside `window`.

    The run always starts at t=0 so the scored window inherits a warmed-up network
    rather than an artificial empty one; the score itself is blind to everything
    outside the window.
    """
    policy = normalize_policy(policy)
    sc = scenario or {}
    lead_x = sc.get("vendor_lead_x", 1)
    cap_x = sc.get("vendor_cap_x", 1.0)
    lane_closed = sc.get("lane_closed")

    # FIFO lots per node per sku: [units, age_in_days]
    inv = {n: {k: [] for k in SKUS} for n in NODES}
    for sku in SKUS:                                   # warm start at the store level
        q = policy[sku]["store"]["S"]
        if q:
            inv["store"][sku].append([q, 0])
    transit: list[dict] = []                           # {dest, sku, qty, eta}
    expediting: set = set()

    demanded = shipped = 0
    a_demanded = a_shipped = 0
    stockout_events = a_list_stockouts = 0
    waste_units = 0.0
    waste_cost = expedite_spend = holding_cost = order_cost = lost_margin = 0.0
    capital_samples: list[float] = []
    scored = 0
    lost_units = 0

    def on_hand(node, sku):
        return sum(l[0] for l in inv[node][sku])

    def inbound(node, sku):
        return sum(s["qty"] for s in transit if s["dest"] == node and s["sku"] == sku)

    def node_used(node):
        return sum(on_hand(node, k) for k in SKUS) + sum(
            s["qty"] for s in transit if s["dest"] == node)

    def take(node, sku, qty):
        """FIFO issue — oldest stock leaves first, which is what keeps waste honest."""
        left = qty
        lots = inv[node][sku]
        while left > 0 and lots:
            if lots[0][0] <= left:
                left -= lots[0][0]
                lots.pop(0)
            else:
                lots[0][0] -= left
                left = 0
        return qty - left

    for t in range(PERIODS):
        in_window = window[0] <= t <= window[1]

        # 1. arrivals
        for s in [x for x in transit if x["eta"] <= t]:
            inv[s["dest"]][s["sku"]].append([s["qty"], 0])
            transit.remove(s)
            if s.get("kind") == "expedite":
                expediting.discard(s["sku"])       # one emergency in flight at a time

        # 2. demand at the store
        for sku in SKUS:
            d = demand(sku, t, scenario)
            got = take("store", sku, min(on_hand("store", sku), d))
            unmet = d - got
            if in_window:
                demanded += d
                shipped += got
                if SKUS[sku]["a_list"]:
                    a_demanded += d
                    a_shipped += got
                if unmet > 0:
                    stockout_events += 1
                    lost_units += unmet
                    lost_margin += unmet * SKUS[sku]["cost"] * MARGIN
                    if SKUS[sku]["a_list"]:
                        a_list_stockouts += 1
            # 3. an A-list stockout expedites: the emergency air-freight every planner
            #    pays for and no planner budgets for. It is a cost, never a rescue —
            #    it lands a day late, so the sale it was meant to save is already lost.
            if unmet > 0 and SKUS[sku]["a_list"] and sku not in expediting:
                qty = max(1, int(round(unmet * 2)))
                transit.append({"dest": "store", "sku": sku, "qty": qty, "eta": t + 1,
                                "kind": "expedite"})
                expediting.add(sku)
                if in_window:
                    expedite_spend += EXPEDITE_FIXED + \
                        EXPEDITE_PREMIUM * qty * SKUS[sku]["cost"]

        # 4. ageing and spoilage — shelf life is perishability and obsolescence at once
        for node in NODES:
            for sku in SKUS:
                life = SKUS[sku]["shelf_life"]
                keep = []
                for lot in inv[node][sku]:
                    lot[1] += 1
                    if lot[1] > life:
                        if in_window:
                            waste_units += lot[0]
                            waste_cost += lot[0] * SKUS[sku]["cost"]
                    else:
                        keep.append(lot)
                inv[node][sku] = keep

        # 5. replenishment — the store pulls from the dc, the dc buys from the vendor
        lane_open = not (lane_closed and lane_closed[0] <= t <= lane_closed[1])
        for sku in SKUS:
            pol = policy[sku]["store"]
            ip = on_hand("store", sku) + inbound("store", sku)
            room = NODE_CAPACITY["store"] - node_used("store")
            if lane_open and ip <= pol["s"] and room > 0:
                qty = min(pol["S"] - ip, on_hand("dc", sku), room)
                if qty > 0:
                    take("dc", sku, qty)
                    transit.append({"dest": "store", "sku": sku, "qty": qty,
                                    "eta": t + DC_LEAD})
                    if in_window:
                        order_cost += TRANSFER_COST

        vendor_left = {v: int(VENDOR_CAPACITY[v] * cap_x) for v in VENDOR_CAPACITY}
        for sku in SKUS:
            pol = policy[sku]["dc"]
            ip = on_hand("dc", sku) + inbound("dc", sku)
            room = NODE_CAPACITY["dc"] - node_used("dc")
            v = SKUS[sku]["vendor"]
            if ip <= pol["s"] and room > 0 and vendor_left[v] > 0:
                qty = min(pol["S"] - ip, room, vendor_left[v])
                if qty > 0:
                    vendor_left[v] -= qty
                    transit.append({"dest": "dc", "sku": sku, "qty": qty,
                                    "eta": t + SKUS[sku]["lead"] * lead_x})
                    if in_window:
                        order_cost += PO_COST

        # 6. the books
        if in_window:
            value = sum((on_hand(n, k)) * SKUS[k]["cost"] for n in NODES for k in SKUS) \
                + sum(s["qty"] * SKUS[s["sku"]]["cost"] for s in transit)
            capital_samples.append(value)
            holding_cost += value * HOLDING_RATE
            scored += 1

    fill = (shipped / demanded) if demanded else 1.0
    wc = (sum(capital_samples) / len(capital_samples)) if capital_samples else 0.0
    out = {
        "window": f"{window[0]}-{window[1]}",
        "periods": scored,
        "fill_rate": round(fill, 4),
        "a_list_fill": round((a_shipped / a_demanded) if a_demanded else 1.0, 4),
        "units_demanded": demanded,
        "units_shipped": shipped,
        "lost_units": lost_units,
        "stockout_events": stockout_events,
        "a_list_stockouts": a_list_stockouts,
        "working_capital": round(wc, 2),
        "expedite_spend": round(expedite_spend, 2),
        "waste_units": int(waste_units),
        "waste_cost": round(waste_cost, 2),
        "holding_cost": round(holding_cost, 2),
        "order_cost": round(order_cost, 2),
        "lost_margin": round(lost_margin, 2),
    }
    out["total_cost"] = round(holding_cost + order_cost + expedite_spend
                              + waste_cost + lost_margin, 2)
    out["efficiency"] = round(fill * 100.0 / max(wc, 1.0) * 1000.0, 2)   # service per $1k
    out["score"] = score(out)
    out["mandate_met"] = bool(fill >= TARGET_FILL and wc <= CAPITAL_CEILING
                              and out["a_list_fill"] >= A_LIST_FILL_FLOOR)
    return out


def score(sc: dict) -> float:
    """One 0–100 number, from four axes at once. Every axis is capped at 1.0, so
    overshooting one can never buy off another — the reason 'order everything' cannot
    win here (LOGISTICS.md §2.2)."""
    parts = components(sc)
    return round(100.0 * sum(WEIGHTS[k] * parts[k] for k in WEIGHTS), 2)


def components(sc: dict) -> dict:
    shortfall = max(0.0, TARGET_FILL - sc["fill_rate"]) / TARGET_FILL
    return {
        "service": max(0.0, 1.0 - SERVICE_SLOPE * shortfall),
        "capital": min(1.0, CAPITAL_CEILING / max(sc["working_capital"], 1.0)),
        "waste": min(1.0, WASTE_BUDGET / max(sc["waste_cost"], WASTE_BUDGET)),
        "expedite": min(1.0, EXPEDITE_BUDGET / max(sc["expedite_spend"], EXPEDITE_BUDGET)),
    }


# ── the oracle ────────────────────────────────────────────────────────────────

ROBUST_WEIGHT = 0.4                 # how much the worst disruption counts in the verdict


def oracle(policy: dict, with_scenarios: bool = True) -> dict:
    """The score. Held-out demand first, then every disruption, and the verdict is a
    blend of the nominal run and the WORST scenario — a plan that only works on an
    average day is not a plan (LOGISTICS.md §2.3)."""
    nominal = simulate(policy, HOLDOUT)
    out = {"nominal": nominal, "scenarios": {}, "score": nominal["score"],
           "robust_score": nominal["score"], "worst": "",
           "mandate_met": nominal["mandate_met"]}
    if not with_scenarios:
        return out
    worst_name, worst = "", nominal["score"]
    for name, sc in SCENARIOS.items():
        r = simulate(policy, HOLDOUT, sc)
        out["scenarios"][name] = r
        if r["score"] < worst:
            worst_name, worst = name, r["score"]
    out["robust_score"] = worst
    out["worst"] = worst_name
    out["score"] = round((1 - ROBUST_WEIGHT) * nominal["score"] + ROBUST_WEIGHT * worst, 2)
    return out


def world() -> dict:
    """The world-state, in the shape every other Phoenix world reports it."""
    inc = incumbent()
    v = inc["verdict"] if inc else None
    n = (v or {}).get("nominal", {})
    return {"skus": len(SKUS), "nodes": len(NODES),
            "policies_scored": policy_count(),
            "incumbent_score": round(inc["score"], 2) if inc else 0.0,
            "fill_rate": n.get("fill_rate", 0.0),
            "working_capital": n.get("working_capital", 0.0),
            "mandate_met": bool(inc and inc["verdict"]["mandate_met"]),
            "commitments_pending": len(commitments("pending")),
            "progress_pct": round(inc["score"]) if inc else 0}


# ── the policy ledger: what we run, and why ───────────────────────────────────

def record_policy(agent: str, policy: dict, verdict: dict, note: str = "",
                  knobs: dict | None = None) -> int:
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO policies(ts, agent, policy, score, robust, scorecard, note, knobs)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), agent, json.dumps(policy), verdict["score"],
             verdict["robust_score"], json.dumps(verdict), note,
             json.dumps(knobs) if knobs else ""))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def adopt(policy_id: int) -> None:
    """Exactly one incumbent — adopting is reversible (it changes no goods), so it
    runs free. Only the purchase order stops at the gate."""
    c = _conn()
    try:
        c.execute("UPDATE policies SET incumbent=0 WHERE incumbent=1")
        c.execute("UPDATE policies SET incumbent=1 WHERE id=?", (policy_id,))
        c.commit()
    finally:
        c.close()


def incumbent() -> dict | None:
    c = _conn()
    try:
        row = c.execute("SELECT id, agent, policy, score, robust, scorecard, knobs "
                        "FROM policies WHERE incumbent=1").fetchone()
    finally:
        c.close()
    if not row:
        return None
    return {"id": row[0], "agent": row[1], "policy": json.loads(row[2]),
            "score": row[3], "robust_score": row[4], "verdict": json.loads(row[5]),
            "knobs": json.loads(row[6]) if row[6] else {}}


def policy_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
    finally:
        c.close()


def leaderboard(limit: int = 10) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT id, agent, score, robust, incumbent, note FROM policies "
                         "ORDER BY score DESC LIMIT ?", (limit,)).fetchall()
    finally:
        c.close()
    return [{"id": i, "agent": a, "score": s, "robust_score": r,
             "incumbent": bool(inc), "note": n} for i, a, s, r, inc, n in rows]


# ── the gate: a commitment is a dossier, not an order ─────────────────────────

TACIT_CONSENT_S = 3600              # Article IV.7 — an hour of silence goes to the Board
CANCEL_WINDOW_S = 86_400            # how long a vendor lets a raised order be cancelled
CANCEL_FEE_PCT = 0.05               # what they charge for the privilege


def envelope() -> dict:
    """The pre-approved envelope: the ONLY commitments tacit consent may ever cover
    (LOGISTICS.md §3). Empty by default — v1 waits for a human on everything, and the
    envelope is the human's to set, in the environment, not the model's to widen."""
    raw = os.environ.get("GOV_LOGISTICS_ENVELOPE", "").strip()
    base = {"skus": [], "vendors": [], "max_value": 0.0}
    if not raw:
        return base
    try:
        got = json.loads(raw)
    except ValueError:
        return base
    return {"skus": list(got.get("skus", [])), "vendors": list(got.get("vendors", [])),
            "max_value": float(got.get("max_value", 0) or 0)}


def within_envelope(d: dict) -> tuple[bool, str]:
    env = envelope()
    if not env["skus"] or not env["vendors"] or env["max_value"] <= 0:
        return False, "no pre-approved envelope is set — every commitment waits for a human"
    if d["sku"] not in env["skus"]:
        return False, f"{d['sku']} is not on the pre-approved SKU list"
    if d["vendor"] not in env["vendors"]:
        return False, f"{d['vendor']} is not a pre-approved vendor"
    if d["value"] > env["max_value"]:
        return False, (f"{m(d['value'])} exceeds the pre-approved ceiling "
                       f"{m(env['max_value'])}")
    return True, (f"inside the envelope: {d['sku']} from {d['vendor']} at "
                  f"{m(d['value'])} ≤ {m(env['max_value'])}")


def wait_cost_per_day(sku: str) -> float:
    """Article IV.4 — waiting has a price and the gate must show it. A day of silence
    on a replenishment costs a day of demand at margin, plus the expedite it makes
    likelier. Read from the readable history, never guessed."""
    st = train_stats()[sku]
    return round(st["mean_daily"] * SKUS[sku]["cost"] * MARGIN
                 + EXPEDITE_FIXED / max(SKUS[sku]["lead"], 1), 2)


def propose_commitment(agent: str, sku: str, qty: int, node: str = "dc",
                       forecast: str = "", decision_id: int = 0,
                       board: dict | None = None) -> dict:
    """Package an irreversible act with the evidence behind it and PARK it. Nothing
    downstream of this function can make it happen — only a human can."""
    if sku not in SKUS:
        raise ValueError(f"unknown sku {sku}")
    s = SKUS[sku]
    qty = max(1, int(qty))
    value = round(qty * s["cost"], 2)
    lead = s["lead"] if node == "dc" else DC_LEAD
    cancel_fee = round(value * CANCEL_FEE_PCT, 2)
    d = {"agent": agent, "sku": sku, "node": node, "qty": qty, "vendor": s["vendor"],
         "value": value, "eta": lead,
         "forecast": forecast or (f"train-window mean {train_stats()[sku]['mean_daily']}/day, "
                                  f"{lead}-day lead → {train_stats()[sku]['lead_demand']} "
                                  f"units of lead-time cover"),
         "rollback": (f"cancellable with {s['vendor']} until "
                      f"{CANCEL_WINDOW_S // 3600}h before dispatch for a "
                      f"{CANCEL_FEE_PCT:.0%} fee ({m(cancel_fee, 2)}); after dispatch "
                      f"the goods are ours"),
         "cancel_by": time.time() + CANCEL_WINDOW_S, "cancel_fee": cancel_fee,
         "wait_cost_per_day": wait_cost_per_day(sku)}
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO commitments(ts, agent, sku, node, qty, vendor, value, eta, "
            "forecast, rollback, cancel_by, cancel_fee, wait_cost_per_day, decision_id, "
            "board) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), agent, sku, node, qty, d["vendor"], value, lead, d["forecast"],
             d["rollback"], d["cancel_by"], cancel_fee, d["wait_cost_per_day"], decision_id,
             json.dumps(board) if board else ""))
        c.commit()
        d["id"] = cur.lastrowid
    finally:
        c.close()
    d["status"] = "pending"
    return d


def commitments(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = ("SELECT id, ts, agent, sku, node, qty, vendor, value, eta, forecast, "
             "rollback, cancel_by, cancel_fee, wait_cost_per_day, status, decided_by, "
             "why, decision_id, board FROM commitments")
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        rows = c.execute(q + " ORDER BY id DESC", args).fetchall()
    finally:
        c.close()
    keys = ("id", "ts", "agent", "sku", "node", "qty", "vendor", "value", "eta",
            "forecast", "rollback", "cancel_by", "cancel_fee", "wait_cost_per_day",
            "status", "decided_by", "why", "decision_id", "board")
    out = [dict(zip(keys, r)) for r in rows]
    now = time.time()
    for d in out:                                   # IV.4: the running cost of waiting
        waited = max(0.0, (now - d["ts"]) / 86_400.0)     # days
        d["waited_days"] = round(waited, 3)
        d["cost_of_waiting"] = round(waited * d["wait_cost_per_day"], 2)
        try:
            d["board"] = json.loads(d["board"]) if d["board"] else None
        except ValueError:
            d["board"] = None
    return out


def decide(cid: int, decision: str, by: str, why: str = "") -> tuple[bool, str]:
    """The human's word. 'approve' and 'reject' are the only decisions a person makes;
    'tacit' is written only by the Board sweep, and never outside the envelope."""
    if decision not in ("approve", "reject", "tacit"):
        return False, f"unknown decision {decision!r}"
    status = {"approve": "approved", "reject": "rejected",
              "tacit": "approved by tacit consent"}[decision]
    c = _conn()
    try:
        cur = c.execute("UPDATE commitments SET status=?, decided_by=?, decided_ts=?, why=? "
                        "WHERE id=? AND status='pending'",
                        (status, by, time.time(), why, cid))
        c.commit()
        if cur.rowcount == 0:
            return False, f"commitment {cid} is not pending"
    finally:
        c.close()
    return True, f"commitment {cid} → {status} ({by})"


def tacit_sweep(board_vote, ctx, now: float | None = None) -> list[dict]:
    """Article IV.7, narrowed to this domain: an hour of human silence sends a
    commitment back to the Board — but ONLY if it sits inside the pre-approved
    envelope. Outside it, silence decides nothing, forever; the request keeps waiting
    and keeps costing (IV.4), which is the human's signal, not the model's problem.

    `ctx` may be a dict or a callable taking the dossier, so each commitment can be
    voted on against its own numbers rather than one blurred average.
    """
    now = now or time.time()
    out = []
    for d in commitments("pending"):
        if now - d["ts"] < TACIT_CONSENT_S:
            continue
        ok, why = within_envelope(d)
        if not ok:
            out.append({"id": d["id"], "action": "still waiting", "why": why})
            continue
        v = board_vote(f"commit {d['qty']}× {d['sku']} from {d['vendor']} "
                       f"({m(d['value'])})", ctx(d) if callable(ctx) else ctx)
        if v["approved"]:
            decide(d["id"], "tacit", "board (tacit consent)",
                   f"{why}; board {v['tally']} after an hour of silence")
            out.append({"id": d["id"], "action": "approved by tacit consent",
                        "tally": v["tally"], "why": why})
        else:
            decide(d["id"], "reject", "board (tacit consent)",
                   f"board {v['tally']} — stood down after an hour of silence")
            out.append({"id": d["id"], "action": "stood down", "tally": v["tally"]})
    return out


# The capability that does not exist. Article V is literal here: there is no
# procurement client, no vendor credential, and no code path that could acquire one.
PROCUREMENT_ADAPTER = None


def place_order(cid: int) -> tuple[bool, str]:
    """Always refuses. An approved dossier is handed to the human to place in their own
    system; Phoenix holds no ERP or procurement credentials, so no model — however
    convinced — can turn a simulated order into a real one."""
    return False, ("REFUSED: no procurement capability exists in this sandbox. "
                   f"Commitment {cid} is a dossier for a human to act on, not an order.")


def render_world() -> str:
    w = world()
    return (f"{w['skus']} SKUs × {w['nodes']} nodes | policies scored {w['policies_scored']:>3} "
            f"| incumbent {w['incumbent_score']:>5.1f}/100  fill {w['fill_rate']:.1%}  "
            f"capital {m(w['working_capital'])} | mandate "
            f"{'MET' if w['mandate_met'] else 'unmet'} | gate: "
            f"{w['commitments_pending']} pending")


if __name__ == "__main__":
    init()
    for name, pol in (("do-nothing", do_nothing()),
                      ("naive reorder point", naive_reorder_point()),
                      ("order everything", order_everything())):
        v = oracle(pol)
        n = v["nominal"]
        print(f"{name:<22} score {v['score']:>6.2f}  (nominal {n['score']:>6.2f}, "
              f"worst {v['robust_score']:>6.2f} @ {v['worst'] or '-':<18}) "
              f"fill {n['fill_rate']:.1%}  capital {m(n['working_capital']):>10}  "
              f"waste {m(n['waste_cost']):>9}  expedite {m(n['expedite_spend']):>9}")

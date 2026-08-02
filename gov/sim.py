"""Age of Empires MVP — a living, developing game economy the Governor oversees.

Villagers GATHER food/wood/gold (reversible, free-running) and, when they finish a
quota, park awaiting orders — the idle alert — and can be RE-TASKED back to work, so
the fleet stays alive instead of freezing. The settlement DEVELOPS over time: houses
raise the population cap, resource camps and the wheelbarrow tech raise yields, so the
economy grows. Advancing the Age spends resources irreversibly, so it stops at the
human gate.

The game state (the ``world`` table) is the oracle; the director (``director.py``)
drives it turn by turn and the anchor (``anchor.py``) accumulates what it learns.
This module reuses ``governor.py`` unchanged: ``tokens`` is compute/effort (capped),
food/wood/gold are the economy.
"""

import json
import os
import sqlite3
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

# Defaults next to this file; set GOV_DATA_DIR (e.g. a Railway volume mount like /data)
# to persist game state across redeploys. The directory is created if missing, and if
# it can't be written (e.g. GOV_DATA_DIR set but no volume mounted) we fall back to the
# module directory instead of crash-looping.
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


DB = os.path.join(_data_dir(), "aoe.sqlite")

RESOURCES = ("food", "wood", "gold")
BASE = {"food": 20, "wood": 15, "gold": 8}           # base yield per gather round
CAMP_FOR = {"food": "mill", "wood": "lumber_camp", "gold": "mining_camp"}
QUOTA = 3                                            # rounds before a villager parks
ADVANCE_COST = {"food": 500, "gold": 300}            # Dark→Feudal base price
ADVANCE_GROWTH = 100                                 # each later age costs 100x the one before
AGE_ORDER = ("Dark Age", "Feudal Age", "Castle Age", "Imperial Age")
NEXT_AGE = {"Dark Age": "Feudal Age", "Feudal Age": "Castle Age", "Castle Age": "Imperial Age"}


def advance_cost(age: str | None = None) -> dict:
    """Price of advancing OUT of `age` (current world age when None). Real growth is
    exponential, not linear: every age costs 100-fold the one before, so each era is a
    genuine accumulation project — Feudal 500 food, Castle 50,000, Imperial 5,000,000."""
    if age is None:
        age = world()["age"]
    mult = ADVANCE_GROWTH ** AGE_ORDER.index(age) if age in AGE_ORDER else 1
    return {r: v * mult for r, v in ADVANCE_COST.items()}

# What the settlement can develop. Buildings are reversible (you can demolish), so they
# run free; only advancing the Age is gated. rank orders the tree (I = foundations).
STRUCTURES = {
    "house":        {"cost": {"wood": 50},               "effect": "+2 population cap",  "rank": 1},
    "mill":         {"cost": {"wood": 100},              "effect": "+50% food yield",    "rank": 1},
    "lumber_camp":  {"cost": {"wood": 80},               "effect": "+50% wood yield",    "rank": 1},
    "mining_camp":  {"cost": {"wood": 120},              "effect": "+50% gold yield",    "rank": 1},
    "wheelbarrow":  {"cost": {"food": 100, "wood": 100}, "effect": "+25% all yields",    "rank": 2},
}

# Effect vocabulary for governor-proposed developments — machine-usable by design so a
# proposal can actually change the world: yield_pct (one resource), all_yield_pct, pop_cap.
CUSTOM_KINDS = ("yield_pct", "all_yield_pct", "pop_cap")
_COLUMNS = ("food", "wood", "gold", "house", "mill", "lumber_camp", "mining_camp", "wheelbarrow")


def _append(a: list, b: list) -> list:
    return (a or []) + (b or [])


class Unit(TypedDict):
    uid: str
    role: str            # villager | herald
    resource: str        # villager: food | wood | gold
    task: str
    steps: int           # gather rounds toward the current quota
    tokens: int          # compute/effort spent — what the governor caps
    last_order: str      # most recent order given at the idle gate
    log: Annotated[list, _append]
    verdict: str


# ── shared World (the oracle) ────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _world_init(c: sqlite3.Connection) -> None:
    cols = ", ".join(f"{name} INT DEFAULT 0" for name in _COLUMNS)
    c.execute(f"CREATE TABLE IF NOT EXISTS world(id INTEGER PRIMARY KEY CHECK(id=1), "
              f"{cols}, age TEXT DEFAULT 'Dark Age')")
    c.execute("INSERT OR IGNORE INTO world(id) VALUES(1)")
    # Governor-proposed developments, adopted by the human. Machine-usable effects only.
    c.execute("CREATE TABLE IF NOT EXISTS custom_devs("
              "name TEXT PRIMARY KEY, food INT DEFAULT 0, wood INT DEFAULT 0, gold INT DEFAULT 0, "
              "kind TEXT, value INT, resource TEXT DEFAULT '', rank INT DEFAULT 2, "
              "source TEXT DEFAULT '', built INT DEFAULT 0)")
    # Assets need upkeep: every built development has a condition (100 → 0) that decays
    # and scales its effect. Lives with the world — a new world starts fresh.
    c.execute("CREATE TABLE IF NOT EXISTS conditions("
              "name TEXT PRIMARY KEY, condition INT DEFAULT 100)")
    c.commit()


def world() -> dict:
    c = _conn()
    try:
        row = c.execute(f"SELECT {', '.join(_COLUMNS)}, age FROM world WHERE id=1").fetchone()
        w = dict(zip(_COLUMNS + ("age",), row))
        pop_bonus = c.execute("SELECT COALESCE(SUM(value*built),0) FROM custom_devs "
                              "WHERE kind='pop_cap'").fetchone()[0]
        w["pop_cap"] = 3 + 2 * w["house"] + pop_bonus
        return w
    finally:
        c.close()


def structures() -> dict:
    w = world()
    return {k: w[k] for k in ("house", "mill", "lumber_camp", "mining_camp", "wheelbarrow")}


def effective_yield(resource: str, w: dict | None = None) -> int:
    """Yield grows as the settlement develops — camps, the wheelbarrow tech, and any
    adopted custom developments. Every bonus scales with the asset's CONDITION: a mill
    at 60% gives 60% of its bonus. Maintenance is real."""
    w = w or world()
    cond = conditions()
    camp = w[CAMP_FOR[resource]] * cond.get(CAMP_FOR[resource], 100) / 100
    tech = w["wheelbarrow"] * cond.get("wheelbarrow", 100) / 100
    y = BASE[resource] * (1 + 0.5 * camp) * (1 + 0.25 * tech)
    for d in custom_devs():
        if not d["built"]:
            continue
        eff = d["built"] * cond.get(d["name"], 100) / 100
        if d["kind"] == "yield_pct" and d["resource"] == resource:
            y *= 1 + (d["value"] / 100) * eff
        elif d["kind"] == "all_yield_pct":
            y *= 1 + (d["value"] / 100) * eff
    return int(y)


# ── decay, repair & spoilage — assets cost upkeep, food rots ─────────────────

DECAY_EVERY = 5          # decay tick cadence (turns)
REPAIR_FRACTION = 0.25   # repair costs this share of the build price
FOOD_SPOIL_PCT = 2       # % of overflow that rots per turn


def conditions() -> dict:
    c = _conn()
    try:
        return dict(c.execute("SELECT name, condition FROM conditions").fetchall())
    finally:
        c.close()


def decay_tick(turn: int) -> list[tuple[str, int]]:
    """Every DECAY_EVERY turns, each BUILT development loses `rank` condition points —
    grander works cost more to keep. Returns [(name, new_condition), ...]."""
    if turn % DECAY_EVERY:
        return []
    out = []
    c = _conn()
    try:
        for d in dev_catalog():
            if not d["built"]:
                continue
            c.execute("INSERT OR IGNORE INTO conditions(name, condition) VALUES(?, 100)",
                      (d["name"],))
            c.execute("UPDATE conditions SET condition=MAX(0, condition-?) WHERE name=?",
                      (d["rank"], d["name"]))
            out.append((d["name"],
                        c.execute("SELECT condition FROM conditions WHERE name=?",
                                  (d["name"],)).fetchone()[0]))
        c.commit()
        return out
    finally:
        c.close()


def repair_cost(name: str) -> dict:
    d = next((x for x in dev_catalog() if x["name"] == name), None)
    if not d:
        return {}
    return {r: max(1, int(v * REPAIR_FRACTION)) for r, v in d["cost"].items()}


def repair(name: str) -> tuple[bool, str]:
    """Spend a fraction of the build price to restore an asset to full condition."""
    cost = repair_cost(name)
    if not cost:
        return False, f"unknown development {name}"
    c = _conn()
    try:
        have = dict(zip(("food", "wood", "gold"),
                        c.execute("SELECT food, wood, gold FROM world WHERE id=1").fetchone()))
        for r, amt in cost.items():
            if have.get(r, 0) < amt:
                return False, f"cannot afford repair of {name}: need {r} {amt}, have {have.get(r, 0)}"
        sets = ", ".join(f"{r}={r}-{amt}" for r, amt in cost.items())
        c.execute(f"UPDATE world SET {sets} WHERE id=1")
        c.execute("INSERT INTO conditions(name, condition) VALUES(?, 100) "
                  "ON CONFLICT(name) DO UPDATE SET condition=100", (name,))
        c.commit()
        return True, f"repaired {name} to 100% (" + ", ".join(f"{v} {r}" for r, v in cost.items()) + ")"
    finally:
        c.close()


def food_cap(w: dict | None = None) -> int:
    """Storage is finite: the settlement can bank ~1.2x the NEXT era's food price.
    Anything above it rots — spoilage forces flow, not hoards."""
    w = w or world()
    return int(1.2 * advance_cost(w["age"])["food"])


def spoil_tick() -> tuple[int, int]:
    """Rot food above the storage cap. Returns (loss, cap)."""
    w = world()
    cap = food_cap(w)
    over = w["food"] - cap
    if over <= 0:
        return 0, cap
    loss = max(1, over * FOOD_SPOIL_PCT // 100)
    _world_add("food", -loss)
    return loss, cap


def custom_devs() -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT name, food, wood, gold, kind, value, resource, rank, source, "
                         "built FROM custom_devs ORDER BY rank, name").fetchall()
        return [{"name": n, "cost": {r: v for r, v in (("food", f), ("wood", wd), ("gold", g)) if v},
                 "kind": k, "value": val, "resource": res, "rank": rk, "source": src, "built": b}
                for n, f, wd, g, k, val, res, rk, src, b in rows]
    finally:
        c.close()


def dev_add(name: str, cost: dict, kind: str, value: int, resource: str = "",
            rank: int = 2, source: str = "") -> tuple[bool, str]:
    """Adopt a governor-proposed development into the buildable catalog."""
    name = name.strip().lower().replace(" ", "_")[:32]
    if not name or kind not in CUSTOM_KINDS:
        return False, f"invalid development (kind must be one of {CUSTOM_KINDS})"
    if kind == "yield_pct" and resource not in RESOURCES:
        return False, "yield_pct needs a valid resource"
    if name in STRUCTURES:
        return False, f"{name} already exists as a base structure"
    value = max(1, min(100, int(value)))
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO custom_devs(name, food, wood, gold, kind, value, "
                  "resource, rank, source) VALUES(?,?,?,?,?,?,?,?,?)",
                  (name, int(cost.get("food", 0)), int(cost.get("wood", 0)),
                   int(cost.get("gold", 0)), kind, value, resource, int(rank), source))
        c.commit()
        return True, f"adopted development {name}"
    finally:
        c.close()


def _custom_effect_text(d: dict) -> str:
    if d["kind"] == "yield_pct":
        return f"+{d['value']}% {d['resource']} yield"
    if d["kind"] == "all_yield_pct":
        return f"+{d['value']}% all yields"
    return f"+{d['value']} population cap"


def dev_catalog() -> list[dict]:
    """The full ranked development tree — base structures + adopted customs, with counts."""
    w = world()
    out = [{"name": k, "cost": v["cost"], "effect": v["effect"], "rank": v["rank"],
            "built": w[k], "custom": False} for k, v in STRUCTURES.items()]
    out += [{"name": d["name"], "cost": d["cost"], "effect": _custom_effect_text(d),
             "rank": d["rank"], "built": d["built"], "custom": True, "source": d["source"]}
            for d in custom_devs()]
    return sorted(out, key=lambda x: (x["rank"], x["name"]))


def build_development(name: str) -> tuple[bool, str]:
    """Build anything in the catalog — base structure or adopted custom development."""
    if name in STRUCTURES:
        return build_structure(name)
    d = next((x for x in custom_devs() if x["name"] == name), None)
    if d is None:
        return False, f"unknown development {name}"
    c = _conn()
    try:
        have = dict(zip(("food", "wood", "gold"),
                        c.execute("SELECT food, wood, gold FROM world WHERE id=1").fetchone()))
        for r, amt in d["cost"].items():
            if have[r] < amt:
                return False, f"cannot afford {name}: need {r} {amt}, have {have[r]}"
        if d["cost"]:
            sets = ", ".join(f"{r}={r}-{amt}" for r, amt in d["cost"].items())
            c.execute(f"UPDATE world SET {sets} WHERE id=1")
        c.execute("UPDATE custom_devs SET built=built+1 WHERE name=?", (name,))
        c.execute("INSERT INTO conditions(name, condition) VALUES(?, 100) "
                  "ON CONFLICT(name) DO UPDATE SET condition=100", (name,))
        c.commit()
        return True, f"built {name} ({_custom_effect_text(d)})"
    finally:
        c.close()


def _world_add(resource: str, amount: int) -> None:
    c = _conn()
    try:
        c.execute(f"UPDATE world SET {resource}={resource}+? WHERE id=1", (amount,))
        c.commit()
    finally:
        c.close()


def build_structure(kind: str) -> tuple[bool, str]:
    """Develop a building/tech. Reversible, so it runs free (no gate). Returns (ok, msg)."""
    if kind not in STRUCTURES:
        return False, f"unknown structure {kind}"
    cost = STRUCTURES[kind]["cost"]
    c = _conn()
    try:
        have = dict(zip(_COLUMNS, c.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM world WHERE id=1").fetchone()))
        if kind == "wheelbarrow" and have["wheelbarrow"]:
            return False, "wheelbarrow already researched"
        for r, amt in cost.items():
            if have[r] < amt:
                return False, f"cannot afford {kind}: need {r} {amt}, have {have[r]}"
        sets = ", ".join(f"{r}={r}-{amt}" for r, amt in cost.items())
        bump = "wheelbarrow=1" if kind == "wheelbarrow" else f"{kind}={kind}+1"
        c.execute(f"UPDATE world SET {sets}, {bump} WHERE id=1")
        c.execute("INSERT INTO conditions(name, condition) VALUES(?, 100) "
                  "ON CONFLICT(name) DO UPDATE SET condition=100", (kind,))
        c.commit()
        return True, f"built {kind} ({STRUCTURES[kind]['effect']})"
    finally:
        c.close()


def _world_advance() -> tuple[bool, str]:
    """Spend the age-up cost and bump the Age. Irreversible. Returns (ok, message)."""
    c = _conn()
    try:
        food, gold, age = c.execute("SELECT food, gold, age FROM world WHERE id=1").fetchone()
        nxt = NEXT_AGE.get(age)
        if nxt is None:
            return False, f"already at {age}"
        cost = advance_cost(age)
        if food < cost["food"] or gold < cost["gold"]:
            return False, (f"insufficient resources: have food {food}/gold {gold}, "
                           f"need food {cost['food']}/gold {cost['gold']}")
        c.execute("UPDATE world SET food=food-?, gold=gold-?, age=? WHERE id=1",
                  (cost["food"], cost["gold"], nxt))
        c.commit()
        return True, f"advanced to {nxt}"
    finally:
        c.close()


# ── graph nodes ──────────────────────────────────────────────────────────────

def by_role(state: Unit) -> str:
    return state["role"]


def plan(state: Unit) -> dict:
    return {"tokens": state["tokens"] + 800,
            "log": [f"villager {state['uid']} → gather {state['resource']}"]}


def gather(state: Unit) -> dict:
    time.sleep(0.02)
    got = effective_yield(state["resource"])
    _world_add(state["resource"], got)
    return {"steps": state["steps"] + 1, "tokens": state["tokens"] + 3000,
            "log": [f"gathered {got} {state['resource']} (round {state['steps'] + 1})"]}


def more(state: Unit) -> str:
    return "gather" if state["steps"] < QUOTA else "orders"


def orders(state: Unit) -> dict:
    """Villager finished its quota — parks awaiting orders (the idle alert)."""
    decision = interrupt({
        "uid": state["uid"],
        "action": f"Villager idle — gathered {state['steps'] * effective_yield(state['resource'])} "
                  f"{state['resource']}, awaiting orders",
        "reversible": True,
        "tokens_spent": state["tokens"],
    })
    return {"last_order": decision, "log": [f"order: {decision}"]}


def route_orders(state: Unit) -> str:
    """Re-task back to work on a 'gather[:resource]' order; otherwise stand down."""
    return "retask" if str(state.get("last_order", "")).startswith("gather") else "stand_down"


def retask(state: Unit) -> dict:
    order = state["last_order"]
    res = order.split(":", 1)[1] if ":" in order else state["resource"]
    res = res if res in RESOURCES else state["resource"]
    return {"resource": res, "steps": 0, "log": [f"re-tasked → gather {res}"]}


def stand_down(state: Unit) -> dict:
    return {"verdict": "dismissed", "log": ["dismissed"]}


def assess(state: Unit) -> dict:
    w = world()
    return {"tokens": state["tokens"] + 500,
            "log": [f"herald: treasury food {w['food']} / gold {w['gold']}, age {w['age']}"]}


def advance(state: Unit) -> dict:
    """The irreversible action: spend resources to advance the Age. Gated."""
    cost = advance_cost()
    decision = interrupt({
        "uid": state["uid"],
        "action": f"ADVANCE to {NEXT_AGE.get(world()['age'], 'next Age')} — "
                  f"spend food {cost['food']:,} + gold {cost['gold']:,} (irreversible)",
        "reversible": False,
        "tokens_spent": state["tokens"],
    })
    if decision == "approve":
        ok, msg = _world_advance()
        return {"verdict": "advanced" if ok else "blocked", "log": [f"approved → {msg}"]}
    return {"verdict": "held", "log": [f"rejected ({decision}) → held"]}


def build(checkpointer):
    g = StateGraph(Unit)
    for name, fn in (("plan", plan), ("gather", gather), ("orders", orders),
                     ("retask", retask), ("stand_down", stand_down),
                     ("assess", assess), ("advance", advance)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, by_role, {"villager": "plan", "herald": "assess"})
    g.add_edge("plan", "gather")
    g.add_conditional_edges("gather", more, {"gather": "gather", "orders": "orders"})
    g.add_conditional_edges("orders", route_orders, {"retask": "retask", "stand_down": "stand_down"})
    g.add_edge("retask", "gather")          # re-tasked villagers loop back to work
    g.add_edge("stand_down", END)
    g.add_edge("assess", "advance")
    g.add_edge("advance", END)
    return g.compile(checkpointer=checkpointer)


def connect() -> SqliteSaver:
    conn = sqlite3.connect(DB, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _world_init(conn)
    return SqliteSaver(conn)


def spawn(graph, uid: str, role: str, resource: str = "", task: str = "") -> dict:
    cfg = {"configurable": {"thread_id": uid}}
    return graph.invoke(
        {"uid": uid, "role": role, "resource": resource,
         "task": task or (f"gather {resource}" if role == "villager" else "advance the age"),
         "steps": 0, "tokens": 0, "last_order": "", "log": [], "verdict": ""},
        cfg,
    )


def resume(graph, uid: str, decision: str) -> dict:
    cfg = {"configurable": {"thread_id": uid}}
    return graph.invoke(Command(resume=decision), cfg)


def render_world() -> str:
    w = world()
    built = ", ".join(f"{k}×{w[k]}" for k in ("house", "mill", "lumber_camp", "mining_camp")
                      if w[k]) or "none"
    tech = " +wheelbarrow" if w["wheelbarrow"] else ""
    return (f"{w['age']:<12} food {w['food']:>5}  wood {w['wood']:>5}  gold {w['gold']:>5}  "
            f"| pop_cap {w['pop_cap']} | built: {built}{tech}")

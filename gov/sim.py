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

import os
import sqlite3
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

# Defaults next to this file; set GOV_DATA_DIR (e.g. a Railway volume mount like /data)
# to persist game state across redeploys.
DB = os.path.join(os.environ.get("GOV_DATA_DIR", os.path.dirname(os.path.abspath(__file__))),
                  "aoe.sqlite")

RESOURCES = ("food", "wood", "gold")
BASE = {"food": 20, "wood": 15, "gold": 8}           # base yield per gather round
CAMP_FOR = {"food": "mill", "wood": "lumber_camp", "gold": "mining_camp"}
QUOTA = 3                                            # rounds before a villager parks
ADVANCE_COST = {"food": 500, "gold": 300}            # irreversible age-up price
NEXT_AGE = {"Dark Age": "Feudal Age", "Feudal Age": "Castle Age", "Castle Age": "Imperial Age"}

# What the settlement can develop. Buildings are reversible (you can demolish), so they
# run free; only advancing the Age is gated.
STRUCTURES = {
    "house":        {"cost": {"wood": 50},               "effect": "+2 population cap"},
    "mill":         {"cost": {"wood": 100},              "effect": "+50% food yield"},
    "lumber_camp":  {"cost": {"wood": 80},               "effect": "+50% wood yield"},
    "mining_camp":  {"cost": {"wood": 120},              "effect": "+50% gold yield"},
    "wheelbarrow":  {"cost": {"food": 100, "wood": 100}, "effect": "+25% all yields"},
}
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
    c.commit()


def world() -> dict:
    c = _conn()
    try:
        row = c.execute(f"SELECT {', '.join(_COLUMNS)}, age FROM world WHERE id=1").fetchone()
        w = dict(zip(_COLUMNS + ("age",), row))
        w["pop_cap"] = 3 + 2 * w["house"]
        return w
    finally:
        c.close()


def structures() -> dict:
    w = world()
    return {k: w[k] for k in ("house", "mill", "lumber_camp", "mining_camp", "wheelbarrow")}


def effective_yield(resource: str, w: dict | None = None) -> int:
    """Yield grows as the settlement develops — camps and the wheelbarrow tech."""
    w = w or world()
    camp = w[CAMP_FOR[resource]]
    tech = w["wheelbarrow"]
    return int(BASE[resource] * (1 + 0.5 * camp) * (1 + 0.25 * tech))


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
        if food < ADVANCE_COST["food"] or gold < ADVANCE_COST["gold"]:
            return False, (f"insufficient resources: have food {food}/gold {gold}, "
                           f"need food {ADVANCE_COST['food']}/gold {ADVANCE_COST['gold']}")
        c.execute("UPDATE world SET food=food-?, gold=gold-?, age=? WHERE id=1",
                  (ADVANCE_COST["food"], ADVANCE_COST["gold"], nxt))
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
    decision = interrupt({
        "uid": state["uid"],
        "action": f"ADVANCE to {NEXT_AGE.get(world()['age'], 'next Age')} — "
                  f"spend food {ADVANCE_COST['food']} + gold {ADVANCE_COST['gold']} (irreversible)",
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

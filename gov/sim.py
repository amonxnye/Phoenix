"""Age of Empires MVP — the Governor over a real game economy.

The spike's simulated ``work`` node is replaced with real game work:

  * Villagers GATHER food/wood/gold. Gathering is reversible and cheap, so it runs
    free, and each round adds to the shared World.
  * A Villager that finishes its quota parks "awaiting orders" — the literal
    idle-villager alert.
  * A Herald proposes ADVANCING to the next Age. That spends resources that cannot
    be reclaimed, so it is the irreversible action and stops at the human gate.

The game state (the ``world`` table) is the oracle: resources and Age are objective,
instant to read, and resettable — a real undo. The DeepSeek brain (see ``brain.py``)
chooses what to gather and when to advance; a rule-based default runs without a key.

This module reuses ``governor.py`` unchanged for the read view, cap, idle and gate —
the governor is graph-agnostic. ``tokens`` here means compute/effort spent (what the
governor caps); food/wood/gold are the game economy, tracked in the World.
"""

import os
import sqlite3
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aoe.sqlite")

RESOURCES = ("food", "wood", "gold")
YIELD = {"food": 20, "wood": 15, "gold": 8}          # gathered per round
QUOTA = 3                                            # rounds before a villager parks
ADVANCE_COST = {"food": 500, "gold": 300}            # irreversible age-up price
NEXT_AGE = {"Dark Age": "Feudal Age", "Feudal Age": "Castle Age", "Castle Age": "Imperial Age"}


def _append(a: list, b: list) -> list:
    return (a or []) + (b or [])


class Unit(TypedDict):
    uid: str
    role: str            # villager | herald
    resource: str        # villager only: food | wood | gold
    task: str
    steps: int           # gather rounds completed (governor displays this)
    tokens: int          # compute/effort spent — what the governor caps
    log: Annotated[list, _append]
    verdict: str


# ── shared World (the oracle) ────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _world_init(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS world("
              "id INTEGER PRIMARY KEY CHECK(id=1), food INT, wood INT, gold INT, age TEXT)")
    c.execute("INSERT OR IGNORE INTO world(id,food,wood,gold,age) VALUES(1,0,0,0,'Dark Age')")
    c.commit()


def _world_add(resource: str, amount: int) -> None:
    c = _conn()
    try:
        c.execute(f"UPDATE world SET {resource}={resource}+? WHERE id=1", (amount,))
        c.commit()
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


def world() -> dict:
    c = _conn()
    try:
        food, wood, gold, age = c.execute(
            "SELECT food, wood, gold, age FROM world WHERE id=1").fetchone()
        return {"food": food, "wood": wood, "gold": gold, "age": age}
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
    got = YIELD[state["resource"]]
    _world_add(state["resource"], got)
    return {"steps": state["steps"] + 1, "tokens": state["tokens"] + 3000,
            "log": [f"gathered {got} {state['resource']} (round {state['steps'] + 1})"]}


def more(state: Unit) -> str:
    return "gather" if state["steps"] < QUOTA else "orders"


def orders(state: Unit) -> dict:
    """Villager finished its quota — parks awaiting orders. This is the idle alert."""
    decision = interrupt({
        "uid": state["uid"],
        "action": f"Villager idle — gathered {state['steps'] * YIELD[state['resource']]} "
                  f"{state['resource']}, awaiting orders",
        "reversible": True,
        "tokens_spent": state["tokens"],
    })
    return {"verdict": f"orders:{decision}", "log": [f"re-tasked ({decision})"]}


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
                     ("assess", assess), ("advance", advance)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, by_role, {"villager": "plan", "herald": "assess"})
    g.add_edge("plan", "gather")
    g.add_conditional_edges("gather", more, {"gather": "gather", "orders": "orders"})
    g.add_edge("orders", END)
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
         "steps": 0, "tokens": 0, "log": [], "verdict": ""},
        cfg,
    )


def resume(graph, uid: str, decision: str) -> dict:
    cfg = {"configurable": {"thread_id": uid}}
    return graph.invoke(Command(resume=decision), cfg)


def render_world() -> str:
    w = world()
    return (f"🏰 {w['age']:<12}  🍖 food {w['food']:>5}  🪵 wood {w['wood']:>5}  "
            f"🪙 gold {w['gold']:>5}")

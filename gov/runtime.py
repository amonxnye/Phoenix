"""Agent runtime: a LangGraph graph whose irreversible step pauses for a human."""

import os
import sqlite3
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.sqlite")


def _append(a: list, b: list) -> list:
    return (a or []) + (b or [])


class Unit(TypedDict):
    unit_id: str
    task: str
    steps: int
    tokens: int
    log: Annotated[list, _append]
    verdict: str


def plan(state: Unit) -> dict:
    return {
        "tokens": state["tokens"] + 1200,
        "log": [f"planned: {state['task']}"],
    }


def work(state: Unit) -> dict:
    time.sleep(0.02)
    return {
        "steps": state["steps"] + 1,
        "tokens": state["tokens"] + 3400,
        "log": [f"work step {state['steps'] + 1}"],
    }


def more_work(state: Unit) -> str:
    return "work" if state["steps"] < 3 else "gate"


def gate(state: Unit) -> dict:
    """The irreversible action. Execution stops here until a human resumes."""
    decision = interrupt(
        {
            "unit_id": state["unit_id"],
            "action": f"PUBLISH result of: {state['task']}",
            "reversible": False,
            "tokens_spent": state["tokens"],
        }
    )
    if decision == "approve":
        return {"verdict": "published", "log": ["approved -> published"]}
    return {"verdict": "aborted", "log": [f"rejected ({decision}) -> aborted"]}


def build(checkpointer):
    g = StateGraph(Unit)
    g.add_node("plan", plan)
    g.add_node("work", work)
    g.add_node("gate", gate)
    g.add_edge(START, "plan")
    g.add_edge("plan", "work")
    g.add_conditional_edges("work", more_work, {"work": "work", "gate": "gate"})
    g.add_edge("gate", END)
    return g.compile(checkpointer=checkpointer)


def connect() -> SqliteSaver:
    """Swap this one function for PostgresSaver in production — same interface."""
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn)


def spawn(graph, unit_id: str, task: str) -> dict:
    cfg = {"configurable": {"thread_id": unit_id}}
    return graph.invoke(
        {"unit_id": unit_id, "task": task, "steps": 0, "tokens": 0,
         "log": [], "verdict": ""},
        cfg,
    )


def resume(graph, unit_id: str, decision: str) -> dict:
    cfg = {"configurable": {"thread_id": unit_id}}
    return graph.invoke(Command(resume=decision), cfg)

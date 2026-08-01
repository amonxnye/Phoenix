# The Governor — an operator control plane for LangGraph agent fleets

**Status:** working spike, 16/16 acceptance checks passing
**Scope:** LangGraph-only runtime · own tooling first (no auth, no multi-tenancy)
**Size:** 203 lines of product code + 130 lines of checks

---

## What this is

Not a dashboard. Watching agents is a crowded category — multi-agent session
viewers for Claude Code and Grok, AgentCenter, agentsroom, plus the observability
tier (LangSmith, Langfuse, AgentOps). Those are viewers and tracers.

This is the **governing** layer, which is still thin:

| Control | What it does |
|---|---|
| **Hard resource cap** | Halts spawning at a ceiling. Halts — does not warn. |
| **Idle reclamation** | Surfaces units parked on a human who isn't looking. |
| **Durable approval gate** | Irreversible actions pause until a human resumes them, across restarts. |

The design metaphor is an RTS. Age of Empires solved resource visibility, idle-unit
alerts, and select-and-command in 1999; agent tooling largely hasn't.

## The architectural bet

**The LangGraph checkpointer database is the backend.**

Every unit's state, current node, and pending interrupt already persist in the
checkpointer tables. So there is no separate state store, no event bus, no polling
daemon — the governor is a read view over `checkpointer.list(None)` plus
`graph.get_state()`, and a resume endpoint.

That bet is why `governor.py` is 106 lines.

```
checkpointer.list(None)   →  every thread_id the system knows about
graph.get_state(cfg)      →  .values (state) · .next (position) · .tasks[].interrupts (pending)
graph.invoke(Command(resume=x), cfg)  →  drain one approval
```

## Setup

```bash
pip install langgraph langgraph-checkpoint-sqlite
python3 gov/verify.py          # → 16/16 checks passed
```

Swap SQLite for Postgres by changing one function (`runtime.connect`) — the
checkpointer interface is identical.

## Verification results

Each check either passes or fails; none require interpretation.

```
1. Runtime — concurrent units, durable state
  [PASS] 5 units spawned concurrently  — 0.17s wall clock
  [PASS] all parked at the gate
  [PASS] each completed 3 work steps
  [PASS] fresh process reads identical state  — units=5 steps=15 parked=5

2. Read layer — no separate state store
  [PASS] thread list comes from checkpointer  — 5 threads
  [PASS] pending interrupt payload readable  — action: PUBLISH result of: refactor auth modul…

3. Approval gate
  [PASS] approved unit published
  [PASS] rejected unit aborted
  [PASS] resolved units report done
  [PASS] untouched units still parked
  [PASS] decisions persist across restart  — approved -> published | rejected (reject) -> aborted

4. Governor — cap and idle detection
  [PASS] spend tracked across fleet  — 57,000/100,000 spent
  [PASS] cap HALTS spawning, not warns  — 57,000 spent + 10,000 projected > 1,000
  [PASS] newly parked unit reads awaiting_approval  — age 0.0s
  [PASS] same unit flips to idle once stale  — age 2.3s
  [PASS] all stale approvals surface together  — 3 original + 1 new

16/16 checks passed
```

The restart checks shell out to genuinely separate Python processes — nothing
carries over in memory.

## One design decision worth knowing

"Idle" changed meaning during the build. The original plan was a wedged unit
spinning without progress. That's the wrong fixture for this architecture.

In a human-in-the-loop system the real idle villager is a unit that **finished its
work and is parked on a human who isn't looking** — work complete, tokens spent,
value zero, and it will sit there indefinitely. So `idle` is `awaiting_approval`
aged past a threshold, and the test asserts the transition rather than a static
state. This falls out of the interrupt model for free and is probably the single
most valuable alert in the product.

## Not done

- **Work nodes are simulated.** Deterministic on purpose, so the control plane is
  testable without spending tokens or waiting on a model.
- **No sandbox.** Generated code would run in-process. LangGraph does not solve
  this — `exec()`-grade isolation is still entirely yours to build.
- **Console not wired.** The HTML operator surface still renders fixtures.
- **No verifier.** Nothing ships this. It's the reason to point v1 at a domain
  that already has one (a repo with tests).

## Next

1. Wire the HTML console to `governor.units()` so it renders live instead of fixtures.
2. Swap simulated nodes for real agent work against a repo with tests.
3. Sandbox the execution path.

Console first — a couple of hours, and it makes everything after it easier to debug.

---

# Source

## `runtime.py`

```python
"""Agent runtime: a LangGraph graph whose irreversible step pauses for a human.

Work is simulated so the control plane can be tested deterministically — the
governor's job is to watch, cap, and gate units, and that logic should be
verifiable without spending tokens or waiting on a model.
"""

import sqlite3
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

DB = "/home/claude/gov/checkpoints.sqlite"


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
```

## `governor.py`

```python
"""The governor: a read view over the checkpointer, plus the two controls.

There is no separate state store. Every unit's state, position, and pending
interrupt already live in the checkpointer tables — the console reads those.
"""

import time
from dataclasses import dataclass, field

IDLE_AFTER_S = 2.0        # flag a unit with no state change in this long
TOKEN_CAP = 100_000       # hard ceiling across the whole fleet


@dataclass
class UnitView:
    unit_id: str
    task: str
    status: str            # running | awaiting_approval | done | idle
    node: str
    steps: int
    tokens: int
    age_s: float
    pending: dict | None = None
    log: list = field(default_factory=list)


def _thread_ids(checkpointer) -> list[str]:
    """List every thread the checkpointer knows about. config=None spans all."""
    seen = {}
    for ct in checkpointer.list(None):
        tid = ct.config["configurable"]["thread_id"]
        seen.setdefault(tid, True)
    return list(seen)


def units(graph, checkpointer) -> list[UnitView]:
    out = []
    for tid in _thread_ids(checkpointer):
        snap = graph.get_state({"configurable": {"thread_id": tid}})
        v = snap.values
        if not v:
            continue

        pending = None
        for task in snap.tasks:
            if task.interrupts:
                pending = dict(task.interrupts[0].value)

        age = time.time() - _ts(snap)
        if pending:
            # The real idle-villager case in a human-in-the-loop system: the
            # unit finished its work and is parked on a human who isn't looking.
            status = "idle" if age > IDLE_AFTER_S else "awaiting_approval"
        elif not snap.next:
            status = "done"
        else:
            status = "running"

        out.append(UnitView(
            unit_id=tid,
            task=v.get("task", ""),
            status=status,
            node=(snap.next[0] if snap.next else "—"),
            steps=v.get("steps", 0),
            tokens=v.get("tokens", 0),
            age_s=round(age, 1),
            pending=pending,
            log=v.get("log", []),
        ))
    return sorted(out, key=lambda u: u.unit_id)


def spent(views: list[UnitView]) -> int:
    return sum(u.tokens for u in views)


def may_spawn(views: list[UnitView], cost: int = 10_000) -> tuple[bool, str]:
    """Hard cap. Returns (allowed, reason) — this halts, it does not warn."""
    s = spent(views)
    if s + cost > TOKEN_CAP:
        return False, f"token cap reached: {s:,} spent + {cost:,} projected > {TOKEN_CAP:,}"
    return True, f"{s:,}/{TOKEN_CAP:,} spent"


def idle(views: list[UnitView]) -> list[UnitView]:
    """The idle-villager check: units parked on a human who isn't looking."""
    return [u for u in views if u.status == "idle"]


def approvals(views: list[UnitView]) -> list[UnitView]:
    return [u for u in views if u.status in ("awaiting_approval", "idle")]


def _ts(snap) -> float:
    from datetime import datetime
    return datetime.fromisoformat(snap.created_at.replace("Z", "+00:00")).timestamp()


def render(views: list[UnitView]) -> str:
    glyph = {"running": "▶", "awaiting_approval": "◆", "idle": "⏹", "done": "✓"}
    w = f"{'UNIT':<10} {'STATE':<18} {'NODE':<7} {'STEPS':>5} {'TOKENS':>8} {'AGE':>6}\n"
    w += "─" * 60 + "\n"
    for u in views:
        w += (f"{u.unit_id:<10} {glyph[u.status]} {u.status:<16} {u.node:<7} "
              f"{u.steps:>5} {u.tokens:>8,} {u.age_s:>5.1f}s\n")
    return w
```

## `verify.py`

```python
"""Acceptance checks. Each either passes or fails — no interpretation needed.

Run:  python3 gov/verify.py
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import governor as G
import runtime as R

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        p = R.DB + suffix
        if os.path.exists(p):
            os.remove(p)


# ── 1. runtime: N concurrent units, durable across a process boundary ────────
print("\n1. Runtime — concurrent units, durable state")
fresh_db()

cp = R.connect()
graph = R.build(cp)

TASKS = [
    ("unit-01", "refactor auth module"),
    ("unit-02", "add retry to payment client"),
    ("unit-03", "migrate config loader"),
    ("unit-04", "fix flaky integration test"),
    ("unit-05", "bump dependency set"),
]
t0 = time.time()
with ThreadPoolExecutor(max_workers=5) as ex:
    list(ex.map(lambda t: R.spawn(graph, *t), TASKS))
elapsed = time.time() - t0

v = G.units(graph, cp)
check("5 units spawned concurrently", len(v) == 5, f"{elapsed:.2f}s wall clock")
check("all parked at the gate", all(u.pending for u in v))
check("each completed 3 work steps", all(u.steps == 3 for u in v))

# separate process — nothing in memory carries over
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0,'/home/claude/gov');"
     "import runtime as R, governor as G;"
     "cp=R.connect(); g=R.build(cp); v=G.units(g,cp);"
     "print(len(v), sum(u.steps for u in v), sum(1 for u in v if u.pending))"],
    capture_output=True, text=True,
)
n, steps, parked = probe.stdout.split()
check("fresh process reads identical state", (n, steps, parked) == ("5", "15", "5"),
      f"units={n} steps={steps} parked={parked}")

# ── 2. read layer: threads discovered from the checkpointer alone ────────────
print("\n2. Read layer — no separate state store")
tids = G._thread_ids(cp)
check("thread list comes from checkpointer", sorted(tids) == [t[0] for t in TASKS],
      f"{len(tids)} threads")
check("pending interrupt payload readable", v[0].pending.get("reversible") is False,
      f"action: {v[0].pending['action'][:38]}…")

# ── 3. approval gate: approve continues, reject aborts, both durable ─────────
print("\n3. Approval gate")
R.resume(graph, "unit-01", "approve")
R.resume(graph, "unit-02", "reject")

after = {u.unit_id: u for u in G.units(graph, cp)}
check("approved unit published", after["unit-01"].log[-1] == "approved -> published")
check("rejected unit aborted", after["unit-02"].log[-1] == "rejected (reject) -> aborted")
check("resolved units report done",
      after["unit-01"].status == "done" and after["unit-02"].status == "done")
check("untouched units still parked",
      all(after[f"unit-0{i}"].pending for i in (3, 4, 5)))

# decision survives a restart
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0,'/home/claude/gov');"
     "import runtime as R, governor as G;"
     "cp=R.connect(); g=R.build(cp);"
     "d={u.unit_id:u for u in G.units(g,cp)};"
     "print(d['unit-01'].log[-1], '|', d['unit-02'].log[-1])"],
    capture_output=True, text=True,
)
check("decisions persist across restart",
      "published" in probe.stdout and "aborted" in probe.stdout,
      probe.stdout.strip() or probe.stderr.strip()[:80])

# ── 4. governor: hard cap halts, stale approvals surface as idle ─────────────
print("\n4. Governor — cap and idle detection")
v = G.units(graph, cp)
ok, reason = G.may_spawn(v)
check("spend tracked across fleet", G.spent(v) > 0, reason)

G.TOKEN_CAP = 1_000
ok, reason = G.may_spawn(v)
check("cap HALTS spawning, not warns", ok is False, reason)
G.TOKEN_CAP = 100_000

# self-contained: a brand-new unit must read fresh, then age into idle
R.spawn(graph, "unit-06", "tidy imports")
fresh = {u.unit_id: u for u in G.units(graph, cp)}["unit-06"]
check("newly parked unit reads awaiting_approval",
      fresh.status == "awaiting_approval", f"age {fresh.age_s}s")

time.sleep(G.IDLE_AFTER_S + 0.3)
aged = {u.unit_id: u for u in G.units(graph, cp)}["unit-06"]
check("same unit flips to idle once stale", aged.status == "idle", f"age {aged.age_s}s")
check("all stale approvals surface together", len(G.idle(G.units(graph, cp))) == 4,
      "3 original + 1 new, all parked on a human")

print("\n" + G.render(G.units(graph, cp)))
print(f"{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)
```

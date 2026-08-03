"""The governor: a read view over the checkpointer, plus the two controls."""

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

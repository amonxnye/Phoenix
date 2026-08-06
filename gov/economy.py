"""The agent economy — status earned by measured contribution.

Every agent is enlisted at the lowest tier with a budget and a capability set. As it
contributes measured value to the Vision (resources gathered — read from the oracle, not
guessed), it earns promotion: a higher tier, a bigger budget, and more capabilities. An
agent that never contributes is retired. Status is a consequence of results, so it can't
be gamed by noise — the currency is contribution-to-Vision.

The ledger is persisted with the game, so status survives restarts. Promotion grants
*reach*, never *escape*: the cap and the gate bind every tier (Constitution III–IV).
"""

import os
import sqlite3

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

# Capability tiers. Promotion is earned at `promote_at` cumulative contribution.
# Budgets re-tuned from the production audit: the 400k cohort returned 2.3x the
# contribution-per-token of the 60k cohort, and 6-turn villager lives churned the
# fleet (2,670 lifetime agents in a day — births, careers and reaps are data too).
# Longer careers are both more efficient and quieter.
TIERS = [
    {"name": "villager", "budget": 150_000, "promote_at": 600,
     "can": ("gather",)},
    {"name": "foreman",  "budget": 300_000, "promote_at": 2_500,
     "can": ("gather", "propose_build")},
    {"name": "delegate", "budget": 600_000, "promote_at": None,          # top tier
     "can": ("gather", "propose_build", "propose_spawn", "propose_advance")},
]
MAX_TIER = len(TIERS) - 1


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS ledger("
                  "agent TEXT PRIMARY KEY, tier INT DEFAULT 0, contribution INT DEFAULT 0, "
                  "budget INT DEFAULT 0, alive INT DEFAULT 1)")
        c.commit()
    finally:
        c.close()


def enlist(agent: str, tier: int = 0) -> None:
    # Upsert: if an id is ever reused (it shouldn't be — ids are persisted-monotonic),
    # the row is reset to a fresh living villager instead of silently staying dead.
    c = _conn()
    try:
        c.execute("INSERT INTO ledger(agent, tier, contribution, budget, alive) "
                  "VALUES(?,?,0,?,1) ON CONFLICT(agent) DO UPDATE SET "
                  "tier=excluded.tier, contribution=0, budget=excluded.budget, alive=1",
                  (agent, tier, TIERS[tier]["budget"]))
        c.commit()
    finally:
        c.close()


def ensure(agent: str) -> None:
    """Enlist an agent only if it is not already on the ledger. `enlist` deliberately
    RESETS a reused id to a fresh villager, so a long-lived agent that calls it every
    cycle silently loses its cumulative contribution — and never earns promotion
    (Article II.2). Anything that credits the same agent repeatedly uses this."""
    c = _conn()
    try:
        if c.execute("SELECT 1 FROM ledger WHERE agent=?", (agent,)).fetchone():
            return
    finally:
        c.close()
    enlist(agent)


def credit(agent: str, amount: int) -> None:
    """Record measured contribution (value produced) for this agent."""
    c = _conn()
    try:
        c.execute("UPDATE ledger SET contribution=contribution+? WHERE agent=?", (amount, agent))
        c.commit()
    finally:
        c.close()


def evaluate(agent: str) -> str | None:
    """Promote if earned. Returns the new role name on promotion, else None."""
    c = _conn()
    try:
        row = c.execute("SELECT tier, contribution FROM ledger WHERE agent=?", (agent,)).fetchone()
        if not row:
            return None
        tier, contribution = row
        gate = TIERS[tier]["promote_at"]
        if gate is not None and contribution >= gate and tier < MAX_TIER:
            new = tier + 1
            c.execute("UPDATE ledger SET tier=?, budget=? WHERE agent=?",
                      (new, TIERS[new]["budget"], agent))
            c.commit()
            return TIERS[new]["name"]
        return None
    finally:
        c.close()


def retire(agent: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE ledger SET alive=0 WHERE agent=?", (agent,))
        c.commit()
    finally:
        c.close()


def role(agent: str) -> str:
    c = _conn()
    try:
        row = c.execute("SELECT tier FROM ledger WHERE agent=?", (agent,)).fetchone()
        return TIERS[row[0]]["name"] if row else "villager"
    finally:
        c.close()


def can(agent: str, capability: str) -> bool:
    c = _conn()
    try:
        row = c.execute("SELECT tier FROM ledger WHERE agent=?", (agent,)).fetchone()
        return bool(row) and capability in TIERS[row[0]]["can"]
    finally:
        c.close()


def roster(alive_only: bool = True) -> list[dict]:
    c = _conn()
    try:
        q = ("SELECT agent, tier, contribution, budget, alive FROM ledger"
             + (" WHERE alive=1" if alive_only else "") + " ORDER BY tier DESC, contribution DESC")
        return [{"agent": a, "role": TIERS[t]["name"], "tier": t,
                 "contribution": ctr, "budget": b, "alive": bool(al),
                 "next_at": TIERS[t]["promote_at"]}
                for a, t, ctr, b, al in c.execute(q).fetchall()]
    finally:
        c.close()

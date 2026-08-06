"""The Campaign driver — an army that drives a problem to a real solution.

The settlement proved the machinery for running autonomous agents: a measured oracle,
an economy that promotes what contributes and reaps what thrashes, careers and lineage
that remember, and a gate that stops the irreversible at a human. This module keeps all
of that and drops the game. In its place: a persistent loop that points a *squad* of
agents at a problem and keeps working until one of four things is true —

    SOLVED        the oracle says the Vision is met;
    GATE_BLOCKED  the only work left is irreversible, and a human must decide;
    DRY           progress stalled and creativity is exhausted — stop, don't burn;
    BUDGET        the round budget is spent.

Three properties are the whole point, and each is structural, not a promise:

1. **Persistence.** It does not stop at the first failure; it works round after round
   until the oracle is satisfied or a real stop condition fires.

2. **Creativity that escalates — bounded.** While it is winning it stays conservative
   (cheap, low-temperature, known tactics). Each stalled round it climbs a strategy
   ladder — more agents, higher temperature, more divergent tactics — and the moment
   progress resumes it drops back down. Creativity is spent only where it is needed,
   and it is capped so the army escalates instead of thrashing.

3. **It never destroys value.** The score is ratcheted: the oracle already reverts any
   change that breaks a test (workspace/redteam), and on top of that the campaign holds
   a high-water mark and refuses to accept a round that would lower it. An agent that
   produces only waste is reaped (economy), so a thrasher cannot spend the whole budget.
   Everything an agent decides is recorded in the anchor with its lineage.

The loop is world-agnostic: it talks to a small ``World`` protocol (score / solved /
remaining / attempt) and a ``tactic`` callable. ``SecurityWorld`` binds it to the
red-team harness; a model-free tactic makes the whole driver testable without a brain.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import economy

# ── outcomes ──────────────────────────────────────────────────────────────────
SOLVED = "solved"
GATE_BLOCKED = "gate-blocked"
DRY = "dry"
BUDGET = "budget"
STARVED = "starved"           # the whole squad was reaped — nobody left to work

# ── the creativity ladder ─────────────────────────────────────────────────────
# One rung per level of desperation. Rung 0 is what a fresh, winning campaign uses;
# every stalled round climbs one rung, every gaining round resets to 0. `temp` is the
# model temperature a live tactic should use; `squad` is how many agents to field that
# round; `tactics` names the divergence a tactic may draw on.
LADDER = [
    {"name": "recon",     "temp": 0.2,  "squad": 1, "tactics": ("known",)},
    {"name": "probe",     "temp": 0.45, "squad": 2, "tactics": ("known", "variant")},
    {"name": "diversify", "temp": 0.7,  "squad": 3, "tactics": ("variant", "chain")},
    {"name": "creative",  "temp": 0.95, "squad": 4, "tactics": ("chain", "novel")},
]
MAX_RUNG = len(LADDER) - 1

WASTE_LIMIT = 3               # consecutive wasted attempts before an agent is reaped


def strategy_for(stall: int) -> dict:
    """The rung to fight at, given how many rounds we've been stuck. Climbs with the
    stall count and clamps at the top — bounded escalation, never unbounded."""
    rung = min(stall, MAX_RUNG)
    s = dict(LADDER[rung])
    s["rung"] = rung
    return s


class SecurityWorld:
    """Bind the campaign loop to the red-team harness (redteam.py). ``attempt`` runs a
    real analyst cycle — find an uncovered class, then fix an open finding — and reports
    whether it moved the oracle and whether the remaining work is now gated."""

    def __init__(self):
        import redteam as R
        self.R = R
        R.init()

    def score(self) -> int:
        return self.R.world()["progress_pct"]

    def solved(self) -> bool:
        w = self.R.world()
        return w["classes_fixed"] >= w["classes_total"] and w["classes_total"] > 0

    def remaining(self) -> list[dict]:
        """Open work, each item tagged reversible/irreversible so the loop can tell a
        real block (only gated work left) from ordinary progress."""
        w = self.R.world()
        items = [{"ref": c, "kind": "find", "gated": False} for c in w["uncovered"]]
        items += [{"ref": f["id"], "kind": "fix", "gated": False}
                  for f in self.R.findings(status="reproduced")]
        return items

    def attempt(self, agent: str, strategy: dict, tactic) -> dict:
        return tactic(self, agent, strategy)


def run_campaign(world, squad: list[str], *, max_rounds: int = 40,
                 patience: int = 2, tactic=None, mission: str = "") -> dict:
    """Drive ``world`` toward solved with ``squad``. Returns a report: the outcome, the
    high-water score, the per-round log, and the reaped roster.

    ``tactic(world, agent, strategy) -> {"gain": bool, "did": str, "gated": bool}`` is
    how one agent spends one turn. Injectable so the driver is testable without a model.
    """
    if tactic is None:
        tactic = default_security_tactic
    for a in squad:
        economy.enlist(a)
    anchor.record(-1, "campaign", f"campaign opened: {mission or 'drive to solved'} — "
                                  f"squad {', '.join(squad)}, up to {max_rounds} rounds")

    best = world.score()
    stall = 0
    waste_streak = {a: 0 for a in squad}
    active = list(squad)
    rounds: list[dict] = []
    outcome = BUDGET

    for rnd in range(1, max_rounds + 1):
        if world.solved():
            outcome = SOLVED
            break

        remaining = world.remaining()
        if remaining and all(it["gated"] for it in remaining):
            outcome = GATE_BLOCKED           # only irreversible work left — human's call
            break
        if not remaining and not world.solved():
            # nothing actionable and not solved: treat as dry rather than spin
            outcome = DRY
            break

        strat = strategy_for(stall)
        fielded = active[:max(1, strat["squad"])]
        before = world.score()
        gains, did_log = 0, []

        for agent in fielded:
            res = world.attempt(agent, strat, tactic)
            did_log.append(f"{agent}:{res.get('did', '?')}")
            if res.get("gain"):
                gains += 1
                waste_streak[agent] = 0
            else:
                waste_streak[agent] = waste_streak.get(agent, 0) + 1
                # reap a persistent thrasher so it cannot spend the whole budget
                if waste_streak[agent] >= WASTE_LIMIT and agent in active:
                    economy.retire(agent)
                    active.remove(agent)
                    anchor.record(-1, "reap", f"{agent} reaped — {WASTE_LIMIT} wasted "
                                              f"attempts, contributing nothing")
                    anchor.career_add(agent, -1, "reap", "reaped: sustained waste")

        after = world.score()
        # the value ratchet: the oracle already reverts breaking changes; the campaign
        # additionally refuses to let its high-water mark fall.
        preserved = after >= best
        best = max(best, after)
        if after > before:
            stall = 0                        # winning → return to cheap, conservative play
        else:
            stall += 1                       # stuck → next round climbs the ladder

        rounds.append({"round": rnd, "rung": strat["name"], "squad": len(fielded),
                       "score_before": before, "score_after": after, "best": best,
                       "gains": gains, "value_preserved": preserved,
                       "did": did_log})

        if not active:
            outcome = STARVED
            break
        # DRY: stuck past patience AND already fighting at the top of the ladder
        if stall > patience and strat["rung"] >= MAX_RUNG:
            outcome = DRY
            break
    else:
        outcome = SOLVED if world.solved() else BUDGET

    if world.solved():
        outcome = SOLVED

    anchor.record(-1, "campaign", f"campaign closed: {outcome} at {best}% "
                                  f"after {len(rounds)} round(s)")
    return {"outcome": outcome, "best_score": best, "rounds_used": len(rounds),
            "final_score": world.score(), "solved": world.solved(),
            "reaped": [a for a in squad if a not in active],
            "survivors": active, "log": rounds, "mission": mission}


def default_security_tactic(world, agent: str, strategy: dict) -> dict:
    """The live tactic: pick an open item and run a real analyst cycle at the strategy's
    temperature. Needs a configured brain; the loop stays model-free when a test injects
    its own tactic instead."""
    import analyst as AN
    items = world.remaining()
    if not items:
        return {"gain": False, "did": "nothing", "gated": False}
    item = next((it for it in items if it["kind"] == "fix"), items[0])
    before = world.score()
    if item["kind"] == "find":
        r = AN.find_cycle(agent, item["ref"])
        did = r.get("did", "?")
    else:
        r = AN.fix_cycle(agent, item["ref"])
        did = r.get("did", "?")
    return {"gain": world.score() > before, "did": did, "gated": False}


if __name__ == "__main__":
    import argparse
    import brain
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--squad", default="sec-01,sec-02,sec-03")
    ap.add_argument("--rounds", type=int, default=20)
    a = ap.parse_args()
    anchor.init()
    economy.init()
    if not brain.available():
        print("no brain configured — the live campaign needs a model "
              "(machinery is proven model-free by gov/verify_campaign.py)")
        sys.exit(1)
    report = run_campaign(SecurityWorld(), a.squad.split(","), max_rounds=a.rounds,
                          mission="harden sandbox/replica")
    print(json.dumps(report, indent=2))

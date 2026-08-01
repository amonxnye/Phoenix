"""The board of governors — quorum for the powers that can blow the budget.

A single approver is a single point of failure for the one decision that runs away:
creating agents. So the token-maxing powers are routed to a BOARD that must reach
QUORUM, not to one Governor. Each governor weighs a proposal from a different stance:

  * Prudence — risk: is there budget headroom and side-effect room?
  * Growth   — mission: does this serve the Vision?
  * Ledger   — money: can we actually pay for it?

The board approves only if a quorum agrees. It supervises the main Governor: powers that
lead to token-maxing (agent creation, and later self-spawning) go here instead of to one
decider (Constitution, amended).
"""

import vision as V

GOVERNORS = ("Prudence", "Growth", "Ledger")
QUORUM = 2


def propose_vision(sc: dict, spend_ratio: float, current_key: str) -> dict | None:
    """The Board watches progress and recommends a Vision change to the human.

    It only *proposes*; adopting a new Vision is the human's power alone (Constitution I).
    Returns {action, vision, name, why} or None (hold).
    """
    if sc["goal_met"] and spend_ratio < 0.7 and sc["within_budget"]:
        nxt = V.MORE_AMBITIOUS.get(current_key)
        if nxt:
            return {"action": "Aim higher", "vision": nxt, "name": V.get(nxt).name,
                    "why": "vision met with budget to spare — the board recommends a bolder goal"}
    if (not sc["within_budget"] or spend_ratio > 0.9) and current_key != "consolidate":
        return {"action": "Consolidate", "vision": "consolidate", "name": V.get("consolidate").name,
                "why": "side-effects or spend running high — the board recommends consolidating"}
    return None


def vote(proposal: str, ctx: dict) -> dict:
    """ctx: aligned(bool), affordable(bool), within_budget(bool), spent(int), cap(int)."""
    spend_ratio = ctx["spent"] / ctx["cap"] if ctx.get("cap") else 1.0
    ballots = {
        "Prudence": bool(ctx.get("within_budget")) and spend_ratio < 0.9,
        "Growth":   bool(ctx.get("aligned")),
        "Ledger":   bool(ctx.get("affordable")),
    }
    yes = sum(ballots.values())
    return {
        "proposal": proposal,
        "ballots": ballots,
        "yes": yes,
        "quorum": QUORUM,
        "approved": yes >= QUORUM,
        "tally": f"{yes}/{len(GOVERNORS)}",
    }

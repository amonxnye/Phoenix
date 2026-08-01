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

GOVERNORS = ("Prudence", "Growth", "Ledger")
QUORUM = 2


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

"""The board of governors — quorum for the powers that can blow the budget.

A single approver is a single point of failure for the one decision that runs away:
creating agents. So the token-maxing powers are routed to a BOARD that must reach
QUORUM, not to one Governor. Article VIII (as amended): each governor judges from a
DISJOINT EVIDENCE SET it alone reads, may return unknown, and a member whose vote
never varies is flagged as non-functional — three seats must be three signals, not
one `if` wearing three hats.

  * Prudence — runway: at the current burn, how many turns until the cap? Plus the
    side-effect budget. Votes NO when runway is short or side-effects are blown.
  * Growth   — momentum: has the Vision score moved lately, and is the fleet under
    strength? Votes YES to invest when progress is alive or staffing is the blocker.
  * Ledger   — cash: can we actually afford this one action right now?

The board approves only on quorum. It also has a DUTY TO ESCALATE (VIII.4): a power
it blocks three times for the same unchanged reason is reported once to the human —
the board may withhold a power, never the fact that it did.
"""

from collections import deque

import models
import vision as V

GOVERNORS = ("Prudence", "Growth", "Ledger")
QUORUM = 2
DEGENERACY_WINDOW = 20          # a member constant across this many votes is flagged

_history: dict[str, deque] = {g: deque(maxlen=DEGENERACY_WINDOW) for g in GOVERNORS}


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


def propose_cap(spend_ratio: float, sc: dict, cap: int) -> dict | None:
    """The Board watches the spend cap and recommends a change to the human."""
    if spend_ratio > 0.9 and not sc["goal_met"]:
        return {"action": "Raise the cap", "cap": int(cap * 1.5),
                "why": "spend near the ceiling while the goal is unfinished"}
    if spend_ratio < 0.3 and sc["side_effects"] == 0 and cap > 50_000:
        return {"action": "Tighten the cap", "cap": int(cap * 0.75),
                "why": "plenty of headroom — tighten to enforce discipline"}
    return None


def vote(proposal: str, ctx: dict) -> dict:
    """Quorum vote from three DISJOINT evidence sets.

    ctx: affordable(bool)         — Ledger's evidence: can we pay for this action
         spent(int), cap(int)     — shared treasury numbers
         burn_per_turn(int)       — Prudence's evidence: current fleet burn
         within_budget(bool)      —   "        "       : side-effect budget intact
         progress_delta(int|None) — Growth's evidence: Vision points moved recently
         understaffed(bool)       —   "       "      : fleet below target
         offline(list[str])       — seats whose assigned model is failing (Article X.3)

    Members may vote None (unknown) when their evidence is missing; quorum counts
    only yes votes, so unknown never approves anything.

    A seat whose model is OFFLINE abstains and says so. It is never filled by another
    model: a silent substitution would change who governs, in secret (Article X.3).
    """
    cap = ctx.get("cap") or 0
    spent = ctx.get("spent", 0)
    burn = ctx.get("burn_per_turn")

    # Prudence: runway and volatility. Short runway or blown side-effect budget → NO.
    if burn is None:
        prudence, p_why = None, "runway unknown — no burn telemetry"
    else:
        runway = (cap - spent) / burn if burn > 0 else 999
        if not ctx.get("within_budget", True):
            prudence, p_why = False, "side-effect budget blown"
        elif runway < 5:
            prudence, p_why = False, f"runway {runway:.0f} turns at current burn"
        else:
            prudence, p_why = True, f"runway {runway:.0f} turns — safe"

    # Growth: momentum and opportunity. Progress alive or staffing short → YES.
    delta = ctx.get("progress_delta")
    if delta is None:
        growth, g_why = (True if ctx.get("understaffed") else None), \
            "no progress telemetry" + (" but fleet is short — invest" if ctx.get("understaffed") else "")
    elif delta > 0:
        growth, g_why = True, f"vision moved +{delta}% recently — momentum is real"
    elif ctx.get("understaffed"):
        growth, g_why = True, "progress flat but the fleet is under strength — staffing is the blocker"
    else:
        growth, g_why = False, f"vision flat ({delta:+d}%) with a full fleet — more of the same won't move it"

    # Ledger: cash. Can we afford this one action, right now? The rationale always
    # carries the live numbers — a reason that never varies is a label, not a reason.
    ledger = bool(ctx.get("affordable"))
    committed = round(100 * spent / cap) if cap else 100
    l_why = (f"affordable — {spent:,}/{cap:,} committed ({committed}%)" if ledger
             else f"cannot afford it — {spent:,}/{cap:,} committed ({committed}%)")

    ballots = {"Prudence": prudence, "Growth": growth, "Ledger": ledger}
    reasons = {"Prudence": p_why, "Growth": g_why, "Ledger": l_why}
    off = ctx.get("offline")
    if off is None:                    # ask the switchboard directly — the rule holds
        off = models.offline_seats()   # at every call site, present and future
    offline = [g for g in off if g in GOVERNORS]
    for g in offline:                  # an outage is an abstention, never a substitution
        ballots[g] = None
        reasons[g] = "model unavailable — abstains; the seat is not filled by another"
    for g in GOVERNORS:
        _history[g].append(ballots[g])
    yes = sum(1 for v in ballots.values() if v is True)
    return {
        "proposal": proposal,
        "ballots": ballots,
        "reasons": reasons,
        "yes": yes,
        "quorum": QUORUM,
        "approved": yes >= QUORUM,
        "tally": f"{yes}/{len(GOVERNORS)}",
        "degenerate": degenerate_members(),
        "abstained": offline,
    }


def degenerate_members() -> list[str]:
    """Members whose vote has not varied across the window — decoration, not judgement
    (Article VIII.1 as amended). Reported so the human can see the board is real."""
    out = []
    for g, h in _history.items():
        if len(h) == DEGENERACY_WINDOW and len({v for v in h}) == 1:
            out.append(g)
    return out

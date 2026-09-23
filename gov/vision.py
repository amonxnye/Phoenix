"""The vision — the organization's goal-setter.

Every organization needs a vision, one number everyone drives toward, and a way to tell
whether the work is actually paying for itself. This module is that layer above the
fleet:

  * a VISION with an explicit, measurable target (the goal),
  * PROGRESS — one 0–100% score the whole settlement is trying to reach,
  * a SIDE-EFFECT budget — negative outcomes (waste, halts, blocks) must stay under it,
  * VALUE CREATED — surplus produced beyond the goal, so we can ask for a little extra.

Progress is the oracle made explicit: an objective, cheap, instant measure of "are we
winning?" The director drives toward 100%; the governor keeps the side-effects in check.
"""

from dataclasses import dataclass

# Ten leaps. The legacy four (Dark/Feudal/Castle/Imperial) keep their names where
# they fit so a running world keeps its place; "Dark Age" reads as "Stone Age".
AGES = ["Stone Age", "Tool Age", "Bronze Age", "Iron Age", "Classical Age",
        "Feudal Age", "Castle Age", "Imperial Age", "Industrial Age", "Tech Age"]
LEGACY_AGES = {"Dark Age": "Stone Age"}


@dataclass(frozen=True)
class Vision:
    name: str
    target_age: str          # advance the civilization to here
    target_buildings: int    # develop at least this much
    target_resources: int    # bank at least this total stockpile
    max_side_effects: int    # negatives allowed before we say the plan is failing
    # weights for the scorecard (must sum to 1.0)
    w_age: float = 0.5
    w_dev: float = 0.25
    w_econ: float = 0.25


# The default organizational goal. Swap this object to re-point the whole fleet.
GOAL = Vision(
    name="Prosper into the Tool Age",
    target_age="Tool Age",
    target_buildings=5,
    target_resources=1200,
    max_side_effects=5,
)

# The menu of visions the Board can propose and the human can adopt. Adopting one
# re-briefs every agent downstream — to push harder (a bolder goal) or ease off
# (consolidate). Only the human adopts; the Board only proposes (Constitution I).
VISIONS = {
    "consolidate": Vision("Consolidate — stabilise and bank", "Stone Age", 5, 800, 3),
    "tool":        GOAL,
    "bronze":      Vision("Cast bronze — reach the Bronze Age", "Bronze Age", 5, 1000, 4),
    "iron":        Vision("Forge iron — reach the Iron Age", "Iron Age", 5, 1100, 5),
    "classical":   Vision("Build the classical city", "Classical Age", 5, 1200, 5),
    "feudal":      Vision("Prosper into the Feudal Age", "Feudal Age", 5, 1200, 5),
    "castle":      Vision("Ascend to the Castle Age", "Castle Age", 6, 2000, 6),
    "imperial":    Vision("Empire — reach the Imperial Age", "Imperial Age", 7, 3000, 8),
    "industrial":  Vision("Industrialise — reach the Industrial Age", "Industrial Age", 8, 4000, 9),
    "tech":        Vision("The Tech Age", "Tech Age", 9, 5000, 10),
}
DEFAULT_VISION = "tool"
_LADDER = ["consolidate", "tool", "bronze", "iron", "classical", "feudal", "castle", "imperial", "industrial", "tech"]
MORE_AMBITIOUS = {a: b for a, b in zip(_LADDER, _LADDER[1:])}


def get(key: str) -> Vision:
    return VISIONS.get(key, GOAL)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def scorecard(world: dict, structures: dict, side_effects: int, goal: Vision = GOAL) -> dict:
    """The single readout: how close are we to 100% of the vision, and at what cost?"""
    age = LEGACY_AGES.get(world["age"], world["age"])
    age_idx = AGES.index(age) if age in AGES else 0
    target_idx = AGES.index(goal.target_age)
    age_pct = _clamp01(age_idx / target_idx) if target_idx else 1.0

    dev = sum(structures.values())
    dev_pct = _clamp01(dev / goal.target_buildings) if goal.target_buildings else 1.0

    stock = world["food"] + world["wood"] + world["gold"]
    econ_pct = _clamp01(stock / goal.target_resources) if goal.target_resources else 1.0

    progress = round(100 * (goal.w_age * age_pct + goal.w_dev * dev_pct + goal.w_econ * econ_pct))

    surplus = max(0, stock - goal.target_resources)
    extra_value_pct = round(100 * surplus / goal.target_resources, 2) if goal.target_resources else 0.0

    return {
        "vision": goal.name,
        "progress": progress,                 # 0..100 — the number everyone drives to
        "age_pct": round(100 * age_pct),
        "dev_pct": round(100 * dev_pct),
        "econ_pct": round(100 * econ_pct),
        "goal_met": progress >= 100,
        "side_effects": side_effects,
        "side_effect_budget": goal.max_side_effects,
        "within_budget": side_effects <= goal.max_side_effects,
        "extra_value_pct": extra_value_pct,   # value created beyond the goal
    }


def bar(progress: int, width: int = 24) -> str:
    filled = round(width * progress / 100)
    return "[" + "#" * filled + "·" * (width - filled) + f"] {progress:>3}%"

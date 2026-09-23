"""Lives — Article II as amended: agents live on.

An agent is not spent and discarded. It works, earns contribution, climbs the ranks
(villager → foreman → delegate → steward → leader), mentors those below it, and at
the top it starts projects of its own. When a working life's budget is used up the
agent is RENEWED — a fresh working season on the same identity, the same career, the
same rank — never retired by the system. Only a human may retire an agent.

Every agent has a temperament, fixed at birth from its name so it is the same across
restarts: a favourite resource, a curiosity, an ambition. That is its free will, and
it is exercised only inside the framework: an agent may follow its own inclination
when nothing binds — never against an operator's order, an age-up shortfall, a
resource floor or a lopsided economy. Every free choice is recorded as such.
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor                                    # noqa: E402
import economy                                   # noqa: E402

RESOURCES = ("food", "wood", "gold")
MENTOR_TIER = 3                                  # steward and above mentor
LEADER_TIER = 4                                  # leaders start projects
MENTOR_PCT = 10                                  # a mentored newcomer earns this much extra
BINDING = ("standing order", "shortfall", "floor", "lopsided", "short at")


def _h(uid: str, salt: str) -> float:
    d = hashlib.sha256(f"{uid}:{salt}".encode()).digest()
    return int.from_bytes(d[:4], "big") / 2 ** 32


def temperament(uid: str) -> dict:
    """Fixed at birth, from the name: the same agent is the same person after a restart."""
    return {"favourite": RESOURCES[int(_h(uid, "fav") * 3) % 3],
            "curiosity": round(0.08 + 0.22 * _h(uid, "cur"), 2),      # how often it follows itself
            "ambition": round(0.5 + _h(uid, "amb"), 2)}               # how hard it pushes to lead


def free_choice(uid: str, res: str, why: str, turn: int) -> tuple[str, str, bool]:
    """The agent may follow its own inclination — when, and only when, nothing binds.
    Returns (resource, why, chose_freely)."""
    if any(b in why for b in BINDING):
        return res, why, False                   # the rules bind; free will waits
    t = temperament(uid)
    if t["favourite"] == res or _h(uid, f"turn{turn}") >= t["curiosity"]:
        return res, why, False
    return (t["favourite"], f"{uid} chose {t['favourite']} of its own accord (curiosity "
            f"{t['curiosity']}); the director had suggested {res} — nothing bound the choice", True)


def mentors() -> list[dict]:
    return [r for r in economy.roster() if r["tier"] >= MENTOR_TIER]


def leaders() -> list[dict]:
    return [r for r in economy.roster() if r["tier"] >= LEADER_TIER]


def nurture(uid: str, got: int, turn: int) -> dict | None:
    """A newcomer working while a steward or leader lives is mentored: it earns extra
    contribution, and its mentor earns a share for the teaching."""
    me = next((r for r in economy.roster() if r["agent"] == uid), None)
    if not me or me["tier"] > 1:
        return None
    ms = [m for m in mentors() if m["agent"] != uid]
    if not ms:
        return None
    mentor = ms[int(_h(uid, f"m{turn}") * len(ms)) % len(ms)]["agent"]
    bonus = max(1, got * MENTOR_PCT // 100)
    economy.credit(uid, bonus)
    economy.credit(mentor, max(1, bonus // 2))
    anchor.career_add(uid, turn, "mentored", f"+{bonus} learning from {mentor}")
    anchor.career_add(mentor, turn, "mentor", f"+{max(1, bonus // 2)} teaching {uid}")
    return {"mentor": mentor, "bonus": bonus}


def project_leader(turn: int) -> str | None:
    """The leader whose turn it is to start a project — ambition decides the order."""
    ls = leaders()
    if not ls:
        return None
    ls.sort(key=lambda r: (-temperament(r["agent"])["ambition"], r["agent"]))
    return ls[turn % len(ls)]["agent"]

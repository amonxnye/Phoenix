"""The architect — Article XII. The world grows toward utopia on its own ideas.

Every civic work begins inside the world: the architect reads what the settlement
measures about itself (its five qualities, the weakest one, its age and treasury),
the lessons its agents have learned, and what it has already built, and designs ONE
work that answers the weakest quality. The design is bounded to what the world can
draw and compute:

    quality  — beauty · order · health · knowledge · harmony     (what it improves)
    value    — 5–30 points                                        (how much)
    shape    — plaza, garden, fountain, tower, temple, monument,
               lamp, grove, aqueduct, library, wall, road         (how the 3D worlds draw it)
    colour, scale, district                                       (where and how it looks)

The PRICE is not the model's to set. It is computed from the age: a civic work
costs a share of the current leap, so works stay meaningful as the world grows.

Designing costs model tokens, and those are managed: the architect's calls are
charged to a daily budget (UTOPIA_TOKENS_PER_DAY). Over budget — or with no model,
or with a reply that does not fit the vocabulary — the architect falls back to its
own pattern book for the weakest quality, and the record says which it was.

Nothing here adopts or builds. A design is a proposal: the Board votes on it, the
human adopts or rejects it, silence consents after the waiting period (IV.7), and
the director builds it when the treasury can pay.
"""

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor                                    # noqa: E402
import brain                                     # noqa: E402
import sim                                       # noqa: E402

TOKENS_PER_DAY = int(os.environ.get("UTOPIA_TOKENS_PER_DAY", "200000"))
PRICE_SHARE = float(os.environ.get("UTOPIA_PRICE_SHARE", "0.04"))   # of the current leap, per 10 points
ASK = None                                       # replaceable by the suite: (prompt) -> str

# The pattern book: what the world builds when it designs without a model. One entry
# per quality per stage of growth (early ages → later ages).
PATTERNS = {
    "beauty":    [("flower_garden", "garden", "#e07a9a", "nature"),
                  ("sculpture_court", "monument", "#d8c8a8", "centre"),
                  ("crystal_fountain", "fountain", "#7ac8e0", "centre")],
    "order":     [("paved_road", "road", "#8a7a66", "residential"),
                  ("watchtower", "tower", "#8a8272", "edge"),
                  ("city_wall", "wall", "#9a8e7a", "edge")],
    "health":    [("clean_well", "fountain", "#6ab0d8", "residential"),
                  ("aqueduct", "aqueduct", "#bfb39a", "nature"),
                  ("healing_grove", "grove", "#5aa05a", "nature")],
    "knowledge": [("scribes_hall", "library", "#b8a070", "centre"),
                  ("observatory", "tower", "#9ab0d0", "edge"),
                  ("great_library", "library", "#e0c890", "centre")],
    "harmony":   [("village_green", "plaza", "#7ab86a", "centre"),
                  ("lantern_walk", "lamp", "#ffc870", "residential"),
                  ("temple_of_accord", "temple", "#e8dcc0", "centre")],
}


def _price(age: str, value: int) -> dict:
    """What a work of `value` points costs at this age: a share of the current leap,
    at least a floor so the first works are not free."""
    leap = sim.advance_cost(age)
    k = PRICE_SHARE * value / 10
    return {r: max(40, int(round(v * k, -1))) for r, v in leap.items()}


def _rank(value: int) -> int:
    return 2 if value < 12 else 3 if value < 22 else 4


def tokens_spent_today() -> int:
    """The architect's model tokens in the last 24 hours, from the permanent call log."""
    c = anchor._conn()
    try:
        row = c.execute("SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM model_calls "
                        "WHERE purpose='civic-design' AND ts >= ?", (time.time() - 86400,)).fetchone()
        return int(row[0] or 0)
    except anchor.sqlite3.OperationalError:
        return 0
    finally:
        c.close()


def budget() -> dict:
    spent = tokens_spent_today()
    return {"per_day": TOKENS_PER_DAY, "spent_24h": spent, "left": max(0, TOKENS_PER_DAY - spent)}


def _existing() -> set:
    return {d["name"] for d in sim.dev_catalog()}


def _pattern(q: str, age_idx: int, existing: set) -> tuple[str, str, str, str] | None:
    stage = min(2, age_idx // 3)
    for name, shape, color, district in PATTERNS[q][stage:] + PATTERNS[q][:stage]:
        if name not in existing:
            return name, shape, color, district
    return None


SCHEMA = ('Reply with ONLY a JSON object: {"name": snake_case, new; "quality": one of '
          '["beauty","order","health","knowledge","harmony"]; "value": int 5-30; "shape": one of '
          '["plaza","garden","fountain","tower","temple","monument","lamp","grove","aqueduct",'
          '"library","wall","road"]; "color": "#rrggbb"; "scale": number 0.6-2.0; "district": one of '
          '["centre","residential","nature","industry","edge"]; "why": one sentence tying the work '
          'to the settlement\'s measured need}. The price is set by the world, not by you.')


def _ask(prompt: str) -> str:
    if ASK is not None:
        return ASK(prompt)
    return brain._chat([{"role": "user", "content": prompt}], 600, 0.7, "civic-design")


def _parse(text: str, existing: set) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    name = re.sub(r"[^a-z0-9_]", "", str(d.get("name", "")).lower().replace(" ", "_"))[:32]
    if not name or name in existing or d.get("quality") not in sim.UTOPIA_QUALITIES:
        return None
    if d.get("shape") not in sim.UTOPIA_SHAPES:
        return None
    try:
        value = max(5, min(30, int(d.get("value", 10))))
    except (TypeError, ValueError):
        return None
    return {"name": name, "quality": d["quality"], "value": value,
            "visual": sim.clean_visual(d), "why": str(d.get("why", ""))[:200]}


def design(situation: str = "") -> dict | None:
    """One civic work, from the world's own ideas. Returns a proposal in the shape the
    console's development pipeline expects, or None if nothing new can be designed."""
    w = sim.world()
    u = sim.utopia_state()
    age = sim.canonical_age(w["age"])
    age_idx = sim.AGE_ORDER.index(age) if age in sim.AGE_ORDER else 0
    existing = _existing()
    need = u["weakest"]
    lessons = [s["lesson"] for s in anchor.skills_top(4)] if hasattr(anchor, "skills_top") else []
    how, got = "pattern book", None
    b = budget()
    if not brain.available() and ASK is None:
        how = "pattern book (no model)"
    elif b["left"] <= 0:
        how = f"pattern book (design budget spent: {b['spent_24h']:,}/{b['per_day']:,} tokens today)"
        anchor.record(-1, "civic-budget", f"architect over its token budget — {b['spent_24h']:,} of {b['per_day']:,}")
    else:
        built = [d["name"] for d in sim.custom_devs() if d["kind"] == "utopia" and d["built"]]
        prompt = (f"Role: the architect of a settlement growing toward a utopia. Design ONE civic work.\n"
                  f"Age: {age}. Treasury: food {w['food']:,}, wood {w['wood']:,}, gold {w['gold']:,}.\n"
                  f"The city's qualities (0-100): {json.dumps(u['qualities'])}; index {u['index']}; "
                  f"balance {u['balance']}. The weakest quality is {need} — answer it unless the "
                  f"lessons below argue otherwise.\n"
                  f"Civic works already built: {', '.join(built[-12:]) or 'none'}.\n"
                  f"Lessons the settlement has learned: {' | '.join(lessons) or 'none yet'}.\n"
                  f"{situation[:400]}\n\n{SCHEMA}")
        try:
            got = _parse(_ask(prompt), existing)
            how = "architect (model)" if got else "pattern book (the model's design did not fit the vocabulary)"
        except Exception as e:                    # noqa: BLE001 — the pattern book stands in
            how = f"pattern book (model call failed: {type(e).__name__})"
    if not got:
        pat = _pattern(need, age_idx, existing)
        if not pat:                               # every pattern for the weakest quality exists
            for q in sim.UTOPIA_QUALITIES:
                pat = _pattern(q, age_idx, existing)
                if pat:
                    need = q
                    break
        if not pat:
            return None
        name, shape, color, district = pat
        value = 10 + 5 * min(2, age_idx // 3)
        got = {"name": name, "quality": need, "value": value,
               "visual": sim.clean_visual({"shape": shape, "color": color, "scale": 1.0 + 0.15 * min(4, age_idx // 2),
                                            "district": district}),
               "why": f"the city's weakest quality is {need} ({u['qualities'][need]}/100)"}
    return {"name": got["name"], "cost": _price(age, got["value"]), "kind": "utopia", "value": got["value"],
            "resource": got["quality"], "rank": _rank(got["value"]), "visual": got["visual"],
            "why": got["why"], "source": f"internal idea — {how}", "civic": True}

"""Age of Empires MVP — a living, developing game economy the Governor oversees.

Villagers GATHER food/wood/gold (reversible, free-running) and, when they finish a
quota, park awaiting orders — the idle alert — and can be RE-TASKED back to work, so
the fleet stays alive instead of freezing. The settlement DEVELOPS over time: houses
raise the population cap, resource camps and the wheelbarrow tech raise yields, so the
economy grows. Advancing the Age spends resources irreversibly, so it stops at the
human gate.

The game state (the ``world`` table) is the oracle; the director (``director.py``)
drives it turn by turn and the anchor (``anchor.py``) accumulates what it learns.
This module reuses ``governor.py`` unchanged: ``tokens`` is compute/effort (capped),
food/wood/gold are the economy.
"""

import json
import os
import sqlite3
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

# Defaults next to this file; set GOV_DATA_DIR (e.g. a Railway volume mount like /data)
# to persist game state across redeploys. The directory is created if missing, and if
# it can't be written (e.g. GOV_DATA_DIR set but no volume mounted) we fall back to the
# module directory instead of crash-looping.
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

RESOURCES = ("food", "wood", "gold")
BASE = {"food": 20, "wood": 15, "gold": 8}           # base yield per gather round
CAMP_FOR = {"food": "mill", "wood": "lumber_camp", "gold": "mining_camp"}
QUOTA = 3                                            # rounds before a villager parks
ADVANCE_COST = {"food": 500, "gold": 300}            # Dark→Feudal base price
ADVANCE_GROWTH = 100                                 # each later age costs 100x the one before
AGE_ORDER = ("Dark Age", "Feudal Age", "Castle Age", "Imperial Age")
NEXT_AGE = {"Dark Age": "Feudal Age", "Feudal Age": "Castle Age", "Castle Age": "Imperial Age"}


def advance_cost(age: str | None = None) -> dict:
    """Price of advancing OUT of `age` (current world age when None). Real growth is
    exponential, not linear: every age costs 100-fold the one before, so each era is a
    genuine accumulation project — Feudal 500 food, Castle 50,000, Imperial 5,000,000."""
    if age is None:
        age = world()["age"]
    mult = ADVANCE_GROWTH ** AGE_ORDER.index(age) if age in AGE_ORDER else 1
    return {r: v * mult for r, v in ADVANCE_COST.items()}

# What the settlement can develop. Buildings are reversible (you can demolish), so they
# run free; only advancing the Age is gated. rank orders the tree (I = foundations).
STRUCTURES = {
    "house":        {"cost": {"wood": 50},               "effect": "+2 population cap",  "rank": 1},
    "mill":         {"cost": {"wood": 100},              "effect": "+50% food yield",    "rank": 1},
    "lumber_camp":  {"cost": {"wood": 80},               "effect": "+50% wood yield",    "rank": 1},
    "mining_camp":  {"cost": {"wood": 120},              "effect": "+50% gold yield",    "rank": 1},
    "wheelbarrow":  {"cost": {"food": 100, "wood": 100}, "effect": "+25% all yields",    "rank": 2},
}

# Effect vocabulary for governor-proposed developments — machine-usable by design so a
# proposal can actually change the world: yield_pct (one resource), all_yield_pct, pop_cap.
CUSTOM_KINDS = ("yield_pct", "all_yield_pct", "pop_cap")
_COLUMNS = ("food", "wood", "gold", "house", "mill", "lumber_camp", "mining_camp", "wheelbarrow")


def _append(a: list, b: list) -> list:
    return (a or []) + (b or [])


class Unit(TypedDict):
    uid: str
    role: str            # villager | herald
    resource: str        # villager: food | wood | gold
    task: str
    steps: int           # gather rounds toward the current quota
    tokens: int          # compute/effort spent — what the governor caps
    last_order: str      # most recent order given at the idle gate
    log: Annotated[list, _append]
    verdict: str


# ── shared World (the oracle) ────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _world_init(c: sqlite3.Connection) -> None:
    cols = ", ".join(f"{name} INT DEFAULT 0" for name in _COLUMNS)
    c.execute(f"CREATE TABLE IF NOT EXISTS world(id INTEGER PRIMARY KEY CHECK(id=1), "
              f"{cols}, age TEXT DEFAULT 'Dark Age')")
    c.execute("INSERT OR IGNORE INTO world(id) VALUES(1)")
    # Governor-proposed developments, adopted by the human. Machine-usable effects only.
    c.execute("CREATE TABLE IF NOT EXISTS custom_devs("
              "name TEXT PRIMARY KEY, food INT DEFAULT 0, wood INT DEFAULT 0, gold INT DEFAULT 0, "
              "kind TEXT, value INT, resource TEXT DEFAULT '', rank INT DEFAULT 2, "
              "source TEXT DEFAULT '', built INT DEFAULT 0)")
    # Assets need upkeep: every built development has a condition (100 → 0) that decays
    # and scales its effect. Lives with the world — a new world starts fresh.
    c.execute("CREATE TABLE IF NOT EXISTS conditions("
              "name TEXT PRIMARY KEY, condition INT DEFAULT 100)")
    # Every built thing has a PLACE: one tile per built instance, assigned at build
    # time, spiralling out from the town centre. The map is a projection of world
    # state — it never gates or blocks the economy.
    c.execute("CREATE TABLE IF NOT EXISTS placements("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, x INT, y INT)")
    # The land itself: one class per tile (forest, berries, gold seam, water), with a
    # STOCK that gathering wears down. Generated once, deterministically — the same
    # world founds the same land every time.
    c.execute("CREATE TABLE IF NOT EXISTS terrain("
              "x INT, y INT, cls TEXT, stock INT, PRIMARY KEY(x, y))")
    _terrain_init(c)
    c.commit()


def world() -> dict:
    c = _conn()
    try:
        row = c.execute(f"SELECT {', '.join(_COLUMNS)}, age FROM world WHERE id=1").fetchone()
        w = dict(zip(_COLUMNS + ("age",), row))
        pop_bonus = c.execute("SELECT COALESCE(SUM(value*built),0) FROM custom_devs "
                              "WHERE kind='pop_cap'").fetchone()[0]
        w["pop_cap"] = 3 + 2 * w["house"] + pop_bonus
        return w
    finally:
        c.close()


def structures() -> dict:
    w = world()
    return {k: w[k] for k in ("house", "mill", "lumber_camp", "mining_camp", "wheelbarrow")}


def effective_yield(resource: str, w: dict | None = None) -> int:
    """Yield grows as the settlement develops — camps, the wheelbarrow tech, and any
    adopted custom developments. Every bonus scales with the asset's CONDITION: a mill
    at 60% gives 60% of its bonus. Maintenance is real."""
    w = w or world()
    cond = conditions()
    camp = w[CAMP_FOR[resource]] * cond.get(CAMP_FOR[resource], 100) / 100
    tech = w["wheelbarrow"] * cond.get("wheelbarrow", 100) / 100
    y = BASE[resource] * (1 + 0.5 * camp) * (1 + 0.25 * tech)
    for d in custom_devs():
        if not d["built"]:
            continue
        eff = d["built"] * cond.get(d["name"], 100) / 100
        if d["kind"] == "yield_pct" and d["resource"] == resource:
            y *= 1 + (d["value"] / 100) * eff
        elif d["kind"] == "all_yield_pct":
            y *= 1 + (d["value"] / 100) * eff
    # Location pays: each camp placed on the ring around its resource ground works
    # the ground directly. The ring is finite, so the bonus is a portfolio decision.
    near = proximity_camps(resource)
    if near:
        y *= 1 + (PROXIMITY_PCT / 100) * near
    # The land pays too — and wears out: every live terrain tile the camps work
    # adds its cut, and gathering depletes those same tiles (terrain_deplete).
    tb = terrain_bonus_tiles(resource)
    if tb:
        y *= 1 + (TERRAIN_PCT / 100) * tb
    return int(y)


# ── decay, repair & spoilage — assets cost upkeep, food rots ─────────────────

DECAY_EVERY = 5          # decay tick cadence (turns)
REPAIR_FRACTION = 0.25   # repair costs this share of the build price
FOOD_SPOIL_PCT = 2       # % of overflow that rots per turn


def conditions() -> dict:
    c = _conn()
    try:
        return dict(c.execute("SELECT name, condition FROM conditions").fetchall())
    finally:
        c.close()


def decay_tick(turn: int) -> list[tuple[str, int]]:
    """Every DECAY_EVERY turns, each BUILT development loses `rank` condition points —
    grander works cost more to keep. Returns [(name, new_condition), ...]."""
    if turn % DECAY_EVERY:
        return []
    out = []
    c = _conn()
    try:
        for d in dev_catalog():
            if not d["built"]:
                continue
            c.execute("INSERT OR IGNORE INTO conditions(name, condition) VALUES(?, 100)",
                      (d["name"],))
            c.execute("UPDATE conditions SET condition=MAX(0, condition-?) WHERE name=?",
                      (d["rank"], d["name"]))
            out.append((d["name"],
                        c.execute("SELECT condition FROM conditions WHERE name=?",
                                  (d["name"],)).fetchone()[0]))
        c.commit()
        return out
    finally:
        c.close()


def repair_cost(name: str) -> dict:
    d = next((x for x in dev_catalog() if x["name"] == name), None)
    if not d:
        return {}
    return {r: max(1, int(v * REPAIR_FRACTION)) for r, v in d["cost"].items()}


def repair(name: str) -> tuple[bool, str]:
    """Spend a fraction of the build price to restore an asset to full condition."""
    cost = repair_cost(name)
    if not cost:
        return False, f"unknown development {name}"
    c = _conn()
    try:
        have = dict(zip(("food", "wood", "gold"),
                        c.execute("SELECT food, wood, gold FROM world WHERE id=1").fetchone()))
        for r, amt in cost.items():
            if have.get(r, 0) < amt:
                return False, f"cannot afford repair of {name}: need {r} {amt}, have {have.get(r, 0)}"
        sets = ", ".join(f"{r}={r}-{amt}" for r, amt in cost.items())
        c.execute(f"UPDATE world SET {sets} WHERE id=1")
        c.execute("INSERT INTO conditions(name, condition) VALUES(?, 100) "
                  "ON CONFLICT(name) DO UPDATE SET condition=100", (name,))
        c.commit()
        return True, f"repaired {name} to 100% (" + ", ".join(f"{v} {r}" for r, v in cost.items()) + ")"
    finally:
        c.close()


def food_cap(w: dict | None = None) -> int:
    """Storage is finite: the settlement can bank ~1.2x the NEXT era's food price.
    Anything above it rots — spoilage forces flow, not hoards."""
    w = w or world()
    return int(1.2 * advance_cost(w["age"])["food"])


TRADE_RATE = 5           # market rate: food per 1 gold


def trade_surplus() -> tuple[int, int]:
    """Article IV.5 — a gate may pause the Age, never the settlement. The sink runs
    LAST in the preference order (build → repair → sell — selling at 5:1 is the worst
    deal except rotting at ∞:1) and is unbounded: the WHOLE remaining overflow sells,
    so spoilage only ever eats the sub-trade remainder. Returns (food_sold, gold_got)."""
    w = world()
    cap = food_cap(w)
    over = w["food"] - cap
    if over < TRADE_RATE:
        return 0, 0
    amount = over // TRADE_RATE * TRADE_RATE
    gold = amount // TRADE_RATE
    _world_add("food", -amount)
    _world_add("gold", gold)
    return amount, gold


def spoil_tick() -> tuple[int, int]:
    """Rot food above the storage cap. Returns (loss, cap)."""
    w = world()
    cap = food_cap(w)
    over = w["food"] - cap
    if over <= 0:
        return 0, cap
    loss = max(1, over * FOOD_SPOIL_PCT // 100)
    _world_add("food", -loss)
    return loss, cap


def custom_devs() -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("SELECT name, food, wood, gold, kind, value, resource, rank, source, "
                         "built FROM custom_devs ORDER BY rank, name").fetchall()
        return [{"name": n, "cost": {r: v for r, v in (("food", f), ("wood", wd), ("gold", g)) if v},
                 "kind": k, "value": val, "resource": res, "rank": rk, "source": src, "built": b}
                for n, f, wd, g, k, val, res, rk, src, b in rows]
    finally:
        c.close()


def dev_add(name: str, cost: dict, kind: str, value: int, resource: str = "",
            rank: int = 2, source: str = "") -> tuple[bool, str]:
    """Adopt a governor-proposed development into the buildable catalog."""
    name = name.strip().lower().replace(" ", "_")[:32]
    if not name or kind not in CUSTOM_KINDS:
        return False, f"invalid development (kind must be one of {CUSTOM_KINDS})"
    if kind == "yield_pct" and resource not in RESOURCES:
        return False, "yield_pct needs a valid resource"
    if name in STRUCTURES:
        return False, f"{name} already exists as a base structure"
    value = max(1, min(100, int(value)))
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO custom_devs(name, food, wood, gold, kind, value, "
                  "resource, rank, source) VALUES(?,?,?,?,?,?,?,?,?)",
                  (name, int(cost.get("food", 0)), int(cost.get("wood", 0)),
                   int(cost.get("gold", 0)), kind, value, resource, int(rank), source))
        c.commit()
        return True, f"adopted development {name}"
    finally:
        c.close()


def _custom_effect_text(d: dict) -> str:
    if d["kind"] == "yield_pct":
        return f"+{d['value']}% {d['resource']} yield"
    if d["kind"] == "all_yield_pct":
        return f"+{d['value']}% all yields"
    return f"+{d['value']} population cap"


def dev_catalog() -> list[dict]:
    """The full ranked development tree — base structures + adopted customs, with counts."""
    w = world()
    out = [{"name": k, "cost": v["cost"], "effect": v["effect"], "rank": v["rank"],
            "built": w[k], "custom": False} for k, v in STRUCTURES.items()]
    out += [{"name": d["name"], "cost": d["cost"], "effect": _custom_effect_text(d),
             "rank": d["rank"], "built": d["built"], "custom": True, "source": d["source"]}
            for d in custom_devs()]
    return sorted(out, key=lambda x: (x["rank"], x["name"]))


def build_development(name: str) -> tuple[bool, str]:
    """Build anything in the catalog — base structure or adopted custom development."""
    if name in STRUCTURES:
        return build_structure(name)
    d = next((x for x in custom_devs() if x["name"] == name), None)
    if d is None:
        return False, f"unknown development {name}"
    c = _conn()
    try:
        have = dict(zip(("food", "wood", "gold"),
                        c.execute("SELECT food, wood, gold FROM world WHERE id=1").fetchone()))
        for r, amt in d["cost"].items():
            if have[r] < amt:
                return False, f"cannot afford {name}: need {r} {amt}, have {have[r]}"
        if d["cost"]:
            sets = ", ".join(f"{r}={r}-{amt}" for r, amt in d["cost"].items())
            c.execute(f"UPDATE world SET {sets} WHERE id=1")
        c.execute("UPDATE custom_devs SET built=built+1 WHERE name=?", (name,))
        c.execute("INSERT INTO conditions(name, condition) VALUES(?, 100) "
                  "ON CONFLICT(name) DO UPDATE SET condition=100", (name,))
        _place(c, name)
        c.commit()
        return True, f"built {name} ({_custom_effect_text(d)})"
    finally:
        c.close()


def _world_add(resource: str, amount: int) -> None:
    c = _conn()
    try:
        c.execute(f"UPDATE world SET {resource}={resource}+? WHERE id=1", (amount,))
        c.commit()
    finally:
        c.close()


def build_structure(kind: str) -> tuple[bool, str]:
    """Develop a building/tech. Reversible, so it runs free (no gate). Returns (ok, msg)."""
    if kind not in STRUCTURES:
        return False, f"unknown structure {kind}"
    cost = STRUCTURES[kind]["cost"]
    c = _conn()
    try:
        have = dict(zip(_COLUMNS, c.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM world WHERE id=1").fetchone()))
        if kind == "wheelbarrow" and have["wheelbarrow"]:
            return False, "wheelbarrow already researched"
        for r, amt in cost.items():
            if have[r] < amt:
                return False, f"cannot afford {kind}: need {r} {amt}, have {have[r]}"
        sets = ", ".join(f"{r}={r}-{amt}" for r, amt in cost.items())
        bump = "wheelbarrow=1" if kind == "wheelbarrow" else f"{kind}={kind}+1"
        c.execute(f"UPDATE world SET {sets}, {bump} WHERE id=1")
        c.execute("INSERT INTO conditions(name, condition) VALUES(?, 100) "
                  "ON CONFLICT(name) DO UPDATE SET condition=100", (kind,))
        _place(c, kind)
        c.commit()
        return True, f"built {kind} ({STRUCTURES[kind]['effect']})"
    finally:
        c.close()


# ── the map — every built thing has a place, and the place matters ───────────

MAP_W, MAP_H = 24, 16
TOWN_CENTER = (MAP_W // 2, MAP_H // 2)
# Where each resource lies on the land. Camps placed within PROXIMITY_RADIUS tiles
# of their ground work it directly: each one adds PROXIMITY_PCT% to that resource's
# yield. Location is real economics — the ring around a ground is finite, so late
# camps land outside it and earn no bonus.
GROUNDS = {"food": (3, 4), "wood": (20, 3), "gold": (20, 12)}
PROXIMITY_RADIUS = 3
PROXIMITY_PCT = 15

# ── terrain — the land feeds the economy, and wears out ──────────────────────
# Around each ground the land carries its resource: forest, berry bushes, a gold
# seam. A camp works every LIVE tile of its class within TERRAIN_RADIUS: each adds
# TERRAIN_PCT% to that yield. Gathering depletes the worked tiles' stock — worked-out
# land pays nothing until (a future) regrowth, so the land is a wasting asset.
TERRAIN_KIND = {"food": "berries", "wood": "forest", "gold": "gold_seam"}
TERRAIN_SPREAD = 3       # how far a ground's class scatters around it
TERRAIN_RADIUS = 2       # how far a camp reaches to work live tiles
TERRAIN_PCT = 3          # yield % per live tile a camp works
TERRAIN_STOCK = 100      # a fresh tile's stock; gathering wears it down
WATER = {(7, 12), (8, 12), (7, 13), (8, 13)}    # the pond — never built on

# Paint registry — how each development kind renders on the canvas, served to the
# client in map_state() so new kinds never need client changes. layer orders the
# paint (terrain 0 → grounds 1 → buildings 2); shape names a client-side sprite.
RENDER = {
    "house":       {"shape": "house", "color": "#8b5a2b", "layer": 2},
    "mill":        {"shape": "mill",  "color": "#c9b98f", "layer": 2},
    "lumber_camp": {"shape": "camp",  "color": "#5a8a3a", "accent": "#b5793a", "layer": 2},
    "mining_camp": {"shape": "camp",  "color": "#3a2c18", "accent": "#e0b23a", "layer": 2},
    "wheelbarrow": {"shape": "tech",  "color": "#c9b98f", "layer": 2},
}
RES_COLOR = {"food": "#e05a5a", "wood": "#b5793a", "gold": "#e0b23a"}


def render_registry() -> dict:
    """RENDER plus an entry for every adopted custom development (diamond, coloured
    by the resource it boosts) — the client draws only what this registry names.
    Each entry carries the development's rank (grander works draw bigger) and its
    effect text (the hover tooltip), so the client never needs its own catalog."""
    cat = {d["name"]: d for d in dev_catalog()}
    reg = {}
    for name, spec in RENDER.items():
        e = dict(spec)
        if name in cat:
            e["rank"], e["effect"] = cat[name]["rank"], cat[name]["effect"]
        reg[name] = e
    for d in custom_devs():
        if d["name"] in reg:
            continue
        reg[d["name"]] = {
            "shape": "diamond", "layer": 2,
            "rank": d["rank"], "effect": _custom_effect_text(d),
            "color": RES_COLOR.get(d["resource"], "#8ab4ff")
            if d["kind"] == "yield_pct" else "#8ab4ff"}
    return reg


def _thash(x: int, y: int) -> float:
    """Deterministic per-tile hash in [0,1) — the land's one and only seed."""
    h = (x * 374761393 + y * 668265263) ^ (x * y * 2246822519 + 1)
    h = (h ^ (h >> 13)) * 1274126177
    return ((h ^ (h >> 16)) & 0xFFFFFFFF) / 4294967296


def _terrain_init(c: sqlite3.Connection) -> None:
    """Found the land once: water, then each resource's class scattered around its
    ground by the tile hash. Pure function of the constants — every fresh world (and
    every re-init after a wipe) lays the exact same land."""
    if c.execute("SELECT COUNT(*) FROM terrain").fetchone()[0]:
        return
    tiles = {(x, y): ("water", 0) for x, y in WATER}
    for res, (gx, gy) in GROUNDS.items():
        for dx in range(-TERRAIN_SPREAD, TERRAIN_SPREAD + 1):
            for dy in range(-TERRAIN_SPREAD, TERRAIN_SPREAD + 1):
                x, y = gx + dx, gy + dy
                if not (0 <= x < MAP_W and 0 <= y < MAP_H):
                    continue
                if (x, y) in tiles or (x, y) == TOWN_CENTER:
                    continue
                if _thash(x, y) > 0.45:
                    tiles[(x, y)] = (TERRAIN_KIND[res], TERRAIN_STOCK)
    c.executemany("INSERT INTO terrain(x, y, cls, stock) VALUES(?,?,?,?)",
                  [(x, y, cls, st) for (x, y), (cls, st) in tiles.items()])


def terrain() -> list[dict]:
    c = _conn()
    try:
        return [{"x": x, "y": y, "cls": cls, "stock": st} for x, y, cls, st in
                c.execute("SELECT x, y, cls, stock FROM terrain ORDER BY x, y")]
    finally:
        c.close()


def _worked_tiles(resource: str) -> list[tuple[int, int, int]]:
    """The live tiles of this resource's class within TERRAIN_RADIUS of any of its
    camps — the land the settlement is actually working. [(x, y, stock), ...]"""
    c = _conn()
    try:
        camps = c.execute("SELECT x, y FROM placements WHERE name=?",
                          (CAMP_FOR[resource],)).fetchall()
        if not camps:
            return []
        rows = c.execute("SELECT x, y, stock FROM terrain WHERE cls=? AND stock>0",
                         (TERRAIN_KIND[resource],)).fetchall()
    finally:
        c.close()
    return [(x, y, st) for x, y, st in rows
            if any(max(abs(x - cx), abs(y - cy)) <= TERRAIN_RADIUS for cx, cy in camps)]


def terrain_bonus_tiles(resource: str) -> int:
    return len(_worked_tiles(resource))


def terrain_deplete(resource: str, amount: int = 1) -> None:
    """Gathering wears the land: take `amount` stock off the richest tile the camps
    are working. No camp, no worked land — nothing depletes."""
    worked = _worked_tiles(resource)
    if not worked:
        return
    x, y, _ = max(worked, key=lambda t: t[2])
    c = _conn()
    try:
        c.execute("UPDATE terrain SET stock=MAX(0, stock-?) WHERE x=? AND y=?",
                  (amount, x, y))
        c.commit()
    finally:
        c.close()


def _ground_for(name: str) -> tuple[int, int] | None:
    """The ground a development wants to sit near: camps and custom yield
    developments seek their resource; everything else grows from the town."""
    for res, camp in CAMP_FOR.items():
        if name == camp:
            return GROUNDS[res]
    d = next((x for x in custom_devs() if x["name"] == name), None)
    if d and d["kind"] == "yield_pct":
        return GROUNDS.get(d["resource"])
    return None


def _spiral(limit: int = 4 * MAP_W * MAP_H):
    """Square-spiral offsets out from (0,0): the town grows ring by ring."""
    x, y, dx, dy = 0, 0, 0, -1
    for _ in range(limit):
        yield x, y
        if x == y or (x < 0 and x == -y) or (x > 0 and x == 1 - y):
            dx, dy = -dy, dx
        x, y = x + dx, y + dy


def _next_tile(c: sqlite3.Connection, near: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """First free tile spiralling out from `near` (default: the town centre). The
    town centre and the resource grounds are never built on. None when the map is
    full — the build still succeeds (a missing tile never blocks the economy)."""
    taken = ({TOWN_CENTER} | set(GROUNDS.values()) | WATER
             | set(c.execute("SELECT x, y FROM placements").fetchall()))
    cx, cy = near or TOWN_CENTER
    for ox, oy in _spiral():
        x, y = cx + ox, cy + oy
        if 0 <= x < MAP_W and 0 <= y < MAP_H and (x, y) not in taken:
            return x, y
    return None


def _place(c: sqlite3.Connection, name: str) -> tuple[int, int] | None:
    t = _next_tile(c, _ground_for(name))
    if t:
        c.execute("INSERT INTO placements(name, x, y) VALUES(?,?,?)", (name, t[0], t[1]))
    return t


def proximity_camps(resource: str) -> int:
    """How many of this resource's camps sit within PROXIMITY_RADIUS (Chebyshev)
    of its ground — each one earns the settlement +PROXIMITY_PCT% on that yield."""
    gx, gy = GROUNDS[resource]
    c = _conn()
    try:
        rows = c.execute("SELECT x, y FROM placements WHERE name=?",
                         (CAMP_FOR[resource],)).fetchall()
    finally:
        c.close()
    return sum(1 for x, y in rows if max(abs(x - gx), abs(y - gy)) <= PROXIMITY_RADIUS)


def _sync_placements(c: sqlite3.Connection) -> None:
    """Reconcile placements with what is actually built. Backfills tiles for worlds
    that predate the map, and drops the newest tiles for anything no longer built
    (a demolition, a world reset) — counts in the world table stay the oracle."""
    counts = {d["name"]: d["built"] for d in dev_catalog() if d["built"]}
    have = dict(c.execute("SELECT name, COUNT(*) FROM placements GROUP BY name").fetchall())
    for name, n in have.items():
        extra = n - counts.get(name, 0)
        if extra > 0:
            c.execute("DELETE FROM placements WHERE id IN ("
                      "SELECT id FROM placements WHERE name=? ORDER BY id DESC LIMIT ?)",
                      (name, extra))
    for name, n in counts.items():
        for _ in range(n - have.get(name, 0)):
            _place(c, name)
    c.commit()


def map_state() -> dict:
    """The settlement as a place: grid size, the town centre, the resource grounds,
    and one tile per built development with its live condition. A camp inside its
    ground's ring is marked `near` — that is the tile earning the proximity bonus."""
    camp_res = {camp: res for res, camp in CAMP_FOR.items()}
    c = _conn()
    try:
        _sync_placements(c)
        cond = conditions()
        rows = c.execute("SELECT id, name, x, y FROM placements ORDER BY id").fetchall()
        out = []
        for pid, n, x, y in rows:
            res = camp_res.get(n)
            gx, gy = GROUNDS.get(res, (None, None)) if res else (None, None)
            out.append({"id": pid, "name": n, "x": x, "y": y, "condition": cond.get(n, 100),
                        "near": bool(res) and max(abs(x - gx), abs(y - gy)) <= PROXIMITY_RADIUS})
        return {"w": MAP_W, "h": MAP_H, "town": list(TOWN_CENTER),
                "grounds": {r: list(t) for r, t in GROUNDS.items()},
                "proximity": {"radius": PROXIMITY_RADIUS, "pct": PROXIMITY_PCT,
                              "camps": {r: proximity_camps(r) for r in RESOURCES}},
                "terrain": terrain(),
                "terrain_bonus": {"pct": TERRAIN_PCT, "stock": TERRAIN_STOCK,
                                  "tiles": {r: terrain_bonus_tiles(r) for r in RESOURCES}},
                "registry": render_registry(),
                "placements": out}
    finally:
        c.close()


def _world_advance() -> tuple[bool, str]:
    """Spend the age-up cost and bump the Age. Irreversible. Returns (ok, message)."""
    c = _conn()
    try:
        food, gold, age = c.execute("SELECT food, gold, age FROM world WHERE id=1").fetchone()
        nxt = NEXT_AGE.get(age)
        if nxt is None:
            return False, f"already at {age}"
        cost = advance_cost(age)
        if food < cost["food"] or gold < cost["gold"]:
            return False, (f"insufficient resources: have food {food}/gold {gold}, "
                           f"need food {cost['food']}/gold {cost['gold']}")
        c.execute("UPDATE world SET food=food-?, gold=gold-?, age=? WHERE id=1",
                  (cost["food"], cost["gold"], nxt))
        c.commit()
        return True, f"advanced to {nxt}"
    finally:
        c.close()


# ── graph nodes ──────────────────────────────────────────────────────────────

def by_role(state: Unit) -> str:
    return state["role"]


def plan(state: Unit) -> dict:
    return {"tokens": state["tokens"] + 800,
            "log": [f"villager {state['uid']} → gather {state['resource']}"]}


def gather(state: Unit) -> dict:
    time.sleep(0.02)
    got = effective_yield(state["resource"])
    _world_add(state["resource"], got)
    terrain_deplete(state["resource"])       # working the land wears the land
    return {"steps": state["steps"] + 1, "tokens": state["tokens"] + 3000,
            "log": [f"gathered {got} {state['resource']} (round {state['steps'] + 1})"]}


def more(state: Unit) -> str:
    return "gather" if state["steps"] < QUOTA else "orders"


def orders(state: Unit) -> dict:
    """Villager finished its quota — parks awaiting orders (the idle alert)."""
    decision = interrupt({
        "uid": state["uid"],
        "action": f"Villager idle — gathered {state['steps'] * effective_yield(state['resource'])} "
                  f"{state['resource']}, awaiting orders",
        "reversible": True,
        "tokens_spent": state["tokens"],
    })
    return {"last_order": decision, "log": [f"order: {decision}"]}


def route_orders(state: Unit) -> str:
    """Re-task back to work on a 'gather[:resource]' order; otherwise stand down."""
    return "retask" if str(state.get("last_order", "")).startswith("gather") else "stand_down"


def retask(state: Unit) -> dict:
    order = state["last_order"]
    res = order.split(":", 1)[1] if ":" in order else state["resource"]
    res = res if res in RESOURCES else state["resource"]
    return {"resource": res, "steps": 0, "log": [f"re-tasked → gather {res}"]}


def stand_down(state: Unit) -> dict:
    return {"verdict": "dismissed", "log": ["dismissed"]}


def assess(state: Unit) -> dict:
    w = world()
    return {"tokens": state["tokens"] + 500,
            "log": [f"herald: treasury food {w['food']} / gold {w['gold']}, age {w['age']}"]}


def advance(state: Unit) -> dict:
    """The irreversible action: spend resources to advance the Age. Gated."""
    cost = advance_cost()
    decision = interrupt({
        "uid": state["uid"],
        "action": f"ADVANCE to {NEXT_AGE.get(world()['age'], 'next Age')} — "
                  f"spend food {cost['food']:,} + gold {cost['gold']:,} (irreversible)",
        "reversible": False,
        "tokens_spent": state["tokens"],
    })
    if decision == "approve":
        ok, msg = _world_advance()
        return {"verdict": "advanced" if ok else "blocked", "log": [f"approved → {msg}"]}
    return {"verdict": "held", "log": [f"rejected ({decision}) → held"]}


def build(checkpointer):
    g = StateGraph(Unit)
    for name, fn in (("plan", plan), ("gather", gather), ("orders", orders),
                     ("retask", retask), ("stand_down", stand_down),
                     ("assess", assess), ("advance", advance)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, by_role, {"villager": "plan", "herald": "assess"})
    g.add_edge("plan", "gather")
    g.add_conditional_edges("gather", more, {"gather": "gather", "orders": "orders"})
    g.add_conditional_edges("orders", route_orders, {"retask": "retask", "stand_down": "stand_down"})
    g.add_edge("retask", "gather")          # re-tasked villagers loop back to work
    g.add_edge("stand_down", END)
    g.add_edge("assess", "advance")
    g.add_edge("advance", END)
    return g.compile(checkpointer=checkpointer)


def connect() -> SqliteSaver:
    conn = sqlite3.connect(DB, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _world_init(conn)
    return SqliteSaver(conn)


def spawn(graph, uid: str, role: str, resource: str = "", task: str = "") -> dict:
    cfg = {"configurable": {"thread_id": uid}}
    return graph.invoke(
        {"uid": uid, "role": role, "resource": resource,
         "task": task or (f"gather {resource}" if role == "villager" else "advance the age"),
         "steps": 0, "tokens": 0, "last_order": "", "log": [], "verdict": ""},
        cfg,
    )


def resume(graph, uid: str, decision: str) -> dict:
    cfg = {"configurable": {"thread_id": uid}}
    return graph.invoke(Command(resume=decision), cfg)


def render_world() -> str:
    w = world()
    built = ", ".join(f"{k}×{w[k]}" for k in ("house", "mill", "lumber_camp", "mining_camp")
                      if w[k]) or "none"
    tech = " +wheelbarrow" if w["wheelbarrow"] else ""
    return (f"{w['age']:<12} food {w['food']:>5}  wood {w['wood']:>5}  gold {w['gold']:>5}  "
            f"| pop_cap {w['pop_cap']} | built: {built}{tech}")

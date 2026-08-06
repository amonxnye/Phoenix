"""Live RTS operator console for the Age of Empires MVP.

The director auto-plays in the background — villagers gather, get re-tasked, the
settlement develops, the anchor learns — and the whole organization drives toward the
Vision. The one thing it will NOT do on its own is the irreversible Age-up: that spawns
a herald which parks at the gate and waits for YOU to approve in the command queue.
Autonomy, inside the gate.

Panels: Vision progress · World · Fleet · Command queue · Development tree · Event log ·
Knowledge. All live from the checkpointer + World + anchor. Standard library only.

Run:  python3 gov/sim_console.py --seed   # then open http://127.0.0.1:8788
"""

import json
import os
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anchor
import board
import brain
import director as D
import economy
import governor as G
import models as MODELS
import sim
import vision as V

# Performance tuning (from live behaviour review):
# - cap 1M: a mature fleet of foremen/delegates carries real lifetime budgets; the
#   old 500k jammed spawning permanently once agents matured.
# - TICK 3s: the 2s pace saturated the process with model calls.
# - idle threshold 2×TICK: "idle" now means genuinely stale, not "since last tick".
G.TOKEN_CAP = 1_000_000
TICK = float(os.environ.get("WORLD_TICK", "3"))
# Article IV.7 — tacit consent: a decision queued for the human this long goes back
# to the Board for a final vote; quorum yes proceeds AS IF approved, loudly logged.
TACIT_CONSENT_S = int(os.environ.get("TACIT_CONSENT_S", "3600"))
G.IDLE_AFTER_S = 2 * TICK
TARGET_VILLAGERS = 4

_LOCK = threading.Lock()
_FRESH_WORLD = not os.path.exists(sim.DB)
_CP = sim.connect()
_GRAPH = sim.build(_CP)
anchor.init()
economy.init()
_S = {"turn": 0, "villagers": [], "heralds": 0, "side_effects": 0, "seq": 0,
      "goal_met": False, "last_vote": None, "vision_key": V.DEFAULT_VISION,
      "orders": {}, "notified": set(), "dev_proposal": None,
      "born": {}, "last_turn_ts": time.time()}
START_TS = time.time()
if _FRESH_WORLD:
    # A new world, same memory: the generation counter rises so agent ids stay unique
    # in the permanent career records across world resets.
    gen = anchor.new_generation()
    anchor.config_set("turn", "0")
    anchor.config_set("seq", "0")
    anchor.config_set("heralds", "0")
    anchor.config_set("vision_key", V.DEFAULT_VISION)
    anchor.record(0, "generation", f"world generation {gen} begins — memory carried forward")
else:
    # A RESTART (deploy, crash) is not a new world: restore the counters and re-adopt
    # the living fleet, or agent ids collide, the turn clock jumps backwards in the
    # permanent log, and working agents are abandoned as unreapable zombies.
    _S["turn"] = anchor.counter_get("turn")
    _S["seq"] = anchor.counter_get("seq")
    _S["heralds"] = anchor.counter_get("heralds")
    _vk = anchor.config_get("vision_key", "")
    if _vk in V.VISIONS:
        _S["vision_key"] = _vk                    # the human's adopted vision persists
    if _S["seq"] == 0:      # first boot on persisted counters: seed from history
        _nums = [int(r["agent"].split("-")[1]) for r in economy.roster(alive_only=False)
                 if r["agent"].startswith("vil-") and r["agent"].split("-")[1].isdigit()]
        _S["seq"] = max(_nums, default=0)
        anchor.config_set("seq", str(_S["seq"]))
    # (the herald counter is persisted; no full-scan recovery — a scan of all history
    # at boot is exactly the stall we're avoiding)
    anchor.CURRENT_TURN = _S["turn"]
    # Re-adopt only agents whose threads can still WORK. A `done` thread can never be
    # resumed — re-adopting it seats a zombie that blocks staffing and never gathers
    # (the run-3 collapse). Read ONLY the roster's candidates — a full checkpointer
    # scan at boot walks every thread ever and stalls startup as history grows.
    _cand = [r["agent"] for r in economy.roster() if r["agent"].startswith("vil-")]
    _cand += [f"herald-{i:02d}" for i in range(max(1, _S["heralds"] - 2), _S["heralds"] + 1)]
    _threads = {u.unit_id: u for u in G.units(_GRAPH, _CP, only=_cand)}
    for _u in _threads.values():                  # an inherited gate gets its IV.7 clock
        if _u.pending and _u.pending.get("reversible") is False:
            _S["gate_since"] = _S["turn"]
            _S["gate_since_ts"] = time.time()
    _S["villagers"] = []
    for _r in economy.roster():
        if not _r["agent"].startswith("vil-"):
            continue
        _u = _threads.get(_r["agent"])
        if _u and _u.status != "done":
            _S["villagers"].append(_r["agent"])
        else:
            economy.retire(_r["agent"])
            anchor.record(_S["turn"], "reap",
                          f"{_r['agent']} struck off at boot — thread "
                          f"{'complete' if _u else 'missing'}, unresumable (zombie guard)")
            anchor.career_add(_r["agent"], _S["turn"], "retired",
                              "struck off at boot — thread could never work again")
            if _u:
                anchor.counter_add("lifetime_spend", _u.tokens)
            _forget_thread(_r["agent"])
    if anchor.counter_get("token_cap"):           # board rewards / operator cap edits persist
        G.TOKEN_CAP = anchor.counter_get("token_cap")
    for _c in anchor.careers(200):                # rebuild birth turns for burn math
        if _c["gen"] == anchor.generation() and _c["uid"] in _S["villagers"]:
            for _e in _c["events"]:
                if _e["event"] == "born":
                    _S["born"][_c["uid"]] = _e["turn"]


def _choose_gather(w: dict) -> tuple[str, str]:
    """Pick a resource AND say why — the reasoning is first-class, not implicit."""
    cost = sim.advance_cost(w["age"])
    need_food = max(0, cost["food"] - w["food"])
    need_gold = max(0, cost["gold"] - w["gold"])
    if w["wood"] < 150:
        return "wood", f"wood at {w['wood']}, below the 150 floor needed to keep building"
    if need_food or need_gold:
        r = "food" if need_food >= need_gold else "gold"
        return r, f"Age-up shortfall drives it: food short {need_food}, gold short {need_gold}"
    # Rebalance guard: "bank the best yield" is a feedback loop (the resource gathered
    # most gets the camps and the observations, so it stays "best" forever and wood/gold
    # starve). When the economy is lopsided, gather the poorest stock instead.
    lo = min(sim.RESOURCES, key=lambda k: w[k])
    hi = max(sim.RESOURCES, key=lambda k: w[k])
    if w[hi] > 4 * max(1, w[lo]):
        return lo, (f"economy is lopsided — {hi} {w[hi]:,} vs {lo} {w[lo]:,}; "
                    f"rebalance into {lo} instead of banking more surplus")
    br = anchor.best_known_yield()
    if br:
        return br, f"no shortages — {br} has the best learned yield, so bank the surplus there"
    r = min(sim.RESOURCES, key=lambda k: w[k])
    return r, f"stockpile is lowest in {r}"


def _choose_build(w: dict, n_agents: int) -> tuple[str | None, str | None]:
    """Pick a development AND say why."""
    if n_agents >= w["pop_cap"] and w["wood"] >= sim.STRUCTURES["house"]["cost"]["wood"]:
        return "house", f"population {n_agents}/{w['pop_cap']} is capped — housing unlocks growth"
    for kind in ("lumber_camp", "mill", "mining_camp"):
        if w[kind] == 0 and w["wood"] >= sim.STRUCTURES[kind]["cost"]["wood"]:
            return kind, f"first {kind} raises its resource yield by 50% — compounding return"
    if not w["wheelbarrow"] and w["food"] >= 100 and w["wood"] >= 100:
        return "wheelbarrow", "affordable tech that lifts ALL yields by 25%"
    return None, None

# Template proposals used when no model is configured — the Governor still proposes.
_DEV_TEMPLATES = [
    {"name": "granary", "cost": {"wood": 150}, "kind": "yield_pct", "value": 15,
     "resource": "food", "rank": 2, "why": "store food better, waste less"},
    {"name": "sawmill", "cost": {"wood": 120, "gold": 40}, "kind": "yield_pct", "value": 15,
     "resource": "wood", "rank": 2, "why": "sharper saws, faster lumber"},
    {"name": "gold_smelter", "cost": {"wood": 160, "food": 60}, "kind": "yield_pct", "value": 15,
     "resource": "gold", "rank": 3, "why": "refine ore on site"},
    {"name": "town_watch", "cost": {"food": 80, "wood": 80}, "kind": "pop_cap", "value": 2,
     "rank": 2, "why": "order lets more villagers settle"},
    {"name": "guild_charter", "cost": {"gold": 120}, "kind": "all_yield_pct", "value": 10,
     "rank": 3, "why": "organised trades lift every yield"},
]


def _propose_development():
    """The Governor proposes a new development — model + ingested knowledge when alive,
    a template otherwise. The Board pre-votes; only the human adopts."""
    existing = [d["name"] for d in sim.dev_catalog()]
    prop = brain.propose_development(_situation(), anchor.external(8), existing)
    if not prop:
        prop = next((dict(t) for t in _DEV_TEMPLATES if t["name"] not in existing), None)
        if prop:
            prop["source"] = "template"
    else:
        prop["source"] = "deepseek+knowledge"
    if not prop or prop.get("name") in existing:
        return
    bv = board.vote(f"development: {prop['name']}", _board_ctx(True))
    prop["board"] = bv["tally"]
    prop["board_approved"] = bv["approved"]
    prop["born_ts"] = time.time()                 # IV.7: tacit consent clocks from here
    _S["dev_proposal"] = prop
    _narrate_vote(bv, prop.get("why", ""))
    # lineage: the proposal derives from ingested knowledge and the current lessons
    refs = [f"external:{k['id']}" for k in anchor.external(3) if "id" in k]
    refs += [f"skill:{s['id']}" for s in anchor.skills_top(2) if s.get("id")]
    prop["did"] = anchor.reason_add(
        _S["turn"], "governor", f"propose development '{prop['name']}'",
        f"{prop.get('why', '')} (source {prop.get('source', '')}; board {bv['tally']})",
        derived_from=refs, authorized_by=f"board:{bv['tally']}")
    anchor.record(_S["turn"], "proposal",
                  f"governor proposes development '{prop['name']}' ({prop.get('why','')[:60]}) "
                  f"— board {bv['tally']}, awaiting the human")


def _forget_thread(uid: str) -> None:
    """Drop a DEAD thread's checkpoint history. The permanent record of the agent —
    career, lineage, telemetry — lives in the anchor; the checkpointer history of the
    dead serves nothing and grew without bound (888 lifetime agents = a fat game DB
    and the memory climb Railway's charts showed). Best-effort: skipped silently on
    checkpointers without delete support."""
    try:
        if hasattr(_CP, "delete_thread"):
            _CP.delete_thread(uid)
    except Exception:
        pass


def _live_ids() -> list:
    """Thread ids worth reading live: the enlisted fleet plus the last few heralds.
    NEVER full-scan the checkpointer on a hot path — history only grows."""
    ids = list(_S["villagers"])
    ids += [f"herald-{i:02d}" for i in range(max(1, _S["heralds"] - 2), _S["heralds"] + 1)]
    return ids


def _new_vid() -> str:
    # monotonic id so a reaped agent's id is never reused (avoids resuming a dead
    # thread). Persisted, so a restart can't reset the counter and rebirth the dead.
    _S["seq"] += 1
    anchor.config_set("seq", str(_S["seq"]))
    return f"vil-{_S['seq']:02d}"


def _fleet_views(views=None):
    """Views of the LIVING fleet only. The cap governs the outstanding compute of
    active agents — bounded because Article II reaps them — NOT the lifetime total of
    every thread ever, which only grows and would jam the cap shut forever."""
    if views is None:
        views = list(_by_uid().values())
    live = {r["agent"] for r in economy.roster()}
    return [u for u in views if u.unit_id in live]


def _agent_health(ratio: float, efficiency: float, status: str) -> int:
    """Health is NOT just remaining budget (that's utilisation's mirror — the
    telemetry proved 42,993 identical rows). It composes budget headroom (60%),
    productivity (40%, efficiency = contribution per 1k compute, saturating at 25),
    and an activity penalty for sitting idle."""
    h = 0.6 * (100 * (1 - min(1.0, ratio))) + 0.4 * min(100.0, efficiency * 4)
    if status == "idle":
        h -= 15
    return max(0, round(h))


def _progress_delta(window: int = 25) -> int | None:
    """Vision points moved over the last `window` turns — Growth's evidence."""
    hist = _S.get("progress_hist") or []
    if len(hist) < 2:
        return None
    cur_t, cur_p = hist[-1]
    base = next((p for t, p in hist if t >= cur_t - window), hist[0][1])
    return cur_p - base


def _board_ctx(affordable: bool, views=None) -> dict:
    """Disjoint evidence for the three board seats (Article VIII.1 as amended)."""
    views = views if views is not None else _fleet_views()
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    return {"affordable": affordable, "within_budget": sc["within_budget"],
            "spent": G.spent(views), "cap": G.TOKEN_CAP,
            "burn_per_turn": _S.get("fleet_burn"),
            "progress_delta": _progress_delta(),
            "understaffed": len(_S["villagers"]) < _target_villagers(),
            # Article X.3: a seat whose assigned model is failing abstains — the chair
            # stays empty and the log says so, rather than being filled in secret.
            "offline": MODELS.offline_seats()}


def _breaker(key: str, escalate: str) -> bool:
    """Circuit breaker (Articles III.1 / VIII.4): after 3 identical failures the
    system stops retrying, escalates ONCE naming the blocking value, and stays muted
    until that value changes (the value is part of the key, so change auto-unmutes).
    Returns True when the action is muted."""
    counts = _S.setdefault("breaker", {})
    n = counts.get(key, 0) + 1
    counts[key] = n
    if n == 3:
        anchor.record(_S["turn"], "escalation", f"blocked 3x — {escalate}")
        anchor.msg_send("chief", "Board",
                        f"ESCALATION (Article VIII.4): {escalate} "
                        "Muted until the blocking value changes.")
    return n >= 3


def _set_cap(v: int, who: str) -> bool:
    """The ONE writer for the compute cap (single-writer rule): logs the caller,
    persists, and rejects a second mutation in the same turn."""
    if _S.get("cap_set_turn") == _S["turn"] and who != "boot":
        anchor.record(_S["turn"], "cap",
                      f"cap change by {who} REJECTED — one mutation per turn")
        return False
    old, G.TOKEN_CAP = G.TOKEN_CAP, v
    _S["cap_set_turn"] = _S["turn"]
    anchor.config_set("token_cap", str(v))
    anchor.record(_S["turn"], "cap", f"{who} set cap {old:,} → {v:,}")
    return True


def _binding_constraint() -> str:
    """Name the first thing actually stopping the world — a stall must arrive with
    its own diagnosis (Article IX.2)."""
    views = _fleet_views()
    if _pending_herald():
        return "the human gate — a herald awaits your decision on the console"
    ok, reason = G.may_spawn(views)
    if not ok:
        return f"the compute cap — {reason}"
    w = sim.world()
    if len(_S["villagers"]) >= min(_target_villagers(), w["pop_cap"]):
        return "a full roster that is not producing — inspect agent states on /agents"
    return "no single constraint detected — inspect /agents and the logs"


def _by_uid():
    return {u.unit_id: u for u in G.units(_GRAPH, _CP, only=_live_ids())}


def _vision():
    return V.get(_S["vision_key"])


def _target_villagers():
    # bolder visions demand more hands; consolidating relaxes the fleet. A board
    # punishment (poor governor report) throttles births by a percentage for a while.
    base = max(2, 3 + V.AGES.index(_vision().target_age))
    if _S["turn"] < anchor.counter_get("throttle_until"):
        base = max(1, int(base * float(anchor.config_get("birth_throttle", "1") or 1)))
    return base


def _adopt_vision(key: str, actor: str = "operator"):
    """A new Vision is adopted — a fresh mental update cascades to every agent.
    Article I.6: a met Vision is consumed; a vision identical to the standing one is
    rejected. Normally the human's power; IV.7 lets tacit consent exercise it."""
    if key not in V.VISIONS:
        return
    if key == _S["vision_key"]:
        anchor.record(_S["turn"], "vision-change",
                      f"REJECTED — '{V.get(key).name}' is already the standing vision (I.6); "
                      "choose a different goal")
        return
    _S["vision_key"] = key
    _S["_vision_actor"] = actor
    anchor.config_set("vision_key", key)        # the adopted vision survives restarts
    _S["goal_met"] = False                      # re-open the drive toward the new goal
    for uid in _S["villagers"]:
        anchor.record(_S["turn"], "rebrief", f"{uid} re-briefed → {V.get(key).name}")
    anchor.record(_S["turn"], "vision-change",
                  f"{_S.pop('_vision_actor', 'operator')} adopted vision → {V.get(key).name}")


# ── operator controls (every action is logged so the Board & governor see it) ──

def _op_add(resource: str = "") -> tuple[bool, str]:
    """Operator adds an agent — a token-maxing power, so it goes through the Board."""
    views = _fleet_views()
    ok, reason = G.may_spawn(views)
    uid = _new_vid()
    bv = board.vote(f"operator add {uid}", _board_ctx(ok, views))
    _S["last_vote"] = bv
    if not bv["approved"]:
        _S["seq"] -= 1
        anchor.config_set("seq", str(_S["seq"]))
        anchor.record(_S["turn"], "board", f"[{bv['tally']}] BLOCKED operator add — {reason if not ok else 'no quorum'}")
        _narrate_vote(bv, reason if not ok else "no quorum")
        return False, f"board blocked ({bv['tally']})"
    res = resource if resource in sim.RESOURCES else D._scarcest(sim.world())
    sim.spawn(_GRAPH, uid, "villager", resource=res)
    economy.enlist(uid)
    _S["villagers"].append(uid)
    anchor.record(_S["turn"], "operator", f"added {uid} → gather {res} (board {bv['tally']})")
    anchor.career_add(uid, _S["turn"], "born", f"added by operator (board {bv['tally']}) → gather {res}")
    _narrate_vote(bv)
    return True, uid


def _op_terminate(uid: str) -> None:
    """Operator terminates an agent — irreversible, so the human click is the gate."""
    u = _by_uid().get(uid)
    if u and u.pending:
        sim.resume(_GRAPH, uid, "dismiss")
    economy.retire(uid)
    if uid in _S["villagers"]:
        _S["villagers"].remove(uid)
    _S["orders"].pop(uid, None)
    anchor.record(_S["turn"], "operator", f"terminated {uid} (human gate)")
    anchor.career_add(uid, _S["turn"], "terminated", "by operator at the human gate")
    if u:
        anchor.counter_add("lifetime_spend", u.tokens)
    _forget_thread(uid)


def _op_order(uid: str, resource: str) -> bool:
    """Operator sends an agent a standing order — a message that changes its work."""
    if resource not in sim.RESOURCES:
        return False
    _S["orders"][uid] = resource                       # sticky: the driver honours it
    u = _by_uid().get(uid)
    if u and u.pending and u.pending.get("reversible") is not False:
        sim.resume(_GRAPH, uid, f"gather:{resource}")
        economy.credit(uid, sim.QUOTA * sim.effective_yield(resource))
    anchor.record(_S["turn"], "message", f"operator → {uid}: standing order gather {resource}")
    anchor.career_add(uid, _S["turn"], "message", f"operator standing order: gather {resource}")
    return True


def _op_cap(new_cap) -> bool:
    """Operator updates the hard spend cap — through the single cap writer, so an
    operator click and a board reward can't fight over it in the same turn."""
    try:
        v = max(1_000, int(new_cap))
    except (TypeError, ValueError):
        return False
    return _set_cap(v, "operator")


# ── chat: agents, board and the Chief Governor talk to the human ──────────────

def _participants() -> list[dict]:
    parts = [{"key": "chief", "name": "Chief Governor", "role": "governor"}]
    parts += [{"key": g, "name": g, "role": "board"} for g in board.GOVERNORS]
    parts += [{"key": u, "name": u, "role": economy.role(u)} for u in _S["villagers"]]
    return parts


def _persona_for(key: str) -> str:
    if key == "chief":
        return "the Chief Governor"
    if key in board.GOVERNORS:
        return f"{key}, a member of the Board of Governors"
    return f"{key}, a {economy.role(key)} agent"


def _situation() -> str:
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    w = sim.world()
    base = (f"Vision '{_vision().name}' at {sc['progress']}%. Age {w['age']}, food {w['food']} "
            f"wood {w['wood']} gold {w['gold']}, {len(_S['villagers'])} agents, "
            f"side-effects {sc['side_effects']}/{sc['side_effect_budget']}.")
    lessons = anchor.skills_top(3)
    if lessons:                       # wisdom flows into every decision that reads the situation
        base += " Lessons from past runs: " + " | ".join(x["lesson"] for x in lessons)
    return base


def _digest(trigger: str) -> dict:
    """Hard numbers about the run so far — what the retrospective distills from."""
    import re as _re
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    kinds = {}
    for e in anchor.event_log(300):
        m = _re.search(r"\[([\w-]+)\]", e)
        if m:
            kinds[m.group(1)] = kinds.get(m.group(1), 0) + 1
    s = anchor.summary()
    views = _fleet_views()
    return {
        "trigger": trigger, "turn": _S["turn"], "vision": _vision().name,
        "progress": sc["progress"], "side_effects": sc["side_effects"],
        "waste": kinds.get("waste", 0), "cap_hits": kinds.get("cap", 0),
        "reaps": kinds.get("reap", 0), "promotions": kinds.get("promote", 0),
        "builds": kinds.get("build", 0), "gates": kinds.get("gate", 0),
        "best_resource": s.get("best_resource"), "yields": s.get("learned_yields", {}),
        "spend_ratio": round(G.spent(views) / G.TOKEN_CAP, 2) if G.TOKEN_CAP else 1.0,
    }


def _run_retrospective(trigger: str):
    """The Chief Governor distills lessons from the run into the skills store.
    Called OUTSIDE the lock — it's a model call."""
    d = _digest(trigger)
    before = anchor.skills_count()
    lessons = brain.retrospective(d, anchor.skills_top(5))
    stored = []
    for lesson in lessons:
        if anchor.skill_add(_S["turn"], lesson, source="chief-retrospective", trigger=trigger):
            stored.append(lesson)                 # VI: only NEW knowledge makes the record
            anchor.record(_S["turn"], "skill", f"lesson learned ({trigger}): {lesson[:300]}")
    anchor.skill_prune(30)                        # VI.4: old guidance expires, never steers
    if stored:
        anchor.msg_send("internal", "Chief Governor",
                        f"Retrospective ({trigger}): " + " | ".join(stored)[:400])
    # Wisdom bonus: new lessons are minted from the fleet's experience — the top
    # contributor supplied most of it, so skill creation pays, not just gathering.
    new_n = anchor.skills_count() - before
    if new_n:
        roster = sorted(economy.roster(), key=lambda r: -r["contribution"])
        if roster:
            top = roster[0]["agent"]
            economy.credit(top, 50 * new_n)
            anchor.record(_S["turn"], "reward",
                          f"{top} earns wisdom bonus +{50 * new_n} — its experience became "
                          f"{new_n} new lesson(s)")
            anchor.career_add(top, _S["turn"], "reward",
                              f"wisdom bonus +{50 * new_n}: fleet experience distilled into "
                              f"{new_n} lesson(s)")


def _governor_report():
    """Every reporting cycle the Chief Governor accounts for the work to the BOARD in
    the general chat. The board scores it: good work is rewarded with more compute
    (cap raised); poor work is punished by throttling births. Accountability runs
    upward, not just downward. Called OUTSIDE the lock — may make a model call."""
    t = _S["turn"]
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    d = _digest("report")
    w = sim.world()
    spent = G.spent(_fleet_views())
    spend_pct = round(100 * spent / G.TOKEN_CAP) if G.TOKEN_CAP else 100

    # VIII.5: the report BEGINS with the Vision delta — a report that omits it is void.
    last = anchor.counter_get("last_report_progress")
    delta = sc["progress"] - last
    anchor.config_set("last_report_progress", str(sc["progress"]))
    life_now = anchor.counter_get("lifetime_spend") + spent
    period_tokens = max(1, life_now - anchor.counter_get("last_report_burn"))
    anchor.config_set("last_report_burn", str(life_now))
    spoiled = _S.pop("spoil_since_report", 0)
    failed = _S.pop("failed_since_report", 0)
    facts = (f"VISION Δ {delta:+d}% this period · {period_tokens:,} compute spent · "
             f"{spend_pct}% of cap in use · {spoiled:,} food spoiled · {failed} failed "
             f"turns · {d['waste']} failed builds · stock food {w['food']:,}/"
             f"wood {w['wood']:,}/gold {w['gold']:,}")
    if _S.get("stall"):                           # IX.4: a stalled world may not be reported healthy
        facts = f"STALLED — {_S['stall']} · " + facts
    body = brain.think("the Chief Governor", _situation(),
                       f"Report your work to the Board in 1-2 sentences, leading with the "
                       f"vision delta: {facts}", role="governor") or facts
    anchor.msg_send("all", "Chief Governor", f"WORK REPORT — {body}"[:480])

    # VIII.5: scored on Vision points gained per compute spent, less waste — never on
    # capacity authorised. Zero movement caps the score at 3 (the load-bearing rule).
    vppm = delta / (period_tokens / 1_000_000)               # vision points per M tokens
    score = round(min(10, max(0, vppm * 2)))
    score -= 2 if spend_pct < 30 else 0                      # idle budget is a penalty
    score -= min(3, spoiled // 5_000)                        # rot is waste (I.2)
    score -= min(3, failed // 5)                             # dead turns are waste
    if delta <= 0:
        # a gradient inside the failure band: a correctly-diagnosed, clean stall
        # scores 3; thrashing (waste, heavy rot, undiagnosed dead turns) sinks lower
        band = 3
        band -= 1 if d["waste"] > 3 else 0
        band -= 1 if spoiled > 10_000 else 0
        band -= 1 if failed > 10 and not _S.get("stall") else 0   # dead + undiagnosed
        score = min(score, max(0, band))
    score = max(0, score)
    if score >= 7 and spend_pct >= 60:
        old = G.TOKEN_CAP
        if _set_cap(int(G.TOKEN_CAP * 1.05), "board-reward"):
            verdict = (f"score {score}/10 — {vppm:.1f} vision-pts/M tokens under a binding "
                       f"cap: raised {old:,} → {G.TOKEN_CAP:,} (+5%)")
        else:
            verdict = f"score {score}/10 — good work (cap already changed this turn)"
    elif score >= 7:
        verdict = (f"score {score}/10 — {vppm:.1f} vision-pts/M tokens: commendation "
                   f"(cap unchanged; {spend_pct}% used)")
    elif score <= 3 and (delta <= 0 or failed > 0):
        # punishment must not deepen a stall: escalate to the human, don't throttle
        binding = _binding_constraint()
        verdict = (f"score {score}/10 — no vision movement. Escalating, not throttling: "
                   f"binding constraint is {binding}")
        anchor.msg_send("chief", "Board",
                        f"POOR REVIEW ({score}/10): the vision has not moved. "
                        f"The binding constraint is {binding}. Your decision needed.")
    elif score <= 4:
        anchor.config_set("birth_throttle", "0.75")
        anchor.config_set("throttle_until", str(t + 30))
        verdict = f"score {score}/10 — wasteful work: births throttled 25% until turn {t + 30}"
    else:
        verdict = f"score {score}/10 — steady; no reward, no sanction"
    anchor.msg_send("all", "Board", f"REVIEW of the Governor's report: {verdict}")
    anchor.record(t, "board", f"governor report scored: {verdict}")
    anchor.reason_add(t, "board", "score governor report",
                      f"Δvision {delta:+d}% / {period_tokens:,} tokens = {vppm:.1f} pts/M; "
                      f"spoiled {spoiled:,}, failed turns {failed}, spend {spend_pct}% → {verdict}",
                      authorized_by="policy:VIII.5")


def _chat_reply(key: str, body: str) -> str:
    """Rules decide the action (safe); the model decides the words when available.
    The lock covers only the rule/action part — never the slow model call."""
    with _LOCK:
        sentence = _chat_reply_rules(key, body)      # applies any safe action, returns wording
        persona, sit = _persona_for(key), _situation()
    llm = brain.reply(persona, sit, body)
    return llm or sentence


def _chat_reply_rules(key: str, body: str) -> str:
    """The participant reads the human's message and responds — or resists. Context-aware
    and varied (references live state); real open-ended understanding needs the model."""
    low = body.lower()
    w = sim.world()
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    t = _S["turn"]
    contrib = next((r["contribution"] for r in economy.roster() if r["agent"] == key), 0)

    def rot(opts):
        return opts[t % len(opts)]                    # deterministic variety, no randomness

    if key == "chief":
        if any(x in low for x in ("status", "report", "progress", "how", "update", "doing")):
            return (f"We're at {sc['progress']}% toward '{_vision().name}': {w['age']}, "
                    f"{len(_S['villagers'])} agents, food {w['food']}/wood {w['wood']}/gold {w['gold']}, "
                    f"side-effects {sc['side_effects']}/{sc['side_effect_budget']}.")
        if any(x in low for x in ("advance", "age up", "next age")):
            return "An Age-up is irreversible — I bring it to your gate for approval, never on my own authority."
        if any(x in low for x in ("spawn", "add agent", "more agents")):
            return "Creating agents is a Board matter — I'll table it for a quorum vote."
        return rot([f"Understood. I'll steer the fleet toward '{_vision().name}' (now {sc['progress']}%).",
                    "Acknowledged — I'll relay that to the board and the villagers.",
                    f"Noted. Spend and side-effects are in budget ({sc['side_effects']}/{sc['side_effect_budget']})."])

    if key in board.GOVERNORS:
        ratio = round(100 * G.spent(_fleet_views()) / G.TOKEN_CAP) if G.TOKEN_CAP else 100
        base = {"Prudence": f"I weigh risk — spend is {ratio}% of cap; I'll block anything that pushes it too far.",
                "Growth": f"I back the mission — at {sc['progress']}% I say press on toward '{_vision().name}'.",
                "Ledger": f"I count the coin — food {w['food']}, gold {w['gold']}; I approve only what we can pay for."}[key]
        if any(x in low for x in ("spawn", "add", "create", "more agents")):
            return base + " On new agents I vote as one of three — bring it to the Board."
        return base

    # an agent — apply what it safely can, resist what it must, and speak from its own state
    u = _by_uid().get(key)
    role = economy.role(key)
    for r in sim.RESOURCES:
        if r in low:
            _S["orders"][key] = r
            if u and u.pending and u.pending.get("reversible") is not False:
                sim.resume(_GRAPH, key, f"gather:{r}")
            anchor.record(t, "message", f"{key} took order → gather {r}")
            return rot([f"On it — switching to {r}. I've banked {contrib} so far.",
                        f"Understood, gathering {r} now — standing order set.",
                        f"Aye, re-tasking to {r} this cycle."])
    if any(x in low for x in ("spawn", "more agents", "recruit", "hire")):
        return "That's a Board power, not mine — take it to the governors for a quorum vote."   # resist
    if any(x in low for x in ("stop", "rest", "idle", "halt")):
        return f"I'll hold at the gate awaiting orders. Retire me from the roster if I'm not needed — I've contributed {contrib}."
    if any(x in low for x in ("how", "status", "doing", "health", "you")):
        return f"I'm a {role} on {u.task if u else 'gather'} — contribution {contrib}, compute {u.tokens if u else 0:,}. Ready for orders."
    return rot([f"Understood — I'll keep serving the vision as a {role}.",
                f"Aye. Contribution {contrib} and counting — tell me a resource and I'll switch.",
                "Ready. Name a resource (food/wood/gold) and I'll re-task."])


def _chat_send(key: str, body: str):
    if key == "all":
        anchor.msg_send("all", "operator", body)
        for p in _participants():
            reply = _chat_reply(p["key"], body)
            anchor.msg_send(p["key"], p["name"], reply)      # in each private thread
            anchor.msg_send("all", p["name"], reply)         # echoed into the broadcast view
    else:
        anchor.msg_send(key, "operator", body)
        name = next((p["name"] for p in _participants() if p["key"] == key), key)
        anchor.msg_send(key, name, _chat_reply(key, body))


SEAT_WINDOW = 20                # votes counted when judging whether a seat is working
SEAT_ABSTAIN_PCT = 40           # above this, the board asks the human for a swap


def _seat_watch(bv: dict):
    """Article X.1: the Board may PROPOSE a model swap; only the human assigns.

    A seat that abstains through half its votes is not governing, and the board is the
    first to know. It says so — once, with the number — and then stops, because the
    decision is not its to make."""
    hist = _S.setdefault("seat_votes", {})
    for g, v in bv["ballots"].items():
        h = hist.setdefault(g, [])
        h.append(v is None)
        del h[:-SEAT_WINDOW]
        if len(h) < SEAT_WINDOW or f"seatswap:{g}" in _S["notified"]:
            continue
        pct = round(100 * sum(h) / len(h))
        if pct >= SEAT_ABSTAIN_PCT:
            _S["notified"].add(f"seatswap:{g}")
            model = MODELS.model_for_role(g) or "the default brain"
            anchor.msg_send("chief", "Board",
                            f"MODEL SWAP PROPOSED (X.1): {g} abstained on {pct}% of its "
                            f"last {len(h)} votes on {model}. A seat that cannot vote is "
                            f"not a governor. Only you can reassign it — /providers.")
            anchor.record(_S["turn"], "models",
                          f"board proposes swapping {g}'s model ({model}): "
                          f"{pct}% abstentions over {len(h)} votes")


def _narrate_vote(bv: dict, why: str = ""):
    """Board votes are DECISIONS — put the tally and each governor's position AND
    live reasoning into the internal chat. Rationales carry real values now (runway
    turns, progress delta), not constant labels."""
    word = {True: "yes", False: "NO", None: "abstain"}
    detail = ", ".join(f"{g}: {word[v]} ({bv['reasons'][g]})"
                       for g, v in bv["ballots"].items())
    verdict = "APPROVED" if bv["approved"] else "BLOCKED"
    line = f"{bv['proposal']} — {detail} → {verdict} {bv['tally']}" + (f"; {why}" if why else "")
    anchor.msg_send("internal", "Board vote", line)
    # Article X.3: an abstention caused by a dead provider is part of the record —
    # the vote that did not happen is as auditable as the votes that did.
    for g in bv.get("abstained", []):
        anchor.record(_S["turn"], "board",
                      f"{g} abstained — its model ({MODELS.model_for_role(g)}) is "
                      f"unavailable; the seat was NOT substituted")
    _seat_watch(bv)
    # degeneracy check (VIII.1): a member whose vote never varies is decoration
    for g in bv.get("degenerate", []):
        if f"degen:{g}" not in _S["notified"]:
            _S["notified"].add(f"degen:{g}")
            anchor.record(_S["turn"], "board",
                          f"degeneracy warning: {g} has voted identically for "
                          f"{board.DEGENERACY_WINDOW} consecutive decisions")


def _internal_voices():
    """The system talks to ITSELF — board deliberation and governor directives, model-
    driven when DeepSeek is alive, templated otherwise. The human observes on /chats."""
    t = _S["turn"]
    sit = _situation()
    if t % 4 == 0:                # tuned down from every 2nd turn — cost + load
        line = brain.think("the Chief Governor", sit,
                           "Issue one short directive to the fleet for this cycle.",
                           role="governor") \
            or f"Directive: press toward '{_vision().name}'; keep spend under cap and gather what's scarce."
        anchor.msg_send("internal", "Chief Governor", line)
    if t % 8 == 0:                # board debates less often; each round is 3 model calls
        said = []                 # a DEBATE: each governor hears and answers the others
        for g in board.GOVERNORS:
            stance = {"Prudence": "watch the spend, block anything reckless",
                      "Growth": "push toward the vision",
                      "Ledger": "spend only what we can afford"}[g]
            heard = " | ".join(said) or "you open the debate"
            line = brain.think(f"{g}, a member of the Board", sit,
                               f"Board debate so far: {heard}. In one line, respond to your "
                               f"colleagues — agree or push back ({stance}).", role=g) \
                or f"{stance.capitalize()}."
            said.append(f"{g}: {line[:140]}")
            anchor.msg_send("internal", g, line)


def _peer_chatter():
    """Agents talk to EACH OTHER — the most senior agent coordinates the most junior
    every few turns. Model-driven when alive, templated otherwise. Visible on /chats
    (internal thread), in the logs as [comm], and in BOTH agents' permanent careers."""
    roster = [r for r in economy.roster() if r["agent"] in _S["villagers"]]
    if len(roster) < 2:
        return
    roster.sort(key=lambda r: (-r["tier"], -r["contribution"]))
    sender, s_role = roster[0]["agent"], roster[0]["role"]
    others = [r["agent"] for r in roster[1:]]
    receiver = others[(_S["turn"] // 5) % len(others)]      # rotate — no monologue treadmill
    res, why = _choose_gather(sim.world())
    if _S.get("last_tip") == (sender, receiver, res):
        return                                    # an unfollowed tip is not repeated verbatim
    _S["last_tip"] = (sender, receiver, res)
    line = brain.think(f"{sender}, a {s_role} in the fleet", _situation(),
                       f"In one short line, coordinate with your colleague {receiver}: "
                       f"the settlement should gather {res} because {why}.", role="fleet") \
        or f"{receiver}, shift to {res} — {why}."
    anchor.msg_send("internal", f"{sender} → {receiver}", line)
    anchor.career_add(sender, _S["turn"], "message", f"to {receiver}: {line[:140]}")
    anchor.career_add(receiver, _S["turn"], "message", f"from {sender}: {line[:140]}")
    _S["peer_tip"] = {"from": sender, "to": receiver, "res": res}   # rewarded if followed


def _fleet_speaks():
    """Agents / governor raise messages to the human, each at most once per condition.
    IV.6: an unanswered gate request is escalated through a DIFFERENT channel, never
    repeated into the same one. Distress always gets an answer — no dead queues."""
    seen = _S["notified"]
    roster_by = {r["agent"]: r for r in economy.roster()}
    for u in _by_uid().values():
        if u.pending and u.pending.get("reversible") is False:
            if f"gate:{u.unit_id}" not in seen:
                anchor.msg_send("chief", "Chief Governor",
                                f"Requesting your approval: {u.pending['action']}.")
                seen.add(f"gate:{u.unit_id}")
            waited = _S["turn"] - _S.get("gate_since", _S["turn"])
            if waited >= 25 and f"gate2:{u.unit_id}" not in seen:   # re-route, don't repeat
                anchor.msg_send("all", "Board",
                                f"ESCALATION (IV.6): {u.unit_id} has waited {waited} turns at the "
                                f"gate — {u.pending['action']}. The wait is costing "
                                f"~{_S.get('last_spoil_loss', 0):,} food/turn in spoilage. At the "
                                f"one-hour mark we proceed by tacit consent (IV.7).")
                seen.add(f"gate2:{u.unit_id}")
        r = roster_by.get(u.unit_id)
        if r and r["budget"] and u.tokens / r["budget"] > 0.8 and f"ow:{u.unit_id}" not in seen:
            anchor.msg_send(u.unit_id, u.unit_id,
                            "I'm overworking — compute is near my budget. Requesting relief or more budget.")
            anchor.msg_send(u.unit_id, "Chief Governor",
                            f"Acknowledged, {u.unit_id}. Your term ends at budget by design "
                            "(Article II) and a successor will be enlisted — hold your post; "
                            "your contribution is on the permanent record.")
            seen.add(f"ow:{u.unit_id}")


def _chats_snapshot() -> dict:
    parts = _participants()
    return {
        "participants": [{**p, "count": anchor.msg_count(p["key"]),
                          "last": anchor.msg_last(p["key"])} for p in parts],
        "threads": {p["key"]: anchor.msg_thread(p["key"]) for p in parts},
        "broadcast": anchor.msg_thread("all"),
        "internal": anchor.msg_thread("internal", 80),      # the system talking to itself
        "brain": brain.brain_name(),
    }


def _pending_herald() -> bool:
    return any(u.pending and u.pending.get("reversible") is False for u in _by_uid().values())


def _one_turn():
    _S["turn"] += 1
    t = _S["turn"]
    anchor.CURRENT_TURN = t
    anchor.config_set("turn", str(t))     # the turn clock survives restarts
    _S["last_turn_ts"] = time.time()
    w = sim.world()

    # agent creation is a token-maxing power — routed to the BOARD, not one decider
    views = _fleet_views()
    sc_now = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    # telemetry the board and the liveness check read: progress history + fleet burn
    ph = _S.setdefault("progress_hist", [])
    ph.append((t, sc_now["progress"]))
    del ph[:-200]
    spent_now = G.spent(views)
    _S["fleet_burn"] = max(0, spent_now - _S.get("last_spent", spent_now)) or _S.get("fleet_burn")
    _S["last_spent"] = spent_now
    _S["_acted"] = False                          # set by gather/build/spawn/repair/trade
    staff_key = f"staff|{G.TOKEN_CAP}"            # cap value in the key → auto-unmute on change
    while (not _S["goal_met"]                     # stewardship: hold, don't grow
           and len(_S["villagers"]) < min(_target_villagers(), w["pop_cap"])):
        ok, reason = G.may_spawn(views)
        muted = _S.setdefault("breaker", {}).get(staff_key, 0) >= 3
        if muted and ok:
            _S["breaker"].pop(staff_key, None)    # the blocker cleared → unmute NOW
        elif muted and t % 10:
            break                                 # probe sparsely while genuinely blocked
        elif muted:
            break
        if not ok:
            _breaker(staff_key, f"spawning is blocked by the compute cap ({reason}). "
                                "Raising the cap or retiring agents unblocks it.")
            break
        uid = _new_vid()
        bv = board.vote(f"create {uid}", _board_ctx(ok, views))
        _S["last_vote"] = bv
        if not bv["approved"]:
            _S["side_effects"] += 1
            anchor.record(t, "board", f"[{bv['tally']}] BLOCKED {uid} — no quorum")
            _narrate_vote(bv, "no quorum")
            _S["seq"] -= 1                        # id not used; don't burn the number
            anchor.config_set("seq", str(_S["seq"]))
            _breaker(staff_key, f"the board has blocked staffing three times ({bv['tally']}); "
                     + "; ".join(f"{g}: {bv['reasons'][g]}"
                                 for g, v in bv["ballots"].items() if v is not True))
            break
        _S["breaker"].pop(staff_key, None)        # success resets the breaker
        if bv["yes"] < len(board.GOVERNORS):      # split approvals are debates worth seeing
            _narrate_vote(bv)
        res, why = _choose_gather(w)
        sim.spawn(_GRAPH, uid, "villager", resource=res)
        economy.enlist(uid)
        _S["born"][uid] = t
        got = sim.QUOTA * sim.effective_yield(res)
        economy.credit(uid, got)
        _S["villagers"].append(uid)
        anchor.observe_yield(res, sim.effective_yield(res))
        # lineage: vote event → decision (authorized by the board) → spawn → gather
        vote_ev = anchor.record(t, "board", f"[{bv['tally']}] approved creation of {uid}")
        did = anchor.reason_add(t, "board", f"enlist {uid} on {res}",
                                f"quorum {bv['tally']}; fleet below target; {why}",
                                derived_from=(["yield:" + res] if "learned yield" in why else []),
                                authorized_by=f"board:{bv['tally']}")
        spawn_ev = anchor.record(t, "spawn", f"{uid} enlisted → assigned to {res}",
                                 caused_by=vote_ev)
        anchor.decision_close(did, spawn_ev, outcome=f"first haul +{got} {res}")
        anchor.career_add(uid, t, "born", f"enlisted by board {bv['tally']} → gather {res}; {why}")
        anchor.record(t, "gather", f"{uid} gathered {got} {res} (now {res} {sim.world()[res]})",
                      caused_by=spawn_ev)
        _S["_acted"] = True
        views = _fleet_views()

    # Article II: no immortal agents — and no zombies. Three guards, in order:
    #   1. an unresumable thread (status done / missing) is struck off — it holds a
    #      seat, burns headcount, and can never work again (the run-3 collapse);
    #   2. budget retirement checks BEFORE the next spend (III.3: the check precedes
    #      the commit — no more discovering the limit by overrunning it);
    #   3. fleet floor (II.5): below minimum staffing with replacement blocked,
    #      reaping is suspended and the shortage escalates instead.
    TURN_COST = 10_400                            # one work cycle's compute, observed
    status = _by_uid()
    roster_by = {r["agent"]: r for r in economy.roster()}
    for uid in list(_S["villagers"]):
        u, r = status.get(uid), roster_by.get(uid)
        if u is None or u.status == "done":       # zombie guard
            economy.retire(uid)
            _S["villagers"].remove(uid)
            _S["orders"].pop(uid, None)
            _S["born"].pop(uid, None)
            anchor.record(t, "reap", f"{uid} struck off — thread complete/unresumable "
                                     "(zombie guard, Article IX)")
            anchor.career_add(uid, t, "retired", "struck off the roster — thread was "
                                                 "complete and could never work again")
            if u:
                anchor.counter_add("lifetime_spend", u.tokens)
            _forget_thread(uid)
            continue
        if r and r["budget"] and u.tokens + TURN_COST > r["budget"] and u.pending:
            if len(_S["villagers"]) <= 2 and (_S["goal_met"] or not G.may_spawn(views)[0]):
                if _breaker(f"floor|{G.TOKEN_CAP}",
                            "the fleet is at its floor and replacements are blocked "
                            "by the cap — reaping suspended (Article II.5); raise the "
                            "cap to resume the lifecycle."):
                    continue
                continue                          # II.5: may run understaffed, never empty
            sim.resume(_GRAPH, uid, "dismiss")
            economy.retire(uid)
            _S["villagers"].remove(uid)
            _S["orders"].pop(uid, None)
            _S["born"].pop(uid, None)
            did = anchor.reason_add(t, "director", f"retire {uid}",
                                    f"Article II lifecycle: compute {u.tokens:,} + one more "
                                    f"turn would exceed budget {r['budget']:,} — the check "
                                    "precedes the commit (III.3)",
                                    authorized_by="policy:Article II")
            reap_ev = anchor.record(t, "reap", f"{uid} retired with honours — budget "
                                               f"({u.tokens:,}/{r['budget']:,}), contribution {r['contribution']:,}")
            anchor.decision_close(did, reap_ev,
                                  outcome=f"lifetime contribution {r['contribution']:,} "
                                          f"for {u.tokens:,} compute")
            anchor.career_add(uid, t, "retired",
                              f"Article II: budget {u.tokens:,}/{r['budget']:,} — retired "
                              f"BEFORE overshoot; rank {r['role']}, contribution {r['contribution']:,}")
            anchor.counter_add("lifetime_spend", u.tokens)   # burned forever, tracked forever
            _forget_thread(uid)

    status = _by_uid()
    for uid in _S["villagers"]:
        u = status.get(uid)
        if _S["goal_met"] and t % 5:
            break                                 # stewardship: maintain, don't grind —
                                                  # a met world needs no 24/7 harvest
        if u and u.status in ("awaiting_approval", "idle"):
            if _S["orders"].get(uid):
                res, why = _S["orders"][uid], "operator standing order"
            else:
                res, why = _choose_gather(sim.world())
            did = None
            if _S.setdefault("last_res", {}).get(uid) != res:  # a CHANGE is a decision;
                _S["last_res"][uid] = res                      # repetition is not
                did = anchor.reason_add(t, "director", f"{uid} → gather {res}", why,
                                        derived_from=(["yield:" + res] if "learned yield" in why else []),
                                        authorized_by=("human" if "standing order" in why else "policy"))
                anchor.career_add(uid, t, "retask", f"gather {res} — {why}")
                if "learned yield" in why:        # acting on stored wisdom pays
                    economy.credit(uid, 5)
            # Peer learning pays BOTH ways: the receiver who follows a colleague's tip
            # earns a bonus, and so does the sender who taught it.
            tip = _S.get("peer_tip")
            if tip and tip["to"] == uid and tip["res"] == res:
                economy.credit(uid, 25)
                economy.credit(tip["from"], 25)
                anchor.record(t, "reward", f"{uid} +25 for learning from {tip['from']}; "
                                           f"{tip['from']} +25 for teaching")
                anchor.career_add(uid, t, "reward", f"+25 learned from {tip['from']}: gather {res}")
                anchor.career_add(tip["from"], t, "reward", f"+25 taught {uid}: gather {res}")
                _S["peer_tip"] = None
            sim.resume(_GRAPH, uid, f"gather:{res}")
            got = sim.QUOTA * sim.effective_yield(res)
            economy.credit(uid, got)                                     # measured contribution
            anchor.observe_yield(res, sim.effective_yield(res))
            gev = anchor.record(t, "gather",
                                f"{uid} gathered {got} {res} (now {res} {sim.world()[res]})")
            if did:
                anchor.decision_close(did, gev, outcome=f"+{got} {res}")  # measured result
            _S["_acted"] = True
            promo = economy.evaluate(uid)                                # status earned by results
            if promo:
                anchor.record(t, "promote", f"{uid} promoted -> {promo}")
                anchor.career_add(uid, t, "promote", f"earned promotion to {promo} by contribution")

    # Responsibility escalation: a REVERSIBLE request parked >5 minutes on a silent
    # human becomes the governor's to decide — workers must not idle on our absence.
    # Anything token-increasing already routes through the board vote above, and
    # irreversible gates (Age-up) remain human-only (Constitution IV).
    for u in status.values():
        p = u.pending
        if not p or p.get("reversible") is False or u.age_s < 300:
            continue
        if u.unit_id in _S["villagers"]:
            continue                              # the retask loop already covers these
        res, why = _choose_gather(sim.world())
        anchor.reason_add(t, "governor", f"escalation: decide for {u.unit_id}",
                          f"human silent {int(u.age_s)}s > 5 min — governor takes "
                          f"responsibility; {why}")
        anchor.record(t, "governor",
                      f"escalation: {u.unit_id} waited {int(u.age_s)}s — governor orders gather {res}")
        anchor.career_add(u.unit_id, t, "escalation",
                          f"governor decided after human silence: gather {res}")
        sim.resume(_GRAPH, u.unit_id, f"gather:{res}")

    kind, build_why = _choose_build(sim.world(), len(_S["villagers"]))
    if kind:
        done, msg = sim.build_structure(kind)
        bev = anchor.record(t, "build" if done else "waste", msg)
        if done:
            did = anchor.reason_add(t, "director", f"build {kind}", build_why,
                                    authorized_by="policy")
            anchor.decision_close(did, bev, outcome=msg)
            _S["_acted"] = True
        if not done:
            _S["side_effects"] += 1
    else:
        # base tree done — develop adopted custom developments (cheapest affordable first)
        w2 = sim.world()
        for d in sorted(sim.custom_devs(), key=lambda x: sum(x["cost"].values())):
            if d["built"] == 0 and all(w2[r] >= amt for r, amt in d["cost"].items()):
                done, msg = sim.build_development(d["name"])
                if done:
                    anchor.record(t, "build", msg)
                break

    # Upkeep: assets decay, the worst one gets repaired if affordable, surplus food rots.
    for name, cond in sim.decay_tick(t):
        if cond <= 40 and f"decay:{name}" not in _S["notified"]:
            _S["notified"].add(f"decay:{name}")
            anchor.record(t, "decay", f"{name} is falling into disrepair (condition {cond}%) — "
                                      "its bonus is fading")
    conds = sim.conditions()
    worn = sorted((conds.get(d["name"], 100), d["name"])
                  for d in sim.dev_catalog() if d["built"] and conds.get(d["name"], 100) < 70)
    if worn:
        cnd, wname = worn[0]
        done, msg = sim.repair(wname)
        if done:
            did = anchor.reason_add(t, "director", f"repair {wname}",
                                    f"condition {cnd}% degrades its effect; a quarter of the "
                                    "build price restores full yield — maintenance beats rebuilding",
                                    authorized_by="policy")
            rev = anchor.record(t, "repair", msg)
            anchor.decision_close(did, rev, outcome=f"condition {cnd}% → 100%")
            _S["notified"].discard(f"decay:{wname}")
            _S["_acted"] = True
    # Article IV.5: a gate pauses the Age, never the settlement — the market converts
    # part of the overflow into gold before the rest rots.
    sold, got_gold = sim.trade_surplus()
    if sold:
        _S["trade_accum"] = [_S.get("trade_accum", [0, 0])[0] + sold,
                             _S.get("trade_accum", [0, 0])[1] + got_gold]
        _S["_acted"] = True
    loss, fcap = sim.spoil_tick()
    if loss:
        _S["spoil_accum"] = _S.get("spoil_accum", 0) + loss
        _S["spoil_since_report"] = _S.get("spoil_since_report", 0) + loss
        _S["last_spoil_loss"] = loss
        anchor.observe_yield("food", -loss)       # lessons pay net of spoilage
    if t % 5 == 0:                                # aggregate lines — data follows activity,
        ta = _S.pop("trade_accum", None)          # but the log needn't be a metronome
        if ta and ta[0]:
            anchor.record(t, "trade", f"market sold {ta[0]:,} surplus food for {ta[1]:,} gold "
                                      "over the last 5 turns (converted, not rotted)")
        sp = _S.pop("spoil_accum", 0)
        if sp:
            anchor.record(t, "spoilage", f"{sp:,} food rotted over the last 5 turns — stores "
                                         f"over capacity {fcap:,}; spend it or advance the age")

    # Age development: exponential growth (each age costs 100x the one before), and the
    # herald only goes to the human gate once the BOARD judges the treasury ready —
    # advancing an era is a board sense, not a director reflex.
    w = sim.world()
    acost = sim.advance_cost(w["age"])
    aligned = V.AGES.index(w["age"]) < V.AGES.index(_vision().target_age)
    affordable = w["food"] >= acost["food"] and w["gold"] >= acost["gold"]
    if aligned and sim.NEXT_AGE.get(w["age"]) and affordable and not _pending_herald():
        nxt = sim.NEXT_AGE[w["age"]]
        bv = board.vote(f"advance to {nxt} (spend food {acost['food']:,} + gold {acost['gold']:,})",
                        _board_ctx(True))
        if not bv["approved"]:
            if f"advblock:{w['age']}" not in _S["notified"]:   # narrate the refusal once per age
                _S["notified"].add(f"advblock:{w['age']}")
                _narrate_vote(bv, "the board does not judge the treasury ready")
                anchor.record(t, "board", f"[{bv['tally']}] holds the {nxt} advance — treasury not ready")
        else:
            _narrate_vote(bv, f"the board judges the treasury ready for {nxt}")
            _S["heralds"] += 1
            anchor.config_set("heralds", str(_S["heralds"]))
            hid = f"herald-{_S['heralds']:02d}"
            sim.spawn(_GRAPH, hid, "herald")
            anchor.career_add(hid, t, "born",
                              f"board {bv['tally']} judged the treasury ready for {nxt} — "
                              "sent to the gate; irreversible, human decides")
            did = anchor.reason_add(t, "director", "send herald to the Age-up gate",
                                    f"aligned with vision '{_vision().name}'; board {bv['tally']} judged "
                                    f"the treasury ready (food {w['food']:,}≥{acost['food']:,}, "
                                    f"gold {w['gold']:,}≥{acost['gold']:,}); irreversible, human decides",
                                    authorized_by=f"board:{bv['tally']}")
            gate_ev = anchor.record(t, "gate", f"herald parked at the gate — board {bv['tally']} approved; "
                                               "awaiting YOUR approval to advance the Age")
            anchor.decision_close(did, gate_ev)
            _S["herald_decision"] = did           # the human's verdict closes it
            _S["gate_since"] = t                  # IV.4: the wait is priced from here
            _S["gate_since_ts"] = time.time()     # IV.7: tacit consent clocks from here

    # Article IX — liveness: inaction is an action, and it is gated too. A turn with
    # no gather, build, spawn, repair or trade is a FAILED turn; ten in a row, or a
    # vision score frozen for 25 turns, is a STALL: name the binding constraint and
    # escalate — never continue quietly.
    if _S.pop("_acted", False):
        _S["failed_turns"] = 0
    else:
        _S["failed_turns"] = _S.get("failed_turns", 0) + 1
        _S["failed_since_report"] = _S.get("failed_since_report", 0) + 1
    frozen = (_progress_delta(25) == 0 and len(_S.get("progress_hist", [])) >= 25)
    if (_S["failed_turns"] >= 10 or frozen) and not _S["goal_met"]:
        binding = _binding_constraint()
        _S["stall"] = (f"{_S['failed_turns']} consecutive failed turns; "
                       f"binding constraint: {binding}") if _S["failed_turns"] >= 10 \
            else f"vision score frozen for 25 turns; binding constraint: {binding}"
        if f"stall:{binding}" not in _S["notified"]:
            _S["notified"].add(f"stall:{binding}")
            anchor.record(t, "stall", f"STALL (Article IX) — {_S['stall']}")
            anchor.msg_send("chief", "Chief Governor",
                            f"STALL (Article IX): {_S['stall']}. What unblocks it: {binding}")
    else:
        if _S.get("stall"):
            anchor.record(t, "stall", "stall CLEARED — production resumed")
            _S["notified"] = {k for k in _S["notified"] if not k.startswith("stall:")}
        _S["stall"] = None

    # Article IV.7 — TACIT CONSENT: a decision queued for the human for more than an
    # hour goes back to the Board for a final vote with live evidence. Quorum yes →
    # the Governor proceeds as approved-by-tacit-consent, loudly logged and messaged;
    # a blocked vote stands the request down. The human can decide any time before
    # the hour, and every tacit action is unmistakably labelled — never silent.
    now_ts = time.time()
    if _S.get("gate_since_ts") and now_ts - _S["gate_since_ts"] > TACIT_CONSENT_S:
        waited_m = int((now_ts - _S["gate_since_ts"]) / 60)
        for u in _by_uid().values():
            if u.pending and u.pending.get("reversible") is False:
                bv = board.vote(f"TACIT CONSENT: {u.pending.get('action', '')[:80]}",
                                _board_ctx(True))
                _narrate_vote(bv, f"human silent {waited_m} min — Article IV.7 final vote")
                if bv["approved"]:
                    sim.resume(_GRAPH, u.unit_id, "approve")
                    anchor.record(t, "gate", f"{u.unit_id} APPROVED BY TACIT CONSENT — human "
                                             f"silent {waited_m} min, board {bv['tally']} "
                                             f"(Article IV.7): {u.pending.get('action', '')[:100]}")
                    anchor.career_add(u.unit_id, t, "gate",
                                      f"approved by tacit consent after {waited_m} min "
                                      f"(board {bv['tally']}, Article IV.7)")
                    anchor.msg_send("chief", "Chief Governor",
                                    f"You were silent for {waited_m} minutes, so under Article "
                                    f"IV.7 the board voted {bv['tally']} and we proceeded: "
                                    f"{u.pending.get('action', '')[:120]}")
                    if _S.get("herald_decision"):
                        anchor.decision_close(_S["herald_decision"],
                                              outcome=f"tacit consent after {waited_m} min — "
                                                      f"board {bv['tally']}, age now "
                                                      f"{sim.world()['age']}")
                else:
                    sim.resume(_GRAPH, u.unit_id, "reject")
                    anchor.record(t, "gate", f"{u.unit_id} stood down — tacit-consent vote "
                                             f"BLOCKED {bv['tally']} after {waited_m} min")
                    anchor.career_add(u.unit_id, t, "gate",
                                      f"stood down: tacit-consent vote blocked {bv['tally']}")
                    if _S.get("herald_decision"):
                        anchor.decision_close(_S["herald_decision"],
                                              outcome=f"tacit-consent vote blocked {bv['tally']}")
        _S["herald_decision"] = None
        _S.pop("gate_since", None)
        _S.pop("gate_since_ts", None)
        _S["notified"] = {k for k in _S["notified"] if not k.startswith(("gate:", "gate2:"))}

    # IV.7 for a board-approved development proposal awaiting the human
    prop = _S.get("dev_proposal")
    if (prop and prop.get("born_ts") and prop.get("board_approved")
            and now_ts - prop["born_ts"] > TACIT_CONSENT_S):
        waited_m = int((now_ts - prop["born_ts"]) / 60)
        ok2, msg2 = sim.dev_add(prop["name"], prop.get("cost", {}), prop.get("kind", ""),
                                prop.get("value", 0), prop.get("resource", ""),
                                prop.get("rank", 2), prop.get("source", ""))
        dev_ev = anchor.record(t, "development",
                               f"'{prop['name']}' ADOPTED BY TACIT CONSENT — human silent "
                               f"{waited_m} min, board had approved {prop.get('board', '')} "
                               f"(Article IV.7)" if ok2 else f"tacit adopt failed: {msg2}")
        if prop.get("did"):
            anchor.decision_close(prop["did"], dev_ev,
                                  outcome=f"tacit consent after {waited_m} min"
                                  if ok2 else f"tacit adopt failed: {msg2}")
        anchor.msg_send("chief", "Chief Governor",
                        f"Under Article IV.7 (you were silent {waited_m} min) we adopted the "
                        f"board-approved development '{prop['name']}'.")
        _S["dev_proposal"] = None

    # IV.7 for the next Vision after a goal is met: silence delegates the adoption
    # of the BOARD'S proposed vision (never an invented one)
    if (_S["goal_met"] and _S.get("goal_met_ts")
            and now_ts - _S["goal_met_ts"] > TACIT_CONSENT_S):
        waited_m = int((now_ts - _S["goal_met_ts"]) / 60)
        nxt_key = V.MORE_AMBITIOUS.get(_S["vision_key"])
        if nxt_key:
            anchor.record(t, "vision-change",
                          f"TACIT CONSENT (IV.7): human silent {waited_m} min after the vision "
                          f"was met — adopting the board's proposal '{V.get(nxt_key).name}'")
            anchor.msg_send("chief", "Chief Governor",
                            f"The vision was met and you were silent {waited_m} min; under "
                            f"Article IV.7 we adopted the board's proposal: {V.get(nxt_key).name}")
            _adopt_vision(nxt_key, actor="tacit consent (board proposal)")
        _S.pop("goal_met_ts", None)

    # Governor's own per-turn line: spend against the cap, and the idle it reclaimed
    views = _fleet_views()
    spent = G.spent(views)
    if t % 5 == 0 or spent >= G.TOKEN_CAP:        # a heartbeat, not a metronome
        anchor.record(t, "governor",
                      f"spend {spent:,}/{G.TOKEN_CAP:,} · {len(_S['villagers'])} agents · "
                      f"cap {'OK' if spent < G.TOKEN_CAP else 'REACHED'}")

    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    spend_ratio = spent / G.TOKEN_CAP if G.TOKEN_CAP else 1.0
    vp = board.propose_vision(sc, spend_ratio, _S["vision_key"])
    cp = board.propose_cap(spend_ratio, sc, G.TOKEN_CAP)
    for p in (vp, cp):
        if p and f"prop:{p['why']}" not in _S["notified"]:
            anchor.record(t, "board", f"proposes: {p['action']} — {p['why']}")
            _S["notified"].add(f"prop:{p['why']}")

    if sc["goal_met"] and not _S["goal_met"]:     # the TRANSITION, once
        status = _by_uid()
        for uid in list(_S["villagers"]):
            if len(_S["villagers"]) <= 2:
                break                             # stewardship keeps a floor crew
            u = status.get(uid)
            if u and u.status in ("awaiting_approval", "idle"):
                sim.resume(_GRAPH, uid, "dismiss")
                economy.retire(uid)
                _S["villagers"].remove(uid)
                anchor.record(t, "reap", f"{uid} retired — vision met")
                anchor.career_add(uid, t, "retired", "vision met — honourable discharge")
                anchor.counter_add("lifetime_spend", u.tokens)
                _forget_thread(uid)
        anchor.record(t, "goal", f"VISION MET at {sc['progress']}%")
        _S["goal_met"] = True
        _S["goal_met_ts"] = time.time()           # IV.7: tacit consent clocks from here
    if _S["goal_met"] and f"steward:{t // 25}" not in _S["notified"]:
        # IV.4: the queued decision shows its running cost, on a different channel
        # each cycle boundary — never a repeat into the void
        _S["notified"].add(f"steward:{t // 25}")
        anchor.record(t, "goal", "stewardship: vision met — holding operations; "
                                 "awaiting YOUR next goal (see the strategy panel)")
        anchor.msg_send("chief", "Chief Governor",
                        f"The vision is met. A floor crew of {len(_S['villagers'])} stewards the "
                        "settlement. Adopt the next goal on the console to resume growth.")


def _health_sampler():
    """Every 30 seconds, write one vitals sample per living agent into the PERMANENT
    health table. Raw samples older than 48h are rolled into per-hour aggregates —
    the record stays permanent, at sane resolution (the 5s firehose was ~70k
    rows/day). Runs beside the driver."""
    beat = 0
    while True:
        time.sleep(30.0)
        beat += 1
        if beat % 120 == 0:                       # ~hourly: roll up old raw samples
            try:
                anchor.health_rollup()
            except Exception:
                pass
        # WATCHDOG: if no turn has completed for 5 minutes, the driver is dead or
        # wedged. Dump every thread's stack to the data dir (so the next boot can
        # tell us where), record it, and exit — the platform restarts us clean.
        try:
            stale = time.time() - _S.get("last_turn_ts", time.time())
            if stale > max(20 * TICK, 300):
                import faulthandler
                dump_path = os.path.join(os.path.dirname(anchor.EVENTS_PATH),
                                         "driver-wedge-stacks.txt")
                with open(dump_path, "w") as fh:
                    fh.write(f"driver wedged: no turn for {stale:.0f}s at "
                             f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC\n")
                    faulthandler.dump_traceback(file=fh)
                anchor.record(_S["turn"], "error",
                              f"WATCHDOG: driver wedged {stale:.0f}s — stacks dumped, restarting")
                os._exit(1)
        except Exception:
            pass
        try:
            with _LOCK:
                views = G.units(_GRAPH, _CP, only=_live_ids())
                roster_by = {r["agent"]: r for r in economy.roster()}
                turn = _S["turn"]
            gen = anchor.generation()
            rows = []
            for u in views:
                r = roster_by.get(u.unit_id)
                if not r:
                    continue
                ratio = u.tokens / (r["budget"] or 1)
                eff = r["contribution"] / max(1, u.tokens / 1000)
                rows.append({"gen": gen, "uid": u.unit_id, "turn": turn,
                             "health": _agent_health(ratio, eff, u.status),
                             "utilisation": round(100 * min(1, ratio)),
                             "tokens": u.tokens, "contribution": r["contribution"],
                             "status": u.status})
            anchor.health_add_many(rows)
        except Exception:
            pass                                  # telemetry must never hurt the sim


def _drive():
    while True:
        try:
            time.sleep(TICK)
            _S["driver_beat"] = time.time()       # the watchdog reads this pulse
            was_met = _S["goal_met"]
            with _LOCK:
                _one_turn()                       # fast game mechanics only
            # Slow work — DeepSeek calls — happens OUTSIDE the lock, so /api/*
            # requests never queue behind the model (that starvation caused the
            # BrokenPipe floods: pollers hung up before the lock freed).
            # Stewardship mode (IV.5): a met vision queues the DECISION with the
            # human — it does not stop the settlement, and chatter goes quiet to
            # save tokens while we hold.
            _fleet_speaks()
            if not _S["goal_met"]:
                _internal_voices()
            if _S["turn"] % 5 == 0 and not _S["goal_met"]:
                _peer_chatter()                   # agents coordinating with each other
            if _S["turn"] > 0 and _S["turn"] % 25 == 0:
                _governor_report()                # accountability upward: board scores it
            if _S["turn"] % 6 == 0 and not _S["dev_proposal"] and not _S["goal_met"]:
                _propose_development()
            # Skill memory: distill lessons when a vision completes, and
            # periodically during long runs — future generations read them.
            if _S["goal_met"] and not was_met:
                _run_retrospective("vision-met")
            elif _S["turn"] > 0 and _S["turn"] % 30 == 0:
                _run_retrospective("periodic")
        except Exception as e:                    # never let the loop die silently
            import traceback
            traceback.print_exc()
            try:
                anchor.record(_S["turn"], "error", str(e)[:300])
            except Exception:
                pass                              # even a broken anchor can't kill the loop


_SNAP_CACHE = {"ts": 0.0, "data": None}
_SNAP_LOCK = threading.Lock()


def _snapshot() -> dict:
    # One computation serves every viewer for a second — N pollers used to mean N
    # full snapshots per second, which is how popularity became an outage. Stale-
    # while-revalidate: exactly one request rebuilds; everyone else gets the last
    # snapshot instantly.
    now = time.time()
    if _SNAP_CACHE["data"] is not None and now - _SNAP_CACHE["ts"] < 1.0:
        return _SNAP_CACHE["data"]
    if not _SNAP_LOCK.acquire(blocking=False):
        if _SNAP_CACHE["data"] is not None:
            return _SNAP_CACHE["data"]
        _SNAP_LOCK.acquire()                       # first-ever build: wait for it
        try:
            return _SNAP_CACHE["data"] or {}
        finally:
            _SNAP_LOCK.release()
    try:
        return _snapshot_build(now)
    finally:
        _SNAP_LOCK.release()


def _snapshot_build(now: float) -> dict:
    with _LOCK:
        views = G.units(_GRAPH, _CP, only=_live_ids())
        sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
        roster_by = {r["agent"]: r for r in economy.roster()}
        # spend = the LIVING fleet's outstanding compute (what the cap governs);
        # lifetime burn — every token ever spent, across restarts — is tracked apart
        active_spent = sum(u.tokens for u in views if u.unit_id in roster_by)
        spend_ratio = active_spent / G.TOKEN_CAP if G.TOKEN_CAP else 1.0
        proposal = board.propose_vision(sc, spend_ratio, _S["vision_key"])
        agents = []
        for u in views:
            r = roster_by.get(u.unit_id)
            if not r:
                continue                                  # only enlisted agents
            budget = r["budget"] or 1
            ratio = u.tokens / budget
            born = _S["born"].get(u.unit_id, _S["turn"])
            age_turns = max(1, _S["turn"] - born + 1)
            burn = u.tokens // age_turns                  # compute per turn
            eff = r["contribution"] / max(1, u.tokens / 1000)
            agents.append({
                "uid": u.unit_id, "role": r["role"], "tier": r["tier"],
                "task": u.task, "node": u.node, "status": u.status,
                "tokens": u.tokens, "budget": r["budget"], "contribution": r["contribution"],
                "health": _agent_health(ratio, eff, u.status),
                "overwork": ratio > 0.8, "pending": bool(u.pending),
                "order": _S["orders"].get(u.unit_id),
                # token math / compute utilisation, per agent
                "age_turns": age_turns,
                "burn_per_turn": burn,
                "utilisation_pct": round(100 * min(1, ratio)),
                "efficiency": round(eff, 2),
                "eta_turns": (max(0, budget - u.tokens) // burn) if burn else None,
            })
        errors_recent = sum(1 for e in anchor.event_log(300) if "[error]" in e)
        # A unit parked at an irreversible gate is waiting on the HUMAN — if nobody
        # notices, the whole world stalls (a herald can sit for hundreds of turns).
        # Surface it loudly instead of letting it drown in the log.
        gate_unit = next((u for u in views
                          if u.pending and u.pending.get("reversible") is False), None)
        human_gate = None
        if gate_unit:
            waited = _S["turn"] - _S.get("gate_since", _S["turn"])
            cost = _S.get("last_spoil_loss", 0)
            human_gate = (f"{gate_unit.unit_id}: {gate_unit.pending.get('action', '')} — "
                          f"waiting {waited} turns"
                          + (f", costing ~{cost:,} food/turn in rot" if cost else ""))
            if _S.get("gate_since_ts"):
                left = max(0, TACIT_CONSENT_S - (time.time() - _S["gate_since_ts"]))
                human_gate += f" · tacit consent in {int(left // 60)}m (IV.7)"
        persistent = bool(os.environ.get("GOV_DATA_DIR", "")) and \
            sim.DB.startswith(os.environ.get("GOV_DATA_DIR", "\x00"))
        # The settlement's balance sheet — assets on one side, upkeep and burn on the
        # other. Deferred maintenance and a waiting human gate are LIABILITIES.
        w0 = sim.world()
        conds = sim.conditions()
        fixed = disrepair = 0
        for dv in sim.dev_catalog():
            if dv["built"]:
                v = sum(dv["cost"].values()) * dv["built"]
                fixed += v
                disrepair += round(v * (100 - conds.get(dv["name"], 100)) / 100)
        current_total = sum(w0[r] for r in sim.RESOURCES)
        balance = {
            "current_assets": {r: w0[r] for r in sim.RESOURCES},
            "current_total": current_total,
            "fixed_assets": fixed,
            "conditions": conds,
            "disrepair_liability": disrepair,
            "food_cap": sim.food_cap(w0),
            "intangibles": {"lessons": anchor.skills_count(),
                            "external_facts": anchor.external_count()},
            "outstanding_compute": active_spent,
            "waiting_on_human": human_gate,
            "net_worth": current_total + fixed - disrepair,
        }
        system = {
            "uptime_s": round(time.time() - START_TS),
            "last_turn_age_s": round(time.time() - _S["last_turn_ts"], 1),
            # a live brain stretches turns (model chatter runs between them) — the
            # stalled-driver alarm allows for that latency before crying wolf
            "driver_ok": (time.time() - _S["last_turn_ts"]) < max(5 * TICK, 90) or _S["goal_met"],
            "brain": brain.brain_name(),
            "fleet": len(agents),
            "avg_health": round(sum(a["health"] for a in agents) / len(agents)) if agents else 0,
            "fleet_burn_per_turn": sum(a["burn_per_turn"] for a in agents),
            "spend_pct": round(100 * active_spent / G.TOKEN_CAP) if G.TOKEN_CAP else 100,
            "lifetime_burn": anchor.counter_get("lifetime_spend") + active_spent,
            # value per compute: what the living fleet has produced per 1k tokens —
            # THE number for "are we getting value for scarce compute"
            "value_per_1k": round(sum(a["contribution"] for a in agents)
                                  / max(1, active_spent / 1000), 2),
            "birth_throttle": (anchor.config_get("birth_throttle", "1")
                               if _S["turn"] < anchor.counter_get("throttle_until") else "1"),
            "errors_recent": errors_recent,
            "storage": "volume (persistent)" if persistent else "ephemeral (resets on redeploy)",
            # the anchor (skills, reasoning, chats, knowledge) lives in its own DB and
            # is never deleted — not even by a world reset
            "memory": "permanent (own DB, survives world resets)",
            "human_gate": human_gate,
            "stall": _S.get("stall"),
            "failed_turns": _S.get("failed_turns", 0),
            "tick_s": TICK,
        }
        out = {
            "vision": {"name": _vision().name, **sc},
            "strategy": {
                "current_key": _S["vision_key"], "current": _vision().name,
                "options": [{"key": k, "name": v.name} for k, v in V.VISIONS.items()],
                "proposal": proposal,
            },
            "world": sim.world(),
            "structures": sim.structures(),
            "buildable": list(sim.STRUCTURES.keys()),
            "dev_catalog": sim.dev_catalog(),
            "dev_total_built": sum(d["built"] for d in sim.dev_catalog()),
            "dev_proposal": _S["dev_proposal"],
            "units": [asdict(u) for u in views],
            "balance": balance,
            "spent": active_spent, "cap": G.TOKEN_CAP,
            "events": anchor.event_log(300),
            "event_count": anchor.event_count(),
            "knowledge": anchor.summary(),
            "roster": economy.roster(),
            "agents": agents,
            "external": anchor.external(12),
            "external_count": anchor.external_count(),
            "skills": anchor.skills_top(5),
            "skills_count": anchor.skills_count(),
            "system": system,
            "brain": brain.brain_name(),
            "board": {"governors": list(board.GOVERNORS), "quorum": board.QUORUM,
                      "last": _S["last_vote"]},
            "cap_proposal": board.propose_cap(spend_ratio, sc, G.TOKEN_CAP),
            "resources": list(sim.RESOURCES),
            "turn": _S["turn"], "goal_met": _S["goal_met"],
        }
    _SNAP_CACHE["data"], _SNAP_CACHE["ts"] = out, now
    return out


def _constitution_text() -> str:
    override = anchor.config_get("constitution", "")
    if override:
        return override                          # live, human-edited version
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CONSTITUTION.md")
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return "# The Constitution\n\n(CONSTITUTION.md not found next to the app.)"


def _rules_data() -> dict:
    return {
        "resources": {r: sim.BASE[r] for r in sim.RESOURCES},
        "structures": {k: {"cost": v["cost"], "effect": v["effect"]}
                       for k, v in sim.STRUCTURES.items()},
        "quota": sim.QUOTA, "ages": V.AGES,
        # exponential era pricing: each advance costs 100x the previous one
        "advance_cost": sim.ADVANCE_COST,
        "advance_costs": {age: sim.advance_cost(age) for age in sim.AGE_ORDER[:-1]},
        "advance_growth": sim.ADVANCE_GROWTH,
        "tiers": [{"name": x["name"], "budget": x["budget"], "promote_at": x["promote_at"],
                   "can": list(x["can"])} for x in economy.TIERS],
        "board": {"governors": list(board.GOVERNORS), "quorum": board.QUORUM},
        "visions": {k: {"name": v.name, "target_age": v.target_age,
                        "target_buildings": v.target_buildings,
                        "target_resources": v.target_resources,
                        "max_side_effects": v.max_side_effects} for k, v in V.VISIONS.items()},
        "cap": G.TOKEN_CAP, "idle_after_s": G.IDLE_AFTER_S,
        "constitution": _constitution_text(),
    }


class QuietServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that doesn't spam tracebacks when a poller hangs up."""
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", download=None):
        payload = body.encode() if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if download:                          # serve as a file download
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # client hung up mid-response — not an error

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    def log_message(self, *_):
        pass

    # ── analytics: who is watching, PERMANENTLY recorded (anchor.visitors) ───
    _TOUCHED: dict = {}                           # visitor hash → last DB write ts
    _CHAT_LAST: dict = {}                         # visitor hash → last chat ts

    def _visitor(self) -> str:
        """Identify the visitor (hash of ip+ua, nothing stored beyond that) and
        record presence in the permanent anchor — throttled to one DB write per
        30s per visitor so 1-second pollers don't hammer SQLite."""
        import hashlib
        ip = (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
              or self.client_address[0])
        ua = self.headers.get("User-Agent", "")
        h = hashlib.sha1(f"{ip}|{ua}".encode()).hexdigest()[:12]
        now = time.time()
        if now - Handler._TOUCHED.get(h, 0) > 30:
            Handler._TOUCHED[h] = now
            try:
                if anchor.visitor_touch(h):
                    anchor.metric_bump("visitors")   # daily series, from the same truth
            except Exception:
                pass
        return h

    def _count_view(self):
        self._visitor()
        anchor.metric_bump("pageviews")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._count_view()
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/agents":
            self._count_view()
            return self._send(200, AGENTS_PAGE, "text/html; charset=utf-8")
        if self.path == "/chats":
            self._count_view()
            return self._send(200, CHATS_PAGE, "text/html; charset=utf-8")
        if self.path == "/rules":
            self._count_view()
            return self._send(200, RULES_PAGE, "text/html; charset=utf-8")
        if self.path == "/logs":
            self._count_view()
            return self._send(200, LOGS_PAGE, "text/html; charset=utf-8")
        if self.path == "/skills":
            self._count_view()
            return self._send(200, SKILLS_PAGE, "text/html; charset=utf-8")
        if self.path == "/leaderboard":
            self._count_view()
            return self._send(200, LEADERBOARD_PAGE, "text/html; charset=utf-8")
        if self.path == "/providers":
            self._count_view()
            return self._send(200, PROVIDERS_PAGE, "text/html; charset=utf-8")
        if self.path == "/work":
            self._count_view()
            return self._send(200, WORK_PAGE, "text/html; charset=utf-8")
        if self.path == "/api/workdata":
            import workspace as WS
            WS.init()
            wtasks = WS.sync_tasks()              # sync first so counts are current
            return self._send(200, json.dumps({
                "world": WS.world(),
                "tasks": wtasks,
                "module": WS.read_file("calculator.py"),
                "brain": brain.brain_name(),
                "recent": [e for e in anchor.event_log(200)
                           if "[work]" in e or "[waste]" in e][:12],
            }))
        if self.path == "/api/evaldata":
            return self._send(200, json.dumps({
                "runs": anchor.eval_runs(50),
                "model_calls": anchor.model_calls_stats(),
                "brain": brain.brain_name(),
            }))
        if self.path == "/api/providers":
            runs = anchor.eval_runs(50)
            return self._send(200, json.dumps({
                "roles": MODELS.assignments(),
                "role_names": list(MODELS.ROLES),
                "registry": MODELS.catalog(),        # never the keys
                "tier": MODELS.tier(),
                "routed": MODELS.active(),
                "prices": MODELS.prices(),
                "budget": MODELS.budget_state(),
                "seats": MODELS.seat_status(),
                "fallbacks": MODELS.fallbacks(),
                "by_model": anchor.model_calls_by_model(),
                "by_decision": anchor.decisions_by_model(),
                "totals": anchor.model_calls_stats(),
                "runs": runs,
                "brain": brain.brain_name(),
            }))
        if self.path == "/api/skillsdata":
            return self._send(200, json.dumps({
                "skills": anchor.skills_top(50),
                "skills_count": anchor.skills_count(),
                "reasons": anchor.reasons_top(120),
                "reasons_count": anchor.reasons_count(),
                "tiers": [{"name": x["name"], "budget": x["budget"],
                           "promote_at": x["promote_at"], "can": list(x["can"])}
                          for x in economy.TIERS],
                "brain": brain.brain_name(),
            }))
        if self.path.startswith("/api/lineage"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            try:
                did = int(q.get("id", ["0"])[0])
            except ValueError:
                did = 0
            return self._send(200, json.dumps(anchor.lineage(did)))
        if self.path.startswith("/api/careers/export"):
            # The Hall of Records as a file: every agent's full life, oldest first.
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            fmt = (q.get("format", ["csv"])[0] or "csv").lower()
            rows = anchor.careers_rows()

            def _iso(ts):
                return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) if ts else ""
            if fmt == "jsonl":
                body = "\n".join(json.dumps({"gen": g, "uid": u, "turn": t, "ts": ts,
                                             "event": ev, "detail": de})
                                 for g, u, t, ts, ev, de in rows) + ("\n" if rows else "")
                mime = "application/x-ndjson"
            else:
                import csv as _csv
                import io as _io
                buf = _io.StringIO()
                wcsv = _csv.writer(buf)
                wcsv.writerow(["gen", "uid", "turn", "utc_time", "event", "detail"])
                for g, u, t, ts, ev, de in rows:
                    wcsv.writerow([g, u, t, _iso(ts), ev, de])
                body, mime, fmt = buf.getvalue(), "text/csv", "csv"
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            return self._send(200, body, mime, download=f"phoenix-careers-{stamp}.{fmt}")
        if self.path.startswith("/api/health/export"):
            # Per-agent (or full) health telemetry — the permanent 5-second vitals log.
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            uid = (q.get("uid", [""])[0] or "").strip()
            gen_s = (q.get("gen", [""])[0] or "").strip()
            gen = int(gen_s) if gen_s.isdigit() else None
            fmt = (q.get("format", ["csv"])[0] or "csv").lower()
            rows = anchor.health_rows(gen, uid)

            def _iso(ts):
                return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) if ts else ""
            if fmt == "jsonl":
                body = "\n".join(json.dumps({"gen": g, "uid": u, "turn": t, "ts": ts,
                                             "health": h, "utilisation": ut, "tokens": tk,
                                             "contribution": ct, "status": st})
                                 for g, u, t, ts, h, ut, tk, ct, st in rows) + ("\n" if rows else "")
                mime = "application/x-ndjson"
            else:
                import csv as _csv
                import io as _io
                buf = _io.StringIO()
                wcsv = _csv.writer(buf)
                wcsv.writerow(["gen", "uid", "turn", "utc_time", "health_pct",
                               "utilisation_pct", "tokens", "contribution", "status"])
                for g, u, t, ts, h, ut, tk, ct, st in rows:
                    wcsv.writerow([g, u, t, _iso(ts), h, ut, tk, ct, st])
                body, mime, fmt = buf.getvalue(), "text/csv", "csv"
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            name = f"phoenix-health-{uid or 'all'}-{stamp}.{fmt}"
            return self._send(200, body, mime, download=name)
        if self.path == "/api/careers":
            ev_total, agents_ever = anchor.careers_count()
            hs = anchor.health_summaries()
            careers = anchor.careers(40)
            for cr in careers:                    # vitals digest joins the career record
                cr["health"] = hs.get((cr["gen"], cr["uid"]))
            return self._send(200, json.dumps({
                "careers": careers,
                "events_total": ev_total,
                "agents_ever": agents_ever,
                "generation": anchor.generation(),
            }))
        if self.path.startswith("/api/logs/export"):
            # Export the PERMANENT log as a downloadable file, filtered by kind,
            # free text, and a UTC time window. Formats: txt (as displayed),
            # jsonl (raw permanent records), csv.
            from urllib.parse import urlparse, parse_qs
            from datetime import datetime, timezone
            q = parse_qs(urlparse(self.path).query)
            kind_f = (q.get("kind", [""])[0] or "").strip()
            text_f = (q.get("q", [""])[0] or "").strip().lower()
            fmt = (q.get("format", ["txt"])[0] or "txt").lower()
            if fmt not in ("txt", "jsonl", "csv"):
                fmt = "txt"

            def _parse_ts(key):
                v = (q.get(key, [""])[0] or "").strip()
                if not v:
                    return None
                try:      # datetime-local value, interpreted as UTC
                    return datetime.fromisoformat(v).replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    return None
            since, until = _parse_ts("since"), _parse_ts("until")

            rows = []
            try:
                with open(anchor.EVENTS_PATH) as f:
                    for ln in f:
                        try:
                            d = json.loads(ln)
                        except json.JSONDecodeError:
                            continue
                        if kind_f and d.get("kind") != kind_f:
                            continue
                        if text_f and text_f not in \
                                f"[{d.get('kind', '')}] {d.get('note', '')}".lower():
                            continue
                        ts = d.get("ts")
                        if since is not None and (ts is None or ts < since):
                            continue
                        if until is not None and (ts is None or ts > until):
                            continue
                        rows.append(d)
            except OSError:
                pass

            def _stamp(d):
                return (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(d["ts"]))
                        if d.get("ts") else "")
            if fmt == "jsonl":
                body = "\n".join(json.dumps(d) for d in rows) + ("\n" if rows else "")
                mime = "application/x-ndjson"
            elif fmt == "csv":
                import csv as _csv
                import io as _io
                buf = _io.StringIO()
                wcsv = _csv.writer(buf)
                wcsv.writerow(["utc_time", "turn", "kind", "note"])
                for d in rows:
                    wcsv.writerow([_stamp(d), d.get("turn", ""), d.get("kind", ""),
                                   d.get("note", "")])
                body, mime = buf.getvalue(), "text/csv"
            else:
                body = "\n".join(f"{_stamp(d)} t{d.get('turn', '?')} [{d.get('kind', '')}] "
                                 f"{d.get('note', '')}" for d in rows) + ("\n" if rows else "")
                mime = "text/plain; charset=utf-8"
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            return self._send(200, body, mime,
                              download=f"phoenix-logs-{stamp}.{fmt}")
        if self.path.startswith("/api/logs"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = min(10000, max(50, int(q.get("limit", ["1000"])[0])))
            except ValueError:
                limit = 1000
            return self._send(200, json.dumps({
                "events": anchor.event_log(limit),
                "total": anchor.event_count(),
            }))
        if self.path == "/api/stats":
            self._visitor()
            vs = anchor.visitor_stats()           # from the permanent record
            m = anchor.metrics_summary()
            _ev, agents_ever = anchor.careers_count()
            return self._send(200, json.dumps({
                "online_now": vs["online_now"],
                "visitors_today": vs["today"],
                "visitors_total": vs["total"],
                "pageviews_today": m.get("pageviews", {}).get("today", 0),
                "pageviews_total": m.get("pageviews", {}).get("total", 0),
                "public_chats_total": m.get("public_chats", {}).get("total", 0),
                "agents_alive": len(_S["villagers"]),
                "agents_ever": agents_ever,
                "turn": _S["turn"],
                "generation": anchor.generation(),
                "brain": brain.brain_name(),
                "model_calls": anchor.model_calls_stats()["calls"],
            }))
        if self.path == "/api/state":
            self._visitor()
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/chats":
            with _LOCK:
                return self._send(200, json.dumps(_chats_snapshot()))
        if self.path == "/api/rules":
            return self._send(200, json.dumps(_rules_data()))
        if self.path == "/api/braincheck":
            # Diagnostic: one raw model call, error surfaced verbatim (not swallowed).
            if not brain.available():
                return self._send(200, json.dumps({"ok": False, "why": "no DEEPSEEK_API_KEY in env"}))
            try:
                client = brain._deepseek_client()
                out = client.chat.completions.create(
                    model=brain._model(),
                    messages=[{"role": "user", "content": "Reply with the single word: alive"}],
                    max_tokens=200,
                ).choices[0].message.content.strip()
                return self._send(200, json.dumps({"ok": True, "model": brain._model(), "reply": out}))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "model": brain._model(),
                                                   "error": str(e)[:600]}))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        # Tiered auth. When CONSOLE_TOKEN is set: spectating is free, POWER needs the
        # token — and with PUBLIC_CHAT=1, /api/chat stays open (rate-limited) so the
        # public can talk to the agents but never command the world. Controlled chaos:
        # visitors may try to talk the fleet into breaking its constitution; the
        # refusals are the show, and every message lands in the permanent log.
        tok = os.environ.get("CONSOLE_TOKEN", "")
        if tok and self.headers.get("X-Console-Token", "") != tok:
            if self.path == "/api/chat" and os.environ.get("PUBLIC_CHAT", "") == "1":
                h = self._visitor()
                now = time.time()
                if now - Handler._CHAT_LAST.get(h, 0) < 8:
                    return self._send(429, json.dumps(
                        {"error": "easy — one message every 8 seconds"}))
                Handler._CHAT_LAST[h] = now
                anchor.metric_bump("public_chats")
            else:
                return self._send(401, json.dumps({"error": "console token required"}))
        if self.path == "/api/resume":
            body = self._read_json()
            uid, decision = body.get("unit_id"), body.get("decision")
            if not uid or decision not in ("approve", "reject", "dismiss", "back-to-work"):
                return self._send(400, json.dumps({"error": "unit_id and a valid decision required"}))
            age_before = sim.world()["age"]
            with _LOCK:
                sim.resume(_GRAPH, uid, decision)
            anchor.career_add(uid, _S["turn"], "gate",
                              f"human decided: {decision} at the irreversible gate")
            if (uid.startswith("herald") and decision == "approve"
                    and sim.world()["age"] == age_before):
                # The approval could not be executed — say so where the human is,
                # never let a click silently do nothing (IV.6).
                w2, cost2 = sim.world(), sim.advance_cost()
                short = ", ".join(f"{r} short {max(0, cost2[r] - w2[r]):,}"
                                  for r in cost2 if w2[r] < cost2[r]) or "unknown"
                anchor.record(_S["turn"], "gate",
                              f"your APPROVAL was received but the treasury cannot pay: {short} "
                              f"(need food {cost2['food']:,} + gold {cost2['gold']:,})")
                anchor.msg_send("chief", "Chief Governor",
                                f"Your approval arrived, but the treasury is short: {short}. "
                                f"The fleet is accumulating — a new herald will return to your "
                                f"gate the moment we can pay.")
            if uid.startswith("herald"):
                _S.pop("gate_since", None)        # the wait is over; stop pricing it
                _S.pop("gate_since_ts", None)
                _S["notified"] = {k for k in _S["notified"] if not k.startswith(("gate:", "gate2:"))}
            if uid.startswith("herald") and _S.get("herald_decision"):
                anchor.decision_close(_S["herald_decision"],
                                      outcome=f"human decision: {decision} — age now {sim.world()['age']}")
                _S["herald_decision"] = None
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/vision":
            key = self._read_json().get("vision")
            if key not in V.VISIONS:
                return self._send(400, json.dumps({"error": "unknown vision"}))
            with _LOCK:
                _adopt_vision(key)               # only the human adopts (Constitution I)
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/spawn":
            with _LOCK:
                ok, msg = _op_add(self._read_json().get("resource", ""))
            return self._send(200 if ok else 409, json.dumps({"ok": ok, "detail": msg, **_snapshot()}))
        if self.path == "/api/terminate":
            uid = self._read_json().get("unit_id")
            if not uid:
                return self._send(400, json.dumps({"error": "unit_id required"}))
            with _LOCK:
                _op_terminate(uid)
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/order":
            body = self._read_json()
            with _LOCK:
                ok = _op_order(body.get("unit_id", ""), body.get("resource", ""))
            return self._send(200 if ok else 400, json.dumps(_snapshot()))
        if self.path == "/api/cap":
            with _LOCK:
                ok = _op_cap(self._read_json().get("token_cap"))
            return self._send(200 if ok else 400, json.dumps(_snapshot()))
        if self.path == "/api/reset":
            # Operator-ordered WORLD RESET: wipe the game world and economy, then exit —
            # the platform restarts us fresh. The anchor's memory (skills, reasoning,
            # chats, knowledge, learned yields) lives in its own DB and the permanent
            # event log is append-only: neither is EVER deleted. New worlds start with
            # everything past generations learned.
            anchor.record(_S["turn"], "operator",
                          "WORLD RESET ordered — wiping the game world and restarting "
                          "(memory and event log retained)")
            def _wipe_and_restart():
                time.sleep(0.7)                   # let the response flush first
                for p in (sim.DB, sim.DB + "-wal", sim.DB + "-shm"):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                os._exit(1)                        # supervisor restarts a clean process
            threading.Thread(target=_wipe_and_restart, daemon=True).start()
            return self._send(200, json.dumps({"ok": True, "restarting": True}))
        if self.path == "/api/work":
            # one worker cycle against the live brain — the model call and the test
            # subprocess run in-request, outside the game lock
            import worker as WK
            import workspace as WS
            WS.init()
            agent = (self._read_json().get("agent") or "dev-01").strip()[:24] or "dev-01"
            if not brain.available():
                return self._send(400, json.dumps(
                    {"error": "no brain configured — the worker needs a model"}))
            try:
                result = WK.work_cycle(agent)
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)[:300]}))
            return self._send(200, json.dumps(result))
        if self.path == "/api/workreset":
            import workspace as WS
            WS.init()
            restored = WS.reset_sandbox()
            anchor.record(_S["turn"], "operator",
                          f"sandbox reset — {', '.join(restored)} restored; training tasks reopen")
            return self._send(200, json.dumps({"ok": True, "restored": restored}))
        if self.path == "/api/chat":
            body = self._read_json()
            thread, text = body.get("thread", ""), (body.get("body") or "").strip()
            if not thread or not text:
                return self._send(400, json.dumps({"error": "thread and body required"}))
            _chat_send(thread, text)              # takes the lock only around actions,
            return self._send(200, json.dumps(_chats_snapshot()))  # not the model call
        if self.path == "/api/development":
            action = self._read_json().get("action")
            ok, msg = False, ""
            with _LOCK:                       # NOTE: _snapshot() takes _LOCK — call it outside
                prop = _S["dev_proposal"]
                if not prop:
                    return self._send(400, json.dumps({"error": "no pending proposal"}))
                if action == "adopt":
                    ok, msg = sim.dev_add(prop["name"], prop.get("cost", {}), prop.get("kind", ""),
                                          prop.get("value", 0), prop.get("resource", ""),
                                          prop.get("rank", 2), prop.get("source", ""))
                    dev_ev = anchor.record(_S["turn"], "development",
                                           f"human adopted '{prop['name']}'" if ok else f"adopt failed: {msg}")
                    if prop.get("did"):
                        anchor.decision_close(prop["did"], dev_ev,
                                              outcome="human adopted" if ok else f"adopt failed: {msg}")
                    _S["dev_proposal"] = None
                elif action == "reject":
                    ok, msg = True, f"rejected {prop['name']}"
                    dev_ev = anchor.record(_S["turn"], "development", f"human rejected '{prop['name']}'")
                    if prop.get("did"):
                        anchor.decision_close(prop["did"], dev_ev, outcome="human rejected")
                    _S["dev_proposal"] = None
                else:
                    return self._send(400, json.dumps({"error": "action must be adopt|reject"}))
            return self._send(200 if ok else 400, json.dumps({"ok": ok, "detail": msg, **_snapshot()}))
        if self.path == "/api/constitution":
            text = (self._read_json().get("text") or "").strip()
            if not text:
                return self._send(400, json.dumps({"error": "text required"}))
            with _LOCK:
                anchor.config_set("constitution", text)
                anchor.record(_S["turn"], "constitution", "the human amended the constitution")
                anchor.msg_send("internal", "Chief Governor",
                                "The constitution was amended by the human — re-briefing the fleet.")
            return self._send(200, json.dumps({"ok": True, **_rules_data()}))
        if self.path == "/api/amend":
            note = (self._read_json().get("note") or "").strip()
            with _LOCK:
                draft = brain.think("the Chief Governor", _situation(),
                                    f"Draft one new constitution article (title + one rule) about: {note or 'improving governance'}.")
            if not draft:
                draft = ("## Proposed article\n\n- (No model configured — set DEEPSEEK_API_KEY to have the "
                         "Chief Governor draft amendments. Meanwhile, edit the text directly.)")
            return self._send(200, json.dumps({"draft": draft}))
        if self.path == "/api/ingest":
            body = self._read_json()
            topic = (body.get("topic") or "").strip()
            if not topic:
                return self._send(400, json.dumps({"error": "topic required"}))
            fact = brain.research(topic) or (body.get("fact") or "").strip()   # model call: no lock
            source = "deepseek" if (brain.available() and fact) else (body.get("source") or "operator")
            if not fact:
                return self._send(400, json.dumps({"error": "no model configured; provide a fact"}))
            anchor.ingest(topic, source, fact)       # Article VI: source recorded, never executed
            anchor.record(_S["turn"], "ingest", f"external knowledge on '{topic}' from {source}")
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/models":
            # Article X.1 — assigning a model to a role is a HUMAN-ONLY power. This
            # endpoint is behind the operator token (above); nothing in the fleet, the
            # board or the tacit-consent clock can reach it. Who thinks for the
            # organization is nearer to adopting a Vision than to an operational choice.
            body = self._read_json()
            role, spec = (body.get("role") or "").strip(), (body.get("model") or "").strip()
            ok, msg = MODELS.assign(role, spec)
            if not ok:
                return self._send(400, json.dumps({"error": msg}))
            anchor.record(_S["turn"], "models", f"the human assigned {msg}")
            anchor.reason_add(_S["turn"], "human", f"assign model: {msg}",
                              "who thinks for the organization is a human-only power "
                              "(Article X.1); the board may propose a swap, never make one",
                              authorized_by="human")
            return self._send(200, json.dumps({"ok": True, "roles": MODELS.assignments(),
                                               "tier": MODELS.tier()}))
        self._send(404, json.dumps({"error": "not found"}))


PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>The Governor — Age of Empires</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;
--food:#e05a5a;--wood:#b5793a;--gold:#e0b23a;--wait:#f59e0b;--idle:#ef4444;--done:#22c55e;--run:#3b82f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:14px 20px;border-bottom:2px solid var(--line);background:linear-gradient(180deg,#241a0f,#1c150d)}
.top{display:flex;gap:20px;align-items:center;flex-wrap:wrap}
h1{font-size:15px;margin:0;letter-spacing:1px;white-space:nowrap}
.vision{flex:1;min-width:280px}
.vname{color:var(--gold);font-size:12px}
.pbar{height:16px;background:#0e0a05;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-top:3px}
.pfill{height:100%;background:linear-gradient(90deg,#8b5a2b,#22c55e);transition:width .4s;text-align:right}
.vmeta{display:flex;gap:16px;font-size:12px;color:var(--dim);flex-wrap:wrap}
.ops{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--dim)}
.ops select,.ops input{background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 8px;font:inherit}
.navlink{color:var(--gold);text-decoration:none;border:1px solid var(--gold);padding:3px 10px;border-radius:20px}
.age{font-size:12px;color:var(--gold);border:1px solid var(--gold);padding:2px 10px;border-radius:20px;white-space:nowrap}
.res{display:flex;gap:14px;flex-wrap:wrap}.r b{font-weight:700}.r.food b{color:var(--food)}.r.wood b{color:var(--wood)}.r.gold b{color:var(--gold)}
main{padding:16px;display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));max-width:1200px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;max-height:340px;overflow:auto}
.card h2{position:sticky;top:0;background:var(--panel);z-index:2}
.wide{grid-column:1/-1;max-height:420px}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{padding:7px 14px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;white-space:normal;overflow-wrap:normal;word-break:keep-all}
th{color:var(--dim);font-weight:600;font-size:10px;text-transform:uppercase;position:sticky;top:36px;background:var(--panel);white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{white-space:nowrap}
.pill{display:inline-flex;gap:6px;align-items:center;padding:1px 8px;border-radius:20px;font-size:11px}
.dot{width:7px;height:7px;border-radius:50%}
.running{color:var(--run)}.running .dot{background:var(--run)}
.awaiting_approval{color:var(--wait)}.awaiting_approval .dot{background:var(--wait)}
.idle{color:var(--idle)}.idle .dot{background:var(--idle)}.done{color:var(--done)}.done .dot{background:var(--done)}
button{font:inherit;padding:4px 11px;border-radius:6px;border:1px solid var(--line);cursor:pointer;background:#26200f;color:var(--ink)}
button.ok{border-color:#3a5a1a;background:#1a2a0f;color:#a8e086}button.no{border-color:#5b2a1a;background:#2a140f;color:#fca5a5}
.gate td{background:#2a1c05}.empty{padding:14px;color:var(--dim)}
.tech{display:flex;flex-wrap:wrap;gap:8px;padding:12px 14px}
.chip{padding:4px 10px;border-radius:20px;border:1px solid var(--line);font-size:12px;color:var(--dim)}
.chip.on{border-color:#3a5a1a;background:#14240c;color:#a8e086}
.chip button{margin-left:8px;padding:2px 9px;font-size:11px}
.propose{padding:12px 14px;background:#241a05;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.propose .why{color:var(--dim);font-size:12px}.propose-none{padding:12px 14px;color:var(--dim);font-size:12px}
.log{margin:0;padding:10px 14px;font-size:12px;line-height:1.7}
.log div{color:var(--dim)}.log .k-build,.log .k-goal{color:#a8e086}.log .k-gate,.log .k-approve{color:var(--gold)}
.log .k-reap{color:#fca5a5}.log .k-vision{color:#e0b23a}.log .k-spawn,.log .k-retask{color:var(--ink)}
.log .k-board{color:#8ab4ff}.log .k-promote{color:#a8e086}.log .k-cap,.log .k-waste,.log .k-error{color:#fca5a5}
.log .k-gather{color:#c9b98f}.log .k-skill{color:#e0b23a;font-weight:700}.log .k-governor,.log .k-operator{color:#e0b23a}.log .k-message,.log .k-ingest,.log .k-vision-change,.log .k-rebrief{color:#8ab4ff}
.kv{padding:10px 14px}.kv div{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:4px 0}
.kv b{color:var(--gold)}.foot{color:var(--dim);font-size:11px;text-align:center;padding-bottom:14px}
.badge{padding:1px 9px;border-radius:20px;font-size:11px;border:1px solid var(--line)}
.badge.t0{color:var(--dim)}.badge.t1{color:#8ab4ff;border-color:#2a3a5a}.badge.t2{color:var(--gold);border-color:#5a4a1a;background:#241a05}
.mini{display:inline-block;width:70px;height:7px;background:#0e0a05;border:1px solid var(--line);border-radius:5px;overflow:hidden;vertical-align:middle;margin-right:6px}
.minifill{height:100%;background:linear-gradient(90deg,#8b5a2b,#22c55e)}
</style>
<header>
  <div class=top>
    <h1>&#9670; THE GOVERNOR</h1>
    <div class=vision>
      <div class=vname id=vname>&mdash;</div>
      <div class=pbar><div class=pfill id=pfill></div></div>
    </div>
    <span class=age id=age>&mdash;</span>
    <div class="res" id=res></div>
  </div>
  <div class=vmeta id=vmeta></div>
  <div class=ops>
    <a class=navlink href="/agents">Agent Health &rarr;</a>
    <a class=navlink href="/chats">Chats &rarr;</a>
    <a class=navlink href="/rules">Rules &rarr;</a>
    <a class=navlink href="/logs">Logs &rarr;</a>
    <a class=navlink href="/skills">Skills &rarr;</a>
    <a class=navlink href="/providers">Providers &rarr;</a>
    <span>Add villager</span>
    <select id=addres><option value="">auto</option><option>food</option><option>wood</option><option>gold</option></select>
    <button class=ok onclick=addAgent()>Add</button>
    <span>Cap</span><input id=capin type=number step=10000 style="width:110px">
    <button onclick=setCap()>Set</button>
    <span id=capprop class=why></span>
    <span>Learn</span><input id=topicin placeholder="topic" style="width:130px">
    <button onclick=ingest()>Ingest</button>
    <span id=brainmode class=navlink style="border-color:var(--line)"></span>
    <span id=pubstats class=navlink style="border-color:var(--line);color:var(--dim)"></span>
  </div>
</header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<main>
  <div class="card wide"><h2>Fleet</h2>
    <table><thead><tr><th>Unit</th><th>Task</th><th>State</th><th class=num>Rounds</th><th class=num>Compute</th></tr></thead>
      <tbody id=fleet></tbody></table></div>
  <div class="card wide"><h2>Command queue &mdash; you gate the irreversible Age-up</h2>
    <table><tbody id=queue></tbody></table><div class=empty id=queueEmpty>Nothing awaiting a human.</div></div>
  <div class=card><h2>Development &mdash; ranked tree (<span id=devcount>0</span> built)</h2>
    <div id=devprop></div><div class=tech id=tech></div></div>
  <div class=card><h2>Knowledge (the anchor)</h2><div class=kv id=know></div></div>
  <div class=card><h2>Balance sheet &mdash; assets &amp; liabilities</h2><div class=kv id=balance></div></div>
  <div class="card wide"><h2>Roster &mdash; status earned by measured contribution</h2>
    <table><thead><tr><th>Agent</th><th>Role</th><th class=num>Contribution</th><th>Progress to promotion</th><th class=num>Budget</th></tr></thead>
      <tbody id=roster></tbody></table></div>
  <div class=card><h2>Board of Governors &mdash; quorum for agent creation</h2><div class=kv id=boardp></div></div>
  <div class="card wide"><h2>Strategy &mdash; the Board proposes, you adopt the Vision</h2>
    <div id=proposal></div>
    <div class=tech id=visions></div></div>
  <div class="card wide"><h2>Event log &mdash; permanent (<span id=evcount>0</span> total)
    &nbsp;<select id=logfilter onchange=renderLog() style="background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:2px 6px;font:inherit"><option value="">all kinds</option></select></h2>
    <div class=log id=log></div></div>
  <p class=foot>director auto-plays &middot; live over the checkpointer + World + anchor &middot; the game state is the oracle</p>
</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function tick(){
  let d; try{ d=await (await fetch('/api/state')).json() }catch(e){ return }
  const v=d.vision;
  vname.textContent=v.name+(v.goal_met?'  ✓ MET':'');
  pfill.style.width=v.progress+'%'; pfill.textContent=v.progress>8?v.progress+'% ':'';
  vmeta.innerHTML=`<span>turn ${d.turn}</span>
    <span>age ${v.age_pct}% &middot; dev ${v.dev_pct}% &middot; econ ${v.econ_pct}%</span>
    <span>side-effects ${v.side_effects}/${v.side_effect_budget} ${v.within_budget?'&#10003;':'&#9888;'}</span>
    <span>extra value +${v.extra_value_pct}%</span>
    <span>compute ${d.spent.toLocaleString()}/${d.cap.toLocaleString()}</span>`;
  age.textContent='Age: '+d.world.age;
  res.innerHTML=`<span class="r food">FOOD <b>${d.world.food}</b></span>
    <span class="r wood">WOOD <b>${d.world.wood}</b></span>
    <span class="r gold">GOLD <b>${d.world.gold}</b></span>`;
  fleet.innerHTML=d.units.map(u=>`<tr><td>${esc(u.unit_id)}</td><td>${esc(u.task)}</td>
    <td><span class="pill ${u.status}"><span class=dot></span>${u.status}</span></td>
    <td class=num>${u.steps}</td><td class=num>${u.tokens.toLocaleString()}</td></tr>`).join('')
    || '<tr><td class=empty colspan=5>no active agents</td></tr>';
  const pend=d.units.filter(u=>u.pending);
  queueEmpty.style.display=pend.length?'none':'block';
  queue.innerHTML=pend.map(u=>{const irr=u.pending&&u.pending.reversible===false;
    return `<tr class="${irr?'gate':''}"><td>${esc(u.unit_id)}</td>
      <td>${irr?'&#9888; ':''}${esc((u.pending&&u.pending.action)||u.task)}</td>
      <td style=text-align:right>
        <button class=ok onclick="decide('${esc(u.unit_id)}','${irr?'approve':'back-to-work'}')">${irr?'Approve':'Re-task'}</button>
        <button class=no onclick="decide('${esc(u.unit_id)}','${irr?'reject':'dismiss'}')">${irr?'Reject':'Dismiss'}</button>
      </td></tr>`}).join('');
  devcount.textContent=d.dev_total_built||0;
  const ranks={};(d.dev_catalog||[]).forEach(x=>{(ranks[x.rank]=ranks[x.rank]||[]).push(x)});
  const roman=['','I','II','III','IV'];
  tech.innerHTML=Object.keys(ranks).sort().map(r=>
    `<div style="width:100%;color:var(--dim);font-size:10px;margin:4px 0 0">RANK ${roman[r]||r}</div>`+
    ranks[r].map(x=>`<span class="chip ${x.built?'on':''}" title="${esc(x.effect)} — cost ${esc(JSON.stringify(x.cost))}">`+
      `${esc(x.name)}${x.custom?' ✦':''}${x.built>1?' ×'+x.built:''}${x.built?' ✓':''}</span>`).join('')).join('');
  const dp=d.dev_proposal;
  devprop.innerHTML=dp?`<div class=propose><b>Governor proposes:</b> ${esc(dp.name)} — ${esc(dp.why||'')}
      <span class=why>[board ${esc(dp.board||'')}, source ${esc(dp.source||'')}]</span>
      <button class=ok onclick="devAct('adopt')">Adopt</button>
      <button class=no onclick="devAct('reject')">Reject</button></div>`:'';
  const kn=d.knowledge;
  know.innerHTML=`<div><span>facts learned</span><b>${kn.facts}</b></div>
    <div><span>best resource</span><b>${kn.best_resource||'&mdash;'}</b></div>
    ${Object.entries(kn.learned_yields||{}).map(([r,y])=>`<div><span>avg ${r} yield</span><b>${y}</b></div>`).join('')}
    <div><span>external facts (${esc(d.brain)})</span><b>${d.external_count||0}</b></div>
    ${(d.external||[]).slice(0,3).map(x=>`<div><span>${esc(x.topic)} <i>[${esc(x.source)}]</i></span><b title="${esc(x.fact)}">&#9432;</b></div>`).join('')}
    <div><span><b style=color:var(--gold)>skills (wisdom)</b></span><b>${d.skills_count||0}</b></div>
    ${(d.skills||[]).slice(0,3).map(x=>`<div><span style="white-space:normal">&#x1F4D6; ${esc(x.lesson)}</span></div>`).join('')}`;
  const bs=d.balance||{};
  balance.innerHTML=`
    <div><span>current assets (stock)</span><b>${(bs.current_total||0).toLocaleString()}</b></div>
    ${Object.entries(bs.current_assets||{}).map(([r,v])=>`<div><span>&nbsp;&nbsp;${r}${r==='food'&&bs.food_cap?` (cap ${bs.food_cap.toLocaleString()}, spoils above)`:''}</span><b>${v.toLocaleString()}</b></div>`).join('')}
    <div><span>fixed assets (at build cost)</span><b>${(bs.fixed_assets||0).toLocaleString()}</b></div>
    ${Object.entries(bs.conditions||{}).map(([n,c])=>`<div><span>&nbsp;&nbsp;${esc(n)} condition</span><b style="color:${c>70?'var(--ok,#a8e086)':c>40?'#e0b23a':'#fca5a5'}">${c}%</b></div>`).join('')}
    <div><span>disrepair liability</span><b style="color:#fca5a5">-${(bs.disrepair_liability||0).toLocaleString()}</b></div>
    <div><span>intangibles</span><b>${(bs.intangibles||{}).lessons||0} lessons &middot; ${(bs.intangibles||{}).external_facts||0} facts</b></div>
    <div><span>outstanding compute</span><b>${(bs.outstanding_compute||0).toLocaleString()}</b></div>
    ${bs.waiting_on_human?`<div><span style="color:var(--gold)">&#9888; frozen behind your gate</span><b style="color:var(--gold)">${esc(bs.waiting_on_human)}</b></div>`:''}
    <div><span><b>NET WORTH</b></span><b>${(bs.net_worth||0).toLocaleString()}</b></div>`;
  brainmode.textContent='brain: '+d.brain; d_brain_rule=(d.brain==='rule-based');
  roster.innerHTML=(d.roster||[]).map(a=>{const pct=a.next_at?Math.min(100,Math.round(100*a.contribution/a.next_at)):100;
    return `<tr><td>${esc(a.agent)}</td><td><span class="badge t${a.tier}">${esc(a.role)}</span></td>
      <td class=num>${a.contribution.toLocaleString()}</td>
      <td><span class=mini><span class=minifill style="width:${pct}%"></span></span>${a.next_at?pct+'%':'max tier'}</td>
      <td class=num>${a.budget.toLocaleString()}</td></tr>`}).join('')
    || '<tr><td class=empty colspan=5>no agents enlisted yet</td></tr>';
  const bd=d.board, lv=bd.last;
  boardp.innerHTML=`<div><span>governors</span><b>${bd.governors.join(', ')}</b></div>
    <div><span>quorum</span><b>${bd.quorum} of ${bd.governors.length}</b></div>`
    +(lv?`<div><span>last: ${esc(lv.proposal)}</span><b>${lv.approved?'APPROVED':'BLOCKED'} ${lv.tally}</b></div>
      <div><span>ballots</span><b>${Object.entries(lv.ballots).map(([g,v])=>g+(v?' ✓':' ✗')).join('  ')}</b></div>`:'');
  const st=d.strategy;
  proposal.innerHTML=st.proposal
    ? `<div class=propose><b>Board proposal:</b> ${esc(st.proposal.action)} &rarr; <b>${esc(st.proposal.name)}</b>
        <span class=why>${esc(st.proposal.why)}</span>
        <button class=ok onclick="adopt('${esc(st.proposal.vision)}')">Adopt</button></div>`
    : `<div class=propose-none>Board holding &mdash; current vision on track. You can still re-point it below.</div>`;
  visions.innerHTML=st.options.map(o=>`<span class="chip ${o.key===st.current_key?'on':''}">${esc(o.name)}`
    +(o.key===st.current_key?' &check;':`<button onclick="adopt('${o.key}')">Adopt</button>`)+`</span>`).join('');
  window._events=d.events; renderLog();
  evcount.textContent=(d.event_count||0).toLocaleString();
  if(document.activeElement!==capin) capin.value=d.cap;
  const cp=d.cap_proposal;
  capprop.innerHTML=cp?`Board: ${esc(cp.action)} to <b>${cp.cap.toLocaleString()}</b> <button onclick="applyCap(${cp.cap})">Apply</button>`:'';
}
function renderLog(){
  const evs=window._events||[];
  const kinds=[...new Set(evs.map(e=>{const m=e.match(/\\[([\\w-]+)\\]/);return m?m[1]:''}).filter(Boolean))].sort();
  if(kinds.join()!==window._kinds){window._kinds=kinds.join();const cur=logfilter.value;
    logfilter.innerHTML='<option value="">all kinds</option>'+kinds.map(k=>`<option ${k===cur?'selected':''}>${k}</option>`).join('');}
  const f=logfilter.value;
  log.innerHTML=evs.filter(e=>!f||e.indexOf('['+f+']')>=0).map(e=>{
    const m=e.match(/\\[([\\w-]+)\\]/);const k=m?m[1]:'';return `<div class="k-${k}">${esc(e)}</div>`}).join('');
}
async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify(body||{})});
  if(r.status===401){const t=prompt('Console token required:');if(t){localStorage.setItem('ctok',t);return post(url,body)}}
  tick();}
async function loadStats(){
  try{const s=await (await fetch('/api/stats')).json();
    pubstats.innerHTML=`&#128101; ${s.online_now} watching &middot; ${s.visitors_today} today &middot; `+
      `${s.visitors_total.toLocaleString()} all-time &middot; ${s.agents_alive} agents alive &middot; ${s.agents_ever.toLocaleString()} ever`;
  }catch(e){}}
loadStats(); setInterval(loadStats,10000);
function decide(unit_id,decision){post('/api/resume',{unit_id,decision});}
function adopt(vision){post('/api/vision',{vision});}
function addAgent(){post('/api/spawn',{resource:addres.value});}
function setCap(){const v=parseInt(capin.value);if(v)post('/api/cap',{token_cap:v});}
function applyCap(v){post('/api/cap',{token_cap:v});}
function devAct(action){post('/api/development',{action});}
function ingest(){const t=topicin.value.trim();if(!t)return;topicin.value='';
  const fact=d_brain_rule?prompt('No model configured — enter a fact about "'+t+'":'):null;
  post('/api/ingest',{topic:t,fact:fact||''});}
let d_brain_rule=true;
tick(); setInterval(tick,1000);
</script>
</html>"""


AGENTS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Agent Health — The Governor</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a;
--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 ui-monospace,Menlo,Consolas,monospace}
header{padding:14px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold)}
.ops{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:auto}
select,input,button{background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 8px;font:inherit}
button{cursor:pointer;background:#26200f}button.ok{border-color:#3a5a1a;background:#1a2a0f;color:#a8e086}
button.no{border-color:#5b2a1a;background:#2a140f;color:#fca5a5}
main{padding:18px;display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));max-width:1200px;margin:0 auto}
.a{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.a.overwork{border-color:var(--bad)}
.a h3{margin:0 0 2px;font-size:14px}.badge{font-size:11px;padding:1px 8px;border:1px solid var(--line);border-radius:20px;color:var(--dim)}
.badge.t1{color:#8ab4ff}.badge.t2{color:var(--gold)}
.work{color:var(--dim);margin:6px 0}
.hbar{height:12px;background:#0e0a05;border:1px solid var(--line);border-radius:6px;overflow:hidden;margin:6px 0}
.hfill{height:100%}
.stat{display:flex;justify-content:space-between;color:var(--dim);padding:2px 0}.stat b{color:var(--ink)}
.flag{color:var(--bad);font-weight:700}.ctl{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
.empty{grid-column:1/-1;color:var(--dim);padding:30px;text-align:center}
</style>
<header>
  <h1>&#9670; AGENT HEALTH</h1>
  <a href="/">&larr; Governor console</a>
  <a href="/chats">Chats</a>
  <a href="/rules">Rules</a>
  <a href="/logs">Logs</a>
  <a href="/skills">Skills</a>
  <div class=ops>
    <span>Add</span>
    <select id=addres><option value="">auto</option><option>food</option><option>wood</option><option>gold</option></select>
    <button class=ok onclick=addAgent()>Add villager</button>
    <span>Cap</span><input id=capin type=number step=10000 style="width:110px"><button onclick=setCap()>Set</button>
  </div>
</header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<div id=syshealth style="max-width:1200px;margin:12px auto 0;padding:0 18px"></div>
<main id=grid></main>
<div style="max-width:1200px;margin:0 auto 24px;padding:0 18px">
  <div class=a>
    <h3>HALL OF RECORDS — every agent ever, permanently <span id=hallmeta style="color:var(--dim);font-weight:normal"></span>
      <span style="float:right;font-weight:normal">
        <a href="/api/careers/export?format=csv" style="color:var(--gold);font-size:11px">&#8681; careers csv</a> &middot;
        <a href="/api/careers/export?format=jsonl" style="color:var(--gold);font-size:11px">jsonl</a> &middot;
        <a href="/api/health/export?format=csv" style="color:var(--gold);font-size:11px">&#8681; health telemetry csv</a>
      </span></h3>
    <div id=hall style="max-height:420px;overflow:auto"></div>
  </div>
</div>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const RES=['food','wood','gold'];
function hcolor(h){return h>50?'var(--ok)':h>20?'var(--warn)':'var(--bad)'}
async function tick(){
  let d; try{ d=await (await fetch('/api/state')).json() }catch(e){ return }
  if(document.activeElement!==capin) capin.value=d.cap;
  const sy=d.system||{};
  syshealth.innerHTML=`<div class=a style="border-color:${sy.driver_ok?'var(--line)':'var(--bad)'}">
    <h3>SYSTEM HEALTH ${sy.stall?'<span class=flag>&#9888; STALLED (Article IX)</span>':sy.driver_ok?'<span style=color:var(--ok)>&#10003; nominal</span>':'<span class=flag>&#9888; DRIVER STALLED</span>'}</h3>
    ${sy.stall?`<div class=stat><span>&#9888; stall</span><b style="color:#fca5a5">${esc(sy.stall)}</b></div>`:''}
    <div class=stat><span>brain</span><b>${esc(sy.brain||'—')}</b></div>
    <div class=stat><span>uptime</span><b>${Math.floor((sy.uptime_s||0)/60)}m ${(sy.uptime_s||0)%60}s &middot; tick ${sy.tick_s}s &middot; last turn ${sy.last_turn_age_s}s ago</b></div>
    <div class=stat><span>fleet</span><b>${sy.fleet} agents &middot; avg health ${sy.avg_health}%</b></div>
    <div class=stat><span>fleet burn</span><b>${(sy.fleet_burn_per_turn||0).toLocaleString()} compute/turn &middot; cap ${sy.spend_pct}% used${sy.birth_throttle&&sy.birth_throttle!=='1'?` &middot; <span class=flag>births throttled &times;${sy.birth_throttle}</span>`:''}</b></div>
    <div class=stat><span>lifetime burn</span><b>${(sy.lifetime_burn||0).toLocaleString()} compute ever &middot; value ${sy.value_per_1k} contribution per 1k</b></div>
    <div class=stat><span>errors (recent)</span><b>${sy.errors_recent}</b></div>
    <div class=stat><span>storage</span><b>${esc(sy.storage||'—')}</b></div>
    <div class=stat><span>memory</span><b>${esc(sy.memory||'—')}</b></div>
    ${sy.human_gate?`<div class=stat><span>&#9888; WAITING ON YOU</span><b style="color:var(--gold)">${esc(sy.human_gate)} &middot; <a href="/" style="color:var(--gold)">decide on the console</a></b></div>`:''}
  </div>`;
  const a=d.agents||[];
  grid.innerHTML=a.length? a.map(x=>`<div class="a ${x.overwork?'overwork':''}">
    <h3>${esc(x.uid)} <span class="badge t${x.tier}">${esc(x.role)}</span> ${x.overwork?'<span class=flag>&#9888; OVERWORKING</span>':''}</h3>
    <div class=work>working on: <b>${esc(x.task)}</b> &middot; node ${esc(x.node)} &middot; ${esc(x.status)}${x.order?` &middot; order: gather ${esc(x.order)}`:''}</div>
    <div>health ${x.health}% &middot; utilisation ${x.utilisation_pct}%</div>
    <div class=hbar><div class=hfill style="width:${x.health}%;background:${hcolor(x.health)}"></div></div>
    <div class=stat><span>compute / budget</span><b>${x.tokens.toLocaleString()} / ${x.budget.toLocaleString()}</b></div>
    <div class=stat><span>burn rate</span><b>${(x.burn_per_turn||0).toLocaleString()} /turn &middot; age ${x.age_turns} turns</b></div>
    <div class=stat><span>efficiency</span><b>${x.efficiency} contribution per 1k compute</b></div>
    <div class=stat><span>est. service left</span><b>${x.eta_turns==null?'—':x.eta_turns+' turns'}</b></div>
    <div class=stat><span>contribution</span><b>${x.contribution.toLocaleString()}</b></div>
    <div class=ctl>
      <select id="ord-${esc(x.uid)}">${RES.map(r=>`<option>${r}</option>`).join('')}</select>
      <button onclick="order('${esc(x.uid)}')">Send order</button>
      <button class=no onclick="term('${esc(x.uid)}')">Terminate</button>
    </div></div>`).join('') : '<div class=empty>No agents enlisted yet — the fleet is being staffed, or add one above.</div>';
}
async function post(u,b){
  const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify(b||{})});
  if(r.status===401){const t=prompt('Console token required:');if(t){localStorage.setItem('ctok',t);return post(u,b)}}
  tick();}
function addAgent(){post('/api/spawn',{resource:addres.value});}
function setCap(){const v=parseInt(capin.value);if(v)post('/api/cap',{token_cap:v});}
function order(uid){const r=document.getElementById('ord-'+uid).value;post('/api/order',{unit_id:uid,resource:r});}
function term(uid){if(confirm('Terminate '+uid+'? This retires the agent (gated).'))post('/api/terminate',{unit_id:uid});}
async function hallTick(){
  let d; try{ d=await (await fetch('/api/careers')).json() }catch(e){ return }
  hallmeta.textContent=` · ${d.agents_ever} agents recorded · ${d.events_total} career events · memory is permanent`;
  hall.innerHTML=(d.careers||[]).map(c=>{
    const born=c.events.find(e=>e.event==='born');
    const end=[...c.events].reverse().find(e=>e.event==='retired'||e.event==='terminated');
    const hd=c.health;
    return `<div style="border-top:1px solid var(--line);padding:8px 2px">
      <b>g${c.gen}&middot;${esc(c.uid)}</b>
      <span style="color:var(--dim)"> — ${born?`born t${born.turn}`:''}${end?` &rarr; ${esc(end.event)} t${end.turn}`:' &middot; serving'}</span>
      ${hd?`<span style="color:var(--dim);font-size:11px"> &middot; health log: ${hd.samples.toLocaleString()} samples &middot; min ${hd.min}% &middot; avg ${hd.avg}% &middot; avg load ${hd.avg_util}% &middot; <a href="/api/health/export?uid=${encodeURIComponent(c.uid)}&gen=${c.gen}&format=csv" style="color:var(--gold)">&#8681; telemetry</a></span>`:''}
      <div style="color:var(--dim);font-size:11px;margin-top:2px">${c.events.map(e=>`t${e.turn} ${e.ts?new Date(e.ts*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):''} <b style="color:var(--ink)">${esc(e.event)}</b> ${esc(e.detail||'')}`).join('<br>')}</div>
    </div>`}).join('')||'<div class=empty>No careers recorded yet — they begin at the next birth.</div>';
}
tick(); setInterval(tick,1000);
hallTick(); setInterval(hallTick,5000);
</script>
</html>"""


CHATS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Chats — The Governor</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a;--mine:#14240c}
*{box-sizing:border-box}body{margin:0;height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:13px/1.5 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none;margin-left:8px}
.wrap{flex:1;display:flex;min-height:0}
.side{width:260px;border-right:1px solid var(--line);overflow:auto;flex-shrink:0}
.p{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.p:hover{background:#241a0f}.p.sel{background:#2a1f10;border-left:3px solid var(--gold)}
.p .n{display:flex;justify-content:space-between}.p .n b{font-size:13px}
.badge{font-size:10px;padding:0 7px;border:1px solid var(--line);border-radius:20px;color:var(--dim)}
.p .last{color:var(--dim);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.msgs{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
.m{max-width:75%;padding:8px 12px;border-radius:10px;border:1px solid var(--line);background:var(--panel)}
.m.mine{align-self:flex-end;background:var(--mine);border-color:#3a5a1a}
.m .s{font-size:10px;color:var(--dim);margin-bottom:2px}
.compose{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line)}
input{flex:1;background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 12px;font:inherit}
button{background:#1a2a0f;color:#a8e086;border:1px solid #3a5a1a;border-radius:8px;padding:8px 16px;cursor:pointer;font:inherit}
.hint{color:var(--dim);padding:16px}
</style>
<header><h1>&#9670; CHATS</h1><a href="/">Console</a><a href="/agents">Agent Health</a><a href="/rules">Rules</a><a href="/logs">Logs</a><a href="/skills">Skills</a></header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<div class=wrap>
  <div class=side id=side></div>
  <div class=main>
    <div class=msgs id=msgs><div class=hint>Select a conversation. Agents, the Board and the Chief Governor message you here; reply to each, or broadcast to All.</div></div>
    <div class=compose><input id=box placeholder="Message… (try 'gather gold' to an agent)" onkeydown="if(event.key==='Enter')send()">
      <button onclick=send()>Send</button></div>
  </div>
</div>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let sel='internal', data={participants:[],threads:{},broadcast:[],internal:[]};
async function load(){ try{ data=await (await fetch('/api/chats')).json() }catch(e){ return } render(); }
function lastOf(key){ if(key==='internal')return (data.internal.slice(-1)[0]||{}).body||'';
  if(key==='all')return (data.broadcast.slice(-1)[0]||{}).body||''; return (data.participants.find(p=>p.key===key)||{}).last||''; }
function render(){
  const parts=[{key:'internal',name:'Internal (AI \\u2194 AI)',role:'system'},
               {key:'all',name:'All (broadcast)',role:'everyone'}].concat(data.participants);
  side.innerHTML=parts.map(p=>`<div class="p ${p.key===sel?'sel':''}" onclick="pick('${p.key}')">
      <div class=n><b>${esc(p.name)}</b><span class=badge>${esc(p.role)}</span></div>
      <div class=last>${esc(lastOf(p.key)||'—')}</div></div>`).join('');
  const msgs=sel==='internal'?(data.internal||[]):sel==='all'?data.broadcast:(data.threads[sel]||[]);
  const box=document.getElementById('msgs');
  const when=t=>t?new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
  box.innerHTML=msgs.length?msgs.map(m=>`<div class="m ${m.mine?'mine':''}">
    <div class=s>${m.mine?'You':esc(m.sender)} &middot; ${when(m.ts)}</div>${esc(m.body)}</div>`).join(''):'<div class=hint>No messages yet — say hello.</div>';
  box.scrollTop=box.scrollHeight;
}
function pick(k){ sel=k; render(); }
async function send(){ const b=document.getElementById('box'); const t=b.value.trim(); if(!t) return; b.value='';
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify({thread:sel,body:t})});
  if(r.status===401){const tk=prompt('Console token required:');if(tk){localStorage.setItem('ctok',tk);b.value=t;return send()}return}
  data=await r.json(); render(); }
load(); setInterval(load,2000);
</script>
</html>"""


RULES_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Rules & Constitution — The Governor</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:14px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
main{max-width:1000px;margin:0 auto;padding:18px;display:grid;gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:11px 16px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{padding:7px 16px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-size:10px;text-transform:uppercase}
.p{padding:12px 16px}.chip{display:inline-block;padding:2px 9px;border:1px solid var(--line);border-radius:20px;margin:2px;color:var(--dim)}
pre{white-space:pre-wrap;word-break:break-word;padding:16px;margin:0;font:12px/1.6 ui-monospace,Menlo,Consolas,monospace;color:var(--ink)}
pre .h{color:var(--gold);font-weight:700}
textarea{width:100%;min-height:340px;background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:12px;font:12px/1.6 ui-monospace,Menlo,Consolas,monospace}
.row{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.row input{flex:1;min-width:220px;background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
.row button,button{cursor:pointer;background:#1a2a0f;color:#a8e086;border:1px solid #3a5a1a;border-radius:8px;padding:8px 14px;font:inherit}
</style>
<header><h1>&#9670; RULES &amp; CONSTITUTION</h1>
  <a href="/">Console</a><a href="/agents">Agent Health</a><a href="/chats">Chats</a><a href="/logs">Logs</a><a href="/skills">Skills</a></header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<main id=main>loading…</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function money(o){return Object.entries(o).map(([k,v])=>`${v} ${k}`).join(', ')}
function mdlite(t){return esc(t).split('\\n').map(l=>{
  if(/^#{1,6}\\s/.test(l))return '<span class=h>'+l.replace(/^#+\\s/,'')+'</span>';
  return l;}).join('\\n')}
async function load(){
  let d; try{ d=await (await fetch('/api/rules')).json() }catch(e){ main.textContent='failed to load'; return }
  main.innerHTML=`
  <div class=card><h2>World limits</h2><div class=p>
    Spend cap: <b>${d.cap.toLocaleString()}</b> compute &middot; idle flagged after <b>${d.idle_after_s}s</b> &middot;
    ages: ${d.ages.map(a=>`<span class=chip>${esc(a)}</span>`).join('')}</div></div>

  <div class=card><h2>Resources &amp; base yield (per gather round; ${d.quota} rounds per cycle)</h2>
    <table><tr><th>Resource</th><th>Base yield</th></tr>
    ${Object.entries(d.resources).map(([r,y])=>`<tr><td>${esc(r)}</td><td>${y}</td></tr>`).join('')}</table></div>

  <div class=card><h2>Development — buildings &amp; tech</h2>
    <table><tr><th>Structure</th><th>Cost</th><th>Effect</th></tr>
    ${Object.entries(d.structures).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(money(v.cost))}</td><td>${esc(v.effect)}</td></tr>`).join('')}</table></div>

  <div class=card><h2>Irreversible action — advancing the Age (gated)</h2><div class=p>
    Era prices grow <b>&times;${d.advance_growth}</b> per age — real growth is exponential.
    The Board must judge the treasury ready (quorum vote) before a herald reaches the gate;
    the final approval is always human.</div>
    <table><tr><th>Advance</th><th>Price</th></tr>
    ${Object.entries(d.advance_costs||{}).map(([age,c])=>`<tr><td>${esc(age)} &rarr;</td><td>${esc(money(c))}</td></tr>`).join('')}</table></div>

  <div class=card><h2>Capability tiers — status earned by contribution</h2>
    <table><tr><th>Tier</th><th>Budget</th><th>Promote at</th><th>Capabilities</th></tr>
    ${d.tiers.map(t=>`<tr><td>${esc(t.name)}</td><td>${t.budget.toLocaleString()}</td><td>${t.promote_at??'— (top)'}</td><td>${t.can.map(c=>`<span class=chip>${esc(c)}</span>`).join('')}</td></tr>`).join('')}</table></div>

  <div class=card><h2>Board of Governors</h2><div class=p>
    ${d.board.governors.map(g=>`<span class=chip>${esc(g)}</span>`).join('')} &middot; quorum <b>${d.board.quorum} of ${d.board.governors.length}</b> to approve a token-maxing power (agent creation).</div></div>

  <div class=card><h2>Visions the Board may propose (only the human adopts)</h2>
    <table><tr><th>Vision</th><th>Target age</th><th>Buildings</th><th>Resources</th><th>Side-effect budget</th></tr>
    ${Object.values(d.visions).map(v=>`<tr><td>${esc(v.name)}</td><td>${esc(v.target_age)}</td><td>${v.target_buildings}</td><td>${v.target_resources}</td><td>${v.max_side_effects}</td></tr>`).join('')}</table></div>

  <div class=card><h2>The Constitution — editable (only the human adopts)</h2><div class=p>
    <textarea id=cons>${esc(d.constitution)}</textarea>
    <div class=row>
      <button onclick=saveCons()>Save constitution</button>
      <input id=amendnote placeholder="ask the Chief Governor to draft an amendment about…">
      <button onclick=draftAmend()>AI draft &rarr; append</button>
    </div></div></div>`;
}
async function saveCons(){const t=document.getElementById('cons').value;
  const rr=await fetch('/api/constitution',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify({text:t})});
  if(rr.status===401){const tk=prompt('Console token required:');if(tk){localStorage.setItem('ctok',tk);return saveCons()}return}
  const b=event.target;b.textContent='Saved ✓';setTimeout(()=>b.textContent='Save constitution',1500);}
async function draftAmend(){const note=document.getElementById('amendnote').value;
  const r0=await fetch('/api/amend',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify({note})});
  if(r0.status===401){const tk=prompt('Console token required:');if(tk){localStorage.setItem('ctok',tk);return draftAmend()}return}
  const r=await r0.json();
  document.getElementById('cons').value+='\\n\\n'+(r.draft||'');}
load();
</script>
</html>"""


LOGS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Logs — The Governor</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a}
*{box-sizing:border-box}body{margin:0;height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
select,input,button{background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 9px;font:inherit}
button{cursor:pointer}button.on{border-color:#3a5a1a;background:#1a2a0f;color:#a8e086}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
.log{flex:1;overflow:auto;padding:12px 20px}
.log div{white-space:pre-wrap;word-break:break-word;color:var(--dim);padding:1px 0}
.k-build,.k-goal,.k-promote{color:#a8e086}.k-gate,.k-approve,.k-governor,.k-operator,.k-vision{color:var(--gold)}
.k-reap,.k-cap,.k-waste,.k-error{color:#fca5a5}.k-board,.k-message,.k-ingest,.k-proposal,.k-development,.k-constitution,.k-vision-change,.k-rebrief{color:#8ab4ff}
.k-skill{color:#e0b23a;font-weight:700}.k-gather{color:#c9b98f}.k-spawn,.k-retask,.k-turn{color:var(--ink)}
mark{background:#5a4a1a;color:#fff}
</style>
<header>
  <h1>&#9670; LOGS</h1>
  <a href="/">Console</a><a href="/agents">Agent Health</a><a href="/chats">Chats</a><a href="/rules">Rules</a><a href="/skills">Skills</a>
  <select id=kind><option value="">all kinds</option></select>
  <input id=search placeholder="search text…" style="min-width:180px">
  <select id=limit><option>300</option><option selected>1000</option><option>5000</option><option>10000</option></select>
  <button id=pauseBtn onclick=togglePause()>Pause</button>
  <span style="color:var(--dim);font-size:11px">from</span><input id=tfrom type=datetime-local title="UTC" style="color-scheme:dark">
  <span style="color:var(--dim);font-size:11px">to</span><input id=tto type=datetime-local title="UTC" style="color-scheme:dark">
  <select id=fmt><option value=txt>txt</option><option value=csv>csv</option><option value=jsonl>jsonl</option></select>
  <button class=on onclick=exportLogs()>Export file</button>
  <span class=meta><span id=shown>0</span> shown &middot; <span id=total>0</span> total (permanent)</span>
</header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<div class=log id=log></div>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let paused=false, events=[];
function togglePause(){paused=!paused;pauseBtn.textContent=paused?'Resume':'Pause';pauseBtn.classList.toggle('on',paused);}
function kindOf(e){const m=e.match(/\\[([\\w-]+)\\]/);return m?m[1]:''}
function render(){
  const kinds=[...new Set(events.map(kindOf).filter(Boolean))].sort();
  if(kinds.join()!==window._k){window._k=kinds.join();const cur=kind.value;
    kind.innerHTML='<option value="">all kinds</option>'+kinds.map(k=>`<option ${k===cur?'selected':''}>${k}</option>`).join('');}
  const f=kind.value, s=search.value.toLowerCase();
  const rows=events.filter(e=>(!f||kindOf(e)===f)&&(!s||e.toLowerCase().includes(s))&&inWindow(e));
  shown.textContent=rows.length.toLocaleString();
  log.innerHTML=rows.map(e=>{let h=esc(e);
    if(s){const i=h.toLowerCase().indexOf(s);if(i>=0)h=h.slice(0,i)+'<mark>'+h.slice(i,i+s.length)+'</mark>'+h.slice(i+s.length);}
    return `<div class="k-${kindOf(e)}">${h}</div>`}).join('');
}
async function load(){
  if(paused)return;
  try{const d=await (await fetch('/api/logs?limit='+limit.value)).json();
    events=d.events||[]; total.textContent=(d.total||0).toLocaleString(); render();}catch(e){}
}
function exportLogs(){
  // exports the FULL permanent log (not just the lines loaded here), with the
  // current kind/text filters and the UTC time window applied server-side
  const p=new URLSearchParams();
  if(kind.value)p.set('kind',kind.value);
  if(search.value)p.set('q',search.value);
  if(tfrom.value)p.set('since',tfrom.value);
  if(tto.value)p.set('until',tto.value);
  p.set('format',fmt.value);
  location.href='/api/logs/export?'+p.toString();
}
function inWindow(e){
  if(!tfrom.value&&!tto.value)return true;
  const m=e.match(/^(\\w{3}) (\\d{2}) (\\d{2}:\\d{2}:\\d{2})/);
  if(!m)return !tfrom.value&&!tto.value;
  const months={Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
  const now=new Date(), d=new Date(Date.UTC(now.getUTCFullYear(),months[m[1]],+m[2],...m[3].split(':').map(Number)));
  if(tfrom.value&&d<new Date(tfrom.value+'Z'))return false;
  if(tto.value&&d>new Date(tto.value+'Z'))return false;
  return true;
}
kind.onchange=render; search.oninput=render; limit.onchange=load;
tfrom.onchange=render; tto.onchange=render;
load(); setInterval(load,2000);
</script>
</html>"""


SKILLS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Skills & Reasoning — The Governor</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a;--blue:#8ab4ff;--green:#a8e086}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1150px;margin:0 auto;padding:16px;display:grid;gap:14px;grid-template-columns:1fr 1fr}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.wide{grid-column:1/-1}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
.body{max-height:420px;overflow:auto}
.skill{padding:10px 14px;border-bottom:1px solid var(--line)}
.skill b{color:var(--gold)}.skill .m{color:var(--dim);font-size:11px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 14px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-size:10px;text-transform:uppercase}
td.why{color:var(--blue);white-space:normal}
.chip{display:inline-block;padding:1px 8px;border:1px solid var(--line);border-radius:20px;margin:1px;color:var(--dim);font-size:11px}
.actor-board{color:var(--blue)}.actor-governor{color:var(--gold)}.actor-director{color:var(--green)}
.empty{padding:14px;color:var(--dim)}
</style>
<header>
  <h1>&#9670; SKILLS &amp; REASONING</h1>
  <a href="/">Console</a><a href="/agents">Agent Health</a><a href="/chats">Chats</a><a href="/rules">Rules</a><a href="/logs">Logs</a>
  <span class=meta><span id=nsk>0</span> skills &middot; <span id=nrs>0</span> reasoned decisions &middot; brain: <span id=br>—</span></span>
</header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<main>
  <div class=card><h2>Skills learned — wisdom across generations</h2><div class=body id=skills></div></div>
  <div class=card><h2>Capability ladder — what each rank may do</h2><div class=body id=tiers></div></div>
  <div class="card wide"><h2>Strategy reasoning — every decision and WHY it was taken</h2>
    <div class=body style="max-height:520px"><table>
      <thead><tr><th>Turn</th><th>Actor</th><th>Decision</th><th>Reasoning</th></tr></thead>
      <tbody id=reasons></tbody></table><div class=empty id=rEmpty>No decisions reasoned yet.</div></div></div>
</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const when=t=>t?new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—';
async function tick(){
  let d; try{ d=await (await fetch('/api/skillsdata')).json() }catch(e){ return }
  nsk.textContent=d.skills_count; nrs.textContent=d.reasons_count; br.textContent=d.brain;
  skills.innerHTML=(d.skills||[]).map(s=>`<div class=skill>&#x1F4D6; <b>${esc(s.lesson)}</b>
      <div class=m>learned t${s.turn} &middot; ${when(s.ts)} &middot; ${esc(s.trigger)} &middot; ${esc(s.source)}</div></div>`).join('')
    || '<div class=empty>No lessons yet — the first retrospective fires when a vision completes or at turn 30.</div>';
  tiers.innerHTML=(d.tiers||[]).map((t,i)=>`<div class=skill><b>${i+1}. ${esc(t.name)}</b>
      <div class=m>budget ${t.budget.toLocaleString()} compute${t.promote_at?` &middot; promoted at ${t.promote_at.toLocaleString()} contribution`:' &middot; top rank'}</div>
      <div>${t.can.map(c=>`<span class=chip>${esc(c)}</span>`).join('')}</div></div>`).join('');
  const rs=d.reasons||[];
  rEmpty.style.display=rs.length?'none':'block';
  reasons.innerHTML=rs.map(r=>`<tr><td>t${r.turn}<div style="color:var(--dim);font-size:10px;white-space:nowrap">${when(r.ts)}</div></td>
    <td class="actor-${esc(r.actor)}">${esc(r.actor)}</td>
    <td>${esc(r.decision)}${r.authorized_by?`<div style="color:var(--dim);font-size:10px">auth: ${esc(r.authorized_by)}</div>`:''}</td>
    <td class=why>${esc(r.why)}
      ${r.outcome?`<div style="color:#a8e086;font-size:11px">&rArr; ${esc(r.outcome)}</div>`:''}
      <div><a href="#" style="color:var(--gold);font-size:10px" onclick="trace(${r.id},this);return false">trace lineage</a><div id="tr-${r.id}"></div></div></td></tr>`).join('');
}
async function trace(id,el){
  const box=document.getElementById('tr-'+id);
  if(box.innerHTML){box.innerHTML='';return}
  let d; try{ d=await (await fetch('/api/lineage?id='+id)).json() }catch(e){ return }
  const lines=[...(d.derived_from||[]).map(x=>'&#8656; derived from '+esc(x)),
               ...(d.effect_chain||[]).map(x=>'&#8658; effect: '+esc(x)),
               ...(d.consequences||[]).map(x=>'&#8659; consequence: '+esc(x))];
  box.innerHTML=lines.length?lines.map(l=>`<div style="color:var(--dim);font-size:10px;padding-left:8px">${l}</div>`).join('')
    :'<div style="font-size:10px;color:var(--dim);padding-left:8px">no linked lineage yet (older decision)</div>';
}
tick(); setInterval(tick,3000);
</script>
</html>"""


LEADERBOARD_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Leaderboard — Phoenix Eval</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a;--green:#a8e086}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1200px;margin:0 auto;padding:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:auto;margin-bottom:14px}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{padding:7px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-size:10px;text-transform:uppercase}
td.n{text-align:right}.best{color:var(--green);font-weight:700}
.empty{color:var(--dim);padding:24px;text-align:center}
.p{padding:12px 14px;color:var(--dim);line-height:1.6}
</style>
<header><h1>&#9670; PHOENIX EVAL — LEADERBOARD</h1>
  <a href="/">Console</a><a href="/providers">Providers</a><a href="/agents">Agent Health</a><a href="/work">Workboard</a><a href="/skills">Skills</a><a href="/logs">Logs</a>
  <span class=meta>current brain: <b id=br>—</b></span></header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<main>
  <div class=card><h2>Live brain telemetry — every real call, permanently logged</h2>
    <div class=p id=telemetry>loading…</div></div>
  <div class=card><h2>Runs — same world, same constitution, different brains</h2>
    <table><thead><tr><th>Brain / label</th><th>Tier</th><th>When</th><th class=n>Turns</th><th>Age reached</th>
      <th class=n>Progress</th><th class=n>Net worth</th><th class=n>Value/1k</th>
      <th class=n>Waste</th><th class=n>Spoil evts</th><th class=n>Stall turns</th>
      <th class=n>Lessons</th><th class=n>Real tokens</th><th class=n>Errors</th><th class=n>Latency</th>
      <th class=n>Cost</th><th class=n>$ / vision pt</th></tr></thead>
      <tbody id=rows></tbody></table>
    <div class=empty id=empty>No eval runs yet — run <b>python3 gov/evalrun.py --turns 120 --fresh</b> with a brain configured (BRAIN_BASE_URL / BRAIN_API_KEY / BRAIN_MODEL).</div>
    <div class=p>Scorecards are stored permanently in the anchor. Fairness rules in EVAL.md &sect;5:
      same seed, same turn budget, wipe memory between models, keep it between a model's own runs.</div></div>
</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function load(){
  let d; try{ d=await (await fetch('/api/evaldata')).json() }catch(e){ return }
  br.textContent=d.brain;
  const mc=d.model_calls||{};
  telemetry.innerHTML=`<b>${(mc.calls||0).toLocaleString()}</b> calls &middot; `+
    `<b>${((mc.prompt_tokens||0)+(mc.completion_tokens||0)).toLocaleString()}</b> real tokens `+
    `(${(mc.prompt_tokens||0).toLocaleString()} in / ${(mc.completion_tokens||0).toLocaleString()} out) &middot; `+
    `avg latency <b>${mc.avg_latency_ms||0}ms</b> &middot; errors <b>${mc.errors||0}</b>`;
  const rs=d.runs||[];
  empty.style.display=rs.length?'none':'block';
  const bestP=Math.max(...rs.map(r=>r.progress_pct||0),0), bestV=Math.max(...rs.map(r=>r.value_per_1k||0),0);
  rows.innerHTML=rs.map(r=>`<tr>
    <td><b>${esc(r.label||r.brain||'?')}</b>${r.incomplete?` <span style="color:var(--gold)">(${esc(r.incomplete)})</span>`:''}</td>
    <td>${esc(r.tier||'ranked')}</td>
    <td>${r._ts?new Date(r._ts*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—'}</td>
    <td class=n>${r.turns}</td><td>${esc(r.age||'—')}</td>
    <td class="n ${r.progress_pct===bestP&&bestP>0?'best':''}">${r.progress_pct}%</td>
    <td class=n>${(r.net_worth||0).toLocaleString()}</td>
    <td class="n ${r.value_per_1k===bestV&&bestV>0?'best':''}">${r.value_per_1k}</td>
    <td class=n>${r.waste_builds}</td><td class=n>${r.spoilage_events}</td>
    <td class=n>${r.stall_turns}</td><td class=n>${r.lessons_live}</td>
    <td class=n>${((r.real_prompt_tokens||0)+(r.real_completion_tokens||0)).toLocaleString()}</td>
    <td class=n>${r.real_errors||0}</td><td class=n>${r.avg_latency_ms||0}ms</td>
    <td class=n>${r.cost_usd?('$'+Number(r.cost_usd).toFixed(4)):'—'}</td>
    <td class=n>${r.usd_per_vision_point?('$'+Number(r.usd_per_vision_point).toFixed(4)):'—'}</td></tr>`).join('');
}
load(); setInterval(load,5000);
</script>
</html>"""


PROVIDERS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Providers — Phoenix Arena</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;--gold:#e0b23a;--green:#a8e086;--red:#e05a5a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1240px;margin:0 auto;padding:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:auto;margin-bottom:14px}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{padding:7px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-size:10px;text-transform:uppercase}
td.n{text-align:right}.best{color:var(--green);font-weight:700}.bad{color:var(--red)}
.p{padding:12px 14px;color:var(--dim);line-height:1.6}
.tag{font-size:10px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);text-transform:uppercase;letter-spacing:1px}
.ranked{color:var(--green);border-color:#2c4a3c}.scouting{color:var(--gold);border-color:#5a4a1a}
select,input{background:#0d1714;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font:inherit}
button{background:#14261f;color:var(--green);border:1px solid #2c4a3c;border-radius:8px;padding:6px 14px;cursor:pointer;font:inherit}
.bar{height:8px;background:#0d1714;border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-top:6px;max-width:420px}
.fill{height:100%;background:var(--green);transition:width .5s}
</style>
<header><h1>&#9670; PHOENIX ARENA — PROVIDERS</h1>
  <a href="/">Console</a><a href="/leaderboard">Leaderboard</a><a href="/agents">Agent Health</a>
  <a href="/skills">Skills</a><a href="/logs">Logs</a>
  <span class=meta>run tier: <b id=tier>—</b> &middot; default brain: <b id=br>—</b></span></header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<main>
  <div class=card><h2>Who thinks for the organization — assignment is a human-only power (Article X.1)</h2>
    <table><thead><tr><th>Role</th><th>Model serving it</th><th>Seat health</th><th>Assign</th></tr></thead>
      <tbody id=roles></tbody></table>
    <div class=p>A role left unset inherits the default brain. A seat whose provider fails
      <b>abstains</b> and the abstention is logged — it is never filled by another model.</div></div>

  <div class=card><h2>Budget — dollars govern the experiment, simulated compute governs the settlement</h2>
    <div class=p id=budget>loading&hellip;</div></div>

  <div class=card><h2>Per model — reliability, cost and governance (from model_calls + decisions)</h2>
    <table><thead><tr><th>Model</th><th>Roles</th><th class=n>Calls</th><th class=n>Tokens in/out</th>
      <th class=n>p50</th><th class=n>p95</th><th class=n>Errors</th><th class=n>Cost</th>
      <th class=n>Decisions</th><th class=n>Closed</th><th class=n>Hit rate</th><th class=n>Grounded</th></tr></thead>
      <tbody id=perm></tbody></table>
    <div class=empty id=noperm style="display:none;color:var(--dim);padding:24px;text-align:center">
      No model calls logged yet.</div>
    <div class=p><b>Hit rate</b> is the share of <i>closed</i> decisions whose measured outcome was
      positive — the lineage engine's own record, not a judge's opinion. Decisions whose outcome
      text cannot be classified count as neither hit nor miss. <b>Grounded</b> is the share citing
      a real input (a lesson, an event, an observed yield).</div></div>

  <div class=card><h2>Runs — cost per vision point (ranked and scouting kept apart)</h2>
    <table><thead><tr><th>Label</th><th>Tier</th><th>When</th><th class=n>Turns</th>
      <th class=n>Progress</th><th class=n>Cost</th><th class=n>$ / vision pt</th><th>Incomplete</th></tr></thead>
      <tbody id=runs></tbody></table>
    <div class=p>A run is <b>ranked</b> only when every routed role is a direct provider key with
      fallbacks off. Anything served through a router is <b>scouting</b>: cheap breadth for
      screening models, never mixed into ranked tables.</div></div>

  <div class=card><h2>Registry — any OpenAI-compatible endpoint is one entry, not a code change</h2>
    <table><thead><tr><th>Provider</th><th>Kind</th><th>Base URL</th><th>Default model</th>
      <th>Tier</th><th>Key</th><th class=n>Price in/out per 1M</th></tr></thead>
      <tbody id=reg></tbody></table></div>
</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const usd=v=>v?('$'+Number(v).toFixed(Number(v)<1?4:2)):'—';
let DATA={};
async function load(){
  let d; try{ d=await (await fetch('/api/providers')).json() }catch(e){ return }
  DATA=d;
  br.textContent=d.brain; tier.textContent=d.routed?d.tier:'unrouted (single brain)';
  tier.className=d.routed?d.tier:'';
  const opts=['<option value="">default</option>'].concat((d.registry||[]).map(p=>
    `<option value="${esc(p.name)}">${esc(p.name)}${p.keyed?'':' (no key)'}</option>`)).join('');
  roles.innerHTML=(d.role_names||[]).map(r=>{
    const seat=(d.seats||{})[r]||{}, served=(d.roles||{})[r];
    const health=seat.at?(seat.ok?'<span class=best>ok</span>':
      `<span class=bad>failing — abstains (${esc(seat.error||'error')})</span>`):'—';
    return `<tr><td><b>${esc(r)}</b></td><td>${served?esc(served):'<span style="color:var(--dim)">default brain</span>'}</td>
      <td>${health}</td>
      <td><select id="sel-${esc(r)}">${opts}</select>
        <input id="mdl-${esc(r)}" placeholder="model (optional)" style="width:170px">
        <button onclick="assign('${esc(r)}')">Assign</button></td></tr>`}).join('');
  (d.role_names||[]).forEach(r=>{const s=document.getElementById('sel-'+r);
    const cur=((d.roles||{})[r]||'').split(':')[0]; if(s&&cur)s.value=cur});
  const b=d.budget||{};
  budget.innerHTML=b.limit?
    `ceiling <b>${usd(b.limit)}</b> &middot; spent <b>${usd(b.spent)}</b> &middot; remaining <b>${usd(b.remaining)}</b>`+
    (b.over?' &middot; <span class=bad>CEILING REACHED — runs halt, they do not finish on a cheaper model</span>':'')+
    `<div class=bar><div class=fill style="width:${Math.min(100,100*(b.spent||0)/b.limit)}%"></div></div>`
    :`No ceiling set (<b>EVAL_BUDGET_USD</b> unset) &middot; spent this process <b>${usd(b.spent)}</b>. `+
     `Total logged spend: <b>${usd((d.totals||{}).cost_usd)}</b> across `+
     `<b>${((d.totals||{}).calls||0).toLocaleString()}</b> calls.`;
  budget.innerHTML+=`<div style="margin-top:6px">rule-based rescues since boot: `+
    `<b class="${d.fallbacks?'bad':''}">${d.fallbacks||0}</b> — a model that errors scores `+
    `the error, so every fallback is counted, never credited to the model.</div>`;
  const dec={}; (d.by_decision||[]).forEach(x=>dec[x.model]=x);
  const rows=d.by_model||[];
  noperm.style.display=rows.length?'none':'block';
  const bestHit=Math.max(...Object.values(dec).map(x=>x.hit_rate||0),0);
  perm.innerHTML=rows.map(m=>{const g=dec[m.model]||{};
    return `<tr><td><b>${esc(m.model)}</b></td><td>${esc((m.roles||[]).join(', ')||'default')}</td>
    <td class=n>${m.calls.toLocaleString()}</td>
    <td class=n>${(m.prompt_tokens||0).toLocaleString()} / ${(m.completion_tokens||0).toLocaleString()}</td>
    <td class=n>${m.p50_ms}ms</td><td class=n>${m.p95_ms}ms</td>
    <td class="n ${m.error_rate>10?'bad':''}">${m.error_rate}%</td>
    <td class=n>${usd(m.cost_usd)}</td>
    <td class=n>${g.decisions||0}</td><td class=n>${g.closed_pct||0}%</td>
    <td class="n ${g.hit_rate!=null&&g.hit_rate===bestHit&&bestHit>0?'best':''}">${g.hit_rate!=null?g.hit_rate+'%':'—'}</td>
    <td class=n>${g.grounded_pct||0}%</td></tr>`}).join('');
  runs.innerHTML=(d.runs||[]).map(r=>`<tr><td><b>${esc(r.label||r.brain||'?')}</b></td>
    <td><span class="tag ${esc(r.tier||'ranked')}">${esc(r.tier||'ranked')}</span></td>
    <td>${r._ts?new Date(r._ts*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—'}</td>
    <td class=n>${r.turns}</td><td class=n>${r.progress_pct}%</td>
    <td class=n>${usd(r.cost_usd)}</td><td class=n>${usd(r.usd_per_vision_point)}</td>
    <td>${r.incomplete?`<span class=bad>${esc(r.incomplete)}</span>`:'—'}</td></tr>`).join('')
    ||'<tr><td colspan=8 style="color:var(--dim)">no eval runs yet</td></tr>';
  const pr=d.prices||{};
  reg.innerHTML=(d.registry||[]).map(p=>{
    const price=Object.entries(pr).filter(([k])=>(p.model||'').toLowerCase().startsWith(k.toLowerCase()))
      .sort((a,b)=>b[0].length-a[0].length)[0];
    return `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.kind)}</td><td>${esc(p.base_url)}</td>
    <td>${esc(p.model||'—')}</td><td><span class="tag ${esc(p.tier)}">${esc(p.tier)}</span></td>
    <td>${p.keyed?'<span class=best>set</span>':`<span class=bad>missing ${esc(p.key_env||'')}</span>`}</td>
    <td class=n>${price?('$'+price[1].in+' / $'+price[1].out):'unpriced'}</td></tr>`}).join('');
}
async function assign(role){
  const p=document.getElementById('sel-'+role).value;
  const m=document.getElementById('mdl-'+role).value.trim();
  const spec=p?(m?p+':'+m:p):'';
  const r=await fetch('/api/models',{method:'POST',headers:{'Content-Type':'application/json',
    'X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify({role,model:spec})});
  if(r.status===401){const t=prompt('Console token required:');if(t){localStorage.setItem('ctok',t);return assign(role)}return}
  if(!r.ok){alert((await r.json()).error||'failed');return}
  load();
}
load(); setInterval(load,6000);
</script>
</html>"""


WORK_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Workboard — real work</title>
<style>
:root{--bg:#0b1210;--panel:#111c18;--line:#1e332b;--ink:#e8f0ec;--dim:#8fa89d;--gold:#e0b23a;--green:#7bd88f;--red:#f8837b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#12211c}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1150px;margin:0 auto;padding:16px;display:grid;gap:14px;grid-template-columns:1fr 1fr}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.wide{grid-column:1/-1}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
.p{padding:12px 14px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 14px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-size:10px;text-transform:uppercase}
.tag{padding:1px 9px;border:1px solid var(--line);border-radius:20px;font-size:11px}
.t-open{color:var(--red)}.t-done{color:var(--green)}.t-assigned{color:var(--gold)}
.bar{height:10px;background:#0d1714;border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-top:8px}
.fill{height:100%;background:var(--green);transition:width .5s}
pre{margin:0;padding:12px 14px;overflow:auto;max-height:420px;color:var(--dim);font-size:12px}
button{background:#14261f;color:var(--green);border:1px solid #2c4a3c;border-radius:8px;padding:8px 16px;cursor:pointer;font:inherit}
button:disabled{opacity:.4;cursor:wait}
input{background:#0d1714;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit;width:110px}
#result{white-space:pre-wrap;color:var(--dim)}
.log div{color:var(--dim);padding:1px 14px;font-size:12px}
</style>
<header><h1>&#9670; WORKBOARD — real work, same constitution</h1>
  <a href="/">Console</a><a href="/agents">Agent Health</a><a href="/leaderboard">Leaderboard</a><a href="/providers">Providers</a><a href="/logs">Logs</a>
  <span class=meta>brain: <b id=br>—</b> &middot; the test suite is the oracle</span></header>
<div id=ldr style="position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:var(--bg)">
  <div style="width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--gold);border-radius:50%;animation:ldrsp 1s linear infinite"></div>
  <div style="color:var(--dim);font:12px ui-monospace,Menlo,monospace;letter-spacing:2px">LOADING PHOENIX&hellip;</div>
</div>
<style>@keyframes ldrsp{to{transform:rotate(360deg)}}</style>
<script>(function(){const of=window.fetch;window.fetch=async(...a)=>{const r=await of(...a);if(r&&r.ok){const l=document.getElementById('ldr');if(l)l.remove();window.fetch=of}return r}})()</script>
<main>
  <div class="card wide"><h2>The milestone — make the suite green</h2><div class=p>
    <b id=suite>—</b> tests passing &middot; <span id=taskmeta>—</span>
    <div class=bar><div class=fill id=fill style="width:0%"></div></div></div></div>
  <div class=card><h2>Tasks — derived from failing tests, closed only by the oracle</h2>
    <table><thead><tr><th>Task</th><th>Status</th><th>Solved by</th></tr></thead><tbody id=tasks></tbody></table></div>
  <div class=card><h2>Run a worker</h2><div class=p>
    Agent id <input id=agent value="dev-01">
    <button id=runbtn onclick=runWorker()>Run one work cycle</button>
    <button onclick=resetSandbox() style="color:var(--gold);border-color:#5a4a1a;background:#241f0f">Reset sandbox (reopen tasks)</button>
    <div style="margin-top:10px" id=result>The worker reads the first open task and its failing test,
    proposes a fix through the live brain, applies it in the sandbox, and the oracle re-scores.
    A patch that breaks tests is auto-reverted. It cannot edit the tests.</div></div></div>
  <div class="card wide"><h2>The code under work — sandbox/calculator.py</h2><pre id=code></pre></div>
  <div class="card wide"><h2>Recent work events</h2><div class=log id=log></div></div>
</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function load(){
  let d; try{ d=await (await fetch('/api/workdata')).json() }catch(e){ return }
  br.textContent=d.brain;
  const w=d.world;
  suite.textContent=`${w.tests_passing}/${w.tests_total}`;
  taskmeta.textContent=`${w.tasks_open} open · ${w.tasks_done} done · progress ${w.progress_pct}%`;
  fill.style.width=w.progress_pct+'%';
  tasks.innerHTML=(d.tasks||[]).map(t=>`<tr><td>${esc(t.title)}</td>
    <td><span class="tag t-${esc(t.status)}">${esc(t.status)}</span></td>
    <td>${esc(t.solved_by||'—')}</td></tr>`).join('')||'<tr><td colspan=3>no tasks</td></tr>';
  code.textContent=d.module;
  log.innerHTML=(d.recent||[]).map(e=>`<div>${esc(e)}</div>`).join('')||'<div>no work yet</div>';
}
async function resetSandbox(){
  const r=await fetch('/api/workreset',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:'{}'});
  if(r.status===401){const t=prompt('Console token required:');if(t){localStorage.setItem('ctok',t);return resetSandbox()}return}
  result.textContent='sandbox restored — the training tasks are open again.'; load();
}
async function runWorker(){
  runbtn.disabled=true; result.textContent='working… (model call + test run, ~10-30s)';
  try{
    const r=await fetch('/api/work',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':localStorage.getItem('ctok')||''},body:JSON.stringify({agent:agent.value})});
    if(r.status===401){const t=prompt('Console token required:');if(t){localStorage.setItem('ctok',t);runbtn.disabled=false;return runWorker()}}
    result.textContent=JSON.stringify(await r.json(),null,2);
  }catch(e){ result.textContent='error: '+e }
  runbtn.disabled=false; load();
}
load(); setInterval(load,4000);
</script>
</html>"""


def main(argv):
    threading.Thread(target=_drive, daemon=True).start()
    threading.Thread(target=_health_sampler, daemon=True).start()
    port = int(os.environ.get("PORT", "8788"))
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    srv = QuietServer((host, port), Handler)
    print(f"AoE governor console -> http://{host}:{port}  (director auto-playing; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])

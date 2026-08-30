"""Acceptance checks for the Age of Empires MVP — the Governor over a game economy.

Proves the three core functions on real game work, reusing governor.py unchanged:
  1. Villagers gather concurrently; the World (oracle) reflects it; state is durable.
  2. Cap HALTS spawning at a compute ceiling.
  3. Idle villagers (finished, parked awaiting orders) surface.
  4. The irreversible Age-up gate pauses, approve/reject is durable.

Run:  python3 gov/verify_sim.py
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brain as B
import governor as G
import sim as S

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        p = S.DB + suffix
        if os.path.exists(p):
            os.remove(p)


# ── 1. villagers gather concurrently; the World reflects it; durable ─────────
print("\n1. Economy — villagers gather, World is the oracle")
fresh_db()
cp = S.connect()
graph = S.build(cp)

w0 = S.world()
villagers = [(f"vil-{i+1:02d}", B.choose_resource(i, w0)) for i in range(3)]
with ThreadPoolExecutor(max_workers=3) as ex:
    list(ex.map(lambda t: S.spawn(graph, t[0], "villager", resource=t[1]), villagers))

v = G.units(graph, cp)
check("3 villagers spawned concurrently", len(v) == 3)
check("each gathered its quota", all(u.steps == S.QUOTA for u in v))
check("all parked awaiting orders", all(u.pending for u in v))

expect = {r: sum(S.BASE[res] * S.QUOTA for _, res in villagers if res == r) for r in S.RESOURCES}
w = S.world()
check("World reflects gathered resources (the oracle)",
      all(w[r] == expect[r] for r in S.RESOURCES),
      S.render_world())

# separate process reads identical World + fleet
probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0,{HERE!r});"
     "import sim as S, governor as G;"
     "cp=S.connect(); g=S.build(cp); v=G.units(g,cp); w=S.world();"
     "print(len(v), sum(u.steps for u in v), w['food']+w['wood']+w['gold'])"],
    capture_output=True, text=True)
n, steps, total = (probe.stdout.split() + ["", "", ""])[:3]
check("fresh process reads identical state (durable)",
      (n, steps) == ("3", str(3 * S.QUOTA)),
      f"units={n} steps={steps} resources={total}" if probe.stdout else probe.stderr.strip()[:80])

# ── 2. governor cap HALTS spawning ───────────────────────────────────────────
print("\n2. Cap — halts, does not warn")
v = G.units(graph, cp)
check("compute spend tracked across fleet", G.spent(v) > 0, f"{G.spent(v):,} tokens")
G.TOKEN_CAP = 1_000
ok, reason = G.may_spawn(v)
check("cap HALTS spawning a new villager", ok is False, reason)
G.TOKEN_CAP = 100_000

# ── 3. idle villager alert ───────────────────────────────────────────────────
print("\n3. Idle — the villager parked on a human who isn't looking")
fresh = {u.unit_id: u for u in G.units(graph, cp)}["vil-01"]
check("freshly parked villager reads awaiting_approval",
      fresh.status == "awaiting_approval", f"age {fresh.age_s}s")
time.sleep(G.IDLE_AFTER_S + 0.3)
aged = {u.unit_id: u for u in G.units(graph, cp)}["vil-01"]
check("same villager flips to idle once stale", aged.status == "idle", f"age {aged.age_s}s")
check("all idle villagers surface together", len(G.idle(G.units(graph, cp))) == 3)

# ── 4. irreversible Age-up gate ──────────────────────────────────────────────
print("\n4. Gate — advancing the Age is irreversible, so it stops at a human")
# Stock the treasury so the advance is affordable, then send the herald.
S._world_add("food", S.ADVANCE_COST["food"])
S._world_add("gold", S.ADVANCE_COST["gold"])
before = S.world()
S.spawn(graph, "herald-01", "herald")
herald = {u.unit_id: u for u in G.units(graph, cp)}["herald-01"]
check("herald parked at the Age-up gate", bool(herald.pending),
      herald.pending["action"][:46] + "…" if herald.pending else "")
check("gate marks the action irreversible", herald.pending and herald.pending["reversible"] is False)

S.resume(graph, "herald-01", "approve")
after = S.world()
check("approve advances the Age and spends resources",
      after["age"] == "Feudal Age" and after["food"] == before["food"] - S.ADVANCE_COST["food"],
      S.render_world())

done = {u.unit_id: u for u in G.units(graph, cp)}["herald-01"]
check("resolved herald reports done", done.status == "done")

# decision survives a restart
probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0,{HERE!r});"
     "import sim as S; print(S.world()['age'])"],
    capture_output=True, text=True)
check("Age-up persists across restart", "Feudal Age" in probe.stdout,
      probe.stdout.strip() or probe.stderr.strip()[:80])

# ── 5. skill memory: retrospectives distill strategy, decisions read it ──────
print("\n5. Skill memory — learning across generations (Article VI: facts → wisdom)")
import anchor as A
import brain as B2
A.init()
digest = {"trigger": "test", "turn": 9, "progress": 62, "side_effects": 1,
          "waste": 2, "cap_hits": 0, "reaps": 3, "promotions": 0,
          "best_resource": "food", "yields": {"food": 28.6}, "spend_ratio": 0.4}
lessons = B2.retrospective(digest, [])
check("retrospective distills lessons from a run", 1 <= len(lessons) <= 3,
      lessons[0][:60] + "…")
for les in lessons:
    A.skill_add(9, les, source="test", trigger="test")
top = {x["lesson"] for x in A.skills_top(5)}
check("lessons persist in the skills store", set(lessons) <= top)
n = A.skills_count()
A.skill_add(9, lessons[0], source="test", trigger="test")     # exact duplicate
check("duplicate lessons are not hoarded", A.skills_count() == n)

print("\n" + S.render_world())
print(G.render(sorted(G.units(graph, cp), key=lambda u: u.unit_id)))

# ── 6. upkeep: assets decay, repair restores, surplus food rots ──────────────
print("\n6. Upkeep — decay, repair, spoilage")
S._world_add("wood", 500)
ok_b, _ = S.build_structure("mill")
y_full = S.effective_yield("food")
for k in range(1, 9):
    S.decay_tick(S.DECAY_EVERY * k)
cond = S.conditions().get("mill", 100)
check("built assets decay over time", ok_b and cond < 100, f"mill condition {cond}%")
y_worn = S.effective_yield("food")
check("a worn asset gives a weaker bonus", y_worn <= y_full, f"{y_full} → {y_worn}")
ok_r, msg = S.repair("mill")
check("repair restores full condition", ok_r and S.conditions()["mill"] == 100, msg)
cap_now = S.food_cap()
S._world_add("food", max(0, cap_now + 2_000 - S.world()["food"]))
loss, cap2 = S.spoil_tick()
check("food above the storage cap rots", loss > 0, f"lost {loss} over cap {cap2:,}")

# ── 7. permanent memory: the anchor survives a world reset ───────────────────
print("\n7. Permanence — wiping the game world never touches the memory")
check("anchor lives in its own DB, separate from the game world", A.DB != S.DB,
      os.path.basename(A.DB))
A.career_add("vil-test", 9, "born", "enlisted for the permanence check")
A.career_add("vil-test", 12, "retired", "budget spent")
life = next((c for c in A.careers() if c["uid"] == "vil-test"), None)
check("an agent's career is recorded and readable", life is not None
      and [e["event"] for e in life["events"]][:2] == ["born", "retired"])
before_skills, before_reasons = A.skills_count(), A.reasons_count()
fresh_db()                                        # the world reset: game DB deleted
check("skills survive the world wipe", A.skills_count() == before_skills and before_skills > 0)
check("reasoning and careers survive the world wipe",
      A.reasons_count() == before_reasons
      and any(c["uid"] == "vil-test" for c in A.careers()))

# ── 8. lineage: decisions are first-class and traceable both ways ────────────
print("\n8. Lineage — why() walks to the roots, credit() walks to the value")
stamp = int(time.time())
sid = A.skill_add(9, f"Test lesson {stamp}: rebalance early", source="test", trigger="lineage")
e1 = A.record(9, "board", "[3/3] approved test build")
did = A.reason_add(9, "director", "build test_mill", "testing provenance",
                   derived_from=[f"skill:{sid}"], authorized_by="board:3/3")
e2 = A.record(9, "build", "built test_mill", caused_by=e1)
A.decision_close(did, e2, outcome="food yield +50%")
A.record(9, "gather", "vil-x gathered 30 food", caused_by=e2)
lin = A.lineage(did)
check("decision carries authorization and measured outcome",
      lin["decision"]["authorized_by"] == "board:3/3"
      and "yield" in lin["decision"]["outcome"])
check("why() walks back to the lesson it derived from",
      any(f"Test lesson {stamp}" in x for x in lin["derived_from"]))
check("effect chain reaches the event that caused it",
      any("approved test build" in x for x in lin["effect_chain"]))
check("credit() walks forward to the value produced",
      any("gathered 30 food" in x for x in lin["consequences"]))

# ── 9. governance regressions (the constitutional audit, as tests) ───────────
print("\n9. Governance — the audit's assertions, enforced")
import board as BD

# VIII.1: disjoint evidence — the same board must vote differently on different evidence
bv_good = BD.vote("create x", {"affordable": True, "within_budget": True, "spent": 100_000,
                               "cap": 1_000_000, "burn_per_turn": 10_000,
                               "progress_delta": 10, "understaffed": True})
bv_bad = BD.vote("create y", {"affordable": False, "within_budget": False, "spent": 990_000,
                              "cap": 1_000_000, "burn_per_turn": 50_000,
                              "progress_delta": 0, "understaffed": False})
check("board approves on good evidence, blocks on bad",
      bv_good["approved"] and not bv_bad["approved"])
check("rationales carry live values, not constant labels",
      "runway" in bv_good["reasons"]["Prudence"]
      and bv_good["reasons"]["Prudence"] != bv_bad["reasons"]["Prudence"])
check("growth votes NO when progress is flat with a full fleet",
      bv_bad["ballots"]["Growth"] is False)

# IV.5: the market converts overflow instead of letting it all rot
S.connect()                                       # re-init the world (wiped in section 7)
cap_now = S.food_cap()
S._world_add("food", max(0, cap_now + 10_000 - S.world()["food"]))
gold_before = S.world()["gold"]
sold, got = S.trade_surplus()
check("surplus food is traded for gold before it rots",
      sold > 0 and S.world()["gold"] == gold_before + got, f"sold {sold} for {got} gold")

# VI.4: knowledge expires — only a bounded set of lessons steers decisions
for i in range(35):
    A.skill_add(9, f"filler lesson {stamp}-{i} for the expiry check", source="test")
A.skill_prune(30)
live = A.skills_top(100)
check("stale lessons stop steering (bounded live set)", len(live) <= 30,
      f"{len(live)} live lessons")
check("pruned lessons remain on the record (never deleted)", A.skills_count() > 30)

print("\n10. The map — every built thing has a place (the canvas projection)")
S._world_add("wood", 1_000)
before = len(S.map_state()["placements"])
ok_h, _ = S.build_structure("house")
ok_m2, _ = S.build_structure("mill")
m = S.map_state()
check("building assigns a tile on the map",
      ok_h and ok_m2 and len(m["placements"]) == before + 2,
      f"{len(m['placements'])} placements")
tiles = [(p["x"], p["y"]) for p in m["placements"]]
check("every placement is a unique in-bounds tile, never the town centre",
      len(set(tiles)) == len(tiles)
      and all(0 <= x < S.MAP_W and 0 <= y < S.MAP_H for x, y in tiles)
      and tuple(S.TOWN_CENTER) not in tiles)
placed = {}
for p in m["placements"]:
    placed[p["name"]] = placed.get(p["name"], 0) + 1
built_counts = {d["name"]: d["built"] for d in S.dev_catalog() if d["built"]}
check("the map mirrors the world's built counts (counts stay the oracle)",
      placed == built_counts, f"{placed}")
_c = S._conn()
_c.execute("DELETE FROM placements")
_c.commit()
_c.close()
m2 = S.map_state()
check("a world that predates the map backfills placements from built counts",
      len(m2["placements"]) == sum(built_counts.values())
      and {p["name"] for p in m2["placements"]} == set(built_counts))

print("\n11. Proximity — place matters: a camp on its ground's ring yields more")
mill_tile = next(p for p in m2["placements"] if p["name"] == "mill")
gfx, gfy = S.GROUNDS["food"]
check("a camp seeks the ring around its resource ground",
      max(abs(mill_tile["x"] - gfx), abs(mill_tile["y"] - gfy)) <= S.PROXIMITY_RADIUS
      and mill_tile["near"], f"mill at ({mill_tile['x']},{mill_tile['y']}), ground ({gfx},{gfy})")
house_tile = next(p for p in m2["placements"] if p["name"] == "house")
tcx, tcy = S.TOWN_CENTER
check("everything else grows from the town centre",
      max(abs(house_tile["x"] - tcx), abs(house_tile["y"] - tcy)) <= 2,
      f"house at ({house_tile['x']},{house_tile['y']})")
y_near = S.effective_yield("food")
exp = S.BASE["food"] * (1 + 0.5)
exp *= 1 + S.PROXIMITY_PCT / 100
tbf = S.terrain_bonus_tiles("food")
exp *= 1 + S.TERRAIN_PCT / 100 * tbf
check("the proximate camp pays its bonus on the yield",
      y_near == int(exp),
      f"yield {y_near} (mill 1.5x · ring +{S.PROXIMITY_PCT}% · {tbf} live berry tiles)")
taken_tiles = {(p["x"], p["y"]) for p in m2["placements"]}
check("grounds and the town centre are never built on",
      not (taken_tiles & ({tuple(S.TOWN_CENTER)} | set(S.GROUNDS.values()))))

print("\n12. The land — deterministic terrain that feeds, and wears out under, the economy")
t0 = S.terrain()
_c = S._conn()
_c.execute("DELETE FROM terrain")
S._terrain_init(_c)
_c.commit()
_c.close()
check("the land is founded deterministically (same seed, same world)",
      S.terrain() == t0 and len(t0) > 10, f"{len(t0)} tiles")
check("every ground grows its class nearby, and the pond exists",
      all(any(tl["cls"] == S.TERRAIN_KIND[r] for tl in t0) for r in S.RESOURCES)
      and any(tl["cls"] == "water" for tl in t0))
S._world_add("wood", 500)
ok_lc, _ = S.build_structure("lumber_camp")
tbw = S.terrain_bonus_tiles("wood")
y_wood = S.effective_yield("wood")
exp_w = S.BASE["wood"] * (1 + 0.5)
exp_w *= 1 + S.PROXIMITY_PCT / 100
exp_w *= 1 + S.TERRAIN_PCT / 100 * tbw
check("a camp works the live tiles around it into the yield",
      ok_lc and tbw > 0 and y_wood == int(exp_w),
      f"{tbw} live forest tiles → wood yield {y_wood}")
S.terrain_deplete("wood", amount=S.TERRAIN_STOCK)        # work one tile to nothing
check("worked-out land stops paying",
      S.terrain_bonus_tiles("wood") == tbw - 1 and S.effective_yield("wood") < y_wood,
      f"yield {y_wood} → {S.effective_yield('wood')}")
_reg = S.render_registry()
_built_kinds = {d["name"] for d in S.dev_catalog() if d["built"]}
check("the paint registry covers everything built, with rank and effect",
      _built_kinds <= set(_reg)
      and all("rank" in _reg[n] and _reg[n].get("effect") for n in _built_kinds))
check("water is never built on",
      not ({(p['x'], p['y']) for p in S.map_state()['placements']} & S.WATER))

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

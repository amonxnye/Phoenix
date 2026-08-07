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

# ── 10. Article VI.2 enforced: a source that isn't checkable doesn't steer ──
print("\n10. Evidence — citations are CHECKED, not just recorded")
import tempfile
_src_dir = tempfile.mkdtemp(dir=os.path.dirname(A.DB))
_src = os.path.join(os.path.basename(_src_dir), "note.txt")
with open(os.path.join(_src_dir, "note.txt"), "w") as _f:
    _f.write("Camps raise the yield of their resource by fifty percent in this world.")

v_ok = A.ingest("camps", _src, "camps raise yield", quote="raise the yield")
check("a resolvable source containing the quote VERIFIES", v_ok["verified"], v_ok["reason"])
v_ghost = A.ingest("ghost", "no-such-file.txt", "invented finding", quote="anything")
check("a fabricated source is refused", not v_ghost["verified"], v_ghost["reason"])
v_wrong = A.ingest("camps", _src, "camps double the yield", quote="double the yield")
check("a real source that does NOT contain the claim is refused",
      not v_wrong["verified"], v_wrong["reason"])
v_bare = A.ingest("camps", _src, "camps are good", quote="")
check("an unquoted assertion is never verified", not v_bare["verified"], v_bare["reason"])

_ver = A.external(20, verified_only=True)
_all = A.external(20)
check("unverified knowledge is kept on the record but excluded from steering",
      len(_all) > len(_ver) and all(x["verified"] for x in _ver),
      f"{len(_ver)} verified of {len(_all)} recorded")

# ── 11. relevance retrieval: the RIGHT lesson, not the newest ───────────────
print("\n11. Memory retrieval — relevance beats recency")
A.skill_add(50, "Gold bottlenecks the Castle advance; mine gold before proposing it.",
            source="test", trigger="relevance")
A.skill_add(51, "Housing raises the population cap; build houses when agents are capped.",
            source="test", trigger="relevance")   # newer, and irrelevant to gold
sit_gold = "Vision 'Ascend to the Castle Age'. The advance needs gold and we are short."
rel = [x["lesson"] for x in A.skills_relevant(sit_gold, 2)]
newest = [x["lesson"] for x in A.skills_top(2)]
check("a relevant OLD lesson outranks a newer irrelevant one",
      any("Gold bottlenecks" in l for l in rel), rel[0][:58] + "…" if rel else "none")
check("recency alone would have surfaced the wrong lesson",
      not any("Gold bottlenecks" in l for l in newest[:1]),
      "newest first: " + (newest[0][:48] + "…" if newest else "none"))
sit_house = "Population is capped at 5 with 5 agents; the fleet cannot grow."
rel2 = [x["lesson"] for x in A.skills_relevant(sit_house, 2)]
check("a different situation retrieves a different lesson",
      any("Housing raises" in l for l in rel2), rel2[0][:58] + "…" if rel2 else "none")
check("retrieval never leaves a decision without wisdom",
      len(A.skills_relevant("zzzz qqqq nomatchwords", 3)) > 0)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

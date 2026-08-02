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

# ── 6. permanent memory: the anchor survives a world reset ───────────────────
print("\n6. Permanence — wiping the game world never touches the memory")
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

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

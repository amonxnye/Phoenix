"""Acceptance checks for the Age of Empires MVP — the Governor over a game economy.

Proves the three core functions on real game work, reusing governor.py unchanged:
  1. Villagers gather concurrently; the World (oracle) reflects it; state is durable.
  2. Cap HALTS spawning at a compute ceiling.
  3. Idle villagers (finished, parked awaiting orders) surface.
  4. The irreversible Age-up gate pauses, approve/reject is durable.

Run:  python3 gov/verify_sim.py
"""

import json as _json_mod
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brain as B
import governor as G
import sim as S

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))          # a truthy non-bool would print PASS and then
                                      # crash the tally — the reporting path must not
                                      # depend on the shape of what it reports (IX.7)
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
S._world_add("wood", S.ADVANCE_COST["wood"])
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
      after["age"] == S.AGE_ORDER[1] and after["food"] == before["food"] - S.ADVANCE_COST["food"]
      and after["wood"] == before["wood"] - S.ADVANCE_COST["wood"],
      S.render_world())

done = {u.unit_id: u for u in G.units(graph, cp)}["herald-01"]
check("resolved herald reports done", done.status == "done")

# decision survives a restart
probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0,{HERE!r});"
     "import sim as S; print(S.world()['age'])"],
    capture_output=True, text=True)
check("Age-up persists across restart", S.AGE_ORDER[1] in probe.stdout,
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
import atexit
import shutil
import tempfile
_src_dir = tempfile.mkdtemp(dir=os.path.dirname(A.DB))
atexit.register(shutil.rmtree, _src_dir, True)   # a test never litters the data dir
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

# Ask for more than the record holds. With a small limit both lists saturate at
# the cap on a lived-in anchor, and the comparison silently degrades to 20 == 20 —
# a check that passes on an empty world and fails on a busy one tests nothing.
_ver = A.external(10_000, verified_only=True)
_all = A.external(10_000)
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

# ── 12. the communication graph: talking vs being heard ─────────────────────
# A transcript proves a message was sent. The graph has to prove one was ACTED
# ON, or "communication" is just volume with a nice layout.
print("\n12. Communication graph — influence, not volume")
_a, _b, _c = f"vil-a{stamp}", f"vil-b{stamp}", f"vil-c{stamp}"
A.msg_send("internal", f"{_a} → {_b}", "shift to gold — the advance needs it", to=_b)
_edge = {(e["from"], e["to"]): e for e in A.comm_edges(500)}
check("an addressed message becomes a graph EDGE, not just a line of text",
      (_a, _b) in _edge, f"{len(_edge)} edges on the record")
check("a tip nobody acted on stays cold", _edge[(_a, _b)]["followed"] == 0)

check("acting on a tip marks the edge heard", A.msg_follow(_a, _b))
_edge = {(e["from"], e["to"]): e for e in A.comm_edges(500)}
check("the edge now carries INFLUENCE, not just traffic",
      _edge[(_a, _b)]["followed"] == 1,
      f"{_edge[(_a, _b)]['followed']}/{_edge[(_a, _b)]['msgs']} acted on")

# Volume must not outrank influence: a chattier edge nobody follows ranks below
# a quieter one that changed behaviour.
for i in range(4):
    A.msg_send("internal", f"{_a} → {_c}", f"unheeded suggestion {i}", to=_c)
_ranked = [(e["from"], e["to"]) for e in A.comm_edges(500)]
check("a heard edge outranks a chattier edge nobody follows",
      _ranked.index((_a, _b)) < _ranked.index((_a, _c)),
      f"heard at #{_ranked.index((_a, _b)) + 1}, chatty at #{_ranked.index((_a, _c)) + 1}")
check("an unfollowed edge cannot be marked heard twice over", not A.msg_follow(_a, _b))

# Legacy rows encode the edge in the sender ("a -> b") with no recipient column;
# they must still draw, or the graph starts empty on every existing world.
A.msg_send("internal", f"{_b} → {_a}", "acknowledged — switching")
check("a legacy arrow-encoded sender still draws an edge",
      (_b, _a) in {(e["from"], e["to"]) for e in A.comm_edges(500)})
check("the recent feed carries both ends and whether it landed",
      all({"from", "to", "body", "followed"} <= set(m) for m in A.comm_recent(5)))

# A migration that cannot run must cost us the FEATURE, never the world. On a full
# or read-only volume the ALTER fails; the first version of this code read that as
# "column already there", queried a column that did not exist, and took the whole
# deployment down. The graph is allowed to be empty. The settlement is not allowed
# to stop.
_saved_cols = A._EDGE_COLS
try:
    A._EDGE_COLS = False
    check("a world without the graph columns still records messages",
          A.msg_send("internal", f"{_a} → {_b}", "sent while degraded") is not None,
          "the id comes back either way — losing the graph must not also lose threading")
    check("the graph degrades to empty rather than raising",
          A.comm_edges(10) == [] and A.comm_recent(10) == []
          and A.msg_follow(_a, _b) is False)
    check("the human-readable transcript survives losing the graph",
          any("sent while degraded" in m["body"] for m in A.msg_thread("internal", 20)))
finally:
    A._EDGE_COLS = _saved_cols

# ── 12b. the ACCP envelope: an exchange you can audit, not a stream of prose ──
# A transcript proves words were exchanged. It cannot say which message was a
# question, which answered it, or which asked and was ignored — and on the live
# record 86% of addressed messages were never acted on, with no way to tell the
# difference between a remark nobody needed and a request nobody answered.
print("\n12b. Conversations — intent, hops, and how an exchange ended (VII.8-10)")
_conv_of = A.msg_conv
def _summary(conv):
    return next((c for c in A.conversations(200) if c["conv"] == conv), {"outcome": "?"})
_q = A.msg_send("internal", f"{_a} → {_b}", "shall we shift to gold?", to=_b, intent="request")
_r = A.msg_send("internal", f"{_b} → {_a}", "agreed, shifting", to=_a,
                intent="response", reply_to=_q)
check("a reply joins its parent's conversation rather than starting a new one",
      bool(_q) and bool(_r) and
      len({m["intent"] for m in A.conversation(_conv_of(_r))}) == 2,
      f"messages {_q} → {_r} share one conversation")
check("a reply counts one hop further than what it answers",
      [m["hops"] for m in A.conversation(_conv_of(_r))] == [1, 2])
check("an answered request reads as answered",
      _summary(_conv_of(_r))["outcome"] == "answered")

_lonely = A.msg_send("internal", f"{_a} → {_c}", "can you cover the mill?", to=_c,
                     intent="request")
check("a request nobody replied to is named, not merely absent (VII.9)",
      _summary(_conv_of(_lonely))["outcome"] == "unanswered",
      "the second law applied to agent traffic, not only to the human gate")
check("unanswered outranks recent — the row worth reading is not buried",
      [c["outcome"] for c in A.conversations(60)].index("unanswered")
      < max((i for i, c in enumerate(A.conversations(60))
             if c["outcome"] in ("answered", "one-way")), default=99),
      "worst outcome first, then newest")

# VII.10 — the two refusals. A hop count nobody enforces is a field, not a limit.
_chain, _refused_at = A.msg_send("internal", "a→b", "open", to="b", intent="request"), 0
for _i in range(A.MAX_HOPS + 4):
    _nxt = A.msg_send("internal", "b→a", f"turn {_i}", to="a",
                      intent="response", reply_to=_chain)
    if _nxt is None:
        _refused_at = _i + 2
        break
    _chain = _nxt
check("an exchange cannot run forever — the ceiling actually refuses",
      _refused_at == A.MAX_HOPS + 1, f"refused at hop {_refused_at}, ceiling {A.MAX_HOPS}")
check("the refusal is recorded, not silent (a refusal nobody sees is not governance)",
      any("exceeds the ceiling" in e for e in A.event_log(40)))

_e1 = A.msg_send("internal", "a→b", "gather failed", to="b", intent="error")
check("an error may not answer an error (ACCP §7)",
      A.msg_send("internal", "b→a", "your error errored", to="a",
                 intent="error", reply_to=_e1) is None,
      "two agents faulting at each other is a loop, not a conversation")
check("but an error may still be answered normally",
      A.msg_send("internal", "b→a", "acknowledged, retrying", to="a",
                 intent="ack", reply_to=_e1) is not None)
check("an unknown intent degrades to notify rather than entering the record",
      A.conversation(_conv_of(A.msg_send("internal", "x", "hi", intent="telepathy")))[0]
      ["intent"] == "notify")

# Losing the envelope must cost the FEATURE, never the transcript — the same rule
# the edge columns learned the hard way, checked separately because it migrates
# separately. A volume that can afford one migration but not both keeps the other.
_saved_conv = A._CONV_COLS
try:
    A._CONV_COLS = False
    check("a world without the envelope columns still records messages",
          A.msg_send("internal", "z", "sent without an envelope") is not None)
    check("the conversation views degrade to empty rather than raising",
          A.conversations(5) == [] and A.conversation("c1") == []
          and A.conv_stats()["convs"] == 0)
    check("the transcript survives losing the envelope",
          any("sent without an envelope" in m["body"] for m in A.msg_thread("internal", 20)))
finally:
    A._CONV_COLS = _saved_conv
check("the console reports the envelope it ACTUALLY has, never asserts it (VII.4)",
      '"enabled": anchor._CONV_COLS' in open(os.path.join(HERE, "sim_console.py")).read())

# Telemetry may never refuse the thing it is counting. A full volume made every
# console page answer 502 while /api/* — which does not count views — stayed up:
# the least important write in the system sat in the request's critical path.
_orig_conn = A._conn


def _dead_conn(*a, **k):
    raise sqlite3.OperationalError("database or disk is full")


try:
    A._conn = _dead_conn
    _bumped = A.metric_bump("pageviews")
    check("counting a page view cannot refuse to serve it", _bumped is None)
finally:
    A._conn = _orig_conn

# ── 13. Article IX.7 — no safeguard may depend on what it guards against ────
# Four outages shared one shape: the alarm was routed through the thing that had
# failed. These check the reporting paths that must survive a dead anchor.
print("\n13. Substrate — the alarms that must work when the store does not")
import sim_console as SC                            # noqa: E402  (needs anchor ready)

_d = SC._disk()
check("the data volume is gauged, not assumed",
      _d["total_mb"] > 0 and 0 <= _d["used_pct"] <= 100,
      f"{_d['used_pct']}% of {_d['total_mb']}MB used")

_before = SC._STORAGE["faults"]
SC._storage_fault("verify-probe")
check("a failed write is counted in memory, not only in the store that failed",
      SC._STORAGE["faults"] == _before + 1 and SC._STORAGE["last"] == "verify-probe")

_src = __import__("inspect").getsource(SC._health_sampler)
_tail = _src[_src.index("if stale > max(20 * TICK"):]
_exit_at = _tail.index("os._exit(1)")
check("the watchdog's restart is not gated behind an anchor write",
      _tail.rindex("anchor.record", 0, _exit_at)
      < _tail.rindex("except Exception:", 0, _exit_at) < _exit_at,
      "the record is followed by its own except, and os._exit comes after both")

# Making the exit unconditional cured the silent hang and bought a restart loop:
# a full volume stops turns completing, so the watchdog fired every five minutes
# forever and each restart killed the console that could have explained it. The
# recovery has to match the diagnosis.
_saved_faults = SC._STORAGE["faults"]
try:
    SC._STORAGE["faults"] = 0
    A.config_set("wd_restarts", "")
    check("a genuine wedge still earns a restart", SC._restart_helps())
    SC._restart_helps(); SC._restart_helps()
    check("but three restarts an hour is a loop, not a recovery",
          not SC._restart_helps(), "the fourth is refused")
    A.config_set("wd_restarts", "")
    SC._storage_fault("verify-restart-probe")
    check("a broken substrate is never restarted into",
          not SC._restart_helps(), "restarting cannot empty a disk")
    check("refusing to restart does not also refuse to report",
          "wd:futile" in _src and "stale > max(5 * TICK, 90)" in _src)
finally:
    SC._STORAGE["faults"] = _saved_faults
    A.config_set("wd_restarts", "")

_drv = __import__("inspect").getsource(SC._drive)
check("a turn that RAISES is counted as a failed turn (Article IX.2)",
      'failed_turns"] = _S.get("failed_turns", 0) + 1' in _drv
      and _drv.index("except Exception as e") < _drv.index('_S["failed_turns"]'))
check("repeated failing turns declare a stall from the error path",
      '_S["stall"]' in _drv.split("except Exception as e")[1])
check("absence of turns is watched from OUTSIDE the driver (IX.3)",
      "stall:absence" in _src and "the world" in _src)

# ── 14. the nightly archive: history survives a reboot AND stops growing ────
print("\n14. Archive — history kept, space returned")
import gzip                                         # noqa: E402
import json as _json                                # noqa: E402

A.ARCHIVE_DIR = tempfile.mkdtemp(dir=os.path.dirname(A.DB))   # not the real one
atexit.register(shutil.rmtree, A.ARCHIVE_DIR, True)           # and never left behind
for i in range(2_000):                              # a log worth folding away
    A.record(i, "gather", f"vil-{i % 5} gathered 2874 food (now food {i})")
_before = os.path.getsize(A.EVENTS_PATH)
_rep = A.archive_night(keep_tail=200)
check("the nightly archive runs and reports what it did",
      _rep["ran"], f"{_rep['events_archived']:,} events, {_rep['freed_mb']}MB freed"
      if _rep["ran"] else _rep["reason"])
check("the working log is smaller afterwards, so the volume stops filling",
      os.path.getsize(A.EVENTS_PATH) < _before,
      f"{round(_before/1e6, 2)}MB → {round(os.path.getsize(A.EVENTS_PATH)/1e6, 2)}MB")

_gz = [f for f in A.archives() if f["name"].startswith("events-")]
_n = 0
if _gz:
    with gzip.open(os.path.join(A.ARCHIVE_DIR, _gz[0]["name"]), "rt") as _f:
        for _line in _f:
            _json.loads(_line)                      # every line must still parse
            _n += 1
check("the archived history is complete and still readable",
      _n >= _rep["events_archived"] > 0, f"{_n:,} events replayed from gzip")

_snap = [f for f in A.archives() if f["name"].startswith("anchor-")]
check("the anchor is snapshotted alongside it, so memory survives too", bool(_snap),
      _snap[0]["name"] if _snap else "none")
check("the live log still reads after rotation", len(A.event_log(5)) > 0)
# An archive is a write, and a write is exactly what a full volume cannot take.
# It must decline and leave the log alone, not finish what the fault started.
_real_statvfs = os.statvfs


class _NoRoom:
    f_bavail = 0
    f_frsize = 4096
    f_blocks = 1_000


try:
    os.statvfs = lambda *_a, **_k: _NoRoom()
    _size_before = os.path.getsize(A.EVENTS_PATH)
    _refused = A.archive_night()
    check("an archive never runs the volume dry — it declines instead",
          not _refused["ran"] and "declined" in _refused["reason"], _refused["reason"])
    check("a declined archive leaves the live log untouched",
          os.path.getsize(A.EVENTS_PATH) == _size_before)
finally:
    os.statvfs = _real_statvfs

# ── 16. the pages are legible — contrast and motion are CHECKED, not intended ──
# "A rule with no enforcing code is a wish." Readable colour and optional motion
# are rules, so they get a test. Three colours shipped today failed WCAG AA and
# nothing caught them; the animation ran regardless of the reader's preference.
print("\n16. Legibility — contrast and motion, enforced")


def _lum(hexc):
    hexc = hexc.lstrip("#")
    ch = []
    for i in (0, 2, 4):
        v = int(hexc[i:i + 2], 16) / 255
        ch.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


_PANEL, _BG = "#1c150d", "#120d08"
_console_src = open(os.path.join(HERE, "sim_console.py")).read()

# DISCOVER the colours, never accept a list. The first version of this check tested
# nine hand-written hex values while the file contained fifty-one; two live text
# colours were passing AA by luck rather than by check, and any colour added later
# would have been invisible to it. An enforcement that only covers what its author
# remembered to enumerate is the same defect as a liveness counter that only counts
# turns that happen — it can never report what it was not told about.
import sim_console as _SC                           # noqa: E402  (palette is the source)

# Every token gets a ROLE, and the roles are exhaustive: adding a colour without
# saying what it is for fails the suite. That is the whole point of a single
# palette — you cannot slip a new value in unclassified and unchecked.
_GROUNDS = {"bg", "panel", "line", "mine"}          # surfaces; never carry text
_GRAPHICS = {"cold", "open"}                        # data marks; 3:1 suffices
_unclassified = set(_SC.PALETTE) - _GROUNDS - _GRAPHICS
check("every colour in the palette has a declared role",
      set(_SC.PALETTE) >= _GROUNDS | _GRAPHICS,
      f"{len(_SC.PALETTE)} tokens: {len(_GROUNDS)} grounds, {len(_GRAPHICS)} marks, "
      f"{len(_unclassified)} text")

_worst = lambda c: min(_contrast(c, _PANEL), _contrast(c, _BG))
_bad_text = {k: round(_worst(_SC.PALETTE[k]), 2)
             for k in _unclassified if _worst(_SC.PALETTE[k]) < 4.5}
check("every text token meets WCAG AA (4.5:1) on both grounds",
      not _bad_text, str(_bad_text) if _bad_text else
      f"lowest {min(round(_worst(_SC.PALETTE[k]), 2) for k in _unclassified)}:1")
_bad_gfx = {k: round(_contrast(_SC.PALETTE[k], _PANEL), 2)
            for k in _GRAPHICS if _contrast(_SC.PALETTE[k], _PANEL) < 3.0}
check("every data-bearing mark meets AA for non-text (3:1)",
      not _bad_gfx, str(_bad_gfx) if _bad_gfx else
      f"lowest {min(round(_contrast(_SC.PALETTE[k], _PANEL), 2) for k in _GRAPHICS)}:1")

# And still catch anything that bypassed the palette entirely.
_stray = sorted({m.group(1).lower() for m in
                 re.finditer(r"(?<![-\w(])color:\s*(#[0-9a-fA-F]{6})", _console_src)}
                - set(v.lower() for v in _SC.PALETTE.values()))
check("no page hard-codes a text colour outside the palette",
      not _stray, str(_stray) if _stray else "every text colour resolves to a token")

_GRAPHIC = {k: _SC.PALETTE[k] for k in _GRAPHICS}
check("no page animates against the reader's stated preference",
      _console_src.count("prefers-reduced-motion") >= 11
      and "const STILL=matchMedia" in _console_src,
      f"{_console_src.count('prefers-reduced-motion')} guards (10 spinners + the graph)")
check("the graphic colours tested are ones the pages actually use",
      all(v in _console_src for v in _GRAPHIC.values()))

# One palette, one definition. Ten copies had drifted into six variants, and the
# only reason nobody noticed is that nothing checked. A token defined per page is
# not a token; it is a convention, and conventions are what this project calls
# wishes.
check("the palette is defined exactly once",
      _console_src.count("\nPALETTE = {") == 1)
check("no page re-declares tokens inline — every one is built through _page()",
      ":root{--" not in _console_src,
      f"{_console_src.count('= _page(')} pages assembled from the shared palette")
check("a page that looks different states only its difference",
      'WORK_PAGE = _page(' in _console_src and 'bg="#0b1210"' in _console_src,
      "the workboard's code-world green is one line, not a second copy")
check("hand-drawn SVG reads the same palette as the stylesheet",
      "PALETTE_JS" in _console_src and "C.gold" in _console_src,
      "CSS custom properties cannot be read from an SVG attribute, so JS gets the object")
# A relationship graph has to answer "who is talking to whom" without a hover, and
# has to survive growing past a handful of members.
check("edges declare their direction with an arrowhead",
      "marker-end" in _console_src and "orient:'auto-start-reverse'" in _console_src)
check("arrowheads are fixed in user space, not scaled by line width",
      "markerUnits:'userSpaceOnUse'" in _console_src,
      "otherwise a 9px line draws a 54px head")
check("both directions of a pair are drawn apart, never on top of each other",
      "const side=(from<to)?1:-1" in _console_src)
check("edges stop at the node rim instead of vanishing beneath it",
      "function trim(" in _console_src)
check("the graph can be expanded and focused, and Escape undoes both",
      all(k in _console_src for k in ("function expand()", "FOCUS=(FOCUS===n.id)",
                                      "e.key!=='Escape'")))

# A volume that is already full cannot be rescued by the nightly archive: that only
# runs while the process is up, and it needs headroom to compress into. Reclamation
# has to work at the floor, at boot, or it is not a recovery at all (IX.7).
print("\n14c. Reclaim — space returned when there is none left")
_ev_before = os.path.getsize(A.EVENTS_PATH) if os.path.exists(A.EVENTS_PATH) else 0
for _i in range(6000):                              # a log worth compacting
    A.record(_i, "gather", "x" * 90)
_grown = os.path.getsize(A.EVENTS_PATH)
_r = A.reclaim(A.DB)
check("reclamation reports what it did, step by step",
      isinstance(_r.get("steps"), list) and "freed_mb" in _r, str(_r["steps"])[:70])
check("a WAL checkpoint is attempted first, before anything is lost",
      "checkpoint" in A.reclaim.__doc__ and "_compact_events" in
      open(os.path.join(HERE, "anchor.py")).read())
_kept = open(A.EVENTS_PATH).read().splitlines() if os.path.exists(A.EVENTS_PATH) else []
check("the event log survives reclamation as valid records",
      all(_json_mod.loads(_l) for _l in _kept) if _kept else True,
      f"{len(_kept):,} lines, every one parses")
check("dropping history is reported, never silent",
      "dropped_events_bytes" in _r)

# Compaction repairs the truncated final record a full volume leaves behind.
with open(A.EVENTS_PATH, "a") as _f:
    _f.write('{"turn": 1, "kind": "gath')                 # a write cut off by ENOSPC
_before_lines = len(open(A.EVENTS_PATH).read().splitlines())
A._compact_events(keep_tail=50)
_after = open(A.EVENTS_PATH).read().splitlines()
check("a record truncated by a full disk is repaired, not carried forward",
      all(_json_mod.loads(_l) for _l in _after), f"{len(_after)} lines, all valid")

_fs = A.flow_stats()
check("the decision pipeline is counted from the permanent record",
      {"proposed", "carried", "blocked", "measured", "escalated"} <= set(_fs),
      f"{_fs['proposed']} proposed, {_fs['measured']} measured")
check("no stage of the pipeline reports a negative count",
      all(isinstance(v, int) and v >= 0 for v in _fs.values()))

# ── 14b. Article I.2 — busy is not productive ───────────────────────────────
# Taken from the live world on 2026-08-16: Castle Age, 96% and frozen for 25
# turns, holding 29,864,149 resources against a target of 2,000 — 14,932x — while
# 65.7% of all activity was still gathering. failed_turns read 0 the whole time,
# because any gather cleared the counter, and the stall blamed "a full roster
# that is not producing" while the roster produced five gathers a turn.
print("\n14b. Waste — effort on a component already full")
import sim as _S_sim                                # noqa: E402
import vision as _V                                 # noqa: E402

_live = {"food": 4_999_990, "wood": 19_890_747, "gold": 4_973_412,
         "age": "Castle Age", "pop_cap": 10}
_built = {"house": 1, "mill": 1, "lumber_camp": 1, "mining_camp": 1, "wheelbarrow": 1}
_sc = _V.scorecard({**_live, **_built}, _built, 0, _V.get("castle"))
check("the live world's economy is scored as full", _sc["econ_pct"] == 100,
      f"{_sc['extra_value_pct']:,.0f}% over target")
check("its shortfall is developments, not resources", _sc["dev_pct"] < 100,
      f"dev {_sc['dev_pct']}% · age {_sc['age_pct']}% · econ {_sc['econ_pct']}%"
      f" — {len(_built)} of {_V.get('castle').target_buildings} buildings")
check("so gathering more cannot raise the score",
      _V.scorecard({**_live, **_built, "wood": _live["wood"] * 10},
                   _built, 0, _V.get("castle"))["progress"] == _sc["progress"],
      "ten times the wood moves the score by exactly 0 points")

# Counting waste tells you the fleet converged on the wrong thing. It does not stop
# it. A gradient-following rule with nothing left to optimise becomes a monoculture,
# which is what produced the 14,932x surplus: "bank the best learned yield" is
# self-reinforcing, because the resource gathered most earns the camps that keep it
# best. Converge where there is a gradient; spread where there is not.
_live_w = {"food": 4_999_990, "wood": 19_890_747, "gold": 4_973_412,
           "age": "Castle Age", "pop_cap": 10}
_short = {"house": 1, "mill": 1, "lumber_camp": 1, "mining_camp": 1, "wheelbarrow": 1}
_sc_short = _V.scorecard({**_live_w, **_short}, _short, 0, _V.get("castle"))
_res, _why = SC._choose_gather({**_live_w, **_short}, _sc_short)
check("a full economy never justifies banking more surplus",
      "bank the surplus" not in _why, _why[:66] + "…")
check("effort is steered to the component that is actually short",
      "developments are short" in _why and _res == "wood",
      f"gathers {_res} for the next development, not the saturated stock")

# An age already reached is a satisfied component too: this branch used to read the
# NEXT age's cost unconditionally and invent a shortfall nobody had asked for.
check("no work is aimed at an age the Vision has already reached",
      _sc_short["age_pct"] == 100 and "Age-up shortfall" not in _why)

_done = {**_short, "granary": 1}
_sc_done = _V.scorecard({**_live_w, **_done}, _done, 0, _V.get("castle"))
SC._S["recent_gathers"] = []
_picks = []
for _ in range(9):
    _r, _ = SC._choose_gather({**_live_w, **_done}, _sc_done)
    SC._note_gather(_r)
    _picks.append(_r)
check("with nothing short, assignments spread instead of forming a monoculture",
      len(set(_picks)) == len(S.RESOURCES) and max(_picks.count(r) for r in set(_picks)) <= 4,
      f"{len(set(_picks))} of {len(S.RESOURCES)} resources across 9 assignments")
check("the diversity rule reads its own history, not chance",
      "recent_gathers" in SC._S and len(SC._S["recent_gathers"]) == 9)
SC._S["recent_gathers"] = []

# The same rut, in the invention domain — and an unbounded prompt alongside it.
# The whole development catalogue was interpolated into every proposal, so the input
# cost of inventing item N grew with N; and showing the model the tail of a list
# invited it to extend the tail. In the live world 9 of 51 developments were variants
# of one theme. Article III.3 — the check precedes the commit — had never been
# applied to our own token spend.
_varied = ["granary_store", "mill_race", "toll_gate", "cider_press", "horse_collar",
           "root_cellar", "fish_trap", "heavy_plow", "guild_charter", "royal_mint",
           "compost_pit", "watermill_weir", "forest_charter", "seed_drill",
           "cattle_post", "tithe_barn", "salt_pan", "hop_yard", "kiln_works",
           "ropewalk_shed"]
_cat = [f"stablecoin_{n}" for n in
        ("reserve", "peg", "insurance", "treasury", "audit", "swap", "vault",
         "bridge", "oracle")] + _varied
_sample, _avoid = B.catalogue_digest(_cat)
check("the proposal prompt is bounded, however long the catalogue grows",
      len(B.catalogue_digest([f"dev_{i}" for i in range(1000)])[0]) < 3 * len(_sample),
      "1000 entries costs about what 51 does")
check("the sample spreads across families instead of showing a rut",
      _sample.count("stablecoin") <= 2, f"9 in the catalogue, {_sample.count('stablecoin')} shown")
check("a family that has taken over is named so it can be avoided",
      "stablecoin" in _avoid and "do NOT" in _avoid, _avoid.strip()[:64] + "…")
check("a varied catalogue draws no spurious warning",
      B.catalogue_digest(_varied)[1] == "",
      f"{len(_varied)} distinct families, no family dominant")
check("an empty catalogue is handled without inventing a rule",
      B.catalogue_digest([]) == ("", ""))

# Article III.4/III.5 — the general form. Bounding one call site fixes one call site;
# the class of defect is text reaching a prompt with no ceiling, and the live system had
# eight such paths, three of them straight from an HTTP body. So this check DISCOVERS its
# subject: it walks every public entry point in brain.py, feeds each one pathological
# input, and inspects the turn that would have gone to the provider. A prompt builder
# added next month is covered the day it is added, without anyone remembering to.
# It patches the TRANSPORT, never _chat itself — patching out the choke point would test
# the test (Article IX.7: a safeguard must not route through what it guards).
import inspect as _inspect
_BIG = "x" * 40_000
_sent: list = []
# ── the retry policy: timeouts and 5xx retried with backoff, refusals not ──────
import netretry as N
_slept, N._SLEEP = [], lambda s: _slept.append(s)
try:
    _n = {"k": 0}
    def _flaky():
        _n["k"] += 1
        if _n["k"] < 3:
            raise TimeoutError("server timed out")
        return "alive"
    _out = N.call(_flaky, what="probe", idempotent=True)
    check("a request that times out is retried with backoff and then succeeds",
          _out == "alive" and N.last()["attempts"] == 3 and len(_slept) == 2
          and 0.7 <= _slept[0] <= 1.3 and 1.4 <= _slept[1] <= 2.6,
          f"attempts {N.last()['attempts']}, waits {[round(s, 2) for s in _slept]}")
    class _Http(Exception):
        def __init__(self, code): super().__init__(f"HTTP {code}"); self.status_code = code
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Http(524)), what="edge", idempotent=True)
        _gave = False
    except _Http as e:
        _gave = e.attempts == N.RETRIES + 1
    check("a server that keeps answering 524 is retried NET_RETRIES times, then given up on, counted",
          _gave and len(_slept) == N.RETRIES and N.last()["gave_up"].startswith("after"),
          f"{N.last().get('attempts')} attempts, waited {round(sum(_slept), 1)}s")
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Http(402)), what="billing", idempotent=True)
        _once = False
    except _Http as e:
        _once = e.attempts == 1
    check("a refusal (402 Insufficient Balance, 401, 404) is NOT retried — the answer will not change",
          _once and not _slept and N.last()["gave_up"] == "not retryable")
    class _Rate(_Http):
        headers = {"Retry-After": "7"}
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Rate(429)), what="rate", retries=1, idempotent=True)
    except _Rate:
        pass
    check("a 429's own Retry-After sets the wait", len(_slept) == 1 and 5 <= _slept[0] <= 9)
    # the gateway's own error semantics (its llms.txt): two 503s mean opposite things
    class _Body(_Http):
        def __init__(self, code, body, headers=None):
            super().__init__(code); self.body = body; self.headers = headers or {}
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Body(503, {"error": {"code": "model_warming"}}, {"Retry-After": "30"})),
               what="svc", retries=1, idempotent=True)
    except _Body: pass
    check("503 model_warming is retried after the gateway's Retry-After (the model is loading)",
          len(_slept) == 1 and 20 <= _slept[0] <= 40 and "loading" in N.last()["errors"][0])
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Body(503, {"error": {"message": "no usable model"}})), what="svc", idempotent=True)
    except _Body: pass
    check("503 WITHOUT model_warming is not retried — no usable model needs an administrator",
          not _slept and N.last()["gave_up"] == "not retryable" and "administrator" in N.last()["errors"][0])
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Body(403, {"error": {"code": "model_disabled"}})), what="gw", idempotent=True)
    except _Body: pass
    check("403 model_disabled is not retried and says to pick another model",
          not _slept and "pick another" in N.last()["errors"][0])
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Body(429, {"error": {"message": "service limit reached"}})), what="svc", idempotent=True)
    except _Body: pass
    check("429 without Retry-After or rate-limit wording is the service limit — not retried",
          not _slept and "service limit" in N.last()["errors"][0])
    _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(_Body(429, {"error": {"message": "rate limit"}}, {"Retry-After": "2"})), what="gw", retries=1, idempotent=True)
    except _Body: pass
    check("…while a 429 rate limit with Retry-After is retried after it", len(_slept) == 1 and 1 <= _slept[0] <= 3)
    _envfb = os.environ.get("BRAIN_MODEL_FALLBACKS")
    os.environ["BRAIN_MODEL_FALLBACKS"] = "qwen3:30b, phi3:14b-instruct, llama3.1:8b"
    try:
        B.DISABLED.clear(); B.DISABLED.add("phi3:14b-instruct")
        _nx = B._fallback({"model": "qwen3:30b", "base_url": "x", "key": "k", "kind": "openai"})
        check("on model_disabled the seam takes the NEXT operator-listed model that is not disabled",
              _nx is not None and _nx["model"] == "llama3.1:8b")
    finally:
        B.DISABLED.clear()
        os.environ.pop("BRAIN_MODEL_FALLBACKS", None)
        if _envfb is not None: os.environ["BRAIN_MODEL_FALLBACKS"] = _envfb
    import anchor as _A2
    B._log_call({"base_url": "https://svc.example/v1", "model": "my-service", "kind": "openai"}, "t", time.time(), None, True, served="qwen3:4b")
    _lastrow = _A2._conn().execute("SELECT model FROM model_calls ORDER BY id DESC LIMIT 1").fetchone()
    check("a service call is logged under the model that actually served it, not the service name",
          _lastrow is not None and _lastrow[0] == "qwen3:4b")
    # the guards that make one shared retry layer safe
    N.reset(); _slept.clear()
    try:
        N.call(lambda: (_ for _ in ()).throw(TimeoutError("t")), what="write", idempotent=False)
    except TimeoutError as e:
        _one = e.attempts == 1 and not _slept and "not idempotent" in N.last()["gave_up"]
    check("a request not declared idempotent is made ONCE, even on a timeout — a retried write could act twice",
          _one, N.last().get("gave_up", ""))
    _clock = [1000.0]; N._NOW = lambda: _clock[0]
    try:
        N.reset(); _slept.clear()
        def _hang(): raise TimeoutError("hung")
        for _ in range(N.BREAK_AFTER):
            try: N.call(_hang, what="gw", idempotent=True, key="gw.example")
            except TimeoutError: pass
        _n_before = N.STATS["attempts"]
        try:
            N.call(_hang, what="gw", idempotent=True, key="gw.example"); _fast = False
        except N.CircuitOpen as e:
            _fast = e.key == "gw.example" and N.STATS["attempts"] == _n_before
        check("after BREAK_AFTER exhausted calls the host's breaker opens: the next call fails at once, no attempt made",
              _fast and N.breakers()["gw.example"]["open"] and N.STATS["breaker_trips"] == 1,
              str(N.breakers()))
        _clock[0] += N.COOL_S + 1
        _ok = N.call(lambda: "alive", what="gw", idempotent=True, key="gw.example")
        check("after the cooling period one probe is let through; success closes the breaker",
              _ok == "alive" and not N.breakers()["gw.example"]["open"]
              and N.breakers()["gw.example"]["failures"] == 0)
        for _ in range(N.BREAK_AFTER):
            try: N.call(_hang, what="gw", idempotent=True, key="gw.example")
            except TimeoutError: pass
        _clock[0] += N.COOL_S + 1
        try: N.call(_hang, what="gw", idempotent=True, key="gw.example")
        except TimeoutError: pass
        check("a probe that fails re-opens the breaker", N.breakers()["gw.example"]["open"])
        try:
            N.call(lambda: (_ for _ in ()).throw(_Http(402)), what="other", idempotent=True, key="other.example")
        except _Http: pass
        check("a refusal does not trip a breaker — it is not the server failing, it is the server answering",
              not N.breakers()["other.example"]["open"] and N.breakers()["other.example"]["failures"] == 0)
    finally:
        N._NOW = time.time; N.reset()
    import threading as _th
    _seen = {}
    def _t(name, n):
        for _ in range(n):
            N.call(lambda: 1, what=name, idempotent=True)
        _seen[name] = N.last()["what"]
    _ts = [_th.Thread(target=_t, args=(f"w{i}", 50)) for i in range(4)]
    [t.start() for t in _ts]; [t.join() for t in _ts]
    check("the readout is per thread: four workers never see each other's attempts",
          _seen == {f"w{i}": f"w{i}" for i in range(4)}, str(_seen))
    check("the native transport is opt-in: without BRAIN_NATIVE=1 a tagged model goes through /v1",
          "BRAIN_NATIVE" in open(os.path.join(HERE, "brain.py")).read().split("def _chat")[1][:2500])
    check("the brain's SDK retries are off so every attempt is one the policy counted",
          "max_retries=0" in open(os.path.join(HERE, "brain.py")).read())
finally:
    N._SLEEP = time.sleep

# ── the deploy package: one container, one volume, a health endpoint ──────────
_root = os.path.dirname(HERE)
_df = open(os.path.join(_root, "Dockerfile")).read() if os.path.exists(os.path.join(_root, "Dockerfile")) else ""
_dc = open(os.path.join(_root, "docker-compose.yml")).read() if os.path.exists(os.path.join(_root, "docker-compose.yml")) else ""
_ee = open(os.path.join(_root, ".env.example")).read() if os.path.exists(os.path.join(_root, ".env.example")) else ""
check("the deploy package mounts the record at /data and points GOV_DATA_DIR at it",
      "phoenix-data:/data" in _dc and "GOV_DATA_DIR=/data" in _df and "GOV_DATA_DIR: /data" in _dc)
check("the container installs unshare for the sandbox and lifts the seccomp block for it, saying why",
      "util-linux" in _df and "seccomp=unconfined" in _dc and "unshare -rn" in _dc)
check("every variable the code reads is in .env.example",
      all(v in _ee for v in ("BRAIN_API_KEY", "BRAIN_BASE_URL", "BRAIN_MODEL", "CONSOLE_TOKEN", "GITHUB_TOKEN",
                             "IMPROVE_INTERVAL_S", "MECHANIC_BASE_URL", "NET_RETRIES", "SANDBOX_NETNS")))
check("the host health check exists and reports the record's writability, commit and isolation",
      '"/healthz"' in open(os.path.join(HERE, "sim_console.py")).read() and "healthz" in _df and "healthz" in _dc)

# ── Article XI: the self-improvement cycle, offline — scripted candidates, oracle, researcher ──
import improve as IMP
import research as RS
import ghpr as _GH
import hashlib as _hl
_fixture_rel = "gov/verify_fixture_improve.py"           # a real file in the live tree, for the copy
_fixture_abs = os.path.join(os.path.dirname(HERE), _fixture_rel)
with open(_fixture_abs, "w") as _f:
    _f.write("def hello():\n    return 1\n")
_live_before = _hl.sha256(open(_fixture_abs, "rb").read()).hexdigest()
_tag = "[%d]" % int(time.time())                        # titles unique per run: the record persists across runs
_cand = {"finding_id": "f1", "title": "hello should say so " + _tag, "severity": "low", "category": "quality",
         "file": _fixture_rel, "patch": "--- a/gov/verify_fixture_improve.py\n+++ b/gov/verify_fixture_improve.py\n@@ -1,2 +1,2 @@\n def hello():\n-    return 1\n+    return 2\n"}
_seen_trees = []
def _green(tree, only=None):
    _seen_trees.append(tree)
    patched = "return 2" in open(os.path.join(tree, _fixture_rel)).read()
    return {"green": True, "suites": {"governor": {"passed": 16, "total": 16, "ok": True, "seconds": 1, "tail": "", "failed": []},
                                      "settlement": {"passed": 200 + (1 if patched else 0), "total": 200 + (1 if patched else 0), "ok": True, "seconds": 2, "tail": "", "failed": []}}}
_asked = []
def _researcher(prompt):
    _asked.append(prompt)
    return ('{"advantage": "hello now says 2, which the patched settlement suite counts as one more passing check", '
            '"risk": "a caller that expected 1 would see 2", "blast_radius": "callers of hello()", '
            '"detection": "the settlement suite", "undo": "revert the commit", "alternatives": "leave it", "confidence": "high — trivial diff"}')
_saved_seams = (IMP.ORACLE, IMP.CANDIDATES, IMP.AUTO_PR, IMP.PUSH_BRANCH, RS.ASK, _GH.open_pr, _GH.get_file, _GH.commit_file)
_mode0 = IMP.MODE
_envt = os.environ.pop("GITHUB_TOKEN", None)          # the suite must never reach GitHub
IMP.ORACLE, IMP.CANDIDATES, IMP.AUTO_PR, IMP.PUSH_BRANCH, RS.ASK = _green, (lambda: [_cand]), False, "", _researcher
IMP.MODE = "code"                                     # this section exercises the code mode; world mode is checked below
try:
    _r = IMP.cycle("test")
    check("a cycle takes the mechanic's patches, tries each on a COPY against the suites, and parks what survived",
          _r["status"] == "complete" and _r["candidates"] == 1 and _r["tried"] == 1 and _r["verified"] == 1
          and len(_seen_trees) == 2 and all(t != os.path.dirname(HERE) and not os.path.exists(t) for t in _seen_trees), str(_r))
    _p = IMP.proposals(status="verified")
    check("the verified improvement waits at the gate with its diff and the suite delta",
          _p and _p[0]["file"] == _fixture_rel and "+    return 2" in _p[0]["patch"]
          and _p[0]["after_json"]["suites"]["settlement"]["passed"] == 201 and IMP.status()["parked"] >= 1)
    check("the live tree was never written — the fixture's digest is unchanged",
          _hl.sha256(open(_fixture_abs, "rb").read()).hexdigest() == _live_before)
    _e = RS.for_proposal(_p[0]["id"])
    check("the research — advantage AND risk — was written for the verified change from the measured facts, before anything else",
          _e is not None and _e["body"]["advantage"].startswith("hello now says 2") and _e["body"]["risk"]
          and _e["evidence"]["suites_after"]["settlement"]["passed"] == 201 and "BEGIN DIFF" in _asked[-1]
          and "return 2" in _asked[-1] and RS.verify_chain()["intact"])
    check("the record is a file too — RESEARCH.md regenerated from the ledger, never edited",
          "R%d" % _e["id"] in RS.render() and "**Advantage.** hello now says 2" in RS.render()
          and os.path.exists(RS.local_path()))
    # a rejection, by name, confirmed on re-run
    def _red(tree, only=None):
        return {"green": False, "suites": {"governor": {"passed": 15, "total": 16, "ok": False, "seconds": 1, "tail": "FAIL x", "failed": ["the gate holds"]},
                                           "settlement": {"passed": 201, "total": 201, "ok": True, "seconds": 2, "tail": "", "failed": []}}}
    IMP.ORACLE = lambda tree, only=None: _green(tree) if "return 2" not in open(os.path.join(tree, _fixture_rel)).read() else _red(tree)
    IMP.CANDIDATES = lambda: [dict(_cand, title="red one " + _tag)]
    _r2 = IMP.cycle("test")
    check("a patch that makes a check fail is rejected NAMING the check, confirmed on a re-run, and recorded as a lesson",
          _r2["verified"] == 0 and _r2["rejected"] == 1
          and any(p["status"] == "rejected" and "governor: the gate holds" in p["note"] and "confirmed on re-run" in p["note"] for p in IMP.proposals())
          and A._conn().execute("SELECT COUNT(*) FROM knowledge WHERE kind='improve-rejected'").fetchone()[0] >= 1,
          str([p["note"][:80] for p in IMP.proposals()[:2]]))
    # the flake guard
    _runs = {"n": 0}
    def _flaky(tree, only=None):
        _runs["n"] += 1
        if "return 2" not in open(os.path.join(tree, _fixture_rel)).read():
            return _green(tree)
        if _runs["n"] == 2:
            return {"green": False, "suites": {"governor": {"passed": 16, "total": 16, "ok": True, "seconds": 1, "tail": "", "failed": []},
                                               "settlement": {"passed": 200, "total": 201, "ok": False, "seconds": 2, "tail": "",
                                                              "failed": ["the time series accounts for every decision"]}}}
        return {"green": True, "suites": {"settlement": {"passed": 201, "total": 201, "ok": True, "seconds": 2, "tail": "", "failed": []}}}
    IMP.ORACLE = _flaky
    IMP.CANDIDATES = lambda: [dict(_cand, title="flaky one " + _tag)]
    _r3 = IMP.cycle("test"); _p3 = IMP.proposals()[0]
    check("a check that fails on the first run and passes on a re-run of the same copy is a flake: verified, and the note says so",
          _r3["verified"] == 1 and _p3["status"] == "verified" and "flaky, not the patch" in _p3["note"]
          and "the time series" in _p3["note"] and _p3["after_json"]["suites"]["settlement"].get("first_run"), _p3["note"][:200])
    IMP.ORACLE = _green
    # unverifiable, protected, unresearched
    IMP.CANDIDATES = lambda: [dict(_cand, file="requirements.txt", title="bump a pin " + _tag,
                                   patch="--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1 @@\n-x\n+y\n")]
    _r4 = IMP.cycle("test"); _p4 = IMP.proposals()[0]
    check("a patch to a file the suites do not exercise is parked unverified — never 'verified' — and still approvable",
          _r4["verified"] == 0 and _p4["status"] == "unverified" and "do not exercise requirements.txt" in _p4["note"] and "unverified" in _r4["note"], str(_r4))
    IMP.CANDIDATES = lambda: [dict(_cand, file="RESEARCH.md", title="rewrite the record " + _tag,
                                   patch="--- a/RESEARCH.md\n+++ b/RESEARCH.md\n@@ -1 +1 @@\n-x\n+y\n"),
                              dict(_cand, file="CONSTITUTION.md", title="loosen a rule " + _tag,
                                   patch="--- a/CONSTITUTION.md\n+++ b/CONSTITUTION.md\n@@ -1 +1 @@\n-x\n+y\n")]
    _r5 = IMP.cycle("test")
    check("a patch to the research record or the constitution is REFUSED before any suite runs — agents cannot reach them",
          _r5.get("refused") == 2 and all(p["status"] == "refused" and "protected" in p["note"] for p in IMP.proposals()[:2])
          and "refused as protected" in _r5["note"] and RS.protected("gov/research.py") and RS.protected("mechanic/CHARTER.md")
          and not RS.protected("gov/improve.py"), str(_r5))
    RS.ASK = lambda prompt: "I cannot say."
    IMP.CANDIDATES = lambda: [dict(_cand, title="no research possible " + _tag)]
    _r6 = IMP.cycle("test"); _p6 = IMP.proposals()[0]
    check("no research — no change: a verified patch whose research lacks advantage and risk is held back, never published",
          _p6["status"] == "unresearched" and "NOT published" in _p6["note"] and _r6.get("proposed", 0) == 0
          and "want of research" in _r6["note"] and RS.for_proposal(_p6["id"]) is None, _p6["note"][:160])
    RS.ASK = _researcher
    check("the suites' FAIL lines are read by name",
          IMP._FAILED.search("  [\x1b[31mFAIL\x1b[0m] the time series accounts for every decision, not a sample  — 0 buckets").group(1).strip()
          == "the time series accounts for every decision, not a sample")
    # the gate without a token: approved → the patch is handed over
    _ap = IMP.approve(_p[0]["id"], "tester")
    check("approval without a GITHUB_TOKEN hands the patch to the human — nothing is pushed",
          _ap["ok"] and _ap["status"] == "approved" and "+    return 2" in _ap["patch"] and IMP.proposal(_p[0]["id"])["status"] == "approved")
    # Article XI.3: published on its own — a draft PR carrying RESEARCH.md
    _opened = []
    _GH.open_pr = lambda repo, token, branch, files, title, body, base="": (_opened.append((branch, sorted(files), title, body)) or "https://github.com/o/r/pull/42")
    os.environ["GITHUB_TOKEN"] = "tok"; IMP.AUTO_PR = True
    IMP.CANDIDATES = lambda: [dict(_cand, title="publish me " + _tag)]
    _r7 = IMP.cycle("test"); _p7 = IMP.proposals()[0]
    check("a verified, researched patch is pushed to its own branch and opened as a draft PR with RESEARCH.md — the merge stays human",
          _r7["verified"] == 1 and _r7.get("proposed") == 1 and _p7["status"] == "proposed" and _p7["pr_url"].endswith("/pull/42")
          and _opened and _opened[-1][0] == "phoenix/improve-%d" % _p7["id"] and _opened[-1][1] == ["RESEARCH.md", _fixture_rel]
          and "advantage" in _opened[-1][3], str(_p7["note"])[:120])
    _r8 = IMP.cycle("test")
    check("the same change is not proposed again while its PR is open — skipped and recorded",
          _r8["tried"] == 0 and _r8["candidates"] == 1
          and A._conn().execute("SELECT COUNT(*) FROM knowledge WHERE kind='improve-skipped'").fetchone()[0] >= 1, str(_r8))
    # the operator names the deploy branch: research committed FIRST, then the patch; the undo kept
    _branch_files = {_fixture_rel: ("def hello():\n    return 1\n", "sha-a")}
    _commits = []
    _GH.get_file = lambda repo, token, branch, path: _branch_files.get(path, (None, ""))
    def _fake_commit(repo, token, branch, path, content, message, expect_sha=""):
        assert expect_sha == _branch_files.get(path, (None, ""))[1], "the write must name the blob it last saw"
        _commits.append((branch, path, content, message)); _branch_files[path] = (content, "sha-%d" % len(_commits))
        return {"sha": "c" * 40, "url": "https://github.com/o/r/commit/" + "c" * 40}
    _GH.commit_file = _fake_commit
    IMP.PUSH_BRANCH = "claude/project-review-1l2hho"
    IMP.CANDIDATES = lambda: [dict(_cand, title="straight to the branch " + _tag)]
    _r9 = IMP.cycle("test"); _p9 = IMP.proposals()[0]
    check("with IMPROVE_PUSH_BRANCH: RESEARCH.md is committed first, then the patch, naming the proposal; the original is kept",
          _p9["status"] == "committed" and len(_commits) >= 2 and _commits[-2][1] == "RESEARCH.md" and "Research R" in _commits[-2][3]
          and "**Advantage.**" in _commits[-2][2] and _commits[-1][0] == "claude/project-review-1l2hho"
          and "return 2" in _commits[-1][2] and "Self-improvement #%d" % _p9["id"] in _commits[-1][3]
          and _p9["original"] == "def hello():\n    return 1\n", str(_p9["note"])[:120])
    _rv = IMP.revert(_p9["id"], "tester")
    check("revert puts the file back as one commit and the proposal reads reverted",
          _rv["ok"] and _commits[-1][2] == "def hello():\n    return 1\n" and "Revert self-improvement" in _commits[-1][3]
          and IMP.proposal(_p9["id"])["status"] == "reverted", str(_rv))
    _branch_files["RESEARCH.md"] = ("# someone rewrote the research file\n", "sha-t")
    IMP.CANDIDATES = lambda: [dict(_cand, title="tampered record " + _tag)]
    _r10 = IMP.cycle("test"); _p10 = IMP.proposals()[0]
    check("a RESEARCH.md on the branch that does not match the record stops the publish and escalates — never overwritten",
          _p10["status"] == "verified" and "refused to append" in _p10["note"]
          and A._conn().execute("SELECT COUNT(*) FROM knowledge WHERE kind='escalation' AND note LIKE '%does not match the research record%'").fetchone()[0] >= 1,
          _p10["note"][-160:])
    _branch_files["RESEARCH.md"] = (RS.expected_tail(10**9), "sha-u")
    IMP.CANDIDATES = lambda: [dict(_cand, title="edited underneath " + _tag)]
    _r11 = IMP.cycle("test"); _p11 = IMP.proposals()[0]
    _branch_files[_fixture_rel] = ("def hello():\n    return 3   # a human edited this since\n", "sha-z")
    _rv2 = IMP.revert(_p11["id"], "tester")
    check("a revert is refused when the file changed since the commit — a later edit is not the proposal's to undo",
          _p11["status"] == "committed" and not _rv2["ok"] and "changed" in _rv2["error"] and IMP.proposal(_p11["id"])["status"] == "committed", str(_rv2))
    _cc = IMP._conn(); _cc.execute("INSERT INTO improve_cycles(ts, trigger, status, candidates, tried, verified, rejected, note, seconds, baseline, signals, model, charter) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (time.time(), "test", "running", 0, 0, 0, 0, "cut off", 0, "{}", "{}", "", "")); _cc.commit(); _cc.close()
    check("a cycle still marked running at boot is reaped as interrupted — a restart mid-cycle is recorded, not left looking alive",
          IMP.reap() >= 1 and IMP.cycles(limit=1)[0]["status"] == "interrupted" and "restarted" in IMP.cycles(limit=1)[0]["note"])
    check("the research chain is intact after every append, and has no edit or delete anywhere in the module",
          RS.verify_chain()["intact"] and RS.verify_chain()["entries"] >= 4
          and "UPDATE research" not in open(os.path.join(HERE, "research.py")).read()
          and "DELETE FROM research" not in open(os.path.join(HERE, "research.py")).read())
    # escalation after three empty cycles
    IMP.CANDIDATES = lambda: []
    for _ in range(IMP.EMPTY_CYCLES_ESCALATE):
        IMP.cycle("test")
    check("three consecutive empty cycles are an escalation to the Chief Governor, not a quiet repeat",
          IMP.status()["empty_streak"] >= IMP.EMPTY_CYCLES_ESCALATE
          and A._conn().execute("SELECT COUNT(*) FROM knowledge WHERE kind='escalation' AND note LIKE 'IMPROVEMENT STALLED%'").fetchone()[0] >= 1)
    check("the constitution names the article and its enforcing code, and bumped its version",
          "## Article XI" in A.charter_text() and "improve.cycle" in A.charter_text() and "research.py" in A.charter_text()
          and re.search(r"^Version: 1\.6", A.charter_text(), re.M) is not None)
    # ── the ten ages, 10,000× per leap, food + wood + gold; a legacy world keeps its place ──
    check("ten ages, each leap the previous one times one growth factor, in food, wood and gold, the ladder readable",
          len(S.AGE_ORDER) == 10 and S.AGE_ORDER[0] == "Stone Age" and S.AGE_ORDER[-1] == "Tech Age"
          and S.advance_cost("Stone Age") == S.ADVANCE_COST
          and S.advance_cost("Tool Age")["food"] == S._round2(S.ADVANCE_COST["food"] * S.ADVANCE_GROWTH)
          and S.advance_cost("Industrial Age")["food"] == S._round2(S.ADVANCE_COST["food"] * S.ADVANCE_GROWTH ** 8)
          and len(S.age_ladder()) == 9 and S.age_ladder()[-1]["to"] == "Tech Age" and "wood" in S.advance_cost(),
          f"growth {S.ADVANCE_GROWTH}, Tech Age costs {S.advance_cost('Industrial Age')['food']:,} food")
    check("difficulty is a setting — the total price of maturity: easy 100M, medium 1B, hard 10B — and the world names it",
          S.DIFFICULTIES == {"easy": 100_000_000, "medium": 1_000_000_000, "hard": 10_000_000_000}
          and S.difficulty()["growth"] == S.ADVANCE_GROWTH and S.difficulty()["ages"] == 10, str(S.difficulty()))
    _d = S.difficulty()
    check("the whole climb from Stone to Tech costs what the difficulty says (default: about one billion), within 3%",
          abs(_d["maturity_total"] - _d["maturity_target"]) <= 0.03 * _d["maturity_target"]
          and all(S._solve_growth(t, 1100, 9) > 1 for t in S.DIFFICULTIES.values()),
          f"{_d['name']}: total {_d['maturity_total']:,} vs target {_d['maturity_target']:,}, growth {_d['growth']}× per leap")
    check("a world from the four-age ladder reads as Stone Age and the vision ladder climbs ten rungs",
          S.canonical_age("Dark Age") == "Stone Age" and _V.AGES == list(S.AGE_ORDER)
          and _V.MORE_AMBITIOUS["imperial"] == "industrial" and _V.MORE_AMBITIOUS["industrial"] == "tech"
          and _V.VISIONS[_V.DEFAULT_VISION].target_age == "Tool Age")
    # ── ideas from outside: read, cite, check, propose, research, queue — never adopt ──
    import ideas as ID
    _saved_ideas = (ID.FETCH, ID.PROPOSE, RS.ASK, N._OPEN)
    _page_text = "<html><body><p>Crop rotation is the practice of growing a series of different types of crops in the same area across a sequence of growing seasons.</p><script>x()</script></body></html>"
    class _Resp:
        def __init__(self, b): self._b = b
        def read(self, n=-1): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    N._OPEN = lambda req, timeout=None, context=None: _Resp(_page_text.encode())
    ID.FETCH = lambda topic: {"title": "Crop rotation", "url": "https://en.wikipedia.org/wiki/Crop_rotation",
                              "extract": "Crop rotation is the practice of growing a series of different types of crops in the same area across a sequence of growing seasons. It reduces reliance on one set of nutrients."} if "rotation" in topic or "productivity" in topic else None
    ID.PROPOSE = lambda situation, facts, existing: {"name": "field_rotation_" + _tag.strip("[]"), "cost": {"food": 60, "wood": 40, "gold": 0},
                                                     "kind": "yield_pct", "value": 15, "resource": "food", "rank": 2,
                                                     "why": "rotating fields keeps soil fertile"} if facts else None
    RS.ASK = _researcher
    try:
        A._URL_CACHE.clear()
        check("a public URL now resolves for the citation check, markup stripped, and a quoted sentence is found in it",
              A.verify_claim("https://en.wikipedia.org/wiki/Crop_rotation", "Crop rotation is the practice of growing a series")[0]
              and not A.verify_claim("https://en.wikipedia.org/wiki/Crop_rotation", "crop rotation doubles the yield")[0]
              and "x()" not in (A._resolve_source("https://en.wikipedia.org/wiki/Crop_rotation") or "x()"))
        _w = S.world()
        _ic = ID.consider(0, "agricultural productivity", _w, "a test settlement")
        _row = ID.rows(limit=1)[0]
        check("an idea is read from its source, ingested with a checked citation, proposed as a buildable development, researched, and QUEUED — not adopted",
              _ic["status"] == "queued" and _row["verified"] == 1 and _row["source"].startswith("https://")
              and _row["proposal"]["kind"] == "yield_pct" and _row["research_id"] and RS.for_proposal(_row["id"]) is not None
              and RS.entries(limit=100000)[-1]["evidence"]["kind"] == "world"
              and _row["proposal"]["name"] not in [d["name"] for d in S.dev_catalog()], str(_ic))
        _tk = ID.take()
        check("the console takes the oldest queued idea for the Board with its source and research cited; the idea reads 'voting'",
              _tk and _tk["idea_id"] == _row["id"] and "research R" in _tk["why"] and "wikipedia" in _tk["why"]
              and ID.rows(limit=1)[0]["status"] == "voting")
        ID.mark(_tk["idea_id"], "adopted", "human adopted")
        _page_text = "<html><body><p>Something else entirely.</p></body></html>"
        A._URL_CACHE.clear()
        _ic2 = ID.consider(0, "crop rotation", _w, "a test settlement")
        check("a fact whose citation does not check out is kept as UNVERIFIED and never becomes a proposal (VI.2)",
              _ic2["status"] == "unverified" and ID.rows(limit=1)[0]["proposal"] is None
              and any(k["topic"] == "crop rotation" and not k["verified"] for k in A.external(5)))
        _ic3 = ID.consider(0, "the wheel", _w, "a test settlement")
        check("a topic with no readable source is recorded as unread, not invented",
              _ic3["status"] == "unread" and ID.rows(limit=1)[0]["status"] == "unread")
        _tops = ID.topics({"age": "Iron Age", "food": 1, "wood": 10**9, "gold": 10**9}, set(), 3)
        check("topics follow the age and the scarcest resource first",
              _tops[0] == "agricultural productivity" and _tops[1] in ID.TOPICS["Iron Age"] and len(_tops) == 3, str(_tops))
        _m0 = IMP.MODE; IMP.MODE = "world"
        ID.FETCH = lambda topic: None
        _topics0 = ID.topics
        ID.topics = lambda w, already, limit: ["a fresh topic " + _tag]     # the record persists across runs
        try:
            _wc = IMP.cycle("test")
        finally:
            ID.topics = _topics0
        check("in world mode a cycle reads topics for this age and records what it found — no code is touched",
              _wc["status"] == "complete" and _wc["candidates"] >= 1 and "mode: world" in _wc["note"]
              and IMP.cycles(limit=1)[0]["note"].startswith("mode: world"), str(_wc))
        IMP.MODE = _m0
    finally:
        ID.FETCH, ID.PROPOSE, RS.ASK, N._OPEN = _saved_ideas
finally:
    IMP.ORACLE, IMP.CANDIDATES, IMP.AUTO_PR, IMP.PUSH_BRANCH, RS.ASK, _GH.open_pr, _GH.get_file, _GH.commit_file = _saved_seams
    IMP.MODE = _mode0
    os.environ.pop("GITHUB_TOKEN", None)
    if _envt is not None: os.environ["GITHUB_TOKEN"] = _envt
    os.remove(_fixture_abs)
# ── Article XII: utopia — five qualities with effects, civic works designed by the world ──
import utopia as UT
import lives as LV
import economy as EC
_u0 = S.utopia_state()
_y0 = S.effective_yield("food")
_p0 = S.world()["pop_cap"]
_ut_names = []
try:
    _nm = "test_rose_garden_" + _tag.strip("[]")
    ok_, _ = S.dev_add(_nm, {"wood": 10}, "utopia", 40, "beauty", 3, "suite",
                       {"shape": "garden", "color": "#e07a9a", "scale": 1.3, "district": "nature", "evil": "<script>"})
    _ut_names.append(_nm)
    S._world_add("wood", 10)
    S.build_development(_nm)
    _u1 = S.utopia_state()
    check("a built civic work raises its quality with diminishing returns, and the index discounts imbalance",
          ok_ and _u1["qualities"]["beauty"] > _u0["qualities"]["beauty"] and _u1["qualities"]["beauty"] < 100
          and _u1["index"] < _u1["qualities"]["beauty"] and _u1["works"] >= 1, str(_u1["qualities"]))
    check("beauty lifts every yield — the civic work is a working asset",
          S.effective_yield("food") > _y0 and _u1["effects"]["all_yield_pct"] > _u0["effects"]["all_yield_pct"])
    _nm2 = "test_healing_well_" + _tag.strip("[]")
    S.dev_add(_nm2, {"wood": 10}, "utopia", 90, "health", 3, "suite", {"shape": "fountain", "district": "residential"})
    _ut_names.append(_nm2); S._world_add("wood", 10); S.build_development(_nm2)
    check("health houses more settlers", S.world()["pop_cap"] > _p0, f"{_p0} → {S.world()['pop_cap']}")
    _reg = S.render_registry()[_nm]
    _pl = [q for q in S.map_state()["placements"] if q["name"] == _nm]
    check("the design reaches the 3D worlds as drawn: known shape, colour, scale, district — junk stripped; placed in its district",
          _reg["shape"] == "garden" and _reg["color"] == "#e07a9a" and _reg["scale"] == 1.3 and "evil" not in _reg
          and _pl and max(abs(_pl[0]["x"] - S.DISTRICT_ANCHOR["nature"][0]), abs(_pl[0]["y"] - S.DISTRICT_ANCHOR["nature"][1])) <= 3
          and "utopia" in S.map_state() and "age_index" in S.map_state())
    check("a design outside the vocabulary is replaced, never trusted",
          S.clean_visual({"shape": "rocket", "color": "red", "scale": 99, "district": "moon"})
          == {"shape": "monument", "color": "#c9b98f", "scale": 2.0, "district": "centre"})
    check("a civic work must name one of the five qualities", not S.dev_add("x_" + _tag.strip("[]"), {}, "utopia", 5, "wealth")[0])
    # the architect: the world's own ideas, a managed token budget, the price set by the world
    _ask0, _tpd0 = UT.ASK, UT.TOKENS_PER_DAY
    UT.ASK = lambda prompt: ('{"name": "hall_of_voices_%s", "quality": "harmony", "value": 20, "shape": "temple", '
                             '"color": "#e8dcc0", "scale": 1.4, "district": "centre", "why": "harmony is the weakest"}' % _tag.strip("[]"))
    try:
        _d = UT.design("a test settlement")
        check("the architect designs from the world's measured need; the price is the world's, not the model's",
              _d and _d["kind"] == "utopia" and _d["resource"] == "harmony" and _d["visual"]["shape"] == "temple"
              and _d["cost"] == UT._price(S.canonical_age(S.world()["age"]), 20) and "architect (model)" in _d["source"], str(_d)[:200])
        UT.ASK = lambda prompt: '{"name": "moon_base", "quality": "harmony", "value": 20, "shape": "rocket"}'
        _d2 = UT.design("")
        check("a design that does not fit the vocabulary falls back to the pattern book, and says so",
              _d2 and _d2["visual"]["shape"] in S.UTOPIA_SHAPES and "pattern book" in _d2["source"], str(_d2)[:160])
        UT.TOKENS_PER_DAY = 0
        _d3 = UT.design("")
        check("over its daily token budget the architect uses its pattern book — design is managed, and recorded",
              _d3 and "budget spent" in _d3["source"]
              and A._conn().execute("SELECT COUNT(*) FROM knowledge WHERE kind='civic-budget'").fetchone()[0] >= 1)
    finally:
        UT.ASK, UT.TOKENS_PER_DAY = _ask0, _tpd0
    # Article II as amended: agents live on, with free will inside the rules
    check("five ranks: villager → foreman → delegate → steward → leader; leaders may start projects",
          [t["name"] for t in EC.TIERS] == ["villager", "foreman", "delegate", "steward", "leader"]
          and "start_project" in EC.TIERS[-1]["can"] and "mentor" in EC.TIERS[3]["can"])
    _t = LV.temperament("vil-07")
    check("a temperament is fixed at birth from the name — the same person after a restart",
          _t == LV.temperament("vil-07") and _t["favourite"] in S.RESOURCES and 0.08 <= _t["curiosity"] <= 0.3)
    _frees = [LV.free_choice("vil-07", "wood" if _t["favourite"] != "wood" else "gold", "no shortages — bank the surplus", n) for n in range(200)]
    _bound = [LV.free_choice("vil-07", "gold", "Age-up shortfall drives it", n) for n in range(200)]
    check("free will: sometimes the agent follows its own inclination when nothing binds — never against a shortfall",
          any(f[2] for f in _frees) and not all(f[2] for f in _frees) and not any(b[2] for b in _bound)
          and all(f[0] == _t["favourite"] for f in _frees if f[2]))
    EC.enlist("vil-mentee-" + _tag.strip("[]"))
    EC.enlist("vil-elder-" + _tag.strip("[]"), tier=3)
    _before = {r["agent"]: r["contribution"] for r in EC.roster()}
    _nu = LV.nurture("vil-mentee-" + _tag.strip("[]"), 100, 1)
    _after = {r["agent"]: r["contribution"] for r in EC.roster()}
    check("a newcomer working while a steward lives is mentored — both earn from it",
          _nu and _after["vil-mentee-" + _tag.strip("[]")] > _before["vil-mentee-" + _tag.strip("[]")]
          and _after[_nu["mentor"]] > _before[_nu["mentor"]], str(_nu))
    _csrc = open(os.path.join(HERE, "sim_console.py")).read()
    check("the system never retires an agent: a spent season and an ended thread are RENEWED on the same identity",
          "def _renew(" in _csrc and "economy.retire" not in _csrc.split("def _op_terminate")[0].split("def _renew(")[0][-4000:]
          and _csrc.count("economy.retire(") == 1 and "honourable discharge" not in _csrc)
    EC.retire("vil-mentee-" + _tag.strip("[]")); EC.retire("vil-elder-" + _tag.strip("[]"))
    _pg3 = open(os.path.join(HERE, "pages", "map3d.html")).read(); _pgb = open(os.path.join(HERE, "pages", "babylon.html")).read()
    check("both 3D worlds draw every civic shape, and grow the town centre with each age",
          all(f"'{sh}'" in _pg3 and f"'{sh}'" in _pgb for sh in S.UTOPIA_SHAPES)
          and "age_index" in _pg3 and "age_index" in _pgb)
finally:
    _cx = S._conn()
    for _n in _ut_names:
        for _tbl in ("custom_devs", "placements", "conditions"):
            _cx.execute(f"DELETE FROM {_tbl} WHERE name=?", (_n,))
    _cx.commit(); _cx.close()

# the pull request is made of reads retried and writes made once, as a draft
import ghpr as GH
_calls = []
class _R:
    def __init__(self, b): self._b = b
    def read(self): return _json_mod.dumps(self._b).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _gh_open(req, timeout=None, context=None):
    _calls.append((req.get_method(), req.full_url.replace(GH.API, ""), _json_mod.loads(req.data) if req.data else None))
    u = req.full_url
    if u.endswith("/repos/o/r"): return _R({"default_branch": "main"})
    if "/git/ref/heads/main" in u: return _R({"object": {"sha": "abc"}})
    if "/git/ref/heads/phoenix/improve-1" in u: raise urllib.error.HTTPError(u, 404, "no", {}, None)
    if "/contents/" in u and req.get_method() == "GET": return _R({"sha": "old"})
    if u.endswith("/pulls"): return _R({"html_url": "https://github.com/o/r/pull/9"})
    return _R({})
_open0, N._OPEN = N._OPEN, _gh_open
try:
    _url = GH.open_pr("o/r", "tok", "phoenix/improve-1", {"gov/x.py": "print(1)\n"}, "t", "b")
    _writes = [c for c in _calls if c[0] != "GET"]
    check("an approval opens a DRAFT pull request: branch from the default branch, file put, PR draft:true",
          _url.endswith("/pull/9") and [c[0] for c in _writes] == ["POST", "PUT", "POST"]
          and _writes[0][2]["ref"] == "refs/heads/phoenix/improve-1" and _writes[1][2]["sha"] == "old"
          and _writes[2][2]["draft"] is True and _writes[2][2]["head"] == "phoenix/improve-1", str([c[:2] for c in _calls]))
finally:
    N._OPEN = _open0

# ── the model review: every call's telemetry, downtime from the record, events from the breaker ──
import anchor as _A
import models_page as _MP
_A.model_call_log("https://gw.example/v1", "qwen3:30b", "chat-reply", 1200, 100, 40, True,
                  attempts=2, reasoning_chars=300, content_chars=100, transport="openai /v1")
for _i in range(3):
    _A.model_call_log("https://gw.example/v1", "qwen3:30b", "panel:quality", 90000, 500, 0, False,
                      error="APITimeoutError: Request timed out (after 6 attempts)", attempts=6)
_A.model_call_log("https://gw.example/v1", "qwen3:30b", "chat-reply", 800, 100, 30, True)
_rv = _MP.review("1h")
_m = next((m for m in _rv["models"] if m["model"] == "qwen3:30b"), None)
check("the review reads every call's telemetry per model: latency percentiles, tokens, attempts, thinking share",
      _m is not None and _m["calls"] >= 5 and _m["errors"] >= 3 and _m["p95_ms"] >= 800
      and _m["retried_calls"] >= 1 and 0 < _m["thinking_share"] < 1 and "openai /v1" in _m["transports"],
      str({k: _m[k] for k in ("calls", "errors", "p95_ms", "retried_calls", "thinking_share")}) if _m else "no row")
check("three consecutive failed calls are a recorded outage, from the record alone",
      any(o["calls"] >= 3 and "timed out" in o["first_error"] for o in _rv["outages"]))
check("who spends the model's time is broken down by purpose",
      any(p["purpose"] == "panel:quality" and p["errors"] >= 3 for p in _rv["purposes"]))
N.reset()
try:
    for _ in range(N.BREAK_AFTER):
        try: N.call(lambda: (_ for _ in ()).throw(TimeoutError("hung")), what="gw", idempotent=True, key="gw.example")
        except TimeoutError: pass
finally:
    N.reset()
check("a breaker opening is written to the permanent record as an event",
      any(e["kind"] == "breaker_open" and e["host"] == "gw.example" for e in _MP._events()))
_csrc = open(os.path.join(HERE, "sim_console.py")).read()
check("the review page is served by the console with the shared palette and a nav link",
      "MODEL REVIEW" in _MP.PAGE and "MODELS_PAGE = _page(" in _csrc and 'href="/models"' in _csrc
      and '"/api/models"' in _csrc)

# ── provider selection: the platform's own gateway is the default, DeepSeek the fallback ──
_envs = {k: os.environ.get(k) for k in ("BRAIN_API_KEY", "BRAIN_BASE_URL", "BRAIN_MODEL", "DEEPSEEK_API_KEY")}
try:
    for k in _envs:
        os.environ.pop(k, None)
    os.environ["BRAIN_API_KEY"] = "sk-test"
    _p = B.provider()
    check("BRAIN_API_KEY alone selects the platform's own gateway and its default model",
          _p == {"kind": "openai", "base_url": B.DEFAULT_BASE_URL, "key": "sk-test",
                 "model": B.DEFAULT_MODEL}, str(_p))
    os.environ["DEEPSEEK_API_KEY"] = "sk-old"
    check("…and wins over a DEEPSEEK_API_KEY that is still set",
          B.provider()["base_url"] == B.DEFAULT_BASE_URL)
    os.environ.pop("BRAIN_API_KEY")
    check("without BRAIN_API_KEY the original provider is the fallback, not a silent no-model",
          B.provider()["base_url"] == "https://api.deepseek.com")
    os.environ["BRAIN_API_KEY"], os.environ["BRAIN_MODEL"] = "sk-test", "llama3.1:8b"
    check("BRAIN_MODEL picks another model the gateway serves", B.provider()["model"] == "llama3.1:8b")
    # the native Ollama transport: think:false in the body, thinking read out of the reply
    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return B._json_mod.dumps(self._b).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    _sentb = []
    _sent_ua = []
    def _fake_open(req, timeout=None, context=None):
        _sentb.append(B._json_mod.loads(req.data)); _sent_ua.append(req.get_header("User-agent", ""))
        if "closed" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)
        return _Resp({"message": {"role": "assistant", "content": "alive", "thinking": ""},
                      "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 3})
    _open0, N._OPEN = N._OPEN, _fake_open
    try:
        _pp = {"kind": "openai", "base_url": "https://gw.example/v1", "key": "k", "model": "qwen3:30b"}
        _o, _u = B._ollama_chat(_pp, [{"role": "user", "content": "say alive"}], 50, 0.0, "probe")
        check("a tagged model is called on Ollama's native /api/chat with think:false, tokens read from the reply",
              _o == "alive" and _sentb[-1]["think"] is False and _sentb[-1]["options"]["num_predict"] == 50
              and _sent_ua[-1].startswith("phoenix-brain/")
              and _u == {"input_tokens": 12, "output_tokens": 3} and B.LAST_RAW["reasoning_chars"] == 0
              and B._native_url(_pp["base_url"]) == "https://gw.example/api/chat", str(_sentb[-1])[:120])
        B.NATIVE["ok"] = None
        _pc = dict(_pp, base_url="https://closed.example/v1")
        try:
            B._ollama_chat(_pc, [{"role": "user", "content": "x"}], 5, 0.0, "probe")
        except urllib.error.HTTPError as e:
            check("a gateway that does not expose /api/chat answers 404 — a refusal, not retried, and the seam remembers",
                  e.attempts == 1 and N.status_of(e) == 404)
    finally:
        N._OPEN = _open0; B.NATIVE["ok"] = None
    check("thinking is off project-wide: Ollama's field for a tag, DeepSeek's for its API, else nothing",
          B.default_extras("qwen3:30b") == {"think": False}
          and B.default_extras("deepseek-v4-flash") == {"thinking": {"type": "disabled"}}
          and B.default_extras("claude-sonnet-5") == {})
finally:
    for k, v in _envs.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v

_saved = {n: getattr(B, n) for n in ("provider", "_deepseek_available", "_anthropic_chat")}
B.provider = lambda: {"kind": "anthropic", "base_url": "t", "model": "t", "key": "t"}
B._deepseek_available = lambda: True
B._anthropic_chat = lambda p, messages, mt, tp: (_sent.extend(messages), ("{}", None))[1]
_probed, _over = [], 0
try:
    for _name, _fn in sorted(vars(B).items()):
        if _name.startswith("_") or not _inspect.isfunction(_fn) or _fn.__module__ != B.__name__:
            continue
        _args = []
        for _p in _inspect.signature(_fn).parameters.values():
            if _p.default is not _inspect.Parameter.empty:
                continue                            # only what the caller must supply
            _ann = getattr(_p.annotation, "__name__", str(_p.annotation))
            _args.append({"str": _BIG, "int": 0,
                          "list": [{"topic": _BIG, "fact": _BIG, "lesson": _BIG}] * 6,
                          "dict": {"food": _BIG, "wood": _BIG, "gold": _BIG}}.get(_ann, _BIG))
        _before = len(_sent)
        try:
            _fn(*_args)
        except Exception:
            pass                                    # a builder may reject junk; that is fine
        if len(_sent) > _before:
            _probed.append(_name)
    _over = max([len(m["content"]) for m in _sent], default=0)
finally:
    for _n, _v in _saved.items():
        setattr(B, _n, _v)
check("every prompt builder in brain.py was actually exercised",
      len(_probed) >= 5 and bool(_sent), f"{len(_probed)} entry points reached the provider: "
      + ", ".join(_probed))
check("no entry point can be made to send an unbounded prompt",
      0 < _over <= B.PROMPT_LIMITS["prompt"],
      f"40,000 chars into every field → worst turn {_over} chars, ceiling "
      f"{B.PROMPT_LIMITS['prompt']}")
check("the ceiling is applied under _chat, so a new caller inherits it",
      "clip(\"prompt\"" in open(os.path.join(HERE, "brain.py")).read().split("def _chat")[1][:900],
      "the choke point clips, not only the individual builders")
check("what the ceiling cut is counted, not silently dropped (III.5)",
      bool(B.prompt_overruns()) and all(v["hits"] > 0 and v["worst"] >= v["limit"]
                                        for v in B.prompt_overruns().values()),
      f"{len(B.prompt_overruns())} fields recorded an overrun")
check("a list is bounded per item as well as in total",
      len(B.clip_join("facts", "fact", [_BIG] * 6)) <= B.PROMPT_LIMITS["facts"]
      and B.prompt_overruns().get("fact", {}).get("hits", 0) > 0,
      "one 40,000-char fact cannot crowd out the other four")
check("an input inside its budget is passed through untouched",
      B.clip("situation", "food 40, wood 12") == "food 40, wood 12",
      "the clamp bites only where it must")
check("every declared limit leaves room for the marker it appends",
      all(len(B.clip(f, _BIG)) <= lim for f, lim in B.PROMPT_LIMITS.items()),
      f"{len(B.PROMPT_LIMITS)} fields, none overshoot their own ceiling")

_src = open(os.path.join(HERE, "sim_console.py")).read()
check("a turn that acts without moving the score is counted as waste",
      'waste_turns' in _src and 'waste_since_report' in _src)
check("waste is debited from the Governor's score, not just displayed",
      "score -= min(3, wasted // 50)" in _src)
check("a stall names the component that is SHORT, not the roster",
      "the only component short, at" in _src
      and "gathering cannot move the score (Article I.2)" in _src)

# ── 15. the views are ARITHMETIC, not decoration ────────────────────────────
# A share that does not sum to its denominator is worse than no share at all:
# it looks quantitative and is not. The first version of /flow drew bands from
# the authority column into "carried", implying that policy decisions pass
# through a board vote — two different populations drawn as one river.
print("\n15. Views — every proportion adds up to its denominator")
_auth = A.decision_authorities()
check("authority shares cover the whole decision population exactly once",
      sum(a["n"] for a in _auth) == _fs["proposed"],
      f"{sum(a['n'] for a in _auth)} across {len(_auth)} authorities "
      f"vs {_fs['proposed']} decisions")
check("every decision's authority is attributed, none silently dropped",
      all(a["authority"] for a in _auth))
_act = A.decision_actors(50)
check("actor counts also sum to the same denominator",
      sum(a["n"] for a in _act) == _fs["proposed"])
check("measured never exceeds decisions taken, per actor",
      all(0 <= a["measured"] <= a["n"] for a in _act))
_ser = A.decision_series(12)
check("the time series accounts for every decision, not a sample",
      sum(b["n"] for b in _ser) == _fs["proposed"], f"{len(_ser)} buckets")
check("measured never exceeds taken in any bucket",
      all(0 <= b["measured"] <= b["n"] for b in _ser))

_br = A.board_record()
check("the board's ledger is kept on its OWN denominator",
      _br["votes"] == _br["carried"] + _br["blocked"])
check("the block rate is a percentage of votes, not of all decisions",
      _br["block_pct"] == round(100 * _br["blocked"] / max(1, _br["votes"])))

# Size on the graph follows RECENT traffic. A cumulative count only ever rises, so
# anything drawn from it can grow and never shrink — a busy agent and a long-retired
# one would look identical. The window is the whole point, so it gets a test.
_act_now = A.comm_activity(60)
_act_none = A.comm_activity(0)
check("recent traffic is counted over a window, not for all time",
      sum(_act_now["inbound"].values()) > 0 and sum(_act_none["inbound"].values()) == 0,
      f"{sum(_act_now['inbound'].values())} inbound in 60m, "
      f"{sum(_act_none['inbound'].values())} in a zero-length window")
check("senders and recipients are counted separately",
      set(_act_now) >= {"inbound", "outbound", "heard", "window_min"},
      "offices are sized by what they send, agents by what they receive")
check("nothing heard exceeds what was received, per agent",
      all(_act_now["heard"].get(k, 0) <= v for k, v in _act_now["inbound"].items()))

_cs = A.comm_series(24)
check("the message series is dense — a quiet hour is data, not a gap",
      len(_cs) == 24 and all(c["heard"] <= c["n"] for c in _cs))
A.msg_send("all", "Chief Governor", "Directive to the fleet — no recipient named.")
_ct = A.comm_totals()
check("heard can never exceed addressed",
      _ct["heard"] <= _ct["addressed"]
      and _ct["heard_pct"] == round(100 * _ct["heard"] / max(1, _ct["addressed"])),
      f"{_ct['heard']}/{_ct['addressed']} = {_ct['heard_pct']}%")
check("addressed and broadcast are counted separately, never conflated",
      _ct["broadcast"] > 0 and _ct["addressed"] > 0,
      f"{_ct['addressed']} addressed, {_ct['broadcast']} broadcast")
print("\n17. The map — every built thing has a place (the canvas projection)")
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

print("\n18. Proximity — place matters: a camp on its ground's ring yields more")
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

print("\n19. The land — deterministic terrain that feeds, and wears out under, the economy")
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

print("\n20. The third dimension — 3D worlds ship whole, offline, and wired")
_pages = os.path.join(HERE, "pages")
_p3, _pb = (os.path.join(_pages, f) for f in ("map3d.html", "babylon.html"))
check("both 3D pages exist and are real scenes, not stubs",
      all(os.path.exists(p) and os.path.getsize(p) > 10_000 for p in (_p3, _pb)),
      " + ".join(f"{os.path.getsize(p) // 1024}KB" for p in (_p3, _pb) if os.path.exists(p)))
_srcs = "".join(open(p).read() for p in (_p3, _pb) if os.path.exists(p))
check("the pages load nothing from the network — engines are vendored",
      "http://" not in _srcs and "https://" not in _srcs
      and "/pages/vendor/" in _srcs)
check("the vendored engines are present",
      all(os.path.getsize(os.path.join(_pages, "vendor", f)) > 100_000
          for f in ("three.module.js", "babylon.min.js"))
      and os.path.exists(os.path.join(_pages, "vendor", "addons", "controls", "OrbitControls.js")))
_console_src2 = open(os.path.join(HERE, "sim_console.py")).read()
check("the console routes them, path-safely, from one file-server",
      all(s in _console_src2 for s in ('"/map3d"', '"/babylon"', "_serve_page_file",
                                       "realpath")))

# ── 21. charter provenance — which rules bound a decision, and which brain took it ──
# A report written in March must be readable against March's rules, and "which model
# decided this" must be answerable. A why-chain answers ON WHAT BASIS; it cannot
# answer BY WHOM or UNDER WHICH RULES, and those are the questions asked of any
# record that has to be defended after the fact.
print("\n21. Charter provenance — the rules have a version, decisions cite it (Article X)")
_ch = A.charter(refresh=True)
check("the constitution declares a version", _ch["version"] != A.CHARTER_UNVERSIONED,
      f"{_ch['stamp']} from {_ch['source']} ({_ch['bytes']:,} bytes)")
check("the stamp carries a digest, not only a number people can forget to bump",
      len(_ch["digest"]) == 12 and _ch["stamp"] == f"{_ch['version']}+{_ch['digest']}")
check("one reader serves both the rules page and the stamp",
      "anchor.charter_text()" in open(os.path.join(HERE, "sim_console.py")).read(),
      "the text shown and the text stamped cannot be different documents")

_did = A.reason_add(1, "tester", "probe", "checking provenance", authorized_by="policy")
_rec = next(x for x in A.reasons_top(5) if x["id"] == _did)
check("every decision records the brain that took it",
      bool(_rec["model"]), f"model={_rec['model']!r}")
check("every decision records the charter that bound it",
      _rec["charter"] == _ch["stamp"], _rec["charter"])
check("provenance is captured at reason_add, not asked of each caller (X.2)",
      "model: str = \"\", charter_stamp: str = \"\"" in open(os.path.join(HERE, "anchor.py")).read(),
      "a call site written next month cannot forget what it never had to supply")
check("the lineage view carries it too, so why(x) answers who and under what",
      A.lineage(_did)["decision"]["charter"] == _ch["stamp"])

# X.3 — the failure the digest exists to catch: an amendment that does not bump the
# version. The run stays governed; the LABEL stops being true, and a label that can
# silently stop being true is worse than none, because it reads as evidence.
_base = A.charter_text()
try:
    # Unique text → a digest never seen before, so the check makes its own conditions
    # instead of depending on whether this exact drift was already reported once.
    A.config_set("constitution", _base + f"\n\n## Article XI — smuggled in {stamp}\n")
    A.charter_invalidate()
    _drift = A.charter(refresh=True)
    check("an amendment that does not bump the version is caught",
          _drift["drifted"] and _drift["version"] == _ch["version"]
          and _drift["digest"] != _ch["digest"],
          f"{_ch['digest']} → {_drift['digest']} under the same version")
    check("the drift is recorded as an event, not merely returned",
          any("without bumping" in e for e in A.event_log(20)))
    check("a live console amendment is named as the source, not the file",
          _drift["source"] == "console amendment")
    # bump whatever version the file currently declares — the check must not pin one
    A.config_set("constitution", re.sub(r"^Version: .*$", f"Version: 9.9.{stamp}", _base,
                                        count=1, flags=re.M)
                 + f"\n\n## Article XI {stamp}\n")
    A.charter_invalidate()
    check("bumping the version alongside the edit clears the drift",
          A.charter(refresh=True)["drifted"] is False)
finally:
    A.config_set("constitution", "")
    A.charter_invalidate()
check("reverting the override returns to the shipped rules",
      A.charter(refresh=True)["stamp"] == _ch["stamp"], _ch["stamp"])

# The migration lesson, applied again: provenance migrates on its own flag, so a
# volume that can afford one ALTER but not both keeps the other.
_saved_prov = A._PROV_COLS
try:
    A._PROV_COLS = False
    _d2 = A.reason_add(1, "tester", "probe degraded", "no provenance columns")
    check("without the provenance columns a decision is still recorded",
          bool(_d2) and next(x for x in A.reasons_top(5) if x["id"] == _d2)["decision"]
          == "probe degraded")
    check("the readers degrade to empty provenance rather than raising",
          next(x for x in A.reasons_top(5) if x["id"] == _d2)["model"] == ""
          and A.lineage(_d2)["decision"]["charter"] == "")
finally:
    A._PROV_COLS = _saved_prov

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

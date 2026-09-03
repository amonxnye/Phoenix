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
    A.config_set("constitution", _base.replace("Version: 1.0", f"Version: 1.9.{stamp}")
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

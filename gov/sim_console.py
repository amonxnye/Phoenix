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
import sim
import vision as V

G.TOKEN_CAP = 500_000            # generous cap so the settlement can develop
TICK = 2.0                       # seconds per director turn
TARGET_VILLAGERS = 4

_LOCK = threading.Lock()
_CP = sim.connect()
_GRAPH = sim.build(_CP)
anchor.init()
economy.init()
_S = {"turn": 0, "villagers": [], "heralds": 0, "side_effects": 0, "seq": 0,
      "goal_met": False, "last_vote": None, "vision_key": V.DEFAULT_VISION,
      "orders": {}, "notified": set()}


def _new_vid() -> str:
    # monotonic id so a reaped agent's id is never reused (avoids resuming a dead thread)
    _S["seq"] += 1
    return f"vil-{_S['seq']:02d}"


def _by_uid():
    return {u.unit_id: u for u in G.units(_GRAPH, _CP)}


def _vision():
    return V.get(_S["vision_key"])


def _target_villagers():
    # bolder visions demand more hands; consolidating relaxes the fleet
    return max(2, 3 + V.AGES.index(_vision().target_age))


def _adopt_vision(key: str):
    """The human adopts a new Vision — a fresh mental update cascades to every agent."""
    if key not in V.VISIONS:
        return
    _S["vision_key"] = key
    _S["goal_met"] = False                      # re-open the drive toward the new goal
    for uid in _S["villagers"]:
        anchor.record(_S["turn"], "rebrief", f"{uid} re-briefed → {V.get(key).name}")
    anchor.record(_S["turn"], "vision-change", f"operator adopted vision → {V.get(key).name}")


# ── operator controls (every action is logged so the Board & governor see it) ──

def _op_add(resource: str = "") -> tuple[bool, str]:
    """Operator adds an agent — a token-maxing power, so it goes through the Board."""
    views = list(_by_uid().values())
    ok, reason = G.may_spawn(views)
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    uid = _new_vid()
    ctx = {"aligned": True, "affordable": ok, "within_budget": sc["within_budget"],
           "spent": G.spent(views), "cap": G.TOKEN_CAP}
    bv = board.vote(f"operator add {uid}", ctx)
    _S["last_vote"] = bv
    if not bv["approved"]:
        _S["seq"] -= 1
        anchor.record(_S["turn"], "board", f"[{bv['tally']}] BLOCKED operator add — {reason if not ok else 'no quorum'}")
        return False, f"board blocked ({bv['tally']})"
    res = resource if resource in sim.RESOURCES else D._scarcest(sim.world())
    sim.spawn(_GRAPH, uid, "villager", resource=res)
    economy.enlist(uid)
    _S["villagers"].append(uid)
    anchor.record(_S["turn"], "operator", f"added {uid} → gather {res} (board {bv['tally']})")
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
    return True


def _op_cap(new_cap) -> bool:
    """Operator updates the hard spend cap."""
    try:
        v = max(1_000, int(new_cap))
    except (TypeError, ValueError):
        return False
    old, G.TOKEN_CAP = G.TOKEN_CAP, v
    anchor.record(_S["turn"], "operator", f"cap updated {old:,} → {v:,}")
    return True


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
    return (f"Vision '{_vision().name}' at {sc['progress']}%. Age {w['age']}, food {w['food']} "
            f"wood {w['wood']} gold {w['gold']}, {len(_S['villagers'])} agents, "
            f"side-effects {sc['side_effects']}/{sc['side_effect_budget']}.")


def _chat_reply(key: str, body: str) -> str:
    """Rules decide the action (safe); the model decides the words when available."""
    sentence = _chat_reply_rules(key, body)          # applies any safe action, returns wording
    llm = brain.reply(_persona_for(key), _situation(), body)
    return llm or sentence


def _chat_reply_rules(key: str, body: str) -> str:
    """The participant reads the human's message and responds — or resists."""
    low = body.lower()
    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    if key == "chief":
        return (f"Progress {sc['progress']}% toward '{_vision().name}', {len(_S['villagers'])} agents, "
                f"side-effects {sc['side_effects']}/{sc['side_effect_budget']}. I'll relay your guidance downstream.")
    if key in board.GOVERNORS:
        stance = {"Prudence": "I weigh risk — I'll block spends that near the cap.",
                  "Growth": "I back whatever serves the vision.",
                  "Ledger": "I approve only what we can pay for."}[key]
        return f"Noted. {stance}"
    # an agent — apply if it can, resist if it must
    for r in sim.RESOURCES:
        if r in low:
            _S["orders"][key] = r
            u = _by_uid().get(key)
            if u and u.pending and u.pending.get("reversible") is not False:
                sim.resume(_GRAPH, key, f"gather:{r}")
            anchor.record(_S["turn"], "message", f"{key} took order → gather {r}")
            return f"Acknowledged — switching to gather {r} (standing order set)."
    if any(w in low for w in ("spawn", "more agents", "recruit", "hire")):
        return "Creating agents is a Board power — please put that to the Board, not me."   # resist
    if any(w in low for w in ("stop", "rest", "idle", "halt")):
        return "I'll hold at the gate awaiting orders — terminate me from the roster if I'm not needed."
    return "Understood — I'll keep gathering toward the vision."


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


def _fleet_speaks():
    """Agents / governor raise messages to the human, each at most once per condition."""
    seen = _S["notified"]
    roster_by = {r["agent"]: r for r in economy.roster()}
    for u in _by_uid().values():
        if u.pending and u.pending.get("reversible") is False and f"gate:{u.unit_id}" not in seen:
            anchor.msg_send("chief", "Chief Governor",
                            f"Requesting your approval: {u.pending['action']}.")
            seen.add(f"gate:{u.unit_id}")
        r = roster_by.get(u.unit_id)
        if r and r["budget"] and u.tokens / r["budget"] > 0.8 and f"ow:{u.unit_id}" not in seen:
            anchor.msg_send(u.unit_id, u.unit_id,
                            "I'm overworking — compute is near my budget. Requesting relief or more budget.")
            seen.add(f"ow:{u.unit_id}")


def _chats_snapshot() -> dict:
    parts = _participants()
    return {
        "participants": [{**p, "count": anchor.msg_count(p["key"]),
                          "last": anchor.msg_last(p["key"])} for p in parts],
        "threads": {p["key"]: anchor.msg_thread(p["key"]) for p in parts},
        "broadcast": anchor.msg_thread("all"),
    }


def _pending_herald() -> bool:
    return any(u.pending and u.pending.get("reversible") is False for u in _by_uid().values())


def _one_turn():
    _S["turn"] += 1
    t = _S["turn"]
    w = sim.world()

    # agent creation is a token-maxing power — routed to the BOARD, not one decider
    views = list(_by_uid().values())
    sc_now = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    while len(_S["villagers"]) < min(_target_villagers(), w["pop_cap"]):
        ok, reason = G.may_spawn(views)
        uid = _new_vid()
        ctx = {"aligned": True, "affordable": ok, "within_budget": sc_now["within_budget"],
               "spent": G.spent(views), "cap": G.TOKEN_CAP}
        bv = board.vote(f"create {uid}", ctx)
        _S["last_vote"] = bv
        if not bv["approved"]:
            _S["side_effects"] += 1
            anchor.record(t, "board", f"[{bv['tally']}] BLOCKED {uid} — {reason if not ok else 'no quorum'}")
            break
        res = anchor.best_known_yield() or brain.choose_resource(len(_S["villagers"]), w)
        sim.spawn(_GRAPH, uid, "villager", resource=res)
        economy.enlist(uid)
        _S["villagers"].append(uid)
        anchor.observe_yield(res, sim.effective_yield(res))
        anchor.record(t, "board", f"[{bv['tally']}] approved -> {uid} gather {res}")
        views = list(_by_uid().values())

    status = _by_uid()
    for uid in _S["villagers"]:
        u = status.get(uid)
        if u and u.status in ("awaiting_approval", "idle"):
            res = _S["orders"].get(uid) or D._scarcest(sim.world())     # honour standing orders
            sim.resume(_GRAPH, uid, f"gather:{res}")
            economy.credit(uid, sim.QUOTA * sim.effective_yield(res))   # measured contribution
            anchor.observe_yield(res, sim.effective_yield(res))
            anchor.record(t, "retask", f"{uid} idle -> gather {res}")
            promo = economy.evaluate(uid)                                # status earned by results
            if promo:
                anchor.record(t, "promote", f"{uid} promoted -> {promo}")

    kind = D._next_build(sim.world(), len(_S["villagers"]))
    if kind:
        done, msg = sim.build_structure(kind)
        anchor.record(t, "build" if done else "waste", msg)
        if not done:
            _S["side_effects"] += 1

    w = sim.world()
    aligned = V.AGES.index(w["age"]) < V.AGES.index(_vision().target_age)
    if aligned and sim.NEXT_AGE.get(w["age"]) and brain.should_advance(w, sim.ADVANCE_COST) \
            and not _pending_herald():
        _S["heralds"] += 1
        sim.spawn(_GRAPH, f"herald-{_S['heralds']:02d}", "herald")
        anchor.record(t, "gate", "herald parked at the gate — awaiting your approval to advance the Age")

    _fleet_speaks()                              # agents / governor raise chat messages

    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
    if sc["goal_met"]:
        status = _by_uid()
        for uid in list(_S["villagers"]):
            u = status.get(uid)
            if u and u.status in ("awaiting_approval", "idle"):
                sim.resume(_GRAPH, uid, "dismiss")
                economy.retire(uid)
                _S["villagers"].remove(uid)
                anchor.record(t, "reap", f"{uid} retired — vision met")
        anchor.record(t, "goal", f"VISION MET at {sc['progress']}%")
        _S["goal_met"] = True


def _drive():
    while True:
        time.sleep(TICK)
        with _LOCK:
            if not _S["goal_met"]:
                try:
                    _one_turn()
                except Exception as e:            # never let the loop die silently
                    anchor.record(_S["turn"], "error", str(e)[:80])


def _snapshot() -> dict:
    with _LOCK:
        views = G.units(_GRAPH, _CP)
        sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"], _vision())
        spend_ratio = G.spent(views) / G.TOKEN_CAP if G.TOKEN_CAP else 1.0
        proposal = board.propose_vision(sc, spend_ratio, _S["vision_key"])
        roster_by = {r["agent"]: r for r in economy.roster()}
        agents = []
        for u in views:
            r = roster_by.get(u.unit_id)
            if not r:
                continue                                  # only enlisted agents
            budget = r["budget"] or 1
            ratio = u.tokens / budget
            agents.append({
                "uid": u.unit_id, "role": r["role"], "tier": r["tier"],
                "task": u.task, "node": u.node, "status": u.status,
                "tokens": u.tokens, "budget": r["budget"], "contribution": r["contribution"],
                "health": max(0, round(100 * (1 - min(1, ratio)))),
                "overwork": ratio > 0.8, "pending": bool(u.pending),
                "order": _S["orders"].get(u.unit_id),
            })
        return {
            "vision": {"name": _vision().name, **sc},
            "strategy": {
                "current_key": _S["vision_key"], "current": _vision().name,
                "options": [{"key": k, "name": v.name} for k, v in V.VISIONS.items()],
                "proposal": proposal,
            },
            "world": sim.world(),
            "structures": sim.structures(),
            "buildable": list(sim.STRUCTURES.keys()),
            "units": [asdict(u) for u in views],
            "spent": G.spent(views), "cap": G.TOKEN_CAP,
            "events": anchor.event_log(150),
            "event_count": anchor.event_count(),
            "knowledge": anchor.summary(),
            "roster": economy.roster(),
            "agents": agents,
            "external": anchor.external(12),
            "external_count": anchor.external_count(),
            "brain": "deepseek" if brain.available() else "rule-based",
            "board": {"governors": list(board.GOVERNORS), "quorum": board.QUORUM,
                      "last": _S["last_vote"]},
            "cap_proposal": board.propose_cap(spend_ratio, sc, G.TOKEN_CAP),
            "resources": list(sim.RESOURCES),
            "turn": _S["turn"], "goal_met": _S["goal_met"],
        }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/agents":
            return self._send(200, AGENTS_PAGE, "text/html; charset=utf-8")
        if self.path == "/chats":
            return self._send(200, CHATS_PAGE, "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/chats":
            with _LOCK:
                return self._send(200, json.dumps(_chats_snapshot()))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/api/resume":
            body = self._read_json()
            uid, decision = body.get("unit_id"), body.get("decision")
            if not uid or decision not in ("approve", "reject", "dismiss", "back-to-work"):
                return self._send(400, json.dumps({"error": "unit_id and a valid decision required"}))
            with _LOCK:
                sim.resume(_GRAPH, uid, decision)
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
        if self.path == "/api/chat":
            body = self._read_json()
            thread, text = body.get("thread", ""), (body.get("body") or "").strip()
            if not thread or not text:
                return self._send(400, json.dumps({"error": "thread and body required"}))
            with _LOCK:
                _chat_send(thread, text)
                return self._send(200, json.dumps(_chats_snapshot()))
        if self.path == "/api/ingest":
            body = self._read_json()
            topic = (body.get("topic") or "").strip()
            if not topic:
                return self._send(400, json.dumps({"error": "topic required"}))
            with _LOCK:
                fact = brain.research(topic) or (body.get("fact") or "").strip()
                source = "deepseek" if (brain.available() and fact) else (body.get("source") or "operator")
                if not fact:
                    return self._send(400, json.dumps({"error": "no model configured; provide a fact"}))
                anchor.ingest(topic, source, fact)   # Article VI: source recorded, never executed
                anchor.record(_S["turn"], "ingest", f"external knowledge on '{topic}' from {source}")
            return self._send(200, json.dumps(_snapshot()))
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
    <span>Add villager</span>
    <select id=addres><option value="">auto</option><option>food</option><option>wood</option><option>gold</option></select>
    <button class=ok onclick=addAgent()>Add</button>
    <span>Cap</span><input id=capin type=number step=10000 style="width:110px">
    <button onclick=setCap()>Set</button>
    <span id=capprop class=why></span>
    <span>Learn</span><input id=topicin placeholder="topic" style="width:130px">
    <button onclick=ingest()>Ingest</button>
    <span id=brainmode class=navlink style="border-color:var(--line)"></span>
  </div>
</header>
<main>
  <div class="card wide"><h2>Fleet</h2>
    <table><thead><tr><th>Unit</th><th>Task</th><th>State</th><th class=num>Rounds</th><th class=num>Compute</th></tr></thead>
      <tbody id=fleet></tbody></table></div>
  <div class="card wide"><h2>Command queue &mdash; you gate the irreversible Age-up</h2>
    <table><tbody id=queue></tbody></table><div class=empty id=queueEmpty>Nothing awaiting a human.</div></div>
  <div class=card><h2>Development</h2><div class=tech id=tech></div></div>
  <div class=card><h2>Knowledge (the anchor)</h2><div class=kv id=know></div></div>
  <div class="card wide"><h2>Roster &mdash; status earned by measured contribution</h2>
    <table><thead><tr><th>Agent</th><th>Role</th><th class=num>Contribution</th><th>Progress to promotion</th><th class=num>Budget</th></tr></thead>
      <tbody id=roster></tbody></table></div>
  <div class=card><h2>Board of Governors &mdash; quorum for agent creation</h2><div class=kv id=boardp></div></div>
  <div class="card wide"><h2>Strategy &mdash; the Board proposes, you adopt the Vision</h2>
    <div id=proposal></div>
    <div class=tech id=visions></div></div>
  <div class="card wide"><h2>Event log &mdash; permanent audit trail (<span id=evcount>0</span> total)</h2><div class=log id=log></div></div>
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
  tech.innerHTML=d.buildable.map(k=>`<span class="chip ${d.structures[k]?'on':''}">${k}${d.structures[k]>1?' ×'+d.structures[k]:''}${d.structures[k]?' ✓':''}</span>`).join('');
  const kn=d.knowledge;
  know.innerHTML=`<div><span>facts learned</span><b>${kn.facts}</b></div>
    <div><span>best resource</span><b>${kn.best_resource||'&mdash;'}</b></div>
    ${Object.entries(kn.learned_yields||{}).map(([r,y])=>`<div><span>avg ${r} yield</span><b>${y}</b></div>`).join('')}
    <div><span>external facts (${esc(d.brain)})</span><b>${d.external_count||0}</b></div>
    ${(d.external||[]).slice(0,3).map(x=>`<div><span>${esc(x.topic)} <i>[${esc(x.source)}]</i></span><b title="${esc(x.fact)}">&#9432;</b></div>`).join('')}`;
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
  log.innerHTML=d.events.map(e=>{const m=e.match(/\\[([\\w-]+)\\]/);const k=m?m[1]:'';return `<div class="k-${k}">${esc(e)}</div>`}).join('');
  evcount.textContent=(d.event_count||0).toLocaleString();
  if(document.activeElement!==capin) capin.value=d.cap;
  const cp=d.cap_proposal;
  capprop.innerHTML=cp?`Board: ${esc(cp.action)} to <b>${cp.cap.toLocaleString()}</b> <button onclick="applyCap(${cp.cap})">Apply</button>`:'';
}
async function post(url,body){await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});tick();}
function decide(unit_id,decision){post('/api/resume',{unit_id,decision});}
function adopt(vision){post('/api/vision',{vision});}
function addAgent(){post('/api/spawn',{resource:addres.value});}
function setCap(){const v=parseInt(capin.value);if(v)post('/api/cap',{token_cap:v});}
function applyCap(v){post('/api/cap',{token_cap:v});}
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
  <div class=ops>
    <span>Add</span>
    <select id=addres><option value="">auto</option><option>food</option><option>wood</option><option>gold</option></select>
    <button class=ok onclick=addAgent()>Add villager</button>
    <span>Cap</span><input id=capin type=number step=10000 style="width:110px"><button onclick=setCap()>Set</button>
  </div>
</header>
<main id=grid></main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const RES=['food','wood','gold'];
function hcolor(h){return h>50?'var(--ok)':h>20?'var(--warn)':'var(--bad)'}
async function tick(){
  let d; try{ d=await (await fetch('/api/state')).json() }catch(e){ return }
  if(document.activeElement!==capin) capin.value=d.cap;
  const a=d.agents||[];
  grid.innerHTML=a.length? a.map(x=>`<div class="a ${x.overwork?'overwork':''}">
    <h3>${esc(x.uid)} <span class="badge t${x.tier}">${esc(x.role)}</span> ${x.overwork?'<span class=flag>&#9888; OVERWORKING</span>':''}</h3>
    <div class=work>working on: <b>${esc(x.task)}</b> &middot; node ${esc(x.node)} &middot; ${esc(x.status)}${x.order?` &middot; order: gather ${esc(x.order)}`:''}</div>
    <div>health ${x.health}%</div>
    <div class=hbar><div class=hfill style="width:${x.health}%;background:${hcolor(x.health)}"></div></div>
    <div class=stat><span>compute / budget</span><b>${x.tokens.toLocaleString()} / ${x.budget.toLocaleString()}</b></div>
    <div class=stat><span>contribution</span><b>${x.contribution.toLocaleString()}</b></div>
    <div class=ctl>
      <select id="ord-${esc(x.uid)}">${RES.map(r=>`<option>${r}</option>`).join('')}</select>
      <button onclick="order('${esc(x.uid)}')">Send order</button>
      <button class=no onclick="term('${esc(x.uid)}')">Terminate</button>
    </div></div>`).join('') : '<div class=empty>No agents enlisted yet — the fleet is being staffed, or add one above.</div>';
}
async function post(u,b){await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});tick();}
function addAgent(){post('/api/spawn',{resource:addres.value});}
function setCap(){const v=parseInt(capin.value);if(v)post('/api/cap',{token_cap:v});}
function order(uid){const r=document.getElementById('ord-'+uid).value;post('/api/order',{unit_id:uid,resource:r});}
function term(uid){if(confirm('Terminate '+uid+'? This retires the agent (gated).'))post('/api/terminate',{unit_id:uid});}
tick(); setInterval(tick,1000);
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
<header><h1>&#9670; CHATS</h1><a href="/">Console</a><a href="/agents">Agent Health</a></header>
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
let sel='chief', data={participants:[],threads:{},broadcast:[]};
async function load(){ try{ data=await (await fetch('/api/chats')).json() }catch(e){ return } render(); }
function render(){
  const parts=[{key:'all',name:'All (broadcast)',role:'everyone'}].concat(data.participants);
  side.innerHTML=parts.map(p=>{const last=p.key==='all'?(data.broadcast.slice(-1)[0]||{}).body||'':p.last||'';
    return `<div class="p ${p.key===sel?'sel':''}" onclick="pick('${p.key}')">
      <div class=n><b>${esc(p.name)}</b><span class=badge>${esc(p.role)}</span></div>
      <div class=last>${esc(last||'—')}</div></div>`}).join('');
  const msgs=sel==='all'?data.broadcast:(data.threads[sel]||[]);
  const box=document.getElementById('msgs');
  box.innerHTML=msgs.length?msgs.map(m=>`<div class="m ${m.mine?'mine':''}">
    <div class=s>${m.mine?'You':esc(m.sender)}</div>${esc(m.body)}</div>`).join(''):'<div class=hint>No messages yet — say hello.</div>';
  box.scrollTop=box.scrollHeight;
}
function pick(k){ sel=k; render(); }
async function send(){ const b=document.getElementById('box'); const t=b.value.trim(); if(!t) return; b.value='';
  data=await (await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thread:sel,body:t})})).json(); render(); }
load(); setInterval(load,2000);
</script>
</html>"""


def main(argv):
    threading.Thread(target=_drive, daemon=True).start()
    port = int(os.environ.get("PORT", "8788"))
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"AoE governor console -> http://{host}:{port}  (director auto-playing; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])

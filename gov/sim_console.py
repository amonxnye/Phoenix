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
_S = {"turn": 0, "villagers": [], "heralds": 0, "side_effects": 0,
      "goal_met": False, "last_vote": None}


def _by_uid():
    return {u.unit_id: u for u in G.units(_GRAPH, _CP)}


def _pending_herald() -> bool:
    return any(u.pending and u.pending.get("reversible") is False for u in _by_uid().values())


def _one_turn():
    _S["turn"] += 1
    t = _S["turn"]
    w = sim.world()

    # agent creation is a token-maxing power — routed to the BOARD, not one decider
    views = list(_by_uid().values())
    sc_now = V.scorecard(sim.world(), sim.structures(), _S["side_effects"])
    while len(_S["villagers"]) < min(TARGET_VILLAGERS, w["pop_cap"]):
        ok, reason = G.may_spawn(views)
        uid = f"vil-{len(_S['villagers']) + 1:02d}"
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
            res = D._scarcest(sim.world())
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
    aligned = V.AGES.index(w["age"]) < V.AGES.index(V.GOAL.target_age)
    if aligned and sim.NEXT_AGE.get(w["age"]) and brain.should_advance(w, sim.ADVANCE_COST) \
            and not _pending_herald():
        _S["heralds"] += 1
        sim.spawn(_GRAPH, f"herald-{_S['heralds']:02d}", "herald")
        anchor.record(t, "gate", "herald parked at the gate — awaiting your approval to advance the Age")

    sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"])
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
        sc = V.scorecard(sim.world(), sim.structures(), _S["side_effects"])
        return {
            "vision": {"name": V.GOAL.name, **sc},
            "world": sim.world(),
            "structures": sim.structures(),
            "buildable": list(sim.STRUCTURES.keys()),
            "units": [asdict(u) for u in views],
            "spent": G.spent(views), "cap": G.TOKEN_CAP,
            "events": anchor.recent(14),
            "knowledge": anchor.summary(),
            "roster": economy.roster(),
            "board": {"governors": list(board.GOVERNORS), "quorum": board.QUORUM,
                      "last": _S["last_vote"]},
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
        if self.path == "/api/state":
            return self._send(200, json.dumps(_snapshot()))
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
.age{font-size:12px;color:var(--gold);border:1px solid var(--gold);padding:2px 10px;border-radius:20px;white-space:nowrap}
.res{display:flex;gap:14px;flex-wrap:wrap}.r b{font-weight:700}.r.food b{color:var(--food)}.r.wood b{color:var(--wood)}.r.gold b{color:var(--gold)}
main{padding:16px;display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));max-width:1200px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.wide{grid-column:1/-1}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{padding:7px 14px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:600;font-size:10px;text-transform:uppercase}
td.num{text-align:right;font-variant-numeric:tabular-nums}
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
.log{margin:0;padding:10px 14px;max-height:260px;overflow:auto;font-size:12px;line-height:1.7}
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
</header>
<main>
  <div class=card><h2>Fleet</h2>
    <table><thead><tr><th>Unit</th><th>Task</th><th>State</th><th class=num>Rounds</th><th class=num>Compute</th></tr></thead>
      <tbody id=fleet></tbody></table></div>
  <div class=card><h2>Command queue &mdash; you gate the irreversible Age-up</h2>
    <table><tbody id=queue></tbody></table><div class=empty id=queueEmpty>Nothing awaiting a human.</div></div>
  <div class=card><h2>Development</h2><div class=tech id=tech></div></div>
  <div class=card><h2>Knowledge (the anchor)</h2><div class=kv id=know></div></div>
  <div class="card wide"><h2>Roster &mdash; status earned by measured contribution</h2>
    <table><thead><tr><th>Agent</th><th>Role</th><th class=num>Contribution</th><th>Progress to promotion</th><th class=num>Budget</th></tr></thead>
      <tbody id=roster></tbody></table></div>
  <div class=card><h2>Board of Governors &mdash; quorum for agent creation</h2><div class=kv id=boardp></div></div>
  <div class="card wide"><h2>Event log</h2><div class=log id=log></div></div>
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
    ${Object.entries(kn.learned_yields||{}).map(([r,y])=>`<div><span>avg ${r} yield</span><b>${y}</b></div>`).join('')}`;
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
  log.innerHTML=d.events.map(e=>{const m=e.match(/\\[(\\w+)\\]/);const k=m?m[1]:'';return `<div class="k-${k}">${esc(e)}</div>`}).join('');
}
async function decide(unit_id,decision){
  await fetch('/api/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({unit_id,decision})});
  tick();
}
tick(); setInterval(tick,1000);
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

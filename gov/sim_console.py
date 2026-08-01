"""Live operator console for the Age of Empires MVP.

Renders the World (the oracle) and the villager fleet from live state — resource
meters, the current Age, the idle-villager alert, and an approval queue where you
drain villager orders and gate the irreversible Age-up. Standard library only.

Run:  python3 gov/sim_console.py --seed   # then open http://127.0.0.1:8788
"""

import json
import os
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brain as B
import governor as G
import sim as S

_CP = S.connect()
_GRAPH = S.build(_CP)


def _seed_if_empty():
    if G._thread_ids(_CP):
        return
    w = S.world()
    for i in range(3):
        S.spawn(_GRAPH, f"vil-{i+1:02d}", "villager", resource=B.choose_resource(i, w))
    # Stock the treasury so the Age-up is affordable, then send the herald to the gate.
    S._world_add("food", S.ADVANCE_COST["food"])
    S._world_add("gold", S.ADVANCE_COST["gold"])
    S.spawn(_GRAPH, "herald-01", "herald")


def _snapshot() -> dict:
    views = G.units(_GRAPH, _CP)
    allowed, reason = G.may_spawn(views)
    return {
        "world": S.world(),
        "units": [asdict(u) for u in views],
        "spent": G.spent(views),
        "cap": G.TOKEN_CAP,
        "may_spawn": allowed,
        "cap_reason": reason,
        "idle_count": len(G.idle(views)),
        "advance_cost": S.ADVANCE_COST,
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
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
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
            S.resume(_GRAPH, uid, decision)
            return self._send(200, json.dumps(_snapshot()))
        self._send(404, json.dumps({"error": "not found"}))


PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>The Governor — Age of Empires</title>
<style>
:root{--bg:#120d08;--panel:#1c150d;--line:#3a2c18;--ink:#f0e6d2;--dim:#b09a72;
--food:#e05a5a;--wood:#8b5a2b;--gold:#e0b23a;--run:#3b82f6;--wait:#f59e0b;--idle:#ef4444;--done:#22c55e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:16px 20px;border-bottom:2px solid var(--line);display:flex;gap:20px;
align-items:center;flex-wrap:wrap;background:linear-gradient(180deg,#241a0f,#1c150d)}
h1{font-size:15px;margin:0;letter-spacing:1px}
.age{font-size:13px;color:var(--gold);border:1px solid var(--gold);padding:3px 12px;border-radius:20px}
.res{display:flex;gap:18px;flex:1;flex-wrap:wrap;min-width:260px}
.r{display:flex;gap:7px;align-items:center;font-variant-numeric:tabular-nums}
.r b{font-weight:700}.r.food b{color:var(--food)}.r.wood b{color:var(--wood)}.r.gold b{color:var(--gold)}
.cap{min-width:200px}.bar{height:9px;background:#0e0a05;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.fill{height:100%;background:linear-gradient(90deg,#3b82f6,#22c55e)}.halt .fill{background:linear-gradient(90deg,#f59e0b,#ef4444)}
.cap small{color:var(--dim)}
main{padding:20px;display:grid;gap:18px;max-width:1080px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;
padding:11px 16px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{padding:9px 16px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-flex;gap:6px;align-items:center;padding:2px 9px;border-radius:20px;font-size:12px}
.dot{width:8px;height:8px;border-radius:50%}
.awaiting_approval{color:var(--wait)}.awaiting_approval .dot{background:var(--wait)}
.idle{color:var(--idle)}.idle .dot{background:var(--idle)}
.done{color:var(--done)}.done .dot{background:var(--done)}.running{color:var(--run)}.running .dot{background:var(--run)}
.alert{margin:0;padding:11px 16px;background:#2a1410;color:#fca5a5;border-bottom:1px solid var(--line);display:none}
.alert.on{display:block}
button{font:inherit;padding:5px 12px;border-radius:6px;border:1px solid var(--line);cursor:pointer;background:#26200f;color:var(--ink)}
button.ok{border-color:#3a5a1a;background:#1a2a0f;color:#a8e086}button.no{border-color:#5b2a1a;background:#2a140f;color:#fca5a5}
.gate td{background:#231705}.empty{padding:16px;color:var(--dim)}.foot{color:var(--dim);font-size:12px;text-align:center}
</style>
<header>
  <h1>◆ THE GOVERNOR</h1>
  <span class=age id=age>—</span>
  <div class=res id=res></div>
  <div class="cap" id=capWrap>
    <div class=bar><div class=fill id=fill></div></div>
    <small id=capText>—</small>
  </div>
</header>
<main>
  <div class=card>
    <h2>Fleet</h2>
    <p class=alert id=idleAlert></p>
    <table><thead><tr><th>Unit</th><th>Task</th><th>State</th><th>Node</th>
      <th class=num>Rounds</th><th class=num>Compute</th><th class=num>Age</th></tr></thead>
      <tbody id=fleet></tbody></table>
  </div>
  <div class=card>
    <h2>Command queue — approve orders &amp; gate the irreversible Age-up</h2>
    <table><tbody id=queue></tbody></table>
    <div class=empty id=queueEmpty>Nothing awaiting a human.</div>
  </div>
  <p class=foot>live over the checkpointer + World · the game state is the oracle · no fixtures</p>
</main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function tick(){
  let d; try{ d=await (await fetch('/api/state')).json() }catch(e){ return }
  age.textContent='Age: '+d.world.age;
  res.innerHTML=`<span class="r food">FOOD <b>${d.world.food}</b></span>
    <span class="r wood">WOOD <b>${d.world.wood}</b></span>
    <span class="r gold">GOLD <b>${d.world.gold}</b></span>`;
  const pct=Math.min(100,100*d.spent/d.cap);
  fill.style.width=pct+'%'; capWrap.classList.toggle('halt',!d.may_spawn);
  capText.textContent=`compute ${d.spent.toLocaleString()} / ${d.cap.toLocaleString()}`+(d.may_spawn?'':' · HALTED');
  idleAlert.classList.toggle('on',d.idle_count>0);
  if(d.idle_count>0) idleAlert.textContent=`IDLE — ${d.idle_count} villager(s) finished gathering, parked awaiting orders.`;
  fleet.innerHTML=d.units.map(u=>`<tr>
    <td>${esc(u.unit_id)}</td><td>${esc(u.task)}</td>
    <td><span class="pill ${u.status}"><span class=dot></span>${u.status}</span></td>
    <td>${esc(u.node)}</td><td class=num>${u.steps}</td>
    <td class=num>${u.tokens.toLocaleString()}</td><td class=num>${u.age_s.toFixed(1)}s</td></tr>`).join('');
  const pend=d.units.filter(u=>u.pending);
  queueEmpty.style.display=pend.length?'none':'block';
  queue.innerHTML=pend.map(u=>{const irr=u.pending&&u.pending.reversible===false;
    return `<tr class="${irr?'gate':''}">
      <td>${esc(u.unit_id)}</td>
      <td>${irr?'! ':''}${esc((u.pending&&u.pending.action)||u.task)}</td>
      <td style=text-align:right>
        <button class=ok onclick="decide('${esc(u.unit_id)}','${irr?'approve':'back-to-work'}')">${irr?'Approve':'Re-task'}</button>
        <button class=no onclick="decide('${esc(u.unit_id)}','${irr?'reject':'dismiss'}')">${irr?'Reject':'Dismiss'}</button>
      </td></tr>`}).join('');
}
async function decide(unit_id,decision){
  await fetch('/api/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({unit_id,decision})});
  tick();
}
tick(); setInterval(tick,1000);
</script>
</html>"""


def main(argv):
    # Seed on --seed, or when SEED=1 in the environment (handy for a hosted deploy).
    if "--seed" in argv or os.environ.get("SEED") == "1":
        _seed_if_empty()
    # Railway (and most PaaS) inject $PORT and require binding 0.0.0.0.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8788"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"AoE governor console → http://{host}:{port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])

"""Live operator console — closes AC-12.

Serves the governor's read view over HTTP and a single-page RTS-style surface that
renders from live ``governor.units()`` instead of fixtures. Standard library only:
no web framework, so ``requirements.txt`` stays as-is and the whole thing is auditable.

Run:  python3 gov/console.py            # then open http://127.0.0.1:8787
      python3 gov/console.py --seed     # spawn a demo fleet first
"""

import json
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import governor as G
import runtime as R

_CP = R.connect()
_GRAPH = R.build(_CP)


def _seed_if_empty():
    """Spawn a demo fleet only if the checkpointer has no threads yet."""
    if G._thread_ids(_CP):
        return
    demo = [
        ("unit-01", "refactor auth module"),
        ("unit-02", "add retry to payment client"),
        ("unit-03", "migrate config loader"),
        ("unit-04", "fix flaky integration test"),
    ]
    for uid, task in demo:
        R.spawn(_GRAPH, uid, task)


def _snapshot() -> dict:
    views = G.units(_GRAPH, _CP)
    allowed, reason = G.may_spawn(views)
    return {
        "units": [asdict(u) for u in views],
        "spent": G.spent(views),
        "cap": G.TOKEN_CAP,
        "may_spawn": allowed,
        "cap_reason": reason,
        "idle_count": len(G.idle(views)),
        "awaiting_count": len(G.approvals(views)),
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

    def log_message(self, *_):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/units":
            return self._send(200, json.dumps(_snapshot()))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        body = self._read_json()
        if self.path == "/api/resume":
            uid, decision = body.get("unit_id"), body.get("decision")
            if decision not in ("approve", "reject") or not uid:
                return self._send(400, json.dumps({"error": "unit_id and decision (approve|reject) required"}))
            R.resume(_GRAPH, uid, decision)
            return self._send(200, json.dumps(_snapshot()))
        if self.path == "/api/spawn":
            # Demonstrates the hard cap: spawning is refused, not warned, at the ceiling.
            allowed, reason = G.may_spawn(G.units(_GRAPH, _CP))
            if not allowed:
                return self._send(409, json.dumps({"error": reason}))
            uid = body.get("unit_id") or f"unit-{len(G._thread_ids(_CP)) + 1:02d}"
            R.spawn(_GRAPH, uid, body.get("task") or "ad-hoc task")
            return self._send(200, json.dumps(_snapshot()))
        self._send(404, json.dumps({"error": "not found"}))


PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>The Governor — operator console</title>
<style>
:root{--bg:#0b0f14;--panel:#131a22;--line:#243240;--ink:#e6edf3;--dim:#8598a8;
--run:#3b82f6;--wait:#f59e0b;--idle:#ef4444;--done:#22c55e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;
gap:24px;align-items:center;flex-wrap:wrap}
h1{font-size:15px;margin:0;letter-spacing:.5px}
.meter{flex:1;min-width:220px}.bar{height:10px;background:#0a2540;border:1px solid var(--line);
border-radius:6px;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,#3b82f6,#22c55e)}
.meter small{color:var(--dim)}
.halt .fill{background:linear-gradient(90deg,#f59e0b,#ef4444)}
main{padding:20px;display:grid;gap:20px;grid-template-columns:1fr;max-width:1100px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);
margin:0;padding:12px 16px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{padding:9px 16px;text-align:left;
border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--dim);font-weight:600;
font-size:11px;text-transform:uppercase;letter-spacing:.5px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-flex;gap:6px;align-items:center;padding:2px 9px;border-radius:20px;
font-size:12px}.dot{width:8px;height:8px;border-radius:50%}
.running{color:var(--run)}.running .dot{background:var(--run)}
.awaiting_approval{color:var(--wait)}.awaiting_approval .dot{background:var(--wait)}
.idle{color:var(--idle)}.idle .dot{background:var(--idle)}
.done{color:var(--done)}.done .dot{background:var(--done)}
.alert{margin:0;padding:12px 16px;background:#2a1416;color:#fca5a5;border-bottom:1px solid var(--line);
display:none}.alert.on{display:block}
.q td{border-bottom:1px solid var(--line)}
button{font:inherit;padding:5px 12px;border-radius:6px;border:1px solid var(--line);
cursor:pointer;background:#1b2530;color:var(--ink)}
button.ok{border-color:#14532d;background:#0f2a1a;color:#86efac}
button.no{border-color:#5b1a1a;background:#2a1010;color:#fca5a5}
.empty{padding:16px;color:var(--dim)}.foot{color:var(--dim);font-size:12px;text-align:center}
</style>
<header>
  <h1>◆ THE GOVERNOR</h1>
  <div class="meter" id=meterWrap>
    <div class=bar><div class=fill id=fill></div></div>
    <small id=meterText>—</small>
  </div>
</header>
<main>
  <div class=card>
    <h2>Fleet</h2>
    <p class=alert id=idleAlert></p>
    <table><thead><tr><th>Unit</th><th>Task</th><th>State</th><th>Node</th>
      <th class=num>Steps</th><th class=num>Tokens</th><th class=num>Age</th></tr></thead>
      <tbody id=fleet></tbody></table>
  </div>
  <div class=card>
    <h2>Approval queue</h2>
    <table><tbody id=queue></tbody></table>
    <div class=empty id=queueEmpty>Nothing awaiting a human.</div>
  </div>
  <p class=foot>polling /api/units · live over the checkpointer, no fixtures</p>
</main>
<script>
const G={units:'/api/units',resume:'/api/resume'};
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function tick(){
  let d; try{ d=await (await fetch(G.units)).json() }catch(e){ return }
  const pct=Math.min(100,100*d.spent/d.cap);
  fill.style.width=pct+'%';
  meterWrap.classList.toggle('halt',!d.may_spawn);
  meterText.textContent=`${d.spent.toLocaleString()} / ${d.cap.toLocaleString()} tokens`
    +(d.may_spawn?'':'  · SPAWN HALTED');
  idleAlert.classList.toggle('on',d.idle_count>0);
  if(d.idle_count>0) idleAlert.textContent=
    `⏹ ${d.idle_count} unit(s) finished and parked on a human who isn't looking — reclaim or resolve.`;
  fleet.innerHTML=d.units.map(u=>`<tr>
    <td>${esc(u.unit_id)}</td><td>${esc(u.task)}</td>
    <td><span class="pill ${u.status}"><span class=dot></span>${u.status}</span></td>
    <td>${esc(u.node)}</td><td class=num>${u.steps}</td>
    <td class=num>${u.tokens.toLocaleString()}</td><td class=num>${u.age_s.toFixed(1)}s</td>
  </tr>`).join('');
  const pend=d.units.filter(u=>u.status==='awaiting_approval'||u.status==='idle');
  queueEmpty.style.display=pend.length?'none':'block';
  queue.innerHTML=pend.map(u=>`<tr class=q>
    <td>${esc(u.unit_id)}</td>
    <td>${esc((u.pending&&u.pending.action)||u.task)}</td>
    <td><span class="pill ${u.status}"><span class=dot></span>${u.status}</span></td>
    <td style=text-align:right>
      <button class=ok onclick="decide('${esc(u.unit_id)}','approve')">Approve</button>
      <button class=no onclick="decide('${esc(u.unit_id)}','reject')">Reject</button>
    </td></tr>`).join('');
}
async function decide(unit_id,decision){
  await fetch(G.resume,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({unit_id,decision})});
  tick();
}
tick(); setInterval(tick,1000);
</script>
</html>"""


def main(argv):
    if "--seed" in argv:
        _seed_if_empty()
    host, port = "127.0.0.1", 8787
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"governor console → http://{host}:{port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])

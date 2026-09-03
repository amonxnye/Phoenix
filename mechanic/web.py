"""The landing page — Milestone 6, pulled forward because a mechanic that only speaks
CLI is half a product.

Mounted inside the Phoenix console (which owns the process, the port and the token
gate) but owned entirely here: the console's shim is five lines that delegate. The
HTML carries the same `/*TOKENS*/` placeholder every console page does, so it wears
the house palette when mounted and still renders standalone.

Two rules the web layer adds on top of the CLI, because a form on a public host is a
different threat than a shell on your own machine:

- **URLs only, never paths.** A local path from a browser would read any directory
  the server can. The CLI keeps paths; the web keeps GitHub URLs, and ingest.py
  enforces what a URL may be.
- **One analysis at a time.** A second request while one runs gets 409 and the name
  of what is running, not a queue that fills the disk while nobody is watching.
"""

import json
import threading
import time
from urllib.parse import parse_qs, urlparse

from . import analyse, charter, ingest, store, watch

# The interface the console calls. Declared, because the mechanic run on itself
# reported these unreachable — correctly: their caller lives outside the package. An
# `__all__` is how the index is told where execution enters (Charter §4), and it is
# the fix the finding itself proposed.
__all__ = ["handle_get", "handle_post", "status", "start_watch", "PAGE"]

_LOCK = threading.Lock()
_ACTIVE = {"status": "idle", "url": "", "name": "", "started": 0.0, "note": "",
           "run_id": "", "history": []}


def _json(code: int, obj) -> tuple:
    return code, "application/json", json.dumps(obj)


def status() -> dict:
    out = dict(_ACTIVE)
    out["elapsed"] = round(time.time() - _ACTIVE["started"], 1) if _ACTIVE["started"] else 0
    out["history"] = _ACTIVE["history"][-8:]
    return out


def _latest_runs() -> dict:
    """repo_id → its most recent run. The landing page shows each repository's
    current state, not every finding it ever produced across re-runs."""
    return {r["id"]: (store.runs(r["id"], 1) or [None])[0] for r in store.repos()}


def _repo_rows() -> list[dict]:
    rows = []
    for r in store.repos():
        last = (store.runs(r["id"], 1) or [{}])[0]
        n_f = len(store.findings(run_id=last["id"])) if last else 0
        n_g = len(store.gaps(run_id=last["id"])) if last else 0
        rows.append({**r, "last_run": last.get("id", ""), "last_status": last.get("status", ""),
                     "last_at": last.get("finished_at") or last.get("started_at") or 0,
                     "findings": n_f, "refusals": n_g, "note": last.get("note", "")})
    rows.sort(key=lambda x: -(x["last_at"] or 0))
    return rows


def _findings_current(repo_id: str = "") -> tuple[list, list]:
    """Findings and refusals from each repository's LATEST run only."""
    names = {r["id"]: r["name"] for r in store.repos()}
    finds, gaps = [], []
    for rid, last in _latest_runs().items():
        if not last or (repo_id and rid != repo_id):
            continue
        for f in store.findings(run_id=last["id"]):
            f["repo"] = names.get(rid, rid)
            finds.append(f)
        for g in store.gaps(run_id=last["id"]):
            g["repo"] = names.get(rid, rid)
            gaps.append(g)
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    finds.sort(key=lambda f: (0 if f["basis"] == "machine-verified" else 1,
                              sev.get(f["severity"], 9), f["repo"]))
    return finds, gaps


# ── GET ──────────────────────────────────────────────────────────────────────

def handle_get(path: str) -> tuple:
    u = urlparse(path)
    p, q = u.path, parse_qs(u.query)
    store.init()
    if p == "/mechanic":
        return 200, "text/html; charset=utf-8", PAGE
    if p == "/api/mechanic/summary":
        from . import brainseam
        return _json(200, {"summary": store.summary(), "charter": charter.charter(),
                           "active": status(), "watch": watch.status(),
                           "brain": brainseam.name() if brainseam.available() else ""})
    if p == "/api/mechanic/repos":
        return _json(200, {"repos": _repo_rows()})
    if p == "/api/mechanic/findings":
        finds, gaps = _findings_current((q.get("repo") or [""])[0])
        trail = {}
        for f in finds:
            trail.setdefault(f["run_id"], {})
        for run_id in trail:
            for d in store.decisions(run_id):
                if d.get("finding_id"):
                    trail[run_id].setdefault(d["finding_id"], []).append(
                        {"stage": d["stage"], "actor": d["actor"], "action": d["action"],
                         "rationale": d["rationale"], "model": d["model"]})
        for f in finds:
            f["trail"] = trail.get(f["run_id"], {}).get(f["id"], [])
        return _json(200, {"findings": finds, "gaps": gaps})
    if p == "/api/mechanic/run":
        rid = (q.get("id") or [""])[0]
        r = store.run(rid)
        if not r:
            return _json(404, {"error": "no such run"})
        return _json(200, {"run": r, "findings": store.findings(run_id=rid),
                           "gaps": store.gaps(run_id=rid), "decisions": store.decisions(rid)})
    if p == "/api/mechanic/report":
        rid = (q.get("id") or [""])[0]
        if not store.run(rid):
            return _json(404, {"error": "no such run"})
        return 200, "text/markdown; charset=utf-8", analyse.render(rid)
    if p == "/api/mechanic/status":
        return _json(200, status())
    return _json(404, {"error": "not found"})


# ── POST: the one thing the page can DO ──────────────────────────────────────

def _worker(url: str, name: str, budget_cents: int = -1) -> None:
    try:
        _ACTIVE.update(status="fetching", note="archive over HTTPS, read-only, no credentials")
        c = ingest.clone(url)
        if "error" in c:
            _ACTIVE.update(status="failed", note=c["error"])
            # a failed clone is still a run, so the fleet page shows it was tried
            store.init()
            rid = store.repo_add(url, name=name)
            run_id = store.run_open(rid, "", charter.charter()["stamp"])
            store.gap_add(run_id, "ingest", c["error"])
            store.run_close(run_id, "halted", note=c["error"])
            _ACTIVE["run_id"] = run_id
            return
        _ACTIVE.update(status="analysing", note=f"{c['size_mb']} MB at {c['sha'][:12]}")
        try:
            res = analyse.run(c["path"], name=c["name"], url=url, commit_sha=c["sha"],
                              budget_cents=None if budget_cents < 0 else budget_cents)
        finally:
            ingest.remove(c["tmp"])               # the source was only ever borrowed
        _ACTIVE.update(status=res["status"], run_id=res["run_id"],
                       note=f"{res['findings']} finding(s) ({res.get('judged', 0)} judged), "
                            f"{res['gaps']} refusal(s), {res.get('spend_cents', 0)}¢, "
                            f"{res['seconds']}s" if res["status"] == "complete"
                       else res.get("error", "halted"))
    except Exception as e:                        # noqa: BLE001 — never a silent thread
        _ACTIVE.update(status="failed", note=f"{type(e).__name__}: {e}")
    finally:
        _ACTIVE["history"].append({"url": url, "status": _ACTIVE["status"],
                                   "note": _ACTIVE["note"], "run_id": _ACTIVE["run_id"],
                                   "at": time.time()})
        _ACTIVE["history"] = _ACTIVE["history"][-8:]
        _LOCK.release()


def start_watch() -> bool:
    """Called by the console once it is serving. The CLI never starts the watch."""
    return watch.start()


def handle_post(path: str, body: dict) -> tuple:
    p = urlparse(path).path
    if p == "/api/mechanic/watch":
        rid = (body.get("repo_id") or "").strip()
        if not any(r["id"] == rid for r in store.repos()):
            return _json(404, {"error": "no such repository"})
        on = 1 if body.get("watch") else 0
        try:
            interval = max(900, min(7 * 86400, int(body.get("interval_s") or watch.DEFAULT_INTERVAL_S)))
        except (TypeError, ValueError):
            interval = watch.DEFAULT_INTERVAL_S
        store.repo_set(rid, watch=on, interval_s=interval, halts=0,
                       last_checked=0 if on else time.time())
        return _json(200, {"repo_id": rid, "watch": on, "interval_s": interval,
                           "watch_status": watch.status()})
    if p != "/api/mechanic/analyse":
        return _json(404, {"error": "not found"})
    if body.get("path"):
        return _json(400, {"error": "local paths are a CLI privilege, not a web one — "
                                    "give a https://github.com/<owner>/<repo> URL"})
    url = (body.get("url") or "").strip()
    ok, name = ingest.accepted(url)
    if not ok:
        return _json(400, {"error": name})
    try:                                          # optional; the ceiling is the default
        budget_cents = max(0, min(int(body.get("budget_cents", -1)), 1500))
    except (TypeError, ValueError):
        budget_cents = -1
    if not _LOCK.acquire(blocking=False):
        return _json(409, {"error": f"one at a time — {_ACTIVE['name'] or 'a repository'} "
                                    f"is {_ACTIVE['status']}", "active": status()})
    _ACTIVE.update(status="queued", url=url, name=name, started=time.time(), note="",
                   run_id="")
    threading.Thread(target=_worker, args=(url, name, budget_cents), daemon=True,
                     name="mechanic-analyse").start()
    return _json(202, {"accepted": name, "active": status()})


# ── the page ─────────────────────────────────────────────────────────────────

PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Software Mechanic — Phoenix</title>
<style>
:root{--bg:#171108;--panel:#1f160c;--line:#3a2a14;--ink:#eadfc8;--dim:#9a8a6a;--sub:#c9b890;
      --gold:#e0b04a;--green:#7cc47a;--bad:#e06a5a;--blue:#7aa9d8}
/*TOKENS*/
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1180px;margin:0 auto;padding:16px;display:grid;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:baseline}
.card h2 em{font-style:normal;text-transform:none;letter-spacing:0;color:var(--sub);margin-left:auto;font-size:10px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--line)}
.kpi div{background:var(--panel);padding:10px 14px}.kpi b{display:block;font-size:19px;line-height:1.3}
.kpi span{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.note{padding:10px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
.empty{padding:14px;color:var(--dim)}
form{display:flex;gap:8px;padding:12px 14px;flex-wrap:wrap;align-items:center}
input[type=url]{flex:1 1 320px;background:#120d06;border:1px solid var(--line);color:var(--ink);padding:7px 10px;border-radius:6px;font:inherit}
input[type=url]:focus{outline:none;border-color:var(--gold)}
button{background:#2c2013;border:1px solid var(--gold);color:var(--gold);border-radius:6px;padding:7px 14px;font:inherit;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
#st{flex-basis:100%;color:var(--dim);font-size:11px;min-height:1.2em}
#st.busy{color:var(--gold)}#st.bad{color:var(--bad)}#st.ok{color:var(--green)}
table{width:100%;border-collapse:collapse}th,td{padding:7px 14px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);font-weight:400}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tr.repo{cursor:pointer}tr.repo:hover td{background:#241a0f}tr.repo[aria-selected=true] td{background:#2c2013}
.tag{border-radius:4px;padding:0 7px;font-size:10px;text-transform:uppercase;letter-spacing:.5px;border:1px solid currentColor;white-space:nowrap}
.mv{color:var(--green)}.jd{color:var(--blue)}.s-critical,.s-high{color:var(--bad)}.s-medium{color:var(--gold)}.s-low{color:var(--dim)}
.st-complete{color:var(--green)}.st-halted,.st-failed{color:var(--bad)}.st-analysing,.st-fetching,.st-queued{color:var(--gold)}
details{border-bottom:1px solid var(--line)}details[open]{background:#241a0f55}
summary{padding:9px 14px;cursor:pointer;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;list-style:none}
summary::-webkit-details-marker{display:none}summary:hover{background:#241a0f}
.ttl{font-weight:700;color:var(--gold)}.loc{color:var(--sub);font-size:11px}.rp{color:var(--dim);font-size:11px;margin-left:auto}
.body{padding:4px 14px 12px 30px;color:var(--ink)}.body p{margin:6px 0}.fix{border-left:3px solid var(--green);padding-left:10px}
.ev{color:var(--dim);font-size:11px}code{color:var(--sub)}
.gap{padding:7px 14px;border-bottom:1px solid var(--line);color:var(--sub)}.gap b{color:var(--gold)}
details.tr{border:0;margin-top:6px}details.tr summary{padding:2px 0;color:var(--dim)}details.tr p{margin:2px 0 2px 12px}
select.iv{background:#120d06;color:var(--ink);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:11px}
.st-unchanged{color:var(--dim)}.st-analysed{color:var(--green)}.st-paused,.st-error{color:var(--bad)}
.filters{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--line)}
.ghost{background:#241a0f;border:1px solid var(--line);color:var(--dim);border-radius:5px;padding:2px 10px;font:11px ui-monospace,Menlo,monospace;cursor:pointer}
.ghost[aria-pressed=true]{border-color:var(--gold);color:var(--gold);background:#2c2013}
</style>
<header><h1>&#9670; SOFTWARE MECHANIC</h1>
  <a href="/">Console</a><a href="/comms">Conversations</a><a href="/flow">Decision Flow</a><a href="/rules">Rules</a>
  <span class=meta id=meta>loading&hellip;</span></header>
<main>
  <div class=card><h2>The fleet under watch <em id=ch></em></h2>
    <div class=kpi id=kpi></div>
    <div class=note id=wl></div>
    <div class=note>A mechanic, not a surgeon: it opens the machine, measures what it finds, and says what to
    fix first. <b>Machine-verified</b> findings rest on a graph fact re-checked at report time and lead the
    list. A <b>refusal</b> is a place the analysis declined to conclude, and why &mdash; shown, not hidden.
    Nothing here writes to any repository.</div></div>
  <div class=card><h2>Analyse a repository <em>public GitHub only &middot; read-only archive fetch, no credentials, no git &middot; one at a time</em></h2>
    <form id=f><input type=url id=url placeholder="https://github.com/owner/repo" required>
      <select id=bud title="budget for the judged analysis"><option value=0>machine-verified only ($0)</option><option value=100>$1</option><option value=300 selected>$3</option><option value=1500>$15 max</option></select>
      <button id=go type=submit>Analyse</button><div id=st></div></form></div>
  <div class=card><h2>Repositories <em id=rn></em></h2><div id=repos><div class=empty>loading&hellip;</div></div></div>
  <div class=card><h2>Findings <em>machine-verified first, then by severity</em></h2>
    <div class=filters id=filters></div><div id=finds><div class=empty>loading&hellip;</div></div></div>
  <div class=card><h2>Where the analysis declined to conclude <em id=gn></em></h2><div id=gaps></div></div>
</main>
<script>
/*PALETTE_JS*/
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ago=t=>{if(!t)return'—';const s=Math.max(0,Date.now()/1000-t);
  return s<60?Math.round(s)+'s ago':s<3600?Math.round(s/60)+'m ago':s<86400?Math.round(s/3600)+'h ago':Math.round(s/86400)+'d ago'};
let REPO='', ALL={findings:[],gaps:[]}, BASIS='all';
async function j(u,o){const r=await fetch(u,o);return r.json()}

async function summary(){
  const d=await j('/api/mechanic/summary'); const s=d.summary, a=d.active;
  $('ch').textContent='charter '+d.charter.stamp+(d.charter.drifted?' · DRIFTED':'');
  $('meta').textContent=a.status==='idle'?'idle':(a.name+' · '+a.status+' · '+a.elapsed+'s');
  const w=d.watch||{};
  $('kpi').innerHTML=[['repositories',s.repos],['watched',s.watched],['runs',s.runs],
    ['findings',s.findings],['machine-verified',s.machine_verified],['judged',s.judged],
    ['refusals',s.gaps],['fixed upstream',s.fixed],['spent',((s.spend_cents||0)/100).toFixed(2)+' $']]
    .map(([k,v])=>`<div><b>${v||0}</b><span>${k}</span></div>`).join('');
  $('wl').innerHTML=(d.brain?`brain <b>${esc(d.brain)}</b> &middot; `:'<b>no model configured</b> &mdash; machine-verified only &middot; ')+
    `watch ${w.running?'<b>running</b>':'stopped'} &middot; ${w.watched||0} watched &middot; ${w.cycles||0} cycles`+
    ((w.last||[]).length?'<br>'+w.last.slice(-3).map(x=>`${esc(x.repo)}: <span class="st-${esc(x.action)}">${esc(x.action)}</span> — ${esc(x.note)}`).join('<br>'):'');
  const st=$('st'); st.className='';
  if(a.status==='idle'){st.textContent=a.history.length?lastLine(a.history):'';}
  else if(['queued','fetching','analysing'].includes(a.status)){st.className='busy';st.textContent=a.name+' — '+a.status+(a.note?' · '+a.note:'')+' · '+a.elapsed+'s'}
  else{st.className=a.status==='complete'?'ok':'bad';st.textContent=a.name+' — '+a.status+(a.note?' · '+a.note:'')}
  $('go').disabled=['queued','fetching','analysing'].includes(a.status);
  return a;
}
function lastLine(h){const x=h[h.length-1];return x.url.replace('https://github.com/','')+' — '+x.status+(x.note?' · '+x.note:'')}

async function repos(){
  const d=await j('/api/mechanic/repos'); const rs=d.repos||[];
  $('rn').textContent=rs.length+' watched';
  if(!rs.length){$('repos').innerHTML='<div class=empty>No repositories yet. Paste a GitHub URL above.</div>';return}
  $('repos').innerHTML=`<table><thead><tr><th>repository</th><th class=n>LOC</th><th>last run</th><th class=n>findings</th><th class=n>refusals</th><th>when</th><th>watch</th></tr></thead><tbody>`+
    rs.map(r=>`<tr class=repo data-id="${esc(r.id)}" aria-selected="${r.id===REPO}">
      <td><b>${esc(r.name)}</b><br><span class=loc>${esc(r.url.replace('https://github.com/',''))}${r.last_sha?' @ '+esc(r.last_sha.slice(0,10)):''}</span></td>
      <td class=n>${(r.loc||0).toLocaleString()}</td>
      <td><span class="st-${esc(r.last_status)}">${esc(r.last_status||'—')}</span><br><span class=loc>${esc(r.note)}</span></td>
      <td class=n>${r.findings}</td><td class=n>${r.refusals}</td><td>${ago(r.last_at)}</td>
      <td><label class=loc><input type=checkbox class=w data-id="${esc(r.id)}" ${r.watch?'checked':''}> every
        <select class=iv data-id="${esc(r.id)}">${[[3600,'1h'],[21600,'6h'],[86400,'day'],[604800,'week']].map(([v,l])=>`<option value=${v} ${(r.interval_s||21600)==v?'selected':''}>${l}</option>`).join('')}</select>
        ${r.halts?`<br><span class=st-halted>${r.halts} halt(s)</span>`:''}</label></td></tr>`).join('')+'</tbody></table>';
  $('repos').querySelectorAll('tr.repo td:not(:last-child)').forEach(td=>td.onclick=()=>{const tr=td.parentElement;REPO=REPO===tr.dataset.id?'':tr.dataset.id;findings();repos()});
  $('repos').querySelectorAll('input.w,select.iv').forEach(el=>el.onchange=async()=>{
    const id=el.dataset.id, on=$('repos').querySelector(`input.w[data-id="${id}"]`).checked, iv=$('repos').querySelector(`select.iv[data-id="${id}"]`).value;
    const r=await fetch('/api/mechanic/watch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repo_id:id,watch:on,interval_s:+iv})});
    if(!r.ok){const d=await r.json();$('st').className='bad';$('st').textContent=d.error||('HTTP '+r.status)} tick();});
}

async function findings(){
  ALL=await j('/api/mechanic/findings'+(REPO?'?repo='+encodeURIComponent(REPO):''));
  const counts={all:ALL.findings.length,'machine-verified':0,judged:0};
  ALL.findings.forEach(f=>counts[f.basis]=(counts[f.basis]||0)+1);
  $('filters').innerHTML=Object.entries(counts).map(([k,n])=>`<button class=ghost data-b="${k}" aria-pressed="${k===BASIS}">${k} ${n}</button>`).join('');
  $('filters').querySelectorAll('button').forEach(b=>b.onclick=()=>{BASIS=b.dataset.b;drawFinds()});
  drawFinds();
  $('gn').textContent=ALL.gaps.length+' refusal(s)';
  $('gaps').innerHTML=ALL.gaps.length?ALL.gaps.map(g=>`<div class=gap><b>${esc(g.scope)}</b> <span class=rp>${esc(g.repo)}</span><br>${esc(g.reason)}</div>`).join('')
    :'<div class=empty>None — every symbol examined could be judged.</div>';
}
function drawFinds(){
  const fs=ALL.findings.filter(f=>BASIS==='all'||f.basis===BASIS);
  if(!fs.length){$('finds').innerHTML='<div class=empty>'+(ALL.findings.length?'Nothing matches this filter.':'No findings yet'+(REPO?' for this repository.':'.'))+'</div>';return}
  $('finds').innerHTML=fs.map(f=>{const ev=(f.evidence||[])[0]||{};
    return `<details><summary>
      <span class="tag ${f.basis==='machine-verified'?'mv':'jd'}">${esc(f.basis)}</span>
      <span class="tag s-${esc(f.severity)}">${esc(f.severity)}</span>
      <span class=ttl>${esc(f.title)}</span>
      <span class=loc>${esc(ev.file||'')}${ev.line_range?':'+esc(ev.line_range):''}</span>
      <span class=rp>${esc(f.repo)} &middot; ${esc(f.id)}${f.rank?' &middot; rank '+f.rank:''}${(f.seen_runs||1)>1?' &middot; seen '+f.seen_runs+' cycles':''}${f.upstream&&f.upstream!=='open'?' &middot; <span class=mv>'+esc(f.upstream)+'</span>':''}</span></summary>
      <div class=body><p>${esc(f.description)}</p>
      ${f.recommendation?`<p class=fix><b>Proposed fix.</b> ${esc(f.recommendation)}</p>`:''}
      ${(f.evidence||[]).map(e=>`<p class=ev>evidence: <code>${esc(e.file)}${e.line_range?':'+esc(e.line_range):''}</code> — ${esc(e.reason)}</p>`).join('')}
      ${f.basis==='judged'?`<p class=ev>proposed by <b>${esc(f.proposed_by)}</b> &middot; challenge: <b>${esc(f.challenge||'—')}</b>${f.challenge_reason?' — '+esc(f.challenge_reason):''}</p>`:''}
      ${(f.trail||[]).length?`<details class=tr><summary class=ev>decision record (${f.trail.length})</summary>${f.trail.map(t=>`<p class=ev>${esc(t.stage)} &middot; ${esc(t.actor)} &middot; <b>${esc(t.action)}</b>${t.rationale?' — '+esc(t.rationale):''}${t.model?' <i>('+esc(t.model)+')</i>':''}</p>`).join('')}</details>`:''}
      </div></details>`}).join('');
}

$('f').onsubmit=async e=>{e.preventDefault();
  const r=await fetch('/api/mechanic/analyse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('url').value,budget_cents:+$('bud').value})});
  const d=await r.json(); const st=$('st');
  if(!r.ok){st.className='bad';st.textContent=d.error||('HTTP '+r.status);return}
  st.className='busy';st.textContent=d.accepted+' — queued'; $('url').value=''; tick();
};
let timer=null;
async function tick(){const a=await summary(); await repos(); await findings();
  clearTimeout(timer); timer=setTimeout(tick, ['queued','fetching','analysing'].includes(a.status)?3000:20000)}
tick();
</script>
</html>"""

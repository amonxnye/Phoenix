"""The self-improvement page — /improve. Every cycle and every idea it considered."""

PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Self-Improvement — The Governor</title>
<style>
/*TOKENS*/
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1240px;margin:0 auto;padding:16px;display:grid;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:baseline}
.card h2 em{font-style:normal;text-transform:none;letter-spacing:0;color:var(--sub);margin-left:auto;font-size:10px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--line)}
.kpi div{background:var(--panel);padding:10px 14px}
.kpi b{display:block;font-size:19px;line-height:1.3}
.kpi span{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.note{padding:10px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
.empty{padding:14px;color:var(--dim)}
.filters{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--line);align-items:center}
.ghost{background:#241a0f;border:1px solid var(--line);color:var(--dim);border-radius:5px;padding:2px 10px;font:11px ui-monospace,Menlo,monospace;cursor:pointer}
.ghost:hover{border-color:var(--gold);color:var(--gold)}
.go{background:#3b2a12;border:1px solid var(--gold);color:var(--gold);border-radius:5px;padding:3px 12px;font:11px ui-monospace,Menlo,monospace;cursor:pointer}
.go:disabled{opacity:.5;cursor:default}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--dim);font-weight:400;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
td:first-child,th:first-child{text-align:left}
.wrap{overflow-x:auto}
.bad{color:#f87171}.good{color:#22c55e}.warn{color:#fbbf24}
</style>
<header><h1>SELF-IMPROVEMENT</h1><a href="/">&larr; Console</a><a href="/research">Research</a><a href="/models">Models</a><a href="/rules">Rules</a>
<span class=meta id=meta>loading…</span>
<span class=meta id=tokbox style="margin-left:0"><input id=tok type=password placeholder="console token" style="background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:2px 8px;font:inherit;width:160px" title="CONSOLE_TOKEN — needed to run a cycle; kept only in this browser"> <button class=ghost id=savetok>save</button></span></header>
<main>
<div class=card><div class=filters>
  <button class=go id=run>run a cycle now</button>
  <span id=cur style="color:var(--dim);font-size:11px"></span>
  <span style="margin-left:auto;color:var(--dim);font-size:11px" id=next></span></div>
  <div class=kpi id=kpi></div>
  <div class=note>Article XI: each cycle reads about topics for this age from a public source, checks every citation, proposes a development from what it verified, researches its advantage and risk, and queues it for the Board and you. Nothing is adopted by a cycle.</div></div>
<div class=card><h2>Ideas from outside <em>topic → source read → citation checked → development proposed → researched → the Board, then you</em></h2><div class=wrap><table id=ideas></table></div></div>
<div class=card><h2>Cycles <em>newest first</em></h2><div class=wrap><table id=cycles></table></div></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ts=t=>t?new Date(t*1000).toLocaleString():'—';
let TOKEN='';try{TOKEN=localStorage.getItem('ctok')||localStorage.getItem('console_token')||''}catch(e){}
document.addEventListener('DOMContentLoaded',()=>{try{$('tok').value=TOKEN}catch(e){}});
$('savetok').onclick=()=>{TOKEN=$('tok').value.trim();try{localStorage.setItem('ctok',TOKEN);localStorage.setItem('console_token',TOKEN)}catch(e){}alert(TOKEN?'token saved in this browser':'token cleared')};
async function post(path,body,retried){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':TOKEN},body:JSON.stringify(body||{})});if(r.status===401){const t=retried?'':prompt('Console token required (CONSOLE_TOKEN on the server):');if(t){TOKEN=t.trim();try{localStorage.setItem('ctok',TOKEN);localStorage.setItem('console_token',TOKEN)}catch(e){}return post(path,body,true)}return {ok:false,error:'console token required'}}return r.json()}
$('run').onclick=async()=>{$('run').disabled=true;const r=await post('/api/improve/run');alert(r.status==='busy'?'a cycle is already running: '+r.note:'cycle started');setTimeout(load,1500)};
async function load(){
  const d=await (await fetch('/api/improve')).json();const s=d.status;
  $('meta').textContent=(s.enabled?'enabled':'DISABLED (IMPROVE=0)')+' · every '+(s.interval_s/3600).toFixed(1)+' h';
  $('cur').textContent=s.running?'running — '+s.current:'';$('run').disabled=!!s.running;
  $('next').textContent=s.last_cycle?'next scheduled in '+Math.round(s.next_due_in_s/60)+' min':'no cycle yet';
  const l=s.last_cycle||{};
  $('kpi').innerHTML=[['cycles',d.cycles.length],['last: topics',l.candidates??'—'],['last: read',l.tried??'—'],['last: queued',l.verified??'—'],['last: rejected',l.rejected??'—'],['empty streak',s.empty_streak]].map(([a,b])=>`<div><b>${b}</b><span>${a}</span></div>`).join('');
  $('tokbox').hidden=!s.token_required;       // no CONSOLE_TOKEN set: every action is open
  $('meta').textContent+=(s.research?' · research chain '+(s.research.intact?'intact ('+s.research.entries+')':'BROKEN'):'');
  const id=(s.ideas||{}).recent||[];
  $('ideas').innerHTML='<tr><th>when</th><th>age</th><th>topic</th><th>source</th><th>cited</th><th>development</th><th>research</th><th>status</th></tr>'+
    (id.length?id.map(i=>`<tr><td>${ts(i.ts)}</td><td>${esc(i.age)}</td><td style="white-space:normal">${esc(i.topic)}</td><td>${i.source?'<a href="'+esc(i.source)+'" target=_blank>'+esc(i.title||'source')+'</a>':'—'}</td><td class=${i.verified?'good':'bad'}>${i.verified?'verified':'no'}</td><td style="white-space:normal">${i.proposal?esc(i.proposal.name)+' — '+esc(i.proposal.kind)+' '+esc(i.proposal.value)+(i.proposal.resource?' '+esc(i.proposal.resource):'')+' (cost '+esc(JSON.stringify(i.proposal.cost))+')':'—'}</td><td>${i.research_id?'<a href="/research">R'+i.research_id+'</a>':'—'}</td><td class=${['queued','voting','adopted'].includes(i.status)?'good':'warn'} title="${esc(i.note)}">${esc(i.status)}</td></tr>`).join(''):'<tr><td colspan=8 class=empty>no idea considered yet — the next cycle reads about this age</td></tr>');
  $('cycles').innerHTML='<tr><th>when</th><th>trigger</th><th>status</th><th>candidates</th><th>tried</th><th>verified</th><th>rejected</th><th>seconds</th><th>model</th><th>note</th></tr>'+
    (d.cycles.length?d.cycles.map(c=>`<tr><td>${ts(c.ts)}</td><td>${esc(c.trigger)}</td><td class=${c.status==='complete'?'good':c.status==='running'?'warn':'bad'}>${esc(c.status)}</td><td>${c.candidates}</td><td>${c.tried}</td><td>${c.verified}</td><td>${c.rejected}</td><td>${c.seconds}</td><td>${esc(c.model)}</td><td style="white-space:normal;text-align:left">${esc(c.note)}</td></tr>`).join(''):'<tr><td colspan=10 class=empty>no cycle has run yet</td></tr>');
}
load();setInterval(load,20000);
</script>"""

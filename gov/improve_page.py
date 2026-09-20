"""The self-improvement page — /improve. Every cycle, every proposal, the gate."""

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
.p{border-bottom:1px solid var(--line);padding:10px 14px}
.p h3{margin:0 0 4px;font-size:13px}.p h3 small{color:var(--dim);font-weight:400}
.st{display:inline-block;padding:1px 8px;border-radius:10px;font-size:10px;text-transform:uppercase;letter-spacing:.5px;border:1px solid var(--line)}
.st-committed{color:#22c55e;border-color:#22c55e}.st-unresearched{color:#f87171}.st-refused{color:#f87171}.st-reverted{color:var(--dim)}.st-verified{color:#fbbf24;border-color:#fbbf24}.st-unverified{color:#f87171;border-color:#f87171}.st-proposed{color:#22c55e;border-color:#22c55e}.st-approved{color:#22c55e}.st-rejected{color:#f87171}.st-stale{color:var(--dim)}
pre.diff{background:#0e0a05;border:1px solid var(--line);border-radius:6px;padding:8px 10px;overflow:auto;max-height:320px;font-size:11px;margin:8px 0}
pre.diff .a{color:#22c55e}pre.diff .d{color:#f87171}pre.diff .h{color:var(--gold)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px}
.row input{background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:3px 8px;font:inherit;min-width:260px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;padding:10px 14px;margin:0;font-size:12px}
dt{color:var(--dim)}dd{margin:0}
</style>
<header><h1>SELF-IMPROVEMENT</h1><a href="/">&larr; Console</a><a href="/research">Research</a><a href="/models">Models</a><a href="/mechanic">Mechanic</a><a href="/rules">Rules</a>
<span class=meta id=meta>loading…</span>
<span class=meta style="margin-left:0"><input id=tok type=password placeholder="console token" style="background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:2px 8px;font:inherit;width:160px" title="CONSOLE_TOKEN — needed to approve, reject or run a cycle; kept only in this browser"> <button class=ghost id=savetok>save</button></span></header>
<main>
<div class=card><div class=filters>
  <button class=go id=run>run a cycle now</button>
  <span id=cur style="color:var(--dim);font-size:11px"></span>
  <span style="margin-left:auto;color:var(--dim);font-size:11px" id=next></span></div>
  <div class=kpi id=kpi></div>
  <div class=note>Article XI: the mechanic proposes patches for Phoenix itself; each is applied to a scratch copy and the world's own suites are the oracle — no keys, no network. What survives is parked here. A human approves; only then is a branch pushed and a draft pull request opened. The live tree is never written by a cycle.</div></div>
<div class=card><h2>At the gate <em>verified improvements waiting for a human — and UNVERIFIED ones the suites cannot judge</em></h2><div id=gate></div></div>
<div class=card><h2>Cycles <em>newest first</em></h2><div class=wrap><table id=cycles></table></div></div>
<div class=card><h2>Decided <em>proposed, approved, rejected, stale</em></h2><div id=decided></div></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ts=t=>t?new Date(t*1000).toLocaleString():'—';
let TOKEN='';try{TOKEN=localStorage.getItem('console_token')||''}catch(e){}
document.addEventListener('DOMContentLoaded',()=>{try{$('tok').value=TOKEN}catch(e){}});
$('savetok').onclick=()=>{TOKEN=$('tok').value.trim();try{localStorage.setItem('console_token',TOKEN)}catch(e){}alert(TOKEN?'token saved in this browser':'token cleared')};
function diff(p){return '<pre class=diff>'+p.split('\\n').map(l=>l.startsWith('+')?'<span class=a>'+esc(l)+'</span>':l.startsWith('-')?'<span class=d>'+esc(l)+'</span>':l.startsWith('@@')?'<span class=h>'+esc(l)+'</span>':esc(l)).join('\\n')+'</pre>'}
function suites(j){const s=(j&&j.suites)||{};return Object.entries(s).map(([n,v])=>`<span class=${v.ok?'good':'bad'}>${n} ${v.passed}/${v.total}</span>`).join(' · ')||'—'}
function prop(p,gate){
  return `<div class=p><h3>#${p.id} ${esc(p.title)} <small>${esc(p.file)} · ${esc(p.severity)} · ${esc(p.category)}</small> <span class="st st-${esc(p.status)}">${esc(p.status)}</span></h3>
  <div style="color:var(--dim);font-size:11px">${esc(p.note)}${p.pr_url?' · <a href="'+esc(p.pr_url)+'" target=_blank>'+(p.status==='committed'||p.status==='reverted'?'commit':'pull request')+'</a>':''}${p.status==='committed'?' · <button class=ghost onclick="revert('+p.id+')">revert this commit</button>':''}</div>
  <div style="font-size:11px">before: ${suites(p.before_json)}<br>after: &nbsp;${suites(p.after_json)}</div>
  <details><summary style="cursor:pointer;color:var(--gold);font-size:11px">diff</summary>${diff(p.patch||'')}</details>
  ${gate?`<div class=row><button class=go onclick="approve(${p.id})">approve → ${p.github?'open draft PR':'hand me the patch'}</button><input id="r${p.id}" placeholder="reason to reject"><button class=ghost onclick="reject(${p.id})">reject</button> <span style="color:var(--dim);font-size:11px">waiting ${p.hours_waiting} h</span></div>`:''}</div>`}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':TOKEN},body:JSON.stringify(body||{})});if(r.status===401){alert('console token required — paste CONSOLE_TOKEN in the box at the top right and save');return {ok:false,error:'console token required'}}return r.json()}
async function approve(id){const r=await post('/api/improve/approve',{id});alert(r.ok?(r.pr_url?'draft PR opened: '+r.pr_url:r.note||'approved'):('failed: '+r.error));load()}
async function revert(id){if(!confirm('Put the file back to what it was before #'+id+'? This is one more commit on the deploy branch.'))return;const r=await post('/api/improve/revert',{id});alert(r.ok?'reverted':'failed: '+r.error);load()}
async function reject(id){const r=await post('/api/improve/reject',{id,reason:$('r'+id).value});alert(r.ok?'rejected':'failed: '+r.error);load()}
$('run').onclick=async()=>{$('run').disabled=true;const r=await post('/api/improve/run');alert(r.status==='busy'?'a cycle is already running: '+r.note:'cycle started');setTimeout(load,1500)};
async function load(){
  const d=await (await fetch('/api/improve')).json();const s=d.status;
  $('meta').textContent=(s.enabled?'enabled':'DISABLED (IMPROVE=0)')+' · every '+(s.interval_s/3600).toFixed(1)+' h · isolation: '+s.isolation+' · github: '+(s.github?(s.push_branch?'verified patches are COMMITTED to '+s.push_branch:'verified patches open draft PRs'):'no token — patches are handed over');
  $('cur').textContent=s.running?'running — '+s.current:'';$('run').disabled=!!s.running;
  $('next').textContent=s.last_cycle?'next scheduled in '+Math.round(s.next_due_in_s/60)+' min':'no cycle yet';
  const l=s.last_cycle||{};
  $('kpi').innerHTML=[['cycles',d.cycles.length],['parked at gate',s.parked],['last: candidates',l.candidates??'—'],['last: tried',l.tried??'—'],['last: verified',l.verified??'—'],['last: rejected',l.rejected??'—'],['empty streak',s.empty_streak],['proposed (PRs)',d.proposals.filter(p=>p.status==='proposed').length]].map(([a,b])=>`<div><b>${b}</b><span>${a}</span></div>`).join('');
  $('meta').textContent+=(s.research?' · research chain '+(s.research.intact?'intact ('+s.research.entries+')':'BROKEN'):'');
  const gate=d.proposals.filter(p=>p.status==='verified'||p.status==='unverified').map(p=>({...p,github:s.github,hours_waiting:((Date.now()/1000-p.ts)/3600).toFixed(1)}));
  $('gate').innerHTML=gate.length?gate.map(p=>prop(p,true)).join(''):'<div class=empty>nothing waiting — every verified improvement has been decided</div>';
  $('cycles').innerHTML='<tr><th>when</th><th>trigger</th><th>status</th><th>candidates</th><th>tried</th><th>verified</th><th>rejected</th><th>seconds</th><th>model</th><th>note</th></tr>'+
    (d.cycles.length?d.cycles.map(c=>`<tr><td>${ts(c.ts)}</td><td>${esc(c.trigger)}</td><td class=${c.status==='complete'?'good':c.status==='running'?'warn':'bad'}>${esc(c.status)}</td><td>${c.candidates}</td><td>${c.tried}</td><td>${c.verified}</td><td>${c.rejected}</td><td>${c.seconds}</td><td>${esc(c.model)}</td><td style="white-space:normal;text-align:left">${esc(c.note)}</td></tr>`).join(''):'<tr><td colspan=10 class=empty>no cycle has run yet</td></tr>');
  const dec=d.proposals.filter(p=>p.status!=='verified'&&p.status!=='unverified');
  $('decided').innerHTML=dec.length?dec.map(p=>prop(p,false)).join(''):'<div class=empty>nothing decided yet</div>';
}
load();setInterval(load,20000);
</script>"""

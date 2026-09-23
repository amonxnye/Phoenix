"""The admin page — /admin. Every system, the compute and what it bought, the actors
and decisions worth a second look, the innovation journal, every download, and the
danger zone (world reset, full reset)."""

PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Admin — The Governor</title>
<style>
/*TOKENS*/
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
nav.tabs{display:flex;gap:6px;flex-wrap:wrap;padding:10px 20px;border-bottom:1px solid var(--line)}
nav.tabs a{border:1px solid var(--line);padding:3px 12px;border-radius:20px;color:var(--dim)}
nav.tabs a:hover{color:var(--gold);border-color:var(--gold)}
main{max-width:1200px;margin:0 auto;padding:16px;display:grid;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.card h2 em{font-style:normal;text-transform:none;letter-spacing:0;color:var(--sub);margin-left:auto;font-size:10px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--line)}
.kpi div{background:var(--panel);padding:10px 14px;min-width:0}.kpi b{display:block;font-size:17px;overflow-wrap:anywhere}.kpi span{color:var(--dim);font-size:10px;text-transform:uppercase}
.kpi .bad b{color:var(--alert)}.kpi .good b{color:var(--green)}
.scroll{overflow:auto;max-height:420px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:6px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:400;font-size:10px;text-transform:uppercase;position:sticky;top:0;background:var(--panel)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.empty{padding:14px;color:var(--dim)}.note{padding:10px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
.flag{color:var(--alert);font-size:11px}.dim{color:var(--dim)}
.chart{padding:10px 14px}.chart svg{width:100%;height:170px;display:block}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--dim);padding:0 14px 8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
button{font:inherit;padding:4px 11px;border-radius:6px;border:1px solid var(--line);cursor:pointer;background:#26200f;color:var(--ink)}
button.no{border-color:#5b2a1a;background:#2a140f;color:var(--alert)}button:disabled{opacity:.5;cursor:default}
input,select,textarea{background:#0e0a05;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font:inherit}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 14px}
.dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;padding:12px 14px}
.dl a,.dl button{display:block;border:1px solid var(--line);border-radius:8px;padding:8px 12px;text-align:left}
.dl small{display:block;color:var(--dim);font-size:10px}
.j{padding:10px 14px;border-bottom:1px solid var(--line)}.j b{color:var(--gold)}.j .k{font-size:10px;text-transform:uppercase;border:1px solid var(--line);border-radius:20px;padding:0 8px;margin-right:6px;color:var(--dim)}
.j p{margin:4px 0 0;white-space:pre-wrap;color:var(--ink)}
.danger{border-color:#5b2a1a}.danger h2{color:var(--alert)}
pre{margin:0;padding:10px 14px;font-size:11px;white-space:pre-wrap;color:var(--dim)}
</style>
<header><h1>ADMIN</h1><a href="/">&larr; Console</a><a href="/logs">Logs</a><a href="/models">Models</a><a href="/improve">Self-Improvement</a><a href="/research">Research</a><a href="/utopia">Utopia</a>
<span class=meta><span id=meta>loading…</span> · admin token <input id=atok type=password placeholder="ADMIN_TOKEN" style="width:130px"> <button id=saveatok>save</button></span></header>
<nav class=tabs><a href="#systems">Systems</a><a href="#compute">Compute &amp; efficiency</a><a href="#actors">Actors &amp; decisions</a><a href="#journal">Innovation journal</a><a href="#records">Records &amp; downloads</a><a href="#danger">Danger zone</a></nav>
<main>

<div class=card id=systems><h2>Systems <em>refreshes every 15 s</em></h2><div class=kpi id=sys></div>
<div class=scroll style="max-height:260px"><table id=systab></table></div></div>

<div class=card id=compute><h2>Compute over time <em><select id=hours><option value=24>24 h</option><option value=48 selected>48 h</option><option value=168>7 days</option><option value=720>30 days</option></select></em></h2>
<div class=kpi id=ckpi></div>
<div class=chart><svg id=chart viewBox="0 0 1000 170" preserveAspectRatio=none></svg></div>
<div class=legend><span><i style="background:#c9a227"></i>model tokens / h</span><span><i style="background:#c0392b"></i>wasted on failed calls</span><span><i style="background:#22c55e"></i>contribution per 1k agent compute</span></div>
<div class=scroll style="max-height:300px"><table id=purposes></table></div>
<div class=note>Model tokens are real calls to the brain (every one in the permanent call log). Agent compute is the simulated budget the villagers burn; contribution is what it bought. "Per outcome" divides model tokens by research entries, queued ideas and adopted developments in the window.</div></div>

<div class=card><h2>Efficiency review <em>the world looks at its own compute — within EFFICIENCY_TOKENS_PER_DAY</em></h2>
<div class=row><button id=review>Run an efficiency review now</button><span id=revout class=dim>An automatic review follows every improvement cycle.</span></div>
<pre id=revtext></pre></div>

<div class=card id=actors><h2>Agents <em>flagged first · efficiency against the fleet median</em></h2>
<div class=scroll><table id=agents></table></div></div>

<div class=card><h2>Chaos sources <em>who the incident log points at, last 400 incidents</em></h2>
<div class=scroll style="max-height:300px"><table id=chaos></table></div>
<div class=note id=enforce></div></div>

<div class=card><h2>Questionable decisions <em>outcomes that failed, were rejected, blocked or wasted</em></h2>
<div class=scroll style="max-height:360px"><table id=baddec></table></div></div>

<div class=card><h2>Recent incidents</h2><div class=scroll style="max-height:300px"><table id=incidents></table></div>
<div class=note id=humans></div></div>

<div class=card id=journal><h2>Innovation journal <em>every thought about improving the world — <a href="/api/admin/journal.md">&#8681; Markdown for papers</a></em></h2>
<div class=row><select id=jkind><option value="">all kinds</option></select>
<input id=jnote placeholder="add an operator note to the journal (append-only)" style="flex:1;min-width:220px"><button id=jadd>Add note</button></div>
<div class=scroll style="max-height:520px" id=jlist></div></div>

<div class=card id=records><h2>Records &amp; downloads</h2><div class=dl>
<button id=exportall>&#8681; The whole world (JSON)<small>every table of every database + the permanent event log, one file</small></button>
<a href="/api/admin/journal.md">&#8681; Innovation journal (Markdown)<small>grouped by kind, dated, every entry cited</small></a>
<a href="/research.md">&#8681; Research record (Markdown)<small>the append-only, hash-chained record</small></a>
<a href="/api/logs/export?format=jsonl">&#8681; Event log (JSONL)<small>the permanent log, raw records</small></a>
<a href="/api/logs/export?format=csv">&#8681; Event log (CSV)</a>
<a href="/api/logs/export?format=txt">&#8681; Event log (text)</a>
<a href="/api/careers/export?format=csv">&#8681; Careers (CSV)<small>every agent's life, event by event</small></a>
<a href="/api/health/export?format=csv">&#8681; Health telemetry (CSV)</a>
<a href="/api/admin/compute?hours=720" download="compute-30d.json">&#8681; Compute, 30 days (JSON)</a>
<a href="/api/admin/actors" download="actors.json">&#8681; Actors &amp; decisions (JSON)</a>
</div><div class=note id=exportnote>When ADMIN_TOKEN is set, the whole-world export needs it (save it at the top right).</div></div>

<div class="card danger" id=danger><h2>Danger zone</h2>
<div class=row><button class=no id=wreset>World reset</button><span class=dim>Wipes the game world and economy and restarts. Memory, research, the journal and the event log are kept.</span></div>
<div class=row><input id=confirm placeholder="type RESET EVERYTHING" style="width:220px"><button class=no id=freset>Full reset</button><span class=dim>Exports the whole world to the data folder first, then deletes every database and the event log and restarts from nothing. Needs ADMIN_TOKEN; disabled when it is not set.</span></div>
<pre id=dout></pre></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const n=v=>(v==null?'—':typeof v==='number'?v.toLocaleString():esc(v));
const when=ts=>ts?new Date(ts*1000).toISOString().replace('T',' ').slice(0,16):'—';
const bytes=b=>b>1e9?(b/1e9).toFixed(2)+' GB':b>1e6?(b/1e6).toFixed(1)+' MB':b>1e3?(b/1e3).toFixed(0)+' kB':(b||0)+' B';
let CT='',AT='';try{CT=localStorage.getItem('console_token')||'';AT=localStorage.getItem('admin_token')||''}catch(e){}
$('atok').value=AT;
$('saveatok').onclick=()=>{AT=$('atok').value.trim();try{localStorage.setItem('admin_token',AT)}catch(e){}alert(AT?'admin token saved in this browser':'admin token cleared')};
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':CT,'X-Admin-Token':AT},body:JSON.stringify(body||{})});
  let d={};try{d=await r.json()}catch(e){}if(!r.ok&&!d.error)d.error='HTTP '+r.status;return d}
const kv=(pairs)=>pairs.map(([k,v,c])=>`<div class="${c||''}"><b>${v}</b><span>${k}</span></div>`).join('');

async function systems(){
  const s=await (await fetch('/api/admin/systems')).json();
  const w=s.world,b=s.brain,st=s.storage,im=s.improve||{},up=s.process.uptime_s;
  $('meta').textContent=(s.process.commit||'dev')+' · up '+Math.floor(up/3600)+'h'+Math.floor(up%3600/60)+'m';
  $('sys').innerHTML=kv([['age',esc(w.age)],['difficulty',esc((w.difficulty||{}).name||w.difficulty)],['turn',n(w.turn)],['agents',n(w.agents)],
    ['food',n(w.food)],['wood',n(w.wood)],['gold',n(w.gold)],['utopia index',n(s.utopia.index)],
    ['brain',esc(b.model)],['calls 1 h',n(b.calls_1h)],['errors 1 h',n(b.errors_1h),b.errors_1h?'bad':''],['avg latency',n(b.avg_latency_ms_1h)+' ms'],
    ['improvement',im.error?'error':(im.running?'running':im.enabled?'enabled':'off'),im.error?'bad':''],['mode',esc(im.mode)],
    ['research chain',s.research&&s.research.ok?'intact':'BROKEN',s.research&&s.research.ok?'good':'bad'],
    ['disk used',st.disk.used_pct!=null?st.disk.used_pct+'%':'—',st.disk.used_pct>90?'bad':''],['disk free',bytes(st.disk.free)]]);
  const rows=[];
  for(const[k,v]of Object.entries(st.files))rows.push(['storage',k,bytes(v)]);
  const net=s.network||{};for(const[k,v]of Object.entries(net.stats||{}))rows.push(['network',k,n(v)]);
  for(const[k,v]of Object.entries(net.breakers||{}))rows.push(['breaker',k,esc(JSON.stringify(v))]);
  if(im.last_cycle)rows.push(['improvement','last cycle',when(im.last_cycle.ts)+' · '+esc(im.last_cycle.status)+' · '+esc(im.last_cycle.note)]);
  rows.push(['improvement','isolation / github',esc(im.isolation)+' / '+(im.github?'token':'no token')]);
  const m=s.mechanic||{};if(m.error)rows.push(['mechanic','error',esc(m.error)]);else rows.push(['mechanic','summary',esc(JSON.stringify(m.summary||{}).slice(0,300))]);
  rows.push(['utopia','qualities',esc(JSON.stringify(s.utopia.qualities))]);
  $('systab').innerHTML='<tr><th>system</th><th>what</th><th>reading</th></tr>'+rows.map(r=>`<tr><td class=dim>${r[0]}</td><td>${esc(r[1])}</td><td>${r[2]}</td></tr>`).join('');
}

function chart(hourly){
  const W=1000,H=170,P=4;if(!hourly.length){$('chart').innerHTML='<text x=10 y=90 fill=gray font-size=14>no model calls in this window</text>';return}
  const t0=hourly[0].hour,t1=hourly[hourly.length-1].hour,span=Math.max(3600,t1-t0);
  const mx=Math.max(1,...hourly.map(h=>h.model_tokens)),me=Math.max(1,...hourly.map(h=>h.contribution_per_1k_compute));
  const bw=Math.max(2,(W-2*P)/(span/3600+1)-1);
  const x=h=>P+(h.hour-t0)/span*(W-2*P-bw);
  let s='';
  for(const h of hourly){const y=H-P-(H-2*P)*h.model_tokens/mx,yw=H-P-(H-2*P)*h.wasted_prompt/mx;
    s+=`<rect x=${x(h)} y=${y} width=${bw} height=${H-P-y} fill="#c9a227" opacity=.8><title>${when(h.hour)}: ${h.model_tokens.toLocaleString()} tokens, ${h.calls} calls, ${h.errors} errors</title></rect>`;
    if(h.wasted_prompt)s+=`<rect x=${x(h)} y=${yw} width=${bw} height=${H-P-yw} fill="#c0392b"/>`}
  const pts=hourly.filter(h=>h.agent_compute).map(h=>`${x(h)+bw/2},${H-P-(H-2*P)*h.contribution_per_1k_compute/me}`).join(' ');
  if(pts)s+=`<polyline points="${pts}" fill=none stroke="#22c55e" stroke-width=2 vector-effect=non-scaling-stroke />`;
  $('chart').innerHTML=s;
}
async function compute(){
  const d=await (await fetch('/api/admin/compute?hours='+$('hours').value)).json(),t=d.totals,o=d.outcomes;
  $('ckpi').innerHTML=kv([['model tokens',n(t.model_tokens)],['wasted',n(t.wasted_tokens)+' ('+t.waste_pct+'%)',t.waste_pct>5?'bad':''],
    ['tokens per outcome',n(t.model_tokens_per_outcome)],['agent compute',n(t.agent_compute)],['contribution',n(t.contribution)],
    ['contribution / 1k compute',n(t.contribution_per_1k_compute),'good'],['lifetime agent compute',n(t.lifetime_agent_compute)],
    ['research entries',n(o.research_entries)],['ideas queued',n(o.ideas_queued)],['developments adopted',n(o.developments_adopted)],['civic works',n(o.civic_works)]]);
  chart(d.hourly);
  $('purposes').innerHTML='<tr><th>purpose</th><th class=num>calls</th><th class=num>errors</th><th class=num>tokens</th><th class=num>share</th><th class=num>wasted</th><th class=num>avg latency</th></tr>'+
    (d.purposes.length?d.purposes.map(p=>`<tr><td>${esc(p.purpose)}</td><td class=num>${n(p.calls)}</td><td class=num>${n(p.errors)}</td><td class=num>${n(p.tokens)}</td><td class=num>${p.share_pct}%</td><td class=num>${n(p.wasted)}</td><td class=num>${n(p.avg_latency_ms)} ms</td></tr>`).join(''):'<tr><td colspan=7 class=empty>no model calls in this window</td></tr>');
}
$('hours').onchange=compute;

async function actors(){
  const d=await (await fetch('/api/admin/actors')).json();
  $('agents').innerHTML='<tr><th>agent</th><th>rank</th><th class=num>contribution</th><th class=num>season compute</th><th class=num>per 1k</th><th class=num>promotions</th><th class=num>renewals</th><th class=num>free choices</th><th class=num>projects</th><th class=num>mentored / taught</th><th>flags</th></tr>'+
    (d.agents.length?d.agents.map(a=>`<tr><td>${esc(a.agent)}${a.alive?'':' <span class=dim>(retired)</span>'}</td><td>${esc(a.rank)}</td><td class=num>${n(a.contribution)}</td><td class=num>${n(a.season_compute)}</td><td class=num>${a.per_1k??'—'}</td><td class=num>${a.promotions}</td><td class=num>${a.renewals}</td><td class=num>${a.free_will}</td><td class=num>${a.projects}</td><td class=num>${a.mentored} / ${a.mentor}</td><td class=flag>${a.flags.map(esc).join('<br>')}</td></tr>`).join(''):'<tr><td colspan=11 class=empty>no agents yet</td></tr>');
  $('chaos').innerHTML='<tr><th>source</th><th class=num>incidents</th><th>kinds</th></tr>'+(d.chaos.length?d.chaos.map(c=>`<tr><td>${esc(c.source)}</td><td class=num>${c.incidents}</td><td class=dim>${esc(Object.entries(c.kinds).map(([k,v])=>k+' ×'+v).join(', '))}</td></tr>`).join(''):'<tr><td colspan=3 class=empty>no incidents recorded</td></tr>');
  $('enforce').textContent=d.note+' Fleet median efficiency: '+d.fleet_median_per_1k+' contribution per 1k compute.';
  $('baddec').innerHTML='<tr><th>when</th><th>actor</th><th>decision</th><th>why</th><th>outcome</th><th>authorised by</th></tr>'+(d.questionable_decisions.length?d.questionable_decisions.map(x=>`<tr><td class=dim>${when(x.ts)}<br>turn ${x.turn}</td><td>${esc(x.actor)}</td><td>${esc(x.decision)}</td><td class=dim>${esc(x.why)}</td><td class=flag>${esc(x.outcome)}</td><td class=dim>${esc(x.authorized_by)}</td></tr>`).join(''):'<tr><td colspan=6 class=empty>none</td></tr>');
  $('incidents').innerHTML='<tr><th>turn</th><th>kind</th><th>note</th></tr>'+(d.incidents.length?d.incidents.map(i=>`<tr><td class=num>${i.turn}</td><td>${esc(i.kind)}</td><td class=dim>${esc(i.note)}</td></tr>`).join(''):'<tr><td colspan=3 class=empty>none</td></tr>');
  const h=d.humans;$('humans').textContent=`Humans: ${h.operator_actions} operator actions · ${h.visitors} distinct visitors · ${h.public_chats} public chat messages.`;
}

let J=[];
function renderJ(){
  const k=$('jkind').value,list=J.filter(e=>!k||e.kind===k);
  $('jlist').innerHTML=list.length?list.slice(0,300).map(e=>`<div class=j><span class=k>${esc(e.kind)}</span><b>${esc(e.title)}</b> <span class=dim>· ${when(e.ts)} · ${esc(e.source)} · ${esc(e.ref)}</span><p>${esc(e.body)}</p></div>`).join(''):'<div class=empty>nothing recorded yet</div>';
}
async function journal(){
  J=await (await fetch('/api/admin/journal')).json();
  const kinds=[...new Set(J.map(e=>e.kind))].sort(),cur=$('jkind').value;
  $('jkind').innerHTML='<option value="">all kinds ('+J.length+')</option>'+kinds.map(k=>`<option ${k===cur?'selected':''}>${esc(k)}</option>`).join('');
  renderJ();
}
$('jkind').onchange=renderJ;
$('jadd').onclick=async()=>{const t=$('jnote').value.trim();if(!t)return;const d=await post('/api/admin/note',{text:t});if(d.error)return alert(d.error);$('jnote').value='';journal()};
$('review').onclick=async()=>{$('review').disabled=true;$('revout').textContent='reviewing…';
  const d=await post('/api/admin/efficiency',{});$('review').disabled=false;
  if(d.error){$('revout').textContent=d.error;return}
  $('revout').textContent='written to the journal as J'+d.journal_id+' ('+d.how+')';
  $('revtext').textContent=d.observations.join('\\n')+'\\n\\nInnovation: '+d.innovation;journal()};

$('exportall').onclick=async()=>{$('exportnote').textContent='exporting… a large world takes a while';
  const r=await fetch('/api/admin/export.json',{headers:{'X-Admin-Token':AT}});
  if(!r.ok){let e='HTTP '+r.status;try{e=(await r.json()).error||e}catch(_){}$('exportnote').textContent=e;return}
  const b=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='phoenix-world-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.json';document.body.appendChild(a);a.click();a.remove();
  $('exportnote').textContent='exported '+bytes(b.size)};
$('wreset').onclick=async()=>{if(!confirm('Reset the game world? The economy and buildings are wiped; memory, research and logs stay.'))return;
  const d=await post('/api/reset',{});$('dout').textContent=JSON.stringify(d,null,1)};
$('freset').onclick=async()=>{if($('confirm').value.trim()!=='RESET EVERYTHING')return alert('type RESET EVERYTHING to confirm');
  if(!confirm('Delete EVERY database and the event log? An export is written to the data folder first.'))return;
  const d=await post('/api/admin/full-reset',{confirm:$('confirm').value.trim()});$('dout').textContent=JSON.stringify(d,null,1)};

systems();compute();actors();journal();
setInterval(systems,15000);setInterval(compute,60000);setInterval(actors,60000);
</script>"""

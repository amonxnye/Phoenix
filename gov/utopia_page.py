"""The utopia page — /utopia. The city's five qualities, its civic works, the design budget."""

PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Utopia — The Governor</title>
<style>
/*TOKENS*/
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:2px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#241a0f}
h1{font-size:15px;margin:0;letter-spacing:1px}a{color:var(--gold);text-decoration:none}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0;padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:baseline}
.card h2 em{font-style:normal;text-transform:none;letter-spacing:0;color:var(--sub);margin-left:auto;font-size:10px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--line)}
.kpi div{background:var(--panel);padding:10px 14px}.kpi b{display:block;font-size:19px}.kpi span{color:var(--dim);font-size:10px;text-transform:uppercase}
.bars{padding:10px 14px;display:grid;gap:8px}
.bar{display:grid;grid-template-columns:110px 1fr 60px;gap:10px;align-items:center}
.track{height:12px;background:#0e0a05;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.fill{height:100%;background:linear-gradient(90deg,#8b5a2b,#22c55e)}
.note{padding:10px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:6px 12px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--dim);font-weight:400;font-size:10px;text-transform:uppercase}.empty{padding:14px;color:var(--dim)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:middle;margin-right:6px}
</style>
<header><h1>UTOPIA</h1><a href="/">&larr; Console</a><a href="/map3d">3D World</a><a href="/babylon">Babylon</a><a href="/improve">Self-Improvement</a><a href="/rules">Rules</a>
<span class=meta id=meta>loading…</span></header>
<main>
<div class=card><div class=kpi id=kpi></div>
<div class=note>Article XII: the world designs its own civic works from what it measures about itself — the weakest quality first — and from the lessons its agents have learned. The Board votes, you adopt or reject (silence consents after the waiting period), and the director builds each work when the treasury can pay. Every work is drawn in the 3D worlds with the shape, colour and district it was designed with.</div></div>
<div class=card><h2>The five qualities <em>diminishing returns — a utopia is a balance</em></h2><div class=bars id=bars></div>
<div class=note id=eff></div></div>
<div class=card><h2>Waiting for a decision</h2><div id=pend class=empty>—</div></div>
<div class=card><h2>Civic works <em>adopted designs; built ones stand in the world</em></h2><table id=works></table></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){
  const d=await (await fetch('/api/utopia')).json(), u=d.state, b=d.budget;
  $('meta').textContent=d.age+' · design budget '+b.spent_24h.toLocaleString()+' / '+b.per_day.toLocaleString()+' tokens today';
  $('kpi').innerHTML=[['utopia index',u.index],['balance',u.balance],['civic works built',u.works],['weakest',u.weakest],['all yields',(u.effects.all_yield_pct?'+'+u.effects.all_yield_pct+'%':'—')],['settlers',(u.effects.pop_cap?'+'+u.effects.pop_cap:'—')],['decay slowed',(u.effects.decay_slowdown_pct?u.effects.decay_slowdown_pct+'%':'—')]].map(([k,v])=>`<div><b>${esc(v)}</b><span>${k}</span></div>`).join('');
  $('bars').innerHTML=d.qualities.map(q=>`<div class=bar><span>${q}</span><div class=track><div class=fill style="width:${u.qualities[q]}%"></div></div><span>${u.qualities[q]}</span></div>`).join('');
  $('eff').textContent='beauty + harmony lift every yield (up to +25%) · knowledge lifts every yield (up to +10%) · health adds settlers (up to +5) · order slows decay (up to 50%)';
  const p=d.pending;
  $('pend').innerHTML=p?`<b>${esc(p.name)}</b> — ${esc(p.kind==='utopia'?'+'+p.value+' '+p.resource+' as a '+(p.visual||{}).shape:p.kind)} · cost ${esc(JSON.stringify(p.cost))} · board ${esc(p.board)} · ${esc(p.source)}<br><span style="color:var(--dim)">${esc(p.why)}</span><br>Adopt or reject it on the <a href="/">console</a>.`:'nothing waiting';
  $('works').innerHTML='<tr><th>work</th><th>quality</th><th>shape</th><th>district</th><th>built</th><th>condition</th><th>cost</th><th>origin</th></tr>'+(d.works.length?d.works.map(w=>`<tr><td><span class=sw style="background:${esc((w.visual||{}).color)}"></span>${esc(w.name.replace(/_/g,' '))}</td><td>+${w.value} ${esc(w.resource)}</td><td>${esc((w.visual||{}).shape)}</td><td>${esc((w.visual||{}).district)}</td><td>${w.built}</td><td>${w.condition}%</td><td>${esc(JSON.stringify(w.cost))}</td><td>${esc(w.source)}</td></tr>`).join(''):'<tr><td colspan=8 class=empty>no civic work adopted yet — the architect proposes one every other development cycle</td></tr>');
}
load();setInterval(load,15000);
</script>"""

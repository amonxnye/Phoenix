"""The research record page — /research. Read-only by construction: the page has no
button that writes, and the API it reads has no write."""

PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Research Record — The Governor</title>
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
.note{padding:10px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
.empty{padding:14px;color:var(--dim)}
.good{color:#22c55e}.bad{color:#f87171}
.e{padding:12px 14px;border-bottom:1px solid var(--line)}
.e h3{margin:0 0 6px;font-size:13px}.e h3 small{color:var(--dim);font-weight:400}
.e dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0;font-size:12px}
.e dt{color:var(--gold)}.e dd{margin:0}
.chain{font-size:10px;color:var(--dim)}
</style>
<header><h1>RESEARCH RECORD</h1><a href="/">&larr; Console</a><a href="/improve">Self-Improvement</a><a href="/research.md">RESEARCH.md</a><a href="/rules">Rules</a>
<span class=meta id=meta>loading…</span></header>
<main>
<div class=card><h2>The condition <em>Article XI.6</em></h2>
<div class=note>No change is made to Phoenix without its advantage-and-risk research written first, from the measured facts, into this record. The record is append-only and hash-chained: each entry names the one before it, so an alteration or a gap breaks the chain, and this page says so. No agent can edit it — a proposed patch that touches the research file, the constitution, the charter or the researcher is refused before any suite runs.</div></div>
<div class=card><h2>Entries <em>newest first — each one preceded a change</em></h2><div id=list></div></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ts=t=>t?new Date(t*1000).toLocaleString():'—';
async function load(){
  const d=await (await fetch('/api/research')).json();const c=d.chain||{};
  $('meta').innerHTML=(c.intact?'<span class=good>chain intact</span>':'<span class=bad>CHAIN BROKEN at R'+c.broken_at+'</span>')+' · '+(c.entries||0)+' entries · head '+esc((c.head||'genesis').slice(0,12));
  const es=(d.entries||[]).slice().reverse();
  $('list').innerHTML=es.length?es.map(e=>{const b=e.body||{},ev=e.evidence||{};const su=Object.entries(ev.suites_after||{}).map(([n,s])=>n+' '+s.passed+'/'+s.total).join(', ');
    return `<div class=e><h3>R${e.id} <small>${ts(e.ts)} · proposal #${e.proposal_id} · ${esc(e.file)}</small><br>${esc(e.title)}</h3>
    <dl><dt>advantage</dt><dd>${esc(b.advantage)}</dd><dt>risk</dt><dd>${esc(b.risk)}</dd><dt>blast radius</dt><dd>${esc(b.blast_radius)}</dd><dt>detection</dt><dd>${esc(b.detection)}</dd><dt>undo</dt><dd>${esc(b.undo)}</dd><dt>alternatives</dt><dd>${esc(b.alternatives)}</dd><dt>confidence</dt><dd>${esc(b.confidence)}</dd>
    <dt>evidence</dt><dd>severity ${esc(ev.severity)}, ${esc(ev.category)}; suites on the patched copy: ${esc(su)||'—'}</dd><dt>provenance</dt><dd>model ${esc(e.model)} · charter ${esc(e.charter)}</dd></dl>
    <div class=chain>${esc(e.hash.slice(0,16))} ← ${esc((e.prev_hash||'genesis').slice(0,16))}</div></div>`}).join(''):'<div class=empty>no research yet — the first verified change will write the first entry</div>';
}
load();setInterval(load,30000);
</script>"""

"""The model review — how every model the project has called is actually performing.

Article VII: a property that is merely asserted is not governed; this page is the
readout for the brain. Everything on it comes from two permanent records the seam
writes on every call — `model_calls` (tokens, latency, attempts, thinking share,
transport, outcome) and `model_events` (breaker openings and closings, exhausted
retries) — plus what the gateway itself reports about its compute right now.

Nothing here is sampled or estimated except the tokens-per-second figure, which is
completion tokens over wall latency and therefore includes queueing at the gateway.
It says so on the page.
"""

import time

import anchor

WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": 0}
ROW_CAP = 20000                    # the most rows one review reads; the page says if it hit
OUTAGE_MIN_FAILS = 3               # consecutive failed calls that count as an outage


def _pct(sorted_vals: list, q: float) -> int:
    if not sorted_vals:
        return 0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return int(sorted_vals[i])


def _rows(since: float) -> tuple[list[dict], bool]:
    c = anchor._conn()
    try:
        c.row_factory = anchor.sqlite3.Row
        try:
            q = "SELECT * FROM model_calls WHERE ts >= ? ORDER BY ts DESC LIMIT ?"
            rows = [dict(r) for r in c.execute(q, (since, ROW_CAP + 1))]
        except anchor.sqlite3.OperationalError:
            return [], False
        capped = len(rows) > ROW_CAP
        rows = rows[:ROW_CAP]
        rows.reverse()                              # oldest first for the timelines
        return rows, capped
    finally:
        c.close()


def _events(limit: int = 40) -> list[dict]:
    c = anchor._conn()
    try:
        c.row_factory = anchor.sqlite3.Row
        try:
            return [dict(r) for r in c.execute(
                "SELECT ts, kind, host, detail FROM model_events ORDER BY ts DESC LIMIT ?",
                (limit,))]
        except anchor.sqlite3.OperationalError:
            return []
    finally:
        c.close()


def _agg(rows: list[dict]) -> dict:
    """One group's figures. Latency percentiles over successful calls; tokens/s over
    successful calls with both a latency and completion tokens."""
    ok = [r for r in rows if r.get("ok")]
    lat = sorted(int(r.get("latency_ms") or 0) for r in ok)
    pt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    ct = sum(int(r.get("completion_tokens") or 0) for r in rows)
    tps_num = sum(int(r.get("completion_tokens") or 0) for r in ok if r.get("latency_ms"))
    tps_den = sum(int(r.get("latency_ms") or 0) for r in ok if r.get("latency_ms")) / 1000
    attempts = [int(r.get("attempts") or 1) for r in rows]
    think = sum(int(r.get("reasoning_chars") or 0) for r in rows)
    said = sum(int(r.get("content_chars") or 0) for r in rows)
    transports = sorted({r.get("transport") or "" for r in rows} - {""})
    errs = [r for r in rows if not r.get("ok")]
    return {
        "calls": len(rows), "ok": len(ok), "errors": len(errs),
        "error_rate": round(len(errs) / len(rows), 3) if rows else 0.0,
        "prompt_tokens": pt, "completion_tokens": ct,
        "avg_latency_ms": int(sum(lat) / len(lat)) if lat else 0,
        "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95), "max_ms": lat[-1] if lat else 0,
        "tokens_per_s": round(tps_num / tps_den, 1) if tps_den else 0.0,
        "avg_attempts": round(sum(attempts) / len(attempts), 2) if attempts else 1.0,
        "retried_calls": sum(1 for a in attempts if a > 1),
        "thinking_share": round(think / (think + said), 3) if (think + said) else 0.0,
        "transports": transports,
        "first_ts": rows[0]["ts"] if rows else None, "last_ts": rows[-1]["ts"] if rows else None,
        "last_error": (errs[-1].get("error") or "")[:200] if errs else "",
    }


def _outages(rows: list[dict]) -> list[dict]:
    """Runs of OUTAGE_MIN_FAILS+ consecutive failed calls, oldest first — the record's
    own view of downtime, independent of the breaker (which is per process)."""
    out, run = [], []
    for r in rows + [{"ok": 1, "_end": True}]:
        if not r.get("ok"):
            run.append(r)
            continue
        if len(run) >= OUTAGE_MIN_FAILS:
            out.append({"start": run[0]["ts"], "end": run[-1]["ts"], "calls": len(run),
                        "model": run[0].get("model"),
                        "duration_s": int(run[-1]["ts"] - run[0]["ts"]),
                        "first_error": (run[0].get("error") or "")[:160]})
        run = []
    return out


def _hourly(rows: list[dict], since: float, hours: int) -> list[dict]:
    now = time.time()
    start = max(since, now - hours * 3600) if since else now - hours * 3600
    start -= start % 3600
    buckets: dict[int, list] = {}
    for r in rows:
        if r["ts"] < start:
            continue
        buckets.setdefault(int(r["ts"] - r["ts"] % 3600), []).append(r)
    out = []
    t = int(start)
    while t <= now:
        rs = buckets.get(t, [])
        a = _agg(rs) if rs else None
        out.append({"hour": t, "calls": len(rs), "errors": a["errors"] if a else 0,
                    "avg_latency_ms": a["avg_latency_ms"] if a else 0,
                    "p95_ms": a["p95_ms"] if a else 0,
                    "completion_tokens": a["completion_tokens"] if a else 0})
        t += 3600
    out = out[-hours:]
    while len(out) > 1 and out[0]["calls"] == 0:      # start where the record starts
        out.pop(0)
    return out


def review(window: str = "24h") -> dict:
    secs = WINDOWS.get(window, 86400)
    since = time.time() - secs if secs else 0.0
    rows, capped = _rows(since)
    by_model: dict[str, list] = {}
    by_purpose: dict[str, list] = {}
    for r in rows:
        by_model.setdefault(f"{r.get('model') or '?'} @ {r.get('provider') or '?'}", []).append(r)
        by_purpose.setdefault(r.get("purpose") or "?", []).append(r)
    models = [dict(_agg(rs), model=k.split(" @ ")[0], provider=k.split(" @ ")[1])
              for k, rs in by_model.items()]
    models.sort(key=lambda m: -m["calls"])
    purposes = [dict(_agg(rs), purpose=k) for k, rs in by_purpose.items()]
    purposes.sort(key=lambda p: -p["calls"])
    errs = [r for r in rows if not r.get("ok")][-20:]
    return {
        "window": window, "rows": len(rows), "capped": capped,
        "totals": _agg(rows),
        "models": models, "purposes": purposes[:14],
        "hourly": _hourly(rows, since, 24 if secs and secs <= 86400 else 48),
        "outages": _outages(rows)[-20:],
        "events": _events(),
        "recent_errors": [{"ts": r["ts"], "model": r.get("model"), "purpose": r.get("purpose"),
                           "latency_ms": r.get("latency_ms"), "attempts": r.get("attempts"),
                           "error": (r.get("error") or "")[:200]} for r in reversed(errs)],
        "note": ("tokens/s is completion tokens over wall latency — it includes queueing at "
                 "the gateway, so it measures what callers experience, not the model alone"),
    }


PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Model Review — The Governor</title>
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
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1px;background:var(--line)}
.kpi div{background:var(--panel);padding:10px 14px}
.kpi b{display:block;font-size:19px;line-height:1.3}
.kpi span{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.note{padding:10px 14px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
.empty{padding:14px;color:var(--dim)}
.filters{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--line);align-items:center}
.ghost{background:#241a0f;border:1px solid var(--line);color:var(--dim);border-radius:5px;padding:2px 10px;font:11px ui-monospace,Menlo,monospace;cursor:pointer}
.ghost:hover,.ghost.on{border-color:var(--gold);color:var(--gold)}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--dim);font-weight:400;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
td:first-child,th:first-child{text-align:left}
.wrap{overflow-x:auto}
.bad{color:#f87171}.good{color:#22c55e}.warn{color:#fbbf24}
.err{padding:8px 14px;border-bottom:1px solid var(--line);font-size:11px}
.err b{color:#f87171}.err span{color:var(--dim)}
.two{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;padding:10px 14px;margin:0;font-size:12px}
dt{color:var(--dim)}dd{margin:0}
</style>
<header><h1>MODEL REVIEW</h1><a href="/">&larr; Console</a><a href="/mechanic">Mechanic</a><a href="/network">Network</a>
<span class=meta id=meta>loading…</span></header>
<main>
<div class=card><div class=filters>window
  <button class="ghost on" data-w=24h>24h</button><button class=ghost data-w=1h>1h</button><button class=ghost data-w=7d>7d</button><button class=ghost data-w=30d>30d</button><button class=ghost data-w=all>all</button>
  <span style="margin-left:auto;color:var(--dim);font-size:11px" id=cap></span></div>
  <div class=kpi id=kpi></div><div class=note id=tnote></div></div>
<div class=two>
  <div class=card><h2>Provider now <em id=pnow></em></h2><dl id=prov></dl></div>
  <div class=card><h2>Gateway compute <em>what the server reports about itself</em></h2><dl id=gw></dl><div class=note id=gwnote></div></div>
</div>
<div class=card><h2>Per model <em>latency over successful calls; thinking share = reasoning chars / (reasoning + answer)</em></h2><div class=wrap><table id=models></table></div></div>
<div class=card><h2>Per purpose <em>who is spending the model's time</em></h2><div class=wrap><table id=purposes></table></div></div>
<div class=card><h2>Hourly <em>calls, errors, latency, output — newest last</em></h2><div class=wrap><table id=hourly></table></div></div>
<div class=two>
  <div class=card><h2>Downtime <em>runs of 3+ consecutive failed calls, and breaker events</em></h2><div id=outages></div></div>
  <div class=card><h2>Recent errors <em>last 20, newest first</em></h2><div id=errors></div></div>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ms=v=>v>=1000?(v/1000).toFixed(1)+' s':(v||0)+' ms';
const k=v=>v>=1e6?(v/1e6).toFixed(2)+'M':v>=1e3?(v/1e3).toFixed(1)+'k':String(v||0);
const ts=t=>t?new Date(t*1000).toLocaleString():'—';
const pct=v=>((v||0)*100).toFixed(1)+'%';
let W='24h';
function rateCls(r){return r>0.2?'bad':r>0.02?'warn':'good'}
function row(cells){return '<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>'}
async function load(){
  const d=await (await fetch('/api/models?window='+W)).json();
  const t=d.totals;
  $('meta').textContent=d.rows+' calls in window · refreshed '+new Date().toLocaleTimeString();
  $('cap').textContent=d.capped?'window truncated to the newest '+d.rows+' calls':'';
  $('kpi').innerHTML=[['calls',t.calls],['error rate','<span class='+rateCls(t.error_rate)+'>'+pct(t.error_rate)+'</span>'],['avg latency',ms(t.avg_latency_ms)],['p95 latency',ms(t.p95_ms)],['tokens in',k(t.prompt_tokens)],['tokens out',k(t.completion_tokens)],['tokens/s',t.tokens_per_s],['thinking share',pct(t.thinking_share)],['retried calls',t.retried_calls],['outages',d.outages.length]]
    .map(([a,b])=>`<div><b>${b}</b><span>${a}</span></div>`).join('');
  $('tnote').textContent=d.note;
  const p=d.provider||{};
  $('pnow').textContent=p.configured?'':'no model configured';
  $('prov').innerHTML=p.configured?[['model',p.model],['host',p.host],['kind',p.kind],['native transport',p.native&&p.native.ok===true?'in use (/api/chat, think:false)':p.native&&p.native.ok===false?'refused — '+esc(p.native.why||''):'off (BRAIN_NATIVE unset)'],['retry policy',`${p.policy.retries} retries, backoff ${p.policy.backoff_s}s, breaker after ${p.policy.break_after}, cool ${p.policy.cool_s}s`],['breakers',Object.keys(p.breakers||{}).length?Object.entries(p.breakers).map(([h,b])=>h+': '+(b.open?'<span class=bad>OPEN '+b.seconds_left+'s</span>':'closed, '+b.failures+' exhausted')).join('<br>'):'none tripped this boot'],['this boot',`${p.stats.calls} calls · ${p.stats.retried} retried · ${p.stats.gave_up} gave up · ${p.stats.fast_failed} failed fast`]]
    .map(([a,b])=>`<dt>${a}</dt><dd>${b}</dd>`).join(''):'';
  const g=d.gateway||{};
  if(g.error){$('gw').innerHTML='';$('gwnote').textContent='not reachable from here: '+g.error}
  else{$('gw').innerHTML=[['server',g.version?'Ollama '+esc(g.version):'—'],['loaded now',(g.loaded||[]).length?g.loaded.map(m=>`<b>${esc(m.model)}</b> · ${m.parameter_size||''} ${m.quantization||''} · ${m.size_gb} GB on disk · ${m.vram_gb} GB in VRAM · until ${esc(m.expires_at||'')}`).join('<br>'):'nothing loaded — the next call pays the load'],['served',(g.available||[]).map(esc).join(', ')||'—']]
    .map(([a,b])=>`<dt>${a}</dt><dd>${b}</dd>`).join('');$('gwnote').textContent='a model that is not in VRAM loads on the first call; Ollama serves one request at a time per model, so latency here includes queueing behind the settlement'}
  $('models').innerHTML='<tr><th>model</th><th>calls</th><th>errors</th><th>avg</th><th>p50</th><th>p95</th><th>max</th><th>tok in</th><th>tok out</th><th>tok/s</th><th>think</th><th>retried</th><th>transport</th><th>last seen</th></tr>'+
    (d.models.length?d.models.map(m=>row([`<b>${esc(m.model)}</b><br><span style="color:var(--dim)">${esc(m.provider)}</span>`,m.calls,`<span class=${rateCls(m.error_rate)}>${m.errors} (${pct(m.error_rate)})</span>`,ms(m.avg_latency_ms),ms(m.p50_ms),ms(m.p95_ms),ms(m.max_ms),k(m.prompt_tokens),k(m.completion_tokens),m.tokens_per_s,pct(m.thinking_share),m.retried_calls,esc(m.transports.join(', ')||'openai /v1'),ts(m.last_ts)])).join(''):'<tr><td colspan=14 class=empty>no calls in this window</td></tr>');
  $('purposes').innerHTML='<tr><th>purpose</th><th>calls</th><th>errors</th><th>avg</th><th>p95</th><th>tok in</th><th>tok out</th><th>think</th></tr>'+
    d.purposes.map(p=>row([esc(p.purpose),p.calls,`<span class=${rateCls(p.error_rate)}>${p.errors}</span>`,ms(p.avg_latency_ms),ms(p.p95_ms),k(p.prompt_tokens),k(p.completion_tokens),pct(p.thinking_share)])).join('');
  $('hourly').innerHTML='<tr><th>hour</th><th>calls</th><th>errors</th><th>avg</th><th>p95</th><th>tok out</th></tr>'+
    d.hourly.map(h=>row([new Date(h.hour*1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit'}),h.calls,h.errors?`<span class=bad>${h.errors}</span>`:'0',h.calls?ms(h.avg_latency_ms):'—',h.calls?ms(h.p95_ms):'—',k(h.completion_tokens)])).join('');
  const ev=(d.events||[]).map(e=>`<div class=err><span>${ts(e.ts)}</span> <b class=${e.kind==='breaker_closed'?'good':'bad'}>${esc(e.kind)}</b> ${esc(e.host)} — ${esc(e.detail)}</div>`).join('');
  const ou=d.outages.map(o=>`<div class=err><span>${ts(o.start)}</span> <b>${o.calls} consecutive failures</b> over ${o.duration_s}s on ${esc(o.model)} — ${esc(o.first_error)}</div>`).join('');
  $('outages').innerHTML=(ou+ev)||'<div class=empty>no downtime recorded in this window</div>';
  $('errors').innerHTML=d.recent_errors.length?d.recent_errors.map(e=>`<div class=err><span>${ts(e.ts)}</span> <b>${esc(e.model)}</b> ${esc(e.purpose)} · ${ms(e.latency_ms)}${e.attempts>1?' · '+e.attempts+' attempts':''}<br>${esc(e.error)}</div>`).join(''):'<div class=empty>no errors in this window</div>';
}
document.querySelectorAll('[data-w]').forEach(b=>b.onclick=()=>{W=b.dataset.w;document.querySelectorAll('[data-w]').forEach(x=>x.classList.toggle('on',x===b));load()});
load();setInterval(load,30000);
</script>"""

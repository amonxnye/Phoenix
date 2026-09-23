"""Ideas from the world outside — Article XI in its intended sense: the settlement
improves itself with KNOWLEDGE, not with patches to its own code.

One cycle:

    the world's age and its scarcest need → topics worth knowing about →
    a public source (Wikipedia) fetched and READ → the fact ingested with its citation
    and CHECKED against the source (Article VI) → a development proposed from the
    verified fact, in the machine-usable vocabulary the settlement can build →
    its advantage and risk researched into the append-only record (XI.6) →
    queued for the Board's vote and the human's adoption (IV.7)

What this module never does: adopt anything. It produces sourced, verified, researched
PROPOSALS; the console's existing path — Board vote, human adopt or reject, tacit
consent after silence — decides. An unverified fact is kept for the record and steers
nothing (VI.2), so an idea whose citation does not check out is never proposed.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor                                    # noqa: E402
import brain                                     # noqa: E402
import netretry                                  # noqa: E402
import research                                  # noqa: E402
import sim                                       # noqa: E402

USER_AGENT = "PhoenixResearchBot/1.0 (https://github.com/amonxnye/Phoenix)"
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=3&srsearch="
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
FETCH = None                                     # replaceable: (topic) -> {title, url, extract} | None
PROPOSE = None                                   # replaceable: (situation, facts, existing) -> dict | None

# What a settlement at each age would want to know about. Curated so the search is
# grounded; the brain may add one topic of its own per cycle on top of these.
TOPICS = {
    "Stone Age":      ["hand axe", "control of fire by early humans", "hunter-gatherer", "granary"],
    "Tool Age":       ["ard (plough)", "irrigation in ancient agriculture", "pottery", "domestication of animals"],
    "Bronze Age":     ["bronze smelting", "wheel", "sail", "cuneiform record keeping"],
    "Iron Age":       ["iron smelting", "crop rotation", "coinage", "aqueduct"],
    "Classical Age":  ["Roman road", "watermill", "concrete in ancient Rome", "grain dole"],
    "Feudal Age":     ["three-field system", "heavy plough", "horse collar", "windmill"],
    "Castle Age":     ["guild", "double-entry bookkeeping", "blast furnace", "crop yield"],
    "Imperial Age":   ["printing press", "joint-stock company", "canal", "seed drill"],
    "Industrial Age": ["steam engine", "railway", "mechanised agriculture", "Bessemer process"],
    "Tech Age":       ["electrification", "fertilizer (Haber process)", "computing", "supply chain"],
}
SCARCE_TOPICS = {"food": "agricultural productivity", "wood": "forestry management", "gold": "mining technology"}


def _conn():
    c = anchor._conn()
    c.execute("CREATE TABLE IF NOT EXISTS ideas("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INT, ts REAL, age TEXT, topic TEXT, "
              "title TEXT, source TEXT, fact TEXT, verified INT, knowledge_id INT, proposal TEXT, "
              "research_id INT, status TEXT, note TEXT, decided_ts REAL)")
    return c


def rows(status: str = "", limit: int = 100) -> list[dict]:
    c = _conn()
    try:
        c.row_factory = anchor.sqlite3.Row
        q, args = "SELECT * FROM ideas", []
        if status:
            q += " WHERE status=?"; args.append(status)
        out = [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]
    finally:
        c.close()
    for r in out:
        try:
            r["proposal"] = json.loads(r["proposal"] or "null")
        except ValueError:
            r["proposal"] = None
    return out


def _add(**f) -> int:
    c = _conn()
    try:
        cols = ", ".join(f); qs = ", ".join("?" * len(f))
        cur = c.execute(f"INSERT INTO ideas({cols}) VALUES({qs})", tuple(f.values()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def mark(idea_id: int, status: str, note: str = "") -> None:
    c = _conn()
    try:
        c.execute("UPDATE ideas SET status=?, note=?, decided_ts=? WHERE id=?",
                  (status, note, time.time(), idea_id))
        c.commit()
    finally:
        c.close()


# ── the source ───────────────────────────────────────────────────────────────

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with netretry.urlopen(req, timeout=20, what="wikipedia", idempotent=True, key="en.wikipedia.org") as r:
        return r.read(2_000_000)


def fetch(topic: str) -> dict | None:
    """Wikipedia: search the topic, take the best page, read its summary.
    {title, url, extract} or None. Every hop goes through the retry policy (a 429 is
    honoured with its Retry-After)."""
    if FETCH is not None:
        return FETCH(topic)
    try:
        hits = json.loads(_get(WIKI_SEARCH + urllib.parse.quote(topic)))
        hit = ((hits.get("query") or {}).get("search") or [{}])[0].get("title")
        if not hit:
            return None
        d = json.loads(_get(WIKI_SUMMARY + urllib.parse.quote(hit.replace(" ", "_"))))
        extract = " ".join((d.get("extract") or "").split())
        url = ((d.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
        if not extract or not url:
            return None
        return {"title": d.get("title") or hit, "url": url, "extract": extract[:1200]}
    except Exception as e:                        # noqa: BLE001 — a topic that cannot be read is skipped, recorded
        anchor.record(-1, "idea-unread", f"{topic}: {type(e).__name__}: {str(e)[:120]}")
        return None


def _quote(extract: str) -> str:
    """The first full sentence of the summary — the span the citation check must find."""
    m = re.match(r"(.+?[.!?])(\s|$)", extract)
    return (m.group(1) if m else extract[:200]).strip()[:240]


def _sentences(extract: str) -> list[str]:
    return [x.strip()[:240] for x in re.findall(r".+?[.!?](?=\s|$)", extract) if len(x.split()) >= 6][:6]


def _cited_quote(url: str, extract: str) -> str:
    """The span to cite: the first sentence of the summary that the article itself
    contains. A summary API condenses the lead, so its first sentence is not always the
    page's; quoting one the page really holds keeps the check honest without loosening it.
    Falls back to the first sentence (which will then fail the check, as it should)."""
    for q in _sentences(extract):
        if anchor.verify_claim(url, q)[0]:        # the page is fetched once and cached
            return q
    return _quote(extract)


# ── the topics ───────────────────────────────────────────────────────────────

def topics(world: dict, already: set, limit: int) -> list[str]:
    age = world.get("age", "")
    cost = sim.advance_cost(age) if age in sim.AGE_ORDER else {}
    scarce = min(sim.RESOURCES, key=lambda r: world.get(r, 0) / max(1, cost.get(r, 1))) if cost else "food"
    out = [SCARCE_TOPICS[scarce]] + TOPICS.get(age, [])
    return [t for t in out if t not in already][:limit]


# ── one idea, end to end ─────────────────────────────────────────────────────

def _propose(situation: str, facts: list, existing: list) -> dict | None:
    if PROPOSE is not None:
        return PROPOSE(situation, facts, existing)
    return brain.propose_development(situation, facts, existing)


def consider(cycle_id: int, topic: str, world: dict, situation: str) -> dict:
    """Fetch → ingest with citation → propose from the VERIFIED fact → research → queue.
    Returns {status, ...}; every outcome is a row in `ideas`."""
    age = world.get("age", "")
    page = fetch(topic)
    if not page:
        iid = _add(cycle_id=cycle_id, ts=time.time(), age=age, topic=topic, title="", source="", fact="",
                   verified=0, knowledge_id=0, proposal="null", research_id=0, status="unread",
                   note="no readable source for this topic", decided_ts=0)
        return {"status": "unread", "id": iid}
    quote = _cited_quote(page["url"], page["extract"])
    ing = anchor.ingest(topic, page["url"], page["extract"][:400], quote=quote)
    if not ing["verified"]:
        iid = _add(cycle_id=cycle_id, ts=time.time(), age=age, topic=topic, title=page["title"],
                   source=page["url"], fact=page["extract"][:400], verified=0, knowledge_id=ing["id"],
                   proposal="null", research_id=0, status="unverified",
                   note=f"citation did not check out: {ing['reason']} — kept, steers nothing (VI.2)", decided_ts=0)
        return {"status": "unverified", "id": iid}
    existing = [d["name"] for d in sim.dev_catalog()] + [r["proposal"]["name"] for r in rows(limit=500)
                                                          if r.get("proposal") and r["status"] in ("queued", "voting")]
    fact = {"topic": topic, "fact": f"{page['title']}: {page['extract'][:300]} (source: {page['url']})"}
    prop = _propose(situation, [fact], existing)
    if not prop and PROPOSE is None and brain.LAST_PROPOSAL.get("ok") is False:
        # the MODEL gave nothing — not the same as "nothing new": the topic is tried again
        why = brain.LAST_PROPOSAL.get("why") or "no answer"
        iid = _add(cycle_id=cycle_id, ts=time.time(), age=age, topic=topic, title=page["title"],
                   source=page["url"], fact=page["extract"][:400], verified=1, knowledge_id=ing["id"],
                   proposal="null", research_id=0, status="model-silent",
                   note=f"the fact verified, but the model did not answer: {why} — tried again next cycle",
                   decided_ts=0)
        return {"status": "model-silent", "id": iid}
    if not prop or prop.get("name") in existing:
        iid = _add(cycle_id=cycle_id, ts=time.time(), age=age, topic=topic, title=page["title"],
                   source=page["url"], fact=page["extract"][:400], verified=1, knowledge_id=ing["id"],
                   proposal="null", research_id=0, status="no-proposal",
                   note=("the model proposed '" + str(prop.get("name")) + "', which already exists"
                         if prop else "the fact verified but no new development came of it"), decided_ts=0)
        return {"status": "no-proposal", "id": iid}
    prop = {k: prop.get(k) for k in ("name", "cost", "kind", "value", "resource", "rank", "why")}
    prop["source"] = f"{brain.brain_name()}+{page['url']}"
    iid = _add(cycle_id=cycle_id, ts=time.time(), age=age, topic=topic, title=page["title"],
               source=page["url"], fact=page["extract"][:400], verified=1, knowledge_id=ing["id"],
               proposal=json.dumps(prop), research_id=0, status="researching", note="", decided_ts=0)
    rs = research.research(
        {"id": iid, "title": f"development '{prop['name']}' from {page['title']}",
         "file": f"world:{age}", "category": "development", "severity": prop.get("rank", 2),
         "note": f"proposed from a verified citation: {page['url']}",
         "patch": json.dumps(prop, indent=1) + "\n\nFACT: " + page["extract"][:600]},
        {"before": {"age": age, **{r: world.get(r) for r in sim.RESOURCES}}, "after": prop},
        kind="world")
    if not rs["ok"]:
        mark(iid, "unresearched", f"NOT proposed: {rs['error']}")
        return {"status": "unresearched", "id": iid}
    c = _conn()
    try:
        c.execute("UPDATE ideas SET research_id=?, status='queued', note=? WHERE id=?",
                  (rs["entry"]["id"], f"research R{rs['entry']['id']}; awaiting the Board", iid))
        c.commit()
    finally:
        c.close()
    return {"status": "queued", "id": iid, "proposal": prop}


RETRY_STATUSES = ("unread", "model-silent", "interrupted")   # a topic in these is read again


def reap_stale(older_than_s: float = 1800) -> int:
    """An idea left 'researching' by a process that died mid-cycle is marked
    interrupted, so its topic is considered again rather than stuck forever."""
    c = _conn()
    try:
        # once: ideas recorded as "no development came of it" before the model's silence
        # was told apart were the model's silence (a thinking build out of tokens) — retry them
        if not anchor.config_get("ideas_silent_migrated"):
            c.execute("UPDATE ideas SET status='model-silent', note=note || ' (re-read: before 2026-09-24 a "
                      "silent model was recorded as no proposal)' WHERE status='no-proposal' "
                      "AND note='the fact verified but no new development came of it'")
            c.commit()
            anchor.config_set("ideas_silent_migrated", "1")
        n = c.execute("UPDATE ideas SET status='interrupted', note='the process restarted while this was "
                      "being researched — the topic is read again', decided_ts=? "
                      "WHERE status='researching' AND ts < ?", (time.time(), time.time() - older_than_s)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def take() -> dict | None:
    """The next queued idea for the console to put to the Board — oldest first. Marked
    `voting`; the console marks the outcome."""
    q = [r for r in rows(status="queued", limit=200)]
    if not q:
        return None
    r = q[-1]
    mark(r["id"], "voting", r["note"])
    prop = dict(r["proposal"])
    prop["idea_id"] = r["id"]
    prop["why"] = f"{prop.get('why', '')} [from {r['title']} — {r['source']}; research R{r['research_id']}]"
    return prop


def summary() -> dict:
    rs = rows(limit=500)
    from collections import Counter
    return {"count": len(rs), "by_status": dict(Counter(r["status"] for r in rs)),
            "queued": len([r for r in rs if r["status"] == "queued"]),
            "recent": [{k: r[k] for k in ("id", "ts", "age", "topic", "title", "source", "verified",
                                          "status", "note", "research_id", "proposal")} for r in rs[:30]]}

"""The Lab console — the operator's surface for the evidence framework.

`sim_console.py` is the settlement's console. This is the one for the work that
matters: papers under replication, the claims they make, the arithmetic redone, the
independent evidence found or not found, and — the only part that needs a person — the
gate where a critique or a candidate dossier waits for a human decision.

Nothing here is a mock. Every number comes from the same stores the acceptance suites
run against (`literature`, `replication`, `research_world`), so what the page shows and
what the oracle believes cannot drift apart.

Three things a user can actually do:
  1. **Decide at the gate.** The console *is* the human — approving here is the human
     approval, recorded permanently. Nothing else in the system can give it.
  2. **Run a campaign** against a paper and watch the ladder climb, round by round.
  3. **Check one claim** against one paper: paste the sentence, get the verdict. This
     is the oracle with the lid off, and it is the fastest way to see what the system
     will and will not accept as evidence.

Writes are token-gated exactly as the settlement console is: with CONSOLE_TOKEN set,
reading is free and every action needs the token (Article IV — the gate is a human, and
in a deployment "a human" means one holding the key).

    python3 gov/lab_console.py [--port 8788]
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import anchor
import campaign as CAM
import literature as L
import replication as REP
import research_world as RW

PAGE_PATH = os.path.join(ROOT, "phoenix-command.html")
SPEC_DIR = os.path.join(ROOT, "sandbox", "campaigns")

_RUN = {"running": False, "log": [], "started": 0.0}
_LOCK = threading.Lock()


def _short(s: str, n: int) -> str:
    """Truncate on a word boundary. A title cut mid-word reads like a bug."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or s[:n]) + "…"


# ── what the page reads ───────────────────────────────────────────────────────

def overview() -> dict:
    papers = []
    for cid in L.store_ids():
        rec = L.store_get(cid) or {}
        papers.append({"id": cid, "year": rec.get("year", ""),
                       "title": _short(rec.get("title", ""), 108),
                       "license": rec.get("license", ""),
                       "text": rec.get("abstract") is not None,
                       "url": rec.get("source_url", "")})
    papers.sort(key=lambda p: str(p["year"]), reverse=True)

    reps, settled, claim_n = [], 0, 0
    for pmid in _targets():
        v = REP.verdict(pmid)
        rec = L.store_get(pmid) or {}
        states = [c["state"] for c in v["claims"]]
        claim_n += len(states)
        settled += 1 if v["settled"] else 0
        reps.append({
            "pmid": pmid, "title": _short(rec.get("title", ""), 92), "year": rec.get("year", ""),
            "verdict": v["verdict"],
            "corroborated": states.count("corroborated"),
            "weak": states.count("weakly-corroborated"),
            "unreplicated": states.count("unreplicated"),
            "contradicted": states.count("contradicted"),
        })

    pending = []
    for c in REP.critiques("pending"):
        b = c["body"]
        pending.append({
            "kind": "critique", "id": c["id"], "target": c["target"],
            "title": _short(b.get("target", {}).get("title") or c["target"], 92),
            "verdict": b.get("verdict", "undetermined"), "why": b.get("why", ""),
            "meta": (f"{len(b.get('claims_under_test', []))} claims tested · "
                     f"{len(b.get('arithmetic', []))} statistics recomputed · "
                     f"{len(b.get('independent_evidence', []))} independent hits · "
                     f"publishing this is irreversible"),
            "limitations": b.get("limitations", [])[:4],
        })
    try:
        for d in RW.dossiers("pending"):
            b = d["body"]
            pending.append({
                "kind": "dossier", "id": d["id"], "target": d["compound"],
                "title": f"candidate {d['compound']}", "verdict": "undetermined",
                "why": b.get("goal", ""),
                "meta": (f"{b.get('computed', {}).get('affinity')} kcal/mol · "
                         f"{len(b.get('admet_violations', []))} ADMET violations · "
                         f"{len(b.get('evidence_chain', []))} verified claims · "
                         f"nothing physical or public has happened"),
                "limitations": b.get("limitations", [])[:4],
            })
    except Exception:
        pass                                       # the research world is optional here

    return {"evidence": len(papers), "papers": papers[:60], "targets": len(reps),
            "claims": claim_n, "settled": settled, "gate": len(pending),
            "pending": pending, "replications": reps}


def _targets() -> list[str]:
    REP.init()
    c = REP._conn()
    try:
        return [r[0] for r in c.execute("SELECT pmid FROM targets ORDER BY rowid")]
    finally:
        c.close()


def target_detail(pmid: str) -> dict:
    v = REP.verdict(pmid)
    by_claim = {c["claim"]: c["state"] for c in v["claims"]}
    claims = [{"claim": c["claim"], "quote": c["quote"], "section": c["section"],
               "state": by_claim.get(c["claim"], "unreplicated")}
              for c in REP.claims(pmid)]
    label = {c["id"]: c["claim"] for c in REP.claims(pmid)}
    findings = []
    for f in REP.findings(pmid):
        rec = L.store_get(f["source"]) or {}
        findings.append({"source": f["source"], "verdict": f["verdict"],
                         "grade": f["grade"], "quote": _short(f["quote"], 380),
                         "alias": f["alias"], "url": rec.get("source_url", ""),
                         "for_claim": label.get(f["claim_id"], "")})
    recs = [{"label": r["label"],
             "reported": json.dumps(r["reported"]), "recomputed": json.dumps(r["recomputed"]),
             "verdict": r["verdict"], "why": r["why"]} for r in v["recomputations"]]
    return {"pmid": pmid, "verdict": v["verdict"], "why": v["why"], "claims": claims,
            "recomputations": recs, "findings": findings, "blockers": v["blockers"]}


def check_claim(body: dict) -> dict:
    """One claim, one paper, one sentence — the oracle with the lid off."""
    pmid = str(body.get("pmid", "")).strip()
    if not pmid.upper().startswith(("PMID:", "PMC", "EPMC")):
        pmid = "PMID:" + pmid.lstrip(":")
    rec = L.store_get(pmid)
    if rec is None:
        return {"verdict": "unresolved-citation",
                "why": f"{pmid} is not in the evidence store — it was never retrieved, "
                       f"so it cannot be cited"}
    claim = {"subject": body.get("subject", ""), "relation": body.get("relation", ""),
             "object": body.get("object", "")}
    quote = body.get("quote", "")
    v = L.supports(claim, rec, quote)
    out = {"verdict": v["verdict"], "why": v["why"]}
    if v.get("quote"):
        out["section"] = L.section_of(rec, v["quote"])
    if v["verdict"] in ("unsupported", "not-mentioned", "unquoted"):
        better = L.find_support(claim, rec)
        if better:
            out["why"] += f"\n\nthe sentence you wanted may be: \"{better[:300]}\""
    return out


def specs() -> list[dict]:
    if not os.path.isdir(SPEC_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SPEC_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(SPEC_DIR, name)
        try:
            with open(path) as f:
                s = json.load(f)
            rec = L.store_get(s.get("target", "")) or {}
            label = f"{s.get('target', name)} — {(rec.get('title') or name)[:60]}"
        except (OSError, json.JSONDecodeError):
            label = name
        out.append({"path": os.path.relpath(path, ROOT), "label": label})
    return out


# ── the one long-running action ───────────────────────────────────────────────

def start_run(spec_rel: str, offline: bool) -> tuple[bool, str]:
    with _LOCK:
        if _RUN["running"]:
            return False, "a campaign is already running"
        path = os.path.realpath(os.path.join(ROOT, spec_rel))
        if not path.startswith(os.path.realpath(SPEC_DIR) + os.sep):
            return False, "REFUSED: specs are read from sandbox/campaigns only"
        if not os.path.exists(path):
            return False, f"no such spec: {spec_rel}"
        _RUN.update({"running": True, "log": [], "started": time.time()})
    threading.Thread(target=_run_campaign, args=(path, offline), daemon=True).start()
    return True, "started"


def _run_campaign(path: str, offline: bool) -> None:
    def log(line):
        _RUN["log"].append(line.rstrip())

    try:
        spec = CAM.load_spec(path)
        REP.init()
        for line in CAM.prepare(spec):
            log("   " + line)
        log("")
        c = CAM.Campaign(spec, offline=offline, log=log)
        res = c.run()
        log("")
        log(f"VERDICT: {res['verdict'].upper()}  ({res['how']} after {res['rounds']} "
            f"rounds, {res['ingests']} retrievals, {res['seconds']}s)")
        log(f"   {res['why']}")
        for b in res["blockers"]:
            log(f"   open: {b}")
        d = REP.park_critique(spec["target"])
        log(f"   critique #{d['critique']} parked for a human — nothing published")
    except Exception as e:
        log(f"campaign failed: {type(e).__name__}: {e}")
    finally:
        _RUN["running"] = False


# ── HTTP ──────────────────────────────────────────────────────────────────────

class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json"):
        payload = body.encode() if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        q = urllib.parse.parse_qs(qs)
        try:
            if path in ("/", "/index.html"):
                with open(PAGE_PATH, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/overview":
                return self._json(overview())
            if path == "/api/target":
                pmid = (q.get("pmid") or [""])[0]
                return self._json(target_detail(pmid))
            if path == "/api/specs":
                return self._json({"specs": specs()})
            if path == "/api/run/status":
                return self._json({"running": _RUN["running"], "log": _RUN["log"][-400:]})
            if path == "/api/evidence":
                return self._json({"papers": overview()["papers"]})
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        tok = os.environ.get("CONSOLE_TOKEN", "")
        if tok and self.headers.get("X-Console-Token", "") != tok:
            return self._json({"error": "console token required"}, 401)
        body = self._read_json()
        try:
            if self.path == "/api/decide":
                return self._decide(body)
            if self.path == "/api/run":
                ok, msg = start_run(str(body.get("spec", "")), bool(body.get("offline")))
                return self._json({"ok": ok, "message": msg}, 200 if ok else 400)
            if self.path == "/api/check":
                return self._json(check_claim(body))
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "not found"}, 404)

    def _decide(self, body: dict):
        kind, cid = body.get("kind"), body.get("id")
        decision = body.get("decision")
        if decision not in ("approve", "refuse"):
            return self._json({"error": "decision must be approve or refuse"}, 400)
        # The console IS the human — this is the approval Article IV waits for, and it
        # is the only place in the system that can give it.
        if kind == "critique":
            ok, msg = REP.critique_decide(int(cid), decision, approver="human",
                                          note="decided at the lab console")
        elif kind == "dossier":
            ok, msg = RW.dossier_decide(int(cid), decision, approver="human",
                                        note="decided at the lab console")
        else:
            return self._json({"error": f"unknown gate item: {kind}"}, 400)
        if ok:
            # The decision has already landed. Telemetry failing afterwards must never
            # report it back as an error — a human who is told their approval failed
            # will click again, and at a gate that is exactly the wrong outcome.
            try:
                anchor.record(-1, "gate",
                              f"human {decision}d {kind} #{cid} at the lab console")
            except Exception:
                pass
        return self._json({"ok": ok, "message": msg}, 200 if ok else 400)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8788)))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    anchor.init()
    REP.init()
    try:
        RW.init()
    except Exception:
        pass
    n_ev, n_t = len(L.store_ids()), len(_targets())
    print(f"Phoenix Lab — {n_ev} papers held, {n_t} under replication")
    print(f"  http://{a.host}:{a.port}")
    if not os.environ.get("CONSOLE_TOKEN"):
        print("  (CONSOLE_TOKEN unset — actions are open on this host)")
    QuietServer((a.host, a.port), Handler).serve_forever()

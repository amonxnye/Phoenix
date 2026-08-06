"""Headless smoke test for the Lab console — the surface a user actually touches.

Boots the real server on an ephemeral port against a temp database and exercises the
three things the page can do: read the world, check a claim, and decide at the gate.
The point is that the console cannot drift from the oracle — every number it shows
comes from the same stores the acceptance suites run against.

No network: the campaign is run offline and `urlopen` is torn out first.

Run:  python3 gov/lab_console_smoke.py
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_real_urlopen = urllib.request.urlopen
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


_TMP = tempfile.mkdtemp(prefix="phoenix-labconsole-")
os.environ["GOV_DATA_DIR"] = _TMP

import anchor                                      # noqa: E402
import campaign as CAM                             # noqa: E402
import lab_console as LC                           # noqa: E402
import literature as L                             # noqa: E402
import replication as REP                          # noqa: E402

REP.DB = os.path.join(_TMP, "replication.sqlite")
_ALIASES = L.ALIASES_PATH
if os.path.exists(_ALIASES):
    shutil.copy(_ALIASES, os.path.join(_TMP, "aliases.json"))
L.ALIASES_PATH = os.path.join(_TMP, "aliases.json")
anchor.init()
REP.init()

SPEC = os.path.join(os.path.dirname(HERE), "sandbox", "campaigns", "PMID-41964971.json")
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
PORT = _s.getsockname()[1]
_s.close()
BASE = f"http://127.0.0.1:{PORT}"


def get(path):
    with _real_urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def post(path, body, token=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    if token:
        req.add_header("X-Console-Token", token)
    try:
        with _real_urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


srv = LC.QuietServer(("127.0.0.1", PORT), LC.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError("the console must not reach the network"))

try:
    print("\n1. The page and the world it reads")
    with _real_urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode()
    check("the landing page is served", r.status == 200 and "Phoenix Lab" in html,
          f"{len(html)} bytes")
    check("it is the framework's page, not a mock",
          "Awaiting your decision" in html and "Check a claim against a paper" in html
          and "villager" not in html.lower())
    o = get("/api/overview")
    check("the evidence store is reported from the real store",
          o["evidence"] == len(L.store_ids()) and o["evidence"] > 0,
          f"{o['evidence']} papers held")
    check("nothing is pending before anything has run",
          o["gate"] == 0 and o["targets"] == 0)
    check("campaign specs are discoverable", any(s["path"].endswith(".json")
                                                 for s in get("/api/specs")["specs"]))

    print("\n2. Checking one claim — the oracle with the lid off")
    rec = L.store_get("PMID:41884158")
    quote = next(s for s in L.sentences(L.record_text(rec)) if "AMPK/Drp1 pathway" in s)
    _, v = post("/api/check", {"pmid": "PMID:41884158", "subject": "metformin",
                               "relation": "activates", "object": "AMPK", "quote": quote})
    check("a quoted sentence that says it is supported", v["verdict"] == "supported",
          f"from the {v.get('section')}")
    _, v = post("/api/check", {"pmid": "PMID:41884158", "subject": "metformin",
                               "relation": "activates", "object": "AMPK",
                               "quote": "Metformin was shown to cure osteoarthritis outright."})
    check("an invented sentence is caught", v["verdict"] == "fabricated-quote")
    _, v = post("/api/check", {"pmid": "41884158", "subject": "metformin",
                               "relation": "activates", "object": "AMPK", "quote": ""})
    check("a bare PMID resolves, and an unquoted citation is refused",
          v["verdict"] == "unquoted")
    _, v = post("/api/check", {"pmid": "PMID:99999999", "subject": "x",
                               "relation": "activates", "object": "y", "quote": "z" * 40})
    check("a paper that was never retrieved cannot be cited",
          v["verdict"] == "unresolved-citation")

    print("\n3. Running a campaign, and what it puts at the gate")
    spec = CAM.load_spec(SPEC)
    CAM.prepare(spec)
    CAM.Campaign(spec, offline=True, log=lambda *_: None).run()
    REP.park_critique(spec["target"])
    o = get("/api/overview")
    check("the paper appears under replication with its verdict",
          o["targets"] == 1 and o["replications"][0]["verdict"] in
          ("undetermined", "corroborated", "diverged", "refuted"),
          o["replications"][0]["verdict"])
    check("the critique is waiting for a human", o["gate"] == 1
          and o["pending"][0]["kind"] == "critique")
    check("the gate item states its limitations before asking for a decision",
          len(o["pending"][0]["limitations"]) >= 3)
    d = get("/api/target?pmid=" + spec["target"])
    check("the detail view carries the paper's own quotes", bool(d["claims"])
          and all(c["quote"] for c in d["claims"]), f"{len(d['claims'])} claims")
    check("the arithmetic is shown reported against recomputed",
          len(d["recomputations"]) >= 9
          and any(r["verdict"] == "divergent" for r in d["recomputations"]),
          f"{len(d['recomputations'])} statistics")
    check("every finding says which claim it answers",
          all(f["for_claim"] for f in d["findings"]) if d["findings"] else True,
          f"{len(d['findings'])} findings")
    check("what is still open is shown, never hidden", bool(d["blockers"]))

    print("\n4. The gate — the console is the human")
    os.environ["CONSOLE_TOKEN"] = "smoke-token"
    code, _ = post("/api/decide", {"kind": "critique", "id": 1, "decision": "approve"})
    check("without the token an action is refused", code == 401)
    code, r = post("/api/decide", {"kind": "critique", "id": 1, "decision": "refuse"},
                   token="smoke-token")
    check("with the token the human decision lands", code == 200 and r["ok"], r["message"])
    del os.environ["CONSOLE_TOKEN"]
    check("nothing is pending once decided", get("/api/overview")["gate"] == 0)
    code, r = post("/api/run", {"spec": "../gov/anchor.py", "offline": True})
    check("a spec outside sandbox/campaigns is refused", code == 400
          and "REFUSED" in r.get("message", ""), r.get("message", "")[:60])
finally:
    urllib.request.urlopen = _real_urlopen
    srv.shutdown()
    L.ALIASES_PATH = _ALIASES
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

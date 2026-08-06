"""Headless smoke test for the Lab console — the surface a user actually touches.

Boots the real server on an ephemeral port against a temp database and exercises what
the page can do: read the world, check a claim, run a campaign, decide at the gate —
and, since this is a shared service with no accounts, that two guests using it at the
same time never see or block each other's work. The point throughout is that the
console cannot drift from the oracle — every number it shows comes from the same
stores the acceptance suites run against.

No network: the campaign is run offline and `urlopen` is torn out first.

Run:  python3 gov/lab_console_smoke.py
"""

import http.cookiejar
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
SPEC_REL = "sandbox/campaigns/PMID-41964971.json"   # the path the /api/run endpoint expects
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
PORT = _s.getsockname()[1]
_s.close()
BASE = f"http://127.0.0.1:{PORT}"


# A shared cookie jar for the "one operator" tests (sections 1-4, 6) — they read as a
# single continuous session, same as a browser tab that never closes. Sections 5 and 7
# hand in their OWN jars to simulate independent guests with independent cookies.
_default_jar = http.cookiejar.CookieJar()
_openers: dict[int, "urllib.request.OpenerDirector"] = {}


def _opener(jar):
    key = id(jar)
    if key not in _openers:
        _openers[key] = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return _openers[key]


def get(path, jar=None):
    # NOT `jar or _default_jar` — a brand-new CookieJar has zero cookies, and
    # CookieJar defines __len__, so an empty-but-valid jar is falsy and `or` would
    # silently fall through to _default_jar on a guest's very first request, before
    # it has picked up a cookie. `is None` is the only correct test here.
    j = jar if jar is not None else _default_jar
    with _opener(j).open(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def post(path, body, token=None, jar=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    if token:
        req.add_header("X-Console-Token", token)
    j = jar if jar is not None else _default_jar
    try:
        with _opener(j).open(req, timeout=60) as r:
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
    check("evidence truncation is visible, never silent — the shown count is reported "
          "alongside the true total", o["evidence_shown"] == min(o["evidence"], 60))
    check("no lessons exist before any campaign has run", o["skills"] == [])

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
    result = CAM.Campaign(spec, offline=True, log=lambda *_: None).run()
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
    check("reported/recomputed are real numbers over the wire, not stringified twice",
          all(isinstance(r["reported"], (int, float)) for r in d["recomputations"]),
          f"e.g. {d['recomputations'][0]['label']!r} = {d['recomputations'][0]['reported']!r} "
          f"({type(d['recomputations'][0]['reported']).__name__})")
    check("every finding says which claim it answers",
          all(f["for_claim"] for f in d["findings"]) if d["findings"] else True,
          f"{len(d['findings'])} findings")
    check("what is still open is shown, never hidden", bool(d["blockers"]))
    check("the campaign distilled durable lessons from this run",
          bool(result["skills_learnt"]), f"{len(result['skills_learnt'])} lessons")
    o = get("/api/overview")
    check("the console surfaces the same lessons the campaign just learnt",
          set(result["skills_learnt"]) <= {s["lesson"] for s in o["skills"]},
          f"{len(o['skills'])} live skills")
    check("re-running the identical campaign teaches nothing new (Article VI.4 dedup)",
          CAM.Campaign(spec, offline=True, log=lambda *_: None).run()["skills_learnt"] == [])

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

    print("\n5. Multi-guest isolation — a shared service, no accounts, everyone a guest")
    jar_a, jar_b = http.cookiejar.CookieJar(), http.cookiejar.CookieJar()
    get("/api/overview", jar=jar_a)                # each jar's first hit mints a session
    get("/api/overview", jar=jar_b)
    cookies_a = {c.name: c.value for c in jar_a}
    cookies_b = {c.name: c.value for c in jar_b}
    check("two guests get two distinct session cookies",
          cookies_a.get("phx_session") and cookies_b.get("phx_session")
          and cookies_a["phx_session"] != cookies_b["phx_session"])
    sid_a, sid_b = cookies_a["phx_session"], cookies_b["phx_session"]

    code, r = post("/api/run", {"spec": SPEC_REL, "offline": True}, jar=jar_a)
    check("guest A starts a campaign", code == 200 and r["ok"], r.get("message"))

    # White-box, checked against the server's own state rather than raced over HTTP —
    # a fast-converging campaign could finish before any HTTP poll observes it "still
    # running", which would let a real global-lock regression pass a timing-based
    # check by accident. This can't: it inspects the per-session dict directly.
    with LC._RUNS_LOCK:
        has_a = sid_a in LC._RUNS
        b_running = LC._RUNS.get(sid_b, {}).get("running", False)
        distinct_state = LC._RUNS.get(sid_a) is not LC._RUNS.get(sid_b)
    check("guest A's run lives in its own session entry", has_a)
    check("guest B's run state is a different object and was never touched by A's run "
          "— there is no global 'is a campaign running' flag to collide on",
          distinct_state and not b_running)

    code, r = post("/api/run", {"spec": SPEC_REL, "offline": True}, jar=jar_b)
    check("guest B can start their own run — A's did not lock the whole service",
          code == 200 and r["ok"], r.get("message"))

    def _drain(jar, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = get("/api/run/status", jar=jar)
            if not s["running"]:
                return s
            time.sleep(0.15)
        raise TimeoutError("a guest's campaign never finished")

    log_a, log_b = _drain(jar_a)["log"], _drain(jar_b)["log"]
    check("both guests' campaigns actually completed", bool(log_a) and bool(log_b))

    code, _ = post("/api/decide", {"kind": "critique", "id": 1, "decision": "approve"},
                   jar=jar_a)
    ov_a = get("/api/overview", jar=jar_a)
    ov_b = get("/api/overview", jar=jar_b)
    check("the SHARED state (gate, evidence, skills) converges for every guest — that "
          "part is the collective product, not per-session",
          ov_a["gate"] == ov_b["gate"] and ov_a["targets"] == ov_b["targets"]
          and len(ov_a["skills"]) == len(ov_b["skills"]))

    req = urllib.request.Request(BASE + "/api/overview")
    with _real_urlopen(req, timeout=10) as r0:
        check("a brand-new guest with no cookie at all is still served", r0.status == 200)
finally:
    urllib.request.urlopen = _real_urlopen
    srv.shutdown()
    L.ALIASES_PATH = _ALIASES
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

"""Milestone 1 acceptance — the index, proven, and the honesty rules around it.

The handoff's gate: the four queries give CORRECT answers in under a second on a
100k-LOC repo, indexed in under 15 minutes. "Correct" is the hard word, and it is
checked here the only way it can be — against a fixture whose truth is known, plus
regression tests for every wrong answer the first real repository produced.

Run:  python3 mechanic/verify_mechanic.py
"""

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from mechanic import charter, liveness, report, store          # noqa: E402
from mechanic.index import Index, build                        # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))          # a truthy non-bool must not crash the tally
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


# ── a repository whose truth is known ────────────────────────────────────────
# Every construct below is one the first real repo got wrong, or would have.
FIXTURE = {
    "app.py": '''
import re
import threading
from http.server import BaseHTTPRequestHandler

PATTERN = re.compile(r"x+")          # a METHOD named compile — not the builtin

def helper():                         # reached ONLY through the framework handler
    return helper_inner()

def helper_inner():
    return 1

def used_as_callback():               # never called; passed as a value
    return 2

def dead_one():                       # genuinely unreachable
    return 3

def dead_big():                       # unreachable and large enough to matter
''' + "\n".join(f"    x{i} = {i}" for i in range(70)) + '''
    return x1

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                 # invoked by the stdlib, never by this repo
        return helper()

class MyErr(Exception):               # a BUILTIN base is not a framework…
    def code(self):                   # …so this uncalled method is judged: dead
        return 7

def main():
    t = threading.Thread(target=used_as_callback)
    cfg = getattr(t, "daemon", False) # constant name: a spelling of t.daemon
    return t, cfg
''',
    "dyn.py": '''
def maybe():                          # unreachable statically, but…
    return 1

def dispatch(name):
    return getattr(__import__("dyn"), name)()   # …this can reach anything here
''',
    "lib.py": '''
def public_api():
    return 1

def covered():
    return 2

__all__ = ["public_api"]
''',
    "tests/test_lib.py": '''
from lib import covered

def test_covered():
    assert covered() == 2
''',
}


def make_fixture() -> str:
    root = tempfile.mkdtemp(prefix="mechanic-fixture-")
    for rel, src in FIXTURE.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(src)
    return root


root = make_fixture()
db = os.path.join(root, "index.sqlite")
os.environ["MECHANIC_DATA_DIR"] = os.path.join(root, "data")

# ── 1. the index builds, and says what it could not read ─────────────────────
print("\n1. The index — built, counted, honest about what it skipped")
with open(os.path.join(root, "broken.py"), "w") as f:
    f.write("def (:\n")               # a file that will not parse
s = build(root, db)
check("every .py file was seen", s["files"] == 5, f"{s['files']} files")
check("a file that will not parse is a recorded gap, not a silent skip",
      len(s["unreadable"]) == 1 and "broken.py" in s["unreadable"][0][0],
      s["unreadable"][0][1][:50] if s["unreadable"] else "nothing recorded")
check("symbols and edges were extracted", s["symbols"] >= 12 and s["edges"] > 20,
      f"{s['symbols']} symbols, {s['edges']} edges")
idx = Index(db)

# ── 2. the four queries, against known truth ─────────────────────────────────
print("\n2. The four queries — answers derived from the graph, not from search")
check("callers_of resolves a real call", "app.helper" in idx.callers_of("app.helper_inner"),
      ", ".join(idx.callers_of("app.helper_inner")))
check("callers_of does not confuse a reference with a call",
      "app.main" not in idx.callers_of("app.used_as_callback"),
      "Thread(target=fn) is a reference; references_of sees it, callers_of does not")
check("references_of sees the callback handed as a value",
      "app.main" in idx.references_of("app.used_as_callback"))
check("dependencies_of reads the import graph",
      {"re", "threading", "http.server"} <= set(idx.dependencies_of("app")),
      ", ".join(idx.dependencies_of("app")))
check("dependents_of is the same edge read backwards",
      "tests.test_lib" in idx.dependents_of("lib"))
check("tests_covering maps a test to the symbol it exercises",
      idx.tests_covering("lib.covered") == ["tests.test_lib.test_covered"],
      ", ".join(idx.tests_covering("lib.covered")) or "(none)")
check("tests_covering is empty where nothing tests the symbol",
      idx.tests_covering("app.helper") == [])

dead = idx.unreachable_from()
check("unreachable_from finds the genuinely dead symbol", "app.dead_one" in dead)
check("…and the large one", "app.dead_big" in dead)

# ── 3. the three wrong answers the first real repo gave, never again ─────────
print("\n3. Regressions — every false positive the proving repo produced")
check("a refusal PROPAGATES: a helper reached only through a framework handler is live",
      "app.helper" not in dead and "app.helper_inner" not in dead,
      "27 of 27 findings on the first real run were this bug")
check("the framework handler itself is a root, not a finding", "app.Handler.do_GET" not in dead)
check("a function passed as a value (Thread target) is live", "app.used_as_callback" not in dead)
check("getattr with a CONSTANT name does not refuse the module",
      idx.dynamic_reasons("app") == "", f"app: {idx.dynamic_reasons('app') or 'clean'}")
check("obj.compile() is a method, not the builtin — no refusal",
      "compile" not in idx.dynamic_reasons("app"))
check("getattr with a VARIABLE name does refuse the module",
      "getattr" in idx.dynamic_reasons("dyn"), idx.dynamic_reasons("dyn"))
check("every symbol in a refused module is treated as live", "dyn.maybe" not in dead)
check("__all__ exports are roots", "lib.public_api" not in dead)
check("everything a test file defines is a root", "tests.test_lib.test_covered" not in dead)
check("the class with a foreign base is named",
      "app.Handler" in idx.foreign_base_classes())
check("a builtin base (Exception) is NOT a framework — the class is judged, not refused",
      "app.MyErr" not in idx.foreign_base_classes() and "app.MyErr.code" in dead,
      "the first public run refused 25 such classes; ConfigError(Exception) was one")

# ── 4. the analyst applies the Charter to the graph's answer ─────────────────
print("\n4. Liveness — the Charter's honesty rules, enforced in code")
res = liveness.analyse(idx, root)
titles = {f["symbol"] for f in res["findings"]}
check("exactly the dead symbols are reported, nothing live",
      titles == {"app.dead_one", "app.dead_big", "app.MyErr.code"},
      ", ".join(sorted(titles)))
check("every finding is machine-verified and says so",
      all(f["basis"] == "machine-verified" for f in res["findings"]))
check("every finding carries file, line range and the graph fact (Charter §2)",
      all(f["evidence"] and {"file", "line_range", "reason"} <= set(f["evidence"][0])
          for f in res["findings"]))
check("every finding proposes a fix", all(f["recommendation"] for f in res["findings"]))
check("severity follows consequence — the large dead function outranks the small",
      next(f["severity"] for f in res["findings"] if f["symbol"] == "app.dead_big") == "medium"
      and next(f["severity"] for f in res["findings"] if f["symbol"] == "app.dead_one") == "low")
check("largest cost first", res["findings"][0]["symbol"] == "app.dead_big")
scopes = {g["scope"] for g in res["gaps"]}
check("refusals name the dynamic module and the foreign-base class",
      {"dyn", "app.Handler"} <= scopes, ", ".join(sorted(scopes)))
check("no refusal for the module that only used re.compile and constant getattr",
      "app" not in scopes)
check("confidence is below 1.0 — static Python is never certain, and says so",
      all(f["confidence"] < 1.0 for f in res["findings"]))
idx.close()

# ── 5. the record: multi-repo, evidenced, append-only ────────────────────────
print("\n5. The record — a fleet of repos, and nothing admitted without evidence")
store.init()
rid = store.repo_add("file://fixture", name="fixture", local_path=root)
check("re-registering a repo returns the same id (a sweep can declare its fleet)",
      store.repo_add("file://fixture") == rid, rid)
ch = charter.charter()
check("the Charter declares a version and a digest", ch["version"] != charter.UNVERSIONED
      and len(ch["digest"]) == 12, ch["stamp"])
run_id = store.run_open(rid, "deadbeef", ch["stamp"])
check("a run records the charter that governed it", store.run(run_id)["charter"] == ch["stamp"])
try:
    store.finding_add(run_id, rid, {"category": "liveness", "severity": "low",
                                    "title": "unevidenced", "evidence": []})
    admitted = True
except ValueError:
    admitted = False
check("a finding with no evidence is REFUSED by the store (Charter §2)", not admitted)
fids = [store.finding_add(run_id, rid, f) for f in res["findings"]]
check("ids follow the house ripa- convention", all(f.startswith("ripa-fnd-") for f in fids)
      and run_id.startswith("ripa-run-") and rid.startswith("ripa-repo-"))
for g in res["gaps"]:
    store.gap_add(run_id, g["scope"], g["reason"])
store.decision_add(run_id, "analysis", "liveness (index)", "proposed", "graph fact",
                   finding_id=fids[0], model="none — graph fact", charter=ch["stamp"])
d = store.decisions(run_id)
check("the decision record names the actor, the model and the charter",
      d and d[0]["actor"] and d[0]["model"] and d[0]["charter"] == ch["stamp"])
check("findings come back machine-verified first, then by severity",
      [f["severity"] for f in store.findings(run_id=run_id)][:2] == ["medium", "low"])
store.run_close(run_id)
summ = store.summary()
check("the fleet summary counts what the landing page will need",
      summ["repos"] == 1 and summ["findings"] == 3 and summ["gaps"] == 2, str(summ))

# ── 6. the report separates what is proven from what is judged ───────────────
print("\n6. The report — proven and judged never mixed, refusals shown")
md = report.render(run_id)
check("machine-verified findings lead", md.index("## Machine-verified") < md.index("## Where the analysis"))
check("each finding cites file and line range", "app.py:" in md and "line" in md.lower())
check("each finding carries its proposed fix", "**Proposed fix.**" in md)
check("refusals appear with the construct that blocked the proof", "getattr" in md and "app.Handler" in md)
check("the report names the charter that governed it", ch["stamp"] in md)

# ── 7. prohibitions that are code, not policy ────────────────────────────────
print("\n7. Prohibitions — enforced by absence, not by promise (Charter §5)")
src = open(os.path.join(HERE, "index.py")).read()
check("the indexer parses and never executes", "ast.parse(" in src and "exec(" not in src
      and "eval(" not in src.replace("_DYNAMIC_CALLS", "") and "subprocess" not in src)
ctext = charter.text()
check("every Charter section names its enforcer or admits it is not yet enforced",
      all(("> Enforced by" in sec) or ("Not yet enforced" in sec)
          for sec in ctext.split("\n## ")[2:]),
      f"{len(charter.sections())} sections")
check("the sections the SRS says to write first exist (evidence, findings, refusals)",
      any("evidence" in s.lower() for s in charter.sections())
      and any("refusal" in s.lower() for s in charter.sections()))

# ── 8. the Milestone 1 gate, measured on a corpus three times the size ────────
print("\n8. Milestone 1 gate — <15 min to index, <1 s per query, on ≥100k LOC")
big = "/usr/lib/python3.11"
if os.path.isdir(big):
    bdb = os.path.join(root, "big.sqlite")
    t0 = time.time()
    bs = build(big, bdb)
    tb = time.time() - t0
    loc = 0
    for dp, dn, fn in os.walk(big):
        dn[:] = [d for d in dn if d not in ("test", "tests")]
        for f in fn:
            if f.endswith(".py"):
                try:
                    loc += sum(1 for _ in open(os.path.join(dp, f), errors="ignore"))
                except OSError:
                    pass
    check("the corpus is at least 100k LOC", loc >= 100_000, f"{loc:,} LOC, {bs['files']} files")
    check("indexed in under 15 minutes", tb < 900, f"{tb:.1f}s for {bs['symbols']:,} symbols")
    bi = Index(bdb)
    worst = 0.0
    for fn in (lambda: bi.callers_of("json.loads"),
               lambda: bi.dependencies_of("asyncio.base_events"),
               lambda: bi.tests_covering("json.loads"),
               lambda: bi.unreachable_from()):
        t0 = time.time()
        fn()
        worst = max(worst, time.time() - t0)
    check("every one of the four queries answers in under a second", worst < 1.0,
          f"slowest {worst * 1000:.0f} ms")
    bi.close()
else:
    check("a ≥100k-LOC corpus was available to measure against", False,
          f"{big} not present — the gate could not be measured here")

# ── the UI: Milestone 6 pulled forward, and the rules a public form needs ─────
# A shell on your own machine and a form on a public host are different threats. The
# web layer keeps the CLI's single analysis path and adds exactly the rules that
# difference demands — each one checked here without touching the network.
print("\nWeb — the landing page drives the same path, behind the same gate")
import json                                                              # noqa: E402
from mechanic import analyse, ingest, web                              # noqa: E402

check("only GitHub HTTPS URLs are accepted for cloning",
      ingest.accepted("https://github.com/o/r.git") == (True, "o/r")
      and not ingest.accepted("https://gitlab.com/o/r")[0]
      and not ingest.accepted("/etc")[0],
      "the read-only guarantee is enforced for one host, not promised for all")
os.environ["GITHUB_TOKEN"] = "hunter2"
_h = ingest.headers()
del os.environ["GITHUB_TOKEN"]
check("no request ever carries a credential, whatever the environment holds (Charter §5)",
      "Authorization" not in _h and set(_h) == {"User-Agent", "Accept"},
      "an HTTP GET of an archive cannot write; a write to the origin is impossible")
check("ingestion needs no git — the deployed image has none, and the first public "
      "run said so", "subprocess" not in open(os.path.join(HERE, "ingest.py")).read(),
      "GitHub's archive endpoint, streamed with the standard library")
check("an archive member that escapes the destination is refused",
      ingest._safe("Repo-HEAD/../etc/passwd") == "" and ingest._safe("Repo-HEAD/") == ""
      and ingest._safe("Repo-HEAD/a/b.py") == "a/b.py")

_code, _ct, _body = web.handle_get("/mechanic")
check("the page is served and carries the console's palette placeholders",
      _code == 200 and _ct.startswith("text/html") and "/*TOKENS*/" in _body
      and "/*PALETTE_JS*/" in _body and ":root{--bg:" in _body,
      "house palette when mounted, fallback palette standalone")
_code, _ct, _body = web.handle_get("/api/mechanic/summary")
_j = json.loads(_body)
check("the summary endpoint reports fleet, charter and what is running",
      _code == 200 and {"summary", "charter", "active"} <= set(_j)
      and _j["active"]["status"] == "idle")
check("unknown runs and routes are 404, not 500",
      web.handle_get("/api/mechanic/run?id=nope")[0] == 404
      and web.handle_get("/api/mechanic/nope")[0] == 404)
check("a local path is refused from the web — that privilege stays with the CLI",
      web.handle_post("/api/mechanic/analyse", {"path": "/etc"})[0] == 400)
check("a non-GitHub URL is refused before any clone is attempted",
      web.handle_post("/api/mechanic/analyse", {"url": "https://gitlab.com/o/r"})[0] == 400)
web._LOCK.acquire()
try:
    _code, _, _body = web.handle_post("/api/mechanic/analyse", {"url": "https://github.com/o/r"})
finally:
    web._LOCK.release()
check("a second analysis while one runs gets 409, not a queue that fills the disk",
      _code == 409 and "one at a time" in _body)

_src = open(os.path.join(HERE, "__main__.py")).read()
check("the CLI is a thin wrapper over the same analyse.run the web uses",
      "analyse.run(" in _src and "def _sha(" not in _src and "liveness.analyse" not in _src,
      "one path; the button cannot drift from the command")
_console = os.path.join(os.path.dirname(HERE), "gov", "sim_console.py")
if os.path.exists(_console):
    _cs = open(_console).read()
    check("the console mounts the mechanic through a delegating shim",
          '"/mechanic"' in _cs and "_mechanic_web().handle_get" in _cs
          and "_mechanic_web().handle_post" in _cs)
    check("the analyse trigger sits BEHIND the console's token gate",
          _cs.index("console token required") < _cs.index("_mechanic_web().handle_post"),
          "POWER when CONSOLE_TOKEN is set; spectating stays free")

_saved = {k: os.environ.pop(k, None) for k in ("MECHANIC_DATA_DIR", "GOV_DATA_DIR")}
_vol = tempfile.mkdtemp(prefix="mechanic-volume-")
try:
    os.environ["GOV_DATA_DIR"] = _vol
    check("with no MECHANIC_DATA_DIR the record lives on the settlement's volume",
          store.data_dir() == os.path.join(_vol, "mechanic"),
          "a redeploy must not reset the fleet to ripa-run-0001 — it did, twice")
finally:
    for k, v in _saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    shutil.rmtree(_vol, ignore_errors=True)

# The regression that matters most: the mechanic on itself. Every earlier false
# positive and every true one it found in its own code is pinned here at zero.
_self = analyse.run(HERE, name="mechanic-self")
check("the mechanic run on itself reports no findings",
      _self["status"] == "complete" and _self["findings"] == 0,
      f"{_self['findings']} finding(s), {_self['gaps']} refusal(s) — "
      f"the console-facing entry points are declared, so nothing looks dead")
check("…and exactly the one honest refusal: the AST visitor's framework-called methods",
      _self["gaps"] == 1)

shutil.rmtree(root, ignore_errors=True)   # last, after every section that writes into it
print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

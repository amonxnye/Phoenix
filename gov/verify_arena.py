"""Acceptance checks for the Arena — provider registry, per-role routing, the price
table, the dollar ceiling, and the three constitutional rules of Article X.

Proves ARENA.md steps 1-3 without a single API key or model call:
  1. With every role unset, routing is invisible: the brain resolves exactly as it did
     before models.py existed.
  2. A routed role carries its own model; the default brain still serves the rest.
  3. Assignment is human-only, and every assignment is recorded.
  4. A failed seat ABSTAINS — the board records `unknown`, and no other model fills it.
  5. Every call records role + model + dollars; every decision records its model.
  6. Cost is computed from the human-maintained price table; an unpriced model is
     reported as unpriced, never guessed.
  7. The ceiling refuses an over-budget run up front and halts a breaching one — it
     never finishes on a cheaper model.
  8. Every rule-based rescue is counted, so no scorecard credits the rules' work to
     the model that failed.
  9. One router anywhere in the board makes the whole run SCOUTING, not ranked.
 10. The landing page and /providers serve no keys, the operator console keeps its
     own route, and only the human can assign a model.

Run:  python3 gov/verify_arena.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# an isolated anchor: these checks write config and decisions, and must never touch a
# live settlement's permanent memory
os.environ["GOV_DATA_DIR"] = os.path.join(HERE, ".arena-verify")
os.makedirs(os.environ["GOV_DATA_DIR"], exist_ok=True)
for f in os.listdir(os.environ["GOV_DATA_DIR"]):
    os.remove(os.path.join(os.environ["GOV_DATA_DIR"], f))

import anchor as A
import board
import brain
import models as M

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def clear_env():
    for role in M.ROLES:
        os.environ.pop(f"MODEL_ROLE_{role.upper()}", None)
    for k in ("MODEL_REGISTRY", "MODEL_PRICES", "EVAL_BUDGET_USD",
              "EVAL_PROVIDER_CAP_USD", "BRAIN_BASE_URL", "BRAIN_API_KEY",
              "BRAIN_MODEL", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)
    A.config_set("models.roles", "")
    A.config_set("models.registry", "")
    A.config_set("models.prices", "")
    M.reset_run_state()


A.init()
clear_env()

print("\n1. Unrouted is invisible — a Phoenix without an arena behaves as before")
check("no role resolves when nothing is assigned",
      all(M.resolve(r) is None for r in M.ROLES))
check("models.active() is False, so nothing claims to be routed", M.active() is False)
check("the brain reports rule-based with no provider anywhere",
      brain.brain_name() == "rule-based" and brain.available() is False)
os.environ["BRAIN_BASE_URL"] = "https://api.deepseek.com"
os.environ["BRAIN_API_KEY"] = "test-key"
os.environ["BRAIN_MODEL"] = "deepseek-v4-flash"
check("every role falls through to the single default brain",
      all((brain.provider_for(r) or {}).get("model") == "deepseek-v4-flash"
          for r in M.ROLES),
      brain.brain_name())

print("\n2. Per-role routing — a different mind in a different chair")
os.environ["MODEL_REGISTRY"] = json.dumps({
    "labA": {"kind": "openai", "base_url": "https://a.example/v1",
             "key": "k-a", "model": "model-a", "tier": "ranked"},
    "labB": {"kind": "openai", "base_url": "https://b.example/v1",
             "key": "k-b", "model": "model-b", "tier": "ranked"},
})
os.environ["MODEL_ROLE_PRUDENCE"] = "labA"
os.environ["MODEL_ROLE_GROWTH"] = "labB:model-b-pro"
check("a routed seat resolves to ITS provider",
      (M.resolve("Prudence") or {}).get("model") == "model-a")
check("a 'provider:model' spec pins the model",
      (M.resolve("Growth") or {}).get("model") == "model-b-pro")
check("an unrouted seat still falls through to the default brain",
      M.resolve("Ledger") is None
      and (brain.provider_for("Ledger") or {}).get("model") == "deepseek-v4-flash")
check("the assignment map names every seat's model",
      M.assignments()["Prudence"] == "labA:model-a"
      and M.assignments()["Growth"] == "labB:model-b-pro")
check("an unregistered provider cannot be assigned",
      M.assign("Ledger", "nosuchlab")[0] is False)
check("an unknown role cannot be assigned", M.assign("emperor", "labA")[0] is False)
ok, msg = M.assign("Ledger", "labA")
check("the human may assign a registered provider to a real role", ok, msg)
check("the assignment persists in the anchor, where the console reads it",
      json.loads(A.config_get("models.roles", "{}")).get("Ledger") == "labA")
check("a provider with NO key resolves to nothing — silence, not a substitution",
      M.resolve("worker") is None
      and all(not p["keyed"] for p in M.catalog() if p["name"] == "openai"))

print("\n3. Outages abstain — the chair stays empty (Article X.3)")
M.note_call("Prudence", "labA", "model-a", 0.0, False, "connection refused")
check("a failed seat is reported offline", M.offline_seats() == ["Prudence"])
ctx = {"affordable": True, "within_budget": True, "spent": 100, "cap": 10_000,
       "burn_per_turn": 10, "progress_delta": 5, "understaffed": False}
bv = board.vote("spawn vil-09", ctx)
check("the offline seat votes unknown, not yes and not no",
      bv["ballots"]["Prudence"] is None and "Prudence" in bv["abstained"])
check("the abstention says why, and says the seat was not filled",
      "abstains" in bv["reasons"]["Prudence"]
      and "not filled" in bv["reasons"]["Prudence"])
check("the other seats vote normally — one dead provider is not a dead board",
      bv["ballots"]["Growth"] is True and bv["ballots"]["Ledger"] is True)
check("an abstention cannot become an approval: quorum counts yes votes only",
      bv["yes"] == 2 and bv["approved"] is True)
M.note_call("Growth", "labB", "model-b", 0.0, False, "500")
M.note_call("Ledger", "labA", "model-a", 0.0, False, "500")
bv2 = board.vote("spawn vil-10", ctx)
check("a wholly offline board approves NOTHING", bv2["approved"] is False
      and bv2["yes"] == 0, bv2["tally"])
M.note_call("Prudence", "labA", "model-a", 0.0, True)
M.note_call("Growth", "labB", "model-b", 0.0, True)
M.note_call("Ledger", "labA", "model-a", 0.0, True)
check("a recovered seat votes again", M.offline_seats() == [])

print("\n4. Every call and every decision records its model (Article X.2)")
A.model_call_log("https://a.example/v1", "model-a", "think", 120, 900, 100, True,
                 role="Prudence", cost_usd=0.0021)
A.model_call_log("https://a.example/v1", "model-a", "think", 400, 800, 90, False,
                 "timeout", role="Prudence", cost_usd=0.0)
A.model_call_log("https://b.example/v1", "model-b-pro", "think", 200, 500, 60, True,
                 role="Growth", cost_usd=0.0015)
by_model = {r["model"]: r for r in A.model_calls_by_model()}
check("calls are attributable to the model that served them",
      set(by_model) == {"model-a", "model-b-pro"}, str(sorted(by_model)))
check("the seat that made the call is recorded",
      by_model["model-a"]["roles"] == ["Prudence"])
check("errors and latency percentiles are per model",
      by_model["model-a"]["calls"] == 2 and by_model["model-a"]["errors"] == 1
      and by_model["model-a"]["p95_ms"] == 120)
check("dollars are summed per model",
      abs(by_model["model-a"]["cost_usd"] - 0.0021) < 1e-9)
did = A.reason_add(5, "Prudence", "block the spawn", "runway 3 turns at current burn",
                   derived_from=["event:1"], authorized_by="board:2/3")
row = next(r for r in A.reasons_top(5) if r["id"] == did)
check("a decision records the model that made it", row["model"] == "model-a",
      row["model"])
A.decision_close(did, outcome="+120 food")
hit = {r["model"]: r for r in A.decisions_by_model()}
check("the decision hit rate is a query over the lineage engine",
      hit["model-a"]["hit_rate"] == 100 and hit["model-a"]["closed_pct"] == 100)
d2 = A.reason_add(6, "Prudence", "build a mill", "wood is stocked")
A.decision_close(d2, outcome="failed — not enough wood")
hit = {r["model"]: r for r in A.decisions_by_model()}
check("a failed outcome is a miss, and halves that model's hit rate",
      hit["model-a"]["hit_rate"] == 50, str(hit["model-a"]))
d3 = A.reason_add(7, "Prudence", "watch the yields", "no measurable result yet")
hit = {r["model"]: r for r in A.decisions_by_model()}
check("an open decision counts as neither hit nor miss",
      hit["model-a"]["decisions"] == 3 and hit["model-a"]["closed"] == 2
      and hit["model-a"]["hit_rate"] == 50)
check("grounding is measured, not assumed",
      hit["model-a"]["grounded"] == 1, str(hit["model-a"]["grounded_pct"]) + "%")

print("\n5. The price table is human-maintained, and cost is computed at log time")
check("a seeded price gives a real dollar figure",
      M.cost_usd("deepseek-v4-flash", 1_000_000, 1_000_000) == 0.7)
check("longest-prefix matching finds a dated model name",
      M.cost_usd("claude-sonnet-5-20260101", 1_000_000, 0) == 3.0)
check("an unpriced model costs zero and SAYS it is unpriced",
      M.cost_usd("model-a", 1_000_000, 1_000_000) == 0.0 and M.unpriced("model-a"))
os.environ["MODEL_PRICES"] = json.dumps({"model-a": {"in": 2.0, "out": 8.0}})
check("the human's price table overrides the seed",
      M.cost_usd("model-a", 1_000_000, 500_000) == 6.0 and not M.unpriced("model-a"))

print("\n6. The ceiling is structural — the run halts, it never downgrades")
M.reset_run_state()
os.environ["EVAL_BUDGET_USD"] = "1.00"
M.check_budget()                                   # under the ceiling: no exception
M.note_call("governor", "labA", "model-a", 0.60, True)
check("spend accumulates per run and per provider",
      M.spent_usd() == 0.6 and M.spent_usd("labA") == 0.6)
check("under the ceiling the run continues", M.budget_state()["over"] is False)
M.note_call("governor", "labA", "model-a", 0.50, True)
st = M.budget_state()
check("a breach is visible in the budget state", st["over"] is True
      and st["remaining"] < 0)
try:
    M.check_budget()
    breached = False
except M.BudgetExceeded as e:
    breached = "1.00" in str(e) or "spent" in str(e)
check("the seam refuses further calls once the ceiling is spent", breached)
M.reset_run_state()
os.environ["EVAL_PROVIDER_CAP_USD"] = json.dumps({"labA": 0.10})
M.note_call("governor", "labA", "model-a", 0.15, True)
check("a per-provider sub-cap breaches on its own",
      M.budget_state()["breached_providers"] == ["labA"])
del os.environ["EVAL_PROVIDER_CAP_USD"]
M.reset_run_state()
pf = M.preflight(1000, "model-a", 1000, 200)
check("a pre-flight estimate is computed before a dollar is spent",
      pf["estimate_usd"] == round(2.0 + 1.6, 4), str(pf["estimate_usd"]))
check("an estimate over the ceiling is REFUSED up front", pf["refused"] is True)

print("\n7. A model that errors scores the error (EVAL.md §5)")
M.reset_run_state()
check("a clean run has nothing to explain away", M.fallbacks() == 0)
os.environ["MODEL_ROLE_GOVERNOR"] = "labA"
got = brain.think("the Chief Governor", "situation", "say something")  # labA is not real
check("a failed model call returns None so the rules can carry the turn", got is None)
check("...and the rescue is COUNTED, per role and in total",
      M.fallbacks() == 1 and M.fallbacks("governor") == 1)
del os.environ["MODEL_ROLE_GOVERNOR"]
M.reset_run_state()

print("\n8. Routers are scouting, never ranked (ARENA.md §1)")
check("a direct board is ranked", M.tier() == "ranked")
os.environ["MODEL_ROLE_FLEET"] = "openrouter"
os.environ["OPENROUTER_API_KEY"] = "k-router"
check("one router anywhere makes the whole run scouting", M.tier() == "scouting",
      M.tier())
check("the router is identified by its base URL, not by trust",
      M.is_router("https://openrouter.ai/api/v1") and not M.is_router("https://api.openai.com/v1"))

print("\n9. Keys never leave the process")
blob = json.dumps(M.catalog())
check("the console's view of the registry contains no key material",
      "k-a" not in blob and "k-b" not in blob and "k-router" not in blob)
check("but it does say whether a provider is usable",
      any(p["name"] == "labA" and p["keyed"] for p in M.catalog()))

print("\n10. The pages, live — the landing page, /providers, and the human-only gate")
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

os.environ["CONSOLE_TOKEN"] = "arena-test-token"
import sim_console as SC                            # no driver thread: we serve directly

srv = ThreadingHTTPServer(("127.0.0.1", 0), SC.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read().decode()


def post(path, body, token=""):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Console-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


try:
    status, page = get("/")
    check("GET / serves the landing page, not the operator console",
          status == 200 and "Can your model run it?" in page and "PHOENIX" in page)
    status, page = get("/console")
    check("GET /console still serves the operator console",
          status == 200 and "Vision" in page)
    status, raw = get("/api/home")
    home = json.loads(raw)
    check("the landing payload carries live state, the arena and the counts",
          status == 200 and {"live", "arena", "counts", "runs"} <= set(home))
    check("it says which model is in which chair",
          set(home["arena"]["roles"]) == set(M.ROLES))
    check("the landing payload serves no key material",
          "k-a" not in raw and "k-router" not in raw and "arena-test-token" not in raw)
    status, page = get("/providers")
    check("GET /providers serves the page", status == 200 and "PROVIDERS" in page)
    status, raw = get("/api/providers")
    d = json.loads(raw)
    check("the API reports every role and what serves it",
          status == 200 and set(d["roles"]) == set(M.ROLES))
    check("the API carries the per-model and per-decision tables",
          "by_model" in d and "by_decision" in d and "budget" in d)
    check("the API never serves a key",
          "k-a" not in raw and "k-router" not in raw and "arena-test-token" not in raw)
    code, _ = post("/api/models", {"role": "governor", "model": "labA"})
    check("assigning a model WITHOUT the operator token is refused (Article X.1)",
          code == 401)
    check("...and nothing changed", M.resolve("governor") is None)
    code, body = post("/api/models", {"role": "governor", "model": "labA"},
                      "arena-test-token")
    check("the human, holding the token, may assign",
          code == 200 and body["roles"]["governor"] == "labA:model-a")
    check("the assignment is on the permanent record",
          any("assigned governor" in e for e in A.event_log(50)))
    check("the decision record names the human as the authority",
          any(r["authorized_by"] == "human" and "assign model" in r["decision"]
              for r in A.reasons_top(10)))
    code, body = post("/api/models", {"role": "governor", "model": "ghostlab"},
                      "arena-test-token")
    check("an unregistered provider is refused even for the human", code == 400)

    # the board may PROPOSE a swap; it may not make one (Article X.1)
    M.note_call("Prudence", "labA", "model-a", 0.0, False, "connection refused")
    for _ in range(SC.SEAT_WINDOW):
        SC._narrate_vote(board.vote("staffing", dict(ctx)))
    msgs = " ".join(m["body"] for m in A.msg_thread("chief", 50))
    check("a seat that stops voting is reported to the human, with the number",
          "MODEL SWAP PROPOSED" in msgs and "100%" in msgs)
    check("the board proposes but does not reassign — the role is untouched",
          M.resolve("Prudence")["model"] == "model-a")
    before = len([m for m in A.msg_thread("chief", 50) if "MODEL SWAP" in m["body"]])
    for _ in range(SC.SEAT_WINDOW):
        SC._narrate_vote(board.vote("staffing", dict(ctx)))
    after = len([m for m in A.msg_thread("chief", 50) if "MODEL SWAP" in m["body"]])
    check("it says so once, then stops — a proposal is not a nag", before == after)
finally:
    srv.shutdown()
    os.environ.pop("CONSOLE_TOKEN", None)

clear_env()
print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

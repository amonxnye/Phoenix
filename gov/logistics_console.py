"""The planning console — the operator surface for the logistics harness.

This is the page a planner works in. It answers the four questions the domain asks,
and nothing else:

  1. **Are we meeting the mandate?**   fill rate, working capital, waste, freight —
     each against its target, from the oracle, not from an agent's report.
  2. **What is the plan?**             every SKU's reorder point and order-up-to level
     at both nodes, with the demand it was fitted to.
  3. **Would it survive a bad week?**  the same plan scored under every disruption.
  4. **What needs me?**                purchase orders parked at the gate, priced by
     what the wait is costing, with the board's ballots attached.

**It is a shared world with many visitors.** There are no accounts, so everyone is a
guest with a stable derived id (`guest-xxxxxx`, a hash of address and user-agent — no
cookie, no personal data), and that id signs everything they do: the policies they
propose accrue to a career under their name, the orders they draft carry it, and the
decisions they take at the gate are recorded against it forever. Attribution is what
accountability looks like when there is no login.

Three things follow from being multi-user, and all three are enforced here rather than
hoped for:

  * **One planning run at a time.** Planning is the expensive path (dozens of full
    simulations); a non-blocking lock means the second visitor is told someone else is
    planning instead of both runs interleaving into the same policy ledger.
  * **A per-guest cooldown**, so one visitor cannot spend the box's CPU on a loop.
  * **The gate is compare-and-set.** Two people can open the same dossier; only the
    first decision lands, and the second is told so (`decide` updates only rows still
    `pending`).

Reversible work — planning, simulating, comparing, drafting — runs from a button. The
one irreversible thing, committing to a purchase, waits for a person, and even their
approval does not place an order: it records a decision for them to take to their own
system. ``/api/place`` exists so the guard can be tested from the page and *seen* to
refuse (Article V).

Tiers: reading is always free. When CONSOLE_TOKEN is set the console is in **operator**
mode and every mutation needs the token; with no token it is in **guest** mode and any
visitor may plan, draft and decide — signed, logged, and rate-limited.

Run:  python3 gov/logistics_console.py            → http://127.0.0.1:8790
      python3 gov/logistics_console.py --seed     → and lay down the baseline plan
"""

import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import economy
import logistics_world as L
import planner as P

PAGE_PATH = os.path.join(os.path.dirname(HERE), "phoenix-command.html")

# ── house limits. The page reads every one of these from /api/config rather than
# carrying its own copy, so there is exactly one place to change any of them.
MAX_ORDER_UNITS = int(os.environ.get("GOV_MAX_ORDER_UNITS") or 1_000_000)
MAX_ROUNDS = int(os.environ.get("GOV_MAX_ROUNDS") or 50)
PLAN_COOLDOWN_S = float(os.environ.get("GOV_PLAN_COOLDOWN_S") or 30)
POLL_MS = int(os.environ.get("GOV_POLL_MS") or 15_000)
VISITOR_WRITE_EVERY_S = 30          # throttle presence writes; pollers are not news
TILE_HEADROOM = 0.75                # a budget under three-quarters spent reads as green

_plan_lock = threading.Lock()       # one planning run at a time, process-wide
_last_plan: dict = {}               # guest → when they last started one
_last_seen: dict = {}               # guest → when we last wrote presence


# ── who is here ───────────────────────────────────────────────────────────────

def guest_id(remote: str, forwarded: str, user_agent: str) -> str:
    """A stable, anonymous handle for one visitor. Derived, never stored raw: the hash
    is all that touches the database. Pure so the acceptance suite can check that the
    same visitor gets the same id and a different one does not."""
    ip = (forwarded or "").split(",")[0].strip() or remote or "unknown"
    h = hashlib.sha1(f"{ip}|{user_agent}".encode()).hexdigest()[:6]
    return f"guest-{h}"


def mode() -> str:
    return "operator" if os.environ.get("CONSOLE_TOKEN", "") else "guest"


# ── the mandate, as meters ────────────────────────────────────────────────────

def _budget_state(x: float, budget: float) -> str:
    """Green with room, amber inside the budget, red over it. The only judgement is
    TILE_HEADROOM; the budgets themselves come from the mandate."""
    r = x / budget if budget else 0.0
    return "good" if r <= TILE_HEADROOM else ("warn" if r <= 1.0 else "crit")


def _service_state(fill: float) -> str:
    """Derived from the mandate, not from a taste in numbers: green at or above the
    target, red once the shortfall has eaten the entire service score (the point where
    SERVICE_SLOPE takes it to zero), amber in between."""
    if fill >= L.TARGET_FILL:
        return "good"
    shortfall = (L.TARGET_FILL - fill) / L.TARGET_FILL
    return "warn" if shortfall < 1.0 / L.SERVICE_SLOPE else "crit"


def _tiles(n: dict) -> list[dict]:
    """`pct` is how full each budget is, so 100% is the edge in every tile and one
    colour rule covers them all."""
    return [
        {"label": "Fill rate", "value": f"{n['fill_rate']:.1%}",
         "target": f"target {L.TARGET_FILL:.0%}",
         "pct": min(100.0, 100.0 * n["fill_rate"] / L.TARGET_FILL),
         "state": _service_state(n["fill_rate"]),
         "sub": f"A-list {n['a_list_fill']:.1%} · floor {L.A_LIST_FILL_FLOOR:.0%} · "
                f"{n['lost_units']:,} units of demand lost"},
        {"label": "Working capital", "value": L.m(n["working_capital"]),
         "target": f"ceiling {L.m(L.CAPITAL_CEILING)}",
         "pct": min(100.0, 100.0 * n["working_capital"] / L.CAPITAL_CEILING),
         "state": _budget_state(n["working_capital"], L.CAPITAL_CEILING),
         "sub": f"{n['efficiency']:.1f} service points per {L.m(1000)} held"},
        {"label": "Waste", "value": L.m(n["waste_cost"]),
         "target": f"budget {L.m(L.WASTE_BUDGET)}",
         "pct": min(100.0, 100.0 * n["waste_cost"] / L.WASTE_BUDGET),
         "state": _budget_state(n["waste_cost"], L.WASTE_BUDGET),
         "sub": f"{n['waste_units']:,} units scrapped past their shelf life"},
        {"label": "Emergency freight", "value": L.m(n["expedite_spend"]),
         "target": f"budget {L.m(L.EXPEDITE_BUDGET)}",
         "pct": min(100.0, 100.0 * n["expedite_spend"] / L.EXPEDITE_BUDGET),
         "state": _budget_state(n["expedite_spend"], L.EXPEDITE_BUDGET),
         "sub": f"{n['stockout_events']} stockouts, {n['a_list_stockouts']} on the A-list"},
    ]


def _scenario_state(r: dict) -> str:
    """Green if the plan still meets the mandate under this disruption; otherwise the
    SAME service curve that scores the plan decides — amber while the service score is
    still positive, red once the shortfall has zeroed it.

    Not "amber only if fill is still above target": under a real disruption fill is
    always below target, so that rule painted every scenario red and told the reader
    nothing. Degrading under a fortnight-long lane closure is expected; collapsing is
    not, and the meter has to be able to tell them apart.
    """
    return "good" if r["mandate_met"] else _service_state(r["fill_rate"])


# ── the read view ─────────────────────────────────────────────────────────────

def _network(inc: dict) -> list[dict]:
    """The plan itself, per SKU: what we hold, where, and the demand it was fitted to."""
    st = L.train_stats()
    rows = []
    for sku, s in L.SKUS.items():
        pol = inc["policy"][sku]
        rows.append({
            "sku": sku, "vendor": s["vendor"], "lead": s["lead"],
            "shelf_life": s["shelf_life"], "a_list": s["a_list"],
            "unit_cost": s["cost"], "mean_daily": st[sku]["mean_daily"],
            "stdev": st[sku]["stdev"], "lead_demand": st[sku]["lead_demand"],
            "dc_s": pol["dc"]["s"], "dc_S": pol["dc"]["S"],
            "store_s": pol["store"]["s"], "store_S": pol["store"]["S"],
            "cover_days": round(pol["dc"]["S"] / max(st[sku]["mean_daily"], 0.01), 1),
            # what the plan itself provides for, per node — the composer prefills with
            # it, and the board prices anything beyond it as unaccounted capital
            "planned": {n: P.planned_quantity(sku, n, inc["policy"]) for n in L.NODES},
        })
    return rows


def _rationale(decision_id: int) -> str:
    """Why this order exists, walked back through the permanent record."""
    if not decision_id:
        return ""
    try:
        return (anchor.lineage(decision_id).get("decision") or {}).get("why", "")
    except Exception:
        return ""


def config() -> dict:
    """Everything the page would otherwise have to hardcode."""
    return {
        "nodes": [{"key": n, "label": "the DC" if n == "dc" else f"the {n}"}
                  for n in L.NODES],
        "currency": L.CURRENCY,
        "poll_ms": POLL_MS,
        "default_rounds": P.SEARCH_ROUNDS,
        "max_rounds": MAX_ROUNDS,
        "max_order_units": MAX_ORDER_UNITS,
        "plan_cooldown_s": PLAN_COOLDOWN_S,
        "mode": mode(),
        "tacit_consent_hours": round(L.TACIT_CONSENT_S / 3600, 2),
        "mandate": (f"{L.TARGET_FILL:.0%} fill at or under "
                    f"{L.m(L.CAPITAL_CEILING)} working capital, A-list never below "
                    f"{L.A_LIST_FILL_FLOOR:.0%}"),
        "train": f"days {L.TRAIN[0]}–{L.TRAIN[1]}",
        "holdout": f"days {L.HOLDOUT[0]}–{L.HOLDOUT[1]}, never readable by the planner",
    }


def snapshot(you: str = "") -> dict:
    inc = P.ensure_incumbent()
    v, n = inc["verdict"], inc["verdict"]["nominal"]
    gate = L.commitments("pending")
    for d in gate:
        d["rationale"] = _rationale(d.get("decision_id") or 0)
        d["tacit_eligible"], d["tacit_why"] = L.within_envelope(d)
        d["mine"] = bool(you) and d["agent"] == you
    scenarios = [{"key": k, "label": L.SCENARIOS[k]["label"], "score": r["score"],
                  "fill_rate": r["fill_rate"], "working_capital": r["working_capital"],
                  "state": _scenario_state(r), "worst": k == v.get("worst")}
                 for k, r in v.get("scenarios", {}).items()]
    decided = [d for d in L.commitments() if d["status"] != "pending"][:10]
    return {
        "ok": True,
        "you": you,
        "watchers": anchor.visitor_stats(),
        "config": config(),
        "score": {"blended": v["score"], "nominal": n["score"],
                  "robust": v["robust_score"], "worst": v.get("worst", ""),
                  "mandate_met": n["mandate_met"]},
        "tiles": _tiles(n),
        "scenarios": sorted(scenarios, key=lambda s: s["score"]),
        "network": _network(inc),
        "incumbent": {"id": inc["id"], "agent": inc["agent"], "score": inc["score"]},
        "leaderboard": L.leaderboard(8),
        "policies_scored": L.policy_count(),
        "gate": gate,
        "decided": decided,
        "planners": [r for r in economy.roster(alive_only=False)
                     if r["agent"].startswith("guest-") or r["agent"].startswith("plan")][:8],
        "envelope": L.envelope(),
        "planning_now": _plan_lock.locked(),
        "log": anchor.event_log(40),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PhoenixLogistics/1.0"

    def _send(self, code, body, ctype="application/json"):
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass                                   # the client hung up; not an error

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > 64_000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    def log_message(self, *_):
        pass

    def _guest(self) -> str:
        """Identify the visitor and record their presence in the permanent anchor —
        throttled, because a 15-second poller is not news."""
        who = guest_id(self.client_address[0],
                       self.headers.get("X-Forwarded-For", ""),
                       self.headers.get("User-Agent", ""))
        now = time.time()
        if now - _last_seen.get(who, 0) > VISITOR_WRITE_EVERY_S:
            _last_seen[who] = now
            try:
                anchor.visitor_touch(who)
            except Exception:
                pass                               # presence must never break a request
        return who

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(PAGE_PATH, encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, f"<pre>cannot read {PAGE_PATH}: {e}</pre>",
                                  "text/html; charset=utf-8")
        if self.path == "/api/logistics":
            return self._send(200, json.dumps(snapshot(self._guest())))
        if self.path == "/api/config":
            return self._send(200, json.dumps(config()))
        if self.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        tok = os.environ.get("CONSOLE_TOKEN", "")
        if tok and self.headers.get("X-Console-Token", "") != tok:
            return self._send(401, json.dumps(
                {"error": "this console is in operator mode — a console token is required"}))
        you = self._guest()
        body = self._read_json()

        # Reversible: planning, simulating, comparing. Runs free (Article IV.2) — but
        # one at a time, and not on a loop from one visitor.
        if self.path == "/api/plan":
            raw = body.get("rounds")
            try:                          # `is None`, not `or`: 0 is a value, not an
                rounds = P.SEARCH_ROUNDS if raw is None else int(raw)   # absent field
            except (TypeError, ValueError):
                return self._send(400, json.dumps({"error": "rounds must be a number"}))
            if not 1 <= rounds <= MAX_ROUNDS:
                return self._send(400, json.dumps(
                    {"error": f"rounds must be between 1 and {MAX_ROUNDS}"}))
            waited = time.time() - _last_plan.get(you, 0)
            if waited < PLAN_COOLDOWN_S:
                return self._send(429, json.dumps(
                    {"error": f"planning again in {PLAN_COOLDOWN_S - waited:.0f}s — "
                              f"the simulator is shared"}))
            if not _plan_lock.acquire(blocking=False):
                return self._send(409, json.dumps(
                    {"error": "someone else is planning right now — the world is shared. "
                              "Their result will appear here when it lands."}))
            try:
                _last_plan[you] = time.time()
                r = P.run_search(you, rounds)
            finally:
                _plan_lock.release()
            return self._send(200, json.dumps({"result": r, "state": snapshot(you)}))

        # Still reversible: a dossier is a proposal, and proposing costs nothing.
        if self.path == "/api/propose":
            sku = body.get("sku") or ""
            if sku not in L.SKUS:
                return self._send(400, json.dumps({"error": f"unknown sku {sku!r}"}))
            node = body.get("node") if body.get("node") in L.NODES else L.NODES[0]
            try:
                qty = int(body.get("qty") or 0)
            except (TypeError, ValueError):
                return self._send(400, json.dumps({"error": "qty must be a whole number"}))
            if qty < 0 or qty > MAX_ORDER_UNITS:
                return self._send(400, json.dumps(
                    {"error": f"qty must be between 1 and {MAX_ORDER_UNITS:,}"}))
            d = P.commit(you, sku, node, qty, str(body.get("note") or "")[:200])
            return self._send(200, json.dumps({"commitment": d, "state": snapshot(you)}))

        # The gate. Compare-and-set, so of two people on the same dossier only the
        # first decision lands and the second is told why (Article IV.1, IV.3).
        if self.path == "/api/decide":
            cid, decision = body.get("id"), body.get("decision")
            if decision not in ("approve", "reject") or not cid:
                return self._send(400, json.dumps(
                    {"error": "id and decision (approve|reject) required"}))
            ok, msg = L.decide(int(cid), decision, you, str(body.get("why") or "")[:200])
            if ok:
                anchor.record(-1, "gate", f"{you} {decision}d commitment {cid}")
            return self._send(200 if ok else 409,
                              json.dumps({"ok": ok, "message": msg,
                                          "state": snapshot(you)}))

        # The guard, callable so it can be seen refusing rather than believed.
        if self.path == "/api/place":
            try:
                cid = int(body.get("id") or 0)
            except (TypeError, ValueError):
                cid = 0
            ok, why = L.place_order(cid)
            return self._send(403, json.dumps({"ok": ok, "message": why}))

        # Article IV.7, on demand: what an hour of silence would decide right now.
        if self.path == "/api/sweep":
            return self._send(200, json.dumps({"swept": P.sweep(),
                                               "state": snapshot(you)}))

        self._send(404, json.dumps({"error": "not found"}))


def main(argv):
    anchor.init()
    economy.init()
    L.init()
    if "--seed" in argv:
        P.ensure_incumbent()
    port = int(os.environ.get("PORT") or "8790")   # `or`, not a default: some platforms
    host = os.environ.get("HOST") or (             # export PORT empty rather than unset
        "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"logistics console → http://{host}:{port}  ({mode()} mode, Ctrl-C to stop)")
    print(L.render_world())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])

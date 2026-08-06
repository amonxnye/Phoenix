"""The planning console — the operator surface for the logistics harness.

This is the page a planner actually works in. It answers the four questions the
domain asks, and nothing else:

  1. **Are we meeting the mandate?**   fill rate, working capital, waste, freight —
     each against its target, from the oracle, not from an agent's report.
  2. **What is the plan?**             every SKU's reorder point and order-up-to level
     at both nodes, with the demand it was fitted to.
  3. **Would it survive a bad week?**  the same plan scored under four disruptions.
  4. **What needs me?**                purchase orders parked at the gate, priced by
     what the wait is costing, with the board's ballots attached.

Reversible work — planning, simulating, comparing — runs from a button. The one
irreversible thing, committing to a purchase, is the only thing that waits for the
person reading the page, and even their approval does not place an order: it records a
decision for them to take to their own system. ``/api/place`` exists solely so the
guard can be tested from the page and seen to refuse (Article V).

Spectating is free. Every mutating endpoint needs `X-Console-Token` when CONSOLE_TOKEN
is set — the same tiered auth the settlement console uses.

Run:  python3 gov/logistics_console.py            → http://127.0.0.1:8790
      python3 gov/logistics_console.py --seed     → and lay down the baseline plan
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import economy
import logistics_world as L
import planner as P

PAGE_PATH = os.path.join(os.path.dirname(HERE), "phoenix-command.html")
OPERATOR = "operator"                              # who a click at the gate is signed by


def _tiles(n: dict) -> list[dict]:
    """The mandate, as four meters. `pct` is how full the budget is, so 100% is the
    edge in every tile and the colour rule is the same everywhere."""
    fill_pct = 100.0 * n["fill_rate"] / L.TARGET_FILL if L.TARGET_FILL else 100.0
    return [
        {"label": "Fill rate", "value": f"{n['fill_rate']:.1%}",
         "target": f"target {L.TARGET_FILL:.0%}", "pct": min(100.0, fill_pct),
         "state": "good" if n["fill_rate"] >= L.TARGET_FILL else
                  ("warn" if fill_pct >= 96 else "crit"),
         "sub": f"A-list {n['a_list_fill']:.1%} · floor {L.A_LIST_FILL_FLOOR:.0%} · "
                f"{n['lost_units']:,} units of demand lost"},
        {"label": "Working capital", "value": f"${n['working_capital']:,.0f}",
         "target": f"ceiling ${L.CAPITAL_CEILING:,.0f}",
         "pct": min(100.0, 100.0 * n["working_capital"] / L.CAPITAL_CEILING),
         "state": _budget_state(n["working_capital"], L.CAPITAL_CEILING),
         "sub": f"{n['efficiency']:.1f} service points per $1k held"},
        {"label": "Waste", "value": f"${n['waste_cost']:,.0f}",
         "target": f"budget ${L.WASTE_BUDGET:,.0f}",
         "pct": min(100.0, 100.0 * n["waste_cost"] / L.WASTE_BUDGET),
         "state": _budget_state(n["waste_cost"], L.WASTE_BUDGET),
         "sub": f"{n['waste_units']:,} units scrapped past their shelf life"},
        {"label": "Emergency freight", "value": f"${n['expedite_spend']:,.0f}",
         "target": f"budget ${L.EXPEDITE_BUDGET:,.0f}",
         "pct": min(100.0, 100.0 * n["expedite_spend"] / L.EXPEDITE_BUDGET),
         "state": _budget_state(n["expedite_spend"], L.EXPEDITE_BUDGET),
         "sub": f"{n['stockout_events']} stockouts, {n['a_list_stockouts']} on the A-list"},
    ]


def _budget_state(x: float, budget: float) -> str:
    r = x / budget if budget else 0
    return "good" if r <= 0.75 else ("warn" if r <= 1.0 else "crit")


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
        })
    return rows


def _rationale(cid_decision: int) -> str:
    """Why this order exists, walked back through the permanent record."""
    if not cid_decision:
        return ""
    try:
        lin = anchor.lineage(cid_decision)
    except Exception:
        return ""
    return (lin.get("decision") or {}).get("why", "")


def snapshot() -> dict:
    inc = P.ensure_incumbent()
    v, n = inc["verdict"], inc["verdict"]["nominal"]
    gate = L.commitments("pending")
    for d in gate:
        d["rationale"] = _rationale(d.get("decision_id") or 0)
        ok, why = L.within_envelope(d)
        d["tacit_eligible"], d["tacit_why"] = ok, why
    scenarios = [{"key": k, "label": L.SCENARIOS[k]["label"],
                  "score": r["score"], "fill_rate": r["fill_rate"],
                  "working_capital": r["working_capital"],
                  "worst": k == v.get("worst")}
                 for k, r in v.get("scenarios", {}).items()]
    return {
        "ok": True,
        "mandate": (f"{L.TARGET_FILL:.0%} fill at or under ${L.CAPITAL_CEILING:,.0f} "
                    f"working capital, A-list never below {L.A_LIST_FILL_FLOOR:.0%}"),
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
        "decided": [d for d in L.commitments() if d["status"] != "pending"][:8],
        "roster": economy.roster(alive_only=False)[:6],
        "envelope": L.envelope(),
        "holdout": f"days {L.HOLDOUT[0]}–{L.HOLDOUT[1]}, never readable by the planner",
        "train": f"days {L.TRAIN[0]}–{L.TRAIN[1]}",
        "skus": list(L.SKUS),
        "token_required": bool(os.environ.get("CONSOLE_TOKEN", "")),
        "log": anchor.event_log(40),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(PAGE_PATH, encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, f"<pre>cannot read {PAGE_PATH}: {e}</pre>",
                                  "text/html; charset=utf-8")
        if self.path == "/api/logistics":
            return self._send(200, json.dumps(snapshot()))
        if self.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        tok = os.environ.get("CONSOLE_TOKEN", "")
        if tok and self.headers.get("X-Console-Token", "") != tok:
            return self._send(401, json.dumps({"error": "console token required"}))
        body = self._read_json()

        # Reversible: planning, simulating, comparing. Runs free (Article IV.2).
        if self.path == "/api/plan":
            rounds = max(1, min(50, int(body.get("rounds") or P.SEARCH_ROUNDS)))
            agent = (body.get("agent") or "plan-01").strip()[:24] or "plan-01"
            r = P.run_search(agent, rounds)
            return self._send(200, json.dumps({"result": r, "state": snapshot()}))

        # Still reversible: a dossier is a proposal, and proposing costs nothing.
        if self.path == "/api/propose":
            sku = body.get("sku") or ""
            if sku not in L.SKUS:
                return self._send(400, json.dumps({"error": f"unknown sku {sku!r}"}))
            node = body.get("node") if body.get("node") in L.NODES else "dc"
            d = P.commit((body.get("agent") or "plan-01").strip()[:24] or "plan-01",
                         sku, node)
            return self._send(200, json.dumps({"commitment": d, "state": snapshot()}))

        # The gate. This is the human's decision and nobody else's (Article IV.1).
        if self.path == "/api/decide":
            cid, decision = body.get("id"), body.get("decision")
            if decision not in ("approve", "reject") or not cid:
                return self._send(400, json.dumps(
                    {"error": "id and decision (approve|reject) required"}))
            ok, msg = L.decide(int(cid), decision, OPERATOR, body.get("why", "")[:200])
            if ok:
                anchor.record(-1, "gate", f"{OPERATOR} {decision}d commitment {cid}")
            return self._send(200 if ok else 409,
                              json.dumps({"ok": ok, "message": msg,
                                          "state": snapshot() if ok else None}))

        # The guard, callable so it can be seen refusing rather than believed.
        if self.path == "/api/place":
            ok, why = L.place_order(int(body.get("id") or 0))
            return self._send(403, json.dumps({"ok": ok, "message": why}))

        # Article IV.7, on demand: what an hour of silence would decide right now.
        if self.path == "/api/sweep":
            return self._send(200, json.dumps({"swept": P.sweep(), "state": snapshot()}))

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
    print(f"logistics console → http://{host}:{port}  (Ctrl-C to stop)")
    print(L.render_world())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])

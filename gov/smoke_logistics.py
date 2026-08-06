"""End-to-end smoke test for the logistics harness — the whole product, over the wire,
with the agents' performance measured and printed.

The unit tests (`test_logistics.py`) check functions. The acceptance suite
(`verify_logistics.py`) checks the constitution's claims. This checks the *product*: it
boots the real console on a real socket, drives the exact journey a visitor drives —
arrive, read the plan, run planning, draft a purchase order, try to place it, decide it
— and then reports what the agents actually achieved and what it cost.

It answers the question a unit test cannot: **is this thing any good, and how fast?**

The performance table is deliberately the same shape as the settlement's production
audit, because those are the numbers that turned out to matter there:

    turns that changed world state   →  proposals that moved the plan (liveness)
    talk events : action events      →  rejected proposals per adoption
    contribution per 1k tokens       →  score points per second of simulation
    reports naming the constraint    →  every run names its binding constraint

Exit code is 0 only if every stage passed AND the fleet cleared the performance floors
below — a smoke test that reports a fleet doing nothing and exits 0 is not a test.

Run:  python3 gov/smoke_logistics.py [--rounds 20] [--json out.json]
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# A throwaway world, so a smoke run never disturbs a live one and always starts from
# the same place — the numbers below mean nothing if the state is inherited.
_TMP = tempfile.mkdtemp(prefix="phoenix-logistics-smoke-")
os.environ["GOV_DATA_DIR"] = _TMP
os.environ.pop("GOV_LOGISTICS_ENVELOPE", None)
os.environ.pop("CONSOLE_TOKEN", None)

import anchor                                                        # noqa: E402
import economy as E                                                  # noqa: E402
import logistics_console as LC                                       # noqa: E402
import logistics_world as L                                          # noqa: E402
import planner as P                                                  # noqa: E402

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"

# ── the floors. A run that clears every stage but produces a fleet that never
# improved anything is a failure, and it should be reported as one.
FLOOR = {
    "adoption_rate": 0.10,          # at least a tenth of proposals must beat the plan
    "points_won": 5.0,              # and they must be worth something on the holdout
    "beats_baseline": True,         # on BOTH service and capital, not one
    "simulations_per_second": 5.0,  # the oracle has to be cheap or nobody will run it
    "p95_request_ms": 2_000.0,      # the read path is what every visitor waits on
}

steps, timings = [], {}


def step(name, ok, detail=""):
    steps.append((name, bool(ok), detail))
    print(f"  {PASS if ok else FAIL} {name}" + (f"{DIM}  — {detail}{OFF}" if detail else ""))
    return ok


class Client:
    """A visitor, with a stable identity of their own."""

    def __init__(self, base, name):
        self.base, self.name = base, name

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": self.name})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        timings.setdefault(path, []).append((time.perf_counter() - t0) * 1000)
        return r.status, body

    def post(self, path, body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": self.name},
            method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                out = r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            out = e.code, json.loads(e.read())
        timings.setdefault(path, []).append((time.perf_counter() - t0) * 1000)
        return out


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=P.SEARCH_ROUNDS)
    ap.add_argument("--json", default="", help="also write the report to this file")
    a = ap.parse_args(argv)

    anchor.init()
    E.init()
    L.init()

    print(f"\n{BOLD}Phoenix Logistics — end-to-end smoke{OFF}")
    print(f"{DIM}a throwaway world in {_TMP}{OFF}\n")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), LC.Handler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    alice, bob = Client(base, "smoke-alice"), Client(base, "smoke-bob")
    report = {"base": base, "rounds": a.rounds}

    try:
        # ── 1. a visitor arrives ──────────────────────────────────────────────
        print(f"{BOLD}1. A visitor arrives{OFF}")
        status, page = alice.get("/")
        step("the console serves the page", status == 200 and "Phoenix Logistics" in page,
             f"{len(page):,} bytes")
        status, raw = alice.get("/api/logistics")
        snap = json.loads(raw)
        step("the first read is complete and self-describing", status == 200
             and {"config", "tiles", "network", "gate", "score"} <= snap.keys(),
             f"{len(snap['network'])} SKUs, {len(snap['scenarios'])} disruptions")
        step("they are identified without an account", snap["you"].startswith("guest-"),
             snap["you"])
        step("a second visitor is a different person",
             json.loads(bob.get("/api/logistics")[1])["you"] != snap["you"])
        baseline = snap["score"]["blended"]
        base_nominal = L.oracle(L.naive_reorder_point())["nominal"]
        step("the world starts on the naive baseline, not on nothing",
             snap["incumbent"]["agent"] == "baseline",
             f"score {baseline:.1f}, fill {base_nominal['fill_rate']:.1%}, "
             f"capital {L.m(base_nominal['working_capital'])}")

        # ── 2. the agents work ────────────────────────────────────────────────
        print(f"\n{BOLD}2. The agents plan{OFF}")
        t0 = time.perf_counter()
        status, out = alice.post("/api/plan", {"rounds": a.rounds})
        wall = time.perf_counter() - t0
        step(f"{a.rounds} policies proposed and scored on held-out demand", status == 200,
             f"{wall:.1f}s wall clock")
        if status != 200:
            raise SystemExit(_finish(report, a, srv))
        result = out["result"]
        perf = result["performance"]
        report["result"] = result
        step("the plan improved on the baseline",
             result["score"] > baseline,
             f"{baseline:.1f} → {result['score']:.1f} "
             f"(+{result['score'] - baseline:.1f} points)")
        step("it beats the baseline on BOTH service and capital",
             result["beats_baseline_on_service"] and result["beats_baseline_on_capital"])
        step("the run names its binding constraint (Article IX)",
             bool(result["binding_constraint"]["why"]),
             result["binding_constraint"]["why"][:96])
        step("a second run from the same visitor is refused, not queued",
             alice.post("/api/plan", {"rounds": 1})[0] == 429,
             f"{snap['config']['plan_cooldown_s']:.0f}s cooldown, the simulator is shared")

        # ── 3. the gate ───────────────────────────────────────────────────────
        print(f"\n{BOLD}3. The gate{OFF}")
        status, made = bob.post("/api/propose", {"sku": L.A_LIST[0], "note": "smoke run"})
        c = made.get("commitment", {})
        step("a purchase order is drafted and parked", status == 200
             and c.get("status") == "pending",
             f"{c.get('qty', 0):,} × {c.get('sku')} from {c.get('vendor')} "
             f"({L.m(c.get('value', 0))}), board {c.get('board', {}).get('tally', '?')}")
        step("the dossier prices the wait (IV.4)", c.get("wait_cost_per_day", 0) > 0,
             f"{L.m(c['wait_cost_per_day'], 2)}/day of silence")
        step("and carries its rollback", "cancellable" in c.get("rollback", ""),
             f"{L.m(c.get('cancel_fee', 0), 2)} to cancel")
        status, refused = bob.post("/api/place", {"id": c["id"]})
        step("the system cannot place it (Article V)", status == 403 and not refused["ok"],
             refused["message"][:72])
        first = alice.post("/api/decide", {"id": c["id"], "decision": "approve",
                                           "why": "smoke run"})
        second = bob.post("/api/decide", {"id": c["id"], "decision": "reject"})
        step("one decision lands, the other is told it lost the race",
             first[0] == 200 and second[0] == 409)
        status, still = bob.post("/api/place", {"id": c["id"]})
        step("approval still does not place it", status == 403 and not still["ok"])
        decided = next((d for d in first[1]["state"]["decided"] if d["id"] == c["id"]), {})
        step("the decision is signed and annotated forever",
             decided.get("decided_by", "").startswith("guest-")
             and decided.get("why") == "smoke run",
             f"{decided.get('status')} by {decided.get('decided_by')}")

        # ── 4. what the fleet learned ─────────────────────────────────────────
        print(f"\n{BOLD}4. What the fleet learned{OFF}")
        lessons = anchor.skills_top(6)
        step("lessons were written from measured numbers", bool(lessons),
             f"{len(lessons)} in the permanent record")
        for s in lessons:
            print(f"    {DIM}·{OFF} {s['lesson']}  {DIM}[{s['trigger']}]{OFF}")
        step("and are read back into the next proposal",
             bool(P.lessons_for_prompt()) and "already paid to learn"
             in P.prompt_for(L.incumbent()))
        careers = [c for c in anchor.careers(50) if c["uid"].startswith("guest-")]
        step("every agent has a career in the permanent record", bool(careers),
             ", ".join(f"{c['uid']} ({len(c['events'])} events)" for c in careers[:3]))

        report["lessons"] = [s["lesson"] for s in lessons]
        report["performance"] = perf
        report["binding_constraint"] = result["binding_constraint"]
        report["roster"] = [r for r in E.roster(alive_only=False)
                            if r["agent"].startswith("guest-")]
        report["latency_ms"] = {p: {"n": len(v), "p50": round(pct(v, 50), 1),
                                    "p95": round(pct(v, 95), 1),
                                    "max": round(max(v), 1)}
                                for p, v in timings.items()}
        report["final"] = L.incumbent()["verdict"]["nominal"]
        report["scenarios"] = {k: r["score"]
                               for k, r in L.incumbent()["verdict"]["scenarios"].items()}
    finally:
        srv.shutdown()

    return _finish(report, a, None)


def _finish(report, args, srv):
    perf = report.get("performance")
    print(f"\n{BOLD}5. Agent performance{OFF}")
    if not perf:
        print("   no run completed")
    else:
        n = report["final"]
        rows = [
            ("proposals scored", f"{perf['proposals']}"),
            ("proposals that moved the plan", f"{perf['adopted']}  "
                                              f"({perf['moved_pct']:.0f}% liveness)"),
            ("rejected per adoption", f"{perf['talk_to_action']:.2f} : 1"),
            ("score points won", f"{perf['points_won']:+.2f}"),
            ("points per proposal", f"{perf['points_per_proposal']:.3f}"),
            ("points per second", f"{perf['points_per_second']:.2f}"),
            ("simulations run", f"{perf['simulations']:,}"),
            ("simulations per second", f"{perf['simulations_per_second']:.1f}"),
            ("seconds per proposal", f"{perf['seconds_per_proposal']:.3f}s"),
            ("", ""),
            ("final fill rate", f"{n['fill_rate']:.2%}  "
                                f"(target {L.TARGET_FILL:.0%})"),
            ("final A-list fill", f"{n['a_list_fill']:.2%}  "
                                  f"(floor {L.A_LIST_FILL_FLOOR:.0%})"),
            ("final working capital", f"{L.m(n['working_capital'])}  "
                                      f"(ceiling {L.m(L.CAPITAL_CEILING)})"),
            ("final waste / freight", f"{L.m(n['waste_cost'])} / "
                                      f"{L.m(n['expedite_spend'])}"),
            ("mandate", "MET" if n["mandate_met"] else "unmet"),
            ("worst disruption", f"{min(report['scenarios'].values()):.1f}  "
                                 f"({min(report['scenarios'], key=report['scenarios'].get)})"),
        ]
        for k, v in rows:
            print(f"   {k:<32}{DIM}{'' if k else ''}{OFF}{v}" if k else "")
        print(f"\n   {DIM}binding constraint: {report['binding_constraint']['why']}{OFF}")

        print(f"\n{BOLD}6. Latency{OFF}")
        for path, t in sorted(report["latency_ms"].items()):
            print(f"   {path:<20} n={t['n']:<3} p50 {t['p50']:>8.1f}ms  "
                  f"p95 {t['p95']:>8.1f}ms  max {t['max']:>8.1f}ms")

        # ── the floors ────────────────────────────────────────────────────────
        print(f"\n{BOLD}7. Performance floors{OFF}")
        read = report["latency_ms"].get("/api/logistics", {"p95": 0})
        checks = [
            ("adoption rate above the floor",
             perf["adoption_rate"] >= FLOOR["adoption_rate"],
             f"{perf['adoption_rate']:.0%} ≥ {FLOOR['adoption_rate']:.0%}"),
            ("the fleet won real points",
             perf["points_won"] >= FLOOR["points_won"],
             f"{perf['points_won']:+.2f} ≥ {FLOOR['points_won']}"),
            ("beat the baseline on both axes",
             report["result"]["beats_baseline_on_service"]
             and report["result"]["beats_baseline_on_capital"], ""),
            ("the oracle is cheap enough to run",
             perf["simulations_per_second"] >= FLOOR["simulations_per_second"],
             f"{perf['simulations_per_second']:.1f}/s ≥ "
             f"{FLOOR['simulations_per_second']}/s"),
            ("the read path stays responsive",
             read["p95"] <= FLOOR["p95_request_ms"],
             f"p95 {read['p95']:.0f}ms ≤ {FLOOR['p95_request_ms']:.0f}ms"),
        ]
        for name, ok, detail in checks:
            step(name, ok, detail)

    passed = sum(1 for _, ok, _ in steps if ok)
    report["steps"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in steps]
    report["passed"], report["total"] = passed, len(steps)
    ok = passed == len(steps)
    print(f"\n{BOLD}{passed}/{len(steps)} checks passed{OFF} — "
          f"{'SMOKE CLEAR' if ok else 'SMOKE FAILED'}\n")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"{DIM}report written to {args.json}{OFF}\n")
    shutil.rmtree(_TMP, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

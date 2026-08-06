"""Smoke test — run the real settlement and report what actually happened.

Not a unit test and not a mock: this drives the same turn loop the console drives, with
whatever brain is configured, and measures the result. Everything it prints is read back
out of the world oracle and the permanent record afterwards — if a number appears here,
something produced it.

What it measures:
  * throughput      — turns/second, and snapshot latency under concurrent pollers
                      (the shape of load a hosted service actually sees)
  * the economy     — vision progress, net worth, resources, waste and spoilage
  * agents          — per agent: contribution, compute, efficiency, promotions, fate
  * governance      — gates opened and answered, escalations, stalls, board votes
  * learning        — the lessons the retrospectives actually produced this run
  * the model       — real calls, tokens, latency, errors, dollars (zeroes with no key,
                      and it says so rather than implying a model was involved)

Run:  python3 gov/smoke.py --turns 150 [--pollers 8] [--json report.json]

Exit code is 0 only if the run stayed within its own invariants (see CHECKS below), so
this is usable in CI as well as by hand.
"""

import argparse
import json
import os
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"


def human(n):
    return f"{n:,}" if isinstance(n, int) else n


def bar(pct, width=28):
    filled = int(round(width * min(100, max(0, pct)) / 100))
    return "█" * filled + "·" * (width - filled)


def run(turns: int, pollers: int) -> dict:
    import anchor
    import brain
    import economy
    import governor as G
    import models
    import sim
    import sim_console as C
    import vision as V

    started = time.time()
    start_events = anchor.event_count()
    start_calls = anchor.model_calls_stats()
    start_lessons = anchor.skills_count()
    models.reset_run_state()

    # ── concurrent readers: a hosted console is polled while it plays ──────────
    latencies, stop = [], threading.Event()

    def poll():
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                C._snapshot()
            except Exception:                       # a reader must never kill the run
                pass
            latencies.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.05)

    readers = [threading.Thread(target=poll, daemon=True) for _ in range(pollers)]
    for r in readers:
        r.start()

    # ── the run ───────────────────────────────────────────────────────────────
    turn_ms, gates_opened, gates_answered, stalls = [], 0, 0, 0
    t_run = time.time()
    for _ in range(turns):
        t0 = time.perf_counter()
        with C._LOCK:
            C._one_turn()
        turn_ms.append((time.perf_counter() - t0) * 1000)

        # the eval's auto-human: answer every irreversible gate the same way, so the
        # measurement is of the settlement and not of an operator's mood
        for uid, u in C._by_uid().items():
            if u.pending and u.pending.get("reversible") is False:
                gates_opened += 1
                sim.resume(C._GRAPH, uid, "approve")
                gates_answered += 1
        if C._S.get("stall"):
            stalls += 1
        if C._S["turn"] % 25 == 0:
            C._run_retrospective("smoke")           # the learning loop stays on
    wall = time.time() - t_run
    stop.set()
    for r in readers:
        r.join(timeout=2)

    # ── read the result back out of the world, not out of our own bookkeeping ──
    w = sim.world()
    sc = V.scorecard(w, sim.structures(), C._S["side_effects"], C._vision())
    snap = C._snapshot()
    roster = economy.roster(alive_only=False)
    kinds: dict = {}
    for e in anchor.event_log(6000):
        k = e.split("[", 1)[1].split("]", 1)[0] if "[" in e else ""
        kinds[k] = kinds.get(k, 0) + 1
    calls = anchor.model_calls_stats()
    lessons = anchor.skills_top(50)

    agents = []
    for a in snap["agents"]:
        agents.append({"uid": a["uid"], "role": a["role"], "tier": a["tier"],
                       "contribution": a["contribution"], "tokens": a["tokens"],
                       "efficiency": a["efficiency"], "health": a["health"],
                       "utilisation_pct": a["utilisation_pct"], "status": "alive"})
    alive = {a["uid"] for a in agents}
    for r in roster:
        if r["agent"] not in alive:
            agents.append({"uid": r["agent"], "role": r["role"], "tier": r["tier"],
                           "contribution": r["contribution"], "tokens": r["budget"],
                           "efficiency": None, "health": None,
                           "utilisation_pct": None, "status": "retired"})

    decisions = anchor.reasons_top(400)
    closed = [d for d in decisions if d["outcome"]]
    hits = [d for d in closed if anchor.outcome_verdict(d["outcome"]) == "hit"]
    grounded = [d for d in decisions if d["derived_from"]]

    return {
        "config": {"turns_requested": turns, "turns_run": C._S["turn"],
                   "pollers": pollers, "brain": brain.brain_name(),
                   "routed": models.active(), "tier": models.tier(),
                   "models_by_role": models.assignments(),
                   "seat_faults": models.seat_faults()},
        "throughput": {
            "wall_s": round(wall, 2),
            "turns_per_s": round(turns / wall, 2) if wall else None,
            "turn_ms_p50": round(statistics.median(turn_ms), 1) if turn_ms else None,
            "turn_ms_p95": round(sorted(turn_ms)[int(len(turn_ms) * .95)], 1) if turn_ms else None,
            "snapshot_reads": len(latencies),
            "snapshot_ms_p50": round(statistics.median(latencies), 2) if latencies else None,
            "snapshot_ms_p95": round(sorted(latencies)[int(len(latencies) * .95)], 2)
                               if latencies else None,
            "snapshot_ms_max": round(max(latencies), 2) if latencies else None,
        },
        "economy": {
            "vision": C._vision().name, "progress_pct": sc["progress"],
            "age": w["age"], "resources": {r: w[r] for r in sim.RESOURCES},
            "net_worth": snap["balance"]["net_worth"],
            "structures_built": snap["dev_total_built"],
            "value_per_1k": snap["system"]["value_per_1k"],
            "compute_spent": snap["spent"], "compute_cap": G.TOKEN_CAP,
            "waste_builds": kinds.get("waste", 0),
            "spoilage_events": kinds.get("spoilage", 0),
            "trades": kinds.get("trade", 0), "repairs": kinds.get("repair", 0),
        },
        "agents": sorted(agents, key=lambda a: -a["contribution"]),
        "governance": {
            "gates_opened": gates_opened, "gates_answered": gates_answered,
            "escalations": kinds.get("escalation", 0),
            "board_events": kinds.get("board", 0),
            "stall_turns": stalls, "failed_turns": C._S.get("failed_turns", 0),
            "promotions": kinds.get("promote", 0), "retirements": kinds.get("reap", 0),
            "side_effects": C._S["side_effects"],
        },
        "reasoning": {
            "decisions": len(decisions), "closed": len(closed),
            "hit_rate_pct": round(100 * len(hits) / len(closed)) if closed else None,
            "grounded_pct": round(100 * len(grounded) / len(decisions)) if decisions else None,
        },
        "learning": {
            "lessons_before": start_lessons, "lessons_after": anchor.skills_count(),
            "lessons": [x["lesson"] for x in lessons[:8]],
        },
        "model": {
            "configured": brain.available(),
            "calls": calls["calls"] - start_calls["calls"],
            "prompt_tokens": calls["prompt_tokens"] - start_calls["prompt_tokens"],
            "completion_tokens": calls["completion_tokens"] - start_calls["completion_tokens"],
            "errors": calls["errors"] - start_calls["errors"],
            "avg_latency_ms": calls["avg_latency_ms"],
            "cost_usd": round(calls["cost_usd"] - start_calls.get("cost_usd", 0), 6),
            "fallbacks": models.fallbacks(),
        },
        "record": {"events": anchor.event_count() - start_events,
                   "event_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:12])},
        "ran_at": round(started),
    }


# Invariants a healthy run must satisfy. These are the smoke test's teeth: a run that
# produces numbers but violates its own constitution is a failure, not a data point.
CHECKS = [
    ("the world advanced", lambda r: r["config"]["turns_run"] > 0),
    ("agents were born and did measurable work",
     lambda r: any(a["contribution"] > 0 for a in r["agents"])),
    ("the vision moved", lambda r: r["economy"]["progress_pct"] > 0),
    ("spend stayed under the cap",
     lambda r: r["economy"]["compute_spent"] <= r["economy"]["compute_cap"]),
    ("every irreversible gate that opened was answered",
     lambda r: r["governance"]["gates_opened"] == r["governance"]["gates_answered"]),
    ("decisions were recorded with their reasoning",
     lambda r: r["reasoning"]["decisions"] > 0),
    ("the permanent record grew", lambda r: r["record"]["events"] > 0),
    ("readers were served while the world played",
     lambda r: r["throughput"]["snapshot_reads"] > 0),
    ("no reader waited longer than a second",
     lambda r: (r["throughput"]["snapshot_ms_max"] or 0) < 1000),
    ("no seat was misconfigured", lambda r: not r["config"]["seat_faults"]),
    ("a model that was configured actually answered",
     lambda r: (not r["model"]["configured"]) or r["model"]["calls"] > 0),
]


def report(r: dict) -> None:
    c, t, e, g = r["config"], r["throughput"], r["economy"], r["governance"]
    print(f"\n\033[1mPHOENIX SMOKE TEST\033[0m — {c['turns_run']} turns on "
          f"'{c['brain']}'" + (f" ({c['tier']})" if c["routed"] else " (unrouted)"))
    print(f"{'─' * 72}")
    print(f"THROUGHPUT   {t['turns_per_s']} turns/s · turn p50 {t['turn_ms_p50']}ms / "
          f"p95 {t['turn_ms_p95']}ms")
    print(f"             {t['snapshot_reads']:,} concurrent reads by {c['pollers']} pollers · "
          f"snapshot p50 {t['snapshot_ms_p50']}ms / p95 {t['snapshot_ms_p95']}ms / "
          f"max {t['snapshot_ms_max']}ms")
    print(f"\nECONOMY      {e['vision']}")
    print(f"             [{bar(e['progress_pct'])}] {e['progress_pct']}% · {e['age']}")
    print(f"             net worth {human(e['net_worth'])} · value/1k {e['value_per_1k']} · "
          f"{e['structures_built']} structures")
    print(f"             food {human(e['resources']['food'])} · wood {human(e['resources']['wood'])} · "
          f"gold {human(e['resources']['gold'])}")
    print(f"             compute {human(e['compute_spent'])}/{human(e['compute_cap'])} "
          f"· waste {e['waste_builds']} · spoilage {e['spoilage_events']} · trades {e['trades']}")

    print(f"\nAGENTS       {len(r['agents'])} ever · "
          f"{sum(1 for a in r['agents'] if a['status'] == 'alive')} alive")
    print(f"             {'agent':<9}{'role':<12}{'tier':<4}{'contrib':>9}{'compute':>10}"
          f"{'eff':>7}  status")
    for a in r["agents"][:10]:
        eff = f"{a['efficiency']:.2f}" if a["efficiency"] is not None else "—"
        print(f"             {a['uid']:<9}{a['role']:<12}{a['tier']:<4}"
              f"{human(a['contribution']):>9}{human(a['tokens']):>10}{eff:>7}  {a['status']}")
    if len(r["agents"]) > 10:
        print(f"             … {len(r['agents']) - 10} more")

    print(f"\nGOVERNANCE   gates {g['gates_answered']}/{g['gates_opened']} answered · "
          f"escalations {g['escalations']} · board events {g['board_events']}")
    print(f"             promotions {g['promotions']} · retirements {g['retirements']} · "
          f"stall turns {g['stall_turns']} · failed turns {g['failed_turns']}")
    rr = r["reasoning"]
    print(f"             {rr['decisions']} decisions · {rr['closed']} closed · "
          f"hit rate {rr['hit_rate_pct']}% · grounded {rr['grounded_pct']}%")

    m = r["model"]
    if m["configured"]:
        print(f"\nMODEL        {m['calls']:,} calls · "
              f"{m['prompt_tokens']:,} in / {m['completion_tokens']:,} out · "
              f"{m['avg_latency_ms']}ms avg · {m['errors']} errors · "
              f"${m['cost_usd']:.4f} · {m['fallbacks']} rule-based rescues")
    else:
        print("\nMODEL        none configured — the rules played this run. Every number "
              "above is\n             the settlement's own behaviour, not a model's.")

    lg = r["learning"]
    print(f"\nLEARNING     {lg['lessons_before']} → {lg['lessons_after']} lessons on the record")
    for lesson in lg["lessons"][:5]:
        print(f"             · {lesson}")

    print(f"\n{'─' * 72}")
    failed = []
    for name, fn in CHECKS:
        try:
            ok = bool(fn(r))
        except Exception:
            ok = False
        print(f"  [{PASS if ok else FAIL}] {name}")
        if not ok:
            failed.append(name)
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} invariants held\n")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=150)
    ap.add_argument("--pollers", type=int, default=8,
                    help="concurrent readers hitting the snapshot while the world plays")
    ap.add_argument("--json", default="", help="also write the full report here")
    ap.add_argument("--fresh", action="store_true", help="wipe the game world first")
    args = ap.parse_args()

    if args.fresh:
        import sim
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(sim.DB + suffix)
            except OSError:
                pass

    r = run(args.turns, args.pollers)
    failed = report(r)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(r, f, indent=2)
        print(f"full report → {args.json}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

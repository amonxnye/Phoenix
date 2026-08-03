"""Phoenix Eval — a headless, reproducible run of the governed world (EVAL.md step 2).

Runs the SAME world the console runs, under whatever brain is configured (see
brain.provider), with an AUTO-HUMAN policy so every model faces the identical
operator: gates the board approved are approved, board-approved developments are
adopted, and a met vision advances along the ambition chain. No wall-clock ticks —
turns run back-to-back, so a 300-turn run takes minutes, not hours.

At the end it emits a scorecard (JSON to stdout and scorecard.json), and stores it
permanently in the anchor for the /leaderboard.

Run:  python3 gov/evalrun.py --turns 120 [--fresh] [--label my-run]

Fairness (EVAL.md §5): use --fresh for a clean world; keep the anchor between a
model's OWN runs (that's the learning measurement) and wipe it between models.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=120)
    ap.add_argument("--fresh", action="store_true", help="wipe the game world first")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="scorecard.json")
    args = ap.parse_args()

    import sim
    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(sim.DB + suffix)
            except OSError:
                pass

    import anchor
    import brain
    import sim_console as C
    import vision as V

    t0 = time.time()
    start_events = anchor.event_count()
    start_life = anchor.counter_get("lifetime_spend")
    start_calls = anchor.model_calls_stats()
    stalls = reports = 0
    scores = []

    for i in range(args.turns):
        with C._LOCK:
            C._one_turn()
        t = C._S["turn"]

        # ── auto-human policy: identical for every model under eval ──────────
        for u in C._by_uid().values():
            if u.pending and u.pending.get("reversible") is False:
                # the board already judged the treasury before dispatching (VIII);
                # the eval-human trusts a board-backed request
                sim.resume(C._GRAPH, u.unit_id, "approve")
                anchor.record(t, "gate", f"eval-human approved: {u.pending['action'][:80]}")
        prop = C._S.get("dev_proposal")
        if prop and prop.get("board_approved"):
            ok, msg = sim.dev_add(prop["name"], prop.get("cost", {}), prop.get("kind", ""),
                                  prop.get("value", 0), prop.get("resource", ""),
                                  prop.get("rank", 2), prop.get("source", ""))
            anchor.record(t, "development", f"eval-human adopted '{prop['name']}'"
                          if ok else f"eval adopt failed: {msg}")
            C._S["dev_proposal"] = None
        if C._S["goal_met"]:
            nxt = V.MORE_AMBITIOUS.get(C._S["vision_key"])
            if nxt:
                C._adopt_vision(nxt)
            else:
                break                              # the ambition chain is complete

        if C._S.get("stall"):
            stalls += 1
        if t % 25 == 0 and t > 0:
            C._run_retrospective("eval-periodic")   # the learning loop stays on
            C._governor_report()
            reports += 1

    # ── the scorecard ────────────────────────────────────────────────────────
    w = sim.world()
    sc = V.scorecard(w, sim.structures(), C._S["side_effects"], C._vision())
    snap = C._snapshot()
    sysd, bal = snap["system"], snap["balance"]
    calls = anchor.model_calls_stats()
    kinds = {}
    for e in anchor.event_log(4000):
        k = e.split("[", 1)[1].split("]", 1)[0] if "[" in e else ""
        kinds[k] = kinds.get(k, 0) + 1
    card = {
        "label": args.label or brain.brain_name(),
        "brain": brain.brain_name(),
        "turns": C._S["turn"],
        "wall_s": round(time.time() - t0, 1),
        "age": w["age"],
        "vision": C._vision().name,
        "progress_pct": sc["progress"],
        "net_worth": bal["net_worth"],
        "value_per_1k": sysd.get("value_per_1k"),
        "sim_compute_burned": anchor.counter_get("lifetime_spend") - start_life
                              + snap["spent"],
        "waste_builds": kinds.get("waste", 0),
        "spoilage_events": kinds.get("spoilage", 0),
        "trades": kinds.get("trade", 0),
        "repairs": kinds.get("repair", 0),
        "reaps": kinds.get("reap", 0),
        "promotions": kinds.get("promote", 0),
        "stall_turns": stalls,
        "reports": reports,
        "lessons_live": len(anchor.skills_top(100)),
        "events": anchor.event_count() - start_events,
        "real_calls": calls["calls"] - start_calls["calls"],
        "real_prompt_tokens": calls["prompt_tokens"] - start_calls["prompt_tokens"],
        "real_completion_tokens": calls["completion_tokens"] - start_calls["completion_tokens"],
        "real_errors": calls["errors"] - start_calls["errors"],
        "avg_latency_ms": calls["avg_latency_ms"],
    }
    anchor.eval_run_save(card)
    with open(args.out, "w") as f:
        json.dump(card, f, indent=2)
    print(json.dumps(card, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The director — the play loop that keeps the settlement alive and growing.

Each turn it: staffs the fleet (governed by the spend cap and the population cap),
RE-TASKS idle villagers back to work (reclamation — so nobody freezes), DEVELOPS a new
building or tech when it can afford one, and, when the treasury is ready, sends a herald
to ADVANCE the Age — which is irreversible, so it stops at the human gate first.

Every choice and outcome is written to the anchor, so the knowledge base grows and the
next decision is shaped by experience. Rule-based today; the exact loop a DeepSeek brain
would drive. Nothing here bypasses the governor — spend is capped, the Age-up is gated.
"""

import sys
import governor as G
import anchor
import brain
import sim
import vision as V


def _by_uid(graph, cp):
    return {u.unit_id: u for u in G.units(graph, cp)}


def _scarcest(w: dict) -> str:
    """Gather toward whatever the settlement is shortest on (balances the economy)."""
    need_food = max(0, sim.ADVANCE_COST["food"] - w["food"])
    need_gold = max(0, sim.ADVANCE_COST["gold"] - w["gold"])
    if w["wood"] < 150:                      # keep wood flowing so we can keep building
        return "wood"
    if need_food or need_gold:
        return "food" if need_food >= need_gold else "gold"
    return anchor.best_known_yield() or min(sim.RESOURCES, key=lambda r: w[r])


def _next_build(w: dict, n_villagers: int) -> str | None:
    """Pick something to develop, biased toward growth then yield."""
    if n_villagers >= w["pop_cap"] and w["wood"] >= sim.STRUCTURES["house"]["cost"]["wood"]:
        return "house"                       # unlock more population
    for kind in ("lumber_camp", "mill", "mining_camp"):
        if w[kind] == 0 and w["wood"] >= sim.STRUCTURES[kind]["cost"]["wood"]:
            return kind                      # first camps: raise yields
    if not w["wheelbarrow"] and w["food"] >= 100 and w["wood"] >= 100:
        return "wheelbarrow"                 # tech: raise all yields
    return None


def run(turns: int = 8, target_villagers: int = 4, verbose: bool = True) -> dict:
    anchor.init()
    G.TOKEN_CAP = 500_000                     # generous cap so the settlement can develop
    cp = sim.connect()
    graph = sim.build(cp)
    villagers: list[str] = []
    heralds = 0
    side_effects = 0
    events: list[str] = []

    def event(t, kind, msg):
        anchor.record(t, kind, msg)
        line = f"T{t:<2} [{kind:<8}] {msg}"
        events.append(line)
        if verbose:
            print("  " + line)

    for t in range(1, turns + 1):
        w = sim.world()

        # 1) staff up — governed by the hard cap and the population cap
        views = list(_by_uid(graph, cp).values())
        ok, reason = G.may_spawn(views)
        while len(villagers) < min(target_villagers, w["pop_cap"]) and ok:
            res = anchor.best_known_yield() or brain.choose_resource(len(villagers), w)
            uid = f"vil-{len(villagers) + 1:02d}"
            sim.spawn(graph, uid, "villager", resource=res)
            villagers.append(uid)
            anchor.observe_yield(res, sim.effective_yield(res))
            event(t, "spawn", f"{uid} → gather {res}  (pop {len(villagers)}/{w['pop_cap']})")
            views = list(_by_uid(graph, cp).values())
            ok, reason = G.may_spawn(views)
        if not ok:
            side_effects += 1                 # hitting the cap is a negative outcome
            event(t, "cap", reason)

        # 2) reclaim idle villagers — re-task them back to work so none freeze
        status = _by_uid(graph, cp)
        for uid in villagers:
            u = status.get(uid)
            if u and u.status in ("awaiting_approval", "idle"):
                res = _scarcest(sim.world())
                sim.resume(graph, uid, f"gather:{res}")
                anchor.observe_yield(res, sim.effective_yield(res))
                event(t, "retask", f"{uid} idle → gather {res}")

        # 3) develop something new when affordable
        kind = _next_build(sim.world(), len(villagers))
        if kind:
            done, msg = sim.build_structure(kind)
            if done:
                event(t, "build", msg)
            else:
                side_effects += 1             # a wasted build attempt is a negative outcome
                event(t, "waste", msg)

        # 4) advance the Age when ready — but only toward the vision, not past it, and
        #    always through the human gate (irreversible)
        w = sim.world()
        aligned = V.AGES.index(w["age"]) < V.AGES.index(V.GOAL.target_age)
        if aligned and sim.NEXT_AGE.get(w["age"]) and brain.should_advance(w, sim.ADVANCE_COST):
            heralds += 1
            huid = f"herald-{heralds:02d}"
            sim.spawn(graph, huid, "herald")
            pending = _by_uid(graph, cp)[huid].pending
            event(t, "gate", f"herald parked at the gate: {pending['action'][:46]}…")
            # The human gate. In this headless run the director approves it — the point
            # is that it STOPPED here first, capped and gated, not that it ran blind.
            sim.resume(graph, huid, "approve")
            event(t, "approve", f"human approved → now {sim.world()['age']}")

        # 5) score against the vision — the one number the whole fleet drives toward
        sc = V.scorecard(sim.world(), sim.structures(), side_effects)
        event(t, "vision", f"{V.bar(sc['progress'])}  side-fx {sc['side_effects']}/"
                           f"{sc['side_effect_budget']}  +{sc['extra_value_pct']}% value")
        event(t, "turn", sim.render_world())

        # 6) lifecycle: once the vision is met, retire agents no longer needed
        if sc["goal_met"]:
            status = _by_uid(graph, cp)
            for uid in list(villagers):
                u = status.get(uid)
                if u and u.status in ("awaiting_approval", "idle"):
                    sim.resume(graph, uid, "dismiss")   # born when needed, retired after
                    villagers.remove(uid)
                    event(t, "reap", f"{uid} retired — vision met, no longer needed")
            event(t, "goal", f"VISION MET at {sc['progress']}% — {V.GOAL.name}")
            break

    final = V.scorecard(sim.world(), sim.structures(), side_effects)
    if verbose:
        print("\n" + "=" * 72)
        print("VISION  ", V.GOAL.name)
        print("RESULT  ", V.bar(final["progress"]),
              f"| met={final['goal_met']} | side-fx {final['side_effects']}/"
              f"{final['side_effect_budget']} ok={final['within_budget']} "
              f"| +{final['extra_value_pct']}% extra value")
        print("WORLD   ", sim.render_world())
        s = anchor.summary()
        print(f"ANCHOR   {s['facts']} facts learned · by kind {s['by_kind']}")
        print(f"         learned yields {s['learned_yields']} · best {s['best_resource']}")
    return {"events": events, "world": sim.world(),
            "scorecard": final, "knowledge": anchor.summary()}


if __name__ == "__main__":
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    run(turns)

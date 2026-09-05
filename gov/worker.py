"""The worker agent — a villager whose resource is code (REALWORK.md §3.2).

One work cycle ("gather turn"): read the task and its failing test, propose a fix
through the ONE brain seam, apply it in the sandbox, re-run the oracle. Measured
contribution = tests newly passing — fed into the SAME economy (credit, promotion,
budget, reap) and the SAME permanent record (careers, lineage) as the game fleet.

The worker can only write inside the sandbox and cannot touch the tests
(workspace.apply_patch enforces both), and the oracle subprocess runs with a
stripped environment. Article V, enforced literally.

Run one cycle by hand:  python3 gov/worker.py --agent dev-01 [--module calculator.py]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import brain
import economy
import workspace as W

CREDIT_PER_TEST = 100        # contribution for each test turned green
MAX_ATTEMPTS = 3             # IX.8: repeated failure on one task is a loop, not effort


def _attempts(task_id) -> int:
    return anchor.counter_get(f"attempts:{task_id}")


def work_cycle(agent: str, module: str = "calculator.py") -> dict:
    """One full cycle for one agent. Returns what happened, measured by the oracle."""
    W.init()
    before = W.oracle()
    tasks = [t for t in W.sync_tasks() if t["status"] in ("open", "assigned")]
    if not tasks:
        # Undetected inaction, coding edition: a worker that does nothing must not
        # do it silently, or a dead loop is indistinguishable from a finished one.
        anchor.record(-1, "idle", f"{agent} ran with no open task — suite is green")
        return {"agent": agent, "did": "nothing", "reason": "no open tasks — suite is green"}

    # IX.8: the recovery must be bounded. Without this the worker re-picks the same
    # unsolvable task forever, burning budget and calling it effort — the coding-domain
    # twin of a watchdog restarting into a full disk.
    open_tasks = [t for t in tasks if _attempts(t["id"]) < MAX_ATTEMPTS]
    if not open_tasks:
        stuck = ", ".join(f"{t['test']} ({_attempts(t['id'])} tries)" for t in tasks)
        anchor.record(-1, "escalation",
                      f"WORKBOARD STUCK — every open task has failed {MAX_ATTEMPTS}x: {stuck}")
        anchor.msg_send("chief", "Chief Governor",
                        f"Work is stalled: {stuck}. Retrying will not help — the task or "
                        f"the model needs to change.")
        return {"agent": agent, "did": "stuck", "reason": stuck, "attempts": MAX_ATTEMPTS}
    task = open_tasks[0]
    W.assign(task["id"], agent)

    src = W.read_file(module)
    tests_src = W.read_file(os.path.join(W.TESTS_DIR, "test_" + module))
    prompt = (
        f"You are {agent}, a software agent. Fix the code so the failing tests pass.\n"
        f"Failing tests: {', '.join(before['failures'])}\n\n"
        f"--- {module} (current) ---\n{src}\n\n"
        f"--- the test suite (read-only, do NOT modify) ---\n{tests_src}\n\n"
        f"Reply with ONLY the complete corrected content of {module}. No prose, no fences."
    )
    try:
        fixed = brain._chat([{"role": "user", "content": prompt}], 2000, 0.2, "worker-patch")
    except Exception as e:
        anchor.record(-1, "error", f"{agent} could not produce a patch: {str(e)[:120]}")
        return {"agent": agent, "did": "error", "reason": str(e)[:200]}
    if fixed.startswith("```"):
        fixed = fixed.strip("`")
        fixed = fixed[fixed.find("\n") + 1:]
    return apply_and_score(agent, module, fixed, task, before)


def apply_and_score(agent: str, module: str, content: str,
                    task: dict, before: dict | None = None) -> dict:
    """Apply a proposed patch and measure it against the oracle. Split out so the
    machinery is testable without any model (verify injects a known-good patch)."""
    before = before or W.oracle()
    old = W.read_file(module)
    ok, msg = W.apply_patch(module, content)
    if not ok:
        anchor.counter_add(f"attempts:{task['id']}", 1)
        anchor.record(-1, "waste", f"{agent} patch refused: {msg}")
        return {"agent": agent, "did": "refused", "reason": msg}
    after = W.oracle()
    newly = sorted(set(before["failures"]) - set(after["failures"]))
    broke = sorted(set(after["failures"]) - set(before["failures"]))
    if broke or after["passed"] < before["passed"]:
        W.apply_patch(module, old)                 # a patch that breaks tests reverts
        anchor.counter_add(f"attempts:{task['id']}", 1)
        anchor.record(-1, "waste", f"{agent} patch reverted — broke {', '.join(broke)}")
        anchor.career_add(agent, -1, "retask", f"patch reverted: broke {', '.join(broke)}")
        return {"agent": agent, "did": "reverted", "broke": broke}

    # Article I.2 in the coding domain: a patch that applies cleanly, breaks nothing,
    # and turns NOTHING green is not work. It was previously recorded as `[work]` with
    # a contribution of zero — motion filed as productivity, which is exactly the
    # failure the settlement spent 12,000 turns committing. Effort is not progress.
    if not newly:
        anchor.counter_add(f"attempts:{task['id']}", 1)
        anchor.record(-1, "waste",
                      f"{agent} patch changed {module} but moved no test — suite still "
                      f"{after['passed']}/{after['total']} (attempt "
                      f"{_attempts(task['id'])} of {MAX_ATTEMPTS})")
        anchor.career_add(agent, -1, "retask",
                          f"patched {module} with no measured effect — no contribution")
        return {"agent": agent, "did": "no-op", "suite": f"{after['passed']}/{after['total']}",
                "attempts": _attempts(task["id"]), "contribution": 0}

    anchor.config_set(f"attempts:{task['id']}", "0")  # progress clears the budget
    economy.enlist(agent)
    got = CREDIT_PER_TEST * len(newly)
    if got:
        economy.credit(agent, got)
    for name in newly:
        W.mark_solved(name, agent)
    did = anchor.reason_add(-1, agent, f"patch {module}",
                            f"targeted {task['test']}; oracle verdict: {len(newly)} newly "
                            f"passing ({', '.join(newly) or 'none'})",
                            derived_from=[f"task:{task['id']}"], authorized_by="policy")
    ev = anchor.record(-1, "work", f"{agent} patched {module} — {len(newly)} tests newly "
                                   f"green ({', '.join(newly) or 'none'}), suite "
                                   f"{after['passed']}/{after['total']}")
    anchor.decision_close(did, ev, outcome=f"+{got} contribution; suite "
                                           f"{before['passed']}→{after['passed']}")
    anchor.career_add(agent, -1, "work",
                      f"patched {module}: {len(newly)} tests green (+{got} contribution)")
    promo = economy.evaluate(agent)
    if promo:
        anchor.record(-1, "promote", f"{agent} promoted -> {promo}")
        anchor.career_add(agent, -1, "promote", f"earned promotion to {promo} by shipped work")
    return {"agent": agent, "did": "patched", "newly_passing": newly,
            "contribution": got, "suite": f"{after['passed']}/{after['total']}",
            "promoted": promo}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="dev-01")
    ap.add_argument("--module", default="calculator.py")
    a = ap.parse_args()
    anchor.init()
    economy.init()
    if not brain.available():
        print("no brain configured (BRAIN_API_KEY) — the worker needs a model")
        sys.exit(1)
    import json
    print(json.dumps(work_cycle(a.agent, a.module), indent=2))

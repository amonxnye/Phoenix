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


def work_cycle(agent: str, module: str = "calculator.py") -> dict:
    """One full cycle for one agent. Returns what happened, measured by the oracle."""
    W.init()
    before = W.oracle()
    open_tasks = [t for t in W.sync_tasks() if t["status"] in ("open", "assigned")]
    if not open_tasks:
        return {"agent": agent, "did": "nothing", "reason": "no open tasks — suite is green"}
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
        anchor.record(-1, "waste", f"{agent} patch refused: {msg}")
        return {"agent": agent, "did": "refused", "reason": msg}
    after = W.oracle()
    newly = sorted(set(before["failures"]) - set(after["failures"]))
    broke = sorted(set(after["failures"]) - set(before["failures"]))
    if broke or after["passed"] < before["passed"]:
        W.apply_patch(module, old)                 # a patch that breaks tests reverts
        anchor.record(-1, "waste", f"{agent} patch reverted — broke {', '.join(broke)}")
        anchor.career_add(agent, -1, "retask", f"patch reverted: broke {', '.join(broke)}")
        return {"agent": agent, "did": "reverted", "broke": broke}

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
        print("no brain configured (BRAIN_* / DEEPSEEK_API_KEY) — the worker needs a model")
        sys.exit(1)
    import json
    print(json.dumps(work_cycle(a.agent, a.module), indent=2))

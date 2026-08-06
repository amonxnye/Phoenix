"""The worker agent — a villager whose resource is code (REALWORK.md §3.2).

One work cycle ("gather turn"): read the task and the runner's own failure output,
propose a fix through the ONE brain seam, apply it in the repo, re-run the oracle.
Measured contribution = tests newly passing — fed into the SAME economy (credit,
promotion, budget, reap) and the SAME permanent record (careers, lineage) as the
game fleet.

The worker can only write inside the configured repo and cannot touch the tests or
the CI that runs them (workspace.apply_patch enforces both), and the oracle
subprocess runs with a stripped environment. Article V, enforced literally.

Which file it edits is derived from the failing test, not hardcoded, so the same
loop runs on a toy module or a real repository with many of them (BUILDER.md §5.1).

Run one cycle by hand:
    python3 gov/worker.py --agent dev-01
    python3 gov/worker.py --agent dev-01 --repo /path/to/repo --test-cmd "python -m pytest -q"
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
MAX_SOURCE = 20000           # how much of a file a worker is shown / asked to rewrite


def work_cycle(agent: str, module: str = "") -> dict:
    """One full cycle for one agent. Returns what happened, measured by the oracle.

    ``module`` is optional: left empty, the target is derived from the failing test.
    """
    W.init()
    before = W.oracle()
    if not before["ok"]:
        anchor.record(-1, "error", f"oracle has no verdict: {before['error']}")
        return {"agent": agent, "did": "blocked", "reason": before["error"]}
    open_tasks = [t for t in W.sync_tasks() if t["status"] in ("open", "assigned")]
    if not open_tasks:
        return {"agent": agent, "did": "nothing", "reason": "no open tasks — suite is green"}
    task = open_tasks[0]
    W.assign(task["id"], agent)

    target = module or W.find_source_for(task["test"])
    if not target:
        reason = (f"no source file could be derived from {task['test']} — "
                  f"name one with --module")
        anchor.record(-1, "error", f"{agent} could not locate the code for {task['test']}")
        return {"agent": agent, "did": "blocked", "reason": reason}

    try:
        src = W.read_file(target)
    except OSError:
        src = ""                                   # the fix is to create the file
    test_file = W.find_test_file(task["test"])
    try:
        tests_src = W.read_file(test_file) if test_file else ""
    except OSError:
        tests_src = ""

    cfg = W.config()
    prompt = (
        f"You are {agent}, a software agent working in the repository "
        f"'{cfg['repo_name']}'. Fix the code so the failing tests pass.\n"
        f"The oracle is `{cfg['test_cmd_str']}` — it, not you, decides if you succeeded.\n\n"
        f"TASK: {task['title']}  (failing test: {task['test']})\n"
        f"Other failing tests: {', '.join(t for t in before['failures'][:20] if t != task['test']) or 'none'}\n\n"
        f"--- what the test runner reported ---\n{W.failure_report(task['test'], before)}\n\n"
        f"--- {target} (current content) ---\n{src[:MAX_SOURCE] or '(this file does not exist yet)'}\n\n"
        f"--- {test_file or 'the test suite'} (read-only, you may NOT modify it) ---\n"
        f"{tests_src[:MAX_SOURCE]}\n\n"
        f"Reply with ONLY the complete corrected content of {target}. No prose, no fences."
    )
    try:
        fixed = brain._chat([{"role": "user", "content": prompt}], 4000, 0.2, "worker-patch")
    except Exception as e:
        anchor.record(-1, "error", f"{agent} could not produce a patch: {str(e)[:120]}")
        return {"agent": agent, "did": "error", "reason": str(e)[:200]}
    return apply_and_score(agent, target, _unfence(fixed), task, before)


def _unfence(text: str) -> str:
    """Models fence code even when told not to. Strip one wrapping fence, nothing else."""
    t = text.strip()
    if not t.startswith("```"):
        return text
    t = t[3:]
    nl = t.find("\n")
    t = t[nl + 1:] if nl != -1 else t
    end = t.rfind("```")
    return t[:end] if end != -1 else t


def apply_and_score(agent: str, module: str, content: str,
                    task: dict, before: dict | None = None) -> dict:
    """Apply a proposed patch and measure it against the oracle. Split out so the
    machinery is testable without any model (verify injects a known-good patch)."""
    before = before or W.oracle()
    if not before["ok"]:
        return {"agent": agent, "did": "blocked", "reason": before["error"]}
    try:
        old = W.read_file(module)
    except OSError:
        old = None                                 # the file is new; the undo is removal
    ok, msg = W.apply_patch(module, content)
    if not ok:
        anchor.record(-1, "waste", f"{agent} patch refused: {msg}")
        return {"agent": agent, "did": "refused", "reason": msg}

    after = W.oracle()
    if not after["ok"]:
        # No verdict is not a pass. A patch that stops the suite from running at all
        # would otherwise show zero failures and be paid for deleting the oracle.
        W.restore(module, old)
        anchor.record(-1, "waste",
                      f"{agent} patch reverted — the suite stopped running: {after['error']}")
        anchor.career_add(agent, -1, "retask",
                          f"patch reverted: suite unrunnable ({after['error'][:80]})")
        return {"agent": agent, "did": "reverted", "reason": after["error"], "broke": []}

    newly = sorted(set(before["failures"]) - set(after["failures"]))
    broke = sorted(set(after["failures"]) - set(before["failures"]))
    if broke or after["passed"] < before["passed"]:
        W.restore(module, old)                     # a patch that breaks tests reverts
        anchor.record(-1, "waste", f"{agent} patch reverted — broke {', '.join(broke)}")
        anchor.career_add(agent, -1, "retask", f"patch reverted: broke {', '.join(broke)}")
        return {"agent": agent, "did": "reverted", "broke": broke}

    economy.ensure(agent)          # never `enlist` here: it would wipe prior contribution
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
    return {"agent": agent, "did": "patched", "module": module, "newly_passing": newly,
            "contribution": got, "suite": f"{after['passed']}/{after['total']}",
            "promoted": promo}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="dev-01")
    ap.add_argument("--module", default="", help="target file; derived from the test if omitted")
    ap.add_argument("--repo", default="", help="repository root (default: the toy sandbox)")
    ap.add_argument("--test-cmd", default="", help="the oracle, e.g. 'python -m pytest -q'")
    a = ap.parse_args()
    W.configure(repo=a.repo, test_cmd=a.test_cmd)
    anchor.init()
    economy.init()
    if not brain.available():
        print("no brain configured (BRAIN_* / DEEPSEEK_API_KEY) — the worker needs a model")
        sys.exit(1)
    import json
    print(json.dumps(work_cycle(a.agent, a.module), indent=2))

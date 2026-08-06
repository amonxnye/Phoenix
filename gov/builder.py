"""The Builder — a governed organization that develops software unattended.

This is BUILDER.md steps 2 and 3 made real. One command takes a repository, works
its backlog without supervision, and stops exactly where the constitution says it
must:

    python3 gov/builder.py run --repo /path/to/repo --test-cmd "python -m pytest -q"

What "autonomous" means here, precisely:

- **Isolated.** Every task gets its own git worktree and branch (worktree.py). Your
  checkout is never touched, not even while agents are running, and two agents
  cannot collide.
- **Iterative.** An agent gets several attempts at a task, and each retry is fed
  what the previous one actually did to the test suite. One-shot patching is why
  the first version could only fix easy things.
- **Measured, never claimed.** An attempt is kept only if the oracle says the suite
  improved and nothing regressed. Anything else is reverted inside the worktree.
- **Bounded.** Attempts are capped, budgets are debited per attempt, and an agent
  that burns its budget without moving the oracle is reaped (Article II).
- **Gated.** The one irreversible act — merging — never happens autonomously. Each
  solved task parks a dossier (branch, diff, test delta, risk, lineage) at the gate
  for a human. Nothing is pushed anywhere unless the operator explicitly allows it.
- **Loud when stuck.** A run that ends with no progress names its binding constraint
  instead of exiting quietly (Article IX).

Review and land the work:

    python3 gov/builder.py gate                 # what is waiting, and why
    python3 gov/builder.py show --id 3          # the full diff and test delta
    python3 gov/builder.py approve --id 3       # merge into the base branch
    python3 gov/builder.py reject --id 3 --reason "..."
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import economy
import solver as S
import workspace as W
import worktree as WT

CREDIT_PER_TEST = 100        # contribution for each test turned green
COST_PER_ATTEMPT = 5_000     # budget debited per attempt when the model cost is unknown
DEFAULT_ATTEMPTS = 3

# Paths whose blast radius reaches past the test suite. A dossier touching one of
# these is HIGH risk: a human decides it, and tacit consent (Article IV.7) never
# applies — no amount of silence approves a migration.
RISK_PATTERNS = (
    (r"(^|/)(auth|login|session|password|crypto|secret|token)", "authentication/secrets"),
    (r"(^|/)(migrations?|alembic|schema)(/|\.)", "data migration"),
    (r"(^|/)(deploy|infra|terraform|helm|k8s|ansible)(/|\.)", "infrastructure"),
    (r"(^|/)(Dockerfile|docker-compose|Procfile)", "runtime/deployment"),
    (r"(^|/)\.github/", "CI configuration"),
    (r"(^|/)(payment|billing|charge|invoice)", "money"),
)


def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return HERE


DB = os.path.join(_data_dir(), "builder.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS gate("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT, branch TEXT, "
                  "base_branch TEXT, base_sha TEXT, agent TEXT, task TEXT, title TEXT, "
                  "files TEXT, diff TEXT, delta TEXT, risk TEXT, risk_why TEXT, "
                  "attempts INT, decision_id INT, status TEXT DEFAULT 'pending', "
                  "decided_by TEXT DEFAULT '', outcome TEXT DEFAULT '', created REAL)")
        c.commit()
    finally:
        c.close()


# ── risk ──────────────────────────────────────────────────────────────────────

def classify_risk(files: list[str]) -> tuple[str, str]:
    """Which of the blast-radius classes this diff touches. Cheap, conservative, and
    deliberately not a model call — the risk gate must not depend on a judgement an
    agent could talk its way past."""
    hits = []
    for path in files:
        for pattern, label in RISK_PATTERNS:
            if re.search(pattern, path, re.I) and label not in hits:
                hits.append(label)
    return ("high" if hits else "low"), ", ".join(hits)


def park(*, repo: str, branch: str, base_branch: str, base_sha: str, agent: str,
         task: str, title: str, files: list, diff: str, delta: dict,
         attempts: int, decision_id: int) -> int:
    """Put finished, oracle-proven work in front of a human and stop. The single
    write path to the gate — a task solved by one agent and a campaign fought by a
    fleet arrive here identically, because what a reviewer needs is the same."""
    init()
    risk, risk_why = classify_risk(files)
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO gate(repo, branch, base_branch, base_sha, agent, task, title, "
            "files, diff, delta, risk, risk_why, attempts, decision_id, status, created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)",
            (repo, branch, base_branch, base_sha, agent, task, title,
             json.dumps(files), diff, json.dumps(delta), risk, risk_why,
             attempts, decision_id, time.time()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


# ── one task, start to finish ─────────────────────────────────────────────────

def solve_task(agent: str, task: dict, repo: str, *, solve=None,
               max_attempts: int = DEFAULT_ATTEMPTS, log=print) -> dict:
    """Work one task to a merge-ready branch, or give up and say why.

    Runs entirely inside a fresh worktree. The caller's checkout is never written to,
    so a failure here costs a discarded branch and nothing else."""
    solve = solve or S.native_solver
    cfg = W.config()
    repo_cfg = dict(cfg)
    wt = WT.create(repo, agent, task["test"].split("::")[-1])
    log(f"  {agent}: worktree {wt['branch']}")
    # the agent's world is the worktree; the backlog stays keyed to the real repo
    W.configure(repo=wt["path"], test_cmd=repo_cfg["test_cmd_str"],
                timeout=repo_cfg["timeout"], protected=repo_cfg["protected"],
                key=repo)
    history, attempts_used = [], 0
    try:
        before = W.oracle()
        if not before["ok"]:
            return _abandon(agent, task, wt, repo, "the oracle has no verdict in the "
                            f"worktree: {before['error']}", history, 0)
        if task["test"] not in before["failures"]:
            return _abandon(agent, task, wt, repo,
                            "the target test already passes at this commit",
                            history, 0)

        target = W.find_source_for(task["test"])
        test_file = W.find_test_file(task["test"])
        last_outcome = ""
        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            if economy.budget_left(agent) <= 0:
                history.append("budget exhausted")
                break
            req = _request(agent, task, repo_cfg, before, target, test_file,
                           attempt, last_outcome)
            try:
                result = solve(req)
            except Exception as e:                 # a dead executor is a failed turn
                last_outcome = f"the executor failed: {str(e)[:200]}"
                history.append(f"attempt {attempt}: executor error")
                economy.charge(agent, COST_PER_ATTEMPT)
                log(f"  {agent}: attempt {attempt} — executor error: {str(e)[:80]}")
                continue
            economy.charge(agent, int(result.get("tokens") or COST_PER_ATTEMPT))
            kept, last_outcome, after = try_patch(agent, result.get("files") or {}, before)
            history.append(f"attempt {attempt}: {last_outcome}")
            log(f"  {agent}: attempt {attempt} — {last_outcome}")
            if kept and task["test"] not in after["failures"]:
                return _park_at_gate(agent, task, wt, repo, before, after,
                                     history, attempt)
            if kept:
                before = after                     # partial progress is a new baseline
        return _abandon(agent, task, wt, repo,
                        f"{attempts_used} attempts produced no green for {task['test']}",
                        history, attempts_used)
    finally:
        W.configure(repo=repo_cfg["repo"], test_cmd=repo_cfg["test_cmd_str"],
                    timeout=repo_cfg["timeout"], protected=repo_cfg["protected"],
                    key=repo_cfg["key"])


def _request(agent, task, repo_cfg, before, target, test_file, attempt, last_outcome):
    files = {}
    if target:
        try:
            files[target] = W.read_file(target)
        except OSError:
            files[target] = ""
    test_sources = {}
    if test_file:
        try:
            test_sources[test_file] = W.read_file(test_file)
        except OSError:
            pass
    return {"agent": agent, "repo_name": repo_cfg["repo_name"],
            "test_cmd": repo_cfg["test_cmd_str"], "task_title": task["title"],
            "task_test": task["test"], "failures": before["failures"],
            "failure_report": W.failure_report(task["test"], before),
            "files": files, "test_sources": test_sources,
            "writable": W.list_sources(80), "attempt": attempt,
            "last_outcome": last_outcome}


def try_patch(agent: str, files: dict, before: dict) -> tuple[bool, str, dict]:
    """Apply a whole multi-file patch, or none of it, and let the oracle judge.

    All-or-nothing matters: half a patch is a state neither the agent nor the tests
    ever reasoned about."""
    if not files:
        return False, "the executor returned no files", before
    for path in files:
        why = W.protected_reason(path)
        if why:
            return False, f"refused — {why.split('—')[0].strip()}", before
    old = {}
    for path in files:
        try:
            old[path] = W.read_file(path)
        except OSError:
            old[path] = None                       # a new file; the undo is removal
    for path, content in files.items():
        W.apply_patch(path, content)
    after = W.oracle()
    changed = ", ".join(sorted(files))
    if not after["ok"]:
        rollback(old)
        return False, f"reverted — the suite stopped running ({after['error'][:80]})", before
    broke = sorted(set(after["failures"]) - set(before["failures"]))
    if broke:
        rollback(old)
        return False, f"reverted — broke {', '.join(broke[:3])}", before
    newly = sorted(set(before["failures"]) - set(after["failures"]))
    if not newly:
        rollback(old)
        return False, f"reverted — {changed} changed nothing the oracle can see", before
    return True, (f"kept {changed} — {len(newly)} newly green, suite "
                  f"{after['passed']}/{after['total']}"), after


def rollback(old: dict) -> None:
    for path, content in old.items():
        W.restore(path, content)


def _park_at_gate(agent, task, wt, repo, before, after, history, attempts) -> dict:
    """The work is done and proven. It stops here — a human merges it (Article IV)."""
    newly = sorted(set(before["failures"]) - set(after["failures"]))
    files = WT.changed_files(wt)
    risk, risk_why = classify_risk(files)
    message = (f"{task['title']}\n\n"
               f"Turned green: {', '.join(newly)}\n"
               f"Suite {before['passed']}/{before['total']} -> "
               f"{after['passed']}/{after['total']}\n"
               f"Agent: {agent} ({attempts} attempt(s)); oracle: {W.config()['test_cmd_str']}\n")
    WT.commit_all(wt, message)
    the_diff = WT.diff(wt)

    economy.ensure(agent)
    got = CREDIT_PER_TEST * len(newly)
    economy.credit(agent, got)
    for name in newly:
        W.mark_solved(name, agent)
    did = anchor.reason_add(
        -1, agent, f"branch {wt['branch']}",
        f"targeted {task['test']}; oracle verdict: {len(newly)} newly passing "
        f"({', '.join(newly)}); {attempts} attempt(s); risk {risk}",
        derived_from=[f"task:{task['id']}", f"commit:{wt['base_sha'][:12]}"],
        authorized_by="policy")
    ev = anchor.record(-1, "work", f"{agent} solved {task['test']} on {wt['branch']} — "
                                   f"suite {before['passed']}→{after['passed']}, "
                                   f"awaiting human merge")
    anchor.decision_close(did, ev, outcome=f"+{got} contribution; parked at the gate")
    anchor.career_add(agent, -1, "work",
                      f"{wt['branch']}: {len(newly)} tests green (+{got} contribution)")
    promo = economy.evaluate(agent)
    if promo:
        anchor.record(-1, "promote", f"{agent} promoted -> {promo}")
        anchor.career_add(agent, -1, "promote", "earned promotion by shipped work")

    gate_id = park(repo=repo, branch=wt["branch"], base_branch=wt["base_branch"],
                   base_sha=wt["base_sha"], agent=agent, task=task["test"],
                   title=task["title"], files=files, diff=the_diff,
                   delta={"before": before["passed"], "after": after["passed"],
                          "total": after["total"], "newly": newly},
                   attempts=attempts, decision_id=did)
    WT.remove(repo, wt["path"])                    # the branch survives; the checkout goes
    return {"agent": agent, "task": task["test"], "did": "parked", "gate_id": gate_id,
            "branch": wt["branch"], "newly_passing": newly, "risk": risk,
            "contribution": got, "attempts": attempts,
            "suite": f"{after['passed']}/{after['total']}", "promoted": promo,
            "history": history}


def _abandon(agent, task, wt, repo, reason, history, attempts) -> dict:
    """Give up honestly: the branch is destroyed, the reason is recorded, and the
    task goes back on the board. Nothing half-finished is left lying around."""
    anchor.record(-1, "waste", f"{agent} abandoned {task['test']}: {reason}")
    if attempts:
        anchor.career_add(agent, -1, "retask", f"{task['test']}: {reason[:120]}")
    WT.remove(repo, wt["path"], branch=wt["branch"])
    left = economy.budget_left(agent)
    reaped = False
    if attempts and left <= 0:
        economy.retire(agent)                      # Article II: budget spent, no result
        anchor.record(-1, "reap", f"{agent} retired — budget spent without moving the oracle")
        anchor.career_add(agent, -1, "reap", "budget exhausted without a green test")
        reaped = True
    return {"agent": agent, "task": task["test"], "did": "abandoned", "reason": reason,
            "attempts": attempts, "history": history, "reaped": reaped}


# ── the run ───────────────────────────────────────────────────────────────────

def run(repo: str = "", test_cmd: str = "", agents: int = 1,
        max_attempts: int = DEFAULT_ATTEMPTS, limit: int = 0,
        solve=None, log=print) -> dict:
    """Work the backlog unattended. Returns a report; parks every success at the gate."""
    anchor.init()
    economy.init()
    init()
    cfg = W.configure(repo=repo, test_cmd=test_cmd)
    repo = cfg["repo"]
    W.init()
    if not WT.is_git(repo):
        return {"ok": False, "error": f"{repo} is not a git repository — the Builder "
                                      f"needs one, because the branch IS the undo"}
    if not WT.has_commits(repo):
        return {"ok": False, "error": f"{repo} has no commits to branch from"}

    baseline = W.oracle()
    if not baseline["ok"]:
        anchor.record(-1, "stall", f"builder cannot start: {baseline['error']}")
        return {"ok": False, "error": baseline["error"],
                "binding_constraint": "the test command does not produce results"}
    log(f"repo {cfg['repo_name']} · oracle `{cfg['test_cmd_str']}` · "
        f"suite {baseline['passed']}/{baseline['total']}")

    open_tasks = [t for t in W.sync_tasks() if t["status"] in ("open", "assigned")]
    if limit:
        open_tasks = open_tasks[:limit]
    if not open_tasks:
        log("nothing to do — the suite is green")
        return {"ok": True, "results": [], "parked": 0, "tasks": 0,
                "suite": f"{baseline['passed']}/{baseline['total']}"}

    fleet = [f"dev-{i + 1:02d}" for i in range(max(1, agents))]
    for a in fleet:
        economy.ensure(a)
    log(f"{len(open_tasks)} open task(s), fleet {', '.join(fleet)}")

    results = []
    covered = {}                 # test -> branch that already fixes it, pending merge
    for i, task in enumerate(open_tasks):
        if task["test"] in covered:
            # One patch often turns several tests green. Re-solving them would branch
            # from the same base and produce a redundant diff for the human to read.
            log(f"task {task['id']}: already fixed by {covered[task['test']]} — skipped")
            results.append({"task": task["test"], "did": "covered",
                            "branch": covered[task["test"]]})
            continue
        agent = next((a for a in fleet[i % len(fleet):] + fleet
                      if economy.budget_left(a) > 0), "")
        if not agent:
            log("every agent is out of budget — stopping")
            anchor.record(-1, "stall", "builder stopped: the whole fleet is out of budget")
            break
        W.assign(task["id"], agent)
        log(f"task {task['id']}: {task['title']}  ->  {agent}")
        try:
            r = solve_task(agent, task, repo, solve=solve,
                           max_attempts=max_attempts, log=log)
            results.append(r)
            for test in r.get("newly_passing", []):
                covered.setdefault(test, r.get("branch", ""))
        except WT.GitError as e:
            log(f"  git refused: {e}")
            results.append({"agent": agent, "task": task["test"], "did": "error",
                            "reason": str(e)})

    parked = [r for r in results if r.get("did") == "parked"]
    final = W.oracle()
    report = {"ok": True, "tasks": len(open_tasks), "parked": len(parked),
              "results": results, "repo": repo,
              "suite_at_base": f"{baseline['passed']}/{baseline['total']}",
              "suite_now": f"{final['passed']}/{final['total']}",
              "gate": [r["gate_id"] for r in parked]}
    if not parked and results:
        # Article IX: a run that achieved nothing must say why, not exit quietly.
        constraint = results[0].get("reason", "no attempt satisfied the oracle")
        report["binding_constraint"] = constraint
        anchor.record(-1, "stall", f"builder run produced no mergeable work: {constraint}")
        log(f"\nSTALLED — {constraint}")
    if parked:
        log(f"\n{len(parked)} branch(es) waiting at the gate: "
            + ", ".join(f"#{r['gate_id']} {r['branch']} ({r['risk']} risk)" for r in parked))
        log("review with:  python3 gov/builder.py gate")
    return report


# ── the gate ──────────────────────────────────────────────────────────────────

def gate_pending(repo: str = "") -> list[dict]:
    init()
    c = _conn()
    try:
        q = ("SELECT id, repo, branch, base_branch, agent, task, title, files, delta, "
             "risk, risk_why, attempts, created FROM gate WHERE status='pending'")
        args = []
        if repo:
            q += " AND repo=?"
            args.append(os.path.realpath(repo))
        return [{"id": i, "repo": r, "branch": b, "base_branch": bb, "agent": a,
                 "task": t, "title": ti, "files": json.loads(f), "delta": json.loads(d),
                 "risk": rk, "risk_why": rw, "attempts": at, "created": cr}
                for i, r, b, bb, a, t, ti, f, d, rk, rw, at, cr
                in c.execute(q + " ORDER BY id", args)]
    finally:
        c.close()


def gate_get(gate_id: int) -> dict | None:
    init()
    c = _conn()
    try:
        row = c.execute(
            "SELECT id, repo, branch, base_branch, base_sha, agent, task, title, files, "
            "diff, delta, risk, risk_why, attempts, decision_id, status, decided_by, "
            "outcome FROM gate WHERE id=?", (gate_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    keys = ("id", "repo", "branch", "base_branch", "base_sha", "agent", "task", "title",
            "files", "diff", "delta", "risk", "risk_why", "attempts", "decision_id",
            "status", "decided_by", "outcome")
    d = dict(zip(keys, row))
    d["files"] = json.loads(d["files"])
    d["delta"] = json.loads(d["delta"])
    return d


def approve(gate_id: int, by: str = "human") -> tuple[bool, str]:
    """The irreversible act, taken by a person. Merges the agent's branch into the
    base. Pushes only if the operator set BUILDER_ALLOW_PUSH — an approval to merge
    is not an approval to publish."""
    d = gate_get(gate_id)
    if not d:
        return False, f"no gate entry #{gate_id}"
    if d["status"] != "pending":
        return False, f"#{gate_id} is already {d['status']}"
    ok, msg = WT.merge(d["repo"], d["branch"], d["base_branch"],
                       f"{d['title']}\n\nMerged from {d['branch']} "
                       f"(agent {d['agent']}, approved by {by}).")
    if not ok:
        return False, msg
    pushed = ""
    if os.environ.get("BUILDER_ALLOW_PUSH", "").strip() in ("1", "true", "yes"):
        pok, pmsg = WT.push(d["repo"], d["base_branch"])
        pushed = f"; pushed" if pok else f"; push failed: {pmsg[:100]}"
    _decide(gate_id, "merged", by, f"merged into {d['base_branch']} at {msg[:12]}{pushed}")
    ev = anchor.record(-1, "gate", f"{by} merged {d['branch']} into {d['base_branch']} "
                                   f"— {len(d['delta'].get('newly', []))} tests green")
    if d["decision_id"]:
        anchor.decision_close(d["decision_id"], ev,
                              outcome=f"merged by {by} into {d['base_branch']}")
    anchor.career_add(d["agent"], -1, "merge", f"{d['branch']} merged by {by}")
    return True, f"merged {d['branch']} into {d['base_branch']}{pushed}"


def reject(gate_id: int, by: str = "human", reason: str = "") -> tuple[bool, str]:
    """Refusing costs nothing and destroys nothing of yours — the branch is deleted,
    the base never moved, and the task goes back on the board."""
    d = gate_get(gate_id)
    if not d:
        return False, f"no gate entry #{gate_id}"
    if d["status"] != "pending":
        return False, f"#{gate_id} is already {d['status']}"
    WT._git(d["repo"], "branch", "-D", d["branch"], check=False)
    _decide(gate_id, "rejected", by, reason or "rejected")
    ev = anchor.record(-1, "gate", f"{by} rejected {d['branch']}: {reason or 'no reason given'}")
    if d["decision_id"]:
        anchor.decision_close(d["decision_id"], ev, outcome=f"rejected by {by}")
    anchor.career_add(d["agent"], -1, "retask", f"{d['branch']} rejected: {reason[:100]}")
    if reason:
        anchor.skill_add(-1, f"rejected at the gate: {reason[:180]}", source=d["branch"],
                         trigger=d["task"])
    return True, f"rejected {d['branch']} (branch deleted; base untouched)"


def _decide(gate_id: int, status: str, by: str, outcome: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE gate SET status=?, decided_by=?, outcome=? WHERE id=?",
                  (status, by, outcome[:300], gate_id))
        c.commit()
    finally:
        c.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_gate(rows: list[dict]) -> None:
    if not rows:
        print("nothing waiting at the gate")
        return
    print(f"{len(rows)} branch(es) awaiting a human:\n")
    for d in rows:
        delta = d["delta"]
        flag = "HIGH RISK — " + d["risk_why"] if d["risk"] == "high" else "low risk"
        print(f"  #{d['id']}  {d['branch']}")
        print(f"      {d['title']}  ({d['agent']}, {d['attempts']} attempt(s))")
        print(f"      suite {delta['before']}/{delta['total']} -> "
              f"{delta['after']}/{delta['total']}; green: {', '.join(delta['newly'])}")
        print(f"      files: {', '.join(d['files'])}")
        print(f"      {flag}\n")
    print("approve with:  python3 gov/builder.py approve --id <n>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phoenix Builder — governed autonomous "
                                             "software development")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="work the backlog unattended")
    r.add_argument("--repo", default="", help="repository root (default: WORKSPACE_REPO)")
    r.add_argument("--test-cmd", default="", help="the oracle, e.g. 'python -m pytest -q'")
    r.add_argument("--agents", type=int, default=1)
    r.add_argument("--max-attempts", type=int, default=DEFAULT_ATTEMPTS)
    r.add_argument("--limit", type=int, default=0, help="max tasks this run")
    r.add_argument("--json", action="store_true")

    g = sub.add_parser("gate", help="list branches awaiting a human")
    g.add_argument("--repo", default="")

    s = sub.add_parser("show", help="the full diff behind a gate entry")
    s.add_argument("--id", type=int, required=True)

    a = sub.add_parser("approve", help="merge an approved branch into its base")
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--by", default="human")

    x = sub.add_parser("reject", help="delete the branch; the base never moves")
    x.add_argument("--id", type=int, required=True)
    x.add_argument("--by", default="human")
    x.add_argument("--reason", default="")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        if not S.available():
            print("no brain configured (BRAIN_* / DEEPSEEK_API_KEY) — the Builder "
                  "needs a model to write patches")
            return 1
        report = run(repo=args.repo, test_cmd=args.test_cmd, agents=args.agents,
                     max_attempts=args.max_attempts, limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    if args.cmd == "gate":
        _print_gate(gate_pending(args.repo))
        return 0

    if args.cmd == "show":
        d = gate_get(args.id)
        if not d:
            print(f"no gate entry #{args.id}")
            return 1
        print(f"#{d['id']}  {d['branch']}  ({d['status']})")
        print(f"{d['title']}\nagent {d['agent']} · risk {d['risk']}"
              + (f" ({d['risk_why']})" if d["risk_why"] else ""))
        print(f"suite {d['delta']['before']}/{d['delta']['total']} -> "
              f"{d['delta']['after']}/{d['delta']['total']}\n")
        print(d["diff"])
        return 0

    if args.cmd == "approve":
        ok, msg = approve(args.id, args.by)
        print(msg)
        return 0 if ok else 1

    if args.cmd == "reject":
        ok, msg = reject(args.id, args.by, args.reason)
        print(msg)
        return 0 if ok else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""The campaign — a fleet that works one problem until it is solved.

`builder.py` sends one agent at one task and gives up after a few tries. That is a
worker, not an army. A campaign is the organization applied to a problem that does
not fall over on the first push:

    python3 gov/campaign.py run --repo /path/to/repo --agents 4 --rounds 6

Four ideas, and they are the whole design.

**1. A champion, so ground is never lost.** The campaign keeps the best proven state
so far as a commit. Every round branches from the CHAMPION, not from the start, and
a new champion is crowned only when the oracle counts strictly more tests passing
with nothing broken. The score is therefore monotonic: a campaign can stall, but it
cannot go backwards, and a wild idea that fails costs a deleted branch. This is what
"get creative without destroying value" means mechanically — the downside of every
experiment is bounded at zero, so the fleet can afford to be strange.

**2. Different agents try different things.** A round assigns each agent a distinct
strategy from a ladder (solver.STRATEGIES) — smallest-possible-fix, read the test as
a contract, widen the search, rewrite outright, contrarian, decompose. Every agent
is told what the others have already tried and had rejected. Running one prompt four
times gives four rewordings of one idea; this gives four ideas.

**3. It gets stranger as it gets stuck, not sooner.** Round 1 draws the conservative
end of the ladder at low temperature. Each failed round slides the slate toward the
strange end and raises the temperature. Creativity is a response to evidence, not a
setting — and it is safe to escalate precisely because of the champion.

**4. It stops.** Solved when the goal tests are green. Reaped when budgets run out
(Article II). Abandoned after N rounds that move nothing — and then it says what the
binding constraint was rather than going quiet (Article IX). What it learned becomes
a lesson in the anchor, so the next campaign starts further along.

The campaign's whole diff parks at the human gate exactly like a single task's does.
Nothing merges autonomously, however many agents it took.
"""

import argparse
import concurrent.futures as futures
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import builder as B
import economy
import solver as S
import workspace as W
import worktree as WT

DEFAULT_AGENTS = 3
DEFAULT_ROUNDS = 5
DEFAULT_DRY_LIMIT = 2         # rounds that move nothing before the campaign stops
COST_PER_SORTIE = 5_000       # budget debited when the executor reports no token count
CREDIT_PER_TEST = 100

_LOG_LOCK = threading.Lock()


def _safe_log(log, message: str) -> None:
    with _LOG_LOCK:
        log(message)


# ── staffing: the organization outlives its agents ────────────────────────────

def staff(count: int) -> list[str]:
    """Field a fleet of `count` agents with budget to spend.

    Living agents that still have budget are re-used; the rest of the roster is made
    up of NEW ids on fresh budgets. This is Article II working as intended rather
    than as an obstacle: an agent that spent itself is retired for good and the
    organization enlists a successor, so a campaign is never silently staffed by
    corpses. Ids are persisted-monotonic — a name is never reused, because the career
    behind it is permanent."""
    roster = economy.roster(alive_only=True)
    taken = {r["agent"] for r in economy.roster(alive_only=False)}
    fleet = [r["agent"] for r in roster
             if r["agent"].startswith("dev-") and r["budget"] > 0][:count]
    while len(fleet) < count:
        name = ""
        while not name or name in taken:
            name = f"dev-{anchor.counter_add('builder_agent_seq', 1):02d}"
        economy.enlist(name)                        # a genuinely new id: fresh budget
        anchor.career_add(name, -1, "born", "enlisted for a campaign")
        taken.add(name)
        fleet.append(name)
    return fleet


def reap_spent(fleet: list[str]) -> list[str]:
    """Retire any agent that has spent its budget (Article II). Returns who went, so
    the next round staffs replacements instead of sending empty-handed agents."""
    gone = []
    for agent in fleet:
        if economy.budget_left(agent) <= 0:
            economy.retire(agent)
            anchor.record(-1, "reap", f"{agent} retired — budget spent")
            anchor.career_add(agent, -1, "reap", "budget spent on campaign sorties")
            gone.append(agent)
    return gone


# ── one agent, one strategy, one round ────────────────────────────────────────

def _sortie(repo: str, agent: str, strategy: dict, champion: dict, goal: list,
            campaign: dict, solve, cfg: dict, round_no: int, log) -> dict:
    """One agent's attempt, in its own worktree, branched from the champion.

    Returns what the ORACLE said, never what the agent claims. A sortie that does
    not strictly improve on the champion leaves nothing behind but a note."""
    out = {"agent": agent, "strategy": strategy["key"], "improved": False,
           "sha": "", "passed": champion["passed"], "files": [], "note": "",
           "round": round_no, "cost": 0}
    if economy.budget_left(agent) <= 0:
        out["note"] = "out of budget"
        return out
    try:
        wt = WT.create(repo, agent, campaign["slug"], base_sha=champion["sha"],
                       suffix=f"r{round_no}-{strategy['key']}")
    except WT.GitError as e:
        out["note"] = f"git refused: {str(e)[:80]}"
        return out

    keep_branch = False
    try:
        # thread-local: this agent's world is its worktree, nobody else's
        W.configure(repo=wt["path"], test_cmd=cfg["test_cmd_str"],
                    timeout=cfg["timeout"], protected=cfg["protected"], key=repo)
        before = W.oracle()
        if not before["ok"]:
            out["note"] = f"no verdict at the champion: {before['error'][:60]}"
            return out
        target_test = next((t for t in goal if t in before["failures"]),
                           before["failures"][0] if before["failures"] else "")
        if not target_test:
            out["note"] = "nothing left failing here"
            return out

        source = W.find_source_for(target_test)
        test_file = W.find_test_file(target_test)
        files = {}
        if source:
            try:
                files[source] = W.read_file(source)
            except OSError:
                files[source] = ""
        tests = {}
        if test_file:
            try:
                tests[test_file] = W.read_file(test_file)
            except OSError:
                pass

        req = {"agent": agent, "repo_name": cfg["repo_name"],
               "test_cmd": cfg["test_cmd_str"],
               "task_title": campaign["title"], "task_test": target_test,
               "failures": before["failures"],
               "failure_report": W.failure_report(target_test, before),
               "files": files, "test_sources": tests, "writable": W.list_sources(80),
               "attempt": 1, "strategy": strategy["key"],
               "strategy_brief": strategy["brief"], "temperature": strategy["temp"],
               "tried": list(campaign["tried"]), "lessons": campaign["lessons"],
               "champion_note": campaign.get("champion_note", "")}
        try:
            result = solve(req)
        except Exception as e:
            out["cost"] = COST_PER_SORTIE
            economy.charge(agent, COST_PER_SORTIE)
            out["note"] = f"executor error: {str(e)[:80]}"
            return out
        out["cost"] = int(result.get("tokens") or COST_PER_SORTIE)
        economy.charge(agent, out["cost"])

        kept, note, after = B.try_patch(agent, result.get("files") or {}, before)
        out["note"] = note
        if not kept or after["passed"] <= champion["passed"]:
            # Not strictly better than what the campaign already holds. The whole
            # point of the champion is that this costs nothing.
            if kept:
                out["note"] = (f"kept locally but no better than the champion "
                               f"({after['passed']}/{after['total']})")
            return out
        out["files"] = WT.changed_files(wt)
        out["sha"] = WT.commit_all(
            wt, f"{campaign['title']} — round {round_no}, {strategy['key']} ({agent})\n\n"
                f"{note}\n")
        out["passed"] = after["passed"]
        out["total"] = after["total"]
        out["failures"] = after["failures"]
        out["improved"] = True
        keep_branch = True
        return out
    finally:
        W.release()
        WT.remove(repo, wt["path"], branch="" if keep_branch else wt["branch"])
        out["branch"] = wt["branch"] if keep_branch else ""


# ── the campaign ──────────────────────────────────────────────────────────────

def run(repo: str = "", test_cmd: str = "", *, agents: int = DEFAULT_AGENTS,
        rounds: int = DEFAULT_ROUNDS, dry_limit: int = DEFAULT_DRY_LIMIT,
        budget: int = 0, goal: list | None = None, title: str = "",
        solve=None, log=print) -> dict:
    """Fight one problem with a fleet until it is solved, dry, or out of budget."""
    anchor.init()
    economy.init()
    B.init()
    solve = solve or S.native_solver
    cfg = W.configure(repo=repo, test_cmd=test_cmd)
    repo = cfg["repo"]
    W.init()

    if not WT.is_git(repo) or not WT.has_commits(repo):
        return {"ok": False, "error": f"{repo} needs to be a git repository with at "
                                      f"least one commit — the branch IS the undo"}
    base = W.oracle()
    if not base["ok"]:
        anchor.record(-1, "stall", f"campaign cannot start: {base['error']}")
        return {"ok": False, "error": base["error"],
                "binding_constraint": "the test command does not produce results"}

    goal = [t for t in (goal or base["failures"])]
    base_branch, base_sha = WT.base_ref(repo)
    title = title or (f"make {len(goal)} failing test(s) pass"
                      if len(goal) != 1 else f"make {goal[0].split('.')[-1]} pass")
    campaign = {"slug": f"campaign-{int(time.time())}", "title": title,
                "tried": [], "lessons": [s["lesson"] for s in anchor.skills_top(8)],
                "champion_note": ""}
    champion = {"sha": base_sha, "passed": base["passed"], "total": base["total"],
                "failures": list(base["failures"]), "round": 0, "by": "", "files": []}
    camp_branch = f"{WT.BRANCH_PREFIX}/campaign/{campaign['slug']}"

    if not goal:
        log("nothing to fight for — the suite is already green")
        return {"ok": True, "solved": True, "rounds": 0, "gate_id": 0,
                "suite": f"{base['passed']}/{base['total']}"}

    size = max(1, agents)
    fleet = staff(size)
    # The campaign's own ceiling (Article III), distinct from any one agent's budget
    # (Article II). Agents are reaped and replaced; THIS is what ends the campaign.
    budget = budget if budget > 0 else size * rounds * COST_PER_SORTIE * 2
    spent_before = sum(economy.budget_left(a) for a in fleet)
    log(f"CAMPAIGN {campaign['title']}")
    log(f"  repo {cfg['repo_name']} · oracle `{cfg['test_cmd_str']}` · "
        f"base {base['passed']}/{base['total']} · fleet {', '.join(fleet)} · "
        f"budget {budget:,}")
    did = anchor.reason_add(-1, "campaign", f"campaign {campaign['slug']}",
                            f"goal: {title}; {len(goal)} test(s); fleet of {len(fleet)}",
                            derived_from=[f"commit:{base_sha[:12]}"],
                            authorized_by="policy")

    dry, history, all_branches, spent, fought = 0, [], [], 0, 0
    for round_no in range(1, max(1, rounds) + 1):
        if not [t for t in goal if t in champion["failures"]]:
            break                                  # the goal is met
        if spent >= budget:
            history.append(f"round {round_no}: campaign budget spent ({spent:,})")
            anchor.record(-1, "stall",
                          f"campaign stopped at its budget: {spent:,} of {budget:,}")
            log(f"   -> campaign budget spent ({spent:,}/{budget:,})")
            break
        gone = reap_spent(fleet)                   # Article II, then hire successors
        if gone:
            log(f"   reaped {', '.join(gone)} — budget spent; enlisting successors")
            fleet = staff(size)
        fought += 1              # a round only counts once agents actually fly
        slate = S.strategies_for(round_no, len(fleet))
        log(f"round {round_no}  [{', '.join(s['key'] for s in slate)}]  "
            f"from champion {champion['passed']}/{champion['total']}")

        with futures.ThreadPoolExecutor(max_workers=len(fleet)) as pool:
            jobs = [pool.submit(_sortie, repo, agent, strategy, champion, goal,
                                campaign, solve, cfg, round_no, log)
                    for agent, strategy in zip(fleet, slate)]
            sorties = []
            for j in jobs:
                try:
                    sorties.append(j.result())
                except Exception as e:             # one agent dying is not the fleet dying
                    sorties.append({"agent": "?", "strategy": "?", "improved": False,
                                    "note": f"sortie crashed: {str(e)[:80]}",
                                    "branch": "", "sha": ""})

        spent += sum(s.get("cost", 0) for s in sorties)
        for s in sorties:
            mark = "**" if s["improved"] else "  "
            log(f"   {mark} {s['agent']:<8} {s['strategy']:<14} {s['note'][:70]}")
            if s.get("branch"):
                all_branches.append(s["branch"])
            if not s["improved"]:
                campaign["tried"].append(f"{s['strategy']}: {s['note'][:120]}")

        winners = [s for s in sorties if s["improved"]]
        if winners:
            # Fewest files wins a tie: the same result with a smaller blast radius is
            # the better one, and the human has less to read.
            best = max(winners, key=lambda s: (s["passed"], -len(s["files"])))
            gain = best["passed"] - champion["passed"]      # measure before crowning
            WT.set_branch(repo, camp_branch, best["sha"])   # ref it BEFORE any deletes
            champion = {"sha": best["sha"], "passed": best["passed"],
                        "total": best["total"], "failures": best["failures"],
                        "round": round_no, "by": best["agent"],
                        "files": best["files"]}
            campaign["champion_note"] = (
                f"round {round_no}: {best['agent']} ({best['strategy']}) reached "
                f"{best['passed']}/{best['total']} by changing "
                f"{', '.join(best['files'][:4])}")
            economy.credit(best["agent"], CREDIT_PER_TEST * max(1, gain))
            anchor.career_add(best["agent"], -1, "work",
                              f"campaign round {round_no}: new champion at "
                              f"{best['passed']}/{best['total']} via {best['strategy']}")
            log(f"   -> CHAMPION {best['passed']}/{best['total']} "
                f"({best['agent']}, {best['strategy']})")
            history.append(f"round {round_no}: {best['agent']}/{best['strategy']} "
                           f"-> {best['passed']}/{best['total']}")
            dry = 0
        else:
            dry += 1
            history.append(f"round {round_no}: no agent improved on the champion")
            log(f"   -> no improvement ({dry}/{dry_limit} dry rounds)")
            if dry >= dry_limit:
                break

    for b in all_branches:                          # losing sorties leave nothing
        if b != camp_branch:
            WT._git(repo, "branch", "-D", b, check=False)

    solved = not [t for t in goal if t in champion["failures"]]
    gained = champion["passed"] - base["passed"]
    report = {"ok": True, "solved": solved, "rounds": fought,
              "history": history, "repo": repo, "goal": goal,
              "base": f"{base['passed']}/{base['total']}",
              "champion": f"{champion['passed']}/{champion['total']}",
              "gained": gained, "branch": camp_branch if gained > 0 else "",
              "gate_id": 0}

    if gained <= 0:
        # Article IX: nothing to show is a result that must name its own cause.
        constraint = (f"{fought} round(s), {len(campaign['tried'])} rejected "
                      f"approaches, no test turned green — the failing tests are "
                      f"{', '.join(goal[:3])}")
        report["binding_constraint"] = constraint
        anchor.record(-1, "stall", f"campaign produced nothing: {constraint}")
        anchor.decision_close(did, outcome="no progress; campaign abandoned")
        WT._git(repo, "branch", "-D", camp_branch, check=False)
        log(f"\nSTALLED — {constraint}")
        return report

    files = WT.files_changed_between(repo, base_sha, champion["sha"])
    newly = sorted(set(base["failures"]) - set(champion["failures"]))
    gate_id = B.park(
        repo=repo, branch=camp_branch, base_branch=base_branch, base_sha=base_sha,
        agent=f"campaign/{len(fleet)} agents", task=", ".join(goal[:4]),
        title=title, files=files,
        diff=WT.diff_range(repo, base_sha, champion["sha"]),
        delta={"before": base["passed"], "after": champion["passed"],
               "total": champion["total"], "newly": newly},
        attempts=fought, decision_id=did)
    report["gate_id"] = gate_id

    lesson = (f"campaign '{title}': {'solved' if solved else 'partial'} in "
              f"{fought} round(s); what worked last was "
              f"{champion['by']} at round {champion['round']}")
    anchor.skill_add(-1, lesson, source=camp_branch, trigger=title)
    ev = anchor.record(-1, "work", f"campaign {campaign['slug']} — "
                                   f"{base['passed']}→{champion['passed']}/"
                                   f"{champion['total']}, {fought} round(s), "
                                   f"awaiting human merge")
    anchor.decision_close(did, ev, outcome=f"suite {base['passed']}→{champion['passed']}; "
                                           f"parked at gate #{gate_id}")
    log(f"\n{'SOLVED' if solved else 'PARTIAL'} — {base['passed']}/{base['total']} -> "
        f"{champion['passed']}/{champion['total']} in {fought} round(s)")
    log(f"branch {camp_branch} waiting at the gate as #{gate_id}")
    log("review with:  python3 gov/builder.py show --id %d" % gate_id)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phoenix campaign — a fleet against one "
                                             "problem, until it gives")
    ap.add_argument("--repo", default="")
    ap.add_argument("--test-cmd", default="")
    ap.add_argument("--agents", type=int, default=DEFAULT_AGENTS)
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--dry-limit", type=int, default=DEFAULT_DRY_LIMIT)
    ap.add_argument("--title", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not S.available():
        print("no brain configured (BRAIN_* / DEEPSEEK_API_KEY) — a campaign needs a model")
        return 1
    report = run(repo=a.repo, test_cmd=a.test_cmd, agents=a.agents, rounds=a.rounds,
                 dry_limit=a.dry_limit, title=a.title)
    if a.json:
        print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

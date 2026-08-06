"""Smoke test + agent-performance benchmark for the audit engine.

Runs the whole pipeline the way the Settlement runs agents: contribution is measured by
the oracle (a confirmed exploit), never claimed; agents are credited, promoted, and (if
they thrash) reaped; and every confirmation teaches a **skill** that is recorded in the
permanent anchor. The output is a real report — timings, throughput, per-agent
performance, and the skills learned — not a pass/fail line.

Usage:
    python3 gov/bench.py [repo_path] [--squad sec-1,sec-2,sec-3] [--cap 12] [--strict]

With no repo_path it prefers the cloned Cloudflare repo, else builds a deterministic
fixture so the benchmark always runs (CI-safe). --strict exits non-zero if nothing
confirmed (a genuine smoke failure).
"""

import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import economy
import range as RANGE
import reproducer as RP
import scanner as SC

POINTS_CONFIRM = 100         # oracle-measured contribution per confirmed finding
WASTE_REAP = 3               # consecutive non-confirms before an agent is reaped

SKILL = {
    "js-new-function": "js-new-function: a non-arithmetic expression reaching new Function "
                       "proves RCE; the arithmetic-allowlist fix stops it.",
    "js-eval": "js-eval: eval() on request data executes arbitrary JS; validate "
               "arithmetic-only before evaluating.",
    "js-traversal": "js-traversal: an unchecked `${dir}/${name}` join escapes via ../; "
                    "resolve and assert the prefix.",
    "py-eval": "py-eval: eval/exec on input is RCE; parse and allow arithmetic AST only.",
    "py-traversal": "py-traversal: os.path.join on a name escapes via ../; realpath and "
                    "assert the prefix.",
}


def _fixture_repo() -> tuple[str, bool]:
    d = tempfile.mkdtemp(prefix="phx-bench-")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "src", "calc.js"), "w") as f:
        f.write("export function calc(req){\n"
                "  const expr = req.query.expr;\n"
                "  return eval(expr);            // planted rce\n}\n")
    with open(os.path.join(d, "src", "run.ts"), "w") as f:
        f.write("export function run(req){\n"
                "  const fn = new Function('return ' + req.body.code)();\n"
                "  return fn;\n}\n")
    with open(os.path.join(d, "src", "files.py"), "w") as f:
        f.write("def read_doc(name):\n"
                "    return open('docs/' + name).read()   # planted traversal\n")
    return d, False


def provable_candidates(scan_res: dict, root: str) -> list[dict]:
    out = []
    for c in scan_res["candidates"]:
        cc = dict(c)
        cc["abspath"] = os.path.join(root, c["file"])
        if RP.pick_generator(cc):
            out.append(cc)
    return out


def run_bench(repo_path: str | None, squad: list[str], cap: int) -> dict:
    anchor.init()
    economy.init()
    real = bool(repo_path and os.path.isdir(repo_path))
    if not real:
        repo_path, _ = _fixture_repo()

    t0 = time.time()
    scan_res = SC.scan(repo_path)
    scan_ms = int((time.time() - t0) * 1000)

    provable = provable_candidates(scan_res, repo_path)[:cap]
    for a in squad:
        economy.enlist(a)

    rows, waste = [], {a: 0 for a in squad}
    active = list(squad)
    learned, promos, reaps = set(), [], []
    t1 = time.time()
    for i, cand in enumerate(provable):
        agent = active[i % len(active)] if active else squad[0]
        r = RP.prove(cand)
        ms = (r.get("vulnerable", {}) or {}).get("elapsed_ms", 0) or 0
        confirmed = r.get("confirmed")
        rows.append({"agent": agent, "loc": f"{cand['file']}:{cand['line']}",
                     "sink": r.get("sink", cand.get("class")), "confirmed": confirmed, "ms": ms})
        if confirmed:
            waste[agent] = 0
            economy.credit(agent, POINTS_CONFIRM)
            sink = r.get("sink")
            if sink in SKILL and sink not in learned:
                sid = anchor.skill_add(-1, SKILL[sink], source="audit", trigger=sink)
                if sid:
                    learned.add(sink)
            did = anchor.reason_add(-1, agent, f"prove {sink}",
                                    f"exploit reproduced on the rebuild of {cand['file']}:{cand['line']}; "
                                    f"the hardened rebuild refuses it",
                                    derived_from=[f"candidate:{cand['file']}:{cand['line']}"],
                                    authorized_by="audit-scope")
            ev = anchor.record(-1, "work", f"{agent} confirmed {sink} at {cand['file']}:{cand['line']}")
            anchor.decision_close(did, ev, outcome=f"+{POINTS_CONFIRM} contribution")
            anchor.career_add(agent, -1, "work", f"confirmed {sink} (+{POINTS_CONFIRM})")
            p = economy.evaluate(agent)
            if p:
                promos.append((agent, p))
                anchor.career_add(agent, -1, "promote", f"promoted to {p}")
        else:
            waste[agent] = waste.get(agent, 0) + 1
            anchor.record(-1, "waste", f"{agent} could not confirm {cand['file']}:{cand['line']}")
            if waste[agent] >= WASTE_REAP and agent in active:
                economy.retire(agent); active.remove(agent); reaps.append(agent)
                anchor.career_add(agent, -1, "reap", "reaped: sustained non-confirmation")
    prove_ms = int((time.time() - t1) * 1000)

    confirmed = [r for r in rows if r["confirmed"]]
    avg_ms = round(sum(r["ms"] for r in rows) / len(rows)) if rows else 0
    tput = round(len(rows) / (prove_ms / 1000), 2) if prove_ms else 0.0
    return {"real": real, "target": repo_path,
            "scan_ms": scan_ms, "files": scan_res["files_scanned"],
            "candidates": scan_res["total"], "by_class": scan_res["by_class"],
            "provable": len(provable), "rows": rows,
            "proved": len(rows), "confirmed": len(confirmed),
            "refuted": len(rows) - len(confirmed), "avg_prove_ms": avg_ms,
            "prove_ms": prove_ms, "throughput": tput,
            "roster": economy.roster(alive_only=False), "promotions": promos,
            "reaps": reaps, "skills": anchor.skills_top(20)}


def report(m: dict) -> str:
    L = []
    L.append("\n\033[1mPHOENIX AUDIT — SMOKE + PERFORMANCE BENCHMARK\033[0m")
    L.append(f"target: {m['target']}  ({'real repo' if m['real'] else 'fixture'})  "
             f"· node range: {'up' if RANGE.NodeRange.available else 'down'}")
    L.append(f"scan:   {m['files']} files → {m['candidates']} candidates in {m['scan_ms']}ms "
             f"({round(m['files']/(m['scan_ms']/1000)) if m['scan_ms'] else '∞'} files/s)  "
             f"by class: {m['by_class']}")
    L.append(f"prove:  {m['provable']} provable → {m['proved']} attempted, "
             f"\033[32m{m['confirmed']} confirmed\033[0m, {m['refuted']} refuted "
             f"· avg {m['avg_prove_ms']}ms · {m['throughput']} proves/s")
    L.append("\n  agent    verdict     ms    location")
    for r in m["rows"][:16]:
        v = "\033[32mconfirmed\033[0m" if r["confirmed"] else "\033[31mrefuted  \033[0m"
        L.append(f"  {r['agent']:<8} {v}  {r['ms']:>4}  {r['loc']}  [{r['sink']}]")
    L.append("\n\033[1mAGENT PERFORMANCE\033[0m (contribution measured by the oracle)")
    L.append("  agent      role        contribution  alive")
    for a in m["roster"]:
        L.append(f"  {a['agent']:<10} {a['role']:<10} {a['contribution']:>10}    "
                 f"{'yes' if a['alive'] else 'REAPED'}")
    if m["promotions"]:
        L.append("  promotions: " + ", ".join(f"{a}→{r}" for a, r in m["promotions"]))
    if m["reaps"]:
        L.append("  reaped: " + ", ".join(m["reaps"]))
    L.append("\n\033[1mSKILLS LEARNED\033[0m (recorded permanently in the anchor)")
    for s in m["skills"][:8]:
        L.append(f"  · {s['lesson']}")
    if not m["skills"]:
        L.append("  (none)")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=os.environ.get(
        "BENCH_REPO", "/workspace/amonxnye/computer-cloufare"))
    ap.add_argument("--squad", default="sec-1,sec-2,sec-3")
    ap.add_argument("--cap", type=int, default=12)
    ap.add_argument("--strict", action="store_true", help="exit non-zero if nothing confirmed")
    a = ap.parse_args()
    repo = a.repo if os.path.isdir(a.repo) else None
    m = run_bench(repo, a.squad.split(","), a.cap)
    print(report(m))
    print()
    if a.strict and m["confirmed"] == 0:
        print("STRICT: nothing confirmed — smoke FAILED")
        sys.exit(1)
    sys.exit(0)

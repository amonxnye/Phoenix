"""Smoke — drive the whole product once, and report what the agents actually did.

The verify_* suites answer "is each part correct". This answers a different and
blunter question: if someone deploys this today and uses it, does the thing work end
to end, and what does it cost? It provisions real repositories, runs real fleets,
approves a real merge, rejects a real branch, serves two guests over real HTTP at the
same time, and then prints the ledger.

Two modes, and the report says which one it ran in, because the difference matters:

  rehearsal  scripted executors stand in for the model. Every mechanism is real —
             git, worktrees, the oracle, the gate, the ledger — but the numbers
             measure the harness, not a model. Needs no API key and no network.

  live       the configured model does the work (--live). These numbers are the ones
             worth quoting: keep rate, cost per test, and whether attempt three ever
             pays are properties of a model on a task, not of this code.

    python3 gov/smoke.py                 # rehearsal
    python3 gov/smoke.py --live          # against the configured model
    python3 gov/smoke.py --json          # machine-readable, for CI
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import builder as B                                                    # noqa: E402
import campaign as C                                                   # noqa: E402
import guests                                                          # noqa: E402
import metrics as M                                                    # noqa: E402
import solver as S                                                     # noqa: E402
import starter                                                         # noqa: E402
import worktree as WT                                                  # noqa: E402

OK, BAD = "ok", "FAILED"
steps: list[dict] = []
_t0 = time.perf_counter()


def step(name, ok, detail="", seconds=None):
    steps.append({"step": name, "ok": bool(ok), "detail": detail,
                  "seconds": round(seconds, 2) if seconds is not None else None})
    mark = " ok " if ok else "FAIL"
    took = f"{seconds:6.2f}s" if seconds is not None else "       "
    print(f"  [{mark}] {took}  {name}" + (f"  — {detail}" if detail else ""))


def section(title):
    print(f"\n{title}\n" + "─" * 74)


# ── the rehearsal fleet ──────────────────────────────────────────────────────
#
# Deliberately imperfect. A scripted solver that always succeeds would produce a
# report with a 100% keep rate and teach nobody anything about reading one — and it
# would never exercise the revert path, the retry ladder or the failure outcomes.

FIXES = {
    "orders.py": '''"""Order pricing."""

TAX = 0.08
RATE_PER_KG = 1.5
EXPRESS_MULTIPLIER = 2
KM_PER_DAY = 167


def shipping(weight_kg, express=False):
    base = weight_kg * RATE_PER_KG
    return base * EXPRESS_MULTIPLIER if express else base


def eta_days(distance_km, express=False):
    if express:
        return 1
    return max(1, round(distance_km / KM_PER_DAY))


def discount(code, subtotal):
    if code == "SAVE10":
        return round(subtotal * 0.10, 2)
    return 0.0


def total(subtotal, weight_kg, code="", express=False):
    taxed = (subtotal - discount(code, subtotal)) * (1 + TAX)
    return round(taxed + shipping(weight_kg, express), 2)
''',
    "inventory.py": '''"""Stock control helpers."""


def reorder_point(daily_usage, lead_time_days, safety_stock=0):
    return daily_usage * lead_time_days + safety_stock


def is_low(stock, point):
    return stock <= point


def restock_qty(stock, target):
    return max(0, target - stock)
''',
    "textkit.py": '''"""Small text helpers."""

import re

ELLIPSIS = "\\u2026"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit - 1] + ELLIPSIS


def wrap_words(text, width):
    lines, current = [], ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
''',
}

# Which approaches "work" in a rehearsal. Mirrors the real shape: the conservative
# strategies land the straightforward fixes and the exotic ones mostly do not, so the
# per-strategy table has something in it to read.
REHEARSAL_WINNERS = {"direct", "read-the-test", "rewrite", ""}


def rehearsal_solver(delay: float = 0.0):
    lock = threading.Lock()
    seen: dict[str, int] = {}

    def solve(req):
        if delay:
            time.sleep(delay)
        path = next(iter(req.get("files") or {}), "")
        strategy = req.get("strategy", "")
        with lock:
            seen[path] = seen.get(path, 0) + 1
            nth = seen[path]
        source = req["files"][path]
        # first attempt on a file misses; a strategy off the list misses
        if nth == 1 or (strategy and strategy not in REHEARSAL_WINNERS):
            return {"files": {path: source + "\n# investigated\n"}, "tokens": 1400}
        fix = FIXES.get(os.path.basename(path))
        return {"files": ({path: fix} if fix else {}), "tokens": 2100}

    return solve


# ── HTTP helper for the service scenario ─────────────────────────────────────

def http(port, method, path, body=None, cookie=""):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), ""
        except json.JSONDecodeError:
            return e.code, {}, ""


# ── the report ───────────────────────────────────────────────────────────────

def table(title, rows, key, cols):
    if not rows:
        return
    print(f"\n  {title}")
    head = f"    {key:<16}" + "".join(f"{c[0]:>13}" for c in cols)
    print(head)
    print("    " + "─" * (len(head) - 4))
    for r in rows:
        line = f"    {str(r[key])[:16]:<16}"
        for _, field, fmt in cols:
            v = r.get(field)
            line += f"{('—' if v is None else fmt(v)):>13}"
        print(line)


def performance_report(repos, mode, model_name):
    section("PERFORMANCE — what the agents did")
    print(f"  mode: {mode}" + (f"  ·  model: {model_name}" if model_name else ""))
    if mode == "rehearsal":
        print("  NOTE: scripted executors. These numbers measure the harness and its")
        print("        accounting, not a model's ability to write code.")

    total_summary = {"sorties": 0, "paying": 0, "rejected": 0, "tests_gained": 0,
                     "cost": 0, "seconds": 0.0, "runs": 0, "runs_solved": 0}
    for repo in repos:
        s = M.summary(repo=repo)
        for k in total_summary:
            total_summary[k] += s.get(k, 0)

    n = total_summary["sorties"]
    rate = round(total_summary["rejected"] / n, 3) if n else 0.0
    print(f"\n  {total_summary['runs']} run(s) · {n} sortie(s) · "
          f"{total_summary['paying']} kept · {total_summary['rejected']} rejected "
          f"by the oracle ({rate:.0%})")
    print(f"  {total_summary['tests_gained']} test(s) turned green · "
          f"{total_summary['cost']:,} budget spent · "
          f"{total_summary['seconds']:.1f}s inside the executors")
    if total_summary["tests_gained"] > 0:
        print(f"  {total_summary['cost'] // max(1, total_summary['tests_gained']):,} "
              f"budget per test turned green")

    pct = lambda v: f"{v:.0%}"                                        # noqa: E731
    num = lambda v: f"{v:,}"                                          # noqa: E731
    ms = lambda v: f"{v / 1000:.1f}s"                                 # noqa: E731

    cols = [("sorties", "sorties", num), ("keep rate", "keep_rate", pct),
            ("tests +", "tests_gained", num), ("cost", "cost", num),
            ("cost/test", "cost_per_test", num), ("avg time", "avg_ms", ms)]

    agents, strategies, attempts = [], [], {}
    for repo in repos:
        agents += M.by_agent(repo=repo)
        strategies += M.by_strategy(repo=repo)
        for row in M.by_attempt(repo=repo):
            a = attempts.setdefault(row["attempt"], dict(row))
            if a is not row:
                a["sorties"] += row["sorties"]
                a["paying"] += row["paying"]
                a["tests_gained"] += row["tests_gained"]
                a["cost"] += row["cost"]
                a["keep_rate"] = round(a["paying"] / a["sorties"], 3) if a["sorties"] else 0.0

    table("by agent", agents, "agent", cols)
    table("by strategy (campaigns only)", strategies, "strategy", cols)
    table("by attempt number", [attempts[k] for k in sorted(attempts)], "attempt", cols)

    counts = {}
    for repo in repos:
        for outcome, c in M.outcomes(repo=repo).items():
            counts[outcome] = counts.get(outcome, 0) + c
    print("\n  why attempts were thrown away")
    for outcome in [o for o in M.OUTCOMES if o not in M.PAYING]:
        if counts.get(outcome):
            bar = "█" * min(40, counts[outcome])
            print(f"    {outcome:<14} {counts[outcome]:>4}  {bar}")
    return total_summary


# ── scenarios ────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phoenix smoke test — end to end, with numbers")
    ap.add_argument("--live", action="store_true",
                    help="use the configured model instead of scripted executors")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    ap.add_argument("--keep", action="store_true", help="do not delete the workspaces")
    ap.add_argument("--agents", type=int, default=3, help="campaign fleet size")
    ap.add_argument("--rounds", type=int, default=3, help="campaign rounds")
    a = ap.parse_args(argv)

    data = tempfile.mkdtemp(prefix="phoenix-smoke-data-")
    os.environ["GOV_DATA_DIR"] = data
    os.environ.setdefault("GATE_SECRET", "smoke-secret")
    for mod in (B, M, guests):
        for attr in ("DB", "SECRET_FILE"):
            if hasattr(mod, attr):
                setattr(mod, attr, os.path.join(data, os.path.basename(getattr(mod, attr))))
    guests._SECRET = None

    mode = "live" if a.live else "rehearsal"
    model_name = ""
    if a.live:
        if not S.available():
            print("--live needs a model: set BRAIN_BASE_URL and BRAIN_API_KEY "
                  "(or DEEPSEEK_API_KEY)")
            return 2
        model_name = os.environ.get("BRAIN_MODEL", "") or "configured brain"
        solve = None                                    # None means native_solver
    else:
        solve = rehearsal_solver()

    print(f"PHOENIX SMOKE · {mode} · python {sys.version.split()[0]}")
    print(f"data {data}")

    workspaces, repos = [], []

    try:
        # ── 1. preflight ────────────────────────────────────────────────────
        section("1. Preflight")
        t = time.perf_counter()
        git_ok = shutil.which("git") is not None
        step("git is available", git_ok, shutil.which("git") or "not found",
             time.perf_counter() - t)
        if not git_ok:
            return 1
        step("python is 3.10 or newer", sys.version_info >= (3, 10),
             sys.version.split()[0])

        # ── 2. a real workspace ─────────────────────────────────────────────
        section("2. Provisioning a workspace")
        t = time.perf_counter()
        dest = tempfile.mkdtemp(prefix="smoke-builder-")
        ws = starter.provision(dest, "orders")
        workspaces.append(dest)
        repos.append(ws["repo"])
        step("starter repository built and proven red", len(ws["red"]) >= 4,
             f"{len(ws['red'])} failing test(s) in {ws['title'].lower()}",
             time.perf_counter() - t)

        # ── 3. the builder ──────────────────────────────────────────────────
        section("3. One agent works the backlog")
        t = time.perf_counter()
        report = B.run(repo=ws["repo"], test_cmd=starter.TEST_CMD, agents=1,
                       limit=1, solve=solve, log=lambda *_: None,
                       actor="smoke", agent_prefix="smoke")
        took = time.perf_counter() - t
        step("the run completed", report.get("ok"), report.get("error", ""), took)
        step("work parked at the gate", report.get("parked", 0) >= 1,
             f"{report.get('parked', 0)} dossier(s); base repo still "
             f"{report.get('suite_now')} — nothing merges without a human")
        step("the run distilled lessons from its own ledger",
             isinstance(report.get("lessons"), list),
             "; ".join(report.get("lessons", []))[:80] or "none new")

        gate_id = (report.get("gate") or [0])[0]
        dossier = B.gate_get(gate_id) if gate_id else None
        step("the dossier carries a real diff", bool(dossier)
             and "diff --git" in (dossier or {}).get("diff", ""),
             f"#{gate_id} {(dossier or {}).get('branch', '')[:40]}")
        step("and a risk class decided by path, not by a model",
             bool(dossier) and (dossier or {}).get("risk") in ("low", "high"),
             (dossier or {}).get("risk", ""))

        # ── 4. the gate ─────────────────────────────────────────────────────
        section("4. A human decides")
        if gate_id:
            head_before = WT._git(ws["repo"], "rev-parse", "HEAD").strip()
            red_before = len(starter._red_tests(ws["repo"]))
            t = time.perf_counter()
            ok, msg = B.approve(gate_id, by="smoke-operator")
            took = time.perf_counter() - t
            step("approving merges the branch", ok, msg[:70], took)
            head_after = WT._git(ws["repo"], "rev-parse", "HEAD").strip()
            step("the base branch moved", head_before != head_after,
                 f"{head_before[:8]} -> {head_after[:8]}")
            red_after = len(starter._red_tests(ws["repo"]))
            step("the suite really improved in the base repository",
                 red_after < red_before, f"{red_before} red -> {red_after} red")

        # ── 5. a campaign ───────────────────────────────────────────────────
        section(f"5. A fleet of {a.agents} against one problem")
        dest2 = tempfile.mkdtemp(prefix="smoke-campaign-")
        ws2 = starter.provision(dest2, "textkit")
        workspaces.append(dest2)
        repos.append(ws2["repo"])
        t = time.perf_counter()
        creport = C.run(repo=ws2["repo"], test_cmd=starter.TEST_CMD, agents=a.agents,
                        rounds=a.rounds, solve=solve, log=lambda *_: None,
                        actor="smoke", agent_prefix="fleet")
        took = time.perf_counter() - t
        step("the campaign completed", creport.get("ok"), creport.get("error", ""), took)
        step("it reports what it reached", bool(creport.get("champion")),
             f"{creport.get('base')} -> {creport.get('champion')} in "
             f"{creport.get('rounds')} round(s)")
        if creport.get("gate_id"):
            step("the whole campaign parks as one dossier", True,
                 f"#{creport['gate_id']}")
            t = time.perf_counter()
            ok, msg = B.reject(creport["gate_id"], by="smoke-operator",
                               reason="smoke test: proving a rejection costs nothing")
            step("rejecting destroys nothing of the base", ok, msg[:70],
                 time.perf_counter() - t)
            step("the base commit is where it was",
                 len(starter._red_tests(ws2["repo"])) == len(ws2["red"]),
                 f"{len(ws2['red'])} still red")
        else:
            step("the campaign found no improvement to park",
                 creport.get("binding_constraint") is not None,
                 (creport.get("binding_constraint") or "")[:70])

        # ── 6. the service, two guests at once ──────────────────────────────
        section("6. Two guests, one instance")
        import gate as G
        srv, service = G.build(host="127.0.0.1", port=0, serve_guests=True, solve=solve)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        t = time.perf_counter()
        _, _, ck1 = http(port, "GET", "/api/state")
        _, _, ck2 = http(port, "GET", "/api/state")
        c1, c2 = ck1.split(";")[0], ck2.split(";")[0]
        step("two visitors get two sessions", c1 != c2, "", time.perf_counter() - t)

        t = time.perf_counter()
        results = {}

        def provision_and_run(cookie, name, key):
            http(port, "POST", "/api/workspace", {"starter": name}, cookie=cookie)
            http(port, "POST", "/api/run", {"kind": "builder"}, cookie=cookie)
            deadline = time.time() + 300
            while time.time() < deadline:
                _, st, _ = http(port, "GET", "/api/state", cookie=cookie)
                if not st.get("run", {}).get("active"):
                    results[key] = st
                    return
                time.sleep(0.3)
            results[key] = {}

        t1 = threading.Thread(target=provision_and_run, args=(c1, "orders", "g1"))
        t2 = threading.Thread(target=provision_and_run, args=(c2, "inventory", "g2"))
        t1.start(), t2.start()
        t1.join(), t2.join()
        took = time.perf_counter() - t

        s1, s2 = results.get("g1", {}), results.get("g2", {})
        step("both guests' fleets ran concurrently",
             bool(s1.get("pending")) and bool(s2.get("pending")),
             f"{len(s1.get('pending', []))} and {len(s2.get('pending', []))} parked", took)
        for st in (s1, s2):
            for row in st.get("pending", []):
                repos.append(B.gate_get(row["id"])["repo"])
        ids1 = {p["id"] for p in s1.get("pending", [])}
        ids2 = {p["id"] for p in s2.get("pending", [])}
        step("neither guest can see the other's work", ids1.isdisjoint(ids2)
             and bool(ids1) and bool(ids2), f"{sorted(ids1)} vs {sorted(ids2)}")
        if ids2:
            code, body, _ = http(port, "POST", "/api/gate/approve",
                                 {"id": sorted(ids2)[0]}, cookie=c1)
            step("nor approve it", code == 403, body.get("error", ""))
        if ids1:
            code, body, _ = http(port, "POST", "/api/gate/approve",
                                 {"id": sorted(ids1)[0]}, cookie=c1)
            step("the owner can", code == 200 and body.get("ok"),
                 body.get("message", "")[:60])
        srv.shutdown()

        # ── the numbers ─────────────────────────────────────────────────────
        totals = performance_report(sorted(set(repos)), mode, model_name)

        section("RESULT")
        failed = [s for s in steps if not s["ok"]]
        elapsed = time.perf_counter() - _t0
        print(f"  {len(steps) - len(failed)}/{len(steps)} steps passed in {elapsed:.1f}s")
        if failed:
            for f in failed:
                print(f"    FAILED: {f['step']}  {f['detail']}")
        if a.json:
            print("\n" + json.dumps({"mode": mode, "model": model_name,
                                     "steps": steps, "performance": totals,
                                     "seconds": round(elapsed, 1)}, indent=2))
        return 0 if not failed else 1

    finally:
        if not a.keep:
            for d in workspaces:
                shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(data, ignore_errors=True)
        else:
            print(f"\nkept: {', '.join(workspaces)}\n      {data}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

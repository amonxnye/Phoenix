"""Acceptance checks. Each either passes or fails — no interpretation needed."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import governor as G
import runtime as R

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))          # a truthy non-bool would print PASS and then
                                      # crash the tally — the reporting path must not
                                      # depend on the shape of what it reports (IX.7)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        p = R.DB + suffix
        if os.path.exists(p):
            os.remove(p)


print("\n1. Runtime — concurrent units, durable state")
fresh_db()

cp = R.connect()
graph = R.build(cp)

TASKS = [
    ("unit-01", "refactor auth module"),
    ("unit-02", "add retry to payment client"),
    ("unit-03", "migrate config loader"),
    ("unit-04", "fix flaky integration test"),
    ("unit-05", "bump dependency set"),
]
t0 = time.time()
with ThreadPoolExecutor(max_workers=5) as ex:
    list(ex.map(lambda t: R.spawn(graph, *t), TASKS))
elapsed = time.time() - t0

v = G.units(graph, cp)
check("5 units spawned concurrently", len(v) == 5, f"{elapsed:.2f}s wall clock")
check("all parked at the gate", all(u.pending for u in v))
check("each completed 3 work steps", all(u.steps == 3 for u in v))

probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0,{HERE!r});"
     "import runtime as R, governor as G;"
     "cp=R.connect(); g=R.build(cp); v=G.units(g,cp);"
     "print(len(v), sum(u.steps for u in v), sum(1 for u in v if u.pending))"],
    capture_output=True, text=True,
)
n, steps, parked = probe.stdout.split()
check("fresh process reads identical state", (n, steps, parked) == ("5", "15", "5"),
      f"units={n} steps={steps} parked={parked}")

print("\n2. Read layer — no separate state store")
tids = G._thread_ids(cp)
check("thread list comes from checkpointer", sorted(tids) == [t[0] for t in TASKS],
      f"{len(tids)} threads")
check("pending interrupt payload readable", v[0].pending.get("reversible") is False,
      f"action: {v[0].pending['action'][:38]}…")

print("\n3. Approval gate")
R.resume(graph, "unit-01", "approve")
R.resume(graph, "unit-02", "reject")

after = {u.unit_id: u for u in G.units(graph, cp)}
check("approved unit published", after["unit-01"].log[-1] == "approved -> published")
check("rejected unit aborted", after["unit-02"].log[-1] == "rejected (reject) -> aborted")
check("resolved units report done",
      after["unit-01"].status == "done" and after["unit-02"].status == "done")
check("untouched units still parked",
      all(after[f"unit-0{i}"].pending for i in (3, 4, 5)))

probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0,{HERE!r});"
     "import runtime as R, governor as G;"
     "cp=R.connect(); g=R.build(cp);"
     "d={u.unit_id:u for u in G.units(g,cp)};"
     "print(d['unit-01'].log[-1], '|', d['unit-02'].log[-1])"],
    capture_output=True, text=True,
)
check("decisions persist across restart",
      "published" in probe.stdout and "aborted" in probe.stdout,
      probe.stdout.strip() or probe.stderr.strip()[:80])

print("\n4. Governor — cap and idle detection")
v = G.units(graph, cp)
ok, reason = G.may_spawn(v)
check("spend tracked across fleet", G.spent(v) > 0, reason)

G.TOKEN_CAP = 1_000
ok, reason = G.may_spawn(v)
check("cap HALTS spawning, not warns", ok is False, reason)
G.TOKEN_CAP = 100_000

R.spawn(graph, "unit-06", "tidy imports")
fresh = {u.unit_id: u for u in G.units(graph, cp)}["unit-06"]
check("newly parked unit reads awaiting_approval",
      fresh.status == "awaiting_approval", f"age {fresh.age_s}s")

time.sleep(G.IDLE_AFTER_S + 0.3)
aged = {u.unit_id: u for u in G.units(graph, cp)}["unit-06"]
check("same unit flips to idle once stale", aged.status == "idle", f"age {aged.age_s}s")
check("all stale approvals surface together", len(G.idle(G.units(graph, cp))) == 4,
      "3 original + 1 new, all parked on a human")

print("\n" + G.render(G.units(graph, cp)))
print(f"{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

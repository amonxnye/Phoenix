"""The watch — analysis over time, autonomously, under rules.

A watched repository is re-fetched on its interval. What makes this safe to leave
running is the same thing that made the settlement safe to leave running:

- **Read-only, forever.** The watch can only ever fetch an archive and analyse it.
- **A commit that has not moved costs nothing.** The sha is compared before any
  analysis; an unchanged tree is a recorded no-op, not a re-run.
- **Every cycle has a budget** (Charter §7), and a run that halts is recorded as
  halted, never retried in a loop.
- **Three consecutive halts pause the repository and escalate** — Constitution IX.8
  in this domain: repetition is not effort, and a watch that keeps failing is a
  finding about the watch.
- **Findings carry across cycles by fingerprint.** One that persists gains a cycle
  count. A MACHINE-VERIFIED finding that vanishes after a new commit is marked fixed
  upstream — the graph said it was dead, and the graph now says it is gone or used.
  A judged finding that vanishes is marked unconfirmed, not fixed: a model not
  repeating itself is not evidence that anything changed.
"""

import threading
import time

from . import analyse, ingest, store

MAX_HALTS = 3
DEFAULT_INTERVAL_S = 6 * 3600
CHECK_EVERY_S = 60

FETCH = ingest.fetch                                  # replaceable in tests
ANALYSE = analyse.run
_STATE = {"thread": None, "cycles": 0, "last_tick": 0.0, "last": []}


def carry_over(repo_id: str, new_run_id: str) -> dict:
    """Compare the new run's findings with the previous run's, by fingerprint."""
    prev_runs = [r for r in store.runs(repo_id, 5) if r["id"] != new_run_id
                 and r["status"] == "complete"]
    if not prev_runs:
        return {"persisted": 0, "fixed": 0, "unconfirmed": 0, "new": 0}
    prev = {f["fingerprint"]: f for f in store.findings(run_id=prev_runs[0]["id"])
            if f.get("fingerprint")}
    now = {f["fingerprint"]: f for f in store.findings(run_id=new_run_id) if f.get("fingerprint")}
    persisted = fixed = unconfirmed = 0
    for fp, f in now.items():
        if fp in prev:
            p = prev[fp]
            store.finding_set(f["id"], first_seen_run=p.get("first_seen_run") or p["id"],
                              seen_runs=int(p.get("seen_runs") or 1) + 1)
            persisted += 1
    for fp, p in prev.items():
        if fp not in now and p.get("upstream") == "open":
            if p["basis"] == "machine-verified":
                store.finding_set(p["id"], upstream="fixed")
                fixed += 1
            else:
                store.finding_set(p["id"], upstream="unconfirmed")
                unconfirmed += 1
    return {"persisted": persisted, "fixed": fixed, "unconfirmed": unconfirmed,
            "new": len(now) - persisted}


def cycle(repo: dict, budget_cents: int | None = None) -> dict:
    """One watched repository, once. Returns what happened, in words the page shows."""
    rid, url = repo["id"], repo["url"]
    c = FETCH(url)
    if "error" in c:
        halts = int(repo.get("halts") or 0) + 1
        store.repo_set(rid, halts=halts, last_checked=time.time())
        run_id = store.run_open(rid, "", analyse.charter.charter()["stamp"], trigger="watch")
        store.gap_add(run_id, "ingest", c["error"])
        store.run_close(run_id, "halted", note=c["error"])
        return _after_halt(repo, halts, c["error"])
    try:
        if c["sha"] and c["sha"] == (repo.get("last_sha") or ""):
            store.repo_set(rid, last_checked=time.time())
            return {"repo": repo["name"], "action": "unchanged",
                    "note": f"still at {c['sha'][:12]} — nothing spent"}
        res = ANALYSE(c["path"], name=c["name"], url=url, budget_cents=budget_cents,
                      commit_sha=c["sha"], trigger="watch")
    finally:
        ingest.remove(c["tmp"])
    if res["status"] != "complete":
        halts = int(repo.get("halts") or 0) + 1
        store.repo_set(rid, halts=halts, last_checked=time.time())
        return _after_halt(repo, halts, res.get("error", res["status"]))
    carried = carry_over(rid, res["run_id"])
    store.repo_set(rid, last_sha=c["sha"], halts=0, last_checked=time.time())
    return {"repo": repo["name"], "action": "analysed", "run_id": res["run_id"],
            "note": (f"{res['findings']} finding(s), {res.get('judged', 0)} judged, "
                     f"{res['gaps']} refusal(s), {res['seconds']}s; "
                     f"{carried['persisted']} persisted, {carried['new']} new, "
                     f"{carried['fixed']} fixed upstream")}


def _after_halt(repo: dict, halts: int, why: str) -> dict:
    if halts >= MAX_HALTS:
        store.repo_set(repo["id"], watch=0)
        # IX.8: the loop stops and the human is told, once, with the reason.
        rid = store.run_open(repo["id"], "", analyse.charter.charter()["stamp"], trigger="watch")
        store.gap_add(rid, "watch", f"paused after {halts} consecutive halts — attention "
                                    f"needed; last reason: {why}")
        store.run_close(rid, "halted", note="watch paused — attention needed")
        return {"repo": repo["name"], "action": "paused",
                "note": f"{halts} consecutive halts — watch paused, attention needed: {why}"}
    return {"repo": repo["name"], "action": "halted", "note": f"halt {halts}/{MAX_HALTS}: {why}"}


def due(now: float | None = None) -> list[dict]:
    now = now or time.time()
    return [r for r in store.repos() if int(r.get("watch") or 0)
            and now - float(r.get("last_checked") or 0) >= int(r.get("interval_s") or DEFAULT_INTERVAL_S)]


def tick(now: float | None = None, budget_cents: int | None = None) -> list[dict]:
    out = []
    for repo in due(now):
        try:
            out.append(cycle(repo, budget_cents))
        except Exception as e:                        # noqa: BLE001 — a cycle may not kill the watch
            out.append({"repo": repo["name"], "action": "error", "note": f"{type(e).__name__}: {e}"})
    _STATE.update(cycles=_STATE["cycles"] + len(out), last_tick=time.time(), last=out[-6:])
    return out


def status() -> dict:
    ws = [r for r in store.repos() if int(r.get("watch") or 0)]
    nxt = min((float(r.get("last_checked") or 0) + int(r.get("interval_s") or DEFAULT_INTERVAL_S)
               for r in ws), default=0)
    return {"running": bool(_STATE["thread"] and _STATE["thread"].is_alive()),
            "watched": len(ws), "cycles": _STATE["cycles"], "last_tick": _STATE["last_tick"],
            "next_due": nxt, "last": _STATE["last"]}


def _loop():
    while True:                                       # a daemon thread; dies with the process
        try:
            tick()
        except Exception:                             # noqa: BLE001 — a tick may not kill the watch
            pass
        time.sleep(CHECK_EVERY_S)


def start() -> bool:
    """Idempotent. Started by the console; the CLI never starts it."""
    t = _STATE["thread"]
    if t and t.is_alive():
        return False
    t = threading.Thread(target=_loop, daemon=True, name="mechanic-watch")
    _STATE["thread"] = t
    t.start()
    return True

"""Article XI — the world improves itself, and every improvement stops at the gate.

Phoenix already had every primitive: a mechanic that finds defects in a repository
and proposes patches verified in memory (mechanic/fixer.py), a sandbox that applies a
patch with no credentials and no network (gov/workspace.py), an oracle that decides
by running tests rather than by judging (workspace.oracle), and a gate at which the
irreversible waits for a human (Article IV). This module points them at Phoenix
itself and closes the loop as one measured cycle:

    measure → propose → try in a sandbox copy → run the world's own suites →
    park what survived at the gate → record everything → learn from what failed

Four rules hold the loop inside the constitution:

1. **The live tree is never written.** Every patch is applied to a scratch COPY of
   the repository; the copy is deleted after the verdict. A cycle cannot change the
   running world, only propose to.
2. **The oracle is the suites, run stripped.** A candidate is "verified" only if
   every verification suite Phoenix ships passes on the patched copy with at least
   the baseline's count — in a subprocess with no keys, no tokens and, where the
   platform allows, no network. No model judges whether a fix is good; the suites do.
3. **Verified means parked, not merged.** A verified improvement waits at the gate
   with its diff, the suite delta and the cost of waiting. A human approves it; only
   then is a branch pushed and a draft pull request opened (with a GITHUB_TOKEN), or
   the patch handed over for the human to apply. Merging stays a human act on GitHub.
4. **A cycle that finds nothing is a fact, and repeated nothing is an escalation.**
   Article IX applied to improvement: three consecutive empty cycles are reported to
   the Chief Governor, never quietly repeated.

Everything the cycle does — the signals it read, the candidates it tried, the
verdicts, the spend, the model, the charter in force — is written to the anchor and
shown on /improve.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)                     # the mechanic package lives beside gov/

import anchor                                    # noqa: E402
import brain                                     # noqa: E402
import workspace                                 # noqa: E402

SUITES = (("governor", "gov/verify.py"), ("work", "gov/verify_work.py"),
          ("settlement", "gov/verify_sim.py"), ("mechanic", "mechanic/verify_mechanic.py"))
INTERVAL_S = int(os.environ.get("IMPROVE_INTERVAL_S", str(6 * 3600)))
MAX_TRIES = int(os.environ.get("IMPROVE_MAX_TRIES", "3"))   # candidates tried per cycle
SUITE_TIMEOUT_S = int(os.environ.get("IMPROVE_SUITE_TIMEOUT_S", "600"))
EMPTY_CYCLES_ESCALATE = 3
EXCLUDE = {"node_modules", "__pycache__", ".venv", "data", ".improve-data"}
# (the workspace `sandbox/` IS copied: the work suite is its oracle; its databases are not.
#  `.git` is copied when present — a few MB — so history facts read the same as live.)
_PASSED = re.compile(r"(\d+)/(\d+) checks passed")

# Replaceable seams — the suite scripts both so the whole loop runs offline.
ORACLE = None            # callable(tree_dir) -> {"green", "suites": {name: {...}}}
CANDIDATES = None        # callable() -> list[candidate]
_STATE = {"thread": None, "running": False, "last_tick": 0.0, "current": ""}
_LOCK = threading.Lock()


# ── the record ───────────────────────────────────────────────────────────────

def _init(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS improve_cycles("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, trigger TEXT, status TEXT, "
              "candidates INT, tried INT, verified INT, rejected INT, note TEXT, "
              "seconds REAL, baseline TEXT, signals TEXT, model TEXT, charter TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS improvements("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INT, ts REAL, finding_id TEXT, "
              "title TEXT, file TEXT, severity TEXT, category TEXT, patch TEXT, "
              "before_json TEXT, after_json TEXT, status TEXT, note TEXT, pr_url TEXT, "
              "actor TEXT, decided_ts REAL)")


_ANCHOR_READY = {"ok": False}


def _conn():
    if not _ANCHOR_READY["ok"]:
        anchor.init()                            # the record lives in the anchor: a fresh data
        _ANCHOR_READY["ok"] = True               # dir must be initialised before it is written
    c = anchor._conn()
    _init(c)
    return c


def cycles(limit: int = 20) -> list[dict]:
    c = _conn()
    try:
        c.row_factory = anchor.sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM improve_cycles ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        c.close()
    for r in rows:
        for k in ("baseline", "signals"):
            try:
                r[k] = json.loads(r.get(k) or "{}")
            except (TypeError, ValueError):
                r[k] = {}
    return rows


def proposals(status: str = "", limit: int = 50) -> list[dict]:
    c = _conn()
    try:
        c.row_factory = anchor.sqlite3.Row
        q, args = "SELECT * FROM improvements", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        rows = [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]
    finally:
        c.close()
    for r in rows:
        for k in ("before_json", "after_json"):
            try:
                r[k] = json.loads(r.get(k) or "{}")
            except (TypeError, ValueError):
                r[k] = {}
    return rows


def proposal(pid: int) -> dict | None:
    return next((p for p in proposals(limit=10000) if p["id"] == int(pid)), None)


def _set(pid: int, **fields) -> None:
    c = _conn()
    try:
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE improvements SET {cols} WHERE id=?", (*fields.values(), pid))
        c.commit()
    finally:
        c.close()


# ── the sandbox copy and the oracle ──────────────────────────────────────────

def _ignore(_dir, names):
    return [n for n in names if n in EXCLUDE or n.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm"))]


def copy_tree() -> str:
    """A scratch copy of the repository — the only tree a cycle ever writes."""
    dst = tempfile.mkdtemp(prefix="phoenix-improve-")
    tree = os.path.join(dst, "tree")
    shutil.copytree(REPO, tree, ignore=_ignore, symlinks=False)
    return tree


def run_suites(tree: str) -> dict:
    """Phoenix's own verification suites on a tree, each in a stripped subprocess:
    no keys, no tokens, its own data directory inside the tree, no network where the
    platform allows. The verdict is the suites' own count line."""
    data = os.path.join(tree, ".improve-data")
    os.makedirs(data, exist_ok=True)
    import site
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tree, "LANG": "C.UTF-8",
           "GOV_DATA_DIR": data, "MECHANIC_DATA_DIR": os.path.join(data, "mechanic"),
           "MECHANIC_WATCH": "0", "IMPROVE": "0", "PYTHONDONTWRITEBYTECODE": "1",
           # HOME moved, so the interpreter's user site-packages must be named explicitly —
           # the first real run lost every third-party import that way. No secret lives here.
           "PYTHONUSERBASE": site.getuserbase()}
    for k in ("PYTHONPATH", "VIRTUAL_ENV"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    prefix, _mode = workspace._sandbox()
    want = [s for s in SUITES if s[0] in os.environ.get("IMPROVE_SUITES", ",".join(n for n, _ in SUITES)).split(",")]
    out, green = {}, True
    for name, rel in want:
        t0 = time.time()
        try:
            proc = subprocess.run(prefix + [sys.executable, os.path.join(tree, rel)], cwd=tree,
                                  env=env, capture_output=True, text=True, timeout=SUITE_TIMEOUT_S)
            text = (proc.stdout or "") + (proc.stderr or "")
            m = list(_PASSED.finditer(text))
            passed, total = (int(m[-1].group(1)), int(m[-1].group(2))) if m else (0, 0)
            ok = proc.returncode == 0 and total > 0 and passed == total
            tail = "\n".join(ln for ln in text.splitlines() if "FAIL" in ln or "Traceback" in ln
                             or "Error" in ln)[-1200:]
        except subprocess.TimeoutExpired:
            passed, total, ok, tail = 0, 0, False, f"timed out after {SUITE_TIMEOUT_S}s"
        except OSError as e:
            passed, total, ok, tail = 0, 0, False, f"could not run: {e}"
        out[name] = {"passed": passed, "total": total, "ok": ok,
                     "seconds": round(time.time() - t0, 1), "tail": tail}
        green = green and ok
    return {"green": green, "suites": out, "isolation": _mode}


def _oracle(tree: str) -> dict:
    return (ORACLE or run_suites)(tree)


def apply_to_tree(tree: str, cand: dict) -> str:
    """Apply a candidate's patch to the copy. Returns '' or why it could not."""
    from mechanic import fixer                     # the same parser that verified it in memory
    path = os.path.join(tree, cand["file"])
    if not os.path.isfile(path):
        return f"{cand['file']} is not in the tree"
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    hunks = fixer.parse(cand["patch"])
    if not hunks:
        return "patch is not a unified diff"
    new, why = fixer.apply(src, hunks)
    if new is None:
        return f"does not apply: {why}"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return ""


def usable(baseline: dict) -> tuple[bool, str]:
    """A baseline the verdict can be measured against: every suite actually ran and
    reported a count. A suite that could not run is a broken oracle, not a red one."""
    dead = [n for n, s in (baseline.get("suites") or {}).items() if not s.get("total")]
    if not baseline.get("suites"):
        return False, "no suite ran"
    if dead:
        return False, "could not run: " + "; ".join(f"{n} — {baseline['suites'][n].get('tail', '')[-160:]}" for n in dead)
    return True, ""


def verdict(after: dict, before: dict) -> tuple[bool, str]:
    """Measured against the baseline, suite by suite: every suite must have run, passed
    at least as many checks as it did unpatched, and not gone red where it was green.
    A count comparison, not a demand for perfection — a check that is flaky under load
    fails the same way with or without the patch, and the record shows the counts."""
    red, worse = [], []
    for n, b in (before.get("suites") or {}).items():
        s = (after.get("suites") or {}).get(n)
        if not s or not s.get("total"):
            return False, f"suite could not run on the patched copy: {n} — {(s or {}).get('tail', '')[-160:]}"
        if s["passed"] < b.get("passed", 0):
            worse.append(f"{n} ({s['passed']}/{s['total']} vs baseline {b['passed']}/{b['total']})")
        if b.get("ok") and not s.get("ok"):
            red.append(f"{n} ({s['passed']}/{s['total']})")
    if red:
        return False, "suite red: " + ", ".join(red)
    if worse:
        return False, "regression: " + ", ".join(worse)
    return True, "every suite at or above baseline: " + ", ".join(
        f"{n} {s['passed']}/{s['total']}" for n, s in after["suites"].items())


# ── candidates: the mechanic on Phoenix itself ───────────────────────────────

def _candidates() -> list[dict]:
    """Run the mechanic on this repository; keep the proposed patches that applied and
    parsed in memory, most consequential first."""
    if CANDIDATES is not None:
        return CANDIDATES()
    from mechanic import analyse, store
    res = analyse.run(REPO, name="phoenix-self", trigger="improve")
    out = []
    for f in store.findings(run_id=res.get("run_id", "")):
        if f.get("patch_status") == "applies-and-parses" and f.get("patch"):
            ev = (f.get("evidence") or [{}])[0]
            out.append({"finding_id": f["id"], "title": f["title"], "severity": f["severity"],
                        "category": f["category"],
                        "file": f.get("file") or ev.get("file", ""), "patch": f["patch"],
                        "run_id": res.get("run_id", "")})
    out.sort(key=lambda c: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(c["severity"], 4))
    return [c for c in out if c["file"]]


def _signals() -> dict:
    """What the world can measure about itself before it tries to change: the brain's
    recent performance, prompt overruns, and the last cycle's outcome."""
    sig = {"model": brain.brain_name(), "overruns": len(brain.prompt_overruns())}
    try:
        import models_page
        t = models_page.review("24h")["totals"]
        sig.update(calls_24h=t["calls"], error_rate_24h=t["error_rate"], p95_ms_24h=t["p95_ms"])
    except Exception as e:                        # noqa: BLE001 — a readout
        sig["review"] = f"unavailable: {e}"
    return sig


def _empty_streak() -> int:
    n = 0
    for c in cycles(limit=EMPTY_CYCLES_ESCALATE + 1):
        if c["status"] == "complete" and c["verified"] == 0:
            n += 1
        else:
            break
    return n


# ── one cycle ────────────────────────────────────────────────────────────────

def cycle(trigger: str = "scheduled") -> dict:
    """Measure, propose, try, park, record. Never raises; never writes the live tree."""
    with _LOCK:
        if _STATE["running"]:
            return {"status": "busy", "note": _STATE["current"]}
        _STATE["running"] = True
    t0 = time.time()
    c = _conn()
    try:
        ch = anchor.charter() if hasattr(anchor, "charter") else {}
        stamp = ch.get("stamp", "") if isinstance(ch, dict) else ""
        cur = c.execute("INSERT INTO improve_cycles(ts, trigger, status, candidates, tried, verified, "
                        "rejected, note, seconds, baseline, signals, model, charter) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t0, trigger, "running", 0, 0, 0, 0, "", 0, "{}", "{}",
                         brain.brain_name(), stamp))
        cid = cur.lastrowid
        c.commit()
    finally:
        c.close()
    live_digest = _tree_digest()
    counts = {"candidates": 0, "tried": 0, "verified": 0, "rejected": 0}
    note, baseline, signals = "", {}, {}
    try:
        _STATE["current"] = "measuring"
        signals = _signals()
        _STATE["current"] = "proposing (the mechanic on Phoenix itself)"
        cands = _candidates()
        counts["candidates"] = len(cands)
        if cands:
            _STATE["current"] = "baseline: the suites on an unpatched copy"
            base_tree = copy_tree()
            try:
                baseline = _oracle(base_tree)
            finally:
                shutil.rmtree(os.path.dirname(base_tree), ignore_errors=True)
            ok_base, why_base = usable(baseline)
            if not ok_base:
                note = f"baseline unusable — {why_base}; nothing can be verified against it"
                cands = []
        for cand in cands[:MAX_TRIES]:
            counts["tried"] += 1
            _STATE["current"] = f"trying {cand['file']}: {cand['title'][:60]}"
            tree = copy_tree()
            try:
                why = apply_to_tree(tree, cand)
                after = _oracle(tree) if not why else {"green": False, "suites": {}}
            finally:
                shutil.rmtree(os.path.dirname(tree), ignore_errors=True)
            ok, reason = (False, f"could not apply to the copy: {why}") if why else verdict(after, baseline)
            status = "verified" if ok else "rejected"
            counts["verified" if ok else "rejected"] += 1
            c = _conn()
            try:
                c.execute("INSERT INTO improvements(cycle_id, ts, finding_id, title, file, severity, "
                          "category, patch, before_json, after_json, status, note, pr_url, actor, decided_ts) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, time.time(), cand.get("finding_id", ""), cand["title"], cand["file"],
                           cand.get("severity", ""), cand.get("category", ""), cand["patch"],
                           json.dumps(baseline), json.dumps(after), status, reason, "", "", 0))
                c.commit()
            finally:
                c.close()
            if not ok:
                # what failed becomes a lesson the record keeps: the next fixer prompt can cite it
                anchor.record(-1, "improve-rejected", f"{cand['file']}: {cand['title'][:80]} — {reason[:160]}")
        if not note:
            note = (f"{counts['verified']} verified and parked at the gate, {counts['rejected']} rejected"
                    if counts["tried"] else "no candidate patch to try")
        status = "complete"
    except Exception as e:                        # noqa: BLE001 — closed, not abandoned
        status, note = "halted", f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        _STATE.update(running=False, current="")
    if _tree_digest() != live_digest:
        status, note = "halted", "INVARIANT BROKEN: the live tree changed during the cycle — " + note
        anchor.record(-1, "escalation", "improve cycle: the live tree changed during a cycle; " + note)
    c = _conn()
    try:
        c.execute("UPDATE improve_cycles SET status=?, candidates=?, tried=?, verified=?, rejected=?, "
                  "note=?, seconds=?, baseline=?, signals=? WHERE id=?",
                  (status, counts["candidates"], counts["tried"], counts["verified"], counts["rejected"],
                   note, round(time.time() - t0, 1), json.dumps(baseline), json.dumps(signals), cid))
        c.commit()
    finally:
        c.close()
    if status == "complete" and counts["verified"] == 0 and _empty_streak() >= EMPTY_CYCLES_ESCALATE:
        anchor.record(-1, "escalation",
                      f"IMPROVEMENT STALLED — {EMPTY_CYCLES_ESCALATE} consecutive cycles verified nothing; "
                      f"last: {note[:120]}")
        try:
            anchor.msg_send("chief", "Chief Governor",
                            f"Self-improvement has produced nothing for {EMPTY_CYCLES_ESCALATE} cycles: "
                            f"{note[:160]}. The model, the suites or the candidates need a human look.")
        except Exception:                          # noqa: BLE001 — the escalation is already recorded
            pass
    return {"cycle_id": cid, "status": status, "note": note, "seconds": round(time.time() - t0, 1), **counts}


def _tree_digest() -> str:
    """A digest of every Python file in the live tree — the invariant a cycle must keep."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE)
        for fn in sorted(files):
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                try:
                    with open(p, "rb") as fh:
                        h.update(p.encode()); h.update(fh.read())
                except OSError:
                    pass
    return h.hexdigest()[:16]


# ── the gate ─────────────────────────────────────────────────────────────────

def approve(pid: int, actor: str = "console") -> dict:
    """A human approves a verified improvement. With a GITHUB_TOKEN the branch is
    pushed and a DRAFT pull request opened — merging remains a human act on GitHub.
    Without one, the patch is handed over for the human to apply."""
    p = proposal(pid)
    if not p:
        return {"ok": False, "error": "no such proposal"}
    if p["status"] != "verified":
        return {"ok": False, "error": f"proposal is {p['status']}, not verified"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("IMPROVE_REPO", "amonxnye/Phoenix").strip()
    if not token:
        _set(pid, status="approved", actor=actor, decided_ts=time.time(),
             note=p["note"] + " — approved; no GITHUB_TOKEN, apply the patch by hand")
        return {"ok": True, "status": "approved", "patch": p["patch"],
                "note": "approved — no GITHUB_TOKEN configured, so the patch is yours to apply"}
    try:
        import ghpr
        from mechanic import fixer
        with open(os.path.join(REPO, p["file"]), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        new, why = fixer.apply(src, fixer.parse(p["patch"]) or [])
        if new is None:
            _set(pid, status="stale", note=f"no longer applies to the live tree: {why}")
            return {"ok": False, "error": f"stale: {why}"}
        url = ghpr.open_pr(repo, token, branch=f"phoenix/improve-{pid}", files={p["file"]: new},
                           title=f"Self-improvement #{pid}: {p['title'][:70]}",
                           body=(f"Proposed by the Phoenix improvement cycle {p['cycle_id']} and verified: "
                                 f"{p['note']}.\n\nFinding: {p['title']}\nFile: `{p['file']}`\n\n"
                                 f"Suites after the patch: " +
                                 ", ".join(f"{n} {s['passed']}/{s['total']}" for n, s in (p["after_json"].get("suites") or {}).items()) +
                                 f"\n\nApproved at the console by {actor}. Merging is the human act."))
        _set(pid, status="proposed", pr_url=url, actor=actor, decided_ts=time.time())
        return {"ok": True, "status": "proposed", "pr_url": url}
    except Exception as e:                        # noqa: BLE001 — reported at the gate, not raised
        _set(pid, note=f"{p['note']} — PR failed: {type(e).__name__}: {str(e)[:160]}")
        return {"ok": False, "error": f"PR failed: {type(e).__name__}: {str(e)[:160]}"}


def reject(pid: int, reason: str = "", actor: str = "console") -> dict:
    p = proposal(pid)
    if not p:
        return {"ok": False, "error": "no such proposal"}
    _set(pid, status="rejected", note=f"rejected by {actor}: {reason or 'no reason given'}",
         actor=actor, decided_ts=time.time())
    anchor.record(-1, "improve-rejected", f"{p['file']}: {p['title'][:80]} — by {actor}: {reason[:120]}")
    return {"ok": True, "status": "rejected"}


# ── the scheduler ────────────────────────────────────────────────────────────

def enabled() -> bool:
    return os.environ.get("IMPROVE", "1").strip() != "0"


def due(now: float | None = None) -> bool:
    now = now or time.time()
    last = cycles(limit=1)
    return not last or (now - last[0]["ts"]) >= INTERVAL_S


def _loop():
    while True:
        try:
            _STATE["last_tick"] = time.time()
            if enabled() and due() and not _STATE["running"]:
                cycle("scheduled")
        except Exception:                          # noqa: BLE001 — the loop must outlive one bad cycle
            pass
        time.sleep(60)


def start() -> bool:
    if _STATE["thread"] and _STATE["thread"].is_alive():
        return False
    if not enabled():
        return False
    t = threading.Thread(target=_loop, daemon=True, name="phoenix-improve")
    t.start()
    _STATE["thread"] = t
    return True


def status() -> dict:
    last = cycles(limit=1)
    parked = proposals(status="verified")
    return {"enabled": enabled(), "running": _STATE["running"], "current": _STATE["current"],
            "thread": bool(_STATE["thread"] and _STATE["thread"].is_alive()),
            "interval_s": INTERVAL_S, "max_tries": MAX_TRIES,
            "last_cycle": last[0] if last else None,
            "next_due_in_s": max(0, int(INTERVAL_S - (time.time() - last[0]["ts"]))) if last else 0,
            "parked": len(parked), "empty_streak": _empty_streak(),
            "isolation": workspace.sandbox_mode(), "github": bool(os.environ.get("GITHUB_TOKEN", "").strip()),
            "waiting_cost": [{"id": p["id"], "title": p["title"],
                              "hours_waiting": round((time.time() - p["ts"]) / 3600, 1)} for p in parked]}

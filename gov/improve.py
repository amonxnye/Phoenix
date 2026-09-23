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
3. **Verified means proposed, never merged.** A verified improvement is pushed to its
   own branch and opened as a DRAFT pull request on its own (IMPROVE_AUTO_PR=1, with a
   GITHUB_TOKEN held by the orchestrator); the merge is the human act, on GitHub, with
   the diff and the suite delta in front of them. Without a token, or for a patch the
   suites cannot judge, the proposal waits at the console gate for a human instead.
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
import resource
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
import research                                  # noqa: E402
import workspace                                 # noqa: E402

SUITES = (("governor", "gov/verify.py"), ("work", "gov/verify_work.py"),
          ("settlement", "gov/verify_sim.py"), ("mechanic", "mechanic/verify_mechanic.py"))
INTERVAL_S = int(os.environ.get("IMPROVE_INTERVAL_S", str(3600)))       # hourly
AUTO_PR = os.environ.get("IMPROVE_AUTO_PR", "1").strip() != "0"          # verified → published
# Where a verified patch goes. Empty: its own branch and a DRAFT pull request (the merge
# is the human act). A branch name: committed straight onto that branch — if that is the
# branch the host deploys, the change is LIVE minutes later with no human having read
# it. The operator chose that; the record keeps the undo: every commit names its
# proposal and `revert` puts the file back with one click.
PUSH_BRANCH = os.environ.get("IMPROVE_PUSH_BRANCH", "").strip()
# What "improve" means. `world` (the operator's intent): knowledge from outside — cited,
# verified, researched — becomes developments the Board votes on and the human adopts.
# `code`: the mechanic's patches to Phoenix itself, verified by the suites. `both`.
MODE = os.environ.get("IMPROVE_MODE", "world").strip().lower()
MAX_TRIES = int(os.environ.get("IMPROVE_MAX_TRIES", "10"))  # candidates tried per cycle
SUITE_TIMEOUT_S = int(os.environ.get("IMPROVE_SUITE_TIMEOUT_S", "600"))
EMPTY_CYCLES_ESCALATE = 3
EXCLUDE = {"node_modules", "__pycache__", ".venv", "data", ".improve-data"}
# (the workspace `sandbox/` IS copied: the work suite is its oracle; its databases are not.
#  `.git` is copied when present — a few MB — so history facts read the same as live.)
_PASSED = re.compile(r"(\d+)/(\d+) checks passed")
_FAILED = re.compile(r"FAIL\S*\]\s+(.+?)(?:\s+—\s|$)")     # the suites' own FAIL lines, by name
VERIFIABLE = (".py",)         # what the suites exercise; anything else is parked, never "verified"

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
    have = {r[1] for r in c.execute("PRAGMA table_info(improvements)")}
    for col in ("original", "committed_sha"):        # the undo: what the file was, and the commit
        if col not in have:
            c.execute(f"ALTER TABLE improvements ADD COLUMN {col} TEXT DEFAULT ''")


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


def run_suites(tree: str, only: list | None = None) -> dict:
    """Phoenix's own verification suites on a tree, each in a stripped subprocess:
    no keys, no tokens, its own data directory inside the tree, no network where the
    platform allows. The verdict is the suites' own count line, plus the NAMES of the
    checks that failed — a flaky check is told apart from a real one by name, not by
    count. `only` re-runs a subset (the flake guard)."""
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
    if only is not None:
        want = [s for s in want if s[0] in only]
    out, green = {}, True
    for name, rel in want:
        t0 = time.time()
        try:
            def _limits():
                resource.setrlimit(resource.RLIMIT_CPU, (SUITE_TIMEOUT_S, SUITE_TIMEOUT_S))
                resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
                resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
                resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 ** 2, 512 * 1024 ** 2))
                resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
                os.setsid()
            proc = subprocess.run(prefix + [sys.executable, os.path.join(tree, rel)], cwd=tree,
                                  env=env, capture_output=True, text=True, timeout=SUITE_TIMEOUT_S,
                                  preexec_fn=_limits)
            text = (proc.stdout or "") + (proc.stderr or "")
            m = list(_PASSED.finditer(text))
            passed, total = (int(m[-1].group(1)), int(m[-1].group(2))) if m else (0, 0)
            ok = proc.returncode == 0 and total > 0 and passed == total
            tail = "\n".join(ln for ln in text.splitlines() if "FAIL" in ln or "Traceback" in ln
                             or "Error" in ln)[-1200:]
            failed = sorted({m.group(1).strip() for m in _FAILED.finditer(text)})
        except subprocess.TimeoutExpired:
            passed, total, ok, tail, failed = 0, 0, False, f"timed out after {SUITE_TIMEOUT_S}s", []
        except OSError as e:
            passed, total, ok, tail, failed = 0, 0, False, f"could not run: {e}", []
        out[name] = {"passed": passed, "total": total, "ok": ok, "failed": failed,
                     "seconds": round(time.time() - t0, 1), "tail": tail}
        green = green and ok
    return {"green": green, "suites": out, "isolation": _mode}


def _oracle(tree: str, only: list | None = None) -> dict:
    fn = ORACLE or run_suites
    if only is None:
        return fn(tree)
    try:
        return fn(tree, only=only)
    except TypeError:                              # a scripted oracle that takes no subset
        return fn(tree)


def verifiable(cand: dict) -> tuple[bool, str]:
    """Can the suites say anything about this patch? They exercise Python; a change to
    a manifest, a workflow or a document passes them without being tested at all, and
    "verified" would be a false claim. Such a patch is parked as UNVERIFIED — a human
    judges it — never as verified."""
    f = cand.get("file", "")
    if f.endswith(VERIFIABLE):
        return True, ""
    return False, f"the suites do not exercise {f}: parked unverified for a human to judge"


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


def new_failures(after: dict, before: dict) -> dict:
    """Per suite, the checks that fail on the patched copy and did not fail unpatched —
    by NAME. A check that fails both ways is the world's problem, not the patch's."""
    out = {}
    for n, b in (before.get("suites") or {}).items():
        s = (after.get("suites") or {}).get(n) or {}
        new = sorted(set(s.get("failed") or []) - set(b.get("failed") or []))
        if new:
            out[n] = new
    return out


def verdict(after: dict, before: dict) -> tuple[bool, str, dict]:
    """Measured against the baseline, suite by suite. A suite must have run. Then, by
    name: a check that fails on the patched copy and passed unpatched is a new failure
    and the patch is rejected naming it. Where a suite reports no names (a scripted
    oracle, a crash), the counts decide instead. The third value is the suites that
    would need a re-run to tell a flake from a real failure."""
    suspects = {}
    for n, b in (before.get("suites") or {}).items():
        s = (after.get("suites") or {}).get(n)
        if not s or not s.get("total"):
            return False, f"suite could not run on the patched copy: {n} — {(s or {}).get('tail', '')[-160:]}", {}
    named = new_failures(after, before)
    for n, names in named.items():
        suspects[n] = names
    for n, b in (before.get("suites") or {}).items():
        s = after["suites"][n]
        if not s.get("failed") and not b.get("failed") and s["passed"] < b.get("passed", 0):
            suspects[n] = [f"{s['passed']}/{s['total']} vs baseline {b['passed']}/{b['total']}"]
    if suspects:
        return False, "new failures: " + "; ".join(f"{n}: {', '.join(v)}" for n, v in suspects.items()), suspects
    return True, "no new failure in any suite: " + ", ".join(
        f"{n} {s['passed']}/{s['total']}" for n, s in after["suites"].items()), {}


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


def _already_proposed(cand: dict) -> str:
    """The same change must not be proposed again every hour. A proposal for the same
    file and finding that is waiting, open as a PR, approved, or rejected by a HUMAN
    blocks a repeat; one the suites rejected may be tried again (the flake guard and a
    new patch can change that verdict)."""
    for p in proposals(limit=1000):
        if p["file"] == cand.get("file") and p["title"] == cand.get("title"):
            if p["status"] in ("verified", "unverified", "proposed", "approved", "committed"):
                return f"already {p['status']} as #{p['id']}"
            if p["status"] == "rejected" and (p.get("note") or "").startswith("rejected by"):
                return f"rejected by a human as #{p['id']}"
    return ""


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
    """One improvement cycle in the configured MODE. Never raises."""
    with _LOCK:
        if _STATE["running"]:
            return {"status": "busy", "note": _STATE["current"]}
        _STATE["running"] = True
    if MODE in ("world", "both"):
        r = _world_cycle(trigger)
        if MODE == "world":
            _STATE.update(running=False, current="")
            return r
    return _code_cycle(trigger)


def _world_cycle(trigger: str) -> dict:
    """Ideas from outside: topics for this age → source read → citation checked →
    development proposed → researched → queued for the Board and the human."""
    import ideas
    t0 = time.time()
    c = _conn()
    try:
        ch = anchor.charter() if hasattr(anchor, "charter") else {}
        stamp = ch.get("stamp", "") if isinstance(ch, dict) else ""
        cur = c.execute("INSERT INTO improve_cycles(ts, trigger, status, candidates, tried, verified, "
                        "rejected, note, seconds, baseline, signals, model, charter) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t0, trigger, "running", 0, 0, 0, 0, "mode: world", 0, "{}", "{}",
                         brain.brain_name(), stamp))
        cid = cur.lastrowid
        c.commit()
    finally:
        c.close()
    counts = {"candidates": 0, "tried": 0, "verified": 0, "rejected": 0, "queued": 0}
    note, status = "", "complete"
    try:
        import sim
        w = sim.world()
        _STATE["current"] = f"choosing topics for the {w.get('age')}"
        # a topic read once is not read again; one that could NOT be read is tried again later
        already = {r["topic"] for r in ideas.rows(limit=1000) if r["status"] != "unread"}
        tops = ideas.topics(w, already, MAX_TRIES)
        counts["candidates"] = len(tops)
        situation = (f"The settlement is in the {w.get('age')} with food {w.get('food')}, wood {w.get('wood')}, "
                     f"gold {w.get('gold')}; the next leap costs {sim.advance_cost(w.get('age'))}.")
        for topic in tops:
            counts["tried"] += 1
            _STATE["current"] = f"reading about {topic!r} and checking the citation"
            r = ideas.consider(cid, topic, w, situation)
            if r["status"] == "queued":
                counts["verified"] += 1; counts["queued"] += 1
            elif r["status"] in ("unverified", "unread"):
                counts["rejected"] += 1
        note = (f"mode: world — {counts['tried']} topics read, {counts['queued']} sourced and researched "
                f"developments queued for the Board, {counts['rejected']} unread or uncited"
                if tops else "mode: world — every topic for this age has been read; nothing new to consider")
    except Exception as e:                        # noqa: BLE001 — closed, not abandoned
        status, note = "halted", f"{type(e).__name__}: {str(e)[:200]}"
    c = _conn()
    try:
        c.execute("UPDATE improve_cycles SET status=?, candidates=?, tried=?, verified=?, rejected=?, "
                  "note=?, seconds=? WHERE id=?",
                  (status, counts["candidates"], counts["tried"], counts["verified"], counts["rejected"],
                   note, round(time.time() - t0, 1), cid))
        c.commit()
    finally:
        c.close()
    return {"cycle_id": cid, "status": status, "note": note, "seconds": round(time.time() - t0, 1), **counts}


def _code_cycle(trigger: str = "scheduled") -> dict:
    """Measure, propose, try, park, record. Never raises; never writes the live tree."""
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
    to_publish: list[int] = []                   # published in ONE burst at the end, see below
    note, baseline, signals = "", {}, {}
    try:
        _STATE["current"] = "measuring"
        signals = _signals()
        _STATE["current"] = "proposing (the mechanic on Phoenix itself)"
        cands = _candidates()
        counts["candidates"] = len(cands)
        skipped = [(c, _already_proposed(c)) for c in cands]
        for c, why in skipped:
            if why:
                anchor.record(-1, "improve-skipped", f"{c['file']}: {c['title'][:80]} — {why}")
        cands = [c for c, why in skipped if not why]
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
            can, why_not = verifiable(cand)
            if research.protected(cand.get("file", "")):
                # Article XI.6: the research record, the rules and the researcher are out of
                # the agents' reach. Refused before any suite runs; recorded as a refusal.
                can, why_not = False, f"protected: agents may not change {cand['file']}"
                after, ok, reason, status = {"green": False, "suites": {}}, False, why_not, "refused"
                counts["refused"] = counts.get("refused", 0) + 1
                anchor.record(-1, "improve-refused", f"{cand['file']}: {cand['title'][:80]} — {why_not}")
            elif not can:
                after, ok, reason, status = {"green": False, "suites": {}}, False, why_not, "unverified"
                counts["unverified"] = counts.get("unverified", 0) + 1
            else:
                tree = copy_tree()
                try:
                    why = apply_to_tree(tree, cand)
                    after = _oracle(tree) if not why else {"green": False, "suites": {}}
                    ok, reason, suspects = ((False, f"could not apply to the copy: {why}", {}) if why
                                            else verdict(after, baseline))
                    if suspects and not why:
                        # The flake guard: a check that fails once under load and passes on a
                        # re-run of the same patched copy was never the patch's fault. Cycle 1
                        # on production rejected three sound patches on one flaky check.
                        _STATE["current"] = f"re-running {', '.join(suspects)} to tell a flake from a failure"
                        again = _oracle(tree, only=list(suspects))
                        for n, s in (again.get("suites") or {}).items():
                            after["suites"][n] = dict(s, first_run=after["suites"].get(n))
                        ok, reason2, still = verdict(after, baseline)
                        first = "; ".join(n + ": " + ", ".join(v) for n, v in suspects.items())
                        reason = (f"{reason2} — first run failed {first}, which passed on re-run "
                                  f"(flaky, not the patch)" if ok else f"{reason2} (confirmed on re-run)")
                finally:
                    shutil.rmtree(os.path.dirname(tree), ignore_errors=True)
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
            if not ok and status == "rejected":
                # what failed becomes a lesson the record keeps: the next fixer prompt can cite it
                anchor.record(-1, "improve-rejected", f"{cand['file']}: {cand['title'][:80]} — {reason[:160]}")
            if status == "verified":
                # Article XI.6: the research is the condition. Written first, from the measured
                # facts, into the append-only record. No research — no change, and it says so.
                row = proposals(limit=1)[0]
                _STATE["current"] = f"researching advantage and risk for #{row['id']}"
                rs = research.research(row, {"before": _suite_counts(baseline), "after": _suite_counts(after)})
                if not rs["ok"]:
                    _set(row["id"], status="unresearched", note=f"{row['note']} — NOT published: {rs['error']}")
                    counts["unresearched"] = counts.get("unresearched", 0) + 1
                    anchor.record(-1, "improve-unresearched", f"#{row['id']} {cand['file']}: {rs['error'][:160]}")
                elif AUTO_PR:
                    to_publish.append(row["id"])
        if not note:
            note = (f"{counts['verified']} verified ({counts.get('proposed', 0)} published with research), {counts['rejected']} rejected"
                    + (f", {counts['unresearched']} held back for want of research" if counts.get("unresearched") else "")
                    + (f", {counts['refused']} refused as protected" if counts.get("refused") else "")
                    + (f", {counts['unverified']} parked unverified (a human must judge)" if counts.get("unverified") else "")
                    if counts["tried"] else "no candidate patch to try")
        status = "complete"
    except Exception as e:                        # noqa: BLE001 — closed, not abandoned
        status, note = "halted", f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        _STATE["current"] = ""
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
    # Article XI.3, published as ONE burst after the cycle's record is closed. In branch
    # mode every commit makes the host rebuild and restart this process; publishing as
    # each patch is verified would kill the cycle at its first commit. Ten tries, one
    # burst, one rebuild — and the record already says what happened if the restart
    # lands mid-burst (a proposal left "verified" is re-published by `republish`).
    if status == "complete" and to_publish:
        _STATE["current"] = f"publishing {len(to_publish)} researched change(s) as one burst"
        for pid in to_publish:
            res = publish(pid, actor="cycle (Article XI.3)")
            counts["proposed"] = counts.get("proposed", 0) + (1 if res.get("ok") else 0)
        note = note.replace("(0 published with research)", f"({counts.get('proposed', 0)} published with research)")
        c = _conn()
        try:
            c.execute("UPDATE improve_cycles SET note=? WHERE id=?", (note, cid))
            c.commit()
        finally:
            c.close()
    _STATE.update(running=False, current="")
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


def _suite_counts(res: dict) -> dict:
    return {n: {"passed": s.get("passed"), "total": s.get("total")}
            for n, s in (res.get("suites") or {}).items()}


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
    """A human approves a proposal at the console gate — an unverified one, or a
    verified one that could not be published on its own. With a GITHUB_TOKEN the
    branch is pushed and a DRAFT pull request opened; without one the patch is
    handed over for the human to apply. Merging remains a human act on GitHub."""
    p = proposal(pid)
    if not p:
        return {"ok": False, "error": "no such proposal"}
    if p["status"] not in ("verified", "unverified"):
        return {"ok": False, "error": f"proposal is {p['status']}, not waiting at the gate"}
    if not os.environ.get("GITHUB_TOKEN", "").strip():
        _set(pid, status="approved", actor=actor, decided_ts=time.time(),
             note=p["note"] + " — approved; no GITHUB_TOKEN, apply the patch by hand")
        return {"ok": True, "status": "approved", "patch": p["patch"],
                "note": "approved — no GITHUB_TOKEN configured, so the patch is yours to apply"}
    return publish(pid, actor)


def publish(pid: int, actor: str) -> dict:
    """Send a proposal to GitHub. With IMPROVE_PUSH_BRANCH: one commit straight onto
    that branch (status `committed`, the undo kept). Otherwise: its own branch and a
    DRAFT pull request (status `proposed`). Never merges. A failure leaves the proposal
    at the gate with the reason in its note."""
    p = proposal(pid)
    if not p:
        return {"ok": False, "error": "no such proposal"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("IMPROVE_REPO", "amonxnye/Phoenix").strip()
    if not token:
        return {"ok": False, "error": "no GITHUB_TOKEN — waiting at the console gate"}
    if PUSH_BRANCH:
        return _commit_to_branch(p, repo, token, actor)
    try:
        import ghpr
        from mechanic import fixer
        with open(os.path.join(REPO, p["file"]), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        new, why = fixer.apply(src, fixer.parse(p["patch"]) or [])
        if new is None:
            _set(pid, status="stale", note=f"no longer applies to the live tree: {why}")
            return {"ok": False, "error": f"stale: {why}"}
        entry = research.for_proposal(pid)
        if entry is None:
            return {"ok": False, "error": "no research entry — Article XI.6 forbids publishing without one"}
        url = ghpr.open_pr(repo, token, branch=f"phoenix/improve-{pid}",
                           files={research.FILE: research.render(), p["file"]: new},
                           title=f"Self-improvement #{pid}: {p['title'][:70]}",
                           body=(f"Proposed by the Phoenix improvement cycle {p['cycle_id']} and verified: "
                                 f"{p['note']}.\n\nFinding: {p['title']}\nFile: `{p['file']}`\n\n"
                                 f"Suites after the patch: " +
                                 ", ".join(f"{n} {s['passed']}/{s['total']}" for n, s in (p["after_json"].get("suites") or {}).items()) +
                                 f"\n\nResearch R{entry['id']} (RESEARCH.md): advantage — {entry['body'].get('advantage', '')[:300]} "
                                 f"/ risk — {entry['body'].get('risk', '')[:300]}"
                                 f"\n\nOpened by: {actor}. Verdict: {p['status']}. Merging is the human act."))
        _set(pid, status="proposed", pr_url=url, actor=actor, decided_ts=time.time())
        return {"ok": True, "status": "proposed", "pr_url": url}
    except Exception as e:                        # noqa: BLE001 — reported at the gate, not raised
        _set(pid, note=f"{p['note']} — PR failed: {type(e).__name__}: {str(e)[:160]}")
        return {"ok": False, "error": f"PR failed: {type(e).__name__}: {str(e)[:160]}"}


def _commit_to_branch(p: dict, repo: str, token: str, actor: str) -> dict:
    """The patch, applied to the file AS IT IS ON THE BRANCH (not the local tree — the
    branch may be ahead), committed with the proposal's number in the message. The
    pre-patch content is kept for `revert`."""
    try:
        import ghpr
        from mechanic import fixer
        cur, sha = ghpr.get_file(repo, token, PUSH_BRANCH, p["file"])
        if cur is None:
            _set(p["id"], status="stale", note=f"{p['file']} is not on {PUSH_BRANCH}")
            return {"ok": False, "error": f"stale: {p['file']} is not on {PUSH_BRANCH}"}
        new, why = fixer.apply(cur, fixer.parse(p["patch"]) or [])
        if new is None:
            _set(p["id"], status="stale", note=f"no longer applies on {PUSH_BRANCH}: {why}")
            return {"ok": False, "error": f"stale: {why}"}
        entry = research.for_proposal(p["id"])
        if entry is None:
            return {"ok": False, "error": "no research entry — Article XI.6 forbids publishing without one"}
        # The research goes first. The branch's RESEARCH.md must end exactly where the record
        # says it should; a file that differs was edited or diverged, and the cycle stops.
        cur_r, sha_r = ghpr.get_file(repo, token, PUSH_BRANCH, research.FILE)
        want_prefix = research.expected_tail(entry["id"]).rstrip()
        if cur_r is not None and cur_r.rstrip() and cur_r.rstrip() != want_prefix and not want_prefix.startswith(cur_r.rstrip()):
            anchor.record(-1, "escalation", f"{research.FILE} on {PUSH_BRANCH} does not match the research record — "
                                            f"not appending; a human must reconcile it")
            _set(p["id"], note=f"{p['note']} — {research.FILE} on {PUSH_BRANCH} differs from the record: "
                               f"refused to append; a human must reconcile it")
            return {"ok": False, "error": f"{research.FILE} on {PUSH_BRANCH} differs from the record — refused to append"}
        ghpr.commit_file(repo, token, PUSH_BRANCH, research.FILE, research.render(),
                         f"Research R{entry['id']} for self-improvement #{p['id']}: {p['title'][:60]}\n\n"
                         f"Advantage: {entry['body'].get('advantage', '')[:400]}\n\nRisk: {entry['body'].get('risk', '')[:400]}",
                         expect_sha=sha_r)
        suites = ", ".join(f"{n} {s['passed']}/{s['total']}" for n, s in (p["after_json"].get("suites") or {}).items())
        msg = (f"Self-improvement #{p['id']}: {p['title'][:70]}\n\n{p['note'][:300]}\n\n"
               f"Research: R{entry['id']} in {research.FILE} (advantage, risk, blast radius, detection, undo).\n"
               f"File: {p['file']}\nSuites on the patched copy: {suites}\n"
               f"Committed by: {actor} (Article XI.3). Undo: /improve → revert #{p['id']}.")
        r = ghpr.commit_file(repo, token, PUSH_BRANCH, p["file"], new, msg, expect_sha=sha)
        _set(p["id"], status="committed", pr_url=r.get("url", ""), committed_sha=r.get("sha", ""),
             original=cur, actor=actor, decided_ts=time.time())
        anchor.record(-1, "improve-committed", f"#{p['id']} {p['file']}: {p['title'][:80]} → {PUSH_BRANCH} {r.get('sha', '')[:10]}")
        return {"ok": True, "status": "committed", "pr_url": r.get("url", ""), "sha": r.get("sha", "")}
    except Exception as e:                        # noqa: BLE001 — reported at the gate, not raised
        _set(p["id"], note=f"{p['note']} — commit to {PUSH_BRANCH} failed: {type(e).__name__}: {str(e)[:160]}")
        return {"ok": False, "error": f"commit failed: {type(e).__name__}: {str(e)[:160]}"}


def revert(pid: int, actor: str = "console") -> dict:
    """The undo for a committed improvement: put the file back to what it was, as one
    commit that names the proposal. Refused if the file has changed since — a later
    edit is not this proposal's to undo."""
    p = proposal(pid)
    if not p:
        return {"ok": False, "error": "no such proposal"}
    if p["status"] != "committed":
        return {"ok": False, "error": f"proposal is {p['status']}, nothing to revert"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("IMPROVE_REPO", "amonxnye/Phoenix").strip()
    branch = PUSH_BRANCH
    if not token or not branch:
        return {"ok": False, "error": "no GITHUB_TOKEN / IMPROVE_PUSH_BRANCH"}
    try:
        import ghpr
        from mechanic import fixer
        cur, sha = ghpr.get_file(repo, token, branch, p["file"])
        expected, _ = fixer.apply(p.get("original") or "", fixer.parse(p["patch"]) or [])
        if cur is None or (expected is not None and cur.rstrip() != expected.rstrip()):
            return {"ok": False, "error": f"{p['file']} has changed on {branch} since the commit — revert by hand"}
        r = ghpr.commit_file(repo, token, branch, p["file"], p.get("original") or "",
                             f"Revert self-improvement #{p['id']}: {p['title'][:60]}\n\nReverted by {actor}.", expect_sha=sha)
        _set(p["id"], status="reverted", actor=actor, decided_ts=time.time(),
             note=f"{p['note']} — reverted by {actor}: {r.get('sha', '')[:10]}")
        anchor.record(-1, "improve-reverted", f"#{p['id']} {p['file']} by {actor}")
        return {"ok": True, "status": "reverted", "url": r.get("url", "")}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": f"revert failed: {type(e).__name__}: {str(e)[:160]}"}


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
    try:
        republish()
    except Exception:                              # noqa: BLE001 — a boot chore, never a crash
        pass
    while True:
        try:
            _STATE["last_tick"] = time.time()
            if enabled() and due() and not _STATE["running"]:
                cycle("scheduled")
        except Exception:                          # noqa: BLE001 — the loop must outlive one bad cycle
            pass
        time.sleep(60)


def reap() -> int:
    """A cycle still marked running at boot was cut off by a restart — a deploy, most
    likely one this loop caused. Marked interrupted, never left looking alive."""
    c = _conn()
    try:
        cur = c.execute("UPDATE improve_cycles SET status='interrupted', "
                        "note='the process restarted during the cycle (a deploy?) — ' || note "
                        "WHERE status='running'")
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def republish(actor: str = "boot") -> int:
    """Researched, verified proposals that a restart left unpublished go out now."""
    if not (AUTO_PR and os.environ.get("GITHUB_TOKEN", "").strip()):
        return 0
    n = 0
    for p in proposals(status="verified"):
        if research.for_proposal(p["id"]) is not None and publish(p["id"], actor).get("ok"):
            n += 1
    return n


def start() -> bool:
    if _STATE["thread"] and _STATE["thread"].is_alive():
        return False
    reap()
    if not enabled():
        return False
    t = threading.Thread(target=_loop, daemon=True, name="phoenix-improve")
    t.start()
    _STATE["thread"] = t
    return True


def _ideas_summary() -> dict:
    import ideas
    return ideas.summary()


def status() -> dict:
    last = cycles(limit=1)
    parked = proposals(status="verified") + proposals(status="unverified")
    return {"enabled": enabled(), "running": _STATE["running"], "current": _STATE["current"],
            "thread": bool(_STATE["thread"] and _STATE["thread"].is_alive()),
            "interval_s": INTERVAL_S, "max_tries": MAX_TRIES,
            "last_cycle": last[0] if last else None,
            "next_due_in_s": max(0, int(INTERVAL_S - (time.time() - last[0]["ts"]))) if last else 0,
            "parked": len(parked), "empty_streak": _empty_streak(),
            "push_branch": PUSH_BRANCH, "auto_pr": AUTO_PR, "research": research.verify_chain(),
            "mode": MODE, "ideas": _ideas_summary(),
            "token_required": bool(os.environ.get("CONSOLE_TOKEN", "").strip()),
            "isolation": workspace.sandbox_mode(), "github": bool(os.environ.get("GITHUB_TOKEN", "").strip()),
            "waiting_cost": [{"id": p["id"], "title": p["title"],
                              "hours_waiting": round((time.time() - p["ts"]) / 3600, 1)} for p in parked]}

"""The Security world — governed defensive hardening (SECURITY.md).

Where ``sim.py``'s world is resources and ages and ``workspace.py``'s world is a test
suite, this world is a sandboxed replica and the invariants it must never violate.
The safety law survives the translation intact:

    a reproducing proof-of-concept is the oracle; anything outside the sandbox is the gate.

- ``reproduce()`` runs a PoC in ``pocrunner.py`` — no network, no subprocess, no write
  outside the sandbox — and the verdict comes from ``replica/invariants.py``, never
  from what the PoC or the agent claims. An unreproduced finding earns nothing.
- A finding must reproduce **twice, identically**. A flaky exploit is refuted by the
  oracle before any model is asked for an opinion.
- ``apply_patch`` writes only inside the replica and REFUSES ``invariants.py``, the
  functional tests, and the PoC corpus — an agent cannot pass its exam by editing the
  exam, in either direction (weakening an invariant or blunting its own PoC).
- A fix is only a fix when the PoC stops reproducing **and** the functional suite is
  still green: hardening that deletes the feature is not hardening.
- ``gate_request`` parks everything irreversible — disclosure, exporting a dossier,
  touching anything outside the sandbox — for a human (Article IV). There is no code
  path that performs those acts on approval, because the capability does not exist in
  this box; approval releases the *dossier*, and a person acts.

Scope is code, not documentation: ``SCOPE`` below is the entire authorized target set,
and every finding carries it.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.path.dirname(_HERE), "sandbox")
REPLICA = os.path.join(SANDBOX, "replica")
POCS_DIR = "pocs"
TESTS_DIR = "tests"
ORACLE_FILE = "invariants.py"

# ── authorization, declared as code (SECURITY.md §1) ──────────────────────────
SCOPE = {
    "target": "sandbox/replica",
    "authorization": "owned by this repository — a local replica with no credentials "
                     "and no network route out of the box",
    "declared_in": "SECURITY.md §1",
    "excludes": "every live system, every third party, every host not listed here",
}

VISION = ("No unfixed findings in the hardening checklist for sandbox/replica; "
          "every class closed by a proof-of-concept that reproduced before the patch "
          "and does not reproduce after it.")

# Severity is a property of the class, assigned here — never a number an agent claims.
SEVERITY = {"rce": "critical", "sqli": "high", "traversal": "high", "authz": "medium"}

REPRO_TIMEOUT_S = 30


def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return _HERE


DB = os.path.join(_data_dir(), "redteam.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def classes() -> list[str]:
    """The hardening checklist, read from the oracle file — never from an agent."""
    src = read_file(ORACLE_FILE)
    m = re.search(r"^CLASSES\s*=\s*\[(.*?)\]", src, re.M | re.S)
    return re.findall(r"[\"'](\w+)[\"']", m.group(1)) if m else []


def titles() -> dict[str, str]:
    src = read_file(ORACLE_FILE)
    m = re.search(r"^TITLES\s*=\s*\{(.*?)\n\}", src, re.M | re.S)
    return dict(re.findall(r"[\"'](\w+)[\"']\s*:\s*[\"'](.+?)[\"']", m.group(1))) if m else {}


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS findings("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, class TEXT, title TEXT, "
                  "severity TEXT, status TEXT DEFAULT 'reproduced', found_by TEXT, "
                  "fixed_by TEXT DEFAULT '', poc TEXT, evidence TEXT, "
                  "scope TEXT, note TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS gates("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id INTEGER, kind TEXT, "
                  "status TEXT DEFAULT 'pending', requested_by TEXT, "
                  "detail TEXT, decided_by TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS pristine(path TEXT PRIMARY KEY, content TEXT)")
        for name in sorted(os.listdir(REPLICA)):
            if name.endswith(".py"):
                with open(os.path.join(REPLICA, name)) as f:
                    c.execute("INSERT OR IGNORE INTO pristine(path, content) VALUES(?,?)",
                              (name, f.read()))
        c.commit()
    finally:
        c.close()
    os.makedirs(os.path.join(REPLICA, POCS_DIR), exist_ok=True)


def reset_replica() -> list[str]:
    """Restore the replica to its first-seen (vulnerable) state and reopen every
    finding. The PoC corpus survives — a proven exploit is an asset, not a scratch file."""
    c = _conn()
    try:
        rows = c.execute("SELECT path, content FROM pristine").fetchall()
        c.execute("UPDATE findings SET status='reproduced', fixed_by='' "
                  "WHERE status='fixed'")
        c.commit()
    finally:
        c.close()
    restored = []
    for path, content in rows:
        with open(os.path.join(REPLICA, path), "w") as f:
            f.write(content)
        restored.append(path)
    shutil.rmtree(os.path.join(REPLICA, "__pycache__"), ignore_errors=True)
    return restored


# ── the oracles ───────────────────────────────────────────────────────────────

def _clean_env() -> dict:
    """No keys, no tokens, no proxies — the child inherits nothing worth stealing."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": REPLICA, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


def functional() -> dict:
    """The behaviour oracle: does the service still work? A patch that breaks this is
    not a fix, it is a demolition."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", TESTS_DIR, "-v"],
        cwd=REPLICA, env=_clean_env(), capture_output=True, text=True, timeout=120)
    out = proc.stderr + proc.stdout
    passed, failures = 0, []
    for m in re.finditer(r"^(test_\w+) \([^)]*\) \.\.\. (\w+)", out, re.M):
        if m.group(2) == "ok":
            passed += 1
        else:
            failures.append(m.group(1))
    return {"passed": passed, "failed": len(failures),
            "total": passed + len(failures), "failures": sorted(failures)}


def _run_poc(poc_rel: str) -> dict:
    """One sandboxed execution. The verdict is the invariants', not the PoC's."""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(_HERE, "pocrunner.py"),
             os.path.join(REPLICA, poc_rel)],
            cwd=REPLICA, env=_clean_env(), capture_output=True, text=True,
            timeout=REPRO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"reproduced": False, "violations": [], "denied": [], "requests": 0,
                "error": f"PoC exceeded {REPRO_TIMEOUT_S}s and was killed"}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__POC__ "):
            return json.loads(line[len("__POC__ "):])
    return {"reproduced": False, "violations": [], "denied": [], "requests": 0,
            "error": ("the PoC produced no verdict: "
                      + (proc.stderr or proc.stdout or "no output").strip()[-300:])}


def poc_is_honest(source: str) -> tuple[bool, str]:
    """A PoC must *provoke* the flaw through the request surface, not manufacture the
    evidence for it.

    The invariants read a trace; a PoC with a reference to the service's internals
    could write that trace itself, or edit the credential store it is judged against,
    and file a finding for a vulnerability that does not exist. So the exploit may
    touch exactly one thing on the service it is handed — ``handle()`` — and may not
    import the replica's own modules. Static, cheap, and checked before the PoC runs.
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"the PoC does not parse: {e}"
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "exploit"), None)
    if fn is None:
        return False, "the PoC defines no exploit(vault) function"
    if not fn.args.args:
        return False, "exploit() must take the service as its argument"
    target = fn.args.args[0].arg
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, "names", [])]
            names.append(getattr(node, "module", "") or "")
            for name in names:
                if name.split(".")[0] in ("vault", "invariants", "pocrunner"):
                    return False, (f"REFUSED: the PoC imports {name!r} — evidence must "
                                   f"come from the service's responses, not its internals")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == target and node.attr != "handle":
            return False, (f"REFUSED: the PoC reaches into {target}.{node.attr} — an "
                           f"exploit goes through handle(), or it is not an exploit")
    return True, "the PoC goes through the request surface"


def reproduce(poc_rel: str) -> dict:
    """Run a stored PoC twice and demand the same verdict both times.

    Tier 1 of the oracle (SECURITY.md §3): no repro → not a finding. Tier 2's first and
    cheapest refutations are structural — a PoC that fabricates its evidence, or one
    that only sometimes fires, is killed here before a single model token is spent.
    """
    honest, why = poc_is_honest(read_file(poc_rel))
    if not honest:
        return {"reproduced": False, "flaky": False, "violations": [], "denied": [],
                "dishonest": True, "error": why}
    first, second = _run_poc(poc_rel), _run_poc(poc_rel)
    ids1 = sorted({v["id"] for v in first["violations"]})
    ids2 = sorted({v["id"] for v in second["violations"]})
    if ids1 != ids2:
        return {"reproduced": False, "flaky": True, "dishonest": False, "violations": [],
                "denied": sorted(set(first["denied"]) | set(second["denied"])),
                "error": f"non-deterministic: {ids1 or 'nothing'} then {ids2 or 'nothing'}"}
    out = dict(first)
    out["flaky"] = False
    out["dishonest"] = False
    out["classes"] = sorted({v["class"] for v in first["violations"]})
    return out


def regression() -> list[dict]:
    """Re-run the PoC of every closed finding. A finding that reproduces again has
    regressed — the corpus is the permanent regression suite."""
    out = []
    for f in findings():
        if f["status"] != "fixed" or not f["poc"]:
            continue
        v = reproduce(f["poc"])
        out.append({"id": f["id"], "class": f["class"], "regressed": v["reproduced"]})
    return out


def world() -> dict:
    """The security world-state — the same shape of truth the other worlds report."""
    cs = classes()
    fs = findings()
    fixed = {f["class"] for f in fs if f["status"] == "fixed"}
    found = {f["class"] for f in fs if f["status"] in ("reproduced", "fixed")}
    fn = functional()
    return {"classes_total": len(cs), "classes_found": len(found & set(cs)),
            "classes_fixed": len(fixed & set(cs)),
            "open_findings": sum(1 for f in fs if f["status"] == "reproduced"),
            "refuted": sum(1 for f in fs if f["status"] == "refuted"),
            "functional_passing": fn["passed"], "functional_total": fn["total"],
            "progress_pct": round(100 * len(fixed & set(cs)) / len(cs)) if cs else 0,
            "uncovered": sorted(set(cs) - found)}


# ── findings ──────────────────────────────────────────────────────────────────

def write_poc(vuln_class: str, source: str, agent: str) -> str:
    """Store a PoC in the corpus and return its sandbox-relative path. PoCs live inside
    the replica so the runner's write-fence covers them too."""
    d = os.path.join(REPLICA, POCS_DIR)
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"\W+", "_", f"{vuln_class}_{agent}")[:40]
    n = 1
    while os.path.exists(os.path.join(d, f"poc_{safe}_{n}.py")):
        n += 1
    rel = os.path.join(POCS_DIR, f"poc_{safe}_{n}.py")
    with open(os.path.join(REPLICA, rel), "w") as f:
        f.write(source)
    return rel


def record_finding(vuln_class: str, agent: str, poc_rel: str, verdict: dict) -> int:
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO findings(class, title, severity, status, found_by, poc, "
            "evidence, scope) VALUES(?,?,?,?,?,?,?,?)",
            (vuln_class, titles().get(vuln_class, vuln_class),
             SEVERITY.get(vuln_class, "unknown"), "reproduced", agent, poc_rel,
             json.dumps(verdict.get("violations", []))[:4000], json.dumps(SCOPE)))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def set_status(finding_id: int, status: str, agent: str = "", note: str = "") -> None:
    c = _conn()
    try:
        c.execute("UPDATE findings SET status=?, fixed_by=CASE WHEN ?='fixed' THEN ? "
                  "ELSE fixed_by END, note=COALESCE(NULLIF(?,''), note) WHERE id=?",
                  (status, status, agent, note, finding_id))
        c.commit()
    finally:
        c.close()


def findings(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = ("SELECT id, class, title, severity, status, found_by, fixed_by, poc, "
             "evidence, scope, note FROM findings")
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        rows = c.execute(q + " ORDER BY id", args).fetchall()
    finally:
        c.close()
    keys = ("id", "class", "title", "severity", "status", "found_by", "fixed_by",
            "poc", "evidence", "scope", "note")
    return [dict(zip(keys, r)) for r in rows]


def finding(finding_id: int) -> dict | None:
    return next((f for f in findings() if f["id"] == finding_id), None)


# ── the write path into the replica ───────────────────────────────────────────

def read_file(rel_path: str) -> str:
    with open(_safe_path(rel_path)) as f:
        return f.read()


def apply_patch(rel_path: str, content: str, actor: str = "") -> tuple[bool, str]:
    """Write a file inside the replica. Refuses anything outside it, the invariants,
    the functional tests, and the PoC corpus — every artefact an agent could edit to
    make itself look successful instead of being successful.

    A refusal is a detection signal, not just a return value: when ``actor`` is given,
    the Sentinel is told (SECURITY.md §6.5), so an attempt to edit the oracle or escape
    scope shows up on the watch layer whether or not the caller does anything with it.
    """
    def _refuse(msg: str) -> tuple[bool, str]:
        if actor:
            kind = _sentinel_refusal_kind(msg)
            if kind:
                _emit(actor, kind, detail=msg, target=rel_path)
        return False, msg

    try:
        p = _safe_path(rel_path)
    except ValueError as e:
        return _refuse(str(e))
    rel = os.path.relpath(p, REPLICA)
    head = rel.split(os.sep)[0]
    if head == ORACLE_FILE:
        return _refuse("REFUSED: the invariants are the oracle and are not writable")
    if head == TESTS_DIR:
        return _refuse("REFUSED: the behaviour tests are not writable")
    if head == POCS_DIR:
        return _refuse("REFUSED: the PoC corpus is evidence and is not patchable")
    with open(p, "w") as f:
        f.write(content)
    return True, f"wrote {rel} ({len(content)} bytes)"


# ── Sentinel wiring (best-effort: detection must never break the work path) ───

def _emit(actor: str, kind: str, detail: str = "", target: str = "") -> None:
    try:
        import sentinel
        sentinel.emit(actor, kind, detail=detail, target=target)
    except Exception:
        pass


def _sentinel_refusal_kind(message: str) -> str:
    try:
        import sentinel
        return sentinel.refusal_kind(message)
    except Exception:
        return ""


def _safe_path(rel_path: str) -> str:
    p = os.path.realpath(os.path.join(REPLICA, rel_path))
    root = os.path.realpath(REPLICA)
    if p != root and not p.startswith(root + os.sep):
        raise ValueError(f"REFUSED: {rel_path} is outside the authorized scope "
                         f"({SCOPE['target']})")
    return p


def in_scope(target: str) -> bool:
    """The authorization check. It is belt to the sandbox's braces: the capability to
    reach anything else does not exist, and this refuses to pretend otherwise."""
    try:
        _safe_path(target)
        return True
    except ValueError:
        return False


# ── the gate (Article IV) ─────────────────────────────────────────────────────

GATED = {
    "disclose": "notifying a vendor, filing a CVE, or publishing a finding",
    "export": "moving a finding dossier off this box",
    "deploy": "applying a patch to a production system",
    "live-test": "running anything against a system outside the sandbox",
}


def dossier(finding_id: int) -> dict:
    """Everything a human needs to decide, in one object: the scope it was produced
    under, the evidence the oracle recorded, the PoC, the fix, and the lineage."""
    f = finding(finding_id)
    if not f:
        return {"error": f"no finding {finding_id}"}
    poc_src = ""
    if f["poc"]:
        try:
            poc_src = read_file(f["poc"])
        except OSError:
            poc_src = "(PoC file missing)"
    return {
        "finding": f["id"], "class": f["class"], "title": f["title"],
        "severity": f["severity"], "status": f["status"],
        "scope": json.loads(f["scope"]) if f["scope"] else SCOPE,
        "authorization": SCOPE["authorization"],
        "found_by": f["found_by"], "fixed_by": f["fixed_by"],
        "oracle_evidence": json.loads(f["evidence"]) if f["evidence"] else [],
        "proof_of_concept": poc_src,
        "regression": ("the PoC reproduces before the patch and does not after"
                       if f["status"] == "fixed" else "not yet closed"),
        "note": f["note"],
        "requires_human_approval": True,
        "why": "Every use of this dossier outside the sandbox is irreversible "
               "(SECURITY.md §4). Nothing in this process can perform one.",
    }


def gate_request(finding_id: int, kind: str, agent: str, detail: str = "") -> dict:
    """Park an irreversible act for a human. This is the only way an agent can ask;
    there is no way for it to proceed."""
    if kind not in GATED:
        return {"ok": False, "error": f"unknown gated act {kind!r}"}
    c = _conn()
    try:
        cur = c.execute("INSERT INTO gates(finding_id, kind, requested_by, detail) "
                        "VALUES(?,?,?,?)", (finding_id, kind, agent, detail[:500]))
        c.commit()
        gid = cur.lastrowid
    finally:
        c.close()
    _emit(agent, "gate.requested", detail=f"{kind}: {GATED[kind]}",
          target=f"finding:{finding_id}")
    return {"ok": True, "gate": gid, "status": "pending",
            "act": GATED[kind], "decided_by": "a human being, Article IV"}


def gate_decide(gate_id: int, decision: str, who: str = "operator") -> dict:
    if decision not in ("approve", "reject"):
        return {"ok": False, "error": "decision must be approve or reject"}
    c = _conn()
    try:
        cur = c.execute("UPDATE gates SET status=?, decided_by=? WHERE id=? "
                        "AND status='pending'",
                        ("approved" if decision == "approve" else "rejected", who, gate_id))
        c.commit()
        return {"ok": cur.rowcount > 0, "gate": gate_id, "decision": decision}
    finally:
        c.close()


def gates(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = "SELECT id, finding_id, kind, status, requested_by, detail, decided_by FROM gates"
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        rows = c.execute(q + " ORDER BY id DESC", args).fetchall()
    finally:
        c.close()
    keys = ("id", "finding_id", "kind", "status", "requested_by", "detail", "decided_by")
    return [dict(zip(keys, r)) for r in rows]

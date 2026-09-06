"""Posture — the repository as a SYSTEM, not as a list of units.

A unit-by-unit reading finds an unescaped value on line 40. A security reviewer
finds that the console's authentication is optional, that request bodies have no
ceiling, that the "sandbox" is a subprocess, that half the dependencies float and
that `.gitignore` would not stop a `.env` from being committed — and then says, in one
sentence, whether the thing may face the Internet. That verdict is what an operator
reads first, and it is what this module produces: repository-wide text facts, each
with an ASSESSMENT (what the finding means for exposure), and a list of what HELD UP,
so the reader knows what was examined and passed, not only what failed.

Every check here is a text fact with a file and a line (Charter §2). Where a check is
about absence — no rlimit anywhere in the file that runs the agent's code — the
finding names exactly what was looked for and not found, so the reader can refute it
by pointing at the line the scan missed.
"""

import os
import re

from . import polyglot

FIX_BEFORE_EXPOSURE = "Fix before Internet exposure"
FIX_BEFORE_AUTONOMY = "Fix before real autonomous coding"
DOS = "DoS risk"
XSS_TOKEN = "XSS could compromise the operator token"
SUPPLY_CHAIN = "Supply-chain / reproducibility risk"
SECRET_LEAK = "Secret-leak prevention gap"
ROTATE = "Rotate the credential and remove the file"

MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml",
             "Gemfile", "composer.json")
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb")
ISOLATION = ("setrlimit", "RLIMIT_", "prlimit", "chroot", "unshare -m", "--mount", "bwrap",
             "nsjail", "firejail", "docker", "podman", "seccomp", "gvisor", "runsc",
             "setuid", "--uid", "landlock")
_CRED_FILES = re.compile(r"^(\.env(\.\w+)?|.*\.(pem|key|p12|pfx|keystore|jks)|id_rsa|id_ed25519|id_ecdsa)$")
_CRED_SAMPLES = re.compile(r"\.(example|sample|template|dist)$|\.pub$")

_ENV_TOKEN_PY = re.compile(r"\b(\w+)\s*=\s*os\.(?:environ\.get|getenv)\(\s*[\"']"
                           r"(\w*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|AUTH)\w*)[\"']")
_ENV_TOKEN_JS = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*process\.env\."
                           r"(\w*(?:TOKEN|SECRET|PASSWORD|APIKEY|API_KEY|AUTH)\w*)\b")
_DEPLOYED = re.compile(r"0\.0\.0\.0|environ\.get\(\s*[\"']PORT[\"']|process\.env\.PORT")
_BODY_PY = re.compile(r"Content-Length")
_BODY_READ_PY = re.compile(r"rfile\.read\(")
_BOUNDED = re.compile(r"\bmin\(|MAX|LIMIT|CEILING|\bcap\b|\b413\b|[<>]=?\s*\d|too large", re.I)
_BODY_JS = re.compile(r"(?:express|bodyParser)\.(?:json|urlencoded|raw|text)\(([^)]*)\)")
_BODY_STREAM_JS = re.compile(r"\.on\(\s*[\"']data[\"']")
_BOUNDED_JS = re.compile(r"length|size|MAX|LIMIT|destroy|413", re.I)
_STORAGE = re.compile(r"(?:localStorage|sessionStorage)\.(?:setItem|getItem)\(\s*[\"']([^\"']*)[\"']")
_STORAGE_KEY = re.compile(r"tok|auth|secret|session|jwt|key|pass|cred", re.I)
_ESCAPER = re.compile(r"replace\(\s*/\[([^\]]*)\]/g")
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(==|>=|<=|~=|!=|>|<|===)?")
_SUBPROC_PY = re.compile(r"subprocess\.(?:run|Popen|call|check_output|check_call)\(")
_SUBPROC_JS = re.compile(r"child_process|\b(?:spawn|execFile|fork)\(")
_INTERPRETER = re.compile(r"sys\.executable|process\.execPath|[\"'](?:python3?|node|deno|bun|bash|sh)[\"']")
_SANDBOX_WORDS = re.compile(r"sandbox|untrusted|agent|workspace|generated", re.I)
# Inbound authentication compares the secret with something the CLIENT sent. A key
# read for an outbound call (BRAIN_API_KEY → "is a model configured?") is not a gate.
_INBOUND = re.compile(r"headers|header\(|request\.|req\.|cookie|authorization|bearer", re.I)


def _finding(unit_file, line, end, kind, title, why, fix, severity, assessment,
             category="security", confidence=0.9):
    return {
        "unit": "", "file": unit_file, "symbol": f"posture:{kind}",
        "line_range": f"{line}-{end}", "category": category, "severity": severity,
        "confidence": confidence, "basis": "machine-verified", "title": title,
        "description": why, "recommendation": fix, "claim_kind": "text",
        "claim": f"{kind} at {unit_file}:{line}", "proposed_by": "posture-scan",
        "assessment": assessment,
        "evidence": [{"file": unit_file, "line_range": f"{line}-{end}",
                      "reason": f"text fact: {kind} at lines {line}-{end}"}],
    }


def _window(lines, i, n):
    return "\n".join(lines[i:i + n])


# ── the checks ───────────────────────────────────────────────────────────────

def _auth_optional(units, texts, deployed) -> list[dict]:
    out = []
    for u in units:
        if u["is_test"]:
            continue
        lines = texts.get(u["module"], [])
        rx = _ENV_TOKEN_PY if u.get("lang", "python") == "python" else _ENV_TOKEN_JS
        for i, ln in enumerate(lines):
            m = rx.search(ln)
            if not m:
                continue
            var, env = m.group(1), m.group(2)
            guard = re.compile(rf"\bif\s+(?:not\s+)?{re.escape(var)}\b(?:\s+and\b|\s*:)|"
                               rf"\bif\s*\(\s*!?{re.escape(var)}\s*(?:&&|\))|"
                               rf"\b{re.escape(var)}\s*(?:&&|\?)")
            for j in range(i + 1, min(len(lines), i + 16)):
                if guard.search(lines[j]) and _INBOUND.search(_window(lines, j, 4)):
                    sev = "critical" if deployed else "high"
                    out.append(_finding(
                        u["file"], i + 1, j + 1, "optional authentication",
                        f"authentication is optional: the check on `{env}` is skipped when it "
                        f"is unset ({u['file']}:{j + 1})",
                        f"`{var}` is read from the environment at line {i + 1} and the guard at "
                        f"line {j + 1} only applies when it is non-empty. A deployment that forgets "
                        f"to set `{env}` runs with no authentication at all"
                        + (", and this tree binds a public port (0.0.0.0 / $PORT)." if deployed
                           else "."),
                        f"Refuse to start, or refuse every gated request, when `{env}` is unset; "
                        f"make the open mode an explicit opt-in flag rather than the absence of a "
                        f"secret.", sev, FIX_BEFORE_EXPOSURE))
                    break
            if out and out[-1]["file"] == u["file"]:
                break                                  # one per file
    return out


def _body_ceiling(units, texts) -> tuple[list[dict], list[str]]:
    out, held = [], []
    for u in units:
        if u["is_test"]:
            continue
        lines = texts.get(u["module"], [])
        if u.get("lang", "python") == "python":
            for i, ln in enumerate(lines):
                if not _BODY_PY.search(ln) or "send_header" in ln:
                    continue
                win = _window(lines, i, 7)
                if _BODY_READ_PY.search(win):
                    if _BOUNDED.search(win):
                        held.append(f"the request body read at {u['file']}:{i + 1} is bounded")
                    else:
                        out.append(_finding(
                            u["file"], i + 1, i + 3, "unbounded request body",
                            f"request bodies have no size ceiling ({u['file']}:{i + 1})",
                            f"The handler reads `Content-Length` bytes from the socket with no "
                            f"upper bound. One request declaring a multi-gigabyte body, or a few "
                            f"in parallel, exhausts memory before any authentication runs.",
                            "Reject a `Content-Length` above a fixed ceiling with 413 before "
                            "reading, and read at most that many bytes.", "medium", DOS))
                    break
        else:
            for i, ln in enumerate(lines):
                m = _BODY_JS.search(ln)
                if m and "limit" not in m.group(1):
                    out.append(_finding(
                        u["file"], i + 1, i + 1, "unbounded request body",
                        f"request bodies use the parser's default ceiling ({u['file']}:{i + 1})",
                        "The body parser is mounted without a `limit`; the default is generous "
                        "and the same for every route.",
                        "Pass `{ limit: '…kb' }` sized to what the routes actually accept.",
                        "low", DOS))
                    break
                if _BODY_STREAM_JS.search(ln) and not _BOUNDED_JS.search(_window(lines, i, 7)):
                    out.append(_finding(
                        u["file"], i + 1, i + 6, "unbounded request body",
                        f"request bodies have no size ceiling ({u['file']}:{i + 1})",
                        "The handler accumulates `data` events with no check on the total length.",
                        "Count the bytes as they arrive and destroy the request past a ceiling.",
                        "medium", DOS))
                    break
    return out, held


def _escaping(units, texts) -> list[dict]:
    out = []
    for u in units:
        if u["is_test"]:
            continue
        lines = texts.get(u["module"], [])
        for i, ln in enumerate(lines):
            m = _ESCAPER.search(ln)
            if not m or "<" not in m.group(1) or ">" not in m.group(1):
                continue
            chars = m.group(1)
            missing = [c for c in ('"', "'") if c not in chars]
            if not missing:
                break
            sev = "medium" if '"' in missing else "low"
            out.append(_finding(
                u["file"], i + 1, i + 1, "weak HTML escaping",
                f"the HTML escaper leaves {' and '.join(repr(c) for c in missing)} unescaped "
                f"({u['file']}:{i + 1})",
                f"The escaper replaces `{chars}` only. A value placed inside an attribute "
                f"delimited by a quote it does not escape can close the attribute and add "
                f"its own — an injection surface for any user-controlled text the page "
                f"renders.",
                "Escape `&`, `<`, `>`, `\"` and `'` (`&#39;`), or use `textContent` / a "
                "templating call that escapes for you.", sev, XSS_TOKEN))
            break
    return out


def _token_in_storage(units, texts, weak_escaping) -> list[dict]:
    out = []
    for u in units:
        if u["is_test"]:
            continue
        lines = texts.get(u["module"], [])
        for i, ln in enumerate(lines):
            m = _STORAGE.search(ln)
            if m and _STORAGE_KEY.search(m.group(1)):
                out.append(_finding(
                    u["file"], i + 1, i + 1, "credential in browser storage",
                    f"a credential (`{m.group(1)}`) is kept in browser storage "
                    f"({u['file']}:{i + 1})",
                    "Anything in localStorage / sessionStorage is readable by any script that "
                    "runs on the page, so one injected script reads the token and holds the "
                    "operator's power"
                    + (" — and the page's HTML escaper is incomplete (see the escaping finding)."
                       if weak_escaping else "."),
                    "Keep the credential in an HttpOnly, SameSite cookie set by the server, or "
                    "hold it in memory for the session only.", "medium", XSS_TOKEN))
                break
    return out


def _pins(root) -> tuple[list[dict], list[str]]:
    out, held = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in polyglot.SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root)
        for fn in filenames:
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            if fn == "requirements.txt" or (fn.startswith("requirements") and fn.endswith(".txt")):
                loose, total, first = [], 0, 0
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as f:
                    for i, ln in enumerate(f, 1):
                        s = ln.strip()
                        if not s or s.startswith(("#", "-", "git+", "http")):
                            continue
                        m = _REQ_LINE.match(s)
                        if not m:
                            continue
                        total += 1
                        if m.group(2) != "==":
                            loose.append(f"{m.group(1)} ({s.split('#')[0].strip()})")
                            first = first or i
                if loose:
                    out.append(_finding(
                        rel, first, first, "unpinned dependencies",
                        f"{len(loose)} of {total} dependencies in {rel} are not pinned to an "
                        f"exact version",
                        f"Floating: {', '.join(loose[:8])}{' …' if len(loose) > 8 else ''}. A "
                        f"build today and a build next month resolve to different code, and a "
                        f"compromised release inside the range is installed without a change "
                        f"to this tree.",
                        "Pin every direct dependency to an exact version (or a hash) and "
                        "upgrade deliberately; keep a lockfile for the transitive set.",
                        "medium", SUPPLY_CHAIN))
                elif total:
                    held.append(f"all {total} dependencies in {rel} are pinned to exact versions")
            elif fn == "package.json":
                try:
                    import json
                    with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as f:
                        text = f.read()
                        d = json.loads(text)
                except (OSError, ValueError):
                    continue
                n = sum(len(d.get(k) or {}) for k in ("dependencies", "devDependencies"))
                if not n:
                    continue
                if any(os.path.exists(os.path.join(dirpath, lf)) for lf in LOCKFILES):
                    held.append(f"{rel}'s {n} dependencies are locked by a lockfile")
                else:
                    line = text.count("\n", 0, max(text.find('"dependencies"'), 0)) + 1
                    out.append(_finding(
                        rel, line, line, "unpinned dependencies",
                        f"{rel} declares {n} dependencies with no lockfile",
                        "Version ranges with no lockfile resolve differently on every install.",
                        "Commit the lockfile the package manager writes.", "medium", SUPPLY_CHAIN))
    return out, held


def _gitignore(root) -> tuple[list[dict], list[str]]:
    out, held = [], []
    committed = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in polyglot.SKIP_DIRS and d != ".git"]
        for fn in filenames:
            if _CRED_FILES.match(fn) and not _CRED_SAMPLES.search(fn):
                committed.append(os.path.relpath(os.path.join(dirpath, fn), root))
    for path in sorted(committed)[:5]:
        out.append(_finding(
            path, 1, 1, "credentials file in the tree",
            f"a credentials file is committed: {path}",
            "A `.env` or key file in the tree is in every clone and every archive of it.",
            "Rotate whatever it holds, delete it, and add its pattern to `.gitignore`.",
            "high", ROTATE))
    has_manifest = any(os.path.exists(os.path.join(root, m)) for m in MANIFESTS)
    gi = os.path.join(root, ".gitignore")
    patterns = []
    if os.path.exists(gi):
        with open(gi, encoding="utf-8", errors="replace") as f:
            patterns = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    env_ok = any(p in (".env", ".env*", "*.env", ".env.*", "**/.env", "/.env") for p in patterns)
    key_ok = any(p in ("*.pem", "*.key", "id_rsa", "*.p12", "*.pfx", "secrets/", "*.keystore")
                 for p in patterns)
    if has_manifest and not (env_ok and key_ok):
        missing = [x for x, ok in ((".env", env_ok), ("*.pem / *.key", key_ok)) if not ok]
        out.append(_finding(
            ".gitignore", 1, max(1, len(patterns)), "gitignore gap",
            f"`.gitignore` does not exclude {' or '.join(missing)}"
            + ("" if patterns else " (there is no .gitignore)"),
            "Nothing stops a local `.env` or a private key from being committed by habit; "
            "the ignore file is the last guard before a secret is in the history.",
            "Add `.env`, `.env.*`, `*.pem`, `*.key` and `id_rsa*` to `.gitignore`.",
            "low", SECRET_LEAK))
    elif has_manifest and not committed:
        held.append(".gitignore excludes .env and key files, and no credentials file is in the tree")
    return out, held


def _sandbox(units, texts) -> list[dict]:
    out = []
    for u in units:
        if polyglot.is_harness(u):
            continue                                   # an acceptance suite runs things; it is not the sandbox
        lines = texts.get(u["module"], [])
        text = "\n".join(lines)
        if not _SANDBOX_WORDS.search(text):
            continue
        rx = _SUBPROC_PY if u.get("lang", "python") == "python" else _SUBPROC_JS
        for i, ln in enumerate(lines):
            if rx.search(ln) and _INTERPRETER.search(_window(lines, i, 3)):
                present = [w for w in ISOLATION if w.lower() in text.lower()]
                if present:
                    break
                out.append(_finding(
                    u["file"], i + 1, i + 3, "subprocess sandbox",
                    f"the code sandbox is a subprocess with no filesystem, user or resource "
                    f"isolation ({u['file']}:{i + 1})",
                    f"`{u['file']}` runs an interpreter with `subprocess` in what the module "
                    f"calls a sandbox. The file contains none of: {', '.join(ISOLATION[:9])}, "
                    f"… — so a stripped environment or a network namespace is the whole "
                    f"boundary. That stops a program from phoning home; it does not stop it "
                    f"from reading or writing the host's files, forking, or exhausting memory "
                    f"and CPU.",
                    "Run generated code under a real boundary: a container or microVM, or at "
                    "least a mount namespace with a read-only root, a separate uid, and "
                    "rlimits on memory, CPU and processes.", "high", FIX_BEFORE_AUTONOMY))
                break
    return out


def analyse(root: str, units: list[dict], texts: dict) -> dict:
    """Returns {findings, held}. Never raises on a strange tree."""
    # A Python string that nothing assembles is data — a fixture, a page, a template —
    # and the checks read code. The suite's own fixtures are the proof: they contain
    # every shape below, and the mechanic on itself must stay at zero.
    texts = dict(texts)
    for u in units:
        if u.get("lang", "python") == "python":
            lines = texts.get(u["module"], [])
            _code, fixture, page = polyglot.python_views(lines)
            texts[u["module"]] = ["" if i in fixture - page else ln
                                  for i, ln in enumerate(lines, 1)]
    corpus = "\n".join("\n".join(texts.get(u["module"], [])) for u in units)
    deployed = bool(_DEPLOYED.search(corpus))
    findings, held = [], []
    findings += _auth_optional(units, texts, deployed)
    f, h = _body_ceiling(units, texts)
    findings += f; held += h
    esc = _escaping(units, texts)
    findings += esc
    findings += _token_in_storage(units, texts, bool(esc))
    f, h = _pins(root)
    findings += f; held += h
    f, h = _gitignore(root)
    findings += f; held += h
    findings += _sandbox(units, texts)
    return {"findings": findings, "held": held}


# ── the verdict: one sentence, then the table ────────────────────────────────

_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def assessment_of(f: dict) -> str:
    if f.get("assessment"):
        return f["assessment"]
    cat, sev, basis = f.get("category", ""), f.get("severity", "low"), f.get("basis", "")
    if basis != "machine-verified":
        return "Judged — confirm, then fix" if sev in ("critical", "high") else "Judged — review"
    if cat == "security":
        return FIX_BEFORE_EXPOSURE if sev in ("critical", "high") else "Fix soon"
    if cat == "outdated":
        return "Upgrade; read the changelog between majors"
    if cat == "liveness":
        return "Delete, or register the entry point that reaches it"
    if cat == "slop":
        return "Clean up"
    return "Review"


def _rank(r: dict) -> int:
    """Exposure first, then what a reviewer would read next; slop last."""
    if r["assessment"] == FIX_BEFORE_EXPOSURE:
        return 0
    if r["assessment"] == FIX_BEFORE_AUTONOMY:
        return 1
    if r["basis"] != "machine-verified":
        return 4
    return {"security": 2, "error-handling": 3, "outdated": 4, "liveness": 6,
            "slop": 7}.get(r["category"], 5)


def verdict(findings: list[dict], held: list[str], scope: dict) -> dict:
    """{headline, rows, held}. The headline is computed from the assessments, never
    from counts alone: one optional-authentication finding outweighs forty nits."""
    rows = []
    for f in findings:
        rows.append({"id": f.get("id", ""), "severity": f.get("severity", "low"),
                     "basis": f.get("basis", ""), "title": f.get("title", ""),
                     "assessment": assessment_of(f), "category": f.get("category", ""),
                     "file": (f.get("evidence") or [{}])[0].get("file", f.get("file", "")),
                     "line_range": (f.get("evidence") or [{}])[0].get("line_range", "")})
    rows.sort(key=lambda r: (_rank(r), _SEV.get(r["severity"], 9), r["title"]))
    blocking = [r for r in rows if r["assessment"] == FIX_BEFORE_EXPOSURE
                or (r["severity"] == "critical" and r["basis"] == "machine-verified")]
    autonomy = [r for r in rows if r["assessment"] == FIX_BEFORE_AUTONOMY]
    high = [r for r in rows if r["severity"] == "high" and r not in blocking and r not in autonomy]
    medium = [r for r in rows if r["severity"] == "medium"]
    if blocking:
        head = (f"Not safe to expose to the Internet yet — {len(blocking)} blocking finding"
                f"{'s' if len(blocking) > 1 else ''}"
                + (f", and {len(autonomy)} to fix before autonomous operation" if autonomy else ""))
    elif autonomy:
        head = (f"Not ready for autonomous operation — {len(autonomy)} finding"
                f"{'s' if len(autonomy) > 1 else ''} to fix first")
    elif high:
        head = f"{len(high)} high-severity finding{'s' if len(high) > 1 else ''} to fix before relying on it"
    elif medium:
        head = f"No blocking issue found; {len(medium)} medium finding{'s' if len(medium) > 1 else ''} to fix"
    elif rows:
        head = f"Nothing blocking found; {len(rows)} low-severity item{'s' if len(rows) > 1 else ''}"
    else:
        head = "Nothing found by the scan"
    mv = sum(1 for r in rows if r["basis"] == "machine-verified")
    langs = ", ".join(scope.get("languages") or []) or "no language"
    head += (f" — {scope.get('units', 0)} units in {langs}; {mv} machine-verified, "
             f"{len(rows) - mv} judged, {scope.get('refusals', 0)} refusal"
             f"{'s' if scope.get('refusals', 0) != 1 else ''}.")
    return {"headline": head, "rows": rows[:25], "more": max(0, len(rows) - 25),
            "held": list(held)}

"""The source scanner — the first stage of auditing real code (SECURITY.md, extended).

The toy replica was hand-written so the harness had flaws to find. This points the same
discipline at *real source*: walk a repository, and for every place a dangerous sink
meets attacker-influenced data, record a **candidate** — file, line, the exact snippet,
the class, and why it looks wrong.

A candidate is not a finding. It is a lead, and the harness's first law applies: a claim
is worth nothing until it reproduces (SECURITY.md §3). The scanner's job is only to point
the reproducer at the handful of lines worth recreating, out of a hundred thousand. So it
is tuned to *locate*, not to *judge* — it reports confidence and provenance and leaves the
verdict to a reproduction that actually triggers the flaw.

Read-only by construction: it opens files, it never writes one, and it never executes the
code it reads. Scanning a checkout you have is a defensive audit; nothing here touches a
running system.
"""

import os
import re

# A rule locates one class of sink. Each is deliberately conservative about what counts
# as "attacker-influenced": a literal-only argument is skipped, because a constant can't
# be an injection. The point is to surface the lines a human (or the reproducer) should
# look at, with few enough false positives that the list is worth reading.
#
# fields: id, class, severity, langs (extensions), pattern (regex), why, and an optional
# `needs_var` flag — when set, the match is kept only if it contains a non-literal
# argument (a variable, a template `${...}`, or string concatenation).
RULES = [
    # ── remote code execution ────────────────────────────────────────────────
    {"id": "js-eval", "class": "rce", "severity": "critical",
     "langs": (".js", ".ts", ".tsx", ".mjs", ".cjs"),
     "pattern": r"\beval\s*\(", "needs_var": True,
     "why": "eval() on non-literal input runs attacker-controlled code"},
    {"id": "js-new-function", "class": "rce", "severity": "critical",
     "langs": (".js", ".ts", ".tsx", ".mjs", ".cjs"),
     "pattern": r"\bnew\s+Function\s*\(", "needs_var": True,
     "why": "new Function(str) compiles attacker-controlled source"},
    {"id": "js-vm-run", "class": "rce", "severity": "high",
     "langs": (".js", ".ts", ".mjs", ".cjs"),
     "pattern": r"\bvm\.(runInNewContext|runInThisContext|runInContext)\s*\(",
     "needs_var": True, "why": "vm.run* is not a sandbox against hostile code"},
    {"id": "py-eval-exec", "class": "rce", "severity": "critical",
     "langs": (".py",), "pattern": r"\b(eval|exec)\s*\(", "needs_var": True,
     "why": "eval/exec on request data is arbitrary code execution"},

    # ── OS command injection ─────────────────────────────────────────────────
    # High precision on purpose: bare `exec(` in JS is overwhelmingly RegExp.exec or a
    # method/API named exec (the Computer's own runtime.exec is the product, not a bug).
    # Match only the unambiguous child_process sinks.
    {"id": "js-child-exec", "class": "command-injection", "severity": "critical",
     "langs": (".js", ".ts", ".tsx", ".mjs", ".cjs"),
     "pattern": r"\b(execSync|spawnSync)\s*\(|child_process\.(exec|execSync|spawn|spawnSync)\s*\(",
     "needs_var": True,
     "why": "child_process with an interpolated command is a shell-injection sink"},
    {"id": "py-os-system", "class": "command-injection", "severity": "critical",
     "langs": (".py",), "pattern": r"\bos\.system\s*\(|subprocess\.(call|run|Popen)\s*\(",
     "needs_var": True, "why": "a shell command built from input injects"},

    # ── path traversal ───────────────────────────────────────────────────────
    {"id": "js-path-join", "class": "traversal", "severity": "high",
     "langs": (".js", ".ts", ".tsx", ".mjs", ".cjs"),
     "pattern": r"(readFile|readFileSync|createReadStream|sendFile|open)\s*\([^)]*"
                r"(path\.(join|resolve)|\+|\$\{)", "needs_var": False,
     "why": "a filesystem read built from a joined/interpolated path may escape via .."},
    {"id": "py-open-join", "class": "traversal", "severity": "high",
     "langs": (".py",),
     "pattern": r"\bopen\s*\([^)]*(os\.path\.join|\+|f[\"'])", "needs_var": False,
     "why": "open() on a joined/f-string path may escape the intended directory"},

    # ── SQL injection ────────────────────────────────────────────────────────
    {"id": "sql-concat", "class": "sqli", "severity": "high",
     "langs": (".js", ".ts", ".tsx", ".mjs", ".cjs", ".py"),
     "pattern": r"(SELECT|INSERT|UPDATE|DELETE)\b[^;\n]*(\$\{|\"\s*\+|'\s*\+|%\s|\.format\()",
     "needs_var": False,
     "why": "a SQL string assembled by concatenation/interpolation is an injection sink"},
    {"id": "db-query-var", "class": "sqli", "severity": "medium",
     "langs": (".js", ".ts", ".mjs", ".cjs"),
     "pattern": r"\.(query|execute|exec|run|prepare)\s*\(\s*`[^`]*\$\{",
     "needs_var": False, "why": "a template-literal query with ${} bypasses parameterisation"},

    # ── cross-site scripting ─────────────────────────────────────────────────
    {"id": "js-dangerous-html", "class": "xss", "severity": "high",
     "langs": (".js", ".ts", ".tsx", ".jsx"),
     "pattern": r"dangerouslySetInnerHTML|\.innerHTML\s*=", "needs_var": False,
     "why": "raw HTML injection renders attacker markup in the victim's page"},

    # ── server-side request forgery ──────────────────────────────────────────
    {"id": "js-ssrf-fetch", "class": "ssrf", "severity": "medium",
     "langs": (".js", ".ts", ".mjs", ".cjs"),
     "pattern": r"\b(fetch|axios|got|http\.get|https\.get)\s*\(", "needs_var": True,
     "arg_match": r"req\b|request|params|query|body|input|href|userUrl|targetUrl",
     "why": "an outbound request to an input-derived URL can be steered at internal hosts"},

    # ── unsafe deserialization ───────────────────────────────────────────────
    {"id": "yaml-load", "class": "deserialization", "severity": "high",
     "langs": (".py",), "pattern": r"yaml\.load\s*\((?![^)]*Loader\s*=)",
     "needs_var": False, "why": "yaml.load without SafeLoader can instantiate arbitrary types"},
    {"id": "py-pickle", "class": "deserialization", "severity": "high",
     "langs": (".py",), "pattern": r"pickle\.loads?\s*\(", "needs_var": True,
     "why": "unpickling untrusted bytes is arbitrary code execution"},

    # ── hardcoded secrets ────────────────────────────────────────────────────
    {"id": "secret-assign", "class": "secret", "severity": "medium",
     "langs": (".js", ".ts", ".tsx", ".mjs", ".cjs", ".py", ".env", ".yml", ".yaml", ".json"),
     "pattern": r"(?i)(api[_-]?key|secret|passwd|password|token|private[_-]?key)"
                r"\s*[:=]\s*[\"'][A-Za-z0-9_\-/+=]{16,}[\"']",
     "needs_var": False, "why": "a credential committed to source is exposed to everyone with the repo"},
    {"id": "aws-key", "class": "secret", "severity": "high",
     "langs": (".js", ".ts", ".py", ".env", ".yml", ".yaml", ".json", ".txt"),
     "pattern": r"AKIA[0-9A-Z]{16}", "needs_var": False,
     "why": "an AWS access key id in source is a live credential leak"},
    {"id": "private-key", "class": "secret", "severity": "high",
     "langs": (".js", ".ts", ".py", ".pem", ".txt", ".env", ".key"),
     "pattern": r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
     "needs_var": False, "why": "a private key committed to source is compromised"},
]

# Directories that are never the code under audit — dependencies, build output, VCS.
SKIP_DIRS = {".git", "node_modules", "dist", "build", "out", ".next", "coverage",
             "__pycache__", ".turbo", ".wrangler", "vendor", ".venv", "venv"}

# A candidate on a line that says it is a fixture/negative test is downgraded, not
# dropped — a planted-vulnerable test file is exactly where real ones hide, too.
_TEST_HINT = re.compile(r"(test|spec|fixture|example|mock|__tests__)", re.I)
_QUOTED = re.compile(r"'([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\"|`([^`\\]|\\.)*`")


def _has_variable(line: str, mstart: int) -> bool:
    """Heuristic: does the call at ``mstart`` take something other than a bare literal?
    Strips quoted string constants first, so ``eval('1 + 1')`` (a literal that happens to
    contain a ``+``) is not mistaken for concatenation, while ``eval(userInput)`` and
    ``exec(`ls ${dir}`)`` are correctly seen as variable. Keeps the *-eval/exec/fetch
    rules from crying wolf on constant arguments."""
    after = line[mstart:]
    paren = after.find("(")
    if paren == -1:
        return True
    seg = after[paren + 1: paren + 1 + 240]
    if "${" in seg:                                # template interpolation → variable
        return True
    bare = _QUOTED.sub("", seg).split(")")[0]      # drop constants, keep only the arg list
    return bool(re.search(r"[A-Za-z_]\w*|\+", bare))


def scan_file(path: str, rel: str) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    rules = [r for r in RULES if ext in r["langs"]]
    if not rules:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    is_test = bool(_TEST_HINT.search(rel))
    out = []
    for i, line in enumerate(lines, start=1):
        if len(line) > 2000:
            continue                                # minified/bundled — skip the noise
        stripped = line.lstrip()
        if stripped.startswith(("//", "*", "/*", "* ", "#")):
            continue                                # a comment can't be an exploit
        for r in rules:
            m = re.search(r["pattern"], line)
            if not m:
                continue
            if r.get("needs_var") and not _has_variable(line, m.start()):
                continue
            if r.get("arg_match"):
                after = line[m.start():]
                paren = after.find("(")
                arg = after[paren + 1:paren + 260] if paren != -1 else after
                if not re.search(r["arg_match"], arg):
                    continue
            sev = r["severity"]
            conf = "low" if is_test else "medium"
            out.append({
                "rule": r["id"], "class": r["class"], "severity": sev,
                "confidence": conf, "file": rel, "line": i,
                "snippet": line.strip()[:200], "why": r["why"],
                "is_test": is_test})
    return out


def scan(root: str, max_files: int = 20000) -> dict:
    """Walk ``root`` and return every candidate, plus a summary by class and severity.
    Read-only: opens files, writes nothing, executes nothing."""
    root = os.path.realpath(root)
    candidates, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if scanned >= max_files:
                break
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            scanned += 1
            candidates.extend(scan_file(path, rel))
    by_class: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for c in candidates:
        by_class[c["class"]] = by_class.get(c["class"], 0) + 1
        by_sev[c["severity"]] = by_sev.get(c["severity"], 0) + 1
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    candidates.sort(key=lambda c: (order.get(c["severity"], 9),
                                   0 if not c["is_test"] else 1, c["file"], c["line"]))
    return {"root": root, "files_scanned": scanned, "candidates": candidates,
            "by_class": by_class, "by_severity": by_sev, "total": len(candidates)}


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Scan a source tree for vulnerability candidates.")
    ap.add_argument("root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    result = scan(a.root)
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nScanned {result['files_scanned']} files under {result['root']}")
        print(f"Candidates: {result['total']}  by class: {result['by_class']}  "
              f"by severity: {result['by_severity']}\n")
        for c in result["candidates"][:a.limit]:
            tag = " [test]" if c["is_test"] else ""
            print(f"  [{c['severity']:8}] {c['class']:18} {c['file']}:{c['line']}{tag}")
            print(f"             {c['snippet'][:110]}")

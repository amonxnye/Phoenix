"""Every language in the tree — read as text where it cannot be parsed.

The index parses Python and proves reachability there. The first public run was
pointed at a TypeScript repository and came back in 0.4 seconds with no findings and
no refusal: nothing had been read, and the run said "complete". That is the failure
the Charter exists to prevent (§6 — a gap is visible, never silent).

So every source file the tree contains is now a unit. A unit the index cannot parse
gets what the Charter still admits without a parser: TEXT FACTS (§2) — an exact match,
with the file and the line, checkable by anyone with the file open — and the judged
panel, which reads source, not graphs. What it does not get is a reachability claim:
"unreachable" needs a call graph, and this module never pretends to one. The nearest
honest statement is "this name appears nowhere else in the tree", and that is the
claim made, in those words.
"""

import hashlib
import io
import os
import re
import tokenize

LANGUAGES = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".vue": "vue", ".svelte": "svelte",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".scala": "scala",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".sh": "shell", ".bash": "shell", ".sql": "sql",
}
# Where references to a name may live without being source: templates, configs, docs.
REFERENCE_EXT = (".html", ".htm", ".json", ".yml", ".yaml", ".toml", ".xml", ".md",
                 ".txt", ".cfg", ".ini", ".env", ".ejs", ".hbs", ".njk", ".jinja")
SKIP_DIRS = (".git", "node_modules", "vendor", "__pycache__", ".venv", "venv", "dist",
             "build", "out", "coverage", "target")
BRACE_LANGS = {"javascript", "typescript", "vue", "svelte", "go", "rust", "java", "kotlin",
               "scala", "csharp", "swift", "c", "cpp", "php"}
JS_LANGS = {"javascript", "typescript", "vue", "svelte"}
MAX_FILE_BYTES = 1_500_000
MAX_LINE_CHARS = 3000                                  # longer is a bundle, not source
MIN_DUP_LINES = 6
PER_RULE_CAP = 5                                       # per file, per rule
_TEST_DIRS = ("test", "tests", "__tests__", "spec", "specs")


def lang_of(path: str) -> str:
    base = os.path.basename(path)
    if base.endswith(".d.ts") or base.endswith(".min.js") or base.endswith(".min.css"):
        return ""
    return LANGUAGES.get(os.path.splitext(base)[1].lower(), "")


def is_test(rel: str) -> bool:
    base = os.path.basename(rel).lower()
    parts = rel.replace(os.sep, "/").split("/")[:-1]
    return ("test" in base or ".spec." in base or base.startswith("spec")
            or any(p in _TEST_DIRS for p in parts))


def source_files(root: str, skip: tuple = SKIP_DIRS) -> list[tuple[str, str]]:
    """[(absolute path, language)] for every file the mechanic will read, in a stable
    order. Bundles, minified files and generated output are left out by shape."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
        for fn in sorted(filenames):
            lang = lang_of(fn)
            if not lang:
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append((p, lang))
    return out


def read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    if any(len(ln) > MAX_LINE_CHARS for ln in lines):
        return []                                      # generated: not read as source
    return lines


def summary(units: list[dict]) -> dict:
    """{language: {files, loc}} over the units of one run, largest first."""
    out: dict[str, dict] = {}
    for u in units:
        d = out.setdefault(u.get("lang") or "python", {"files": 0, "loc": 0})
        d["files"] += 1
        d["loc"] += int(u.get("loc") or 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["loc"]))


# ── findings: one shape, the slop scanner's ──────────────────────────────────

def _finding(unit, kind, line, end, title, why, fix, severity="low", category="security",
             symbol="", confidence=0.9, proposed_by="text-scan"):
    return {
        "unit": unit["module"], "file": unit["file"], "symbol": symbol,
        "line_range": f"{line}-{end}", "category": category, "severity": severity,
        "confidence": confidence, "basis": "machine-verified", "title": title,
        "description": why, "recommendation": fix, "claim_kind": "text",
        "claim": f"{kind} at {unit['file']}:{line}", "proposed_by": proposed_by,
        "evidence": [{"file": unit["file"], "line_range": f"{line}-{end}",
                      "reason": f"text fact: {kind} at lines {line}-{end}"}],
    }


def _is_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith(("//", "#", "*", "/*", "--", "<!--"))


# ── security: text facts any language can yield ──────────────────────────────

_PLACEHOLDER = re.compile(r"xxx|example|changeme|changeit|placeholder|your[_-]?|<|>|\$\{|\{\{|"
                          r"%\(|process\.env|os\.environ|getenv|dummy|sample|todo|\*\*\*|\.\.\.|"
                          r"development|do[_-]not|not[_-]use|insecure|default|replace[_-]?me|"
                          r"^[a-z]+$|^(.)\1+$|^0+$", re.I)
_SECRET = re.compile(r"\b[\w-]*?(api[_-]?key|secret[_-]?key|client[_-]?secret|secret|password|"
                     r"passwd|auth[_-]?token|access[_-]?token|private[_-]?key)\b\s*[:=]\s*"
                     r"[\"'`]([^\"'`\s]{8,})[\"'`]", re.I)
_CREDENTIAL = re.compile(r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|"
                         r"xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{32,}|AIza[0-9A-Za-z_-]{35}")
# A statement in a string, AND a value interpolated where a value goes (after =,
# LIKE, IN (, VALUES (, or a comma). `FROM {table}` with an identifier from code is
# not the injection surface this names; `WHERE id = ${id}` is.
_SQL = re.compile(r"[\"'`]\s*(?:SELECT\s+[\w*]|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|"
                  r"DELETE\s+FROM\s|DROP\s+TABLE\s)", re.I)
_SQL_INTERP = re.compile(r"(?:=|\bLIKE|\bIN\s*\(|\bVALUES\s*\(|,)\s*[\"'`]?\s*"
                         r"(?:\$\{|%s|%d|\{[^}]*\}|[\"'`]\s*\+|\+\s*[\w.]+)", re.I)
# Python names its columns in f-strings as a matter of course (`SET {col}=?`) and
# binds values with ?; the surface there is a value QUOTED into the statement, a
# concatenation, or %-formatting / .format() applied to the statement text.
_SQL_INTERP_PY = re.compile(r"(?:=|\bLIKE|\bIN\s*\(|\bVALUES\s*\(|,)\s*['\"]\s*\{[^}]*\}|"
                            r"[\"']\s*\+\s*[\w.]+|[\w.)\]]\s*\+\s*[\"']|\.format\(|"
                            r"[\"']\s*%\s*[\w(]", re.I)
_HTML_LITERAL = re.compile(r"innerHTML\s*=\s*[\"'`][^$`]*[\"'`]\s*;?\s*$")

RULES = [
    # (kind, regex, severity, title, why, fix)
    ("private key in source",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "critical",
     "a private key is committed in the source",
     "The file contains a PEM private key. Anyone with read access to the repository "
     "holds the key; a public repository has published it.",
     "Revoke the key, remove it from the tree and its history, and load keys from the "
     "environment or a secret store."),
    ("credential token in source", _CREDENTIAL, "high",
     "a credential token is committed in the source",
     "The line carries a token in a known provider format (cloud access key, GitHub, "
     "Slack, API key). Committed tokens are live until revoked.",
     "Revoke it, remove it from the tree and its history, and read it from the environment."),
    ("hardcoded secret", _SECRET, "high",
     "a secret is assigned as a string literal",
     "A key, token or password is written into the code rather than read from the "
     "environment. It ships with every copy of the repository.",
     "Read it from the environment or a secret store; rotate the value now in the tree."),
    ("dynamic code execution",
     re.compile(r"\beval\s*\(|new\s+Function\s*\("), "high",
     "code is executed from a string",
     "`eval` / `new Function` turns data into code. If any part of the string can be "
     "influenced by input, that input runs with the program's authority.",
     "Replace with explicit parsing or a lookup table; if evaluation is unavoidable, "
     "constrain the source to literals the program itself wrote."),
    ("shell command built from a string",
     re.compile(r"\b(?:exec|execSync|system|popen|os\.system|subprocess\.(?:call|run|Popen|"
                r"check_output))\s*\(\s*(?:`[^`]*\$\{|[^,)]*\+\s*[\"'`]|[\"'`][^\"'`]*[\"'`]"
                r"\s*\+|f[\"'])|shell\s*=\s*True"), "high",
     "a shell command is assembled from string pieces",
     "The command line is built by interpolation or concatenation before a shell runs "
     "it. Anything that reaches those pieces can add commands of its own.",
     "Pass the program and its arguments as a list without a shell; validate every "
     "piece that came from outside."),
    ("SQL built from a string", None, "high",
     "a SQL statement is assembled from string pieces",
     "The query text is built by interpolation or concatenation. A value that reaches "
     "it unescaped changes the statement, not just the data.",
     "Use parameterised queries (placeholders bound by the driver) for every value."),
    ("TLS verification disabled",
     re.compile(r"rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\W*0\b|"
                r"\bverify\s*=\s*False|InsecureSkipVerify\s*:\s*true|"
                r"CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)\b|_create_unverified_context|"
                r"check_hostname\s*=\s*False"), "high",
     "certificate verification is turned off",
     "The connection accepts any certificate, so any host on the path can present its "
     "own and read or alter the traffic.",
     "Remove the override; if a private CA is in use, trust that CA explicitly."),
    ("HTML injection sink",
     re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML|insertAdjacentHTML\s*\(|"
                r"document\.write\s*\(|\bmark_safe\s*\(|\bMarkup\s*\(|\|\s*safe\b"), "medium",
     "markup is written from a value, not a literal",
     "The line writes raw HTML. Unless every value in it is escaped first, text from a "
     "user renders as markup and script.",
     "Set text content, or escape the value with the framework's escaper before it is "
     "inserted."),
    ("weak randomness for a secret",
     re.compile(r"Math\.random\s*\(|\brandom\.(?:random|randint|choice|randrange)\s*\("),
     "medium", "a token is drawn from a non-cryptographic generator",
     "The value on this line names a token, secret, session or key, and the generator "
     "is predictable by design.",
     "Use the platform's cryptographic generator (crypto.randomBytes / randomUUID, "
     "secrets.token_*)."),
    ("weak hash for a credential",
     re.compile(r"\b(?:md5|sha1)\s*\(|createHash\s*\(\s*[\"'](?:md5|sha1)[\"']|"
                r"hashlib\.(?:md5|sha1)\s*\("), "medium",
     "a password or token is hashed with MD5/SHA-1",
     "The line names a password or token and hashes it with a function that is fast "
     "to brute-force and has known collisions.",
     "Use a password hash (bcrypt, scrypt, argon2) for credentials; SHA-256 or better "
     "for integrity."),
    ("CORS open to any origin",
     re.compile(r"Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*[\"']|"
                r"\borigin\s*:\s*[\"']\*[\"']|\bcors\s*\(\s*\)"), "low",
     "cross-origin requests are allowed from any site",
     "Any web page may call this API from a visitor's browser. Harmless for a public, "
     "credential-free API; not for one that reads cookies or tokens.",
     "Name the allowed origins; never combine `*` with credentials."),
    ("plaintext http URL",
     re.compile(r"[\"']http://(?!localhost|127\.|0\.0\.0\.0|\[::1\]|schemas?\.|www\.w3\.org|"
                r"xml|example\.|.*\.local\b|.*\.test\b)"), "low",
     "a service is addressed over plaintext HTTP",
     "Traffic to this URL can be read and altered on the path.",
     "Use https://, or state on the line why plaintext is acceptable here."),
    ("debug mode on",
     re.compile(r"\bdebug\s*[=:]\s*(?:true|True)\b"), "low",
     "debug mode is switched on in code",
     "Debug mode in a web framework exposes stack traces, and often a console, to "
     "whoever triggers an error.",
     "Drive it from the environment, off by default."),
]
_NEEDS_SECRET_WORD = {"weak randomness for a secret", "weak hash for a credential"}
_SECRET_WORD = re.compile(r"token|secret|password|passwd|otp|nonce|session|api[_-]?key|\bkey\b",
                          re.I)
_ALWAYS = {"private key in source", "credential token in source", "hardcoded secret"}


_PAGE = re.compile(r"<script|<html|<!doctype", re.I)


def python_views(lines: list[str]) -> tuple[list[str], set[int], set[int]]:
    """Two readings of a Python module for the text scans.

    `code`: every line with the CONTENTS of its plain (non-f) string literals blanked,
    unless the line assembles them (%, .format, +) — so `eval(` in code is seen and
    `"eval("` in an assertion is not, while `"SELECT … = '" + name` keeps its text.
    `fixture`: line numbers that are nothing but string fragments — the interior of a
    triple-quoted page or template, or one line of an implicitly concatenated fixture —
    where no rule about code applies at all. The secret rules read the raw line either
    way, because a secret IS a string.
    `page`: the subset of fixture lines that are an embedded web page (a triple-quoted
    string holding <script> or <html>) — served to a browser, so the page-level checks
    (the escaper, what the browser stores) read them as the JavaScript they are."""
    code = list(lines)
    fixture: set[int] = set()
    page: set[int] = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return code, fixture, page
    by_row: dict[int, list] = {}
    for t in toks:
        by_row.setdefault(t.start[0], []).append(t)
    assembling = re.compile(r"%|\.format\(|\+")
    for t in toks:
        if t.type != tokenize.STRING:
            continue
        stripped = t.string.lstrip("rRbBuU")
        if stripped[:1] in "fF":
            continue                                   # an f-string is code
        (r0, c0), (r1, c1) = t.start, t.end
        if r1 > r0:                                    # triple-quoted, spanning lines
            fixture.update(range(r0 + 1, r1))
            if _PAGE.search(t.string):
                page.update(range(r0 + 1, r1))
            code[r0 - 1] = code[r0 - 1][:c0] + " " * (len(code[r0 - 1]) - c0)
            if r1 - 1 < len(code):
                code[r1 - 1] = " " * c1 + code[r1 - 1][c1:]
            continue
        line = lines[r0 - 1]
        if not assembling.search(line[:c0] + line[c1:]):
            code[r0 - 1] = code[r0 - 1][:c0] + " " * (c1 - c0) + code[r0 - 1][c1:]
    for row, ts in by_row.items():
        kinds = {t.type for t in ts} - {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                                        tokenize.INDENT, tokenize.DEDENT}
        ops = {t.string for t in ts if t.type == tokenize.OP}
        if kinds and kinds <= {tokenize.STRING, tokenize.OP} and ops <= {",", "(", ")", "[", "]", "{", "}", "+"} \
                and any(t.type == tokenize.STRING and t.string.lstrip("rRbBuU")[:1] not in "fF" for t in ts):
            fixture.add(row)
    return code, fixture, page


def security_findings(unit: dict, lines: list[str]) -> list[dict]:
    """Deterministic, any language. Each rule is capped per file so one pattern that
    a file repeats on purpose cannot bury the rest of the report."""
    out, seen = [], {}
    if unit.get("lang", "python") == "python":
        code, fixture, _page = python_views(lines)
    else:
        code, fixture = lines, set()
    for i, raw in enumerate(lines, 1):
        comment = _is_comment(raw) or i in fixture
        for kind, rx, sev, title, why, fix in RULES:
            if comment and kind not in _ALWAYS:
                continue
            ln = raw if kind in _ALWAYS else code[i - 1]
            if seen.get(kind, 0) >= PER_RULE_CAP:
                continue
            if kind == "SQL built from a string":
                interp = _SQL_INTERP_PY if unit.get("lang", "python") == "python" else _SQL_INTERP
                hit = bool(_SQL.search(ln) and interp.search(ln))
            else:
                m = rx.search(ln)
                hit = bool(m)
                if hit and kind == "hardcoded secret":
                    val = m.group(2)
                    hit = (not _PLACEHOLDER.search(val)
                           and (any(c.isdigit() for c in val) or len(val) >= 16))
                if hit and kind in _NEEDS_SECRET_WORD:
                    hit = bool(_SECRET_WORD.search(ln))
                if hit and kind == "HTML injection sink" and _HTML_LITERAL.search(ln):
                    hit = False                        # a literal string is not a sink
            if hit:
                seen[kind] = seen.get(kind, 0) + 1
                out.append(_finding(unit, kind, i, i, f"{title} ({unit['file']}:{i})",
                                    why, fix, severity=sev, proposed_by="security-scan"))
                break                                  # one finding per line
    return out


# ── declarations: what a file defines, by shape ──────────────────────────────

_DECL = {
    "js": [
        re.compile(r"^\s*(?P<export>export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*"
                   r"(?P<name>[A-Za-z_$][\w$]*)\s*[<(]"),
        re.compile(r"^\s*(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
                   r"\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|"
                   r"[A-Za-z_$][\w$]*\s*=>)"),
    ],
    "go": [re.compile(r"^func\s+(?P<name>[A-Za-z_]\w*)\s*[(\[]")],
    "rust": [re.compile(r"^(?P<export>pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)")],
    "php": [re.compile(r"^function\s+(?P<name>[A-Za-z_]\w*)\s*\(")],
}
_IMPORT = {
    "js": re.compile(r"^\s*import\s+[^;]*?from\s+[\"']([^\"']+)[\"']|^\s*import\s+[\"']([^\"']+)[\"']|"
                     r"require\(\s*[\"']([^\"']+)[\"']\s*\)"),
    "go": re.compile(r"^\s*(?:import\s+)?\"([^\"]+)\"\s*$"),
    "rust": re.compile(r"^\s*use\s+([\w:]+)"),
    "php": re.compile(r"^\s*(?:use|require(?:_once)?|include(?:_once)?)\s+[\"']?([\w\\./-]+)"),
}


def _family(lang: str) -> str:
    return "js" if lang in JS_LANGS else lang if lang in ("go", "rust", "php") else ""


def _block_end(lines: list[str], start: int) -> int:
    """1-based line of the brace that closes the block opened on the declaration line
    (or the next two). Strings and comments are skipped by shape; an expression body
    with no brace ends on its own line; a body that never closes ends the file."""
    depth, opened, in_block_comment = 0, False, False
    for i in range(start - 1, len(lines)):
        if not opened and i - (start - 1) > 2:
            return start                               # an expression body, no brace
        ln, j = lines[i], 0
        while j < len(ln):
            c = ln[j]
            if in_block_comment:
                if ln.startswith("*/", j):
                    in_block_comment, j = False, j + 2
                    continue
                j += 1
                continue
            if ln.startswith("//", j):
                break
            if ln.startswith("/*", j):
                in_block_comment, j = True, j + 2
                continue
            if c in "\"'`":
                k = j + 1
                while k < len(ln) and ln[k] != c:
                    k += 2 if ln[k] == "\\" else 1
                j = k + 1
                continue
            if c == "{":
                depth, opened = depth + 1, True
            elif c == "}":
                depth -= 1
                if opened and depth == 0:
                    return i + 1
            j += 1
    return len(lines)


def declarations(lines: list[str], lang: str) -> list[dict]:
    """Top-level functions by shape: [{name, line, end, exported}]. Methods inside
    classes are not listed — a framework may call them by convention, and the text
    cannot tell (§4: the expensive error is a live symbol called dead)."""
    fam = _family(lang)
    if not fam:
        return []
    out = []
    for i, ln in enumerate(lines, 1):
        if fam == "js" and ln[:1] in (" ", "\t"):
            continue                                   # indented: a method or a nested function
        for rx in _DECL[fam]:
            m = rx.match(ln)
            if not m:
                continue
            name = m.group("name")
            exported = bool(m.groupdict().get("export"))
            if lang == "go":
                exported = name[:1].isupper()
            out.append({"name": name, "line": i, "end": _block_end(lines, i),
                        "exported": exported})
            break
    return out


def imports(lines: list[str], lang: str) -> list[str]:
    fam = _family(lang)
    if not fam:
        return []
    out = []
    for ln in lines:
        m = _IMPORT[fam].search(ln)
        if m:
            out.append(next(g for g in m.groups() if g))
    return sorted(set(out))[:40]


def checklist(unit: dict, lines: list[str]) -> list[dict]:
    """The critic's checklist for a unit the index did not parse: every function by
    shape, every import. Same ids as the graph's, so coverage is measured the same way."""
    items = [{"id": f"f:{d['name']}@{d['line']}", "kind": "function",
              "what": f"{d['name']} (line {d['line']}-{d['end']})"}
             for d in declarations(lines, unit.get("lang", ""))]
    items += [{"id": f"i:{m}", "kind": "import", "what": f"import {m}"}
              for m in imports(lines, unit.get("lang", ""))]
    return items


# ── slop: the tells, as text facts, for brace languages ──────────────────────

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_EMPTY_CATCH = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}|err\s*!=\s*nil\s*\{\s*\}")
_STUB = re.compile(r"throw\s+new\s+\w*Error\s*\(\s*[\"'`][^\"'`]*not\s+implemented|"
                   r"panic\(\s*\"not implemented|\b(?:todo|unimplemented)!\s*\(", re.I)
_CODE_LINE = re.compile(r"[;{}]\s*$|^\s*(?:const|let|var|if|for|while|return|import|export|"
                        r"function|func|fn|class|switch|case)\b|^\s*[\w.$]+\s*\(.*\)\s*;?\s*$|"
                        r"^\s*[\w.$\[\]]+\s*[+\-*/]?=\s*")
_DIRECTIVE = re.compile(r"eslint|prettier|@ts-|tslint|copyright|licen[cs]e|SPDX|TODO|FIXME|"
                        r"NOTE|^\s*//\s*[A-Z][a-z]+ ", re.I)
_JS_IMPORT = re.compile(r"^[ \t]*import\s+(?:type\s+)?(?P<clause>[^;]*?)\s+from\s+[\"'][^\"']+[\"']",
                        re.M | re.S)
_REQUIRE = re.compile(r"^[ \t]*(?:const|let|var)\s+(?P<clause>\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*"
                      r"require\(", re.M)


def _import_names(clause: str) -> list[str]:
    names = []
    braces = re.search(r"\{([^}]*)\}", clause)
    if braces:
        for part in braces.group(1).split(","):
            part = re.sub(r"^\s*type\s+", "", part.strip())
            if not part:
                continue
            names.append(part.split(" as ")[-1].strip())
        clause = clause[:braces.start()] + clause[braces.end():]
    star = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if star:
        names.append(star.group(1))
        clause = clause.replace(star.group(0), "")
    for part in clause.split(","):
        part = part.strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", part or ""):
            names.append(part)
    return names


def _unused_imports(unit: dict, lines: list[str], text: str) -> list[dict]:
    out = []
    spans = []
    for m in list(_JS_IMPORT.finditer(text)) + list(_REQUIRE.finditer(text)):
        line = text.count("\n", 0, m.start()) + 1
        spans.append((m.start(), m.end()))
        for name in _import_names(m.group("clause")):
            if name == "React" or name.startswith("_"):
                continue
            body = text[:m.start()] + text[m.end():]
            if re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", body):
                continue
            src_line = lines[line - 1] if line - 1 < len(lines) else ""
            if "//" in src_line and re.search(r"noqa|re-?export|unused|side.?effect", src_line, re.I):
                continue                               # a commented import is a decision
            out.append(_finding(unit, "unused import", line, line,
                                f"`{name}` is imported and never used",
                                f"`{name}` is imported at line {line} and referenced nowhere "
                                f"in the module. Unused imports accrete when code is generated "
                                f"in pieces.", "Remove the import.", category="slop",
                                proposed_by="slop-scan"))
    return out


def _commented_out_code(unit: dict, lines: list[str], lang: str) -> list[dict]:
    """Three or more consecutive full-line comments that each read as code. A prose
    line, a directive (eslint, a licence header) or a blank ends the run, so a block
    of code followed by its explanation is reported as the code lines alone."""
    marker = "#" if lang in ("shell", "ruby") else "//"
    out, run, start = [], 0, 0

    def flush():
        if run >= 3:
            out.append(_finding(unit, "commented-out code", start, start + run - 1,
                                f"{run} lines of commented-out code",
                                "The block reads as code, not prose. Code kept as comments "
                                "is dead weight that reads as a plan nobody finished.",
                                "Delete it; version control keeps the history.",
                                category="slop", proposed_by="slop-scan"))
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        body = re.sub(rf"^{re.escape(marker)}\s?", "", s) if s.startswith(marker) else None
        if body is not None and not s.startswith(marker + "/") \
                and _CODE_LINE.search(body) and not _DIRECTIVE.search(body):
            if not run:
                start = i
            run += 1
        else:
            flush(); run = 0
    flush()
    return out


def slop_findings(unit: dict, lines: list[str], lang: str) -> list[dict]:
    """Empty catch blocks, stubs, commented-out code, unused ES imports — each a text
    fact with its lines. Python has its own scanner (slop.py); this is the rest."""
    if lang not in BRACE_LANGS and lang not in ("shell", "ruby"):
        return []
    text = "\n".join(lines)
    out = []
    for m in _EMPTY_CATCH.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        end = line + m.group(0).count("\n")
        out.append(_finding(unit, "unexplained exception swallow", line, end,
                            "an error is caught and dropped, with no stated reason",
                            "Every failure on this path disappears. Reviewed code that swallows "
                            "an error says why inside the block; generated code often does not.",
                            "Handle it, rethrow it, or state the reason in a comment inside the "
                            "block so the swallow is a decision rather than an accident.",
                            severity="medium", category="slop", proposed_by="slop-scan"))
    for i, ln in enumerate(lines, 1):
        if _STUB.search(ln) and not _is_comment(ln):
            out.append(_finding(unit, "stub body", i, i, "an unimplemented stub shipped",
                                "The line throws or panics with 'not implemented'. Callers get "
                                "nothing and no error they can act on.",
                                "Implement it, or delete it and its callers.",
                                category="slop", proposed_by="slop-scan"))
    out += _commented_out_code(unit, lines, lang)
    if lang in JS_LANGS:
        out += _unused_imports(unit, lines, text)
    return out


# ── across the tree: duplicated bodies, names referenced nowhere ─────────────

def _norm_body(lines: list[str], start: int, end: int) -> list[str]:
    body = []
    for ln in lines[start:end - 1]:                    # between the declaration and its close
        s = re.sub(r"\s+", "", ln.split("//")[0])
        if s and not s.startswith(("/*", "*")):
            body.append(s)
    return body


def tree_findings(units: list[dict], texts: dict[str, list[str]]) -> list[dict]:
    """Facts that need the whole tree: a function body duplicated across files, and a
    non-exported top-level function whose name appears nowhere else in the tree
    (source, templates, configs and docs included — a reference in an HTML attribute
    or a config key counts, in the safe direction)."""
    out, bodies, decls = [], {}, []
    # One pass over the tree's text for every identifier, counted once; a lookup per
    # declaration after that, so a large tree costs seconds, not a scan per function.
    mentions: dict[str, int] = {}
    for key, lines in texts.items():
        for w in _IDENT.findall("\n".join(lines)):
            mentions[w] = mentions.get(w, 0) + 1
    for u in units:
        lang = u.get("lang", "python")
        if lang == "python":
            continue
        lines = texts.get(u["module"], [])
        for d in declarations(lines, lang):
            if d["end"] > d["line"]:
                body = _norm_body(lines, d["line"], d["end"])
                if len(body) >= MIN_DUP_LINES:
                    h = hashlib.sha256("\n".join(body).encode()).hexdigest()[:16]
                    bodies.setdefault(h, []).append((u, d))
            if not u["is_test"]:
                decls.append((u, d))
    for group in bodies.values():
        if len(group) < 2:
            continue
        (u, d), rest = group[0], group[1:]
        others = ", ".join(f"{g[1]['name']} ({g[0]['file']}:{g[1]['line']})" for g in rest)
        out.append(_finding(u, "duplicated function body", d["line"], d["end"],
                            f"`{d['name']}` is duplicated {len(rest)}× elsewhere",
                            f"The body of `{d['name']}` is identical, whitespace aside, to "
                            f"{others}. Generated code repeats itself where reviewed code would "
                            f"share.", "Keep one implementation and call it from the others.",
                            severity="medium", category="slop", symbol=d["name"],
                            proposed_by="slop-scan"))
    for u, d in decls:
        name = d["name"]
        if d["exported"] or len(name) < 3 or name in ("main", "init", "setup", "handler",
                                                       "default", "render"):
            continue
        if mentions.get(name, 0) - 1 > 0:             # its own declaration is not a reference
            continue
        span = d["end"] - d["line"] + 1
        out.append(_finding(u, "name referenced nowhere", d["line"], d["end"],
                            f"`{name}` in {u['file']} is referenced nowhere in the tree",
                            f"`{name}` is declared at line {d['line']} and its name appears "
                            f"nowhere else — not in any source file, template, config or "
                            f"document in the tree. This is a text fact, not a call graph: a "
                            f"reference made by a framework's convention or from outside the "
                            f"tree is invisible here. {span} lines.",
                            f"Delete `{name}` ({u['file']}:{d['line']}–{d['end']}), or export "
                            f"it and name the caller if something outside the tree invokes it.",
                            severity="medium" if span >= 60 else "low", category="liveness",
                            symbol=name, confidence=0.85, proposed_by="text-scan"))
    out.sort(key=lambda f: (f["category"], f["file"], int(f["line_range"].split("-")[0])))
    return out


def reference_lines(root: str, skip: tuple = SKIP_DIRS) -> list[str]:
    """Text of non-source files where a name may be referenced (templates, configs,
    docs). Read only for the 'referenced nowhere' fact; never a unit."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(REFERENCE_EXT) or fn.startswith(".env"):
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(p) <= MAX_FILE_BYTES:
                        out += read_lines(p)
                except OSError:
                    pass
    return out

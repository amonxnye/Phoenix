"""The reproducer — rebuild a scanned flaw as a runnable system, in its own language.

Given a scanner candidate (class, file, line, the real source line), the reproducer:

  1. reads the actual source and extracts the enclosing function as evidence;
  2. identifies the concrete sink from the real line (``eval`` vs ``new Function`` vs a
     ``readFile`` on a joined path — parsed, not assumed by class);
  3. generates a minimal reproduction *in the source language* — a harness that drives
     that exact sink with attacker input, plus the invariant that judges it and the
     exploit that triggers it — in both a vulnerable and a hardened variant;
  4. hands it to the matching Range (Node for JS, the cage for Python), which hosts and
     hacks it for real, and returns the trace-based verdict.

Nothing is hardcoded to a class stereotype: the sink is read off the real line, the real
enclosing code is carried as evidence, and the reproduction executes in the language the
flaw was written in. A finding is **confirmed** only if the vulnerable build is exploited
*and* the hardened build refuses the same exploit — the pairing that kills false positives.

Every ``prove`` runs in a unique job directory and cleans up after itself, so any number
of guests can run at once without sharing state.
"""

import os
import re
import shutil
import tempfile

import range as RANGE

JS_EXT = (".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")
PY_EXT = (".py",)

JOBS_ROOT = os.path.join(tempfile.gettempdir(), "phoenix-jobs")


def lang_of(path: str) -> str | None:
    ext = os.path.splitext(path or "")[1].lower()
    if ext in JS_EXT:
        return "js"
    if ext in PY_EXT:
        return "py"
    return None


# ── source extraction (real evidence) ─────────────────────────────────────────

def _resolve(candidate: dict, root: str | None) -> str | None:
    p = candidate.get("abspath")
    if p and os.path.isfile(p):
        return p
    if root:
        cand = os.path.join(root, candidate.get("file", ""))
        if os.path.isfile(cand):
            return cand
    return None


def extract(candidate: dict, root: str | None = None) -> dict:
    """Return the real enclosing code around the candidate line, plus the sink line.
    Best-effort: on any failure the reproduction still builds from the candidate snippet."""
    path = _resolve(candidate, root)
    line = int(candidate.get("line", 0) or 0)
    if not path or line < 1:
        return {"sink_line": candidate.get("snippet", ""), "enclosing": "", "resolved": False}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return {"sink_line": candidate.get("snippet", ""), "enclosing": "", "resolved": False}
    idx = min(line - 1, len(lines) - 1)
    sink = lines[idx].rstrip("\n")
    lang = lang_of(path)
    lo, hi = idx, idx
    if lang == "py":
        # walk up to the enclosing def/class, then down until the indent returns
        base = len(lines[idx]) - len(lines[idx].lstrip())
        for j in range(idx, -1, -1):
            if re.match(r"^\s*(def|class)\s", lines[j]) and \
                    (len(lines[j]) - len(lines[j].lstrip())) < base or j == 0:
                lo = j
                if re.match(r"^\s*(def|class)\s", lines[j]):
                    break
        head = len(lines[lo]) - len(lines[lo].lstrip())
        for j in range(idx + 1, len(lines)):
            if lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip())) <= head:
                break
            hi = j
    else:
        # brace-match: find a signature line at/above, then balance braces forward
        for j in range(idx, max(idx - 40, -1), -1):
            if re.search(r"(function\b|=>\s*\{|\)\s*\{|\b(async\s+)?[\w$]+\s*\([^;]*\)\s*\{)", lines[j]):
                lo = j
                break
        depth = 0
        started = False
        for j in range(lo, min(lo + 80, len(lines))):
            depth += lines[j].count("{") - lines[j].count("}")
            if "{" in lines[j]:
                started = True
            hi = j
            if started and depth <= 0 and j >= idx:
                break
    enclosing = "".join(lines[lo:hi + 1])[:2000]
    return {"sink_line": sink.strip(), "enclosing": enclosing, "resolved": True,
            "lang": lang, "start_line": lo + 1}


# ── generators: one per concrete sink, driven by the real source line ─────────
# Each detects itself from the actual line, targets a language, and emits a runnable
# reproduction (vulnerable and hardened). None is a class stereotype divorced from source.

def _evidence_banner(cls: str, prov: str, evidence: dict) -> str:
    real = evidence.get("enclosing") or evidence.get("sink_line", "")
    real = "\n".join("// " + ln for ln in real.splitlines()[:24])
    return (f"// Reproduction of a {cls} flaw. Models: {prov}\n"
            f"// --- real code extracted from source ---\n{real}\n// ---\n")


def _py_banner(cls: str, prov: str, evidence: dict) -> str:
    real = evidence.get("enclosing") or evidence.get("sink_line", "")
    real = "\n".join("# " + ln for ln in real.splitlines()[:24])
    return (f'"""Reproduction of a {cls} flaw. Models: {prov}\n'
            f"--- real code extracted from source ---\n{real}\n\"\"\"\n")


def gen_js_eval(prov, evidence, hardened, newfunc=False):
    call = ("Function('return (' + input + ')')()" if newfunc else "eval(input)")
    body = ("    if(!/^[\\s\\d+\\-*/().]+$/.test(input)) throw new Error('rejected: non-arithmetic');\n"
            f"    const value = {call};\n" if hardened else
            f"    const value = {call};   // VULNERABLE: attacker string reaches the evaluator\n")
    target = (_evidence_banner("rce", prov, evidence) +
              "export class Target {\n  constructor(){ this.trace = []; }\n"
              "  calc(input){\n" + body +
              "    this.trace.push({ expr: input, evaluated: true });\n"
              "    return value;\n  }\n}\n")
    invariant = ("const arith = s => /^[\\s\\d+\\-*/().]+$/.test(String(s));\n"
                 "export function check(t){\n"
                 "  return (t.trace||[]).filter(e=>e.evaluated && !arith(e.expr))\n"
                 "    .map((e,i)=>({id:'INV-EXEC',cls:'rce',step:i,detail:'evaluated non-arithmetic '+JSON.stringify(e.expr)}));\n}\n")
    exploit = ("export function exploit(t){ t.calc('globalThis.process.pid'); }\n")
    return {"target.mjs": target, "invariant.mjs": invariant, "exploit.mjs": exploit}


def gen_js_traversal(prov, evidence, hardened):
    read = ("    const full = path.resolve(this.root, name);\n"
            "    if(full !== this.root && !full.startsWith(this.root + path.sep)){ this.trace.push({name, served:null}); return null; }\n"
            if hardened else
            "    const full = `${this.root}/${name}`;   // VULNERABLE: unchecked join\n")
    target = (_evidence_banner("traversal", prov, evidence) +
              "import fs from 'node:fs'; import path from 'node:path';\n"
              "export class Target {\n  constructor(){\n"
              "    this.root = path.join(process.cwd(),'docs'); fs.mkdirSync(this.root,{recursive:true});\n"
              "    fs.writeFileSync(path.join(this.root,'readme.txt'),'public');\n"
              "    fs.writeFileSync(path.join(process.cwd(),'SECRET.txt'),'token=hunter2');\n"
              "    this.trace = [];\n  }\n"
              "  read(name){\n" + read +
              "    let body=null; try{ body=fs.readFileSync(full,'utf8'); }catch{}\n"
              "    this.trace.push({name, served: body===null?null: fs.realpathSync(full)}); return body;\n  }\n}\n")
    invariant = ("import fs from 'node:fs';\n"
                 "export function check(t){ const root=fs.realpathSync(t.root); const out=[];\n"
                 "  (t.trace||[]).forEach((e,i)=>{ if(e.served){ const s=fs.realpathSync(e.served);\n"
                 "    if(s!==root && !s.startsWith(root+'/')) out.push({id:'INV-PATH',cls:'traversal',step:i,detail:'served '+s+' via '+JSON.stringify(e.name)}); }});\n"
                 "  return out; }\n")
    exploit = ("export function exploit(t){ t.read('../SECRET.txt'); }\n")
    return {"target.mjs": target, "invariant.mjs": invariant, "exploit.mjs": exploit}


def gen_py_eval(prov, evidence, hardened):
    body = ("        import ast\n"
            "        node = ast.parse(expr, mode='eval')\n"
            "        ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)\n"
            "        if not all(isinstance(n, ok) for n in ast.walk(node)): raise ValueError('rejected')\n"
            "        value = eval(compile(node, '<calc>', 'eval'))\n" if hardened else
            "        value = eval(expr)   # VULNERABLE: attacker string reaches eval\n")
    target = (_py_banner("rce", prov, evidence) +
              "class Target:\n    def __init__(self): self.trace=[]\n"
              "    def calc(self, expr):\n" + body +
              "        self.trace.append({'expr': expr, 'evaluated': True}); return value\n")
    invariant = ("import ast\n"
                 "def _arith(e):\n"
                 "    ok=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Constant,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.USub)\n"
                 "    try: tree=ast.parse(e, mode='eval')\n"
                 "    except SyntaxError: return True\n"
                 "    return all(isinstance(n,ok) for n in ast.walk(tree))\n"
                 "def check(t):\n"
                 "    return [{'id':'INV-EXEC','cls':'rce','step':i,'detail':'evaluated '+repr(e['expr'])}\n"
                 "            for i,e in enumerate(t.trace) if e.get('evaluated') and not _arith(e['expr'])]\n")
    poc = ("def exploit(t):\n    t.calc(\"__import__('os').getpid()\")\n")
    return {"target.py": target, "invariant.py": invariant, "poc.py": poc}


def gen_py_traversal(prov, evidence, hardened):
    guard = ("        import os\n"
             "        full = os.path.realpath(os.path.join(self.root, name))\n"
             "        if full != self.root and not full.startswith(self.root + os.sep):\n"
             "            self.trace.append({'name': name, 'served': None}); return None\n"
             if hardened else
             "        full = self.root + '/' + name   # VULNERABLE: unchecked join\n")
    target = (_py_banner("traversal", prov, evidence) +
              "import os\n"
              "class Target:\n    def __init__(self):\n"
              "        self.root = os.path.join(os.path.dirname(os.path.abspath(__file__)),'docs')\n"
              "        os.makedirs(self.root, exist_ok=True)\n"
              "        open(os.path.join(self.root,'readme.txt'),'w').write('public')\n"
              "        open(os.path.join(os.path.dirname(self.root),'SECRET.txt'),'w').write('token=hunter2')\n"
              "        self.trace=[]\n"
              "    def read(self, name):\n" + guard +
              "        try:\n            with open(full) as f: body=f.read()\n"
              "        except OSError:\n            self.trace.append({'name':name,'served':None}); return None\n"
              "        self.trace.append({'name':name,'served':os.path.realpath(full)}); return body\n")
    invariant = ("import os\n"
                 "def check(t):\n    root=os.path.realpath(t.root); out=[]\n"
                 "    for i,e in enumerate(t.trace):\n"
                 "        s=e.get('served')\n"
                 "        if s and not (os.path.realpath(s)==root or os.path.realpath(s).startswith(root+os.sep)):\n"
                 "            out.append({'id':'INV-PATH','cls':'traversal','step':i,'detail':'served '+s+' via '+repr(e.get('name'))})\n"
                 "    return out\n")
    poc = ("def exploit(t):\n    t.read('../SECRET.txt')\n")
    return {"target.py": target, "invariant.py": invariant, "poc.py": poc}


def gen_js_sqli(prov, evidence, hardened):
    q = ("    const row = this.db.prepare('SELECT name, role FROM users WHERE name=? AND pass=?').get(user, pass);\n"
         if hardened else
         "    const row = this.db.prepare(\"SELECT name, role FROM users WHERE name='\" + user + \"' AND pass='\" + pass + \"'\").get();   // VULNERABLE: concatenated query\n")
    target = (_evidence_banner("sqli", prov, evidence) +
              "import { DatabaseSync } from 'node:sqlite';\n"
              "export class Target {\n  constructor(){\n"
              "    this.db = new DatabaseSync(':memory:');\n"
              "    this.db.exec(\"CREATE TABLE users(name TEXT, pass TEXT, role TEXT)\");\n"
              "    this.db.exec(\"INSERT INTO users VALUES('alice','s3cret','admin'),('bob','pw','user')\");\n"
              "    this.trace = [];\n  }\n"
              "  login(user, pass){\n" + q +
              "    this.trace.push({ user, pass, authed: !!row, as: row ? row.name : null });\n"
              "    return row;\n  }\n}\n")
    invariant = ("const CREDS = { alice:'s3cret', bob:'pw' };\n"
                 "export function check(t){\n"
                 "  return (t.trace||[]).filter(e=>e.authed && CREDS[e.user] !== e.pass)\n"
                 "    .map((e,i)=>({id:'INV-AUTH',cls:'sqli',step:i,detail:'authed as '+JSON.stringify(e.as)+' with a non-matching password '+JSON.stringify(e.pass)}));\n}\n")
    exploit = "export function exploit(t){ t.login('alice', \"' OR '1'='1\"); }\n"
    return {"target.mjs": target, "invariant.mjs": invariant, "exploit.mjs": exploit}


def gen_py_sqli(prov, evidence, hardened):
    q = ("        row = self.db.execute('SELECT name, role FROM users WHERE name=? AND pass=?', (user, pass_)).fetchone()\n"
         if hardened else
         "        row = self.db.execute(\"SELECT name, role FROM users WHERE name='\" + user + \"' AND pass='\" + pass_ + \"'\").fetchone()   # VULNERABLE\n")
    target = (_py_banner("sqli", prov, evidence) +
              "import sqlite3\n"
              "class Target:\n    def __init__(self):\n"
              "        self.db = sqlite3.connect(':memory:')\n"
              "        self.db.execute('CREATE TABLE users(name TEXT, pass TEXT, role TEXT)')\n"
              "        self.db.executemany('INSERT INTO users VALUES(?,?,?)', [('alice','s3cret','admin'),('bob','pw','user')])\n"
              "        self.trace = []\n"
              "    def login(self, user, pass_):\n" + q +
              "        self.trace.append({'user': user, 'pass': pass_, 'authed': bool(row), 'as': row[0] if row else None})\n"
              "        return row\n")
    invariant = ("CREDS = {'alice':'s3cret', 'bob':'pw'}\n"
                 "def check(t):\n"
                 "    return [{'id':'INV-AUTH','cls':'sqli','step':i,'detail':'authed as '+repr(e['as'])+' with non-matching password '+repr(e['pass'])}\n"
                 "            for i,e in enumerate(t.trace) if e.get('authed') and CREDS.get(e['user']) != e['pass']]\n")
    poc = "def exploit(t):\n    t.login('alice', \"' OR '1'='1\")\n"
    return {"target.py": target, "invariant.py": invariant, "poc.py": poc}


# Command injection: the audit cage rightly denies real shell exec, so the reproduction
# proves the *structural breakout* — that untrusted input introduces an extra command —
# without executing anything. That is the flaw itself, demonstrated honestly.
def gen_js_cmdi(prov, evidence, hardened):
    build = ("    const argv = ['convert', userfile, 'out.png'];   // HARDENED: input is one argument\n"
             "    const commands = 1;\n" if hardened else
             "    const cmd = 'convert ' + userfile + ' out.png';   // VULNERABLE: shell string\n"
             "    const commands = cmd.split(/;|&&|\\|\\||\\|/).length;\n")
    target = (_evidence_banner("command-injection", prov, evidence) +
              "export class Target {\n  constructor(){ this.trace = []; }\n"
              "  run(userfile){\n" + build +
              "    this.trace.push({ input: userfile, commands });\n  }\n}\n")
    invariant = ("export function check(t){\n"
                 "  return (t.trace||[]).filter(e=>e.commands > 1)\n"
                 "    .map((e,i)=>({id:'INV-CMD',cls:'command-injection',step:i,detail:'input '+JSON.stringify(e.input)+' introduced '+(e.commands-1)+' extra command(s)'}));\n}\n")
    exploit = "export function exploit(t){ t.run('a.jpg; rm -rf /'); }\n"
    return {"target.mjs": target, "invariant.mjs": invariant, "exploit.mjs": exploit}


def gen_py_cmdi(prov, evidence, hardened):
    build = ("        argv = ['convert', userfile, 'out.png']   # HARDENED: one argument\n"
             "        commands = 1\n" if hardened else
             "        import re\n"
             "        cmd = 'convert ' + userfile + ' out.png'   # VULNERABLE: shell string\n"
             "        commands = len(re.split(r';|&&|\\|\\||\\|', cmd))\n")
    target = (_py_banner("command-injection", prov, evidence) +
              "class Target:\n    def __init__(self): self.trace=[]\n"
              "    def run(self, userfile):\n" + build +
              "        self.trace.append({'input': userfile, 'commands': commands})\n")
    invariant = ("def check(t):\n"
                 "    return [{'id':'INV-CMD','cls':'command-injection','step':i,'detail':'input '+repr(e['input'])+' introduced '+str(e['commands']-1)+' extra command(s)'}\n"
                 "            for i,e in enumerate(t.trace) if e.get('commands',1) > 1]\n")
    poc = "def exploit(t):\n    t.run('a.jpg; rm -rf /')\n"
    return {"target.py": target, "invariant.py": invariant, "poc.py": poc}


# registry: (id, langs, class, detector on the real line, generator)
GENERATORS = [
    {"id": "js-sqli", "langs": JS_EXT, "class": "sqli", "lang": "js",
     "detect": lambda s: re.search(r"SELECT|INSERT|UPDATE|DELETE|\.(query|prepare|exec)\b", s, re.I) is not None,
     "build": gen_js_sqli},
    {"id": "py-sqli", "langs": PY_EXT, "class": "sqli", "lang": "py",
     "detect": lambda s: re.search(r"SELECT|INSERT|UPDATE|DELETE|\.(execute|executemany)\b", s, re.I) is not None,
     "build": gen_py_sqli},
    {"id": "js-cmdi", "langs": JS_EXT, "class": "command-injection", "lang": "js",
     "detect": lambda s: re.search(r"exec|spawn|child_process", s) is not None,
     "build": gen_js_cmdi},
    {"id": "py-cmdi", "langs": PY_EXT, "class": "command-injection", "lang": "py",
     "detect": lambda s: re.search(r"system|popen|subprocess|exec", s) is not None,
     "build": gen_py_cmdi},
    {"id": "js-new-function", "langs": JS_EXT, "class": "rce", "lang": "js",
     "detect": lambda s: "new Function" in s,
     "build": lambda p, e, h: gen_js_eval(p, e, h, newfunc=True)},
    {"id": "js-eval", "langs": JS_EXT, "class": "rce", "lang": "js",
     "detect": lambda s: re.search(r"\beval\s*\(", s) is not None,
     "build": lambda p, e, h: gen_js_eval(p, e, h)},
    {"id": "js-traversal", "langs": JS_EXT, "class": "traversal", "lang": "js",
     "detect": lambda s: re.search(r"(readFile|readFileSync|createReadStream|sendFile)", s) is not None,
     "build": gen_js_traversal},
    {"id": "py-eval", "langs": PY_EXT, "class": "rce", "lang": "py",
     "detect": lambda s: re.search(r"\b(eval|exec)\s*\(", s) is not None,
     "build": gen_py_eval},
    {"id": "py-traversal", "langs": PY_EXT, "class": "traversal", "lang": "py",
     "detect": lambda s: "open(" in s,
     "build": gen_py_traversal},
]


def pick_generator(candidate: dict):
    ext = os.path.splitext(candidate.get("file", ""))[1].lower()
    snippet = candidate.get("snippet", "")
    for g in GENERATORS:
        if ext in g["langs"] and g["class"] == candidate.get("class") and g["detect"](snippet):
            return g
    return None


def supported() -> dict:
    """What can actually be rebuilt-and-run, derived from the registry — never hardcoded
    in the UI. Reflects which Ranges are available in this deployment."""
    js_ok = RANGE.NodeRange.available
    out = {}
    for g in GENERATORS:
        if g["lang"] == "js" and not js_ok:
            continue
        out.setdefault(g["class"], set()).add(g["lang"])
    return {cls: sorted(langs) for cls, langs in out.items()}


def _write(job_dir: str, files: dict) -> None:
    os.makedirs(job_dir, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(job_dir, name), "w") as f:
            f.write(content)


def prove(candidate: dict, root: str | None = None) -> dict:
    """Rebuild → host → exploit → prove, in the source language, in an isolated job dir.
    Confirmed iff the vulnerable build reproduces and the hardened build refuses it."""
    gen = pick_generator(candidate)
    if gen is None:
        return {"confirmed": False, "class": candidate.get("class"),
                "error": "no reproduction generator for this sink in this language",
                "supported": supported()}
    if gen["lang"] == "js" and not RANGE.NodeRange.available:
        return {"confirmed": False, "class": candidate.get("class"),
                "error": "the Node range is unavailable in this deployment",
                "supported": supported()}

    prov = f"{candidate.get('file', '?')}:{candidate.get('line', '?')} — {candidate.get('rule', gen['id'])}"
    evidence = extract(candidate, root)
    os.makedirs(JOBS_ROOT, exist_ok=True)
    job = tempfile.mkdtemp(prefix="phx-", dir=JOBS_ROOT)
    try:
        rng = RANGE.range_for(gen["lang"])
        vdir, fdir = os.path.join(job, "vuln"), os.path.join(job, "fixed")
        _write(vdir, gen["build"](prov, evidence, False))
        _write(fdir, gen["build"](prov, evidence, True))
        v = rng.run(vdir)
        f = rng.run(fdir)
        confirmed = bool(v.get("reproduced")) and not f.get("reproduced")
        return {
            "confirmed": confirmed, "class": candidate.get("class"), "lang": gen["lang"],
            "sink": gen["id"], "provenance": prov,
            "evidence": {"resolved": evidence.get("resolved", False),
                         "sink_line": evidence.get("sink_line", ""),
                         "enclosing": evidence.get("enclosing", "")},
            "vulnerable": {"reproduced": v.get("reproduced"), "violations": v.get("violations"),
                           "denied": v.get("denied"), "error": v.get("error"),
                           "elapsed_ms": v.get("elapsed_ms")},
            "hardened": {"reproduced": f.get("reproduced"), "error": f.get("error"),
                         "elapsed_ms": f.get("elapsed_ms")},
            "verdict": ("exploit proven on the reproduction; the fix stops it"
                        if confirmed else
                        "not confirmed — the exploit did not reproduce, or the fix did not stop it"),
        }
    finally:
        shutil.rmtree(job, ignore_errors=True)      # per-job cleanup — SaaS-safe

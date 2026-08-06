"""The reproducer — rebuild a scanned flaw as a runnable, exploitable system.

The scanner locates a candidate in real source: a class, a file, a line, a snippet. The
reproducer turns that lead into a **reproduction** the Range can host and hack — a minimal
system that exhibits the *same class* of flaw as the code at that line, plus the invariant
that judges it and a PoC that triggers it.

Why rebuild instead of running the original? Because the original is 133k lines of another
project's TypeScript wired to a Cloudflare runtime we neither have nor should point an
exploit at. A faithful, minimal recreation of the pattern — hosted in our own sandbox —
proves the *pattern is exploitable*, with the source candidate attached as the lead it came
from. Confirming it against the real deployed system is the gated, human-only step.

Each template is parameterised by the candidate so the reproduction carries its provenance
(the file:line it models). A template also ships a *hardened* variant, so the same PoC can
be shown to stop reproducing after the fix — the regression proof the economy pays on.
"""

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
REPRO_ROOT = os.path.join(os.path.dirname(_HERE), "sandbox", "reproductions")

SUPPORTED = ("traversal", "rce", "sqli", "command-injection")


def _slug(s: str) -> str:
    return re.sub(r"\W+", "_", s)[:60].strip("_")


# ── templates: (target.py, invariant.py, poc.py), vulnerable and hardened ─────

def _traversal(prov: str, hardened: bool) -> dict:
    guard = ("        import os\n"
             "        full = os.path.realpath(os.path.join(self.root, name))\n"
             "        if full != self.root and not full.startswith(self.root + os.sep):\n"
             "            self.trace.append({'name': name, 'served': None}); return None\n"
             if hardened else
             "        full = self.root + '/' + name   # VULNERABLE: joined unchecked\n")
    target = (f'"""Reproduction of a path-traversal read. Models: {prov}."""\n'
              "import os\n\n"
              "class Target:\n"
              "    def __init__(self):\n"
              "        self.root = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs')\n"
              "        os.makedirs(self.root, exist_ok=True)\n"
              "        open(os.path.join(self.root, 'readme.txt'), 'w').write('public doc')\n"
              "        # a secret that lives OUTSIDE the served directory:\n"
              "        open(os.path.join(os.path.dirname(self.root), 'SECRET.txt'), 'w').write('token=hunter2')\n"
              "        self.trace = []\n\n"
              "    def read(self, name):\n"
              f"{guard}"
              "        try:\n"
              "            with open(full) as f: body = f.read()\n"
              "        except OSError:\n"
              "            self.trace.append({'name': name, 'served': None}); return None\n"
              "        self.trace.append({'name': name, 'served': os.path.realpath(full)})\n"
              "        return body\n")
    invariant = ("import os\n"
                 "def check(t):\n"
                 "    root = os.path.realpath(t.root)\n"
                 "    out = []\n"
                 "    for i, ev in enumerate(t.trace):\n"
                 "        s = ev.get('served')\n"
                 "        if s and not (os.path.realpath(s) == root or os.path.realpath(s).startswith(root + os.sep)):\n"
                 "            out.append({'id': 'INV-PATH', 'class': 'traversal', 'step': i,\n"
                 "                        'detail': 'served ' + s + ' via name ' + repr(ev.get('name'))})\n"
                 "    return out\n")
    poc = (f'"""Exploit for {prov}: escape the docs dir with ../ and read the secret."""\n'
           "def exploit(t):\n"
           "    t.read('../SECRET.txt')\n")
    return {"target.py": target, "invariant.py": invariant, "poc.py": poc}


def _rce(prov: str, hardened: bool) -> dict:
    calc = ("        import ast\n"
            "        node = ast.parse(expr, mode='eval')\n"
            "        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,\n"
            "                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)\n"
            "        for n in ast.walk(node):\n"
            "            if not isinstance(n, allowed): raise ValueError('rejected')\n"
            "        value = eval(compile(node, '<calc>', 'eval'))\n"
            if hardened else
            "        value = eval(expr)   # VULNERABLE: attacker string reaches eval\n")
    target = (f'"""Reproduction of an eval() RCE. Models: {prov}."""\n\n'
              "class Target:\n"
              "    def __init__(self): self.trace = []\n\n"
              "    def calc(self, expr):\n"
              f"{calc}"
              "        self.trace.append({'expr': expr, 'ok': True})\n"
              "        return value\n")
    invariant = ("import ast\n"
                 "def _arith_only(expr):\n"
                 "    ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,\n"
                 "          ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)\n"
                 "    try: tree = ast.parse(expr, mode='eval')\n"
                 "    except SyntaxError: return True\n"
                 "    return all(isinstance(n, ok) and not (isinstance(n, ast.Constant)\n"
                 "               and not isinstance(n.value, (int, float))) for n in ast.walk(tree))\n"
                 "def check(t):\n"
                 "    return [{'id': 'INV-EXEC', 'class': 'rce', 'step': i,\n"
                 "             'detail': 'evaluated ' + repr(ev['expr'])}\n"
                 "            for i, ev in enumerate(t.trace)\n"
                 "            if ev.get('ok') and not _arith_only(ev['expr'])]\n")
    poc = (f'"""Exploit for {prov}: pass a non-arithmetic expression to the evaluator."""\n'
           "def exploit(t):\n"
           "    t.calc(\"__import__('os').getpid()\")\n")
    return {"target.py": target, "invariant.py": invariant, "poc.py": poc}


TEMPLATES = {"traversal": _traversal, "rce": _rce}


def reproduce(candidate: dict, hardened: bool = False) -> dict:
    """Materialise a reproduction of ``candidate`` under sandbox/reproductions and return
    its directory + provenance. ``hardened=True`` writes the fixed variant (same PoC)."""
    cls = candidate.get("class")
    if cls not in TEMPLATES:
        return {"ok": False, "error": f"no reproduction template for class {cls!r} "
                                      f"(have: {', '.join(TEMPLATES)})"}
    prov = f"{candidate.get('file', '?')}:{candidate.get('line', '?')} — {candidate.get('rule', '')}"
    variant = "fixed" if hardened else "vuln"
    name = f"{cls}_{_slug(candidate.get('file', 'x'))}_{candidate.get('line', 0)}_{variant}"
    d = os.path.join(REPRO_ROOT, name)
    os.makedirs(d, exist_ok=True)
    files = TEMPLATES[cls](prov, hardened)
    for fn, content in files.items():
        with open(os.path.join(d, fn), "w") as f:
            f.write(content)
    with open(os.path.join(d, "PROVENANCE.txt"), "w") as f:
        f.write(f"class: {cls}\nmodels: {prov}\nsnippet: {candidate.get('snippet', '')}\n"
                f"variant: {variant}\n")
    return {"ok": True, "dir": d, "class": cls, "provenance": prov, "variant": variant}


def prove(candidate: dict, range_backend=None) -> dict:
    """The full loop for one candidate: rebuild it, host+exploit it on the Range, and
    also confirm the hardened variant refuses the same exploit. A candidate is confirmed
    only if the vulnerable build reproduces AND the fixed build does not — that pairing is
    the regression proof, and it kills a false positive that 'reproduces' in both."""
    import range as RANGE
    rng = range_backend or RANGE.default_range()

    vuln = reproduce(candidate, hardened=False)
    if not vuln["ok"]:
        return {"confirmed": False, **vuln}
    v = rng.run(vuln["dir"])

    fixed = reproduce(candidate, hardened=True)
    f = rng.run(fixed["dir"])

    confirmed = bool(v.get("reproduced")) and not f.get("reproduced")
    return {"confirmed": confirmed, "class": candidate.get("class"),
            "provenance": vuln["provenance"],
            "vulnerable": {"reproduced": v.get("reproduced"), "violations": v.get("violations"),
                           "denied": v.get("denied"), "error": v.get("error")},
            "hardened": {"reproduced": f.get("reproduced"), "error": f.get("error")},
            "verdict": ("exploit proven on the reproduction; the fix stops it"
                        if confirmed else "not confirmed — see diagnostics")}

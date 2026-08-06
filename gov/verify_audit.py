"""Acceptance checks for the real-code audit pipeline: scan -> rebuild -> host -> exploit
-> prove (SECURITY.md, extended). Model-free.

  1. The scanner locates a planted sink and ignores what can't be an exploit (a comment,
     a literal-only call) — it narrows, it does not cry wolf on every line.
  2. The reproducer rebuilds a scanned class as a runnable system, the Range hosts and
     exploits it, and the oracle proves it from the trace — for traversal and for rce.
  3. The false-positive killer: a candidate is confirmed only if the vulnerable build
     reproduces AND the hardened build refuses the same exploit.
  4. Blast-radius containment: the Range cage denies an exploit that reaches for the
     network — a reproduction cannot escape the sandbox (Article V).
  5. The production seam is declared, not faked: ComputerRange is unavailable and refuses
     to pretend it ran.

Run:  python3 gov/verify_audit.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import range as RANGE
import reproducer
import scanner

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


# ── 1. the scanner narrows, it doesn't cry wolf ──────────────────────────────
print("\n1. Scanner — locate the sink, skip what can't be an exploit")
fixture = tempfile.mkdtemp()
with open(os.path.join(fixture, "svc.ts"), "w") as f:
    f.write("export function run(userInput: string) {\n"
            "  const r = eval(userInput);            // real: attacker string to eval\n"
            "  // eval(userInput) in a comment must be ignored\n"
            "  const ok = eval('1 + 1');             // literal-only: not a candidate\n"
            "  return r + ok;\n"
            "}\n")
res = scanner.scan(fixture)
evals = [c for c in res["candidates"] if c["rule"] == "js-eval"]
check("the real eval(userInput) sink is found", len(evals) == 1,
      f"{len(evals)} eval candidate(s) at line(s) {[c['line'] for c in evals]}")
check("the commented and literal-only evals are not flagged",
      all(c["line"] == 2 for c in evals))

# ── 2+3. rebuild -> host -> exploit -> prove, with the FP killer ──────────────
print("\n2. Rebuild → host → exploit → prove (and the fix must stop it)")
trav = reproducer.prove({"class": "traversal", "file": "git/diff.ts", "line": 312,
                         "rule": "js-path-join", "snippet": "readFile(`${dir}/${filepath}`)"})
check("traversal: exploit proven on the rebuild", trav["confirmed"]
      and trav["vulnerable"]["reproduced"], trav["verdict"])
check("traversal: the hardened rebuild refuses the same exploit",
      not trav["hardened"]["reproduced"])

rce = reproducer.prove({"class": "rce", "file": "backends/eval.ts", "line": 42,
                        "rule": "js-eval", "snippet": "eval(userExpr)"})
check("rce: exploit proven on the rebuild", rce["confirmed"]
      and rce["vulnerable"]["reproduced"], rce["verdict"])
check("rce: the hardened rebuild refuses the same exploit",
      not rce["hardened"]["reproduced"])

print("\n3. The false-positive killer is structural")
check("confirmation requires vuln-reproduces AND fixed-does-not",
      trav["confirmed"] == (trav["vulnerable"]["reproduced"]
                            and not trav["hardened"]["reproduced"]))
unsup = reproducer.prove({"class": "xss", "file": "a.tsx", "line": 1})
check("a class with no template is declined, not faked",
      not unsup["confirmed"] and "no reproduction template" in unsup.get("error", ""))

# ── 4. blast-radius containment: the cage denies the network ─────────────────
print("\n4. Blast-radius — the Range cage denies a network-reaching exploit")
esc = tempfile.mkdtemp()
with open(os.path.join(esc, "target.py"), "w") as f:
    f.write("class Target:\n    def __init__(self): self.trace = []\n")
with open(os.path.join(esc, "invariant.py"), "w") as f:
    f.write("def check(t): return []\n")
with open(os.path.join(esc, "poc.py"), "w") as f:
    f.write("def exploit(t):\n    import socket; socket.socket()\n")
v = RANGE.InProcessRange().run(esc)
check("an exploit reaching for the network is denied", not v["reproduced"]
      and any("socket" in d for d in v.get("denied", [])), ", ".join(v.get("denied", [])))

# ── 5. the production seam is declared, not faked ────────────────────────────
print("\n5. The Cloudflare Computer range is a declared seam, not a fake")
check("ComputerRange advertises itself as not-yet-available",
      RANGE.ComputerRange.available is False)
raised = False
try:
    RANGE.ComputerRange().run(esc)
except NotImplementedError:
    raised = True
check("...and refuses to pretend it hosted anything", raised)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

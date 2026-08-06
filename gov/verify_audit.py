"""Product-grade acceptance suite for the audit pipeline: scan → extract → rebuild →
host → exploit → prove. Model-free; exercises every function and process.

Sections:
  A. Scanner        — finds real sinks, skips comments/literals, ranks, precision.
  B. Extraction     — pulls the real enclosing code from actual files (js + py).
  C. Generators     — the concrete sink is chosen from the real line; supported() derived.
  D. NodeRange      — real Node runs a JS reproduction; containment (exec/fs/net denied).
  E. InProcessRange — the Python cage runs a py reproduction; network denied.
  F. prove()        — confirm = vuln reproduces AND fix stops it; unsupported declined.
  G. Isolation      — many concurrent proves get distinct job dirs and clean up.
  H. End-to-end     — scan the real cloned repo and prove a JS finding (skipped if absent).

Run:  python3 gov/verify_audit.py
"""

import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import range as RANGE
import reproducer as RP
import scanner as SC

PASS, FAIL, SKIP = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m", "\033[33mSKIP\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def skip(name, why):
    print(f"  [{SKIP}] {name}  — {why}")


def _fixture(text, name):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(text)
    return d, p


# ── A. scanner ───────────────────────────────────────────────────────────────
print("\nA. Scanner — locate real sinks, skip what can't be an exploit")
d, _ = _fixture("export function run(userInput){\n"
                "  const r = eval(userInput);          // real sink\n"
                "  // eval(userInput) commented — ignore\n"
                "  const ok = eval('1 + 1');           // literal — not a candidate\n"
                "  return r + ok;\n}\n", "svc.ts")
res = SC.scan(d)
evals = [c for c in res["candidates"] if c["rule"] == "js-eval"]
check("the real eval(userInput) sink is found once", len(evals) == 1,
      f"lines {[c['line'] for c in evals]}")
check("the commented + literal evals are skipped", all(c["line"] == 2 for c in evals))
check("candidates are ranked (severity order)", res["candidates"] ==
      sorted(res["candidates"], key=lambda c: ({"critical":0,"high":1,"medium":2,"low":3}
             .get(c["severity"], 9), 0 if not c["is_test"] else 1, c["file"], c["line"])))

# ── B. extraction ────────────────────────────────────────────────────────────
print("\nB. Extraction — pull the real enclosing code from actual files")
djs, pjs = _fixture("function outer(){\n  const dir='/d';\n"
                    "  const data = readFileSync(`${dir}/${name}`);\n  return data;\n}\n", "diff.ts")
ex = RP.extract({"file": "diff.ts", "line": 3, "abspath": pjs, "class": "traversal",
                 "snippet": "const data = readFileSync(`${dir}/${name}`);"})
check("js: extraction resolves and captures the enclosing function", ex["resolved"]
      and "function outer" in ex["enclosing"] and "readFileSync" in ex["enclosing"])
dpy, ppy = _fixture("def handler(req):\n    name = req['n']\n"
                    "    return eval(name)  # sink\n", "svc.py")
exp = RP.extract({"file": "svc.py", "line": 3, "abspath": ppy, "class": "rce",
                  "snippet": "return eval(name)"})
check("py: extraction resolves and captures the enclosing def", exp["resolved"]
      and "def handler" in exp["enclosing"])
check("extraction degrades gracefully for a synthetic candidate",
      RP.extract({"file": "x.ts", "line": 1, "class": "rce", "snippet": "eval(x)"})["resolved"] is False)

# ── C. generators ────────────────────────────────────────────────────────────
print("\nC. Generators — the sink is chosen from the real line, not the class")
g1 = RP.pick_generator({"file": "a.ts", "class": "rce", "snippet": "new Function('return '+x)()"})
g2 = RP.pick_generator({"file": "a.ts", "class": "rce", "snippet": "const v = eval(x)"})
g3 = RP.pick_generator({"file": "a.ts", "class": "traversal", "snippet": "readFileSync(`${d}/${n}`)"})
check("new Function → js-new-function", g1 and g1["id"] == "js-new-function")
check("eval → js-eval", g2 and g2["id"] == "js-eval")
check("readFile → js-traversal", g3 and g3["id"] == "js-traversal")
sup = RP.supported()
check("supported() is derived from the registry (has rce+traversal)",
      "rce" in sup and "traversal" in sup and "py" in sup["rce"])

# ── D. NodeRange (real JS execution + containment) ───────────────────────────
print("\nD. NodeRange — real Node runs the reproduction, and the cage holds")
if not RANGE.NodeRange.available:
    skip("NodeRange tests", "node runtime not available")
else:
    job = tempfile.mkdtemp()
    v = os.path.join(job, "v"); f = os.path.join(job, "f")
    RP._write(v, RP.gen_js_eval("fixture", {}, False))
    RP._write(f, RP.gen_js_eval("fixture", {}, True))
    rv, rf = RANGE.NodeRange().run(v), RANGE.NodeRange().run(f)
    check("a vulnerable JS reproduction is exploited (real Node)", rv["reproduced"]
          and rv.get("elapsed_ms", 0) >= 0, f"{rv.get('elapsed_ms')}ms")
    check("the hardened JS reproduction refuses the same exploit", not rf["reproduced"])
    # containment
    cj = tempfile.mkdtemp()
    with open(cj + "/target.mjs", "w") as fh: fh.write("export class Target{constructor(){this.trace=[]}}\n")
    with open(cj + "/invariant.mjs", "w") as fh: fh.write("export function check(){return[]}\n")
    with open(cj + "/exploit.mjs", "w") as fh:
        fh.write("export async function exploit(t){let r={};"
                 "try{const cp=await import('node:child_process');cp.execSync('id');r.x='RAN'}catch{r.x='no'}"
                 "try{const fs=await import('node:fs');fs.writeFileSync('/tmp/PHX_T','1');r.w='RAN'}catch{r.w='no'}"
                 "try{await fetch('http://127.0.0.1:1');r.n='RAN'}catch{r.n='no'}"
                 "t.trace.push(r);throw new Error(JSON.stringify(r))}\n")
    cv = RANGE.NodeRange().run(cj)
    err = cv.get("error", "")
    check("child_process is denied in the range", '"x":"no"' in err)
    check("out-of-dir write is denied in the range", '"w":"no"' in err
          and not os.path.exists("/tmp/PHX_T"))
    check("network is blocked in the range", '"n":"no"' in err)

# ── E. InProcessRange (python cage) ──────────────────────────────────────────
print("\nE. InProcessRange — the Python cage runs a reproduction, network denied")
pj = tempfile.mkdtemp()
pv, pf = os.path.join(pj, "v"), os.path.join(pj, "f")
RP._write(pv, RP.gen_py_eval("fixture", {}, False))
RP._write(pf, RP.gen_py_eval("fixture", {}, True))
rv = RANGE.InProcessRange().run(pv)
rf = RANGE.InProcessRange().run(pf)
check("a vulnerable python reproduction is exploited", rv["reproduced"])
check("the hardened python reproduction refuses it", not rf["reproduced"])
netj = tempfile.mkdtemp()
with open(netj + "/target.py", "w") as fh: fh.write("class Target:\n    def __init__(self): self.trace=[]\n")
with open(netj + "/invariant.py", "w") as fh: fh.write("def check(t): return []\n")
with open(netj + "/poc.py", "w") as fh: fh.write("def exploit(t):\n    import socket; socket.socket()\n")
nv = RANGE.InProcessRange().run(netj)
check("the python cage denies the network", not nv["reproduced"]
      and any("socket" in x for x in nv.get("denied", [])))

# ── F. prove() — the confirm law ─────────────────────────────────────────────
print("\nF. prove() — confirmed = vuln reproduces AND fix stops it")
lang = "js" if RANGE.NodeRange.available else "py"
snip = "eval(userInput)"
fname = "svc.ts" if lang == "js" else "svc.py"
out = RP.prove({"class": "rce", "file": fname, "line": 1, "snippet": snip, "rule": "eval"})
check("a real sink is confirmed", out["confirmed"] and out["vulnerable"]["reproduced"]
      and not out["hardened"]["reproduced"], out.get("verdict", ""))
check("timing is reported", out["vulnerable"].get("elapsed_ms") is not None)
unsup = RP.prove({"class": "xss", "file": "a.tsx", "line": 1, "snippet": "el.innerHTML=x"})
check("an unsupported sink is declined, not faked", not unsup["confirmed"]
      and "no reproduction generator" in unsup.get("error", ""))

# ── G. isolation (SaaS: concurrent guests) ───────────────────────────────────
print("\nG. Isolation — concurrent proves get distinct dirs and clean up")
import glob
before = len(glob.glob(os.path.join(RP.JOBS_ROOT, "phx-*")))
outs = [None] * 6
def _w(i): outs[i] = RP.prove({"class": "rce", "file": fname, "line": 1, "snippet": snip})
ts = [threading.Thread(target=_w, args=(i,)) for i in range(6)]
[t.start() for t in ts]; [t.join() for t in ts]
check("all concurrent proves succeeded", all(o and o["confirmed"] for o in outs))
after = len(glob.glob(os.path.join(RP.JOBS_ROOT, "phx-*")))
check("every job dir was cleaned up (no leak)", after == before, f"{before} → {after}")

# ── H. end-to-end on the real repo ───────────────────────────────────────────
print("\nH. End-to-end — scan the real repo and prove a JS finding")
REPO = "/workspace/amonxnye/computer-cloufare"
if not (os.path.isdir(REPO) and RANGE.NodeRange.available):
    skip("real-repo end-to-end", "cloned repo or node not present")
else:
    rres = SC.scan(REPO)
    jsprov = None
    for c in rres["candidates"]:
        if c["class"] in ("rce", "traversal") and RP.lang_of(c["file"]) == "js":
            c = dict(c); c["abspath"] = os.path.join(REPO, c["file"])
            r = RP.prove(c)
            if r["confirmed"]:
                jsprov = (c, r); break
    check("a real JS finding is rebuilt and exploited from actual source",
          jsprov is not None and jsprov[1]["evidence"]["resolved"],
          jsprov[1]["provenance"] if jsprov else "none confirmed")

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

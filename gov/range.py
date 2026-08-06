"""The Range — where a reproduction is hosted and hacked, for real (SECURITY.md, extended).

A *reproduction* is a self-contained rebuild of one vulnerability, written in the source
language of the code it models, as a set of files in a job directory:

    JS   target.mjs   invariant.mjs   exploit.mjs     (run by NodeRange)
    py   target.py    invariant.py    poc.py          (run by InProcessRange)

A *Range* hosts that reproduction and runs the exploit against it in a real, contained
runtime, then returns the invariant's trace-based verdict. The exploit's own claims are
ignored; only the invariant's reading of the trace counts (the harness's first law).

Containment is real, not a promise:

- ``NodeRange`` runs the JS reproduction with **actual Node** under Node's permission
  model (``--experimental-permission`` scoped to the job dir): ``child_process`` is
  denied, filesystem access outside the job dir is denied, and a preload neutralises the
  network. A JS flaw is therefore proven *as JS*, not by a stand-in.
- ``InProcessRange`` runs the Python reproduction here, in a stripped subprocess with an
  audit cage (no network, no subprocess, no write outside the dir).
- ``ComputerRange`` is the production seam: each reproduction in its own Cloudflare
  Computer workspace. Declared, not wired — it needs the Cloudflare runtime.

Every Range is per-job: a reproduction runs in a directory of its own, so concurrent
runs (many guests at once) never share state. The caller owns the directory's lifecycle.
"""

import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

# Python audit-cage denials (InProcessRange).
DENIED_EVENTS = (
    "socket.", "urllib.Request", "http.client", "ftplib.", "smtplib.",
    "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
    "os.fork", "os.forkpty", "pty.spawn", "ctypes.", "winreg.",
)


class Verdict(dict):
    """A reproduction's result. ``reproduced`` is True only if the invariant found a
    violation in the trace. ``elapsed_ms`` times the host+exploit run."""


def _node() -> str | None:
    for cand in ("node", "/opt/node22/bin/node", "/usr/bin/node", "/usr/local/bin/node"):
        try:
            r = subprocess.run([cand, "--version"], capture_output=True, timeout=8)
            if r.returncode == 0:
                return cand
        except (OSError, subprocess.SubprocessError):
            continue
    return None


NODE = _node()
NODE_AVAILABLE = NODE is not None


class Range:
    lang = ""
    def run(self, job_dir: str) -> Verdict:              # pragma: no cover - interface
        raise NotImplementedError


# ── Python reproductions (the audit cage) ─────────────────────────────────────

_PY_RUNNER = r"""
import importlib.util, json, os, sys
sys.dont_write_bytecode = True
JOB = os.path.realpath(sys.argv[1]); DENIED = {denied!r}; denials = []
def _inside(p):
    try: return os.path.realpath(str(p)).startswith(JOB + os.sep) or os.path.realpath(str(p)) == JOB
    except Exception: return False
def _hook(event, args):
    if event.startswith(DENIED):
        denials.append(event); raise PermissionError("REFUSED: " + event)
    if event == "open":
        path, mode = (args + (None, None))[:2]
        if mode and any(c in str(mode) for c in "wxa+") and not _inside(path):
            denials.append("open:" + str(mode)); raise PermissionError("REFUSED: write " + str(path))
sys.addaudithook(_hook)
def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(JOB, name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
v = {{"reproduced": False, "violations": [], "denied": [], "requests": 0, "error": ""}}
t = None
try:
    target = _load("target"); invariant = _load("invariant"); poc = _load("poc")
    t = target.Target(); poc.exploit(t)
    v["requests"] = len(getattr(t, "trace", [])); v["violations"] = invariant.check(t)
except BaseException as e:
    v["error"] = (type(e).__name__ + ": " + str(e))[:400]
    if t is not None:
        try: v["violations"] = _load("invariant").check(t); v["requests"] = len(getattr(t, "trace", []))
        except Exception: pass
v["reproduced"] = bool(v["violations"]); v["denied"] = sorted(set(denials))
print("__RANGE__ " + json.dumps(v))
"""


class InProcessRange(Range):
    lang = "py"

    def __init__(self, timeout_s: int = 30):
        self.timeout_s = timeout_s

    def run(self, job_dir: str) -> Verdict:
        runner = _PY_RUNNER.format(denied=DENIED_EVENTS)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": job_dir,
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        t0 = time.time()
        try:
            proc = subprocess.run([sys.executable, "-c", runner, job_dir], cwd=job_dir,
                                  env=env, capture_output=True, text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                           error=f"timeout after {self.timeout_s}s", elapsed_ms=self.timeout_s * 1000)
        dt = int((time.time() - t0) * 1000)
        for line in (proc.stdout or "").splitlines():
            if line.startswith("__RANGE__ "):
                v = Verdict(**json.loads(line[len("__RANGE__ "):])); v["elapsed_ms"] = dt
                return v
        return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                       error=("no verdict: " + (proc.stderr or proc.stdout or "").strip()[-300:]),
                       elapsed_ms=dt)


# ── JavaScript reproductions (real Node, contained) ───────────────────────────

_NETBLOCK = """// defence-in-depth: neutralise network vectors before the reproduction runs
globalThis.fetch = () => { throw new Error('REFUSED: network disabled in the range'); };
try { const net = await import('node:net'); net.Socket.prototype.connect = () => { throw new Error('REFUSED: net.connect disabled'); }; } catch {}
try { const dns = await import('node:dns'); const b=(_h,_o,cb)=>{ const e=new Error('REFUSED: dns disabled'); (cb||_o)(e); }; dns.lookup=b; } catch {}
"""

_JS_RUNNER = """import { Target } from './target.mjs';
import { check } from './invariant.mjs';
import { exploit } from './exploit.mjs';
const v = { reproduced:false, violations:[], denied:[], requests:0, error:'' };
let t;
try { t = new Target(); await exploit(t); v.requests = (t.trace||[]).length; v.violations = check(t); }
catch (e) {
  v.error = String(e && e.message ? e.message : e).slice(0,400);
  if (String(e).includes('ERR_ACCESS_DENIED')) v.denied.push('fs-or-exec');
  if (String(e).includes('REFUSED')) v.denied.push('network');
  try { v.violations = check(t); v.requests = (t.trace||[]).length; } catch {}
}
v.reproduced = v.violations.length > 0;
console.log('__RANGE__ ' + JSON.stringify(v));
"""


class NodeRange(Range):
    """Run a JS reproduction with real Node, contained by the permission model + a
    net-block preload. child_process denied, fs limited to the job dir, network refused."""
    lang = "js"
    available = NODE_AVAILABLE

    def __init__(self, timeout_s: int = 30):
        self.timeout_s = timeout_s

    def run(self, job_dir: str) -> Verdict:
        if not NODE_AVAILABLE:
            return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                           error="node runtime not available", elapsed_ms=0)
        # write the harness plumbing the reproduction plugs into
        with open(os.path.join(job_dir, "netblock.mjs"), "w") as f:
            f.write(_NETBLOCK)
        with open(os.path.join(job_dir, "runner.mjs"), "w") as f:
            f.write(_JS_RUNNER)
        cmd = [NODE, "--experimental-permission", "--experimental-sqlite",
               f"--allow-fs-read={job_dir}", f"--allow-fs-write={job_dir}",
               "--import", os.path.join(job_dir, "netblock.mjs"),
               os.path.join(job_dir, "runner.mjs")]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": job_dir,
               "NODE_NO_WARNINGS": "1"}
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=job_dir, env=env, capture_output=True,
                                  text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                           error=f"timeout after {self.timeout_s}s", elapsed_ms=self.timeout_s * 1000)
        dt = int((time.time() - t0) * 1000)
        for line in (proc.stdout or "").splitlines():
            if line.startswith("__RANGE__ "):
                d = json.loads(line[len("__RANGE__ "):])
                d.setdefault("denied", [])
                v = Verdict(**d); v["elapsed_ms"] = dt
                return v
        return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                       error=("no verdict: " + (proc.stderr or proc.stdout or "").strip()[-300:]),
                       elapsed_ms=dt)


class ComputerRange(Range):
    """Production seam: host each reproduction in its own Cloudflare Computer workspace
    with real execution and real isolation. Not wired — needs the Cloudflare runtime."""
    lang = "*"
    available = False

    def run(self, job_dir: str) -> Verdict:              # pragma: no cover - not wired
        raise NotImplementedError(
            "ComputerRange needs the Cloudflare Computer runtime (Workers + Durable "
            "Objects + a container/isolate backend). The reproduction contract is "
            "identical to NodeRange/InProcessRange; only the host changes.")


def range_for(lang: str) -> Range:
    """Pick the Range for a reproduction's language."""
    if lang == "js":
        return NodeRange()
    if lang == "py":
        return InProcessRange()
    raise ValueError(f"no range for language {lang!r}")

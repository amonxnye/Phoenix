"""The Range — where a reproduced system is hosted and hacked (SECURITY.md, extended).

A *reproduction* is a self-contained rebuild of one vulnerability: three files —

    target.py     the minimal system that exhibits the flaw, with a .trace of what it did
    invariant.py  the oracle: check(target) -> [violations], the property that must hold
    poc.py        the exploit: exploit(target), which provokes the flaw through the target

A *Range* hosts that reproduction and runs the exploit against it, then hands back the
trace-based verdict. The exploit's own claims are ignored; only the invariant's reading
of the trace counts (the harness's first law).

Two backends, one interface:

- ``InProcessRange`` — runs the reproduction here, in this process, inside the same audit
  cage the PoC runner uses: no network, no subprocess, no write outside the box. It runs
  anywhere Python does, needs nothing, and is what the tests and the offline demo use.

- ``ComputerRange`` — the production target: each reproduction gets its **own Cloudflare
  Computer workspace** (a VFS in a Durable Object with a real execution backend — see the
  scanned `@cloudflare/computer`). The system is *actually rebuilt and run* there, and the
  exploit runs against it with real per-workspace isolation as the blast radius. Same
  invariant, same gate. It is a declared seam, not yet wired — standing it up needs the
  Cloudflare runtime, which this offline engine cannot host.

The Range never reaches a real system. Hosting a *reproduction you built* and exploiting
it is defensive research; pointing an exploit at someone's running service is the gated,
human-only act (SECURITY.md §4), and no backend here can perform it.
"""

import importlib.util
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

DENIED_EVENTS = (
    "socket.", "urllib.Request", "http.client", "ftplib.", "smtplib.",
    "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
    "os.fork", "os.forkpty", "pty.spawn", "ctypes.", "winreg.",
)


class Verdict(dict):
    """A reproduction's result. ``reproduced`` is True only if the invariant — reading
    the trace — found a violation. Everything else is diagnostics."""


class Range:
    def run(self, repro_dir: str) -> Verdict:            # pragma: no cover - interface
        raise NotImplementedError


# ── the in-process range (the cage, generalized from pocrunner) ───────────────

_HOST_CHILD = r"""
import importlib.util, json, os, sys
sys.dont_write_bytecode = True
REPRO = sys.argv[1]
SANDBOX = os.path.realpath(REPRO)
DENIED = {denied!r}
denials = []

def _inside(p):
    try: return os.path.realpath(str(p)).startswith(SANDBOX + os.sep)
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
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPRO, name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

verdict = {{"reproduced": False, "violations": [], "denied": [], "requests": 0, "error": ""}}
t = None
try:
    target = _load("target"); invariant = _load("invariant"); poc = _load("poc")
    check = invariant.check
    t = target.Target()
    poc.exploit(t)
    verdict["requests"] = len(getattr(t, "trace", []))
    verdict["violations"] = check(t)
except BaseException as e:
    verdict["error"] = (type(e).__name__ + ": " + str(e))[:400]
    if t is not None:
        try:
            verdict["violations"] = _load("invariant").check(t)
            verdict["requests"] = len(getattr(t, "trace", []))
        except Exception: pass
verdict["reproduced"] = bool(verdict["violations"])
verdict["denied"] = sorted(set(denials))
print("__RANGE__ " + json.dumps(verdict))
"""


class InProcessRange(Range):
    """Host and hack a reproduction in a stripped subprocess with an audit cage. The
    exploit cannot reach the network, spawn a process, or write outside the reproduction
    dir — the same Article-V enforcement the PoC runner uses, now backend-agnostic."""

    def __init__(self, timeout_s: int = 30):
        self.timeout_s = timeout_s

    def run(self, repro_dir: str) -> Verdict:
        child = _HOST_CHILD.format(denied=DENIED_EVENTS)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": repro_dir,
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run([sys.executable, "-c", child, repro_dir],
                                  cwd=repro_dir, env=env, capture_output=True,
                                  text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                           error=f"reproduction exceeded {self.timeout_s}s")
        for line in (proc.stdout or "").splitlines():
            if line.startswith("__RANGE__ "):
                return Verdict(**json.loads(line[len("__RANGE__ "):]))
        return Verdict(reproduced=False, violations=[], denied=[], requests=0,
                       error=("no verdict: " + (proc.stderr or proc.stdout or "").strip()[-300:]))


class ComputerRange(Range):
    """Production seam: host each reproduction in its own Cloudflare Computer workspace
    and exploit it there, with real code execution and real isolation. Not wired yet —
    it needs the Cloudflare runtime (@cloudflare/computer), which this engine can't host.
    Named here so the pipeline is backend-agnostic the day that substrate is available."""

    available = False

    def run(self, repro_dir: str) -> Verdict:            # pragma: no cover - not wired
        raise NotImplementedError(
            "ComputerRange needs the Cloudflare Computer runtime (Workers + Durable "
            "Objects + a container/isolate backend). Use InProcessRange offline; wire "
            "this when a Computer deployment is reachable — the reproduction contract "
            "(target/invariant/poc) is identical, only the host changes.")


def default_range() -> Range:
    return InProcessRange()

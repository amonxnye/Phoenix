"""Ingestion — R1, the read-only clone.

Three things this must be, in order of how expensive it is to get wrong:

1. **Read-only, enforced rather than promised.** The clone runs in an environment with
   every credential stripped — no GITHUB_TOKEN, no GH_TOKEN, nothing whose name says
   token, secret, key or password. A write to the origin is then not merely
   forbidden; it is impossible, which is the only kind of forbidden that holds
   (Charter §5, and Article V of the constitution it came from).

2. **Bounded.** GitHub HTTPS URLs only, a shallow single-branch clone, a wall-clock
   timeout, and a size ceiling checked after the fact — the clone is deleted if it
   exceeds it. A public form that can fill a disk is how the settlement lost eight
   days once; the same volume hosts this.

3. **Temporary.** The clone exists for the length of one analysis. The index and the
   findings persist in the store; the source does not need to.
"""

import os
import re
import shutil
import subprocess
import tempfile

GITHUB = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
MAX_MB = 200
TIMEOUT_S = 180
_SECRET = ("TOKEN", "SECRET", "KEY", "PASSWORD", "PASSWD", "CREDENTIAL")


def accepted(url: str) -> tuple[bool, str]:
    m = GITHUB.match((url or "").strip())
    if not m:
        return False, ("only https://github.com/<owner>/<repo> is accepted — the read-only "
                       "guarantee is enforced for that host and not promised for others")
    return True, f"{m.group(1)}/{m.group(2)}"


def _clean_env() -> dict:
    """The child sees the proxy and the path, and nothing that could authenticate."""
    env = {}
    for k, v in os.environ.items():
        up = k.upper()
        if any(s in up for s in _SECRET):
            continue
        if up in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") or "PROXY" in up \
                or up.startswith("GIT_SSL") or up.startswith("SSL_") or up == "CURL_CA_BUNDLE":
            env[k] = v
    env["GIT_TERMINAL_PROMPT"] = "0"           # never wait on a credential prompt
    env["GIT_ASKPASS"] = "/bin/true" if os.path.exists("/bin/true") else "true"
    return env


def _size_mb(path: str) -> float:
    total = 0
    for dp, dn, fn in os.walk(path):
        dn[:] = [d for d in dn if d != ".git"]
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total / 1_048_576


def clone(url: str) -> dict:
    """Shallow, read-only, temporary. Returns {path, sha, name} or {error}."""
    ok, name = accepted(url)
    if not ok:
        return {"error": name}
    tmp = tempfile.mkdtemp(prefix="mechanic-")
    dest = os.path.join(tmp, name.split("/")[-1])
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--quiet",
             url, dest],
            capture_output=True, text=True, timeout=TIMEOUT_S, env=_clean_env())
    except subprocess.TimeoutExpired:
        remove(tmp)
        return {"error": f"clone exceeded {TIMEOUT_S}s"}
    except OSError as e:
        remove(tmp)
        return {"error": f"git unavailable: {e}"}
    if proc.returncode != 0:
        remove(tmp)
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no detail"]
        return {"error": f"clone failed: {tail[0][:200]}"}
    mb = _size_mb(dest)
    if mb > MAX_MB:
        remove(tmp)
        return {"error": f"repository is {mb:.0f} MB, over the {MAX_MB} MB ceiling"}
    sha = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True,
                         text=True, timeout=10, env=_clean_env()).stdout.strip()
    return {"path": dest, "tmp": tmp, "sha": sha, "name": name.split("/")[-1],
            "size_mb": round(mb, 1)}


def remove(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)

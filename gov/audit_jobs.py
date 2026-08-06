"""Audit jobs — per-guest, isolated scan targets for the SaaS surface.

There are no accounts; everyone is a guest. So a scan target cannot be an arbitrary
server path (that would let one guest read the box). A guest submits a **GitHub repo
URL**; it is shallow-cloned into a job directory of its own, scanned, and referenced by
an opaque ``jobId``. Prove looks the job up by id and reads the real source from that
clone — never a path the client chose. Jobs expire on a TTL and are capped, and cloned
directories are removed on eviction. A pre-cloned **sample** is offered so the product
works instantly without a clone.

Isolation is the property that makes this multi-tenant: every clone is its own directory,
every prove runs in its own job dir (reproducer), and nothing is shared between guests.
"""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

JOBS_ROOT = os.path.join(tempfile.gettempdir(), "phoenix-audit-jobs")
TTL_S = 1800                                   # a job is good for 30 minutes
MAX_JOBS = 40
CLONE_TIMEOUT_S = 240

# Only https GitHub repos. No ssh, no arbitrary hosts (SSRF/abuse surface), no query.
GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?$")

SAMPLE_PATHS = [
    "/workspace/amonxnye/computer-cloufare",   # pre-cloned in this environment
]

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _gc() -> None:
    now = time.time()
    with _LOCK:
        for jid in list(_JOBS):
            if now - _JOBS[jid]["ts"] > TTL_S:
                _evict(jid)
        while len(_JOBS) > MAX_JOBS:
            oldest = min(_JOBS, key=lambda k: _JOBS[k]["ts"])
            _evict(oldest)


def _evict(jid: str) -> None:
    j = _JOBS.pop(jid, None)
    if j and j.get("owned"):
        shutil.rmtree(j["root"], ignore_errors=True)


def register(root: str, label: str, owned: bool) -> str:
    """Record a scan target and return its jobId. ``owned`` clones are deleted on
    eviction; a borrowed path (a sample, a dev path) is never deleted."""
    _gc()
    jid = "job_" + uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[jid] = {"root": os.path.realpath(root), "label": label,
                      "owned": owned, "ts": time.time()}
    return jid


def root_of(jid: str) -> str | None:
    with _LOCK:
        j = _JOBS.get(jid)
        if not j:
            return None
        j["ts"] = time.time()
        return j["root"]


def clone(url: str) -> tuple[str, str, str]:
    """Shallow-clone a GitHub repo into an owned job dir. Returns (jobId, label, root)."""
    url = (url or "").strip()
    if not GITHUB_URL.match(url):
        raise ValueError("only https://github.com/<owner>/<repo> URLs are accepted")
    os.makedirs(JOBS_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(prefix="repo-", dir=JOBS_ROOT)
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(["git", "clone", "--depth", "1", url, d],
                              env=env, capture_output=True, text=True, timeout=CLONE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError(f"clone timed out after {CLONE_TIMEOUT_S}s")
    if proc.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("clone failed: " + (proc.stderr or "").strip()[-200:])
    label = url.rstrip("/").removesuffix(".git")
    return register(d, label, owned=True), label, d


def sample() -> tuple[str, str, str]:
    """Register the pre-cloned sample repo (borrowed, never deleted)."""
    for p in SAMPLE_PATHS:
        if os.path.isdir(p):
            label = "sample: " + os.path.basename(p)
            return register(p, label, owned=False), label, os.path.realpath(p)
    raise RuntimeError("no sample repository is available in this deployment")


def dev_path(path: str) -> tuple[str, str, str]:
    """Register a local path (borrowed) — only reachable by an authenticated operator."""
    root = os.path.realpath(path)
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {path}")
    return register(root, path, owned=False), path, root

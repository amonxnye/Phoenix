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

import io
import os
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid

JOBS_ROOT = os.path.join(tempfile.gettempdir(), "phoenix-audit-jobs")
TTL_S = 1800                                   # a job is good for 30 minutes
MAX_JOBS = 40
CLONE_TIMEOUT_S = 240
DL_TIMEOUT_S = 120
MAX_TARBALL_BYTES = 120 * 1024 * 1024          # 120 MB extracted-archive cap

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


def _owner_repo(url: str) -> tuple[str, str]:
    m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", url)
    if not m:
        raise ValueError("only https://github.com/<owner>/<repo> URLs are accepted")
    return m.group(1), m.group(2)


def _ssl_context() -> ssl.SSLContext:
    """Trust the system CAs plus any deployment/proxy CA bundle, so the tarball fetch
    verifies whether we're behind a corporate/agent proxy or not."""
    ctx = ssl.create_default_context()
    for env in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        p = os.environ.get(env, "").strip()
        if p and os.path.exists(p):
            try:
                ctx.load_verify_locations(p)
            except OSError:
                pass
    for p in ("/root/.ccr/ca-bundle.crt", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(p):
            try:
                ctx.load_verify_locations(p)
            except OSError:
                pass
    return ctx


def _extract_stripped(data: bytes, dest: str) -> None:
    """Extract a GitHub tar.gz into ``dest``, stripping the top-level `<repo>-<sha>/`
    directory. Path-traversal, symlink and device members are skipped for safety."""
    root = os.path.realpath(dest)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            rel = parts[1] if len(parts) > 1 else ""
            if not rel:
                continue
            target = os.path.realpath(os.path.join(dest, rel))
            if target != root and not target.startswith(root + os.sep):
                continue                           # escapes dest → skip
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isreg():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=256 * 1024)
            # symlinks / hardlinks / devices deliberately not extracted


def _download_tarball(owner: str, repo: str, dest: str) -> None:
    """Fetch the default-branch tarball over HTTPS (no git binary needed) and extract it."""
    ctx = _ssl_context()
    urls = [f"https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD",
            f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/main",
            f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/master"]
    last = "no url"
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "phoenix-audit"})
            with urllib.request.urlopen(req, timeout=DL_TIMEOUT_S, context=ctx) as r:
                data = r.read(MAX_TARBALL_BYTES + 1)
            if len(data) > MAX_TARBALL_BYTES:
                raise RuntimeError("repository archive exceeds the size limit")
            _extract_stripped(data, dest)
            return
        except Exception as e:                     # try the next ref
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"tarball download failed ({last})")


def _git_clone(url: str, dest: str) -> None:
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(["git", "clone", "--depth", "1", url, dest],
                          env=env, capture_output=True, text=True, timeout=CLONE_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError("git clone failed: " + (proc.stderr or "").strip()[-200:])


def clone(url: str) -> tuple[str, str, str]:
    """Fetch a public GitHub repo into an owned job dir. Prefers an HTTPS tarball download
    (works with no git binary — the common slim-container case); falls back to `git clone`
    where git exists. Returns (jobId, label, root)."""
    url = (url or "").strip()
    owner, repo = _owner_repo(url)                 # also validates the URL
    os.makedirs(JOBS_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(prefix="repo-", dir=JOBS_ROOT)
    label = f"github.com/{owner}/{repo}"
    try:
        _download_tarball(owner, repo, d)
    except Exception as e_dl:
        if shutil.which("git"):
            try:
                _git_clone(url, d)
            except Exception as e_git:
                shutil.rmtree(d, ignore_errors=True)
                raise RuntimeError(f"could not fetch repo: {e_git}")
        else:
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(f"could not fetch repo: {e_dl}")
    if not any(os.scandir(d)):
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("the repository archive was empty")
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

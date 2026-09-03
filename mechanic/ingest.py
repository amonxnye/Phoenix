"""Ingestion — R1, read-only and dependency-free.

The first version shelled out to `git clone`. The deployed image has no git, and the
first public run said so: "git unavailable". A dependency the target machine may not
have is a capability gap the mechanic would flag in anyone else's code.

So: GitHub's archive endpoint, streamed over HTTPS with the standard library. It is
better than the clone was on every axis the Charter cares about.

- **Read-only, structurally.** An HTTP GET of a tarball cannot write to anything, and
  no request ever carries an Authorization header — `headers()` is the whole list,
  and a token in the environment does not change it (Charter §5).
- **Bounded while it happens.** The byte ceiling is enforced during the download and
  again during extraction, so a tar bomb (a small archive that expands to gigabytes)
  stops at the ceiling instead of filling the volume the settlement once lost eight
  days to. A member with a path outside the destination is refused; links are skipped.
- **Temporary.** The tree exists for one analysis. The index and findings persist.

The commit sha is not in the archive. It is resolved from GitHub's API when that
answers, and recorded as unknown when it does not — the analysis needs the tree, and
the run record deserves the truth about what it could and could not learn.
"""

import io
import os
import re
import shutil
import ssl
import tarfile
import tempfile
import urllib.error
import urllib.request

GITHUB = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
ARCHIVE = "https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD"
COMMIT = "https://api.github.com/repos/{owner}/{repo}/commits/HEAD"
MAX_MB = 200
TIMEOUT_S = 180
_CHUNK = 1 << 16


def accepted(url: str) -> tuple[bool, str]:
    m = GITHUB.match((url or "").strip())
    if not m:
        return False, ("only https://github.com/<owner>/<repo> is accepted — the read-only "
                       "guarantee is enforced for that host and not promised for others")
    return True, f"{m.group(1)}/{m.group(2)}"


def headers(accept: str = "application/x-gzip") -> dict:
    """Every header any request sends. There is no Authorization, and nothing in the
    environment can add one — that is the whole of the read-only guarantee."""
    return {"User-Agent": "phoenix-software-mechanic/0.1 (read-only analysis)",
            "Accept": accept}


def _ctx() -> ssl.SSLContext:
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        p = os.environ.get(var, "")
        if p and os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def _open(url: str, timeout: float, accept: str = "application/x-gzip"):
    req = urllib.request.Request(url, headers=headers(accept))
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())


def resolve_sha(owner: str, repo: str) -> str:
    """Best effort; '' when GitHub's API declines (rate limit, outage). Never raises."""
    try:
        with _open(COMMIT.format(owner=owner, repo=repo), 15,
                   accept="application/vnd.github.sha") as r:
            s = r.read(80).decode("ascii", "ignore").strip()
            return s if re.fullmatch(r"[0-9a-f]{40}", s) else ""
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def _safe(name: str) -> str:
    """Strip the archive's root directory and refuse anything that escapes."""
    parts = name.split("/")[1:]                   # Repo-HEAD/… → …
    if not parts or any(p in ("", ".", "..") for p in parts):
        return ""
    return "/".join(parts)


def fetch(url: str) -> dict:
    """Download and unpack the repository's current tree. Returns
    {path, tmp, sha, name, size_mb} or {error}. Never raises."""
    ok, name = accepted(url)
    if not ok:
        return {"error": name}
    owner, repo = name.split("/")
    cap = MAX_MB * 1_048_576
    tmp = tempfile.mkdtemp(prefix="mechanic-fetch-")   # distinct from the suite's fixtures
    dest = os.path.join(tmp, repo)
    try:
        buf = io.BytesIO()
        with _open(ARCHIVE.format(owner=owner, repo=repo), TIMEOUT_S) as r:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                buf.write(chunk)
                if buf.tell() > cap:
                    remove(tmp)
                    return {"error": f"archive exceeds the {MAX_MB} MB ceiling — stopped"}
        buf.seek(0)
        total = 0
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            for m in tf:
                rel = _safe(m.name)
                if not rel:
                    continue
                target = os.path.join(dest, rel)
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if not m.isfile():
                    continue                      # links and devices are never extracted
                total += m.size
                if total > cap:
                    remove(tmp)
                    return {"error": f"unpacked tree exceeds the {MAX_MB} MB ceiling — stopped"}
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except urllib.error.HTTPError as e:
        remove(tmp)
        why = {403: "GitHub refused the archive — usually rate limiting on this host, or "
                    "a repository that is private or disabled; try again shortly",
               404: "no such public repository (private ones are invisible to a "
                    "credential-less request, which is the point)",
               429: "GitHub is rate-limiting archive downloads — try again shortly"}
        return {"error": why.get(e.code, f"GitHub answered HTTP {e.code}")}
    except (urllib.error.URLError, OSError, tarfile.TarError, EOFError) as e:
        remove(tmp)
        return {"error": f"download failed: {type(e).__name__}: {e}"[:220]}
    if not os.path.isdir(dest):
        remove(tmp)
        return {"error": "archive contained no tree"}
    return {"path": dest, "tmp": tmp, "sha": resolve_sha(owner, repo), "name": repo,
            "size_mb": round(total / 1_048_576, 1)}


clone = fetch                                     # the name the callers already use


def remove(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)

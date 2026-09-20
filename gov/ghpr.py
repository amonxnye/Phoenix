"""Open a DRAFT pull request from a set of file contents — the one place Phoenix
pushes, and it happens only after a human approved at the gate (Article IV, XI).

GitHub's REST API over stdlib urllib, under the project's retry policy: reads are
retried, writes are made exactly once (a retried write could create twice). The
token comes from the environment of the orchestrator, never from a sandbox.
"""

import base64
import json
import os
import urllib.request

import netretry

API = "https://api.github.com"
USER_AGENT = "phoenix-improve/1.0 (+https://github.com/amonxnye/Phoenix)"


def _req(token: str, method: str, path: str, body: dict | None = None, ok404: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT, "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with netretry.urlopen(req, timeout=30, what=f"github {method} {path.split('?')[0]}",
                              idempotent=(method == "GET"), key="api.github.com") as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:                        # noqa: BLE001 — classified by status below
        if ok404 and netretry.status_of(e) == 404:
            return None
        raise


def open_pr(repo: str, token: str, branch: str, files: dict, title: str, body: str,
            base: str = "") -> str:
    """Create (or reuse) `branch` from the default branch, put every file, open a draft
    PR. Returns the PR's html URL."""
    base = base or (_req(token, "GET", f"/repos/{repo}") or {}).get("default_branch", "main")
    head_sha = _req(token, "GET", f"/repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
    if _req(token, "GET", f"/repos/{repo}/git/ref/heads/{branch}", ok404=True) is None:
        _req(token, "POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": head_sha})
    for path, content in files.items():
        cur = _req(token, "GET", f"/repos/{repo}/contents/{path}?ref={branch}", ok404=True)
        put = {"message": f"{title}\n\n{body[:400]}", "branch": branch,
               "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
        if cur and cur.get("sha"):
            put["sha"] = cur["sha"]
        _req(token, "PUT", f"/repos/{repo}/contents/{path}", put)
    pr = _req(token, "POST", f"/repos/{repo}/pulls",
              {"title": title, "head": branch, "base": base, "body": body, "draft": True})
    return pr.get("html_url", "")


def get_file(repo: str, token: str, branch: str, path: str) -> tuple[str | None, str]:
    """(content, sha) of a file on a branch, or (None, '') if it is not there."""
    cur = _req(token, "GET", f"/repos/{repo}/contents/{path}?ref={branch}", ok404=True)
    if not cur or not cur.get("sha"):
        return None, ""
    raw = base64.b64decode((cur.get("content") or "").encode("ascii")).decode("utf-8", "replace")
    return raw, cur["sha"]


def commit_file(repo: str, token: str, branch: str, path: str, content: str, message: str,
                expect_sha: str = "") -> dict:
    """One commit on `branch` that sets `path` to `content`. `expect_sha` is the blob
    the caller last saw — the API refuses the write if the file changed underneath
    (a concurrent human edit), which is the point. Returns {sha, url}."""
    if not expect_sha:
        _, expect_sha = get_file(repo, token, branch, path)
    put = {"message": message, "branch": branch,
           "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
    if expect_sha:
        put["sha"] = expect_sha
    r = _req(token, "PUT", f"/repos/{repo}/contents/{path}", put)
    c = r.get("commit") or {}
    return {"sha": c.get("sha", ""), "url": c.get("html_url", "")}


def configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip())

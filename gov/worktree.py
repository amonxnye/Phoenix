"""Worktree isolation — one checkout per agent (BUILDER.md §5.2, NEXT.md step 2).

An agent never edits the repository you are working in. It gets its own `git
worktree`: a clean checkout of the base commit on its own branch, in its own
directory. Your working tree — including uncommitted changes — is untouched, and
two agents working at once cannot collide.

This is what makes the undo real. Article IV divides the world into reversible work
and the one irreversible act; with a worktree, *everything the agent does is a
branch*, and the only irreversible step left is merging it. Nothing is destroyed if
an agent goes wrong: the branch is dropped and the base commit never moved.

Nothing here talks to a remote. Pushing is a separate, explicitly-enabled decision
made at the gate (see builder.py) — never a side effect of doing the work.
"""

import os
import re
import shutil
import subprocess
import tempfile

BRANCH_PREFIX = "phoenix"
AUTHOR_NAME = "Phoenix Agent"
AUTHOR_EMAIL = "agent@phoenix.local"


class GitError(RuntimeError):
    pass


def _git(repo: str, *args: str, check: bool = True, strip: bool = True) -> str:
    """Run git in `repo`. Our own plumbing — not agent-controlled input — so it runs
    with the ambient environment, unlike the oracle's deliberately stripped one.

    `strip=False` for output whose leading whitespace is data: `status --porcelain`
    puts the status in the first two columns, so a modified-not-staged file starts
    with a space, and stripping it silently eats the first character of the path."""
    proc = subprocess.run(("git", "-C", repo) + args,
                          capture_output=True, text=True, timeout=120)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[:300]}")
    out = proc.stdout or ""
    return out.strip() if strip else out.strip("\n")


def is_git(repo: str) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-inside-work-tree") == "true"
    except (GitError, OSError):
        return False


def has_commits(repo: str) -> bool:
    try:
        _git(repo, "rev-parse", "HEAD")
        return True
    except GitError:
        return False


def base_ref(repo: str) -> tuple[str, str]:
    """(branch name, sha) the agents' work will be based on. A detached HEAD has no
    branch name — the sha still anchors the worktree, and the gate refuses to merge
    into a base it cannot name."""
    sha = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return ("" if branch == "HEAD" else branch), sha


def slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (s[:limit] or "task").strip("-")


def _root() -> str:
    base = os.environ.get("GOV_DATA_DIR", "").strip() or tempfile.gettempdir()
    d = os.path.join(base, "phoenix-worktrees")
    os.makedirs(d, exist_ok=True)
    return d


def create(repo: str, agent: str, task_slug: str) -> dict:
    """A fresh worktree on a new branch off the current HEAD. The checkout is clean
    even if your working tree is dirty — the agent starts from committed truth."""
    if not is_git(repo):
        raise GitError(f"{repo} is not a git repository — worktree isolation needs one")
    if not has_commits(repo):
        raise GitError(f"{repo} has no commits — nothing to branch from")
    base_branch, base_sha = base_ref(repo)
    name = f"{BRANCH_PREFIX}/{slug(agent, 24)}/{slug(task_slug)}"
    path = os.path.join(_root(), slug(os.path.basename(repo.rstrip(os.sep)), 32),
                        slug(name, 80))
    if os.path.exists(path):
        remove(repo, path, branch="")
    _git(repo, "worktree", "prune", check=False)
    if _git(repo, "rev-parse", "--verify", "--quiet", name, check=False):
        _git(repo, "branch", "-D", name, check=False)   # a stale branch from a crash
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _git(repo, "worktree", "add", "-b", name, path, base_sha)
    return {"path": path, "branch": name, "base_branch": base_branch,
            "base_sha": base_sha, "repo": repo}


def changed_files(wt: dict) -> list[str]:
    out = _git(wt["path"], "status", "--porcelain", "--untracked-files=all", strip=False)
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            path = line[3:].strip().strip('"')
            files.append(path.split(" -> ")[-1])    # renames report old -> new
    return sorted(set(files))


def diff(wt: dict, limit: int = 200_000) -> str:
    """What the agent actually changed, against the commit it started from."""
    _git(wt["path"], "add", "-A")                  # so new files show in the diff
    text = _git(wt["path"], "diff", "--cached", wt["base_sha"], "--")
    _git(wt["path"], "reset", "-q", check=False)
    return text[:limit]


def commit_all(wt: dict, message: str) -> str:
    _git(wt["path"], "add", "-A")
    _git(wt["path"], "-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}",
         "commit", "-q", "-m", message, "--author", f"{AUTHOR_NAME} <{AUTHOR_EMAIL}>")
    return _git(wt["path"], "rev-parse", "HEAD")


def is_dirty(wt: dict) -> bool:
    return bool(_git(wt["path"], "status", "--porcelain"))


def merge(repo: str, branch: str, base_branch: str, message: str) -> tuple[bool, str]:
    """THE irreversible act, and the only one — everything up to here was a branch.
    Refuses to merge into a working tree with uncommitted changes rather than mixing
    an agent's work into yours, and refuses a detached HEAD it cannot name."""
    if not base_branch:
        return False, "the base is a detached HEAD — check out a branch before merging"
    current, _ = base_ref(repo)
    if current != base_branch:
        return False, f"working tree is on '{current}', not the base branch '{base_branch}'"
    dirty = [l for l in _git(repo, "status", "--porcelain", strip=False).splitlines()
             if l and not l.startswith("??")]
    if dirty:
        # Untracked files are fine — git refuses on its own if a merge would clobber
        # one. Modified *tracked* files are not: merging on top of them would mix an
        # agent's work into changes you have not committed.
        return False, ("the working tree has uncommitted changes to "
                       + ", ".join(l[3:].strip() for l in dirty[:5])
                       + " — commit or stash first")
    try:
        _git(repo, "merge", "--no-ff", "-m", message, branch)
    except GitError as e:
        _git(repo, "merge", "--abort", check=False)
        return False, str(e)
    return True, _git(repo, "rev-parse", "HEAD")


def push(repo: str, branch: str, remote: str = "origin") -> tuple[bool, str]:
    """Only ever called when the operator has explicitly enabled it (builder.py
    checks BUILDER_ALLOW_PUSH). Agents have no path to this function."""
    try:
        return True, _git(repo, "push", "-u", remote, branch)
    except GitError as e:
        return False, str(e)


def remove(repo: str, path: str, branch: str = "") -> None:
    """Drop the worktree. The branch survives by default — it is the work product,
    and the dossier at the gate points at it."""
    _git(repo, "worktree", "remove", "--force", path, check=False)
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)
    if branch:
        _git(repo, "branch", "-D", branch, check=False)


def list_branches(repo: str) -> list[str]:
    out = _git(repo, "branch", "--list", f"{BRANCH_PREFIX}/*", "--format=%(refname:short)",
               check=False)
    return [b for b in out.splitlines() if b.strip()]

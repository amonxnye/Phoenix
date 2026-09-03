"""The Historian — R5, best effort and honest about it.

History facts are the second kind of admissible evidence (Charter §2), and they are
the differentiator the spec names: intent is not recoverable from a snapshot alone.
They need a git history. The archive ingestion the web uses carries none, and the
deployed image has no git. So this module answers when it can — a local checkout
with git on the path — and records ONE refusal when it cannot, naming why, rather
than inventing dates.
"""

import os
import shutil
import subprocess
import time

DORMANT_DAYS = 730                                  # untouched > 24 months (R5)


def _has_git(root: str) -> bool:
    d = os.path.abspath(root)
    while True:                                       # a subdirectory of a checkout counts
        if os.path.isdir(os.path.join(d, ".git")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def available(root: str) -> tuple[bool, str]:
    if not _has_git(root):
        return False, ("no git history in the tree — archive ingestion carries none; "
                       "history facts (age, churn, authorship, drift signals) are unavailable "
                       "for this run")
    if not shutil.which("git"):
        return False, "git is not installed on this host; history facts are unavailable"
    return True, ""


def facts(root: str, files: list[str], limit: int = 400) -> dict:
    """Per-file history: last touched, commits, authors, liveness class. Empty dict
    when unavailable — the caller records the refusal."""
    ok, _ = available(root)
    if not ok:
        return {}
    out, now = {}, time.time()
    for rel in files[:limit]:
        try:
            log = subprocess.run(
                ["git", "-C", root, "log", "--format=%at|%an", "--", rel],
                capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        except (OSError, subprocess.SubprocessError):
            continue
        if not log:
            continue
        stamps, authors = [], set()
        for line in log:
            ts, _, who = line.partition("|")
            if ts.isdigit():
                stamps.append(int(ts))
            if who:
                authors.add(who)
        if not stamps:
            continue
        last, first = max(stamps), min(stamps)
        age_days = (now - last) / 86400
        cls = ("dormant" if age_days > DORMANT_DAYS else
               "active" if age_days < 90 else "stable")
        out[rel] = {"commits": len(stamps), "authors": len(authors),
                    "last_touched": last, "first_touched": first,
                    "age_days": int(age_days), "liveness": cls}
    return out


def line(rel: str, h: dict) -> str:
    f = h.get(rel)
    if not f:
        return "history: unavailable"
    return (f"history: {f['commits']} commits by {f['authors']} author(s); last touched "
            f"{f['age_days']} days ago ({f['liveness']})")

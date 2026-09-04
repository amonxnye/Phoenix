"""Proposed fixes as patches — R18, done the mechanic's way: proposed, verified for
applicability, never applied.

A prose recommendation is a direction; a diff is a proposal a maintainer can read in a
minute and reject in two. The gap between them is where a model's fix can be wrong in
ways prose hides — off by a line, breaking the syntax, touching a file it was not asked
about. So a patch reaches the page only after two machine checks:

  1. it applies cleanly to the current file (context lines match), and
  2. the patched file still parses.

A patch that fails either is a recorded refusal naming the reason, not a fix. The
repository on disk is never modified; the checks run on a copy in memory. Only the
top findings by consequence are attempted, under the run's budget (gate four).
"""

import ast
import os
import re

from . import brainseam

FIX_TOP = 15
OUT_TOKENS = 2500
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_TARGET = re.compile(r"^\+\+\+ (?:b/)?(\S+)", re.M)

SCHEMA = (
    "Reply with ONLY a unified diff for this one file — a `--- a/<file>` line, a "
    "`+++ b/<file>` line, then hunks with correct `@@ -start,len +start,len @@` headers "
    "and at least two lines of unchanged context around each change. No prose, no fences. "
    "Make the smallest change that resolves the finding."
)


def parse(diff: str) -> list[dict] | None:
    """Unified diff → hunks [{old_start, old_len, new_start, new_len, lines}]. None if
    it is not a diff we can read."""
    text = diff.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else ""
    hunks, cur = [], None
    for ln in text.splitlines():
        if ln.startswith(("--- ", "+++ ")):
            continue
        m = _HUNK.match(ln)
        if m:
            cur = {"old_start": int(m.group(1)), "old_len": int(m.group(2) or 1),
                   "new_start": int(m.group(3)), "new_len": int(m.group(4) or 1), "lines": []}
            hunks.append(cur)
            continue
        if cur is None:
            if ln.strip() == "":
                continue
            return None                              # prose before the first hunk
        if ln.startswith("\\"):
            continue                                 # "\ No newline at end of file"
        if ln[:1] in (" ", "+", "-"):
            cur["lines"].append(ln)
        elif ln == "":
            cur["lines"].append(" ")                 # a blank context line, trimmed by the model
        else:
            return None
    return hunks or None


def target(diff: str) -> str:
    """The file the diff says it patches (`+++ b/<path>`), or '' if it does not say."""
    m = _TARGET.search(diff)
    return m.group(1) if m else ""


def apply(source: str, hunks: list[dict]) -> tuple[str | None, str]:
    """Apply hunks to source text in memory. Returns (new_text, '') or (None, why)."""
    lines = source.splitlines()
    out, pos = [], 0                                 # pos: index into `lines` consumed so far
    for i, h in enumerate(hunks, 1):
        start = h["old_start"] - 1
        if start < pos or start > len(lines):
            return None, f"hunk {i} starts at line {h['old_start']}, out of order or past EOF"
        out.extend(lines[pos:start])
        pos = start
        for ln in h["lines"]:
            tag, body = ln[:1], ln[1:]
            if tag in (" ", "-"):
                if pos >= len(lines):
                    return None, f"hunk {i} runs past the end of the file"
                if lines[pos].rstrip() != body.rstrip():
                    return None, (f"hunk {i} context mismatch at line {pos + 1}: expected "
                                  f"{body.strip()[:40]!r}, file has {lines[pos].strip()[:40]!r}")
                if tag == " ":
                    out.append(lines[pos])
                pos += 1
            else:
                out.append(body)
    out.extend(lines[pos:])
    return "\n".join(out) + ("\n" if source.endswith("\n") else ""), ""


def _numbered(text: str) -> str:
    return "\n".join(f"{i:4d}  {ln}" for i, ln in enumerate(text.splitlines(), 1))


def propose(f: dict, root: str, budget) -> dict:
    """One finding → {patch, status, note}. Never raises; never writes to disk."""
    path = os.path.join(root, f["file"])
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as e:
        return {"patch": "", "status": "failed", "note": f"could not read {f['file']}: {e}"}
    prompt = (f"Role: the fixer. Propose a patch for ONE finding in ONE file.\n\n"
              f"FINDING: {f['title']}\n{f.get('description', '')}\n"
              f"RECOMMENDATION: {f.get('recommendation', '')}\n"
              f"FILE: {f['file']}  LINES: {f.get('line_range', '')}\n\n"
              f"{brainseam.data_block(_numbered(src))}\n\n{SCHEMA}")
    try:
        out = brainseam.ask([{"role": "user", "content": prompt}], OUT_TOKENS, 0.1,
                            "fixer", "strong", budget)
    except Exception as e:                            # noqa: BLE001 — recorded as a refusal
        return {"patch": "", "status": "failed", "note": f"fixer call failed: {e}"}
    hunks = parse(out)
    if not hunks:
        return {"patch": "", "status": "failed",
                "note": f"reply was not a unified diff: {out.strip()[:80]!r}"}
    tgt = target(out)
    if tgt and not (f["file"].endswith(tgt) or tgt.endswith(f["file"])):
        return {"patch": out.strip(), "status": "failed",
                "note": f"patch names another file: {tgt!r}, finding is in {f['file']!r}"}
    new, why = apply(src, hunks)
    if new is None:
        return {"patch": out.strip(), "status": "failed", "note": f"does not apply: {why}"}
    try:
        ast.parse(new)
    except SyntaxError as e:
        return {"patch": out.strip(), "status": "failed",
                "note": f"applies but the result does not parse: line {e.lineno}: {e.msg}"}
    changed = sum(1 for h in hunks for ln in h["lines"] if ln[:1] in "+-")
    return {"patch": out.strip(), "status": "applies-and-parses",
            "note": f"{len(hunks)} hunk(s), {changed} changed line(s); verified in memory"}


def project_cents(n: int, budget_mod) -> float:
    return budget_mod._cents("strong", 3500 * n, OUT_TOKENS * n)

"""The executor seam — given a task and a failing oracle, produce a patch.

EXECUTORS.md §2 names this as the one thing Phoenix may delegate. Everything on the
other side of it stays ours: we run the oracle, we hold the gate, we keep the books.
A solver is any callable with this shape:

    solver(request: dict) -> {"files": {path: content}, "notes": str}

`native_solver` is the built-in one: it calls the ONE brain seam and parses a
multi-file reply. A test can inject a scripted solver and exercise the entire
autonomous loop with no model and no network — which is how verify_builder.py
proves the machinery rather than the model.

Three rules any solver must satisfy (EXECUTORS.md §2):
  1. It returns files, never a merge. The gate is not reachable from here.
  2. It never scores itself. `notes` is commentary; only the oracle decides.
  3. It is charged. The caller debits the agent's budget for what the attempt cost.
"""

import os
import re

FILE_OPEN = "<<<PHOENIX-FILE"
FILE_CLOSE = "<<<PHOENIX-END>>>"
MAX_FILE_CHARS = 20000            # per file, in either direction
MAX_CONTEXT_FILES = 12


def parse_patch(text: str, default_path: str = "") -> dict:
    """Read the multi-file envelope out of a model's reply.

        <<<PHOENIX-FILE path/to/x.py>>>
        ...complete new content...
        <<<PHOENIX-END>>>

    A reply with no envelope is treated as the whole content of `default_path`,
    which keeps the single-file behaviour that shipped first working unchanged.
    Fenced code inside the envelope is unwrapped; a fence is a formatting habit,
    not content."""
    files = {}
    for m in re.finditer(
            re.escape(FILE_OPEN) + r"\s+(?P<path>[^\n>]+?)\s*>>>\n(?P<body>.*?)"
            + re.escape(FILE_CLOSE), text, re.S):
        path = m.group("path").strip().strip('"').strip("'")
        if path:
            files[path] = _unfence(m.group("body"))
    if not files and default_path and text.strip():
        files[default_path] = _unfence(text)
    return files


def _unfence(text: str) -> str:
    t = text.strip("\n")
    stripped = t.strip()
    if not stripped.startswith("```"):
        return t
    body = stripped[3:]
    nl = body.find("\n")
    body = body[nl + 1:] if nl != -1 else body
    end = body.rfind("```")
    return body[:end].rstrip("\n") if end != -1 else body


def render_request(req: dict) -> str:
    """The prompt. Everything in it is evidence the agent could not have invented:
    the runner's own output, the real file contents, and — from attempt 2 on — what
    its previous attempt actually did to the suite."""
    lines = [
        f"You are {req['agent']}, an autonomous software agent working in an isolated "
        f"git worktree of the repository '{req['repo_name']}'.",
        f"The oracle is `{req['test_cmd']}`. It decides whether you succeeded — not you.",
        "",
        f"TASK: {req['task_title']}",
        f"Target test: {req['task_test']}",
    ]
    others = [f for f in req.get("failures", []) if f != req.get("task_test")][:15]
    if others:
        lines.append("Other tests currently failing (fixing them too is welcome, "
                     "breaking them is not): " + ", ".join(others))
    lines += ["", "--- what the test runner reported ---", req.get("failure_report", "")[:6000]]

    if req.get("attempt", 1) > 1:
        lines += ["", f"--- your previous attempt (#{req['attempt'] - 1}) ---",
                  req.get("last_outcome", "")[:3000],
                  "Do not repeat it. Change your approach."]

    for path, content in list(req.get("files", {}).items())[:MAX_CONTEXT_FILES]:
        body = content[:MAX_FILE_CHARS] if content else "(this file does not exist yet)"
        lines += ["", f"--- {path} ---", body]

    if req.get("test_sources"):
        lines.append("")
        lines.append("--- the tests (read-only; you may NOT modify them) ---")
        for path, content in list(req["test_sources"].items())[:3]:
            lines += [f"### {path}", content[:MAX_FILE_CHARS]]

    if req.get("writable"):
        lines += ["", "Files you may create or edit (anything else is refused): "
                  + ", ".join(req["writable"][:60])]
    lines += [
        "",
        "Reply with the COMPLETE new content of every file you change, each wrapped "
        "exactly like this and nothing else:",
        f"{FILE_OPEN} path/to/file.py>>>",
        "<the entire file, not a diff, not a fragment>",
        FILE_CLOSE,
        "",
        "You may not edit the tests, their configuration, or the CI — those writes "
        "are refused and the attempt is wasted. Fix the code instead.",
    ]
    return "\n".join(lines)


def native_solver(req: dict) -> dict:
    """The built-in executor: the ONE brain seam, plus the envelope parser."""
    import brain
    text = brain._chat([{"role": "user", "content": render_request(req)}],
                       req.get("max_tokens", 8000), 0.2, "builder-patch")
    default = next(iter(req.get("files", {})), "")
    return {"files": parse_patch(text, default), "notes": "", "raw": text}


def available() -> bool:
    import brain
    return brain.available()

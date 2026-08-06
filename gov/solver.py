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


# ── strategies: how a fleet gets creative without getting reckless ────────────
#
# Running the same prompt N times produces N variations on one idea, which is why
# a stuck agent stays stuck. Each sortie draws a DIFFERENT approach, and the ladder
# is ordered: cheap and conservative first, wider and stranger as rounds fail. The
# temperature rises with it — exploit, then explore. Creativity is bounded because
# the oracle judges every one of them the same way.
STRATEGIES = (
    {"key": "direct", "temp": 0.1, "brief":
     "Make the SMALLEST change that satisfies the failing assertion. Do not "
     "refactor, rename, or improve anything else."},
    {"key": "read-the-test", "temp": 0.2, "brief":
     "Work backwards from what the test says the contract is. State that contract "
     "to yourself first, then make the code honour it — the test is the "
     "specification, not an obstacle."},
    {"key": "widen", "temp": 0.35, "brief":
     "The defect may not be in the obvious file. Consider that a caller, a default "
     "argument, or a neighbouring helper is the real cause, and fix it there."},
    {"key": "rewrite", "temp": 0.45, "brief":
     "Previous minimal patches failed. Replace the whole function (or module) with "
     "a clean implementation written from the contract, rather than patching around "
     "the existing shape."},
    {"key": "contrarian", "temp": 0.7, "brief":
     "Assume every previous diagnosis in this campaign was WRONG. Name a different "
     "root cause than the ones already tried, and fix that instead."},
    {"key": "decompose", "temp": 0.5, "brief":
     "Split the problem: make the smallest sub-behaviour correct first, even if it "
     "does not turn the whole test green yet. Partial, provable progress is kept."},
)


def strategies_for(round_no: int, count: int) -> list[dict]:
    """The slate of approaches for one round. Round 1 sends the conservative end of
    the ladder; later rounds slide toward the strange end, because the conservative
    ideas have demonstrably not worked."""
    n = len(STRATEGIES)
    start = min(max(0, round_no - 1), max(0, n - 1))
    picked, i = [], 0
    while len(picked) < max(1, count) and i < n * 2:
        picked.append(STRATEGIES[(start + i) % n])
        i += 1
    return picked[:max(1, count)]


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

    if req.get("strategy_brief"):
        lines += ["", f"--- your assigned approach this round: {req.get('strategy', '')} ---",
                  req["strategy_brief"],
                  "Other agents are working the same problem from other angles right "
                  "now. Do YOUR angle — a fleet that converges on one idea is one agent."]

    if req.get("attempt", 1) > 1:
        lines += ["", f"--- your previous attempt (#{req['attempt'] - 1}) ---",
                  req.get("last_outcome", "")[:3000],
                  "Do not repeat it. Change your approach."]

    if req.get("tried"):
        lines += ["", "--- already tried in this campaign, and rejected by the oracle ---"]
        lines += [f"  · {t}" for t in req["tried"][:12]]
        lines.append("Do not propose any of these again.")

    if req.get("lessons"):
        lines += ["", "--- lessons the organization has already paid for ---"]
        lines += [f"  · {l}" for l in req["lessons"][:8]]

    if req.get("champion_note"):
        lines += ["", "--- where this campaign has already got to ---", req["champion_note"],
                  "You are building ON this, not starting over. Do not undo it."]

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
                       req.get("max_tokens", 8000),
                       float(req.get("temperature", 0.2)),
                       f"builder-patch:{req.get('strategy', 'direct')}")
    default = next(iter(req.get("files", {})), "")
    return {"files": parse_patch(text, default), "notes": "", "raw": text}


def available() -> bool:
    import brain
    return brain.available()

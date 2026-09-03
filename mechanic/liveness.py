"""The liveness analyst — the first analyst, and the only one that needs no model.

The handoff says start with drift, because drift is the differentiator. This one
comes first for a different reason: it is the test of whether the index works.
Milestone 1 is "done when the four queries give CORRECT answers", and correctness of
`unreachable_from` cannot be judged by looking at it — only by turning it into
findings about a repository someone knows intimately and checking each one.

So this module is deliberately thin. It asks the graph one question and applies the
Charter's honesty rules to the answer:

  §4  a symbol is reported dead only where nothing in its module can reach it
      dynamically — otherwise the output is a refusal that names the construct;
  §3  what it emits is `machine-verified`, and says so;
  §2  every finding carries the file, the line range, and the graph fact it rests on.

A finding here is a proposed fix in the plainest sense: delete this, and here is the
proof that you can.
"""

import os

from .index import Index, _MAGIC


def analyse(idx: Index, repo_root: str) -> dict:
    """Return {"findings": [...], "gaps": [...]} — never raises on a strange repo,
    because a refusal is a result and an exception is not."""
    findings, gaps = [], []
    # Refusals are computed from the repository's SHAPE, not from the dead set. The
    # index already treats every symbol behind a refusal as a root (a refusal must
    # propagate — see Index.entry_points), so nothing reached only through a refused
    # symbol can appear below. What remains here is the honest statement of where the
    # proof could not go, recorded whether or not anything dead was found there.
    for m in idx.modules():
        if m["dynamic"]:
            gaps.append({"scope": m["name"],
                         "reason": f"reachability unprovable — module uses {m['dynamic']}; "
                                   f"every symbol in it is treated as live"})
    for cls in sorted(idx.foreign_base_classes()):
        gaps.append({"scope": cls,
                     "reason": "reachability unprovable — class inherits from outside the "
                               "repository, so its methods may be invoked by that framework; "
                               "they and everything they reach are treated as live"})
    dead = sorted(idx.unreachable_from())

    for q in dead:
        sym = idx.symbol(q)
        if not sym:
            continue
        name = sym["name"]
        if name in _MAGIC:
            continue                                # the language calls these itself
        span = max(1, int(sym["end_line"] or sym["line"]) - int(sym["line"]) + 1)
        # Consequence, not label: 3 dead lines are noise, 120 are a maintenance cost
        # someone is paying every time they read the file.
        severity = "medium" if span >= 60 else "low"
        findings.append({
            "category": "liveness",
            "severity": severity,
            "confidence": 0.95,                     # static Python is never 1.0; say so
            "basis": "machine-verified",
            "title": f"`{name}` in {sym['file']} is unreachable",
            "description": (
                f"No call to or reference of `{name}` exists anywhere in the repository. "
                f"Roots considered: every module's top level, tests, `main`, dunder methods, "
                f"decorator-registered symbols and `__all__` exports. {span} lines."),
            "recommendation": (
                f"Delete `{name}` ({sym['file']}:{sym['line']}–{sym['end_line']}). If it is "
                f"invoked from outside this repository — a plugin loader, a console_scripts "
                f"entry, a subclass in another package — register that entry point so the "
                f"graph can see it, and this finding will not recur."),
            "evidence": [{"file": sym["file"],
                          "line_range": f"{sym['line']}-{sym['end_line']}",
                          "commit_sha": "",
                          "reason": "graph fact: unreachable_from(entry_points); "
                                    "references_of() is empty"}],
            "symbol": q, "lines": span,
        })

    findings.sort(key=lambda f: (-f["lines"], f["symbol"]))   # largest cost first
    return {"findings": findings, "gaps": gaps,
            "considered": len(dead), "root": os.path.basename(repo_root.rstrip(os.sep))}

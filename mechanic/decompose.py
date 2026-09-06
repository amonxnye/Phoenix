"""Units and their contexts — R3.

A unit is what one analyst can hold in one context: here, a module, with the facts
around it that the index and the historian can supply. The context an agent receives
is BOUNDED (Constitution III.4 applies to our own prompts too) and NUMBERED, so a
finding can cite a line range that a reader can open.

Units are ordered by centrality — the most-depended-upon code first — so a budget
that halts partway has spent itself on the code that matters most.
"""

import os

from . import brainseam, history, polyglot
from .index import Index


def units(idx: Index) -> list[dict]:
    out = []
    for m in idx.modules():
        syms = idx.symbols(module=m["name"])
        entries = [s["name"] for s in syms if s["registered"] or s["exported"]
                   or s["name"] in ("main",) or s["name"].startswith("test_")]
        out.append({"module": m["name"], "file": m["file"], "loc": m["loc"],
                    "symbols": len(syms), "centrality": len(idx.dependents_of(m["name"])),
                    "dynamic": m["dynamic"], "is_test": m["is_test"],
                    "lang": m.get("lang") or "python",
                    "entry_points": entries[:12], "deps": idx.dependencies_of(m["name"])[:20],
                    "dependents": idx.dependents_of(m["name"])[:12]})
    out.sort(key=lambda u: (-u["centrality"], u["module"]))
    return out


def _numbered(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    return "\n".join(f"{i:4d}  {ln}" for i, ln in enumerate(lines, 1))


def context(unit: dict, root: str, idx: Index, hist: dict) -> str:
    """Everything one analyst gets about one unit, and nothing about any other."""
    syms = idx.symbols(module=unit["module"])
    tested = sorted({t.split(".")[0] for s in syms for t in idx.tests_covering(s["qualname"])})
    head = [
        f"UNIT {unit['module']}  ({unit['file']}, {unit['loc']} lines, {len(syms)} symbols)",
        f"centrality: {unit['centrality']} module(s) depend on it"
        + (f": {', '.join(unit['dependents'])}" if unit["dependents"] else ""),
        f"entry points: {', '.join(unit['entry_points']) or 'none declared'}",
        "imports: " + brainseam.clip("unit_deps", ", ".join(unit["deps"]) or "none"),
        f"tests exercising it: {', '.join(tested) or 'none found in the graph'}",
        brainseam.clip("unit_history", history.line(unit["file"], hist)),
    ]
    if unit["dynamic"]:
        head.append(f"dynamic dispatch present: {unit['dynamic']} — reachability here is "
                    f"unprovable; do not claim a symbol is unused")
    if unit.get("lang", "python") != "python":
        head.append(f"language: {unit['lang']} — not indexed: there are no graph facts for "
                    f"this unit; cite line numbers, and do not claim a symbol is unused")
    src = _numbered(os.path.join(root, unit["file"]))
    return "\n".join(head) + "\n\n" + brainseam.data_block(src)


def checklist(unit: dict, idx: Index) -> list[dict]:
    """What the critic must stress — derived from the graph, not invented by a model:
    every function and method with its line, every import, every external call.
    Each item has an id the critic reports back, so coverage is measured."""
    if unit.get("lang", "python") != "python":
        return polyglot.checklist(unit, polyglot.read_lines(os.path.join(idx.root, unit["file"])))
    items = []
    for s in idx.symbols(module=unit["module"]):
        if s["kind"] in ("function", "method"):
            items.append({"id": f"f:{s['name']}@{s['line']}", "kind": s["kind"],
                          "what": f"{s['qualname']} (line {s['line']}-{s['end_line']})"})
    for dep in unit["deps"]:
        items.append({"id": f"i:{dep}", "kind": "import", "what": f"import {dep}"})
    for call in idx.external_calls(unit["module"]):
        items.append({"id": f"c:{call}", "kind": "external call", "what": f"call to {call}()"})
    return items


def checklist_text(items: list[dict]) -> str:
    return brainseam.clip("checklist", "\n".join(f"- [{i['id']}] {i['kind']}: {i['what']}"
                                                  for i in items))


def manifest(us: list[dict], contexts: list[str], roles: int, budget) -> dict:
    """What the run is about to cost, before it costs it (Charter §7, gate one)."""
    return {"units": len(us), "roles": roles, "calls": len(us) * roles,
            "context_chars": sum(len(c) for c in contexts),
            "projected_panel_cents": round(budget.project_panel(contexts, roles), 2)}

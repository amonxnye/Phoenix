"""One analysis path, whoever asks for it.

The CLI and the web UI both call `run()`. A second copy of this sequence in the web
layer would be the ordinary way a UI drifts from the tool it fronts: the button ends
up doing something slightly different from the command, and nobody can say which one
the report describes. So there is one.

`run()` returns data and prints nothing; presentation belongs to the caller.
"""

import os
import subprocess
import time

from . import charter, liveness, report, store
from .index import Index, build


def sha_of(path: str) -> str:
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run(root: str, name: str = "", url: str = "", budget_cents: int = 0,
        commit_sha: str = "") -> dict:
    """Index, decompose into units, run the liveness analyst, persist everything.

    Returns {run_id, repo_id, name, symbols, modules, findings, gaps, seconds}.
    Never raises on a strange repository: unreadable files become recorded refusals
    (Charter §6), and a run that fails outright is closed as `halted` with the reason,
    never left `analysing` forever — an open run nobody will close is the same defect
    as an unanswered request."""
    store.init()
    root = os.path.abspath(root)
    name = name or os.path.basename(root.rstrip(os.sep))
    repo_id = store.repo_add(url or f"file://{root}", name=name, local_path=root)
    ch = charter.charter()
    run_id = store.run_open(repo_id, commit_sha or sha_of(root), ch["stamp"],
                            budget_cents=budget_cents)
    db = os.path.join(store.data_dir(), f"index-{run_id}.sqlite")
    t0 = time.time()
    try:
        s = build(root, db)
        for p, why in s["unreadable"]:
            store.gap_add(run_id, p, f"could not parse: {why}")
        store.run_set(run_id, status="analysing", unit_count=s["modules"],
                      symbol_count=s["symbols"])
        idx = Index(db)
        try:
            units = [{"module": m["name"], "file": m["file"], "loc": m["loc"],
                      "symbols": len(idx.symbols(module=m["name"])),
                      "centrality": len(idx.dependents_of(m["name"])),
                      "dynamic": m["dynamic"]} for m in idx.modules()]
            store.units_add(run_id, units)
            res = liveness.analyse(idx, root)
        finally:
            idx.close()
        for g in res["gaps"]:
            store.gap_add(run_id, g["scope"], g["reason"])
        for f in res["findings"]:
            fid = store.finding_add(run_id, repo_id, f)
            store.decision_add(run_id, "analysis", "liveness (index)", "proposed",
                               f["evidence"][0]["reason"], finding_id=fid,
                               model="none — graph fact", charter=ch["stamp"])
        secs = time.time() - t0
        store.run_close(run_id, "complete",
                        note=f"{len(res['findings'])} findings, {len(res['gaps'])} "
                             f"refusals, {secs:.1f}s")
        store.repo_set(repo_id, loc=sum(u["loc"] for u in units), languages="python")
        return {"run_id": run_id, "repo_id": repo_id, "name": name,
                "symbols": s["symbols"], "modules": s["modules"],
                "findings": len(res["findings"]), "gaps": len(res["gaps"]),
                "seconds": round(secs, 1), "status": "complete"}
    except Exception as e:                        # noqa: BLE001 — closed, not abandoned
        store.gap_add(run_id, "run", f"halted: {type(e).__name__}: {e}")
        store.run_close(run_id, "halted", note=f"{type(e).__name__}: {e}")
        return {"run_id": run_id, "repo_id": repo_id, "name": name, "symbols": 0,
                "modules": 0, "findings": 0, "gaps": 1,
                "seconds": round(time.time() - t0, 1), "status": "halted",
                "error": f"{type(e).__name__}: {e}"}


def render(run_id: str) -> str:
    return report.render(run_id)

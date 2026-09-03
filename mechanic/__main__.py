"""Operator CLI — the primary interface for Phase 1 (SRS §18).

    python -m mechanic index   <path>                    build the index, report timing
    python -m mechanic query   <path> <query> <arg>      one of the four queries
    python -m mechanic analyse <path> [--name N] [--url U]
    python -m mechanic report  <run-id>
    python -m mechanic repos

Local paths only at Milestone 1. Cloning by URL (R1) arrives with the ingestion
service; the read-only credential it requires is the part that must not be improvised.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

from . import charter, liveness, report, store
from .index import Index, build


def _sha(path: str) -> str:
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cmd_index(a):
    db = a.db or os.path.join(tempfile.gettempdir(), "mechanic-index.sqlite")
    t0 = time.time()
    s = build(a.path, db)
    dt = time.time() - t0
    print(f"indexed {s['files']} files · {s['symbols']:,} symbols · {s['edges']:,} edges "
          f"in {dt:.2f}s → {db}")
    for p, why in s["unreadable"]:
        print(f"  could not read {p}: {why}")


def cmd_query(a):
    db = a.db or os.path.join(tempfile.gettempdir(), "mechanic-index.sqlite")
    if not os.path.exists(db):
        build(a.path, db)
    idx = Index(db)
    t0 = time.time()
    if a.query == "callers_of":
        out = idx.callers_of(a.arg)
    elif a.query == "references_of":
        out = idx.references_of(a.arg)
    elif a.query == "dependencies_of":
        out = idx.dependencies_of(a.arg)
    elif a.query == "tests_covering":
        out = idx.tests_covering(a.arg)
    elif a.query == "unreachable":
        out = sorted(idx.unreachable_from())
    else:
        sys.exit(f"unknown query {a.query}")
    dt = (time.time() - t0) * 1000
    for line in out:
        print(line)
    print(f"— {len(out)} result(s) in {dt:.1f}ms", file=sys.stderr)
    idx.close()


def cmd_analyse(a):
    store.init()
    root = os.path.abspath(a.path)
    name = a.name or os.path.basename(root.rstrip(os.sep))
    repo_id = store.repo_add(a.url or f"file://{root}", name=name, local_path=root)
    ch = charter.charter()
    run_id = store.run_open(repo_id, _sha(root), ch["stamp"], budget_cents=a.budget)
    db = os.path.join(store.data_dir(), f"index-{run_id}.sqlite")
    t0 = time.time()
    s = build(root, db)
    for p, why in s["unreadable"]:
        store.gap_add(run_id, p, f"could not parse: {why}")
    store.run_set(run_id, status="analysing", unit_count=s["modules"],
                  symbol_count=s["symbols"])
    idx = Index(db)
    units = [{"module": m["name"], "file": m["file"], "loc": m["loc"],
              "symbols": len(idx.symbols(module=m["name"])),
              "centrality": len(idx.dependents_of(m["name"])), "dynamic": m["dynamic"]}
             for m in idx.modules()]
    store.units_add(run_id, units)
    res = liveness.analyse(idx, root)
    idx.close()
    for g in res["gaps"]:
        store.gap_add(run_id, g["scope"], g["reason"])
    for f in res["findings"]:
        fid = store.finding_add(run_id, repo_id, f)
        store.decision_add(run_id, "analysis", "liveness (index)", "proposed",
                           f["evidence"][0]["reason"], finding_id=fid,
                           model="none — graph fact", charter=ch["stamp"])
    store.run_close(run_id, "complete",
                    note=f"{len(res['findings'])} findings, {len(res['gaps'])} refusals, "
                         f"{time.time() - t0:.1f}s")
    store.repo_set(repo_id, loc=sum(u["loc"] for u in units), languages="python")
    print(f"{run_id} · {name} · {s['symbols']:,} symbols · "
          f"{len(res['findings'])} machine-verified finding(s) · "
          f"{len(res['gaps'])} refusal(s) · {time.time() - t0:.1f}s")
    print(report.render(run_id))


def cmd_report(a):
    store.init()
    print(report.render(a.run_id))


def cmd_repos(a):
    store.init()
    s = store.summary()
    print(f"{s['repos']} repos · {s['runs']} runs · {s['findings']} findings "
          f"({s['machine_verified']} machine-verified) · {s['gaps']} refusals")
    for r in store.repos():
        last = (store.runs(r["id"], 1) or [{}])[0]
        print(f"  {r['id']}  {r['name']:<28} {r.get('loc', 0):>8,} LOC  "
              f"last run {last.get('id', '—')} {last.get('status', '')}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="mechanic")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index"); s.add_argument("path"); s.add_argument("--db")
    s.set_defaults(fn=cmd_index)
    s = sub.add_parser("query"); s.add_argument("path")
    s.add_argument("query", choices=["callers_of", "references_of", "dependencies_of",
                                     "tests_covering", "unreachable"])
    s.add_argument("arg", nargs="?", default=""); s.add_argument("--db")
    s.set_defaults(fn=cmd_query)
    s = sub.add_parser("analyse"); s.add_argument("path"); s.add_argument("--name")
    s.add_argument("--url"); s.add_argument("--budget", type=int, default=0)
    s.set_defaults(fn=cmd_analyse)
    s = sub.add_parser("report"); s.add_argument("run_id"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("repos"); s.set_defaults(fn=cmd_repos)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()

"""Operator CLI — the primary interface for Phase 1 (SRS §18).

    python -m mechanic index   <path>                    build the index, report timing
    python -m mechanic query   <path> <query> <arg>      one of the four queries
    python -m mechanic analyse <path> [--name N] [--url U]
    python -m mechanic report  <run-id>
    python -m mechanic repos

`analyse` accepts a local path or a https://github.com/<owner>/<repo> URL. A URL is
fetched as an archive over HTTPS — read-only, no credentials, no git (ingest.py) —
analysed, and deleted.
The web page (web.py) drives exactly the same `analyse.run()`; there is one path.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

from . import analyse, ingest, report, store
from .index import Index, build


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
    if ingest.accepted(a.path)[0]:                # a URL: borrow, analyse, return
        c = ingest.clone(a.path)
        if "error" in c:
            sys.exit(f"ingest refused: {c['error']}")
        try:
            res = analyse.run(c["path"], name=a.name or c["name"], url=a.path,
                              budget_cents=a.budget, commit_sha=c["sha"])
        finally:
            ingest.remove(c["tmp"])
    else:
        res = analyse.run(a.path, name=a.name, url=a.url, budget_cents=a.budget)
    print(f"{res['run_id']} · {res['name']} · {res['symbols']:,} symbols · "
          f"{res['findings']} machine-verified finding(s) · "
          f"{res['gaps']} refusal(s) · {res['seconds']}s"
          + (f" · HALTED: {res['error']}" if res["status"] != "complete" else ""))
    if res["status"] == "complete":
        print(report.render(res["run_id"]))


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
    s = sub.add_parser("analyse"); s.add_argument("path", help="local path or GitHub URL")
    s.add_argument("--name")
    s.add_argument("--url"); s.add_argument("--budget", type=int, default=0)
    s.set_defaults(fn=cmd_analyse)
    s = sub.add_parser("report"); s.add_argument("run_id"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("repos"); s.set_defaults(fn=cmd_repos)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()

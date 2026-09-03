"""A minimal render of a run — enough to check findings against what you know.

This is NOT Milestone 5's report writer. That one opens with a system description a
stranger could read and ranks by consequence across four analysts; it needs the
Historian and the Governor and it comes later. This exists because Milestone 1's
gate is "correct answers", and the only way to judge correctness is to read the
answers next to the code they are about.
"""

import time

from . import store


def render(run_id: str) -> str:
    r = store.run(run_id)
    if not r:
        return f"# no such run: {run_id}\n"
    repo = next((x for x in store.repos() if x["id"] == r["repo_id"]), {})
    fs = store.findings(run_id=run_id)
    gs = store.gaps(run_id=run_id)
    mv = [f for f in fs if f["basis"] == "machine-verified"]
    jd = [f for f in fs if f["basis"] != "machine-verified"]
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(r["started_at"] or 0))
    out = [f"# {repo.get('name', r['repo_id'])} — mechanic's report",
           f"",
           f"Run `{run_id}` · commit `{(r.get('commit_sha') or '')[:12] or 'n/a'}` · "
           f"{when} · charter `{r.get('charter', '')}`",
           f"",
           f"{r.get('symbol_count', 0):,} symbols across {r.get('unit_count', 0)} modules. "
           f"{len(mv)} machine-verified finding(s), {len(jd)} judged, "
           f"{len(gs)} place(s) where the analysis declined to conclude.",
           f""]
    if mv:
        out += ["## Machine-verified", "",
                "Each of these rests on a graph fact that was re-checked when this report "
                "was written. They are either true or false, and the proof is cited.", ""]
        for f in mv:
            ev = "; ".join(f"`{e['file']}:{e['line_range']}` — {e['reason']}"
                           for e in f["evidence"])
            out += [f"### {f['id']} · {f['severity']} · {f['title']}", "",
                    f["description"], "", f"**Proposed fix.** {f['recommendation']}", "",
                    f"*Evidence:* {ev}", ""]
    if jd:
        out += ["## Judged", "", "*(none yet — the analyst panel is Milestone 4)*", ""]
    out += ["## Where the analysis declined to conclude", ""]
    if gs:
        out += ["A refusal is a result. These are the places a static reading of Python "
                "cannot prove reachability, and the construct that blocked it.", ""]
        out += [f"- **{g['scope']}** — {g['reason']}" for g in gs]
    else:
        out += ["None. Every module could be reasoned about statically."]
    out += [""]
    return "\n".join(out)

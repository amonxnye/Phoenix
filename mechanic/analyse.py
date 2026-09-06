"""One analysis path, whoever asks for it — CLI, web, or the watch.

Stages, in order, each recorded whether or not the next one runs:

    index → units + contexts → history → liveness (machine-verified, free)
          → [gate 1] panel → [gate 2] review → [gate 3] governor → persist + sign

Everything after liveness needs a model and a budget. Without either, the judged
stages are skipped and the run says so as a refusal — the machine-verified findings
still ship. A run that halts at a gate closes as `halted` naming the gate; it is never
left open, and it is never quietly degraded into a cheaper run nobody asked for.
"""

import json
import os
import time

from . import (adjudicate, brainseam, budget as budget_mod, charter, decompose, deps,
               errors, fixer, history, liveness, panel, polyglot, posture, report, review,
               slop, store)
from .index import Index, build


def sha_of(path: str) -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _persist(run_id, repo_id, f, ch, unit_ids) -> str:
    f["unit_id"] = unit_ids.get(f.get("unit", ""), "")
    f["first_seen_run"] = run_id
    f["seen_runs"] = 1
    return store.finding_add(run_id, repo_id, f)


PROGRESS = {"stage": "", "done": 0, "total": 0}   # read by the web status line


def _prog(stage: str, total: int):
    PROGRESS.update(stage=stage, done=0, total=total)

    def step():
        PROGRESS["done"] += 1
    return step


def run(root: str, name: str = "", url: str = "", budget_cents: int | None = None,
        commit_sha: str = "", trigger: str = "manual") -> dict:
    store.init()
    root = os.path.abspath(root)
    name = name or os.path.basename(root.rstrip(os.sep))
    repo_id = store.repo_add(url or f"file://{root}", name=name, local_path=root)
    ch = charter.charter()
    model = brainseam.available()
    if budget_cents is None:
        budget_cents = int(os.environ.get("MECHANIC_BUDGET_CENTS", budget_mod.DEFAULT_CENTS)) \
            if model else 0
    bud = budget_mod.Budget(budget_cents)
    budget_mod.calibrate(brainseam.name(), brainseam.base_url())   # price what this provider charges
    run_id = store.run_open(repo_id, commit_sha or sha_of(root), ch["stamp"],
                            budget_cents=budget_cents, trigger=trigger)
    db = os.path.join(store.data_dir(), f"index-{run_id}.sqlite")
    t0 = time.time()
    counts = {"findings": 0, "judged": 0, "gaps": 0, "patches": 0, "deps": 0}
    try:
        s = build(root, db)
        for p, why in s["unreadable"]:
            store.gap_add(run_id, p, f"could not parse: {why}")
            counts["gaps"] += 1
        store.run_set(run_id, status="analysing", unit_count=s["modules"],
                      symbol_count=s["symbols"])
        idx = Index(db, root)
        try:
            us = decompose.units(idx)
            unit_ids = dict(zip((u["module"] for u in us), store.units_add(run_id, us)))
            store.run_set(run_id, unit_count=len(us))         # every language, not only the parsed one
            # What the tree is made of, and what each part gets. The first public run
            # read a TypeScript repository as "0 modules, 0 findings, complete": nothing
            # was scanned and nothing said so. Every language is now a unit, and every
            # language without a parser is a recorded gap naming what it did not get.
            langs = polyglot.summary(us)
            if not us:
                store.gap_add(run_id, "source", "no source files recognised in the tree — "
                                               "nothing was analysed; the mechanic reads: "
                                               + ", ".join(sorted(set(polyglot.LANGUAGES.values()))))
                counts["gaps"] += 1
            for lang, c in langs.items():
                if lang != "python":
                    store.gap_add(run_id, lang, f"{c['files']} {lang} file(s), {c['loc']:,} lines, "
                                                f"were read as text, not parsed: the reachability "
                                                f"proof (dead code with a call graph) exists for "
                                                f"Python only. The text-fact scans (security, slop, "
                                                f"names referenced nowhere), the dependency checks "
                                                f"and the judged panel cover them.")
                    counts["gaps"] += 1
            ok_hist, why_hist = history.available(root)
            hist = history.facts(root, [u["file"] for u in us]) if ok_hist else {}
            if not ok_hist:
                store.gap_add(run_id, "history", why_hist)
                counts["gaps"] += 1
            contexts = [decompose.context(u, root, idx, hist) for u in us]

            # ── free and exact: liveness, and the injection scan ────────────
            res = liveness.analyse(idx, root)
            for g in res["gaps"]:
                store.gap_add(run_id, g["scope"], g["reason"])
                counts["gaps"] += 1
            mv = list(res["findings"])
            texts = {u["module"]: polyglot.read_lines(os.path.join(root, u["file"])) for u in us}
            for u in us:
                if not u["is_test"]:
                    lines = texts[u["module"]]
                    mv.extend(panel.injection_findings(u, "\n".join(lines)))
                    mv.extend(polyglot.security_findings(u, lines))
                    mv.extend(polyglot.slop_findings(u, lines, u["lang"]))
            texts["__refs__"] = polyglot.reference_lines(root)
            mv.extend(polyglot.tree_findings(us, texts))     # duplicates, names referenced nowhere
            mv.extend(errors.analyse(us, texts)["findings"])  # dropped results, None before a check, docs vs raises
            # ── the repository as a system: posture, and what held up ──────
            po = posture.analyse(root, us, texts)
            mv.extend(po["findings"])
            held = list(po["held"])
            scanned = sum(1 for u in us if not u["is_test"])
            hit = {f["claim"].split(" at ")[0] for f in mv if f.get("proposed_by") == "security-scan"}
            if scanned:
                for kinds, text in ((("private key in source", "credential token in source",
                                      "hardcoded secret"),
                                     "no committed secret, private key or credential token"),
                                    (("dynamic code execution",), "no eval / new Function"),
                                    (("TLS verification disabled",), "TLS verification is never disabled"),
                                    (("SQL built from a string",),
                                     "no SQL statement is built from an interpolated value")):
                    if not hit & set(kinds):
                        held.append(f"{text} across {scanned} source file(s)")
            del texts
            dv = deps.analyse(root)                  # known-vulnerable dependencies (R17)
            od = deps.outdated(root)                 # a major version behind the registry
            counts["deps"] = dv["packages"]
            for g in dv["gaps"] + od["gaps"]:
                store.gap_add(run_id, g["scope"], g["reason"])
                counts["gaps"] += 1
            for f in dv["findings"]:
                f["symbol"] = f["title"].split("`")[1].split("@")[0]   # fingerprint by package
            mv.extend(dv["findings"])
            mv.extend(od["findings"])
            if dv["packages"] and not dv["findings"]:
                held.append(f"{dv['packages']} pinned package(s) checked against OSV.dev: none "
                            f"listed as vulnerable")
            if od["checked"] and not od["findings"]:
                held.append(f"{od['checked']} direct dependenc{'y is' if od['checked'] == 1 else 'ies are'} "
                            f"on the registry's current major version")
            for h in held:
                store.decision_add(run_id, "posture", "posture-scan", "held", h,
                                   model="none — text fact", charter=ch["stamp"])
            store.decision_add(run_id, "analysis", "dependency-scan", "checked",
                               f"{dv['packages']} pinned package(s) against OSV.dev: "
                               f"{len(dv['findings'])} vulnerable; {od['checked']} direct "
                               f"dependenc{'y' if od['checked'] == 1 else 'ies'} against the "
                               f"registries: {len(od['findings'])} a major version behind",
                               model="none — registry fact", charter=ch["stamp"])
            sl = slop.analyse(us, root)              # the tells of unreviewed generation, as facts
            mv.extend(sl["findings"])
            for f in mv:
                f["fingerprint"] = panel.fingerprint(f)
                fid = _persist(run_id, repo_id, f, ch, unit_ids)
                store.decision_add(run_id, "analysis", f.get("proposed_by", "liveness (index)"),
                                   "proposed", f["evidence"][0]["reason"], finding_id=fid,
                                   model="none — graph/text fact", charter=ch["stamp"])
                counts["findings"] += 1

            # ── judged: the swarm, behind three gates ────────────────────────
            if not model:
                store.gap_add(run_id, "panel", "judged analysis not run: no model configured — "
                                               "machine-verified findings only")
                counts["gaps"] += 1
            elif budget_cents <= 0:
                store.gap_add(run_id, "panel", "judged analysis not run: budget is 0")
                counts["gaps"] += 1
            else:
                _swarm(run_id, repo_id, us, contexts, idx, bud, ch, unit_ids, counts, root)
        finally:
            idx.close()
        secs = time.time() - t0
        store.run_set(run_id, spend_cents=bud.spent_cents(),
                      stage_costs=json.dumps(bud.record()))
        failed = sum(bud.failed.values())
        store.run_close(run_id, "complete",
                        note=f"{counts['findings']} findings ({counts['judged']} judged), "
                             f"{counts['gaps']} refusals, {len(us)} units in "
                             f"{', '.join(langs) or 'no language'}, {counts['deps']} deps, "
                             f"{bud.spent_cents()}¢, {secs:.1f}s"
                             + (f", {failed} calls failed at the provider" if failed else ""))
        # A completed run records the commit it analysed, whoever asked for it — so a
        # watch cycle that follows a manual run sees "unchanged" and spends nothing.
        store.repo_set(repo_id, loc=sum(u["loc"] for u in us), languages=", ".join(langs),
                       **({"last_sha": commit_sha} if commit_sha and store._EXT else {}))
        return {"run_id": run_id, "repo_id": repo_id, "name": name, "symbols": s["symbols"],
                "modules": len(us), "languages": langs, "findings": counts["findings"],
                "judged": counts["judged"], "gaps": counts["gaps"], "deps": counts["deps"],
                "patches": counts["patches"], "failed_calls": failed,
                "spend_cents": bud.spent_cents(), "seconds": round(secs, 1),
                "status": "complete"}
    except (budget_mod.OverBudget, brainseam.ProviderDown) as e:
        # Two honest halts: the money ceiling, and a provider that is not answering.
        # Neither is walked through unit by unit; both say why, with the work so far kept.
        scope, why = (("budget", f"halted at {e.stage}: {e}") if isinstance(e, budget_mod.OverBudget)
                      else ("provider", f"halted: {e}"))
        store.gap_add(run_id, scope, why)
        store.run_set(run_id, spend_cents=bud.spent_cents(), stage_costs=json.dumps(bud.record()))
        store.run_close(run_id, "halted", note=f"{scope}: {e}")
        return {"run_id": run_id, "repo_id": repo_id, "name": name, "symbols": 0, "modules": 0,
                "findings": counts["findings"], "judged": counts["judged"],
                "gaps": counts["gaps"] + 1, "spend_cents": bud.spent_cents(),
                "seconds": round(time.time() - t0, 1), "status": "halted",
                "error": f"{scope}: {e}"}
    except Exception as e:                            # noqa: BLE001 — closed, not abandoned
        if os.environ.get("MECHANIC_RAISE"):          # development: see the traceback
            raise
        store.gap_add(run_id, "run", f"halted: {type(e).__name__}: {e}")
        store.run_close(run_id, "halted", note=f"{type(e).__name__}: {e}")
        return {"run_id": run_id, "repo_id": repo_id, "name": name, "symbols": 0, "modules": 0,
                "findings": counts["findings"], "judged": counts["judged"],
                "gaps": counts["gaps"] + 1, "spend_cents": bud.spent_cents(),
                "seconds": round(time.time() - t0, 1), "status": "halted",
                "error": f"{type(e).__name__}: {e}"}


def _swarm(run_id, repo_id, us, contexts, idx, bud, ch, unit_ids, counts, root) -> None:
    stamp, model = ch["stamp"], brainseam.name()
    work = [(u, c) for u, c in zip(us, contexts) if not u["is_test"]]
    wu, wc = [w[0] for w in work], [w[1] for w in work]

    # gate 1 — the panel, projected from the manifest before any call
    man = decompose.manifest(wu, wc, len(panel.ROLES), bud)
    store.run_set(run_id, note=f"manifest: {man['calls']} analyst calls, "
                                f"~{man['projected_panel_cents']}¢")
    bud.gate("panel", man["projected_panel_cents"])
    pr = panel.run(wu, wc, idx, bud, progress=_prog("panel", man["calls"]))
    for cov in pr["coverage"]:                       # the critic's coverage is measured
        store.decision_add(run_id, "panel", "critic", f"stress-tested {cov['covered']}/{cov['total']}",
                           f"unit {cov['unit']}" + (f"; not examined: {', '.join(cov['missed'])}"
                                                    if cov["missed"] else ""),
                           model=model, charter=stamp)
        if cov["missed"]:
            store.gap_add(run_id, cov["unit"], f"critic did not examine {len(cov['missed'])} of "
                                               f"{cov['total']} checklist items: "
                                               f"{', '.join(cov['missed'][:6])}")
            counts["gaps"] += 1
    for unit, role, why in pr["dropped"]:
        store.decision_add(run_id, "panel", f"{role} analyst", "dropped before review", why,
                           model=model, charter=stamp)
    for c in pr["candidates"]:
        store.decision_add(run_id, "panel", f"{c['proposed_by']} analyst", "proposed",
                           c["claim"], model=model, charter=stamp)

    # gate 2 — review, re-projected now that the candidate count is known
    bud.gate("review", bud.project_review(len(pr["candidates"])))
    ctx_by_unit = {u["module"]: c for u, c in work}
    rv = review.review_all(pr["candidates"], ctx_by_unit, idx, bud,
                           progress=_prog("review", len(pr["candidates"])))
    PROGRESS.update(stage="governor", done=0, total=1)
    store.run_set(run_id, kill_rate=rv["kill_rate"])
    if rv["band_gap"]:
        store.gap_add(run_id, "review", rv["band_gap"])
        counts["gaps"] += 1
    for c in rv["refuted"]:
        store.decision_add(run_id, "review", "challenger", "refuted", c["challenge_reason"],
                           model=model, charter=stamp)
    for c in rv["upheld"]:
        store.decision_add(run_id, "review", "challenger",
                           c["challenge"] + (" — " + c["challenge_note"] if c["challenge_note"] else ""),
                           c["challenge_reason"], model=model, charter=stamp)

    # gate 3 — the governor
    bud.gate("governor", bud.project_governor(len(rv["upheld"])))
    centrality = {u["module"]: u["centrality"] for u in us}
    ad = adjudicate.adjudicate(rv["upheld"], centrality, bud, stamp)
    for f in ad["rejected"]:
        store.decision_add(run_id, "governor", ad["actor"], "rejected", f["governor_reason"],
                           model=model, charter=stamp)
    for r in ad["refusals"]:
        store.gap_add(run_id, "governor", f"refusal: {r}")
        counts["gaps"] += 1
    for f in ad["accepted"]:
        fid = _persist(run_id, repo_id, f, ch, unit_ids)
        # the trail, on the finding itself: proposed → challenged → decided
        store.decision_add(run_id, "panel", f"{f['proposed_by']} analyst", "proposed",
                           f["claim"], finding_id=fid, model=model, charter=stamp)
        store.decision_add(run_id, "review", "challenger", f["challenge"],
                           f["challenge_reason"], finding_id=fid, model=model, charter=stamp)
        store.decision_add(run_id, "governor", ad["actor"], f"accepted, rank {f['rank']}",
                           f"consequence {f['consequence']}", finding_id=fid, model=model,
                           charter=stamp)
        counts["findings"] += 1
        counts["judged"] += 1
    store.decision_add(run_id, "governor", ad["actor"], "signed",
                       f"signature {ad['signature']} over {len(ad['accepted'])} finding(s); "
                       f"kill rate {rv['kill_rate']:.0%} of {rv['reviewed']}",
                       model=model, charter=stamp)

    # gate 4 — proposed patches for the top findings by consequence, verified in memory
    top = store.findings(run_id=run_id)[:fixer.FIX_TOP]
    top = [f for f in top if f.get("file") or (f.get("evidence") or [{}])[0].get("file")]
    for f in top:
        f["file"] = f.get("file") or f["evidence"][0]["file"]
        f["line_range"] = f.get("line_range") or f["evidence"][0].get("line_range", "")
    try:
        bud.gate("fixer", fixer.project_cents(len(top), budget_mod))
    except budget_mod.OverBudget as e:
        store.gap_add(run_id, "fixer", f"patches not proposed: {e}")
        counts["gaps"] += 1
        return
    PROGRESS.update(stage="fixer", done=0, total=len(top))
    ok = 0
    for f in top:
        r = fixer.propose(f, root, bud)
        store.finding_set(f["id"], patch=r["patch"], patch_status=r["status"], patch_note=r["note"])
        store.decision_add(run_id, "fixer", "fixer", f"patch {r['status']}", r["note"],
                           finding_id=f["id"], model=model, charter=stamp)
        if r["status"] != "applies-and-parses":
            store.gap_add(run_id, f["id"], f"proposed patch rejected — {r['note']}")
            counts["gaps"] += 1
        else:
            ok += 1
        PROGRESS["done"] += 1
    counts["patches"] = ok


def render(run_id: str) -> str:
    return report.render(run_id)

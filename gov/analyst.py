"""The analyst agent — a villager whose resource is a vulnerability (SECURITY.md §6.2).

One hunt cycle: pick an uncovered class in scope, ask the ONE brain seam for a
proof-of-concept, store it, and let the oracle judge. Measured contribution = findings
that reproduce (never claims) and findings that are closed by a proven fix. Same
economy, same permanent record (careers, lineage) as every other Phoenix agent.

Two contributions, two oracles, both objective:
  - FIND: a PoC that reproduces a real invariant violation, twice and honestly.
  - FIX:  a patch that makes the PoC stop reproducing while the functional suite stays
          green — paid only on the *closed* finding (Article I.2).

The agent can only write inside the replica and cannot touch the invariants, the
functional tests, or the PoC corpus (redteam.apply_patch enforces all three), and
every PoC runs with no network and no subprocess (pocrunner.py). Article V, literally.

Run one cycle by hand:
    python3 gov/analyst.py --agent sec-01 [--class sqli] [--mode find|fix]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import brain
import economy
import redteam as R
import sentinel as S

CREDIT_REPRODUCED = 120       # a finding that survives the oracle
CREDIT_FIXED = 180            # a finding closed by a proven, non-destructive fix


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip() + "\n"


# ── FIND: hunt one class, prove it, bank it ───────────────────────────────────

def find_cycle(agent: str, vuln_class: str = "") -> dict:
    R.init()
    w = R.world()
    if not vuln_class:
        uncovered = w["uncovered"]
        if not uncovered:
            return {"agent": agent, "did": "nothing",
                    "reason": "every class in the checklist already has a finding"}
        vuln_class = uncovered[0]
    if not R.in_scope(R.SCOPE["target"]):
        return {"agent": agent, "did": "refused", "reason": "target out of scope"}

    module = R.read_file("vault.py")
    prompt = (
        f"You are {agent}, a defensive security analyst working ONLY on an authorized, "
        f"sandboxed replica ({R.SCOPE['target']}). Write a proof-of-concept that "
        f"demonstrates a {vuln_class} vulnerability in the service below.\n\n"
        f"Rules: define exactly `def exploit(vault):`. Drive the service ONLY through "
        f"`vault.handle({{'route': ..., ...}})`. Do NOT import anything, do NOT read "
        f"vault internals — the evidence must come from the service's own behaviour. "
        f"No network, no subprocess; neither is available.\n\n"
        f"--- vault.py (the replica under test) ---\n{module}\n\n"
        f"Reply with ONLY the Python source of the PoC. No prose, no fences.")
    try:
        src = _strip_fence(brain._chat([{"role": "user", "content": prompt}],
                                       1500, 0.3, "analyst-poc"))
    except Exception as e:
        anchor.record(-1, "error", f"{agent} could not produce a PoC: {str(e)[:120]}")
        return {"agent": agent, "did": "error", "reason": str(e)[:200]}
    return prove_and_score(agent, vuln_class, src)


def prove_and_score(agent: str, vuln_class: str, src: str) -> dict:
    """Store a PoC and let the oracle judge it. Split out so the machinery is testable
    without any model (verify injects a known-good PoC)."""
    R.init()
    poc_rel = R.write_poc(vuln_class, src, agent)
    verdict = R.reproduce(poc_rel)

    # Detection: whatever the cage denied or the oracle rejected is a signal the blue
    # layer records (SECURITY.md §6.5), independent of the red-team scoring below.
    for kind in S.classify_denials(verdict.get("denied", [])):
        S.emit(agent, kind, detail=f"while hunting {vuln_class}",
               target=R.SCOPE["target"])
    if verdict.get("dishonest"):
        S.emit(agent, "poc.fabricated", detail=verdict.get("error", "")[:200],
               target=vuln_class)
    elif verdict.get("flaky"):
        S.emit(agent, "poc.flaky", detail=verdict.get("error", "")[:200], target=vuln_class)

    if not verdict["reproduced"]:
        why = verdict.get("error") or "did not reproduce"
        kind = ("dishonest PoC" if verdict.get("dishonest")
                else "flaky PoC" if verdict.get("flaky") else "no repro")
        anchor.record(-1, "waste", f"{agent} {kind} for {vuln_class}: {why[:120]}")
        anchor.career_add(agent, -1, "retask", f"{vuln_class} unproven: {kind}")
        return {"agent": agent, "did": "unproven", "class": vuln_class,
                "reason": why, "denied": verdict.get("denied", [])}

    found_classes = set(verdict.get("classes", []))
    if vuln_class not in found_classes:
        anchor.record(-1, "waste", f"{agent} PoC reproduced {sorted(found_classes)}, "
                                   f"not the claimed {vuln_class}")
        return {"agent": agent, "did": "misfiled", "class": vuln_class,
                "actually": sorted(found_classes)}

    fid = R.record_finding(vuln_class, agent, poc_rel, verdict)
    economy.enlist(agent)
    economy.credit(agent, CREDIT_REPRODUCED)
    did = anchor.reason_add(
        -1, agent, f"prove {vuln_class}",
        f"PoC reproduced {vuln_class} against {R.SCOPE['target']}; oracle recorded "
        f"{len(verdict['violations'])} invariant violation(s)",
        derived_from=[f"scope:{R.SCOPE['target']}"], authorized_by=R.SCOPE["declared_in"])
    ev = anchor.record(-1, "work", f"{agent} PROVED {vuln_class} "
                                   f"(finding #{fid}, {R.SEVERITY.get(vuln_class)}) — "
                                   f"reproduced twice under authorization")
    anchor.decision_close(did, ev, outcome=f"+{CREDIT_REPRODUCED} contribution; "
                                           f"finding #{fid} filed with evidence")
    anchor.career_add(agent, -1, "work",
                      f"proved {vuln_class} finding #{fid} (+{CREDIT_REPRODUCED})")
    promo = economy.evaluate(agent)
    if promo:
        anchor.record(-1, "promote", f"{agent} promoted -> {promo}")
        anchor.career_add(agent, -1, "promote", f"promotion by proven findings: {promo}")
    return {"agent": agent, "did": "proved", "class": vuln_class, "finding": fid,
            "severity": R.SEVERITY.get(vuln_class), "contribution": CREDIT_REPRODUCED,
            "promoted": promo}


# ── FIX: close an open finding with a proven, non-destructive patch ───────────

def fix_cycle(agent: str, finding_id: int = 0) -> dict:
    R.init()
    open_findings = R.findings(status="reproduced")
    if not open_findings:
        return {"agent": agent, "did": "nothing", "reason": "no open findings to close"}
    f = (R.finding(finding_id) if finding_id else open_findings[0])
    if not f or f["status"] != "reproduced":
        return {"agent": agent, "did": "nothing", "reason": "finding not open"}

    module = R.read_file("vault.py")
    poc_src = R.read_file(f["poc"]) if f["poc"] else ""
    prompt = (
        f"You are {agent}, a defensive security engineer. Harden the service below so "
        f"the {f['class']} vulnerability ({f['title']}) can no longer be triggered, "
        f"WITHOUT changing its legitimate behaviour — all existing functionality must "
        f"keep working. Fix the root cause; do not merely blocklist the specific input "
        f"in the PoC.\n\n"
        f"--- the proof-of-concept that must stop working ---\n{poc_src}\n\n"
        f"--- vault.py (current) ---\n{module}\n\n"
        f"Reply with ONLY the complete corrected content of vault.py. No prose, no fences.")
    try:
        fixed = _strip_fence(brain._chat([{"role": "user", "content": prompt}],
                                         2500, 0.2, "analyst-fix"))
    except Exception as e:
        anchor.record(-1, "error", f"{agent} could not produce a fix: {str(e)[:120]}")
        return {"agent": agent, "did": "error", "reason": str(e)[:200]}
    return patch_and_score(agent, f, fixed)


def patch_and_score(agent: str, f: dict, content: str) -> dict:
    """Apply a fix and demand the oracle's proof: the PoC must stop reproducing AND the
    functional suite must stay green. Either failure reverts the patch."""
    R.init()
    old = R.read_file("vault.py")
    ok, msg = R.apply_patch("vault.py", content, actor=agent)   # refusals → Sentinel
    if not ok:
        anchor.record(-1, "waste", f"{agent} fix refused: {msg}")
        return {"agent": agent, "did": "refused", "reason": msg}

    fn = R.functional()
    still = R.reproduce(f["poc"])
    if fn["failed"] or still["reproduced"]:
        R.apply_patch("vault.py", old)             # revert: demolition or non-fix
        reason = ("broke functionality: " + ", ".join(fn["failures"])) if fn["failed"] \
            else "the PoC still reproduces — root cause not addressed"
        anchor.record(-1, "waste", f"{agent} fix for #{f['id']} reverted — {reason}")
        anchor.career_add(agent, -1, "retask", f"fix #{f['id']} reverted: {reason[:80]}")
        return {"agent": agent, "did": "reverted", "finding": f["id"], "reason": reason}

    R.set_status(f["id"], "fixed", agent=agent)
    economy.enlist(agent)
    economy.credit(agent, CREDIT_FIXED)
    did = anchor.reason_add(
        -1, agent, f"fix finding #{f['id']}",
        f"patched {f['class']}; PoC no longer reproduces and all "
        f"{fn['passed']}/{fn['total']} functional tests still pass",
        derived_from=[f"finding:{f['id']}"], authorized_by=R.SCOPE["declared_in"])
    ev = anchor.record(-1, "work", f"{agent} CLOSED finding #{f['id']} ({f['class']}) — "
                                   f"regression test proves it: PoC dead, suite green")
    anchor.decision_close(did, ev, outcome=f"+{CREDIT_FIXED} contribution; "
                                           f"finding #{f['id']} closed")
    anchor.career_add(agent, -1, "work",
                      f"closed #{f['id']} ({f['class']}) with a proven fix (+{CREDIT_FIXED})")
    promo = economy.evaluate(agent)
    if promo:
        anchor.record(-1, "promote", f"{agent} promoted -> {promo}")
        anchor.career_add(agent, -1, "promote", f"promotion by closed findings: {promo}")
    return {"agent": agent, "did": "closed", "finding": f["id"], "class": f["class"],
            "contribution": CREDIT_FIXED, "functional": f"{fn['passed']}/{fn['total']}",
            "promoted": promo}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="sec-01")
    ap.add_argument("--class", dest="vclass", default="")
    ap.add_argument("--finding", type=int, default=0)
    ap.add_argument("--mode", choices=["find", "fix"], default="find")
    a = ap.parse_args()
    anchor.init()
    economy.init()
    R.init()
    if not brain.available():
        print("no brain configured (BRAIN_* / DEEPSEEK_API_KEY) — the analyst needs a model")
        sys.exit(1)
    import json
    out = (find_cycle(a.agent, a.vclass) if a.mode == "find"
           else fix_cycle(a.agent, a.finding))
    print(json.dumps(out, indent=2))

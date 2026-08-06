"""A headless research session — the whole harness, running, with no model required.

`director.py` does this for the settlement; this does it for the lab. Every step below
goes through the real seams (`research_world`, `literature`, `researcher`, the economy
and the anchor), so what you see is the system working, not a narration of it. The
hypotheses are scripted rather than generated, which is exactly how the acceptance
suites drive it: the point is what the ORACLE does with them, and the oracle does not
care where a claim came from.

    python3 gov/lab_run.py --fresh
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import economy
import literature as L
import research_world as R
import researcher as RS

AGENT = "res-01"
DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"


def head(n, title):
    print(f"\n{BOLD}{n}. {title}{OFF}")


def say(label, detail=""):
    print(f"   {label}" + (f"  {DIM}{detail}{OFF}" if detail else ""))


def quote_from(cid, needle):
    rec = L.store_get(cid)
    return next(s for s in L.sentences(L.record_text(rec)) if needle in s)


def run(fresh: bool) -> int:
    anchor.init()
    economy.init()
    R.init()
    if fresh:
        R.reset()

    head(1, "The Vision — only a human adopts one, and not every one")
    ok, msg = R.adopt_goal("maximise the virulence of a pathogen", adopted_by="human")
    say(f"hazard goal      → {'ADOPTED' if ok else 'REFUSED'}", msg[:88])
    ok, msg = R.adopt_goal(R.DEFAULT_GOAL["text"], adopted_by="board")
    say(f"board adopts     → {'ADOPTED' if ok else 'REFUSED'}", msg[:88])
    ok, msg = R.adopt_goal(R.DEFAULT_GOAL["text"], adopted_by="human")
    say(f"human adopts     → {'ADOPTED' if ok else 'REFUSED'}", msg[:88])

    head(2, "The reproduction gate — novelty is not trusted from an unvalidated method")
    r = RS.submit_and_score(AGENT, {"subject": "CMP-101", "relation": "downregulates",
                                    "object": "ADAPTOR-Y"}, ["P-001", "P-002"])
    say(f"novel claim      → {r['did']} ({r.get('verdict')})", r.get("reason", "")[:88])
    truth = R.assay("CMP-101", "KIN-X")["affinity"]
    r = RS.reproduce_and_score(AGENT, "CMP-101", "KIN-X", truth)
    say(f"reproduce        → {r['did']}", f"CMP-101/KIN-X = {r.get('value')} kcal/mol, "
                                          f"+{r.get('contribution', 0)} contribution")

    head(3, "Real literature — a citation must be quoted, and the quote must be real")
    MET, AMPK = "PMID:41884158", "PMID:42260217"
    c_met = {"subject": "metformin", "relation": "activates", "object": "AMPK"}
    r = RS.submit_and_score(AGENT, c_met, [MET], "", {MET: "Metformin was shown to cure "
                                                           "osteoarthritis outright."})
    say(f"invented quote   → {r['did']} ({r.get('verdict')})", r.get("reason", "")[:88])
    r = RS.submit_and_score(AGENT, c_met, [MET])
    say(f"no quote at all  → {r['did']} ({r.get('verdict')})", r.get("reason", "")[:88])
    q_met = {MET: quote_from(MET, "AMPK/Drp1 pathway")}
    r = RS.submit_and_score(AGENT, c_met, [MET], "", q_met)
    say(f"quoted properly  → {r['did']} ({r.get('verdict')})", f'"{q_met[MET][:74]}..."')
    c_ampk = {"subject": "AMPK", "relation": "inhibits", "object": "NLRP3"}
    q_ampk = {AMPK: quote_from(AMPK, "AMPK-mediated suppression")}
    r = RS.submit_and_score(AGENT, c_ampk, [AMPK], "", q_ampk)
    say(f"second paper     → {r['did']} ({r.get('verdict')})", f'"{q_ampk[AMPK][:74]}..."')

    head(4, "Synthesis — what neither paper says, and both of them imply")
    ids = {c["claim"]: c["id"] for c in R.claims()}
    steps = [f"claim:{ids['metformin activates AMPK']}",
             f"claim:{ids['AMPK inhibits NLRP3']}"]
    r = RS.submit_and_score(AGENT, {"subject": "metformin", "relation": "inhibits",
                                    "object": "NLRP3"}, steps)
    say(f"direct claim     → {r['did']} ({r.get('verdict')})", r.get("reason", "")[:100])
    r = RS.submit_and_score(AGENT, {"subject": "metformin", "relation": "downregulates",
                                    "object": "NLRP3"}, steps)
    say(f"net effect       → {r['did']} ({r.get('verdict')})",
        f"{r.get('claim')} — +{r.get('contribution', 0)} contribution")

    head(5, "The toy target — the same oracle, on structured findings")
    for claim, cites in [({"subject": "CMP-101", "relation": "inhibits",
                           "object": "KIN-X"}, ["P-001"]),
                         ({"subject": "CMP-101", "relation": "downregulates",
                           "object": "ADAPTOR-Y"}, ["P-001", "P-002"]),
                         ({"subject": "KIN-X", "relation": "upregulates",
                           "object": "PATH-INFLAM"}, ["P-002", "P-003"])]:
        r = RS.submit_and_score(AGENT, claim, cites)
        say(f"{r.get('verdict', r['did']):<16} → {r.get('claim')}",
            f"+{r.get('contribution', 0)}")

    head(6, "The gate — where the organization's authority ends")
    for act in ("order reagent CMP-101", "publish the mechanism", "synthesize the candidate"):
        ok, msg = R.attempt(act)
        say(f"{act:<26} → {'ALLOWED' if ok else 'REFUSED'}", msg[:74])
    parked = R.dossiers("pending")
    if parked:
        d = parked[0]
        say(f"dossier #{d['id']:<17} → PARKED", f"{d['compound']}, awaiting a human")
        b = d["body"]
        say("  computed", f"{b['computed']['affinity']} kcal/mol, "
                          f"ADMET violations: {len(b['admet_violations'])}")
        say("  evidence chain", f"{len(b['evidence_chain'])} verified claims")
        for lim in b["limitations"][:2]:
            say("  limitation", lim[:80])
        ok, msg = R.dossier_decide(d["id"], "approve", approver=AGENT)
        say(f"  agent decides   → {'ALLOWED' if ok else 'REFUSED'}", msg[:74])
        ok, msg = R.dossier_decide(d["id"], "approve", approver="human",
                                   note="reviewed by the domain expert")
        say(f"  human decides   → {'RECORDED' if ok else 'REFUSED'}", msg[:74])

    head(7, "The world — progress is criteria met, never a claim")
    w = R.world()
    for c in R.criteria():
        say(f"[{'x' if c['met'] else ' '}] {c['name']:<26}", c["detail"][:74])
    say(f"progress {w['progress_pct']}%", f"{w['claims_verified']} verified claims "
        f"({w['claims_novel']} novel), {w['reproductions']} reproduction, "
        f"{w['papers']} corpus papers + {w['evidence_records']} real records")
    me = next((x for x in economy.roster(alive_only=False) if x["agent"] == AGENT), {})
    say("the agent", f"{me.get('role')}, {me.get('contribution')} contribution earned")

    head(8, "The permanent record — every claim walks back to its evidence")
    for c in R.claims():
        src = ", ".join(c["citations"])
        print(f"   {'novel' if c['novel'] else 'known':<6} {c['claim']:<38} {DIM}{src}{OFF}")
        for cid, q in (c.get("quotes") or {}).items():
            print(f"          {DIM}{cid}: \"{q[:86]}...\"{OFF}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="clear what the org concluded first")
    sys.exit(run(ap.parse_args().fresh))

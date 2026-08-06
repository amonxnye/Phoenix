"""Acceptance checks for the Research world — science under the constitution.

Proves RESEARCH.md build steps 1-2 without needing any model:
  1. The citation oracle: claims that don't resolve, aren't supported, are contradicted
     by their own citation, or get the direction of effect wrong are all rejected —
     by name, so the rejection is legible.
  2. The reproduction gate: a novel claim from an agent that has not re-derived a
     published result is refused, not discounted.
  3. The computational assay is deterministic, and the ADMET filter kills the
     best-scoring compound in the corpus (affinity alone is not a candidate).
  4. Article V, literally: nothing physical or public can be attempted, the literature
     is not writable, and no path escapes the sandbox.
  5. The gate: a candidate is PARKED with its evidence chain and limitations; only a
     human decides; the org's authority ends there.
  6. Contribution is measured by the oracle, and careers/lineage record it forever.

The research DB is redirected to a temp file, so running this never disturbs a live
research org's memory. Careers and the ledger use the real anchor, as verify_work does.

Run:  python3 gov/verify_research.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import economy as E
import research_world as R
import researcher as RS

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []
AGENT = "res-test"


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def claim(s, r, o):
    return {"subject": s, "relation": r, "object": o}


A.init()
E.init()
_TMP = tempfile.mkdtemp(prefix="phoenix-research-")
R.DB = os.path.join(_TMP, "research.sqlite")        # never touch a live org's memory
R.init()

try:
    # ── 1. the citation oracle ───────────────────────────────────────────────
    print("\n1. Oracle — a claim is worth what its evidence supports")
    check("the corpus is retrievable by citation", R.paper("P-001") is not None
          and R.paper("P-999") is None, f"{len(R.corpus())} papers")
    v = R.check_claim(claim("CMP-101", "inhibits", "KIN-X"), ["P-999"])
    check("a citation that does not resolve is rejected", v["verdict"] == "unresolved-citation")
    v = R.check_claim(claim("CMP-101", "inhibits", "KIN-X"), [])
    check("a claim with no citation is rejected", v["verdict"] == "unresolved-citation")
    v = R.check_claim(claim("CMP-101", "cures", "PHEN-FIBROSIS"), ["P-001"])
    check("an unknown relation is rejected", v["verdict"] == "malformed")
    v = R.check_claim(claim("CMP-404", "inhibits", "KIN-X"), ["P-008"])
    check("a claim its own citation contradicts is rejected", v["verdict"] == "contradicted",
          v["why"][:70])
    v = R.check_claim(claim("CMP-303", "inhibits", "PHEN-FIBROSIS"), ["P-007"])
    check("a claim no cited finding supports is rejected", v["verdict"] == "unsupported")
    v = R.check_claim(claim("CMP-101", "inhibits", "ADAPTOR-Y"), ["P-001", "P-002"])
    check("the wrong direction of effect is caught by name", v["verdict"] == "sign-error",
          v["why"][:90])
    v = R.check_claim(claim("CMP-101", "inhibits", "KIN-X"), ["P-001"])
    check("a restatement is verified but not novel",
          v["ok"] and v["verdict"] == "known" and not v.get("novel"))
    v = R.check_claim(claim("CMP-101", "downregulates", "ADAPTOR-Y"), ["P-001", "P-002"])
    check("a correctly composed inference is novel", v["ok"] and v["verdict"] == "novel",
          v["why"][:90])
    check("check_claim writes nothing (the oracle is pure)", R.claims() == [])

    # ── 2. the reproduction gate ─────────────────────────────────────────────
    print("\n2. Reproduction — an unvalidated method earns no novelty")
    r = R.submit_claim(AGENT, claim("CMP-101", "downregulates", "ADAPTOR-Y"),
                       ["P-001", "P-002"])
    check("a novel claim before any reproduction is refused",
          r["verdict"] == "reproduction-gate")
    bad = RS.reproduce_and_score(AGENT, "CMP-101", "KIN-X", -9.90)
    check("a reported value that does not match is a failed reproduction",
          bad["did"] == "reproduction-failed" and not R.has_reproduced(AGENT))
    truth = R.assay("CMP-101", "KIN-X")["affinity"]
    good = RS.reproduce_and_score(AGENT, "CMP-101", "KIN-X", truth)
    check("re-deriving the published value passes the gate",
          good["did"] == "reproduced" and R.has_reproduced(AGENT), f"{truth} kcal/mol")

    # ── 3. the assay and the developability filter ───────────────────────────
    print("\n3. Assay — computed, deterministic, and not the whole story")
    check("the assay is deterministic", R.assay("CMP-202", "KIN-X") == R.assay("CMP-202", "KIN-X"))
    best = min(R.candidates(), key=lambda c: c["affinity"])
    check("the best-scoring compound is killed by the ADMET filter",
          best["compound"] == "CMP-404" and len(best["admet"]) >= 5 and not best["eligible"],
          f"{best['affinity']} kcal/mol, {len(best['admet'])} violations")
    clean = next(c for c in R.candidates() if c["compound"] == "CMP-202")
    check("a clean compound with no verified link to the target is not a candidate",
          not clean["linked"] and not clean["eligible"], f"{clean['affinity']} kcal/mol")

    # ── 4. Article V, literally ──────────────────────────────────────────────
    print("\n4. Sandbox — the capability does not exist to be misused")
    for act in ("order reagent CMP-101", "synthesize the candidate", "publish the result",
                "file-ip on the mechanism", "contact-lab for a wet-lab protocol"):
        ok, msg = R.attempt(act)
        check(f"'{act.split()[0]}' is refused", not ok, msg[:58])
    ok1, m1 = R.write_note("../gov/anchor.py", "pwned")
    check("a path that escapes the sandbox is refused", not ok1, m1[:58])
    ok2, m2 = R.write_note("corpus/P-001.json", "pwned")
    check("the literature is not writable (no self-cited evidence)", not ok2, m2[:58])
    ok3, m3 = R.write_note("lab/res-test-notes.md", "# working notes\n")
    check("the researcher's own notes are writable", ok3, m3)

    # ── 5. the Vision layer ──────────────────────────────────────────────────
    print("\n5. Vision — which goals this organization will serve at all")
    ok, msg = R.adopt_goal("find a KIN-X candidate", adopted_by="board")
    check("only a human adopts a Vision", not ok, msg[:58])
    ok, msg = R.adopt_goal("increase the virulence of a pathogen", adopted_by="human")
    check("a hazard-enhancement goal is refused at adoption", not ok, msg[:70])
    ok, msg = R.adopt_goal(R.DEFAULT_GOAL["text"], adopted_by="human")
    check("a therapeutic goal is adopted", ok, msg[:60])

    # ── 6. the research loop, scored by the oracle ───────────────────────────
    print("\n6. Research — measured contribution, recorded forever")
    before = next((r["contribution"] for r in E.roster(alive_only=False)
                   if r["agent"] == AGENT), 0)
    rejected = RS.submit_and_score(AGENT, claim("CMP-101", "inhibits", "ADAPTOR-Y"),
                                   ["P-001", "P-002"])
    check("a wrong hypothesis earns nothing and is recorded as waste",
          rejected["did"] == "rejected" and rejected["verdict"] == "sign-error")
    known = RS.submit_and_score(AGENT, claim("CMP-101", "inhibits", "KIN-X"), ["P-001"])
    check("a restatement is accepted but pays nothing",
          known["did"] == "verified" and known["contribution"] == 0)
    novel = [(claim("CMP-101", "downregulates", "ADAPTOR-Y"), ["P-001", "P-002"]),
             (claim("KIN-X", "upregulates", "PATH-INFLAM"), ["P-002", "P-003"]),
             (claim("ADAPTOR-Y", "upregulates", "PHEN-FIBROSIS"), ["P-003", "P-004"])]
    outs = [RS.submit_and_score(AGENT, c, cites) for c, cites in novel]
    check("verified novel claims earn measured contribution",
          all(o["did"] == "verified" and o["novel"] and o["contribution"] > 0 for o in outs),
          f"+{sum(o['contribution'] for o in outs)}")
    dup = RS.submit_and_score("res-other", *novel[0])
    check("a claim already established is prior art, not a second discovery",
          dup["did"] == "rejected" and dup["verdict"] == "already-established")
    after = next((r["contribution"] for r in E.roster(alive_only=False)
                  if r["agent"] == AGENT), 0)
    earned = sum(o["contribution"] for o in outs) + RS.CREDIT_PER_DOSSIER
    check("contribution is what the oracle verified, never what was claimed",
          after - before == earned, f"+{after - before} over {len(outs) + 2} cycles")

    # ── 7. the gate ──────────────────────────────────────────────────────────
    print("\n7. The gate — the organization's authority ends at a human")
    parked = R.dossiers("pending")
    check("a candidate that cleared the oracle is parked, never acted on",
          len(parked) == 1 and parked[0]["compound"] == "CMP-101",
          f"dossier #{parked[0]['id']}" if parked else "none parked")
    body = parked[0]["body"] if parked else {}
    check("the dossier carries its evidence chain, scores and limitations",
          bool(body.get("evidence_chain")) and bool(body.get("limitations"))
          and body.get("computed", {}).get("affinity") is not None,
          f"{len(body.get('evidence_chain', []))} claims, "
          f"{len(body.get('limitations', []))} stated limitations")
    bad = R.park_dossier(AGENT, "CMP-404")
    check("a compound that fails the filters cannot be packaged", not bad["ok"], bad["why"][:60])
    ok, msg = R.dossier_decide(parked[0]["id"], "approve", approver=AGENT)
    check("an agent may not decide at the gate", not ok, msg[:58])
    ok, msg = R.dossier_decide(parked[0]["id"], "approve", approver="human",
                               note="reviewed by the domain expert")
    check("a human decides, and the decision is recorded", ok, msg)
    check("nothing is pending once decided", not R.dossiers("pending"))

    # ── 8. the world, scored ─────────────────────────────────────────────────
    print("\n8. The world — progress is criteria met, never a claim")
    w = R.world()
    check("every acceptance criterion is met", w["progress_pct"] == 100,
          f"{w['criteria_met']} criteria, {w['claims_novel']} novel claims, "
          f"{w['reproductions']} reproduction(s)")
    life = next((c for c in A.careers(60) if c["uid"] == AGENT), None)
    check("the researcher's career records the verified science", life is not None
          and any(e["event"] == "work" for e in (life or {}).get("events", [])))
finally:
    shutil.rmtree(_TMP, ignore_errors=True)
    note = os.path.join(R.SANDBOX, R.LAB_DIR, "res-test-notes.md")
    if os.path.exists(note):
        os.remove(note)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

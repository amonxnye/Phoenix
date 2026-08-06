"""Acceptance checks for the real-literature oracle — biology and healthcare evidence.

Every check here runs against real papers retrieved from Europe PMC and stored in
``sandbox/evidence/``, with the network **torn out**: ``urlopen`` is replaced with
something that raises before the first check runs. If any of this needed a live API,
an API key or a model, it would fail — that is the point. An oracle that cannot be run
offline and identically twice is not an oracle.

What it proves:
  1. A citation resolves to a retrieved record, or it does not resolve at all.
  2. A quote is checked verbatim against the stored text. A sentence an agent invented
     is caught by string comparison, not by judgement.
  3. The quoted sentence must actually assert the claim — and the five grammatical
     shapes the checker accepts are the ones biology actually writes in.
  4. What it must NOT accept: an entity that is oblique rather than the subject, a
     subject reaching across a clause boundary, a negated assertion, the opposite
     direction, and a paper that never mentions the entities.
  5. A paywalled citation is refused honestly (``no-verifiable-text``) instead of
     being scored on text nobody has.
  6. End to end: two literature-grounded claims compose into a novel one, and the
     whole chain — PMIDs, quotes, provenance — survives into the permanent record.

Run:  python3 gov/verify_literature.py
"""

import os
import shutil
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor as A
import economy as E
import literature as L
import research_world as R
import researcher as RS


def _no_network(*a, **k):
    raise AssertionError("the oracle path must never touch the network")


urllib.request.urlopen = _no_network            # Article V: rip the capability out

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []
AGENT = "lit-test"


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def claim(s, r, o):
    return {"subject": s, "relation": r, "object": o}


def sentence(cid, needle):
    """The exact sentence in a stored paper containing `needle` — so the tests quote
    the real text rather than a copy that could drift from it."""
    rec = L.store_get(cid)
    assert rec, f"{cid} missing from the evidence store"
    for s in L.sentences(L.record_text(rec)):
        if needle in s:
            return s
    raise AssertionError(f"no sentence in {cid} contains {needle!r}")


def synthetic(text):
    return {"id": "PMID:0", "title": "", "abstract": text, "open_access": True}


A.init()
E.init()
_TMP = tempfile.mkdtemp(prefix="phoenix-lit-")
R.DB = os.path.join(_TMP, "research.sqlite")
R.init()

# real records this suite reasons about
MET = "PMID:41884158"        # metformin -> AMPK
AMPK = "PMID:42260217"       # AMPK -| NLRP3
APIG = "PMID:42278191"       # apigenin -| EGFR
OSI = "PMID:36575215"        # 'EGFR inhibitor osimertinib'
CYST = "PMID:41596079"       # 'did not inhibit LDL oxidation'
PAYWALLED = "PMID:42502359"  # metadata only

try:
    # ── 1. the evidence store ────────────────────────────────────────────────
    print("\n1. Evidence — retrieved once, recorded with its source")
    ids = L.store_ids()
    check("the store holds retrieved records", len(ids) >= 8, f"{len(ids)} papers")
    rec = L.store_get(MET)
    check("a record carries source, url, licence and retrieval date",
          all(rec.get(k) for k in ("source", "source_url", "license", "retrieved_at")),
          f"{rec['source']}, {rec['license']}, {rec['retrieved_at'][:10]}")
    check("a PMID that was never retrieved does not resolve",
          L.store_get("PMID:99999999") is None)
    check("open-access text is stored; paywalled text is not",
          rec["abstract"] and L.store_get(PAYWALLED)["abstract"] is None)

    # ── 2. quote integrity ───────────────────────────────────────────────────
    print("\n2. Quotes — an agent cannot cite a sentence it wrote")
    real = sentence(MET, "AMPK/Drp1 pathway")
    check("a verbatim quote verifies", L.verify_quote(real, rec))
    check("whitespace and case differences still verify",
          L.verify_quote("  " + real.upper().replace(" ", "  ") + " ", rec))
    check("an invented sentence does not verify",
          not L.verify_quote("Metformin was shown to cure osteoarthritis outright.", rec))
    check("a real sentence from a DIFFERENT paper does not verify",
          not L.verify_quote(sentence(APIG, "via inhibition of the EGFR"), rec))
    check("a fragment too short to be evidence does not verify",
          not L.verify_quote("metformin", rec))

    # ── 3. the grammar the literature actually uses ──────────────────────────
    print("\n3. Support — five shapes accepted, and only those")
    shapes = [
        ("active", MET, claim("metformin", "activates", "AMPK"), "AMPK/Drp1 pathway"),
        ("agent-noun", OSI, claim("osimertinib", "inhibits", "EGFR"), "EGFR inhibitor osimertinib"),
        ("event-via", APIG, claim("apigenin", "inhibits", "EGFR"), "via inhibition of the EGFR"),
        ("compound-agent", AMPK, claim("AMPK", "inhibits", "NLRP3"), "AMPK-mediated suppression"),
    ]
    for kind, cid, c, needle in shapes:
        v = L.supports(c, L.store_get(cid), sentence(cid, needle))
        check(f"{kind}: {c['subject']} {c['relation']} {c['object']}",
              v["verdict"] == "supported", f"{cid} — {v['verdict']}")
    for kind, text, c in [
        ("passive", "EGFR was strongly inhibited by osimertinib in these cells.",
         claim("osimertinib", "inhibits", "EGFR")),
        ("event-noun", "We observed inhibition of EGFR by osimertinib at low doses.",
         claim("osimertinib", "inhibits", "EGFR"))]:
        v = L.supports(c, synthetic(text), text)
        check(f"{kind}: {c['subject']} {c['relation']} {c['object']}",
              v["verdict"] == "supported", v["verdict"])

    # ── 4. what it must refuse ───────────────────────────────────────────────
    print("\n4. Refusals — the failure modes that make an LLM dangerous here")
    v = L.supports(claim("cystamine", "inhibits", "LDL oxidation"), L.store_get(CYST),
                   sentence(CYST, "did not inhibit LDL oxidation by ferrous iron"))
    check("a source that NEGATES the claim contradicts it", v["verdict"] == "contradicted",
          v["why"][:70])
    v = L.supports(claim("apigenin", "activates", "EGFR"), L.store_get(APIG),
                   sentence(APIG, "via inhibition of the EGFR"))
    check("a source reporting the OPPOSITE direction contradicts it",
          v["verdict"] == "contradicted", v["why"][:70])
    v = L.supports(claim("aspirin", "inhibits", "EGFR"), L.store_get(APIG),
                   sentence(APIG, "via inhibition of the EGFR"))
    check("a paper that never mentions the entity cannot support it",
          v["verdict"] == "not-mentioned", v["why"][:70])
    oblique = ("SMK-002 synergized with osimertinib and further downregulated EGFR "
               "signalling in these cells.")
    v = L.supports(claim("osimertinib", "inhibits", "EGFR"), synthetic(oblique), oblique)
    check("an oblique entity is not the subject ('synergized WITH osimertinib')",
          v["verdict"] == "unsupported", v["verdict"])
    agentive = "Treatment with osimertinib inhibited EGFR phosphorylation."
    v = L.supports(claim("osimertinib", "inhibits", "EGFR"), synthetic(agentive), agentive)
    check("but 'treatment WITH osimertinib inhibited EGFR' does assert it",
          v["verdict"] == "supported", v["verdict"])
    check("a subject cannot reach across a clause boundary",
          L.find_support(claim("cystamine", "inhibits", "LDL oxidation"),
                         L.store_get(CYST)) == "",
          "'cystamine had no effect ... but cystine inhibited' supports nothing")
    v = L.supports(claim("osimertinib", "inhibits", "EGFR"), L.store_get(PAYWALLED),
                   "any sentence at all")
    check("a paywalled paper is refused, not guessed at",
          v["verdict"] == "no-verifiable-text", v["why"][:70])

    # ── 5. aliases ───────────────────────────────────────────────────────────
    print("\n5. Aliases — the other name for the same thing")
    check("UniProt synonyms are stored and used", "ERBB1" in L.aliases("EGFR"),
          ", ".join(L.aliases("EGFR")[:4]))
    alias_text = "Erlotinib inhibited ERBB1 phosphorylation in resistant clones."
    v = L.supports(claim("erlotinib", "inhibits", "EGFR"), synthetic(alias_text), alias_text)
    check("a paper using ERBB1 supports a claim about EGFR", v["verdict"] == "supported")
    check("a shorter name does not match inside a longer one",
          not L.mentions("EGFR", "ERBB2 was amplified in these tumours."))

    # ── 6. through the claim engine ──────────────────────────────────────────
    print("\n6. The oracle — provenance enforced before anything is believed")
    c_met = claim("metformin", "activates", "AMPK")
    q_met = {MET: sentence(MET, "AMPK/Drp1 pathway")}
    v = R.check_claim(c_met, [MET])
    check("a PMID cited without a quote is not evidence", v["verdict"] == "unquoted")
    v = R.check_claim(c_met, [MET], {MET: "Metformin cures everything, we found."})
    check("a fabricated quote is rejected outright", v["verdict"] == "fabricated-quote")
    v = R.check_claim(c_met, ["PMID:99999999"], {"PMID:99999999": "x" * 40})
    check("a citation that was never retrieved is rejected",
          v["verdict"] == "unresolved-citation")
    v = R.check_claim(c_met, [MET], q_met)
    check("a quoted sentence that says it is verified", v["ok"] and v["verdict"] == "known",
          v["why"][:80])
    check("check_claim still writes nothing", R.claims() == [])

    # ── 7. end to end: two grounded claims compose into a new one ────────────
    print("\n7. Synthesis — a claim no cited paper states, that both of them imply")
    truth = R.assay("CMP-101", "KIN-X")["affinity"]
    RS.reproduce_and_score(AGENT, "CMP-101", "KIN-X", truth)      # pass the gate
    r1 = RS.submit_and_score(AGENT, c_met, [MET], "", q_met)
    r2 = RS.submit_and_score(AGENT, claim("AMPK", "inhibits", "NLRP3"), [AMPK], "",
                             {AMPK: sentence(AMPK, "AMPK-mediated suppression")})
    check("both literature-grounded claims are verified as known",
          r1["verdict"] == "known" and r2["verdict"] == "known")
    ids = {c["claim"]: c["id"] for c in R.claims()}
    steps = [f"claim:{ids['metformin activates AMPK']}", f"claim:{ids['AMPK inhibits NLRP3']}"]
    r3 = RS.submit_and_score(AGENT, claim("metformin", "downregulates", "NLRP3"), steps)
    check("composing them yields a NOVEL claim, and it is paid for",
          r3["did"] == "verified" and r3["novel"] and r3["contribution"] > 0,
          r3.get("claim", ""))
    wrong = RS.submit_and_score(AGENT, claim("metformin", "inhibits", "NLRP3"), steps)
    check("the same two steps do NOT support a direct 'inhibits'",
          wrong["verdict"] == "sign-error", wrong.get("reason", "")[:80])
    stored = next(c for c in R.claims() if c["claim"] == "metformin downregulates NLRP3")
    check("the novel claim's provenance names the two steps it stands on",
          set(stored["support"]) == set(steps), ", ".join(stored["support"]))
    grounded = next(c for c in R.claims() if c["claim"] == "AMPK inhibits NLRP3")
    check("a grounded claim keeps its PMID and the exact quoted sentence",
          grounded["citations"] == [AMPK] and AMPK in grounded["quotes"]
          and L.verify_quote(grounded["quotes"][AMPK], L.store_get(AMPK)),
          f"{AMPK}: \"{grounded['quotes'].get(AMPK, '')[:60]}...\"")
    life = next((c for c in A.careers(60) if c["uid"] == AGENT), None)
    check("the career records the science and the waste alike", life is not None
          and any(e["event"] == "work" for e in (life or {}).get("events", []))
          and any(e["event"] == "retask" for e in (life or {}).get("events", [])))
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

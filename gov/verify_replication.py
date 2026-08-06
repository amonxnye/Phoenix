"""Acceptance checks for the replication harness and the campaign engine.

Runs entirely offline — ``urlopen`` is replaced with something that raises before the
first check — against real papers already in the evidence store.

What it proves:
  1. The arithmetic oracle agrees with textbook values, and catches impossible numbers.
  2. You cannot replicate a claim the paper never made: a claim is quote-checked against
     the target before it goes under test (the anti-strawman rule).
  3. A paper is never its own witness, and evidence is graded by where it sits — a
     result counts, a title does not, and a hedge ("a future study looking at the
     feasibility of X to induce Y") counts as neither.
  4. Findings are append-only: a contradiction does not erase a corroboration.
  5. The campaign climbs its ladder when a round is barren, drops back when one is
     productive, skips rungs whose work is finished, and can only use equivalences a
     human supplied.
  6. It never stops quietly: every ending is settled, escalated or exhausted, and an
     undetermined verdict carries the specific thing that would settle it.

Run:  python3 gov/verify_replication.py
"""

import json
import os
import shutil
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import campaign as CAM
import literature as L
import statcheck as NUM
import replication as REP


def _no_network(*a, **k):
    raise AssertionError("the replication oracle must never touch the network")


urllib.request.urlopen = _no_network

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []
TARGET = "PMID:41964971"                            # the DIREM trial, in the store
PAYWALLED = "PMID:42502359"


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def claim(s, r, o):
    return {"subject": s, "relation": r, "object": o}


_TMP = tempfile.mkdtemp(prefix="phoenix-rep-")
REP.DB = os.path.join(_TMP, "replication.sqlite")
_ALIASES = L.ALIASES_PATH
if os.path.exists(_ALIASES):                        # never mutate the repo's aliases
    shutil.copy(_ALIASES, os.path.join(_TMP, "aliases.json"))
L.ALIASES_PATH = os.path.join(_TMP, "aliases.json")
REP.init()

QUOTE = ("Conclusions Both calorie-carbohydrate restriction alone and in combination "
         "with intermittent fasting significantly improved glycemic control and induced "
         "diabetes remission compared with the control group.")

try:
    # ── 1. the arithmetic oracle ─────────────────────────────────────────────
    print("\n1. Arithmetic — the paper's own numbers, redone")
    check("chi-square, normal and t tails match textbook values",
          all(abs(p - 0.05) < 5e-4 for p in (NUM.p_from_chi2(3.841, 1),
                                             NUM.p_from_chi2(5.991, 2),
                                             NUM.p_from_z(1.96),
                                             NUM.p_from_t(2.228, 10))),
          "chi2(3.841,1), chi2(5.991,2), z=1.96, t=2.228 df=10 all → 0.0500")
    check("a percentage that follows from the counts is consistent",
          NUM.percentage(9, 40, 22.5)["ok"])
    check("a percentage that does not is caught",
          not NUM.percentage(9, 40, 25.0)["ok"], NUM.percentage(9, 40, 25.0)["why"][:60])
    t = NUM.two_by_two(9, 31, 1, 39)
    check("the 2x2 table reproduces the reported odds ratio",
          NUM.agrees(t["or"], 11.7) and abs(t["p_wald"] - 0.024) < 0.002,
          f"OR {t['or']} (95% CI {t['ci'][0]}–{t['ci'][1]}), Wald p {t['p_wald']}")
    check("a zero cell is corrected, and the correction is declared",
          NUM.two_by_two(0, 40, 5, 35)["haldane_corrected"])
    check("GRIM accepts an attainable mean", NUM.grim(3.15, 20)["ok"])
    check("GRIM rejects an impossible one", not NUM.grim(3.17, 20)["ok"],
          NUM.grim(3.17, 20)["why"][:64])
    check("an SD larger than the scale allows is impossible",
          not NUM.sd_bound(4.5, 2.0, 30, 1, 5)["ok"], NUM.sd_bound(4.5, 2.0, 30, 1, 5)["why"][:64])
    check("a CI that is not centred on its point estimate is flagged",
          NUM.ci_symmetry(11.7, 1.4, 98.3)["ok"] and not NUM.ci_symmetry(1.5, 0.6, 4.3)["ok"],
          NUM.ci_symmetry(1.5, 0.6, 4.3)["why"][:70])

    # ── 2. fidelity: no strawmen ─────────────────────────────────────────────
    print("\n2. Fidelity — you cannot replicate a claim the paper never made")
    ok, msg = REP.adopt_target(TARGET, adopted_by="campaign")
    check("only a human puts a paper under replication", not ok, msg[:64])
    ok, msg = REP.adopt_target("PMID:99999999", adopted_by="human")
    check("a paper that was never retrieved cannot be a target", not ok, msg[:64])
    ok, msg = REP.adopt_target(PAYWALLED, adopted_by="human")
    check("a paper with no readable text cannot be a target", not ok, msg[:64])
    ok, msg = REP.adopt_target(TARGET, adopted_by="human")
    check("a human adopts the target", ok, msg[:60])
    r = REP.put_claim(TARGET, claim("calorie-carbohydrate restriction", "reduces",
                                    "diabetes remission"), QUOTE)
    check("a claim the paper contradicts is refused as not-in-paper",
          r["verdict"] == "not-in-paper", r["why"][:70])
    r = REP.put_claim(TARGET, claim("metformin", "induces", "diabetes remission"), QUOTE)
    check("a claim about something the paper never mentions is refused",
          r["verdict"] == "not-in-paper", r["why"][:70])
    r = REP.put_claim(TARGET, claim("calorie-carbohydrate restriction", "induces",
                                    "diabetes remission"), QUOTE)
    check("the paper's own claim goes under test, with its section recorded",
          r["ok"] and r["section"] == "result", f"quoted from the {r.get('section')}")
    CLAIM_ID = r["claim_id"]

    # ── 3. independence and grading ──────────────────────────────────────────
    print("\n3. Independence — a paper is never its own witness")
    found = REP.independent(TARGET, CLAIM_ID)
    check("the target is excluded from its own replication",
          all(f["source"] != TARGET for f in found), f"{len(found)} independent hit(s)")
    check("evidence is graded, and weak grades do not count",
          all(f["counts"] == (f["grade"] in REP.RESULT_GRADE) for f in found),
          ", ".join(f"{f['source']}[{f['grade']}]" for f in found) or "none")
    rec = L.store_get("PMID:37881774")
    hedge_quote = next((s for s in L.sentences(L.record_text(rec))
                        if "feasibility of a catered meal" in s), "")
    L.aliases_put("calorie restriction", ["low-energy dietary intervention"])
    L.aliases_put("diabetes remission", ["T2D remission"])
    hedged = L.supports(claim("calorie restriction", "induces", "diabetes remission"),
                        rec, hedge_quote)
    check("a hedged or purposive sentence in a CONCLUSION is not corroboration",
          hedged["verdict"] == "hedged" and L.section_of(rec, hedge_quote) == "result",
          hedged["why"][:96])

    # ── 4. value is never destroyed ──────────────────────────────────────────
    print("\n4. Value — nothing verified is deleted to tidy the story")
    before = len(REP.findings(TARGET))
    REP.independent(TARGET, CLAIM_ID)
    check("re-running a search does not duplicate or drop findings",
          len(REP.findings(TARGET)) == before, f"{before} findings, stable")
    REP._conn().close()
    c = REP._conn()
    c.execute("INSERT OR IGNORE INTO findings(target, claim_id, source, verdict, grade, "
              "quote, alias, round) VALUES(?,?,?,?,?,?,?,?)",
              (TARGET, CLAIM_ID, "PMID:00000001", "contradicted", "result",
               "A low-energy diet did not induce remission of type 2 diabetes.", "x/y", 9))
    c.commit()
    c.close()
    f = REP.findings(TARGET, CLAIM_ID)
    check("a contradiction coexists with corroboration, both on the record",
          any(x["verdict"] == "contradicted" for x in f) and len(f) > before,
          f"{len(f)} findings on this claim")
    v = REP.verdict(TARGET)
    check("one result-grade contradiction diverges the verdict",
          v["verdict"] == "diverged", v["why"][:70])
    c = REP._conn()
    c.execute("DELETE FROM findings WHERE source='PMID:00000001'")
    c.commit()
    c.close()

    # ── 5. the verdict names what would settle it ────────────────────────────
    print("\n5. Verdict — undetermined is never allowed to be silent")
    v = REP.verdict(TARGET)
    check("an unsettled replication carries a specific blocker",
          v["verdict"] == "undetermined" and bool(v["blockers"]),
          v["blockers"][0][:88] if v["blockers"] else "none")
    REP.recompute(TARGET, "synthetic GRIM probe", "grim", {"n": 20, "dp": 2}, 3.17)
    v = REP.verdict(TARGET)
    check("an impossible reported value refutes the paper outright",
          v["verdict"] == "refuted", v["why"][:76])
    c = REP._conn()
    c.execute("DELETE FROM recomputations WHERE label='synthetic GRIM probe'")
    c.commit()
    c.close()

    # ── 6. the campaign engine ───────────────────────────────────────────────
    print("\n6. The campaign — creative only when being ordinary stops working")
    spec = json.load(open(os.path.join(os.path.dirname(HERE), "sandbox", "campaigns",
                                       "PMID-41964971.json")))
    REP.reset()
    CAM.prepare(spec)
    c = CAM.Campaign(spec, offline=True, max_rounds=30, log=lambda *_: None)
    res = c.run()
    moves = [h["move"] for h in res["history"]]
    check("it runs to a conclusion with no network at all",
          res["how"] in ("settled", "escalated", "exhausted"),
          f"{res['how']} after {res['rounds']} rounds, {res['ingests']} retrievals")
    check("a barren round climbs the ladder", moves.index("search-store") > moves.index("recompute")
          if "search-store" in moves and "recompute" in moves else False,
          " → ".join(dict.fromkeys(moves)))
    check("it reaches the judgement rungs only after the free ones are spent",
          "declare-equivalence" in moves and
          moves.index("declare-equivalence") > moves.index("search-store"))
    check("a productive round drops back to the cheap checks",
          any(moves[i] == "search-store" and moves[i - 1] == "declare-equivalence"
              for i in range(1, len(moves))))
    check("equivalences come only from the human's candidate list",
          set(res["declared_equivalences"]) <= {e["entity"] for e in spec["equivalences"]},
          ", ".join(res["declared_equivalences"]) or "none used")
    strong = "calorie-carbohydrate restriction induces diabetes remission"
    under_test = {cl["claim"] for cl in REP.claims(TARGET)}
    check("weakening adds a claim and never replaces the original",
          strong in under_test and len(under_test) > 1,
          f"{len(under_test)} claims under test")
    check("it did not talk itself into a replication it cannot support",
          res["verdict"] == "undetermined" and bool(res["blockers"]),
          res["blockers"][0][:80] if res["blockers"] else "")
    check("the ending is never silent",
          bool(res["why"]) and res["how"] in ("settled", "escalated", "exhausted"))

    # ── 7. the gate ──────────────────────────────────────────────────────────
    print("\n7. The gate — saying a paper is wrong is irreversible")
    d = REP.park_critique(TARGET)
    body = d["body"]
    check("the critique is parked, never published", d["status"] == "pending",
          f"critique #{d['critique']}")
    check("it carries the quotes, the arithmetic and what is still open",
          bool(body["claims_under_test"]) and bool(body["arithmetic"])
          and bool(body["limitations"]),
          f"{len(body['claims_under_test'])} claims, {len(body['arithmetic'])} "
          f"recomputations, {len(body['limitations'])} limitations")
    check("every equivalence it leaned on is listed for a reviewer to reject",
          "equivalences_relied_on" in body)
    ok, msg = REP.critique_decide(d["critique"], "approve", approver="campaign")
    check("the campaign may not publish its own critique", not ok, msg[:64])
    ok, msg = REP.critique_decide(d["critique"], "approve", approver="human")
    check("a human decides, and it is recorded", ok, msg)
finally:
    shutil.rmtree(_TMP, ignore_errors=True)
    L.ALIASES_PATH = _ALIASES

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

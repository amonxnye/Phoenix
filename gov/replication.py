"""The replication world — take a paper apart and try to rebuild its conclusion.

The question this answers is not "does this sound right". It is: **can the conclusion
be reached again without taking the paper's word for it, and do the paper's own numbers
survive being redone?** Three oracles, none of which is an opinion:

1. **Fidelity.** A claim put under test must be one the paper actually makes — it is
   quote-checked against the stored paper before anything else happens. You cannot
   replicate, or refute, a claim the authors never made. This is the anti-strawman
   rule, and it is enforced first.
2. **Arithmetic** (`statcheck.py`). The reported percentages, odds ratios, confidence
   intervals and p-values are recomputed from the counts the paper itself reports. A
   mean that no sample size can produce is impossible, not debatable.
3. **Independence.** The same claim is sought in *other* papers, with the target
   excluded, and every hit is graded by where it sits: a paper's own **result** or
   **conclusion** is evidence, its **title** is a topic, and its **introduction** is
   usually a restatement of somebody else — quite possibly of the very paper under
   test. Only result-grade support counts toward replication; weaker support is kept
   and labelled, never quietly promoted.

Equivalence is declared, not assumed. Corroborating "calorie-carbohydrate restriction"
with a paper about "very low calorie diets" is a judgement about whether those are the
same intervention; the alias that made the match is recorded on the finding so a
reviewer can reject it. A replication that hides its equivalences is not one.

The verdict is one of: **corroborated · diverged · refuted · undetermined**, and
`undetermined` is never allowed to be silent — it carries the specific thing that would
settle it (Article IX). The end of the road is a critique dossier parked for a human,
because saying in public that a paper is wrong is irreversible and lands on real
people's names.
"""

import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import literature as L
import statcheck as NUM

RESULT_GRADE = ("result", "conclusion")            # a paper's own contribution
WEAK_GRADE = ("title", "background", "methods", "unsectioned")


def _data_dir() -> str:
    d = os.environ.get("GOV_DATA_DIR", "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return _HERE


DB = os.path.join(_data_dir(), "replication.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS targets("
                  "pmid TEXT PRIMARY KEY, title TEXT, adopted_by TEXT, status TEXT "
                  "DEFAULT 'open')")
        c.execute("CREATE TABLE IF NOT EXISTS tclaims("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, subject TEXT, "
                  "relation TEXT, object TEXT, quote TEXT, section TEXT, "
                  "UNIQUE(target, subject, relation, object))")
        c.execute("CREATE TABLE IF NOT EXISTS recomputations("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, label TEXT, "
                  "kind TEXT, reported TEXT, recomputed TEXT, verdict TEXT, why TEXT, "
                  "UNIQUE(target, label))")
        # findings are APPEND-ONLY: a later round may contradict an earlier one, and
        # both stay. Nothing a campaign learns is ever deleted to make a story tidier.
        c.execute("CREATE TABLE IF NOT EXISTS findings("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, claim_id INT, "
                  "source TEXT, verdict TEXT, grade TEXT, quote TEXT, alias TEXT, "
                  "round INT DEFAULT 0, UNIQUE(target, claim_id, source, verdict))")
        c.execute("CREATE TABLE IF NOT EXISTS critiques("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, body TEXT, "
                  "status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', "
                  "note TEXT DEFAULT '')")
        c.commit()
    finally:
        c.close()


def reset() -> None:
    init()
    c = _conn()
    try:
        for t in ("targets", "tclaims", "recomputations", "findings", "critiques"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
    finally:
        c.close()


# ── 1. the target, and the claims it actually makes ───────────────────────────

def adopt_target(pmid: str, adopted_by: str = "human") -> tuple[bool, str]:
    """Aim the organization at a paper. Only a human does this: challenging published
    work is a reputational act even before anything is said out loud (Article I)."""
    if adopted_by != "human":
        return False, f"REFUSED: only a human puts a paper under replication — {adopted_by} may not"
    rec = L.store_get(pmid)
    if rec is None:
        return False, f"REFUSED: {pmid} is not in the evidence store — retrieve it first"
    if rec.get("abstract") is None:
        return False, (f"REFUSED: {pmid} has no open text to check. A replication that "
                       f"cannot read the paper is not one")
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO targets(pmid, title, adopted_by) VALUES(?,?,?)",
                  (pmid, rec["title"], adopted_by))
        c.commit()
    finally:
        c.close()
    return True, f"under replication: {rec['title'][:70]}"


def put_claim(target: str, claim: dict, quote: str) -> dict:
    """Put one of the paper's claims under test. The paper must actually make it: the
    quote is verified verbatim against the stored text and must assert the claim, or
    the claim is refused as not-in-paper. No strawmen."""
    rec = L.store_get(target)
    if rec is None:
        return {"ok": False, "verdict": "no-target", "why": f"{target} is not stored"}
    v = L.supports(claim, rec, quote)
    if v["verdict"] != "supported":
        return {"ok": False, "verdict": "not-in-paper",
                "why": f"{target} does not make this claim ({v['verdict']}): {v['why'][:150]}"}
    s, r, o = claim["subject"], claim["relation"], claim["object"]
    section = L.section_of(rec, quote)
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO tclaims(target, subject, relation, object, "
                  "quote, section) VALUES(?,?,?,?,?,?)",
                  (target, s, r, o, L._norm_ws(quote), section))
        c.commit()
        row = c.execute("SELECT id FROM tclaims WHERE target=? AND subject=? AND "
                        "relation=? AND object=?", (target, s, r, o)).fetchone()
    finally:
        c.close()
    return {"ok": True, "verdict": "under-test", "claim_id": row[0],
            "claim": f"{s} {r} {o}", "section": section,
            "why": f"the paper states it in its {section}"}


def claims(target: str) -> list[dict]:
    c = _conn()
    try:
        return [{"id": i, "claim": f"{s} {r} {o}", "subject": s, "relation": r,
                 "object": o, "quote": q, "section": sec}
                for i, s, r, o, q, sec in c.execute(
                    "SELECT id, subject, relation, object, quote, section FROM tclaims "
                    "WHERE target=? ORDER BY id", (target,))]
    finally:
        c.close()


# ── 2. the arithmetic, redone ─────────────────────────────────────────────────

def recompute(target: str, label: str, kind: str, args: dict, reported) -> dict:
    """Redo one reported statistic from the counts the paper gives. `kind` is one of
    percentage | two_by_two | grim | sd_bound | ci_symmetry."""
    if kind == "percentage":
        r = NUM.percentage(args["count"], args["total"], reported, args.get("dp", 1))
        verdict, got = ("consistent" if r["ok"] else "divergent"), r.get("recomputed")
    elif kind == "two_by_two":
        t = NUM.two_by_two(args["a"], args["b"], args["c"], args["d"])
        got = t["or"]
        verdict = "consistent" if NUM.agrees(t["or"], float(reported)) else "divergent"
        r = {"why": f"2x2 [{args['a']},{args['b']};{args['c']},{args['d']}] gives "
                    f"OR {t['or']} (95% CI {t['ci'][0]}–{t['ci'][1]}, Wald p "
                    f"{t['p_wald']}) against a reported {reported}", **t}
    elif kind == "grim":
        r = NUM.grim(reported, args["n"], args.get("dp", 2), args.get("items", 1))
        verdict, got = ("consistent" if r["ok"] else "impossible"), r.get("nearest_possible")
    elif kind == "sd_bound":
        r = NUM.sd_bound(args["mean"], reported, args["n"], args["lo"], args["hi"])
        verdict, got = ("consistent" if r["ok"] else "impossible"), r.get("max_possible")
    elif kind == "ci_symmetry":
        r = NUM.ci_symmetry(float(reported), args["lo"], args["hi"])
        verdict, got = ("consistent" if r["ok"] else "divergent"), r.get("implied_point")
    else:
        return {"ok": False, "why": f"no such check: {kind}"}
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO recomputations(target, label, kind, reported, "
                  "recomputed, verdict, why) VALUES(?,?,?,?,?,?,?)",
                  (target, label, kind, json.dumps(reported), json.dumps(got),
                   verdict, r["why"]))
        c.commit()
    finally:
        c.close()
    return {"ok": verdict == "consistent", "verdict": verdict, "label": label,
            "reported": reported, "recomputed": got, "why": r["why"]}


def recomputations(target: str) -> list[dict]:
    c = _conn()
    try:
        return [{"label": la, "kind": k, "reported": json.loads(rep),
                 "recomputed": json.loads(rec), "verdict": v, "why": w}
                for la, k, rep, rec, v, w in c.execute(
                    "SELECT label, kind, reported, recomputed, verdict, why FROM "
                    "recomputations WHERE target=? ORDER BY id", (target,))]
    finally:
        c.close()


# ── 3. independence: reach the conclusion without the paper ───────────────────

def independent(target: str, claim_id: int, round_no: int = 0) -> list[dict]:
    """Look for the claim in every other stored paper. Records what it finds, graded,
    with the alias that made the match — and never counts the target as its own witness."""
    cl = next((c for c in claims(target) if c["id"] == claim_id), None)
    if cl is None:
        return []
    claim = {"subject": cl["subject"], "relation": cl["relation"], "object": cl["object"]}
    out = []
    for cid in L.store_ids():
        if cid == target:
            continue                               # a paper cannot replicate itself
        rec = L.store_get(cid)
        if not rec or rec.get("abstract") is None:
            continue
        sent = L.find_support(claim, rec)
        verdict, quote = None, ""
        if sent:
            v = L.supports(claim, rec, sent)
            if v["verdict"] in ("supported", "contradicted"):
                verdict, quote = v["verdict"], sent
        if verdict is None:
            for s in L.sentences(L.record_text(rec)):
                v = L.supports(claim, rec, s)
                if v["verdict"] == "contradicted":
                    verdict, quote = "contradicted", s
                    break
        if verdict is None:
            continue
        grade = L.section_of(rec, quote)
        alias = f"{L.matched_alias(cl['subject'], quote)} / {L.matched_alias(cl['object'], quote)}"
        out.append({"source": cid, "verdict": verdict, "grade": grade,
                    "quote": quote, "alias": alias,
                    "counts": grade in RESULT_GRADE})
        c = _conn()
        try:
            c.execute("INSERT OR IGNORE INTO findings(target, claim_id, source, verdict,"
                      " grade, quote, alias, round) VALUES(?,?,?,?,?,?,?,?)",
                      (target, claim_id, cid, verdict, grade, quote, alias, round_no))
            c.commit()
        finally:
            c.close()
    return out


def findings(target: str, claim_id: int | None = None) -> list[dict]:
    c = _conn()
    try:
        q = ("SELECT id, claim_id, source, verdict, grade, quote, alias, round FROM "
             "findings WHERE target=?")
        args = [target]
        if claim_id is not None:
            q += " AND claim_id=?"
            args.append(claim_id)
        return [{"id": i, "claim_id": ci, "source": s, "verdict": v, "grade": g,
                 "quote": qt, "alias": a, "round": rd, "counts": g in RESULT_GRADE}
                for i, ci, s, v, g, qt, a, rd in c.execute(q + " ORDER BY id", args)]
    finally:
        c.close()


# ── 4. the verdict, and what would settle it ──────────────────────────────────

def verdict(target: str) -> dict:
    """corroborated | diverged | refuted | undetermined — and if undetermined, the
    specific thing that would settle it. A replication that shrugs is a failed one."""
    recs, cls = recomputations(target), claims(target)
    impossible = [r for r in recs if r["verdict"] == "impossible"]
    divergent = [r for r in recs if r["verdict"] == "divergent"]
    per_claim, blockers = [], []
    for cl in cls:
        f = findings(target, cl["id"])
        pro = [x for x in f if x["verdict"] == "supported" and x["counts"]]
        con = [x for x in f if x["verdict"] == "contradicted" and x["counts"]]
        weak = [x for x in f if x["counts"] is False]
        state = ("contradicted" if con else "corroborated" if pro else
                 "weakly-corroborated" if weak else "unreplicated")
        per_claim.append({"claim": cl["claim"], "state": state, "for": len(pro),
                          "against": len(con), "weak": len(weak),
                          "sources": [x["source"] for x in pro + con]})
        if state == "unreplicated":
            blockers.append(f"no independent paper in the store states '{cl['claim']}' — "
                            f"widen the search or accept it is untested")
        elif state == "weakly-corroborated":
            blockers.append(f"'{cl['claim']}' is supported only in a title or "
                            f"introduction ({len(weak)} source(s)) — find a paper whose "
                            f"own results say it")

    if impossible:
        v, why = "refuted", (f"{len(impossible)} reported value(s) are arithmetically "
                             f"impossible: " + "; ".join(r["label"] for r in impossible))
    elif any(c["state"] == "contradicted" for c in per_claim):
        v, why = "diverged", ("independent results contradict the paper: " +
                              ", ".join(c["claim"] for c in per_claim
                                        if c["state"] == "contradicted"))
    elif not cls:
        v, why = "undetermined", "no claim has been put under test yet"
    elif all(c["state"] == "corroborated" for c in per_claim) and not divergent:
        v, why = "corroborated", ("every claim under test is independently corroborated "
                                  "by result-grade evidence, and the paper's own "
                                  "arithmetic recomputes")
    else:
        v, why = "undetermined", "; ".join(blockers) or "the checks are incomplete"
    if divergent and v not in ("refuted", "diverged"):
        why += (f" | {len(divergent)} reported statistic(s) differ from our "
                f"recomputation beyond rounding: " +
                ", ".join(r["label"] for r in divergent))
    return {"target": target, "verdict": v, "why": why, "claims": per_claim,
            "recomputations": recs, "blockers": blockers,
            "settled": v in ("corroborated", "diverged", "refuted")}


# ── 5. the gate — saying it in public is irreversible ─────────────────────────

def park_critique(target: str, by: str = "campaign") -> dict:
    """Package the replication — what was tested, what recomputed, who corroborated or
    contradicted, on what equivalences, and what remains open — and park it for a human.
    Publishing a critique, posting to PubPeer, or writing to the authors are all
    irreversible acts against named people, and none of them happen here."""
    rec = L.store_get(target)
    v = verdict(target)
    body = {
        "target": {"id": target, "title": rec["title"] if rec else "",
                   "journal": rec.get("journal") if rec else "",
                   "year": rec.get("year") if rec else "", "url": rec.get("source_url") if rec else ""},
        "verdict": v["verdict"], "why": v["why"],
        "claims_under_test": [{"claim": c["claim"], "quoted_from_the_paper": c["quote"],
                               "section": c["section"]} for c in claims(target)],
        "arithmetic": v["recomputations"],
        "independent_evidence": findings(target),
        "equivalences_relied_on": sorted({f["alias"] for f in findings(target)
                                          if f["verdict"] == "supported"}),
        "still_open": v["blockers"],
        "limitations": [
            "only abstracts were read — full texts, appendices and raw data were not",
            "independence means 'not the target paper'; a citing paper is not detectable "
            "from an abstract, so corroboration may not be fully independent",
            "equivalence between interventions and outcomes is a declared judgement, "
            "listed above, and a reviewer may reject any of it",
            "arithmetic that recomputes proves consistency, never honesty or validity",
        ],
        "requires": "a human to review before ANY public statement about this work",
    }
    c = _conn()
    try:
        cur = c.execute("INSERT INTO critiques(target, body) VALUES(?,?)",
                        (target, json.dumps(body)))
        c.commit()
        cid = cur.lastrowid
    finally:
        c.close()
    return {"ok": True, "critique": cid, "status": "pending", "body": body,
            "why": "parked for a human — nothing is said in public by this system"}


def critiques(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = "SELECT id, target, body, status, decided_by, note FROM critiques"
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        return [{"id": i, "target": t, "body": json.loads(b), "status": s,
                 "decided_by": d, "note": n}
                for i, t, b, s, d, n in c.execute(q + " ORDER BY id", args)]
    finally:
        c.close()


def critique_decide(cid: int, decision: str, approver: str, note: str = "") -> tuple[bool, str]:
    if approver != "human":
        return False, f"REFUSED: {approver} may not decide — a critique is published by a person"
    if decision not in ("approve", "refuse"):
        return False, "decision must be 'approve' or 'refuse'"
    c = _conn()
    try:
        cur = c.execute("UPDATE critiques SET status=?, decided_by=?, note=? WHERE id=? "
                        "AND status='pending'",
                        ("approved" if decision == "approve" else "refused",
                         approver, note, cid))
        c.commit()
        if not cur.rowcount:
            return False, f"critique {cid} is not pending"
    finally:
        c.close()
    return True, f"critique {cid} {decision}d by {approver}"

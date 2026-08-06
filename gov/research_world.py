"""The Research world — a governed organization that does science (RESEARCH.md).

Where ``sim.py``'s world is resources and ages and ``workspace.py``'s is a codebase
and its test suite, this world is a **literature corpus and a computational assay**.
The mapping is exact and the one law survives it:

    the oracle is computational and evidentiary; the gate is anything physical or public.

Three layers of oracle, cheapest first (RESEARCH.md §3), none of which asks a model
whether a result is real:

1. ``check_claim`` — every claim must cite retrievable papers, and the cited papers
   must actually contain findings that support it. A claim no cited paper supports is
   rejected. A claim its own citation contradicts is rejected. A claim that restates a
   finding verbatim is *verified but known* — no advance. A claim that is one composed
   inferential step away from two cited findings, and gets the direction right, is
   **novel** — that is the only thing that earns contribution.
2. ``reproduce`` — an agent must re-derive a published number from the public
   descriptor table before any of its novel claims count. Fail the reproduction and
   the novel claims are refused, not discounted (the seam where a real benchmark rerun
   plugs in).
3. ``assay``/``admet`` — deterministic computed scores. Same inputs, same score,
   instantly, with no model in the loop.

Article V is literal here. The sandbox has no procurement credentials, no lab
actuators and no ordering network, so ``attempt()`` refuses every physical or public
action unconditionally — the capability does not exist to be misused. The only write
path an agent has is ``write_note`` into ``sandbox/lab/``, and it refuses the corpus
outright: an agent cannot cite evidence it wrote itself, exactly as the workspace
worker cannot pass its exam by editing the exam.

Safety scope (RESEARCH.md §4): therapeutic and beneficial discovery under human
oversight. ``adopt_goal`` screens the Vision itself and refuses goals of a
weaponization or hazard-enhancement shape — and only a human may adopt one at all.
"""

import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import literature as L                             # the real-literature oracle (offline)
SANDBOX = os.path.join(os.path.dirname(_HERE), "sandbox")
CORPUS_DIR = "corpus"                              # read-only: the literature
LAB_DIR = "lab"                                    # the researcher's only writable ground


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


DB = os.path.join(_data_dir(), "research.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init() -> None:
    c = _conn()
    try:
        # every verified claim, its evidence, and who reached it — the corpus the org
        # adds to. UNIQUE on the triple: a claim already established is prior art.
        c.execute("CREATE TABLE IF NOT EXISTS claims("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, relation TEXT, "
                  "object TEXT, novel INT DEFAULT 0, agent TEXT, citations TEXT, "
                  "support TEXT, quotes TEXT DEFAULT '{}', "
                  "UNIQUE(subject, relation, object))")
        cols = {row[1] for row in c.execute("PRAGMA table_info(claims)")}
        if "quotes" not in cols:                   # a store written before quoting
            c.execute("ALTER TABLE claims ADD COLUMN quotes TEXT DEFAULT '{}'")
        c.execute("CREATE TABLE IF NOT EXISTS reproductions("
                  "agent TEXT, compound TEXT, target TEXT, reported REAL, "
                  "truth REAL, ok INT, PRIMARY KEY(agent, compound, target))")
        # the gate: a candidate that clears the oracle is PARKED, never acted on
        c.execute("CREATE TABLE IF NOT EXISTS dossiers("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, compound TEXT, agent TEXT, "
                  "body TEXT, status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', "
                  "note TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS goal(k TEXT PRIMARY KEY, v TEXT)")
        c.commit()
    finally:
        c.close()
    os.makedirs(os.path.join(SANDBOX, LAB_DIR), exist_ok=True)


# ── the corpus — retrievable literature, never writable ───────────────────────

_CORPUS: dict | None = None


def corpus() -> dict:
    """Every paper by id. Loaded once; the literature does not change under the org."""
    global _CORPUS
    if _CORPUS is None:
        out = {}
        d = os.path.join(SANDBOX, CORPUS_DIR)
        for name in sorted(os.listdir(d)):
            if name.endswith(".json") and name != "assays.json":
                with open(os.path.join(d, name)) as f:
                    p = json.load(f)
                out[p["id"]] = p
        _CORPUS = out
    return _CORPUS


def paper(pid: str) -> dict | None:
    """Resolve a citation. Returns None if it does not resolve — the first way a
    hypothesis dies, and the cheapest."""
    return corpus().get(str(pid).strip())


def corpus_index() -> list[dict]:
    """What a researcher reads: every paper's id, title, year and findings."""
    return [{"id": p["id"], "title": p["title"], "year": p["year"],
             "findings": p["findings"]} for p in corpus().values()]


# ── layer 3: the computational assay (deterministic, instant) ─────────────────

def _assay_table() -> dict:
    with open(os.path.join(SANDBOX, CORPUS_DIR, "assays.json")) as f:
        return json.load(f)


def assay(compound: str, target: str) -> dict | None:
    """Predicted binding affinity for a compound/target pair, from the public
    descriptor table. Lower (more negative) is tighter. Deterministic: this is a
    computation, not a judgement — which is exactly why it can be an oracle."""
    t = _assay_table()
    d = t["compounds"].get(compound)
    w = t["targets"].get(target)
    if not d or not w:
        return None
    score = (w["mw"] * d["mw"] / 500 + w["logp"] * d["logp"] + w["hbd"] * d["hbd"]
             + w["hba"] * d["hba"] + w["tpsa"] * d["tpsa"] / 100 + w["arom"] * d["arom"])
    return {"compound": compound, "target": target, "affinity": -round(score, 2),
            "units": "kcal/mol (predicted, in silico)"}


def admet(compound: str) -> list[str]:
    """Predicted developability violations. Empty list = clean. A compound with a
    beautiful affinity and six violations is not a candidate — the filter is the point."""
    t = _assay_table()
    d = t["compounds"].get(compound)
    if not d:
        return ["unknown compound"]
    lim = t["admet_limits"]
    return [f"{k} {d[k]} > {lim[k]}" for k in ("mw", "logp", "hbd", "hba", "tpsa", "rotb")
            if d[k] > lim[k]]


def compounds() -> list[str]:
    return sorted(_assay_table()["compounds"])


# ── layer 1: citation-checked claims ──────────────────────────────────────────

# The relation algebra. A direct relation carries a sign; composing two of them along
# a shared intermediate gives a NET regulatory effect, never another direct relation —
# two hops of evidence cannot establish that A binds C. Claiming otherwise is the
# commonest way a plausible-sounding hypothesis is actually wrong, so the oracle
# checks the direction and rejects the sign error by name.
SIGN = {"inhibits": -1, "downregulates": -1, "activates": +1, "upregulates": +1}
DIRECT = ("inhibits", "activates", "binds")        # require first-hand evidence
INDIRECT = {+1: "upregulates", -1: "downregulates"}
RELATIONS = tuple(sorted(set(SIGN) | set(DIRECT)))
MAX_HOPS = 2                                       # a claim stays one step from its evidence


def _norm(claim: dict) -> tuple[str, str, str]:
    return (str(claim.get("subject", "")).strip(),
            str(claim.get("relation", "")).strip().lower(),
            str(claim.get("object", "")).strip())


def compose(r1: str, r2: str) -> str | None:
    """The net effect of A --r1--> B --r2--> C, or None if it does not compose."""
    if r1 not in SIGN or r2 not in SIGN:
        return None                                # 'binds' composes with nothing
    return INDIRECT[SIGN[r1] * SIGN[r2]]


def _support_ref(pid: str) -> str:
    """Lineage refs carry their kind: 'paper:P-001', 'PMID:42260217', 'claim:7'."""
    return pid if ":" in pid else f"paper:{pid}"


def check_claim(claim: dict, citations: list, quotes: dict | None = None) -> dict:
    """Score a hypothesis against its evidence. Pure — writes nothing — so the oracle
    can be exercised, and trusted, without a model or a database.

    A citation is one of three things, and all three are checked the same way — by
    something that is not the agent's word for it:
      ``P-001``            a structured finding in the toy corpus
      ``PMID:42260217``    a real paper in the evidence store, which must be QUOTED:
                           the quote is checked verbatim against the stored text, then
                           the quoted sentence must actually assert the claim
      ``claim:7``          a claim this organization already verified, offered as one
                           step of an inference

    verdict is one of: novel | known | unresolved-citation | contradicted |
    fabricated-quote | unquoted | not-mentioned | no-verifiable-text | sign-error |
    unsupported | malformed."""
    quotes = {str(k): v for k, v in (quotes or {}).items()}
    s, r, o = _norm(claim)
    if not s or not o or r not in RELATIONS:
        return {"ok": False, "verdict": "malformed",
                "why": f"a claim is (subject, relation, object) with relation in {RELATIONS}"}
    if not citations:
        return {"ok": False, "verdict": "unresolved-citation",
                "why": "a claim with no citation is an opinion"}

    found, lit_support, prior = [], [], {c["id"]: c for c in claims()}
    for pid in citations:
        ref = str(pid).strip()
        if L.is_citation(ref):                     # a real paper
            rec = L.store_get(ref)
            if rec is None:
                return {"ok": False, "verdict": "unresolved-citation",
                        "why": f"{ref} is not in the evidence store — it was never "
                               f"retrieved, so it cannot be cited"}
            v = L.supports(claim, rec, quotes.get(ref, ""))
            if v["verdict"] in ("contradicted", "fabricated-quote", "unquoted",
                                "not-mentioned", "no-verifiable-text"):
                return {"ok": False, "verdict": v["verdict"], "why": v["why"]}
            if v["verdict"] == "supported":
                lit_support.append((ref, v["quote"]))
            continue
        if ref.startswith("claim:"):               # a step the org already verified
            cid = int(ref.split(":", 1)[1]) if ref.split(":", 1)[1].isdigit() else -1
            c = prior.get(cid)
            if c is None:
                return {"ok": False, "verdict": "unresolved-citation",
                        "why": f"{ref} is not a verified claim of this organization"}
            found.append((ref, c["subject"], c["relation"], c["object"]))
            continue
        p = paper(ref)                             # the toy corpus
        if p is None:
            return {"ok": False, "verdict": "unresolved-citation",
                    "why": f"citation {ref} does not resolve to any paper in the corpus"}
        for f in p["findings"]:
            found.append((p["id"], f["subject"], f["relation"].lower(), f["object"]))

    # a citation that contradicts the claim kills it, even if another supports it
    for pid, fs, fr, fo in found:
        if (fs, fo) == (s, o) and fr in SIGN and r in SIGN and SIGN[fr] != SIGN[r]:
            return {"ok": False, "verdict": "contradicted",
                    "why": f"{pid} reports {fs} {fr} {fo} — the opposite direction"}

    # a real paper that says it, in a sentence the agent quoted and the store confirms
    if lit_support:
        return {"ok": True, "verdict": "known", "novel": False,
                "why": "; ".join(f"{ref} states it: \"{q[:120]}\"" for ref, q in lit_support),
                "support": [ref for ref, _ in lit_support],
                "quotes": {ref: q for ref, q in lit_support}}

    # a verbatim restatement is verified, but it is not an advance
    for pid, fs, fr, fo in found:
        if (fs, fr, fo) == (s, r, o):
            return {"ok": True, "verdict": "known", "novel": False,
                    "why": f"{pid} states this directly",
                    "support": [f"paper:{pid}"]}

    # one composed inferential step across the cited evidence
    near = None
    for p1, s1, r1, o1 in found:
        if s1 != s:
            continue
        for p2, s2, r2, o2 in found:
            if s2 != o1 or o2 != o or (p1, s1, r1, o1) == (p2, s2, r2, o2):
                continue
            net = compose(r1, r2)
            if net is None:
                continue
            if net == r:
                return {"ok": True, "verdict": "novel", "novel": True,
                        "why": f"{p1}: {s1} {r1} {o1}; {p2}: {s2} {r2} {o2} "
                               f"⇒ {s} {net} {o}",
                        "support": [_support_ref(p1), _support_ref(p2)]}
            near = (p1, s1, r1, o1, p2, s2, r2, o2, net)
    if near:
        p1, s1, r1, o1, p2, s2, r2, o2, net = near
        return {"ok": False, "verdict": "sign-error",
                "why": f"{p1}: {s1} {r1} {o1}; {p2}: {s2} {r2} {o2} supports "
                       f"'{s} {net} {o}', not '{s} {r} {o}'"
                       + (f" — {MAX_HOPS} hops of evidence cannot establish a direct "
                          f"'{r}'" if r in DIRECT else "")}
    return {"ok": False, "verdict": "unsupported",
            "why": "no cited finding, alone or composed, supports this claim"}


def submit_claim(agent: str, claim: dict, citations: list,
                 quotes: dict | None = None) -> dict:
    """Run the oracle and, if the claim survives, add it to what the org knows.
    Enforces the reproduction gate and prior art; persists nothing that failed."""
    v = check_claim(claim, citations, quotes)
    s, r, o = _norm(claim)
    v["claim"] = f"{s} {r} {o}"
    if not v["ok"]:
        return v
    c = _conn()
    try:
        # prior art first: it is a property of the claim, not of who is claiming it
        prior = c.execute("SELECT agent FROM claims WHERE subject=? AND relation=? "
                          "AND object=?", (s, r, o)).fetchone()
        if prior:
            return {"ok": False, "verdict": "already-established", "claim": v["claim"],
                    "why": f"already established by {prior[0]} — prior art"}
        if v["verdict"] == "novel" and not has_reproduced(agent):
            return {"ok": False, "verdict": "reproduction-gate", "claim": v["claim"],
                    "why": f"{agent} has not reproduced a published result yet — a novel "
                           f"claim from an unvalidated method is not trusted"}
        c.execute("INSERT INTO claims(subject, relation, object, novel, agent, "
                  "citations, support, quotes) VALUES(?,?,?,?,?,?,?,?)",
                  (s, r, o, 1 if v.get("novel") else 0, agent,
                   json.dumps(list(citations)), json.dumps(v.get("support", [])),
                   json.dumps(v.get("quotes", {}))))
        c.commit()
    finally:
        c.close()
    return v


def reset() -> dict:
    """Clear what the organization concluded — claims, reproductions, dossiers, goal.
    The evidence store and the corpus are untouched: the world is resettable, the
    literature is not (`workspace.reset_sandbox` for the settlement's training ground)."""
    c = _conn()
    try:
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("claims", "reproductions", "dossiers")}
        for t in ("claims", "reproductions", "dossiers", "goal"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
    finally:
        c.close()
    return counts


def claims(novel_only: bool = False) -> list[dict]:
    c = _conn()
    try:
        q = ("SELECT id, subject, relation, object, novel, agent, citations, support, "
             "quotes FROM claims" + (" WHERE novel=1" if novel_only else "") + " ORDER BY id")
        return [{"id": i, "claim": f"{s} {r} {o}", "subject": s, "relation": r,
                 "object": o, "novel": bool(n), "agent": a,
                 "citations": json.loads(ci), "support": json.loads(su),
                 "quotes": json.loads(qu or "{}")}
                for i, s, r, o, n, a, ci, su, qu in c.execute(q)]
    finally:
        c.close()


# ── layer 2: benchmark reproduction ───────────────────────────────────────────

TOLERANCE = 0.05


def reproduce(agent: str, compound: str, target: str, reported: float) -> dict:
    """The agent re-derives a published number from the public descriptor table. The
    oracle recomputes it and compares. Nothing an agent claims about its own method is
    taken on trust — this is the toy stand-in for rerunning a real benchmark."""
    truth = assay(compound, target)
    if truth is None:
        return {"ok": False, "why": f"no assay adapter for {compound}/{target}"}
    published = _published(compound, target)
    if published is None:
        return {"ok": False, "why": f"no published value for {compound}/{target} to "
                                    f"reproduce — pick a pair the literature reports"}
    try:
        got = float(reported)
    except (TypeError, ValueError):
        return {"ok": False, "why": f"reported value {reported!r} is not a number"}
    ok = (abs(got - truth["affinity"]) <= TOLERANCE
          and abs(published - truth["affinity"]) <= TOLERANCE)
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO reproductions(agent, compound, target, "
                  "reported, truth, ok) VALUES(?,?,?,?,?,?)",
                  (agent, compound, target, got, truth["affinity"], 1 if ok else 0))
        c.commit()
    finally:
        c.close()
    return {"ok": ok, "compound": compound, "target": target, "reported": got,
            "truth": truth["affinity"], "published": published,
            "why": "matches the published result" if ok else
                   f"reported {got}, the assay returns {truth['affinity']}"}


def _published(compound: str, target: str) -> float | None:
    for p in corpus().values():
        for a in p.get("assays", []):
            if a["compound"] == compound and a["target"] == target:
                return a["affinity"]
    return None


def has_reproduced(agent: str) -> bool:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM reproductions WHERE agent=? AND ok=1",
                         (agent,)).fetchone()[0] > 0
    finally:
        c.close()


# ── the Vision layer: which goals this org will serve at all ──────────────────

# Article I: only a human adopts a Vision, and the board may refuse to serve one.
# These are the shapes this harness refuses outright. The list is not the safety
# model — the credential-free sandbox and the human gate are (nothing here can order,
# synthesize or publish anything). It is the first of the three, and the cheapest.
HAZARD = re.compile(
    r"\b(bioweapon|biological weapon|chemical weapon|nerve agent|"
    r"select agent|weaponi[sz]|munition|"
    r"(enhance|increase|improve|maximi[sz]e)\w*\s+(the\s+)?"
    r"(virulence|lethality|transmissibility|toxicity|potency\s+of\s+a\s+toxin)|"
    r"(evade|defeat|bypass)\w*\s+(the\s+)?(vaccine|immunit|detection|screening))\b",
    re.I)

DEFAULT_GOAL = {
    "target": "KIN-X",
    "text": "find a developable candidate that engages KIN-X and explain its mechanism",
    "affinity_max": -9.0,                          # tighter than this to count
    "novel_claims_needed": 3,
}


def adopt_goal(text: str, adopted_by: str, target: str = "KIN-X",
               affinity_max: float = -9.0, novel_claims_needed: int = 3) -> tuple[bool, str]:
    """Adopt a research goal. Refused unless a human adopts it (Article I), and
    refused outright if it has the shape of hazard enhancement or weaponization."""
    if adopted_by != "human":
        return False, f"REFUSED: only a human adopts a Vision — {adopted_by} may not"
    if HAZARD.search(text or ""):
        return False, ("REFUSED: this harness serves therapeutic and beneficial "
                       "discovery under human oversight only")
    g = {"target": target, "text": text, "affinity_max": float(affinity_max),
         "novel_claims_needed": int(novel_claims_needed)}
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO goal(k, v) VALUES('goal', ?)", (json.dumps(g),))
        c.commit()
    finally:
        c.close()
    return True, f"adopted: {text}"


def goal() -> dict:
    c = _conn()
    try:
        row = c.execute("SELECT v FROM goal WHERE k='goal'").fetchone()
    finally:
        c.close()
    return json.loads(row[0]) if row else dict(DEFAULT_GOAL)


# ── the world: acceptance criteria, scored ────────────────────────────────────

def candidates() -> list[dict]:
    """Compounds that clear every computed bar AND have a verified claim tying them to
    the goal's target. Affinity alone is not a candidate — that is the whole lesson of
    the compound with the best score and six developability violations."""
    g = goal()
    linked = {cl["subject"] for cl in claims()
              if cl["object"] == g["target"] and cl["relation"] in SIGN}
    out = []
    for cmp_ in compounds():
        a = assay(cmp_, g["target"])
        if a is None:
            continue
        viol = admet(cmp_)
        out.append({"compound": cmp_, "affinity": a["affinity"], "admet": viol,
                    "linked": cmp_ in linked,
                    "eligible": a["affinity"] <= g["affinity_max"] and not viol
                                and cmp_ in linked})
    return sorted(out, key=lambda c: c["affinity"])


def criteria() -> list[dict]:
    """The goal's acceptance criteria — progress is how many are met, never a claim."""
    g = goal()
    reps = _rep_count()
    novel = [c for c in claims() if c["novel"]]
    elig = [c for c in candidates() if c["eligible"]]
    all_parked = dossiers()
    pending = [d for d in all_parked if d["status"] == "pending"]
    return [
        {"name": "reproduction", "met": reps > 0,
         "detail": f"{reps} published result(s) re-derived"},
        {"name": "mechanism", "met": len(novel) >= g["novel_claims_needed"],
         "detail": f"{len(novel)}/{g['novel_claims_needed']} verified novel claims"},
        {"name": "candidate", "met": bool(elig),
         "detail": ", ".join(c["compound"] for c in elig) or
                   f"none clears affinity ≤ {g['affinity_max']} + clean ADMET + a "
                   f"verified link to {g['target']}"},
        # reaching the gate is the criterion; what the human then decides is theirs,
        # so a reviewed dossier does not un-do the organization's work
        {"name": "dossier reached the gate", "met": bool(all_parked),
         "detail": f"{len(all_parked)} packaged, {len(pending)} awaiting a human"},
    ]


def _rep_count() -> int:
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM reproductions WHERE ok=1").fetchone()[0]
    finally:
        c.close()


def world() -> dict:
    """The research world's state — the same shape of truth the other worlds report."""
    crit = criteria()
    met = sum(1 for c in crit if c["met"])
    return {"goal": goal()["text"], "target": goal()["target"],
            "claims_verified": len(claims()), "claims_novel": len(claims(novel_only=True)),
            "reproductions": _rep_count(), "papers": len(corpus()),
            "evidence_records": len(L.store_ids()),
            "candidates": sum(1 for c in candidates() if c["eligible"]),
            "dossiers_pending": len(dossiers("pending")),
            "criteria_met": f"{met}/{len(crit)}",
            "progress_pct": round(100 * met / len(crit)) if crit else 0}


# ── the gate: everything physical or public stops at a human ──────────────────

# Article V, literally: these are not permissions the sandbox holds and declines to
# use — there is no procurement credential, no lab actuator and no route to an
# ordering system in this process. attempt() exists so the refusal is testable.
IRREVERSIBLE = ("order", "purchase", "synthesize", "synthesise", "wet-lab", "protocol",
                "contact-lab", "cro", "publish", "preprint", "patent", "file-ip",
                "export-dossier")


def attempt(action: str, detail: str = "") -> tuple[bool, str]:
    """Every physical or public act. Always refused — the capability does not exist."""
    a = str(action).strip().lower()
    if any(k in a for k in IRREVERSIBLE):
        return False, (f"REFUSED: '{action}' is irreversible and physical or public. "
                       f"This sandbox holds no procurement credentials, no lab "
                       f"actuators and no ordering network — park a dossier for a "
                       f"human instead (Article IV/V)")
    return False, (f"REFUSED: '{action}' is not a capability of the research sandbox. "
                   f"It reads, computes and writes notes; nothing else exists")


def park_dossier(agent: str, compound: str) -> dict:
    """Package a candidate that cleared the oracle — claim, evidence chain, computed
    scores, limitations — and park it at the human gate. This is the end of the
    organization's authority: it never publishes, never orders, never proceeds."""
    g = goal()
    cand = next((c for c in candidates() if c["compound"] == compound), None)
    if cand is None:
        return {"ok": False, "why": f"{compound} has no assay in this adapter"}
    if not cand["eligible"]:
        why = []                                   # name every bar it failed, not just one
        if cand["affinity"] > g["affinity_max"]:
            why.append(f"affinity {cand['affinity']} does not clear {g['affinity_max']}")
        if cand["admet"]:
            why.append(f"ADMET violations: {', '.join(cand['admet'])}")
        if not cand["linked"]:
            why.append(f"no verified claim links it to {g['target']}")
        return {"ok": False, "why": f"{compound} is not a candidate — {'; '.join(why)}"}
    chain = [c for c in claims()
             if c["subject"] == compound or c["object"] == compound
             or c["object"] == g["target"]]
    body = {
        "candidate": compound,
        "goal": g["text"],
        "computed": assay(compound, g["target"]),
        "admet_violations": cand["admet"],
        "evidence_chain": [{"claim": c["claim"], "novel": c["novel"],
                            "citations": c["citations"], "support": c["support"],
                            "quotes": c.get("quotes", {}), "by": c["agent"]}
                           for c in chain],
        "limitations": [
            "every score is predicted in silico; nothing here was measured",
            "no wet-lab validation, no cell assay, no animal or human data",
            "the corpus is a toy: the mechanism is inferred from a handful of papers",
            "indirect claims are one composed step from their evidence, not causation",
        ],
        "requires": "a human domain expert to review before ANY physical or public act",
    }
    c = _conn()
    try:
        cur = c.execute("INSERT INTO dossiers(compound, agent, body) VALUES(?,?,?)",
                        (compound, agent, json.dumps(body)))
        c.commit()
        did = cur.lastrowid
    finally:
        c.close()
    return {"ok": True, "dossier": did, "compound": compound, "body": body,
            "status": "pending", "why": "parked at the human gate — nothing proceeds"}


def dossiers(status: str = "") -> list[dict]:
    c = _conn()
    try:
        q = "SELECT id, compound, agent, body, status, decided_by, note FROM dossiers"
        args = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        return [{"id": i, "compound": cm, "agent": a, "body": json.loads(b),
                 "status": st, "decided_by": d, "note": n}
                for i, cm, a, b, st, d, n in c.execute(q + " ORDER BY id", args)]
    finally:
        c.close()


def dossier_decide(dossier_id: int, decision: str, approver: str,
                   note: str = "") -> tuple[bool, str]:
    """Only a human decides at the gate. An approved dossier is a human's decision to
    act recorded — it still authorizes no action inside this process."""
    if approver != "human":
        return False, (f"REFUSED: {approver} may not decide at the gate — the gate is "
                       f"a human (Article IV)")
    if decision not in ("approve", "refuse"):
        return False, "decision must be 'approve' or 'refuse'"
    c = _conn()
    try:
        cur = c.execute("UPDATE dossiers SET status=?, decided_by=?, note=? "
                        "WHERE id=? AND status='pending'",
                        ("approved" if decision == "approve" else "refused",
                         approver, note, dossier_id))
        c.commit()
        if not cur.rowcount:
            return False, f"dossier {dossier_id} is not pending"
    finally:
        c.close()
    return True, f"dossier {dossier_id} {decision}d by {approver}"


# ── the only write path an agent has ──────────────────────────────────────────

def write_note(rel_path: str, content: str) -> tuple[bool, str]:
    """Write a working note inside sandbox/lab/. Refuses anything outside the sandbox
    and refuses the corpus outright: an agent cannot cite evidence it wrote itself."""
    try:
        p = _safe_path(rel_path)
    except ValueError as e:
        return False, str(e)
    rel = os.path.relpath(p, SANDBOX)
    if rel.split(os.sep)[0] == CORPUS_DIR:
        return False, ("REFUSED: the literature is not writable — an agent may not "
                       "cite evidence it wrote itself")
    if rel.split(os.sep)[0] != LAB_DIR:
        return False, f"REFUSED: notes belong in {LAB_DIR}/, not {rel}"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    return True, f"wrote {rel} ({len(content)} bytes)"


def _safe_path(rel_path: str) -> str:
    p = os.path.realpath(os.path.join(SANDBOX, rel_path))
    if not p.startswith(os.path.realpath(SANDBOX) + os.sep):
        raise ValueError(f"REFUSED: {rel_path} escapes the sandbox")
    return p


if __name__ == "__main__":
    init()
    print(json.dumps({"world": world(), "criteria": criteria(),
                      "candidates": candidates()}, indent=2))

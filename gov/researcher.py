"""The researcher agent — a villager whose resource is verified knowledge (RESEARCH.md §6).

One research cycle, the exact shape of the worker's gather turn:

    read the goal and what is already known → propose a hypothesis WITH its citations
    → the oracle scores it → measured contribution = verified novel advance

An agent that has not yet re-derived a published result reproduces one first; until it
has, its novel claims are refused rather than discounted (the reproduction gate). What
the agent *claims* is worth nothing: contribution is what ``research_world`` verified —
citations that resolve, evidence that actually supports the claim, and a direction of
effect the composed evidence bears out. Rejected hypotheses are recorded as waste, in
the agent's career, forever; that is how a research org learns who is rigorous.

When the goal's computational criteria are met, the cycle packages the candidate as a
dossier and parks it at the human gate. It never publishes, never orders, never
proceeds — ``research_world.attempt()`` refuses every physical or public act because
the sandbox holds no credential to perform one (Article IV/V).

Run one cycle by hand:  python3 gov/researcher.py --agent res-01
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anchor
import brain
import economy
import literature as L
import research_world as R

CREDIT_PER_NOVEL_CLAIM = 150     # a verified advance nobody had stated
CREDIT_PER_REPRODUCTION = 100    # re-deriving a known result is real work
CREDIT_PER_DOSSIER = 300         # packaging a candidate for the human gate
KNOWN_CLAIM_CREDIT = 0           # a restatement is verified, but it is not an advance


def research_cycle(agent: str) -> dict:
    """One full cycle for one agent, measured by the oracle."""
    R.init()
    if not R.has_reproduced(agent):
        return reproduction_cycle(agent)
    known = R.claims()
    prompt = (
        f"You are {agent}, a research agent in a governed organization.\n"
        f"GOAL: {R.goal()['text']} (target: {R.goal()['target']})\n\n"
        f"THE TOY CORPUS (structured findings; cite by id):\n"
        f"{json.dumps(R.corpus_index(), indent=1)}\n\n"
        f"THE REAL LITERATURE (cite by PMID, and you MUST quote the sentence):\n"
        f"{json.dumps(_evidence_brief(), indent=1)}\n\n"
        f"ALREADY ESTABLISHED (claiming one of these again earns nothing, but you may\n"
        f"cite one as 'claim:<id>' to build an inference on it):\n"
        + ("\n".join(f"- claim:{c['id']} {c['claim']}" for c in known) or "- nothing yet")
        + "\n\n"
        f"Propose ONE new claim. A claim is a triple. Direct relations "
        f"({', '.join(R.DIRECT)}) need evidence that reports them first-hand. A claim\n"
        f"you infer by composing TWO steps through a shared intermediate is an INDIRECT\n"
        f"net effect: use 'upregulates' or 'downregulates', and get the direction right\n"
        f"— inhibiting an activator downregulates what it activated.\n\n"
        f"For every PMID you cite, quote the EXACT sentence from that abstract that says\n"
        f"it. The quote is checked verbatim against the stored text: a sentence that is\n"
        f"not in the paper is caught, and the claim is rejected as fabricated.\n\n"
        f'Reply with ONLY JSON: {{"subject": "...", "relation": "...", "object": "...", '
        f'"citations": ["PMID:123", "claim:4"], "quotes": {{"PMID:123": "the exact '
        f'sentence"}}, "why": "one sentence"}}'
    )
    try:
        raw = brain._chat([{"role": "user", "content": prompt}], 800, 0.4, "researcher-hypothesis")
    except Exception as e:
        anchor.record(-1, "error", f"{agent} could not form a hypothesis: {str(e)[:120]}")
        return {"agent": agent, "did": "error", "reason": str(e)[:200]}
    h = _parse_json(raw)
    if not h:
        anchor.record(-1, "waste", f"{agent} returned an unreadable hypothesis")
        return {"agent": agent, "did": "malformed", "raw": raw[:200]}
    return submit_and_score(agent, h, h.get("citations", []), h.get("why", ""),
                            h.get("quotes", {}))


def _evidence_brief(limit: int = 12) -> list[dict]:
    """What the agent may read of the real literature: the stored text, nothing else.
    It cannot fetch — retrieval is the human's ingest step, never an agent's cycle."""
    out = []
    for cid in L.store_ids()[:limit]:
        rec = L.store_get(cid)
        out.append({"id": cid, "year": rec["year"], "title": rec["title"],
                    "abstract": rec["abstract"] or "(not open access — no text to quote)"})
    return out


def reproduction_cycle(agent: str) -> dict:
    """The gate every agent passes before its novel claims count: re-derive a number
    the literature published, from the public descriptor table.

    The agent chooses which published pair to reproduce; the harness runs the assay
    adapter and the oracle compares its output against what the paper reported. (In a
    real deployment the agent invokes the adapter as a tool and the gate compares the
    two outputs — the choice of what to reproduce is the judgement being tested.)"""
    published = [{"paper": p["id"], "compound": a["compound"], "target": a["target"]}
                 for p in R.corpus().values() for a in p.get("assays", [])]
    if not published:
        return {"agent": agent, "did": "nothing", "reason": "no published result to reproduce"}
    pick = published[0]
    if brain.available():
        prompt = (f"You are {agent}. Before your novel claims are trusted you must "
                  f"reproduce one published result.\nPublished assay results:\n"
                  f"{json.dumps(published, indent=1)}\n\n"
                  f'Choose one. Reply with ONLY JSON: {{"compound": "...", "target": "..."}}')
        try:
            chosen = _parse_json(brain._chat([{"role": "user", "content": prompt}],
                                             200, 0.2, "researcher-reproduce"))
            if chosen and any(chosen.get("compound") == p["compound"]
                              and chosen.get("target") == p["target"] for p in published):
                pick = chosen
        except Exception:
            pass                                   # a model that will not answer takes the default
    computed = R.assay(pick["compound"], pick["target"])
    return reproduce_and_score(agent, pick["compound"], pick["target"],
                               computed["affinity"] if computed else None)


# ── the seams: scored by the oracle, testable with no model at all ────────────

def reproduce_and_score(agent: str, compound: str, target: str, value) -> dict:
    v = R.reproduce(agent, compound, target, value)
    pair = f"{compound}/{target}"
    if not v["ok"]:
        anchor.record(-1, "waste", f"{agent} failed to reproduce {pair} — {v['why']}")
        anchor.career_add(agent, -1, "retask", f"reproduction failed on {pair}: {v['why']}")
        return {"agent": agent, "did": "reproduction-failed", "reason": v["why"]}
    economy.ensure(agent)
    economy.credit(agent, CREDIT_PER_REPRODUCTION)
    did = anchor.reason_add(-1, agent, f"reproduce {pair}",
                            f"re-derived the published affinity {v['published']} from the "
                            f"public descriptor table before claiming anything novel",
                            derived_from=[f"assay:{pair}"], authorized_by="policy")
    ev = anchor.record(-1, "work", f"{agent} reproduced {pair}: {v['truth']} "
                                   f"(published {v['published']})")
    anchor.decision_close(did, ev, outcome=f"+{CREDIT_PER_REPRODUCTION} contribution; "
                                           f"novel claims now trusted")
    anchor.career_add(agent, -1, "work", f"reproduced {pair} — method validated")
    return {"agent": agent, "did": "reproduced", "pair": pair, "value": v["truth"],
            "contribution": CREDIT_PER_REPRODUCTION, "world": R.world()}


def submit_and_score(agent: str, claim: dict, citations: list, why: str = "",
                     quotes: dict | None = None) -> dict:
    """Submit one hypothesis and take whatever the oracle says. Split out so the whole
    machinery is exercisable without a model — verify injects known claims."""
    v = R.submit_claim(agent, claim, citations, quotes)
    text = v.get("claim", "")
    if not v["ok"]:
        anchor.record(-1, "waste", f"{agent} hypothesis rejected ({v['verdict']}): "
                                   f"{text} — {v['why'][:160]}")
        anchor.career_add(agent, -1, "retask", f"{v['verdict']}: {text}")
        return {"agent": agent, "did": "rejected", "verdict": v["verdict"],
                "claim": text, "reason": v["why"]}

    novel = bool(v.get("novel"))
    got = CREDIT_PER_NOVEL_CLAIM if novel else KNOWN_CLAIM_CREDIT
    economy.ensure(agent)
    if got:
        economy.credit(agent, got)
    did = anchor.reason_add(-1, agent, f"claim: {text}",
                            (why or v["why"])[:480],
                            derived_from=v.get("support", []) +
                                         [f"paper:{p}" for p in citations],
                            authorized_by="policy")
    ev = anchor.record(-1, "work" if novel else "knowledge",
                       f"{agent} established {'a novel claim' if novel else 'a known result'}: "
                       f"{text} [{', '.join(str(c) for c in citations)}]")
    anchor.decision_close(did, ev, outcome=f"oracle: {v['verdict']}; +{got} contribution")
    anchor.career_add(agent, -1, "work",
                      f"{v['verdict']} claim: {text} (+{got} contribution)")
    promo = economy.evaluate(agent)
    if promo:
        anchor.record(-1, "promote", f"{agent} promoted -> {promo}")
        anchor.career_add(agent, -1, "promote", f"earned promotion to {promo} by verified science")
    out = {"agent": agent, "did": "verified", "verdict": v["verdict"], "claim": text,
           "novel": novel, "contribution": got, "promoted": promo, "world": R.world()}
    parked = maybe_park(agent)
    if parked:
        out["dossier"] = parked
    return out


def maybe_park(agent: str) -> dict | None:
    """If the computational criteria are met, package the candidate and stop at the
    gate. The organization's authority ends here, by construction."""
    met = {c["name"]: c["met"] for c in R.criteria()}
    if not (met["reproduction"] and met["mechanism"] and met["candidate"]):
        return None
    cand = next(c for c in R.candidates() if c["eligible"])
    if any(d["compound"] == cand["compound"] for d in R.dossiers()):
        return None                                # already at the gate; it is the human's now
    d = R.park_dossier(agent, cand["compound"])
    if not d["ok"]:
        return None
    economy.credit(agent, CREDIT_PER_DOSSIER)
    did = anchor.reason_add(-1, agent, f"park dossier: {cand['compound']}",
                            "every computational criterion is met; the next step is "
                            "physical or public, so it stops at a human",
                            derived_from=[f"claim:{c['claim']}"
                                          for c in R.claims(novel_only=True)][:6],
                            authorized_by="policy")
    ev = anchor.record(-1, "gate", f"{agent} parked dossier #{d['dossier']} for "
                                   f"{cand['compound']} — awaiting a human")
    anchor.decision_close(did, ev, outcome="parked at the gate; nothing proceeds")
    anchor.career_add(agent, -1, "work", f"packaged candidate {cand['compound']} for the gate")
    return {"id": d["dossier"], "compound": cand["compound"], "status": "pending"}


def _parse_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="res-01")
    ap.add_argument("--cycles", type=int, default=1)
    a = ap.parse_args()
    anchor.init()
    economy.init()
    R.init()
    if not brain.available():
        print("no brain configured (BRAIN_* / DEEPSEEK_API_KEY) — the researcher needs a model")
        sys.exit(1)
    for _ in range(a.cycles):
        print(json.dumps(research_cycle(a.agent), indent=2))

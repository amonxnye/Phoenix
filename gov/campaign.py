"""The campaign engine — an organization that works a problem until it has an answer.

This is the settlement's structure with the game thrown away. A campaign is pointed at
a problem with a **done condition an oracle can check**, and it keeps going until one
of three things is true: the problem is settled, the budget is spent, or it can name
the specific thing blocking it and hand that to a human. It is never allowed to stop
quietly, and it is never allowed to declare victory on its own say-so (Article IX).

**Getting more creative is structural, not a mood.** The engine climbs a ladder:

    0 recompute            redo the arithmetic that is already in hand      free, certain
    1 search-store         look through evidence already retrieved          free, certain
    2 widen-query          go and get literature phrased a way not yet tried  costs, reversible
    3 declare-equivalence  treat two names as the same thing                judgement, capped
    4 weaken-claim         test the weaker claim the evidence could settle  reframing, capped
    5 escalate             name the blocker, give it to a human             the end of the road

A round that produces something new **drops back down** to the cheap rungs, because the
cheap checks now have more to work with. A round that produces nothing climbs. So the
campaign only gets more inventive exactly when being ordinary has stopped working —
and its inventiveness is a named, logged, reviewable move rather than a longer prompt.

**Creativity that cannot destroy value.** The upper rungs are where a system starts
fooling itself, so they are fenced:

- Findings are **append-only**. A later round may contradict an earlier one; both stay.
  Nothing is deleted to make the story tidy.
- Equivalences can only be declared from a candidate list a **human supplied**, one per
  round, and every finding records which equivalence made the match. The engine may
  use a judgement; it may not invent one.
- Weakening a claim never edits or replaces the original — the weaker claim is a new
  claim under test, labelled as weakened, and the strong one stays unsettled.
- The goalposts cannot move: once a claim has findings, it is frozen.
- Every irreversible act — publishing, contacting authors — stays behind the human gate
  no matter how the campaign ends.

**Nothing runs unseen (Article VII), ported from the settlement.** Every round that
produces something is written to the permanent event log, and the two judgement rungs
each open a first-class decision — `reason_add` naming what was decided and why,
`decision_close` linking it to what it produced — exactly the audit trail
`researcher.py` keeps for a claim. An equivalence is `authorized_by="human"` because it
came from the spec's candidate list, never invented; a weakened claim is
`authorized_by="policy"` because the *move* is the engine's, even though it can only
weaken toward a form a human's equivalence already implies.

**Skills learnt, ported from the settlement's retrospective.** At the end of a run the
campaign distills what it learned into durable lessons — recomputation alone refuted a
claim, an equivalence did or didn't surface anything, a claim was corroborated only at
title/introduction grade — and records them with `anchor.skill_add`. The same dedup and
expiry rules apply as everywhere else in the anchor (Article VI.4): re-running the same
campaign teaches nothing new, and a lesson only sticks around while it keeps being
re-confirmed.

Run:  python3 gov/campaign.py --spec sandbox/campaigns/PMID-41964971.json --fresh
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import anchor
import literature as L
import replication as REP

LADDER = [
    ("recompute", "redo the arithmetic already in hand"),
    ("search-store", "look through the evidence already retrieved"),
    ("widen-query", "retrieve literature phrased a way not yet tried"),
    ("declare-equivalence", "treat two names as the same thing — a human's candidate"),
    ("weaken-claim", "test the weaker claim the evidence could still settle"),
    ("escalate", "name the blocker and hand it to a human"),
]
ESCALATE = len(LADDER) - 1
MAX_ROUNDS = 24
MAX_INGESTS = 6                                    # the only rung that costs anything


class Campaign:
    """One problem, worked until it is settled, escalated, or out of budget."""

    def __init__(self, spec: dict, offline: bool = False, max_rounds: int = MAX_ROUNDS,
                 max_ingests: int = MAX_INGESTS, log=print):
        self.spec = spec
        self.target = spec["target"]
        self.offline = offline
        self.max_rounds = max_rounds
        self.max_ingests = max_ingests
        self.log = log
        self.rung = 0
        self.round = 0
        self.ingests = 0
        self.used_queries: set[str] = set()
        self.declared: list[str] = []
        self.weakened: list[str] = []
        self.history: list[dict] = []

    # ── the moves ────────────────────────────────────────────────────────────

    def _recompute(self) -> list[str]:
        done = {r["label"] for r in REP.recomputations(self.target)}
        out = []
        for spec in self.spec.get("recomputations", []):
            if spec["label"] in done:
                continue
            r = REP.recompute(self.target, spec["label"], spec["kind"],
                              spec.get("args", {}), spec["reported"])
            out.append(f"{spec['label']}: {r['verdict']} — {r['why'][:96]}")
        return out

    def _search_store(self) -> list[str]:
        known = {(f["claim_id"], f["source"], f["verdict"]) for f in REP.findings(self.target)}
        out = []
        for cl in REP.claims(self.target):
            for f in REP.independent(self.target, cl["id"], self.round):
                key = (cl["id"], f["source"], f["verdict"])
                if key in known:
                    continue
                out.append(f"{f['verdict']} [{f['grade']}] {f['source']} for "
                           f"'{cl['claim']}' via {f['alias']}")
        return out

    def _widen_query(self) -> list[str]:
        if self.offline or self.ingests >= self.max_ingests:
            return []
        for q in self.spec.get("queries", []):
            if q in self.used_queries:
                continue
            self.used_queries.add(q)
            self.ingests += 1
            try:
                recs = L.search(q, self.spec.get("per_query", 8))
            except Exception as e:                 # the network is allowed to fail
                return [f"query failed ({str(e)[:60]}) — the oracle is unaffected"]
            new = []
            for r in recs:
                if r.get("abstract") is None or L.store_get(r["id"]):
                    continue
                L.store_put(r)
                new.append(r["id"])
            return [f'query "{q[:56]}" retrieved {len(new)} new paper(s)'] if new else []
        return []

    @staticmethod
    def _already_declared(entity: str, names: list[str]) -> bool:
        """Is this equivalence already true in the world? `self.declared` is this
        instance's memory of what IT did this run; the alias table is what actually
        happened, and it outlives any one Campaign object. A fresh instance pointed at
        a target a PRIOR run already worked must see the persisted state, or it redoes
        — and re-logs, re-teaches — a rung that has nothing left to do."""
        existing = {a.lower() for a in L.aliases(entity)}
        return all(n.lower() in existing for n in names)

    def _declare_equivalence(self) -> list[str]:
        for eq in self.spec.get("equivalences", []):
            key = eq["entity"]
            if key in self.declared:
                continue
            if self._already_declared(key, eq["names"]):
                self.declared.append(key)          # true in the world — catch up, say nothing
                continue
            self.declared.append(key)
            L.aliases_put(key, eq["names"])
            return [f"declared: {', '.join(eq['names'][:3])} counted as '{key}' "
                    f"(a human's candidate, recorded on every finding it makes)"]
        return []

    def _weaken_claim(self) -> list[str]:
        """Test the broader form of a claim: the alias group's head name is the general
        thing the specific one belongs to. The strong claim stays open — this adds a
        question, it does not answer the original one."""
        for cl in REP.claims(self.target):
            broad_s, broad_o = L.aliases(cl["subject"])[0], L.aliases(cl["object"])[0]
            if (broad_s, broad_o) == (cl["subject"], cl["object"]):
                continue
            key = f"{broad_s} {cl['relation']} {broad_o}"
            if key in self.weakened:
                continue
            if any(c["claim"] == key for c in REP.claims(self.target)):
                self.weakened.append(key)          # already under test — catch up, say nothing
                continue
            self.weakened.append(key)
            r = REP.put_claim(self.target, {"subject": broad_s, "relation": cl["relation"],
                                            "object": broad_o}, cl["quote"])
            if r["ok"]:
                return [f"weaker claim under test: '{key}' (the strong form "
                        f"'{cl['claim']}' stays open)"]
        return []

    # ── what is still worth trying ───────────────────────────────────────────
    # A rung whose work is finished forever (every statistic recomputed, every
    # equivalence declared) is skipped rather than burned as a round. The cheap search
    # rungs never retire: new evidence makes them live again, which is the whole reason
    # a productive round drops back down.

    def _can_recompute(self) -> bool:
        done = {r["label"] for r in REP.recomputations(self.target)}
        return any(s["label"] not in done for s in self.spec.get("recomputations", []))

    def _can_search(self) -> bool:
        return bool(REP.claims(self.target))

    def _can_widen(self) -> bool:
        return (not self.offline and self.ingests < self.max_ingests
                and any(q not in self.used_queries for q in self.spec.get("queries", [])))

    def _can_declare(self) -> bool:
        return any(e["entity"] not in self.declared
                  and not self._already_declared(e["entity"], e["names"])
                  for e in self.spec.get("equivalences", []))

    def _can_weaken(self) -> bool:
        existing = {c["claim"] for c in REP.claims(self.target)}
        for cl in REP.claims(self.target):
            broad = f"{L.aliases(cl['subject'])[0]} {cl['relation']} {L.aliases(cl['object'])[0]}"
            if broad != cl["claim"] and broad not in self.weakened and broad not in existing:
                return True
        return False

    MOVES = {"recompute": _recompute, "search-store": _search_store,
             "widen-query": _widen_query, "declare-equivalence": _declare_equivalence,
             "weaken-claim": _weaken_claim}
    AVAILABLE = {"recompute": _can_recompute, "search-store": _can_search,
                 "widen-query": _can_widen, "declare-equivalence": _can_declare,
                 "weaken-claim": _can_weaken}

    def _next_rung(self, start: int) -> int:
        for i in range(start, ESCALATE):
            if self.AVAILABLE[LADDER[i][0]](self):
                return i
        return ESCALATE

    # ── the audit trail — best-effort, never load-bearing for the campaign itself ──

    def _note(self, kind: str, msg: str) -> int | None:
        """Append to the permanent event log. A telemetry failure must never break the
        campaign that produced it — this can fail silently, the campaign cannot."""
        try:
            return anchor.record(-1, kind, msg[:500])
        except Exception:
            return None

    def _lineage(self, decision: str, why: str, authorized_by: str,
                 event: int | None, outcome: str) -> None:
        """Open and close a first-class decision for a judgement rung — the same shape
        `researcher.py` keeps for a claim, so an equivalence or a weakened claim is
        traceable back to what authorized it and what it produced."""
        try:
            did = anchor.reason_add(-1, f"campaign:{self.target}", decision[:240], why[:500],
                                    derived_from=[f"target:{self.target}"],
                                    authorized_by=authorized_by, effect_event=event)
            anchor.decision_close(did, event, outcome=outcome[:240])
        except Exception:
            pass

    # ── the loop ─────────────────────────────────────────────────────────────

    def run(self) -> dict:
        REP.init()
        started = time.time()
        while self.round < self.max_rounds:
            self.round += 1
            v = REP.verdict(self.target)
            if v["settled"]:
                return self._finish("settled", v, started)
            self.rung = self._next_rung(self.rung)
            name, _ = LADDER[self.rung]
            if name == "escalate":
                return self._finish("escalated", v, started)
            produced = self.MOVES[name](self)
            self.history.append({"round": self.round, "move": name,
                                 "produced": produced})
            for line in produced:
                self.log(f"   r{self.round} {name:<19} {line}")
                ev = self._note("work", f"campaign:{self.target} r{self.round} "
                                        f"[{name}] {line}")
                if name == "declare-equivalence":
                    self._lineage(f"declare equivalence (round {self.round})",
                                 "a candidate from the human-supplied list, not invented "
                                 f"by the engine — {line}", "human", ev,
                                 outcome=f"round {self.round}: {line}")
                elif name == "weaken-claim":
                    self._lineage(f"weaken claim (round {self.round})",
                                 "the strong form stays open and unsettled; this only "
                                 f"adds a question the evidence in hand could answer — {line}",
                                 "policy", ev, outcome=f"round {self.round}: {line}")
            if produced:
                self.rung = self._next_rung(0)     # cheap checks have new material
            else:
                self.log(f"   r{self.round} {name:<19} {'(nothing new)':<40} → climbing")
                self.rung += 1
        return self._finish("exhausted", REP.verdict(self.target), started)

    # ── skills learnt — the settlement's retrospective, pointed at replication ────

    def _learn(self, v: dict) -> list[str]:
        """Distill durable, reusable lessons from this run and record them. Dedup and
        expiry are the anchor's job (Article VI.4) — this only has to phrase a lesson
        the same way each time the same situation recurs, so identical re-runs teach
        nothing new and a genuinely different outcome does."""
        lessons = []                                # (text, trigger) pairs

        impossible = [r for r in v["recomputations"] if r["verdict"] == "impossible"]
        if impossible:
            r = impossible[0]
            lessons.append((
                f"Recomputing a paper's own arithmetic can refute it outright before any "
                f"literature search: '{r['label']}' reported {r['reported']}, but the "
                f"paper's own sample size cannot produce it. Always recompute first — "
                f"it is free, certain, and can end the campaign on round one.",
                "arithmetic-can-refute"))

        divergent = [r for r in v["recomputations"] if r["verdict"] == "divergent"]
        if divergent:
            labels = ", ".join(r["label"] for r in divergent[:3])
            lessons.append((
                f"A reported statistic can diverge from its own recomputation without "
                f"being impossible ({labels}) — worth flagging in the critique even when "
                f"it doesn't change the verdict, since it means the point estimate and "
                f"the interval didn't come from the same model.",
                "divergent-not-impossible"))

        for cl in v["claims"]:
            if cl["state"] == "weakly-corroborated":
                lessons.append((
                    f"'{cl['claim']}' was corroborated only in a title or introduction, "
                    f"never a result — abstracts mostly restate prior work rather than "
                    f"report it. Full-text retrieval is the next lever, not a wider "
                    f"abstract search.",
                    "weak-grade-ceiling"))
            elif cl["state"] == "contradicted":
                lessons.append((
                    f"'{cl['claim']}' was contradicted by independent, result-grade "
                    f"evidence — exactly the failure mode the independence check exists "
                    f"to catch before a critique is written.",
                    "independence-caught-a-contradiction"))

        for i, h in enumerate(self.history):
            if h["move"] != "declare-equivalence" or not h["produced"]:
                continue
            line = h["produced"][0]
            entity = line.split("counted as '")[1].split("'")[0] if "counted as '" in line else "?"
            nxt = next((x for x in self.history[i + 1:] if x["move"] == "search-store"), None)
            if nxt and nxt["produced"]:
                lessons.append((
                    f"Broadening '{entity}' to its synonym group surfaced independent "
                    f"evidence a narrower search missed — try wider synonym groups before "
                    f"reaching for a new query.",
                    "equivalence-helped"))
            elif nxt:
                lessons.append((
                    f"Broadening '{entity}' to its synonym group did not surface anything "
                    f"new — the literature in hand may simply not use this framing.",
                    "equivalence-was-neutral"))

        if v["verdict"] == "corroborated":
            lessons.append((
                f"A replication of a paper shaped like '{self.target}' reached full "
                f"independent, result-grade corroboration — a template for what "
                f"'settled' looks like for this claim shape.",
                "settled-corroborated"))

        learnt = []
        for text, trigger in lessons:
            # Truncate to the exact same bound anchor.skill_add applies before storing,
            # so what this returns is always what actually landed — never a string that
            # LOOKS learnt here but reads back shorter (or not at all) from skills_top.
            text = text.strip()[:anchor.SKILL_LESSON_MAX]
            try:
                sid = anchor.skill_add(self.round, text, source=f"campaign:{self.target}",
                                       trigger=trigger)
                if sid:
                    learnt.append(text)
            except Exception:
                pass
        return learnt

    def _finish(self, how: str, v: dict, started: float) -> dict:
        out = {"how": how, "verdict": v["verdict"], "why": v["why"],
               "rounds": self.round, "ingests": self.ingests,
               "blockers": v["blockers"], "claims": v["claims"],
               "declared_equivalences": self.declared, "weakened": self.weakened,
               "seconds": round(time.time() - started, 1), "history": self.history}
        try:
            anchor.record(-1, "work" if how == "settled" else "escalate",
                          f"campaign on {self.target} {how}: {v['verdict']} — "
                          f"{v['why'][:150]}")
        except Exception:
            pass                                   # the campaign result is not the anchor's hostage
        out["skills_learnt"] = self._learn(v)
        return out


def load_spec(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def prepare(spec: dict) -> list[str]:
    """Adopt the target and put the paper's own claims under test. Each claim must be
    quote-verified against the paper before the campaign starts — a campaign against a
    claim the authors never made is worse than no campaign."""
    out = []
    ok, msg = REP.adopt_target(spec["target"], adopted_by="human")
    out.append(f"target: {msg}")
    if not ok:
        return out
    for c in spec.get("claims", []):
        r = REP.put_claim(spec["target"], {k: c[k] for k in ("subject", "relation", "object")},
                          c["quote"])
        out.append(f"claim {'accepted' if r['ok'] else 'REFUSED'}: "
                   f"{c['subject']} {c['relation']} {c['object']}"
                   f"{'' if r['ok'] else ' — ' + r['why'][:90]}"
                   f"{' [' + r.get('section', '') + ']' if r['ok'] else ''}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--offline", action="store_true", help="never retrieve; use what is held")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    a = ap.parse_args()
    anchor.init()
    REP.init()
    if a.fresh:
        REP.reset()
    spec = load_spec(a.spec)
    print(f"\n\033[1mCampaign: replicate {spec['target']}\033[0m")
    for line in prepare(spec):
        print("   " + line)
    print("\n\033[1mRounds\033[0m")
    c = Campaign(spec, offline=a.offline, max_rounds=a.max_rounds)
    res = c.run()
    print(f"\n\033[1mVerdict: {res['verdict'].upper()}\033[0m  ({res['how']} after "
          f"{res['rounds']} rounds, {res['ingests']} retrievals, {res['seconds']}s)")
    print(f"   {res['why']}")
    for cl in res["claims"]:
        print(f"   [{cl['state']:<20}] {cl['claim']}"
              f"  (for {cl['for']}, against {cl['against']}, weak {cl['weak']})")
    if res["declared_equivalences"]:
        print(f"   equivalences used: {', '.join(res['declared_equivalences'])}")
    for b in res["blockers"]:
        print(f"   \033[33mopen:\033[0m {b}")
    if res["skills_learnt"]:
        print("\n\033[1mSkills learnt\033[0m")
        for s in res["skills_learnt"]:
            print(f"   • {s}")
    d = REP.park_critique(spec["target"])
    print(f"\n   critique #{d['critique']} parked for a human — "
          f"{len(d['body']['limitations'])} limitations stated, nothing published")

"""Adversarial review — R7. Every candidate is challenged by a separate agent whose
job is to refute it, with the index and the history in hand.

One rule the spec applied to analysts and not to challengers, applied here to both:
a structural claim must resolve against the index. A challenger that kills a true
finding by citing a call path that does not exist would leave the kill rate looking
healthy while the report lost its best content. So a refutation that names symbols
must name symbols the index knows, or it is inadmissible and the candidate stands —
recorded as such.

The kill rate is a monitored metric (SRS §8): below 15% the challenger is a rubber
stamp; above 60% the analysts are noise. Either is recorded as a gap on the run.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from . import brainseam

MAX_WORKERS = 4

KILL_BAND = (0.15, 0.60)
OUTCOMES = ("upheld", "refuted", "weakened")

SCHEMA = (
    'Reply with STRICT JSON only: {"outcome": one of ["upheld","refuted","weakened"], '
    '"reasoning": 2-4 sentences, "cites": [qualified symbol names your reasoning relies '
    'on, or []], "severity": the severity you would assign, one of '
    '["critical","high","medium","low"]}. Refute only with a fact — a caller, a test, a '
    'guard, a line — never with taste.'
)


def _facts(c: dict, idx) -> str:
    """What the graph knows about the thing the candidate names — handed to the
    challenger so refutation can rest on facts rather than opinion."""
    lines = []
    if c.get("symbol"):
        s = c["symbol"]
        lines.append(f"callers_of({s}) = {idx.callers_of(s)[:12] or 'none'}")
        lines.append(f"references_of({s}) = {idx.references_of(s)[:12] or 'none'}")
        lines.append(f"tests_covering({s}) = {idx.tests_covering(s)[:8] or 'none'}")
    lines.append(f"dependents_of({c['unit']}) = {idx.dependents_of(c['unit'])[:12] or 'none'}")
    dyn = idx.dynamic_reasons(c["unit"])
    if dyn:
        lines.append(f"dynamic dispatch in unit: {dyn}")
    return brainseam.clip("review_facts", "\n".join(lines))


def _parse(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def challenge(c: dict, ctx: str, idx, budget) -> dict:
    """Returns {outcome, reasoning, severity, admissible, note}."""
    cand = brainseam.clip("candidate", json.dumps({k: c[k] for k in (
        "title", "category", "severity", "confidence", "symbol", "line_range",
        "claim_kind", "claim", "description")}))
    prompt = (f"Role: adversarial reviewer. A {c['proposed_by']} analyst proposed the "
              f"finding below about unit {c['unit']}. Your obligation is to attempt, in good "
              f"faith, to refute it using the graph facts and the source.\n\n"
              f"CANDIDATE: {cand}\n\nGRAPH FACTS:\n{_facts(c, idx)}\n\n{ctx}\n\n{SCHEMA}")
    try:
        out = brainseam.ask([{"role": "user", "content": prompt}], 2500, 0.2,
                            "review", "strong", budget)
    except Exception as e:                            # noqa: BLE001
        return {"outcome": "upheld", "reasoning": f"challenger unavailable: {e}",
                "severity": c["severity"], "admissible": False,
                "note": "unchallenged — stands provisionally"}
    d = _parse(out)
    outcome = d.get("outcome") if d.get("outcome") in OUTCOMES else "upheld"
    reasoning = str(d.get("reasoning") or "")[:600]
    sev = d.get("severity") if d.get("severity") in ("critical", "high", "medium", "low") \
        else c["severity"]
    cites = [str(x) for x in (d.get("cites") or []) if isinstance(x, str)][:12]
    bad = [s for s in cites if not idx.symbol(s) and not idx.symbol(f"{c['unit']}.{s}")]
    if outcome == "refuted" and bad:
        # The symmetry rule. A refutation resting on symbols the index does not know
        # is the challenger hallucinating, and it does not get to kill a finding.
        return {"outcome": "upheld", "reasoning": reasoning, "severity": c["severity"],
                "admissible": False,
                "note": f"refutation inadmissible — cites unresolvable {bad[:3]}; candidate stands"}
    if outcome == "weakened":
        order = ["critical", "high", "medium", "low"]
        sev = order[min(3, order.index(sev) + 1)] if sev in order else "low"
    return {"outcome": outcome, "reasoning": reasoning, "severity": sev,
            "admissible": True, "note": ""}


def review_all(candidates: list[dict], contexts_by_unit: dict, idx, budget,
               progress=None) -> dict:
    """Challenge every candidate; annotate each; compute the kill rate and its verdict."""
    n = len(candidates)

    def _do(c):
        r = challenge(c, contexts_by_unit.get(c["unit"], ""), idx, budget)
        c["challenge"] = r["outcome"]
        c["challenge_reason"] = r["reasoning"]
        c["challenge_note"] = r["note"]
        c["severity"] = r["severity"]
        if progress:
            progress()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(_do, candidates))
    killed = sum(1 for c in candidates if c["challenge"] == "refuted")
    rate = killed / n if n else 0.0
    band = ""
    if n >= 5:                                        # a band needs a sample
        if rate < KILL_BAND[0]:
            band = (f"challenge kill rate {rate:.0%} is below {KILL_BAND[0]:.0%} — the "
                    f"challenger may be a rubber stamp; flagged for inspection")
        elif rate > KILL_BAND[1]:
            band = (f"challenge kill rate {rate:.0%} is above {KILL_BAND[1]:.0%} — the "
                    f"analysts may be noise; flagged for inspection")
    return {"reviewed": n, "killed": killed, "kill_rate": round(rate, 3), "band_gap": band,
            "upheld": [c for c in candidates if c["challenge"] != "refuted"],
            "refuted": [c for c in candidates if c["challenge"] == "refuted"]}

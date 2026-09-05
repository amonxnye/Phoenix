"""The Governor — R8. One strong pass over everything that survived review: dedupe
across units and analysts, rank by consequence rather than by label, reject what is
unsupported with a stated reason, record refusals, and sign the set.

The governor's decision record is the compliance artefact (SRS §14.7). Every
accepted finding therefore carries a trail — proposed, challenged, decided — and every
rejection carries its reason. A finding with no trail is not a finding.

Without a model the governor is a rule: accept everything upheld, in rank order, and
say that no judgement was applied. A signature over a rule-ordered set is still a
signature; it just names its author honestly.
"""

import hashlib
import json
import math

from . import brainseam

_SEV_W = {"critical": 8.0, "high": 4.0, "medium": 2.0, "low": 1.0}

SCHEMA = (
    'Reply with STRICT JSON only: {"reject": [{"fingerprint": str, "reason": one '
    'sentence}], "refusals": [one sentence each — questions you decline to answer '
    'responsibly, if any]}. Reject a finding only when it is unsupported by its own '
    'evidence, duplicates another, or its recommendation would be unsafe to follow. Do '
    'not re-rank; ranking is computed from consequence.'
)


def consequence(f: dict, centrality: int) -> float:
    return _SEV_W.get(f.get("severity"), 1.0) * (1 + math.log1p(max(0, centrality))) \
        * float(f.get("confidence", 0.5))


def dedupe(upheld: list[dict]) -> tuple[list[dict], list[tuple]]:
    """Same fingerprint from two roles or two runs of the same unit → one finding,
    proposers merged, highest confidence kept. Returns (kept, merged_pairs)."""
    by = {}
    merged = []
    for f in upheld:
        k = f["fingerprint"]
        if k in by:
            keep = by[k]
            keep["proposed_by"] = "+".join(sorted(set(keep["proposed_by"].split("+"))
                                                  | {f["proposed_by"]}))
            keep["confidence"] = max(keep["confidence"], f["confidence"])
            merged.append((f["proposed_by"], keep["proposed_by"], k))
        else:
            by[k] = f
    return list(by.values()), merged


def sign(findings: list[dict], charter_stamp: str, model: str) -> str:
    body = "\n".join(sorted(f["fingerprint"] for f in findings)) + f"\n{charter_stamp}\n{model}"
    return hashlib.sha256(body.encode()).hexdigest()[:20]


def adjudicate(upheld: list[dict], centrality: dict, budget, charter_stamp: str) -> dict:
    kept, merged = dedupe(upheld)
    for f in kept:
        f["consequence"] = round(consequence(f, centrality.get(f["unit"], 0)), 3)
    kept.sort(key=lambda f: -f["consequence"])
    for i, f in enumerate(kept, 1):
        f["rank"] = i
    rejected, refusals, actor = [], [], "governor (rule: accept upheld, rank by consequence)"
    if kept and brainseam.available():
        actor = f"governor ({brainseam.name()})"
        listing = brainseam.clip("finding_set", json.dumps([
            {k: f.get(k) for k in ("fingerprint", "rank", "title", "category", "severity",
                                   "confidence", "claim", "challenge", "challenge_reason")}
            for f in kept]))
        prompt = (f"Role: the Governor of the Software Mechanic. {len(kept)} findings survived "
                  f"adversarial review; they are listed in consequence order.\n\n{listing}\n\n"
                  f"{SCHEMA}")
        try:
            d = json.loads((brainseam.ask([{"role": "user", "content": prompt}], 4000, 0.1,
                                          "governor", "strong", budget) or "{}")
                           .strip().strip("`").removeprefix("json").strip() or "{}")
        except brainseam.ProviderDown:
            raise
        except Exception:                             # noqa: BLE001 — the rule stands in
            d = {}
        rej = {str(r.get("fingerprint")): str(r.get("reason") or "unsupported")
               for r in (d.get("reject") or []) if isinstance(r, dict)}
        refusals = [str(x)[:300] for x in (d.get("refusals") or []) if isinstance(x, str)][:6]
        still = []
        for f in kept:
            if f["fingerprint"] in rej:
                f["governor"], f["governor_reason"] = "rejected", rej[f["fingerprint"]][:300]
                rejected.append(f)
            else:
                still.append(f)
        kept = still
        for i, f in enumerate(kept, 1):
            f["rank"] = i
    for f in kept:
        f["governor"], f["governor_reason"] = "accepted", f.get("governor_reason", "")
    return {"accepted": kept, "rejected": rejected, "merged": merged, "refusals": refusals,
            "actor": actor, "signature": sign(kept, charter_stamp, brainseam.name())}

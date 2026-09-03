"""The analyst panel — R6. Several agents, one unit each at a time, each blind to the
others, each emitting candidates in one schema. Candidates, not conclusions: nothing
here reaches the report without surviving review.

Isolation is by construction, not by instruction: an analyst's prompt contains its
role, its unit's context, and the schema. There is no channel by which another
analyst's output could reach it.

Two rules the Charter adds before review ever sees a candidate:

- A structural claim that does not resolve against the index is auto-rejected (§9,
  SRS §20 "analyst hallucinates a call path"). Reviewers are expensive; the index is
  free, and it is the oracle for exactly this class of claim.
- A per-unit cap on candidates, so one chatty unit cannot make review unaffordable.

Prompt-injection text found in the repository is itself a finding (§5) — a text fact,
found deterministically before any model reads the unit.
"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import brainseam

ROLES = {
    "quality": ("You examine construction quality: duplicated logic, missing error "
                "handling, untested paths, misleading names, functions doing several "
                "jobs. Prefer claims the graph or the tests can support."),
    "security": ("You examine safety: injection surfaces (shell, SQL, path, template), "
                 "unsafe deserialisation, secrets in code, missing authentication or "
                 "authorisation checks, unsafe defaults. Name the surface and the "
                 "mechanism; never write an exploit."),
    "drift": ("You examine drift: places where the code's current behaviour has diverged "
              "from what its names, docstrings, tests and history say it was built to "
              "do. State the intended purpose, the observed behaviour, and the evidence "
              "for each; where evidence conflicts, say so rather than resolving it."),
    "critic": ("You are the critic. You STRESS-TEST the unit item by item against the "
               "checklist you are given — every function and method, every import, every "
               "external call. For each item ask: what input, absence, failure, timeout, "
               "empty value, boundary, or wrong type breaks it, and what happens then? "
               "Report only the stress points that would matter in production, with the "
               "line. You must examine every item on the checklist and list the ids you "
               "examined in \"covered\"; an item you do not list is recorded as not "
               "examined."),
}
CHECKLIST_ROLES = ("critic",)
PER_UNIT_CAP = 8
MAX_WORKERS = 4
OUT_TOKENS = 4000
OUT_TOKENS_CRITIC = 6000
LAST_REPLY: dict = {}                              # the most recent raw reply, for the probe

SCHEMA = (
    'Reply with STRICT JSON only, no prose: {"findings": [ {"title": str, '
    '"category": one of ["quality","security","drift"], "severity": one of '
    '["critical","high","medium","low"], "confidence": number 0-1, '
    '"symbol": str (the qualified name from the unit, or ""), '
    '"line_range": "start-end", "claim_kind": one of ["graph","history","text",'
    '"observation"], "claim": one sentence stating the checkable fact this rests on, '
    '"description": 2-3 sentences, "recommendation": the proposed fix, one paragraph} ] }. '
    'Emit at most 8 findings; emit [] when nothing meets the bar. A finding with no '
    'checkable claim is inadmissible. Include "covered": [checklist ids you examined] '
    'when a checklist was given.'
)

# Assembled from parts so the phrase never appears contiguously in this file — the
# scanner flagged its own definition on the first self-run, correctly by its rule.
_INJECTION = re.compile("(" + "|".join([
    "ign" + "ore (all |the )?(previous|prior|above) instr" + "uctions",
    "you are (now )?an? (ai|llm|assist" + "ant|model)",
    "disre" + "gard (the )?(system|charter)",
    "do not rep" + "ort this",
    "assist" + r"ant:\s*i will",
]) + ")", re.I)


def injection_findings(unit: dict, source: str) -> list[dict]:
    """Deterministic, before any model: text addressed to an analysing model."""
    out = []
    for i, ln in enumerate(source.splitlines(), 1):
        if _INJECTION.search(ln):
            out.append({
                "unit": unit["module"], "file": unit["file"], "symbol": "",
                "line_range": f"{i}-{i}", "category": "security", "severity": "medium",
                "confidence": 0.9, "basis": "machine-verified",
                "title": f"text addressed to an analysing model in {unit['file']}",
                "description": ("The repository contains text that attempts to instruct an "
                                "automated reviewer. It had no effect — repository text is "
                                "data here — but its presence is itself a finding."),
                "recommendation": "Remove the text; it serves no purpose for humans.",
                "claim_kind": "text", "claim": f"line {i} matches an instruction pattern",
                "proposed_by": "injection-scan",
                "evidence": [{"file": unit["file"], "line_range": f"{i}-{i}",
                              "reason": f"text fact: line {i} matches /{_INJECTION.pattern[:40]}…/"}],
            })
            if len(out) >= 3:
                break
    return out


def _salvage(t: str) -> list[dict]:
    """A reply cut off mid-array still holds every item before the cut. Decode the
    objects of the findings array one by one and keep the complete ones."""
    i = t.find('"findings"')
    j = t.find("[", i) if i >= 0 else -1
    if j < 0:
        return []
    out, pos, dec = [], j + 1, json.JSONDecoder()
    while True:
        k = t.find("{", pos)
        if k < 0:
            break
        try:
            obj, end = dec.raw_decode(t, k)
        except json.JSONDecodeError:
            break                                 # the cut item; everything before it kept
        if isinstance(obj, dict):
            out.append(obj)
        pos = end
    return out


def _parse(text: str) -> list[dict]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        return _salvage(t)
    f = d.get("findings") if isinstance(d, dict) else d
    return [x for x in (f or []) if isinstance(x, dict)]


def fingerprint(f: dict) -> str:
    key = f"{f.get('category')}|{f.get('file')}|{f.get('symbol') or f.get('title', '').lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _admit(raw: dict, unit: dict, idx, role: str) -> tuple[dict | None, str]:
    """Validate one candidate. Returns (candidate, '') or (None, reason)."""
    cat = raw.get("category") if raw.get("category") in ROLES else role
    sev = raw.get("severity") if raw.get("severity") in ("critical", "high", "medium",
                                                          "low") else "low"
    sym = (raw.get("symbol") or "").strip()
    if sym:
        q = sym if idx.symbol(sym) else f"{unit['module']}.{sym}"
        if not idx.symbol(q):
            return None, f"structural claim names `{sym}`, which does not resolve in the index"
        sym = q
    claim = (raw.get("claim") or "").strip()
    if not claim:
        return None, "no checkable claim"
    m = re.search(r"\d+(?:\s*-\s*\d+)?", str(raw.get("line_range") or ""))
    lr = re.sub(r"\s+", "", m.group(0)) if m else ""
    if not lr and sym:                            # the index knows where the symbol is
        ss = idx.symbol(sym)
        lr = f"{ss['line']}-{ss['end_line']}"
    if not lr:
        return None, f"line range {raw.get('line_range')!r} is not a range and no symbol to place it"
    a = int(lr.split("-")[0])
    if a < 1 or a > max(1, unit["loc"]):
        return None, f"line {a} is outside the unit ({unit['loc']} lines)"
    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "unit": unit["module"], "file": unit["file"], "symbol": sym, "line_range": lr,
        "category": cat, "severity": sev, "confidence": conf, "basis": "judged",
        "title": brainseam.clip("candidate", raw.get("title") or "untitled")[:160],
        "description": brainseam.clip("candidate", raw.get("description") or ""),
        "recommendation": brainseam.clip("candidate", raw.get("recommendation") or ""),
        "claim_kind": raw.get("claim_kind") if raw.get("claim_kind") in
        ("graph", "history", "text", "observation") else "observation",
        "claim": claim[:400], "proposed_by": role,
        "evidence": [{"file": unit["file"], "line_range": lr,
                      "reason": f"{raw.get('claim_kind', 'observation')} claim: {claim[:300]}"}],
    }, ""


def _parse_obj(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def _parse_covered(text: str) -> list[str]:
    t = (text or "").strip().strip("`")
    t = t[4:] if t.lower().startswith("json") else t
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in (d.get("covered") or [])] if isinstance(d, dict) else []


def _one(unit: dict, ctx: str, role: str, idx, budget, checklist: list | None = None) -> dict:
    extra = ""
    if checklist:
        from . import decompose
        extra = "\n\nCHECKLIST — every item must be examined:\n" + decompose.checklist_text(checklist)
    prompt = (f"Role: {role} analyst on the Software Mechanic panel.\n{ROLES[role]}\n\n"
              f"{ctx}{extra}\n\n{SCHEMA}")
    try:
        # A reasoning model spends max_tokens thinking before it writes. On the first
        # production run every reply came back EMPTY at 900–1200: the budget was gone
        # before the JSON began. Output tokens on a cheap-class model cost nothing that
        # matters next to a silent run.
        out = brainseam.ask([{"role": "user", "content": prompt}],
                            OUT_TOKENS_CRITIC if checklist else OUT_TOKENS, 0.3,
                            f"panel:{role}", "cheap", budget)
    except Exception as e:                            # noqa: BLE001 — recorded, not raised
        return {"candidates": [], "dropped": [(unit["module"], role, f"call failed: {e}")],
                "coverage": None}
    LAST_REPLY.update(text=out or "", prompt_chars=len(prompt), role=role, unit=unit["module"])
    coverage = None
    if checklist:
        ids = {i["id"] for i in checklist}
        seen = set(_parse_covered(out)) & ids
        coverage = {"unit": unit["module"], "covered": len(seen), "total": len(ids),
                    "missed": sorted(ids - seen)[:12]}
    raws = _parse(out)
    head = (out or "").strip().replace("\n", " ")[:120]
    if not (out or "").strip():
        return {"candidates": [], "dropped": [(unit["module"], role, "reply was empty")],
                "coverage": coverage}
    if not raws and _parse_obj(out) is None and not _salvage(out):
        # Say what came back. A silent drop is how a clipped prompt hid for a whole run.
        return {"candidates": [], "dropped": [(unit["module"], role,
                                               f"reply was not the schema: {head!r}")],
                "coverage": coverage}
    cands, dropped = [], []
    for raw in raws[:PER_UNIT_CAP]:
        c, why = _admit(raw, unit, idx, role)
        if c:
            cands.append(c)
        else:
            dropped.append((unit["module"], role, why))
    if len(raws) > PER_UNIT_CAP:
        dropped.append((unit["module"], role, f"{len(raws) - PER_UNIT_CAP} over the per-unit cap"))
    return {"candidates": cands, "dropped": dropped, "coverage": coverage}


def run(us: list[dict], contexts: list[str], idx, budget,
        roles: tuple = tuple(ROLES), progress=None) -> dict:
    """All units × all roles, in parallel, isolated. Returns candidates, what was
    dropped and why, and the call count."""
    from . import decompose
    jobs = [(u, c, r, decompose.checklist(u, idx) if r in CHECKLIST_ROLES else None)
            for u, c in zip(us, contexts) for r in roles if not u["is_test"]]
    candidates, dropped, coverage = [], [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(lambda j: _one(j[0], j[1], j[2], idx, budget, j[3]), jobs):
            candidates.extend(res["candidates"])
            dropped.extend(res["dropped"])
            if res.get("coverage"):
                coverage.append(res["coverage"])
            if progress:
                progress()
    for c in candidates:
        c["fingerprint"] = fingerprint(c)
    return {"candidates": candidates, "dropped": dropped, "calls": len(jobs),
            "coverage": coverage}

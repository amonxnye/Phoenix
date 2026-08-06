"""The real-literature oracle — biomedical evidence that has to actually say it.

The toy corpus proved the machinery; this is the same oracle pointed at the real
biomedical literature via Europe PMC. It exists to kill the failure mode that makes
LLMs dangerous in biology and medicine: a fluent, plausible claim attached to a
citation that does not exist, does not mention the entities, or says the opposite.

Three things are separated on purpose:

**Retrieval is not the oracle.** ``search``/``resolve``/``ingest`` touch the network,
and only ever run from the hand-written CLI — never from an agent's cycle. They write
retrieved records into ``sandbox/evidence/`` with their source URL and retrieval date
(Article VI.2: external knowledge is ingested with its source recorded).

**The oracle is offline and deterministic.** ``supports()`` reads only the stored
record. Same claim, same record, same verdict — forever, with no network, no model and
no API key. That is what lets it be an oracle rather than an opinion, and what lets CI
run it.

**A citation must be quoted, and the quote must be real.** ``verify_quote`` checks the
agent's quoted sentence is a verbatim substring of the stored title+abstract. An agent
cannot invent a sentence and attribute it to a paper: the fabrication is caught by
string comparison, not by judgement. Then the *same* sentence must survive the support
check — the two are independent, and both must pass.

Only open-access records are stored with their text (Europe PMC ``isOpenAccess``), and
each record carries its licence. A paywalled citation resolves to metadata only, and
the oracle refuses to score a claim on text it does not have rather than guessing —
``no-verifiable-text`` is an honest verdict.

The support check is deliberately **conservative**: it demands the entities and a
direction cue in one sentence, in a recognised grammatical shape. A true claim phrased
in a way it does not recognise is rejected with the sentence it looked at, which costs
the agent a cycle. A false claim slipping through costs the whole point of the system.
Those errors are not symmetric, so the bias is not either.

Ingest (network, human-run):
    python3 gov/literature.py --ingest "osimertinib EGFR resistance" --limit 5
    python3 gov/literature.py --resolve 31825061
    python3 gov/literature.py --aliases EGFR
"""

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.path.dirname(_HERE), "sandbox")
EVIDENCE_DIR = os.path.join(SANDBOX, "evidence")
ALIASES_PATH = os.path.join(EVIDENCE_DIR, "aliases.json")

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNIPROT = "https://rest.uniprot.org/uniprotkb/search"
UA = "Phoenix-research-harness (governed research org; contact: repo owner)"
TIMEOUT = 30


# ── retrieval: the network path, run by a human, never by an agent ────────────

def _get(url: str, params: dict) -> dict:
    q = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(q, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT,
                                context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _record(r: dict) -> dict:
    """Normalise a Europe PMC result into a stored evidence record. Text is kept only
    for open-access papers; everything else is metadata plus an honest null."""
    oa = str(r.get("isOpenAccess", "")).upper() == "Y"
    pmid = str(r.get("pmid") or "")
    # Europe PMC returns entity-encoded markup in titles and abstracts; decode and strip
    # it, or every quote an agent copies out would fail a verbatim check on formatting.
    def _clean(s):
        s = re.sub(r"<[^>]{1,40}>", " ", html.unescape(s or ""))
        # structured abstracts arrive with section headers glued to the previous
        # sentence ("...proliferation.ConclusionThis study..."). Left alone, sentence
        # splitting produces one giant pseudo-sentence and the support check reads
        # across claim boundaries — so separate them at ingest, once.
        s = re.sub(r"(?<=[.:;])(?=[A-Z][a-z]{2,})", " ", s)
        s = re.sub(r"\b(Background|Objectives?|Aims?|Purpose|Methods?|Results?|"
                   r"Conclusions?|Discussion|Introduction|Summary|Findings|"
                   r"Significance|Importance|Interpretation)(?=[A-Z])", r"\1. ", s)
        return _norm_ws(s)
    return {
        "id": f"PMID:{pmid}" if pmid else f"EPMC:{r.get('id')}",
        "pmid": pmid, "pmcid": r.get("pmcid", ""), "doi": r.get("doi", ""),
        "title": _clean(r.get("title")),
        "abstract": _clean(r.get("abstractText")) if oa else None,
        "journal": ((r.get("journalInfo") or {}).get("journal") or {}).get("title", ""),
        "year": r.get("pubYear", ""), "authors": (r.get("authorString") or "").strip(),
        "open_access": oa, "license": r.get("license", ""),
        "source": "Europe PMC",
        "source_url": f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id')}",
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def search(query: str, limit: int = 5, open_access_only: bool = True) -> list[dict]:
    q = f"({query}) AND OPEN_ACCESS:y" if open_access_only else query
    data = _get(EPMC, {"query": q, "format": "json", "resultType": "core",
                       "pageSize": max(1, min(int(limit), 25))})
    return [_record(r) for r in data.get("resultList", {}).get("result", [])]


def resolve(pmid: str) -> dict | None:
    """Resolve one citation against the real world. A PMID that returns nothing here
    is a citation that does not exist — the cheapest and most damning rejection."""
    data = _get(EPMC, {"query": f"EXT_ID:{str(pmid).strip()} AND SRC:MED",
                       "format": "json", "resultType": "core", "pageSize": 1})
    hits = data.get("resultList", {}).get("result", [])
    return _record(hits[0]) if hits else None


def uniprot_aliases(gene: str, organism: str = "9606") -> list[str]:
    """Real synonyms for a protein, so the oracle isn't fooled by a paper that uses the
    other name for the same thing (ERBB1 and HER1 are EGFR)."""
    data = _get(UNIPROT, {"query": f"gene:{gene} AND organism_id:{organism} AND reviewed:true",
                          "format": "json", "size": 1,
                          "fields": "accession,gene_names,protein_name"})
    out, res = [], data.get("results", [])
    if not res:
        return out
    r = res[0]
    for g in r.get("genes", []):
        if g.get("geneName", {}).get("value"):
            out.append(g["geneName"]["value"])
        out += [s["value"] for s in g.get("synonyms", []) if s.get("value")]
    pd = (r.get("proteinDescription") or {}).get("recommendedName", {})
    if pd.get("fullName", {}).get("value"):
        out.append(pd["fullName"]["value"])
    seen, uniq = set(), []
    for a in out:
        if a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq


# ── the evidence store: what the offline oracle reads ─────────────────────────

def _path(cid: str) -> str:
    return os.path.join(EVIDENCE_DIR, cid.replace(":", "-") + ".json")


def store_put(rec: dict) -> str:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    with open(_path(rec["id"]), "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")
    return rec["id"]


def store_get(cid: str) -> dict | None:
    p = _path(str(cid).strip())
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def store_ids() -> list[str]:
    if not os.path.isdir(EVIDENCE_DIR):
        return []
    return sorted(n[:-5].replace("-", ":", 1) for n in os.listdir(EVIDENCE_DIR)
                  if n.endswith(".json") and n != "aliases.json")


def is_citation(ref: str) -> bool:
    return bool(re.match(r"^(PMID|PMC|EPMC):", str(ref).strip(), re.I))


def aliases(entity: str) -> list[str]:
    """Every name the store knows for one thing, the entity itself always included."""
    e = str(entity).strip()
    table = {}
    if os.path.exists(ALIASES_PATH):
        with open(ALIASES_PATH) as f:
            table = json.load(f)
    for key, names in table.items():
        if e.lower() == key.lower() or any(e.lower() == n.lower() for n in names):
            return [key] + [n for n in names if n.lower() != key.lower()]
    return [e]


def aliases_put(entity: str, names: list[str]) -> None:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    table = {}
    if os.path.exists(ALIASES_PATH):
        with open(ALIASES_PATH) as f:
            table = json.load(f)
    have = {n.lower() for n in table.get(entity, [])}
    table[entity] = table.get(entity, []) + [n for n in names if n.lower() not in have]
    with open(ALIASES_PATH, "w") as f:
        json.dump(table, f, indent=2, sort_keys=True)
        f.write("\n")


# ── the offline oracle: does this text actually say that? ─────────────────────

# Direction cues. A relation is recognised by its verb family, and every family has an
# opposite — which is what makes "the citation says the reverse" a detectable verdict
# rather than a reviewer's hunch.
# VERBS are verb forms only. "inhibitor" is a noun and must never satisfy a verb
# pattern: "sotorasib, adagrasib, and the RAS-ON inhibitor RMC-6291 are effective in a
# line altered by KRAS(G12C)" does not assert that sotorasib inhibits KRAS, however
# true that happens to be. The noun forms get their own patterns, which name the
# argument roles explicitly.
VERBS = {
    "inhibits": r"inhibit(?:s|ed|ing)?|block(?:s|ed|ing)?|suppress(?:es|ed|ing)?|"
                r"antagoni[sz](?:e|es|ed|ing)|abrogat(?:e|es|ed|ing)|"
                r"attenuat(?:e|es|ed|ing)|down-?regulat(?:e|es|ed|ing)|"
                r"reduc(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)|impair(?:s|ed|ing)?|"
                r"repress(?:es|ed|ing)?|deplet(?:e|es|ed|ing)|prevent(?:s|ed|ing)?",
    # 'achieved remission' is how clinical outcomes are written; without it the checker
    # can read molecular biology but not a trial result, which is half of healthcare
    "activates": r"activat(?:e|es|ed|ing)|stimulat(?:e|es|ed|ing)|induc(?:e|es|ed|ing)|"
                 r"up-?regulat(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|"
                 r"enhanc(?:e|es|ed|ing)|promot(?:e|es|ed|ing)|"
                 r"potentiat(?:e|es|ed|ing)|agoni[sz](?:e|es|ed|ing)|"
                 r"driv(?:e|es|ing)|driven|achiev(?:e|es|ed|ing)|attain(?:s|ed|ing)?|"
                 r"led\s+to|leads?\s+to|result(?:s|ed)\s+in",
    "binds": r"bind(?:s|ing)?|bound|interact(?:s|ed|ing)?|engag(?:e|es|ed|ing)|"
             r"associat(?:e|es|ed|ing)",
}
NOUN_AGENT = {                                     # "the EGFR inhibitor osimertinib"
    "inhibits": r"inhibitors?|antagonists?|blockers?|suppressors?",
    "activates": r"activators?|agonists?|inducers?|stimulators?",
    "binds": r"ligands?|binders?|binding\s+partners?",
}
PARTICIPLE = {                                     # "nutrition-induced remission"
    "inhibits": r"suppressed|inhibited|blocked|attenuated|reduced|impaired|depleted",
    "activates": r"induced|driven|triggered|stimulated|evoked|promoted|activated",
    "binds": r"bound|engaged",
}
NOUN_EVENT = {                                     # "inhibition of EGFR by osimertinib"
    "inhibits": r"inhibition|blockade|suppression|antagonism|down-?regulation",
    "activates": r"activation|stimulation|induction|up-?regulation",
    "binds": r"binding|interaction|engagement",
}
FAMILY = {"inhibits": "inhibits", "downregulates": "inhibits", "reduces": "inhibits",
          "activates": "activates", "upregulates": "activates", "induces": "activates",
          "binds": "binds"}
OPPOSITE = {"inhibits": "activates", "activates": "inhibits"}

NEGATION = re.compile(
    r"\b(?:no|not|never|neither|nor|without|lack\w*|absence|unaffected|unchanged|"
    r"fail(?:s|ed|ure)?|unable|non-?significant\w*|did\s+not|does\s+not|do\s+not|"
    r"was\s+not|were\s+not|is\s+not|are\s+not)\b", re.I)

_ABBREV = re.compile(r"\b(?:et al|e\.g|i\.e|vs|Fig|approx|ca|cf|Dr|no)\.$", re.I)

# A paper raising a possibility is not a paper reporting a result. 'a future study
# looking at the feasibility of a low-energy diet to induce remission' has exactly the
# grammar of an assertion and none of the content, and counting it as corroboration is
# how a replication talks itself into an answer.
_HEDGE = re.compile(
    r"\b(?:may|might|could|can|would|should|potential(?:ly)?|possibl[ye]|"
    r"appears?\s+to|seem(?:s|ed)?\s+to|suggest(?:s|ed|ing)?|hypothesi[sz]\w*|"
    r"aim(?:s|ed)?\s+to|seek(?:s|ing)?\s+to|feasibility|whether|promis\w+|"
    r"to\s+(?:determine|assess|evaluate|investigate|examine|test|explore)|"
    r"future\s+stud\w+|warrant\w*|remains?\s+unclear|further\s+research)\b", re.I)

# 'X synergized WITH osimertinib ... downregulated EGFR' does not assert that
# osimertinib downregulates EGFR — after a preposition the entity is oblique, not the
# subject. But 'treatment with osimertinib inhibited EGFR' does assert it, because the
# preposition hangs off a noun. The pair of tests separates the two cheaply.
_OBLIQUE = re.compile(r"\b(?:with|plus|alongside|besides|versus|vs\.?|against|"
                      r"compared\s+(?:to|with)|combined\s+with|combination\s+with)\s+$", re.I)
_AGENTIVE = re.compile(r"\b(?:treatment|therapy|therapies|exposure|incubation|"
                       r"administration|stimulation|pre-?treatment|co-?treatment|"
                       r"infusion|supplementation|application)\s+(?:with|by)\s+$", re.I)


def sentences(text: str) -> list[str]:
    """Split into sentences without dragging in a parser. Abbreviation-aware enough
    that 'et al.' and 'e.g.' don't shred a sentence the quote check has to match."""
    out, buf = [], ""
    for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        buf = (buf + " " + part).strip() if buf else part
        if not _ABBREV.search(buf):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [s for s in out if s]


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def record_text(rec: dict) -> str:
    return _norm_ws(f"{rec.get('title', '')} {rec.get('abstract') or ''}")


def _ent_pattern(entity: str) -> str:
    names = sorted(aliases(entity), key=len, reverse=True)
    return r"(?:" + "|".join(re.escape(n) for n in names) + r")"


def mentions(entity: str, text: str) -> bool:
    return matched_alias(entity, text) is not None


def matched_alias(entity: str, text: str) -> str | None:
    """WHICH name for the thing this text actually used. A corroboration that rests on
    treating 'very low calorie diets' as 'calorie restriction' must say so — the
    equivalence is a judgement, and a judgement has to be visible to be challenged."""
    m = re.search(r"(?<![\w-])" + _ent_pattern(entity) + r"(?![\w-]|-(?:induced|mediated|"
                  r"driven|triggered|dependent))", text or "", re.I)
    if m:
        return m.group(0)
    m = re.search(r"(?<![\w-])" + _ent_pattern(entity) + r"(?=-)", text or "", re.I)
    return m.group(0) if m else None


def verify_quote(quote: str, rec: dict) -> bool:
    """Is this sentence really in that paper? Whitespace-normalised, case-insensitive,
    verbatim. An agent cannot cite a sentence it wrote."""
    text = record_text(rec)
    q = _norm_ws(quote)
    return bool(q) and len(q) >= 20 and q.lower() in text.lower()


def _negated(sentence: str, upto: int) -> bool:
    """Negation anywhere in the clause leading up to the cue — 'did not inhibit'."""
    window = sentence[max(0, upto - 60):upto]
    return bool(NEGATION.search(window))


def _hedged(sentence: str, upto: int) -> bool:
    """Is the assertion under a hedge, or is it a purpose rather than a finding? A bare
    infinitive ('...to induce remission') states an intention; only a tensed verb
    reports one."""
    if re.search(r"\bto\s+$", sentence[max(0, upto - 4):upto]):
        return True
    return bool(_HEDGE.search(sentence[max(0, upto - 90):upto]))


def _direction_hit(sentence: str, subj: str, obj: str, family: str) -> tuple[bool, bool, bool]:
    """Does this sentence assert subject --family--> object? Returns (hit, negated).

    Five grammatical shapes are recognised, and no others:
      active      'osimertinib inhibits EGFR'
      passive     'EGFR is inhibited by osimertinib'
      agent-noun  'the EGFR inhibitor osimertinib'
      agent-of    'osimertinib, an inhibitor of EGFR'
      event-noun  'inhibition of EGFR by osimertinib'
    Anything else is treated as not asserted. Being wrong in this direction only costs
    an agent a cycle; being wrong in the other direction costs the whole oracle."""
    S, O = _ent_pattern(subj), _ent_pattern(obj)
    verb, agent, event = VERBS[family], NOUN_AGENT[family], NOUN_EVENT[family]
    b, e = r"(?<![\w-])", r"(?![\w-])"
    cue = rf"(?:{verb}|{agent}|{event})"
    # 'Cystamine had no effect on LDL oxidation, but cystine partially inhibited LDL
    # oxidation' must not read as cystamine inhibiting anything. A clause break ends
    # the reach of a subject, so the gap between the parts of a pattern cannot cross one.
    gap = (lambda n: rf"(?:(?!;|,\s*(?:but|whereas|while|although|though|however|"
                     rf"in\s+contrast|conversely)\b).){{0,{n}}}?")
    patterns = [
        ("active", rf"{b}{S}{e}{gap(120)}{b}(?:{verb}){e}(?!\s+by\b){gap(90)}{b}{O}{e}"),
        # 'remission … can be achieved through targeted nutritional interventions'
        ("passive", rf"{b}{O}{e}{gap(90)}{b}(?:{verb}){e}\s+(?:by|through|via)\s+"
                    rf"{gap(50)}{b}{S}{e}"),
        ("agent-noun", rf"{b}{O}{e}[\s-]+(?:{agent}){e}[\s,]+(?:[\w-]+\s+){{0,2}}?{b}{S}{e}"),
        ("agent-of", rf"{b}{S}{e}.{{0,40}}?{b}(?:{agent}){e}\s+of\s+(?:the\s+)?{b}{O}{e}"),
        ("event-noun",
         rf"{b}(?:{event}){e}\s+of\s+(?:the\s+)?{b}{O}{e}.{{0,60}}?\bby\s+.{{0,50}}?{b}{S}{e}"),
        # 'apigenin exerts its effects via inhibition of the EGFR/MAPK pathways'
        ("event-via",
         rf"{b}{S}{e}.{{0,60}}?\b(?:via|through|by)\s+(?:{event})\s+of\s+(?:the\s+)?{b}{O}{e}"),
        # 'AMPK-mediated suppression of the NLRP3 inflammasome' — the hyphenated agent
        # is how half of biology names the actor, and dropping it loses real evidence
        ("compound-agent",
         rf"{b}{S}-(?:mediated|induced|driven|dependent|triggered|evoked)\s+"
         rf"(?:{event})\s+of\s+(?:the\s+)?{b}{O}{e}"),
        # 'nutrition-induced T2DM remission' — the participle carries the direction, so
        # only participles of THIS family qualify (neutral '-mediated' does not)
        ("compound-active",
         rf"{b}{S}-(?:{PARTICIPLE[family]})\s+(?:the\s+)?{b}{O}{e}"),
    ]
    for kind, pat in patterns:
        for m in re.finditer(pat, sentence, re.I):
            if kind in ("active", "agent-of"):
                head = sentence[:m.start()]
                if _OBLIQUE.search(head) and not _AGENTIVE.search(head):
                    continue                       # the entity is oblique, not the actor
            cm = re.search(rf"{b}{cue}{e}", m.group(0), re.I)
            at = m.start() + (cm.start() if cm else 0)
            return True, _negated(sentence, at), _hedged(sentence, at)
    return False, False, False


def supports(claim: dict, rec: dict, quote: str = "") -> dict:
    """The verdict for one claim against one real paper.

    supported | contradicted | not-mentioned | unsupported | no-verifiable-text |
    fabricated-quote | unquoted"""
    s = str(claim.get("subject", "")).strip()
    r = str(claim.get("relation", "")).strip().lower()
    o = str(claim.get("object", "")).strip()
    fam = FAMILY.get(r)
    if not fam:
        return {"verdict": "unsupported", "why": f"no evidence pattern for relation '{r}'"}
    if rec.get("abstract") is None:
        return {"verdict": "no-verifiable-text",
                "why": f"{rec['id']} is not open access — the text cannot be checked, so "
                       f"the claim is not scored on it"}
    text = record_text(rec)
    if not quote:
        return {"verdict": "unquoted",
                "why": f"quote the sentence in {rec['id']} that says this — a citation "
                       f"without a quote is not evidence"}
    if not verify_quote(quote, rec):
        return {"verdict": "fabricated-quote",
                "why": f"that sentence does not appear in {rec['id']}"}
    if not (mentions(s, text) and mentions(o, text)):
        missing = [e for e in (s, o) if not mentions(e, text)]
        return {"verdict": "not-mentioned",
                "why": f"{rec['id']} never mentions {', '.join(missing)}"}

    hit, neg, hedge = _direction_hit(_norm_ws(quote), s, o, fam)
    if hit and not neg and hedge:
        return {"verdict": "hedged", "quote": _norm_ws(quote),
                "why": f"{rec['id']} raises it as a possibility or a purpose, and does "
                       f"not report it: \"{_norm_ws(quote)[:160]}\""}
    if hit and not neg:
        return {"verdict": "supported", "quote": _norm_ws(quote),
                "why": f"{rec['id']} states it: \"{_norm_ws(quote)[:160]}\""}
    if hit and neg:
        return {"verdict": "contradicted", "quote": _norm_ws(quote),
                "why": f"{rec['id']} negates it: \"{_norm_ws(quote)[:160]}\""}
    opp = OPPOSITE.get(fam)
    if opp:
        ohit, oneg, ohedge = _direction_hit(_norm_ws(quote), s, o, opp)
        if ohit and not oneg and not ohedge:
            return {"verdict": "contradicted", "quote": _norm_ws(quote),
                    "why": f"{rec['id']} reports the opposite direction: "
                           f"\"{_norm_ws(quote)[:160]}\""}
    return {"verdict": "unsupported", "quote": _norm_ws(quote),
            "why": f"the quoted sentence mentions both, but does not assert "
                   f"'{s} {r} {o}': \"{_norm_ws(quote)[:160]}\""}


_SECTION = re.compile(
    r"\b(Background|Introduction|Aims?|Objectives?|Purpose|Materials and methods|"
    r"Methods?|Results?|Findings|Conclusions?|Interpretation|Discussion|Summary)\b")
_SECTION_KIND = {"result": "result", "results": "result", "findings": "result",
                 "conclusion": "conclusion", "conclusions": "conclusion",
                 "interpretation": "conclusion", "discussion": "conclusion",
                 "summary": "conclusion", "method": "methods", "methods": "methods",
                 "materials and methods": "methods"}


def section_of(rec: dict, sentence: str) -> str:
    """Where in the paper a sentence sits: title | background | methods | result |
    conclusion | unsectioned.

    This is the difference between evidence and topic. A title says what a study was
    about; an introduction repeats what other people found — often citing the very work
    under test. Only a paper's own results and conclusions are its contribution, so a
    replication that rests on anything else has to say so out loud."""
    q = _norm_ws(sentence).lower()
    title = _norm_ws(rec.get("title", "")).lower()
    if q and q in title:
        return "title"
    body = _norm_ws(rec.get("abstract") or "")
    at = body.lower().find(q)
    if at < 0:
        return "unsectioned"
    heads = [m for m in _SECTION.finditer(body[:at])]
    if not heads:
        return "background" if at < len(body) * 0.34 else "unsectioned"
    kind = heads[-1].group(1).lower()
    return _SECTION_KIND.get(kind, "background")


def find_support(claim: dict, rec: dict) -> str:
    """The sentence in this paper that would support the claim, if one does. Used to
    tell an agent what it should have quoted — never to score it."""
    if rec.get("abstract") is None:
        return ""
    fam = FAMILY.get(str(claim.get("relation", "")).lower())
    if not fam:
        return ""
    for sent in sentences(record_text(rec)):
        hit, neg, hedge = _direction_hit(sent, claim.get("subject", ""),
                                         claim.get("object", ""), fam)
        if hit and not neg and not hedge:
            return sent
    return ""


# ── the ingest CLI (a human runs this; agents never do) ───────────────────────

def ingest(query: str, limit: int = 5) -> list[dict]:
    recs = search(query, limit)
    for r in recs:
        store_put(r)
    return recs


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ingest real biomedical evidence")
    ap.add_argument("--ingest", metavar="QUERY", help="search Europe PMC and store the hits")
    ap.add_argument("--resolve", metavar="PMID", help="resolve and store one PMID")
    ap.add_argument("--aliases", metavar="GENE", help="store UniProt synonyms for a gene")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--list", action="store_true", help="what the store already holds")
    a = ap.parse_args()
    if a.ingest:
        for r in ingest(a.ingest, a.limit):
            print(f"{r['id']}  {r['year']}  oa={r['open_access']}  {r['title'][:70]}")
    elif a.resolve:
        r = resolve(a.resolve)
        if not r:
            print(f"PMID {a.resolve} does not resolve — that citation does not exist")
            sys.exit(1)
        store_put(r)
        print(json.dumps({k: v for k, v in r.items() if k != "abstract"}, indent=2))
    elif a.aliases:
        names = uniprot_aliases(a.aliases)
        aliases_put(a.aliases, names)
        print(f"{a.aliases}: {', '.join(names) or 'no reviewed entry found'}")
    elif a.list:
        for cid in store_ids():
            rec = store_get(cid)
            print(f"{cid}  {rec['year']}  {'OA' if rec['open_access'] else '--'}  "
                  f"{rec['title'][:70]}")
    else:
        ap.print_help()

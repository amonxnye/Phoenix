# Phoenix Research — a governed organization that does science

**Status:** build steps 1–2 **shipped** (`gov/research_world.py`, `gov/researcher.py`,
40 acceptance checks in `gov/verify_research.py`), and the oracle is now pointed at the
**real biomedical literature** (`gov/literature.py`, Europe PMC + UniProt, 36 further
checks in `gov/verify_literature.py`) — all wired into CI; steps 3–5 design.
The same machinery that governs a settlement and ships code, pointed at **scientific
discovery** — hypothesis generation, literature synthesis, computational validation,
and experiment *design*. The domain is illustrated with drug/therapeutic discovery,
but the harness is domain-agnostic (materials, biology, chemistry, ML research).

The one law survives the translation, and it is *more* important here than anywhere:

> **No autonomous action without an oracle or an undo.**
> In science the oracle is a computational or empirical test; the gate is anything
> that touches the physical world or the public record.

---

## 1. Why governance is the whole point in science

An ungoverned research agent is a bad idea for exactly the reasons Phoenix exists:
it will confidently generate plausible-but-wrong hypotheses, cite papers that don't
say what it claims, and — worst — it has no built-in line between "reason about a
molecule" and "order and synthesize a molecule." Phoenix draws that line as its
central safety model (Article IV), and removes the dangerous capability entirely
rather than policing intent (Article V). A researcher that *cannot* act on the
physical world without a human, and *cannot* claim a result the oracle didn't
confirm, is the only kind worth building.

## 2. The translation table

| Settlement / Code | Research |
|---|---|
| The World (oracle) | A **validation oracle**: computational assays + benchmark datasets + literature evidence |
| Vision ("reach the Castle Age") | A research goal ("find a candidate that binds target X with predicted affinity < N and passes ADMET filters") |
| Progress 0–100% | % of the goal's acceptance criteria a candidate/hypothesis satisfies |
| Villager gathering | A researcher agent proposing and **computationally testing** a hypothesis |
| Structures / developments | Reusable assets the org builds: validated assays, curated datasets, literature maps |
| The irreversible gate (Age-up) | **Anything physical or public**: ordering compounds, wet-lab protocols, publishing, patent filing |
| Careers, lineage, skills | Identical — every hypothesis traces to its evidence and its author |

## 3. The oracle — where a result becomes real (not claimed)

Nothing counts until the oracle scores it. Candidate oracles, cheapest first:

1. **Literature-grounded claims** — a hypothesis must cite retrievable sources; a
   verifier agent checks each citation actually supports the claim (the same
   adversarial-verify pattern Phoenix uses for bugs). Unsupported → rejected, logged.
2. **Reproduce a known benchmark** — the researcher's method must first re-derive an
   established result from public data before its novel claims are trusted (like the
   worker must not break passing tests).
3. **Computational assays** — molecular docking scores, ADMET/tox *prediction*,
   physics/statistics simulations, held-out dataset performance. Deterministic given
   inputs, cheap, instant — a true oracle.
4. **Provenance-checked synthesis of the argument** — the lineage engine (already
   built) makes every conclusion walk back to the evidence that produced it and
   forward to what it enabled: `why(candidate)` and `credit(hypothesis)`.

Crucially, **the oracle is computational and evidentiary — never the physical world.**
The org discovers *on paper and in silico*; reality is on the far side of the gate.

## 4. The gate — the safety boundary, enforced not promised

Reversible (runs free): reading literature, computing, simulating, ranking candidates,
writing up a proposal, critiquing another agent's hypothesis.

Irreversible (stops at a human, Article IV): **ordering any physical reagent,
generating a wet-lab or synthesis protocol for execution, contacting a lab or CRO,
publishing, filing IP, or exporting a candidate dossier.** Article V is literal here:
the sandbox has **no procurement credentials, no lab actuators, no network to ordering
systems** — the researcher agent *cannot* buy or make anything even if it wanted to.
A human domain expert reviews the dossier at the gate, with the full lineage of how
the candidate was reached.

### Safety scope (explicit, non-negotiable)
This harness is for **therapeutic and beneficial discovery under human oversight**.
It operates on literature, public datasets, and predictive models. It does **not**
design, optimize, or provide synthesis routes for hazardous, toxic, or weaponizable
agents; the gate and the credential-free sandbox are the enforcement, and any goal of
that shape is refused at the Vision layer (Article I: only the human adopts a Vision,
and the board may refuse to serve one). Governance is what makes autonomous research
*safer* than a lone agent, not more dangerous.

## 5. What transfers with zero changes (~85%)

The anchor (permanent memory of every hypothesis, its evidence, its outcome), the
board (three reviewers with disjoint evidence — novelty, rigor, feasibility — voting
on what to pursue), the governor's scored progress reports, Article IX liveness (a
research org that stalls names its blocker), the eval harness (which model does the
best *science* under governance — a benchmark that would matter enormously), the
lineage engine (provenance *is* the scientific method), careers, and the console.

## 6. What gets built (the new ~15%)

- **[built]** `research_world.py` — the validation oracle: the citation checker, the
  benchmark reproduction, and the computational assay; returns a score.
- **[built]** `researcher.py` — one hypothesis cycle: read the goal + prior results →
  propose a hypothesis with cited evidence → compute → oracle scores it → contribution
  = novel, verified, reproducible advance. Feeds the same economy and careers.
- **[built]** The **dossier gate**: a candidate that clears the oracle is packaged
  (claim, evidence chain, computed scores, limitations) and parked at the human gate —
  never auto-published, never auto-ordered.
- Oracle adapters per field (docking, ADMET, materials sim, ML-benchmark) — the toy
  adapter is in `sandbox/corpus/assays.json`; the seam is `research_world.assay`.
- **[built]** `literature.py` — the real biomedical literature as evidence: Europe PMC
  retrieval, UniProt aliases, verbatim quote checking and sentence-level support
  (§8 below).

### How the oracle actually rules (the part worth stealing)

A claim is a triple with citations. The corpus is eight toy papers of findings.

- A citation that doesn't resolve, or a claim no cited finding supports → **rejected**.
- A claim its own citation *contradicts* → **rejected**, naming the paper.
- A claim that restates a finding verbatim → **verified but known**. Pays nothing:
  a restatement is not an advance.
- A claim one composed step from two cited findings, **with the direction of effect
  right** → **novel**. This is the only thing that earns contribution.
- The same inference with the direction wrong → **sign-error**, named as such. Two hops
  of evidence give a *net regulatory effect*, never a direct interaction: inhibiting an
  activator downregulates what it activated, and claiming it "inhibits" it is the
  commonest way a plausible hypothesis is quietly false. The oracle refuses it.
- A claim already established → **prior art**, credited to whoever got there first.

Above that sit the other two layers: an agent that has not re-derived a published
number is refused novelty outright (the reproduction gate), and a candidate needs a
computed affinity, a clean developability filter **and** a verified claim tying it to
the target — the best-scoring compound in the corpus fails six ADMET filters and is
not a candidate at all. Affinity alone was never the finding.

## 7. Build order

1. **[shipped]** Literature-grounded claim oracle on a tiny toy corpus (needs no wet
   lab, no model even — a citation must resolve and support the claim).
2. **[shipped]** Researcher agent v1 + benchmark-reproduction gate; contribution =
   verified claims; the dossier parks at the human gate.
2b. **[shipped]** The same oracle on the **real biomedical literature** — Europe PMC
   retrieval, quoted evidence checked verbatim, UniProt aliases (§8).
3. Console: a "Lab bench" page — goals, hypotheses, evidence, the dossier gate.
4. A real computational assay adapter (start with open docking / public datasets).
5. The eval race: which frontier model runs the best governed research org.

Each ships alone and is testable, exactly like the settlement and the workspace.

## 8. The real literature (biology and healthcare)

The toy corpus proved the machinery. `gov/literature.py` points the same oracle at
**Europe PMC**, and it exists to kill the one failure mode that makes language models
dangerous in medicine: a fluent claim attached to a citation that does not exist, does
not mention the entities, or says the opposite.

Three things are separated on purpose:

**Retrieval is not the oracle.** Searching and fetching happen in a human-run ingest
CLI, never in an agent's cycle. Records land in `sandbox/evidence/` with their source
URL, licence and retrieval date (Article VI.2). A researcher agent reads that store and
**cannot fetch** — it has no more network than the workspace worker has credentials.

**The oracle is offline and deterministic.** Scoring reads only stored text. The
acceptance suite runs with `urlopen` replaced by something that raises, and passes: no
network, no key, no model. Same claim, same verdict, forever.

**A citation must be quoted, and the quote must be real.** The agent's sentence is
checked verbatim against the stored text — an invented sentence is caught by string
comparison, not by judgement — and then that same sentence must actually assert the
claim. Two independent checks; both must pass.

### What the text checker accepts, and what it refuses

Five grammatical shapes are recognised, because they are the ones biology writes in:

| shape | example |
|---|---|
| active | *metformin delays progression by activating the AMPK/Drp1 pathway* |
| passive | *EGFR was strongly inhibited by osimertinib* |
| agent-noun | *the EGFR inhibitor osimertinib* |
| event-noun | *inhibition of EGFR by osimertinib* |
| compound-agent | *AMPK-mediated suppression of the NLRP3 inflammasome* |

And the refusals matter more than the acceptances:

- **negation** — *"cystamine … did not inhibit LDL oxidation"* contradicts the claim.
- **opposite direction** — a paper reporting inhibition kills a claim of activation.
- **oblique subject** — *"SMK-002 synergized **with** osimertinib and downregulated
  EGFR"* does not assert that osimertinib downregulates EGFR, however true that is
  elsewhere. But *"treatment **with** osimertinib inhibited EGFR"* does: a preposition
  hanging off a noun is agentive, off a verb it is not.
- **clause boundaries** — *"cystamine had no effect …, **but** cystine inhibited …"*
  supports nothing about cystamine.
- **not mentioned** — a paper that never names the entity cannot support a claim about it.
- **paywalled** — no stored text means `no-verifiable-text`, an honest refusal rather
  than a guess.

Aliases come from UniProt, so a paper writing *ERBB1* supports a claim about *EGFR* —
and a short name never matches inside a longer one (*EGFR* is not *ERBB2*).

The checker is deliberately **conservative**. A true claim phrased in a shape it does
not recognise is rejected, and the agent loses a cycle. A false claim that slips
through costs the entire point of the system. Those errors are not symmetric, so the
bias is not either.

### Synthesis, grounded end to end

Novelty over real literature is inference across verified claims, each of which is
itself pinned to a quoted sentence in a real paper:

```
claim:1  metformin activates AMPK    [PMID:41884158, quoted]
claim:2  AMPK inhibits NLRP3         [PMID:42260217, quoted]
⇒ novel: metformin downregulates NLRP3
   and the SAME two steps are refused for "metformin inhibits NLRP3" — two hops give a
   net effect, not a direct interaction
```

Every verified claim keeps its PMIDs and the exact sentence it stands on, so the
dossier a human reviews at the gate walks all the way back to the source.

## 9. Running it

```bash
python3 gov/verify_research.py    # 40 checks — the whole harness, no model needed
python3 gov/verify_literature.py  # 36 checks — the real-literature oracle, offline
python3 gov/research_world.py     # the world state: criteria, candidates, progress
python3 gov/literature.py --list  # the evidence store: what has been retrieved
python3 gov/researcher.py --agent res-01 --cycles 3    # needs a configured brain

# widening what the organization can reason about (a human's step, never an agent's)
python3 gov/literature.py --ingest "AMPK inhibits NLRP3" --limit 3
python3 gov/literature.py --aliases EGFR
```

The verification run redirects the research database to a temp file, so it never
disturbs a live org's memory, and it walks the full arc: every rejection mode, the
reproduction gate, contribution accruing across five cycles, the dossier packaged with
its limitations, an agent refused at the gate and a human deciding it.

# Next — `harness/research`

**Where it stands:** RESEARCH.md defines the design — oracle = computational assays,
benchmark reproduction and citation-checked evidence; gate = anything physical or
public. No code yet.

## Step 1 — the citation oracle (needs no lab, no model, not even an API key)

The cheapest possible start, and it already does something no chatbot does: **a claim
is worth nothing until its citation resolves and supports it.**

- `research_world.py`: a claim record = {statement, citations[], status}.
- `oracle()`: for each citation — does it resolve, and does the quoted passage
  actually contain the supporting text? Unresolvable or unsupported → the claim is
  rejected and logged as waste (Article I.2).
- Toy corpus checked into `sandbox_research/` (a handful of local documents), so the
  suite runs offline and deterministically — the research twin of `calculator.py`.
- **Acceptance:** `verify_research.py` — a supported claim passes, a fabricated
  citation fails, a real citation that doesn't support the claim fails.

## Step 2 — the researcher agent

Read the goal + prior verified claims → propose a hypothesis **with citations** →
oracle scores it → contribution = *verified* claims only. Same economy, careers and
lineage as every other Phoenix agent. Hallucinated citations cost the agent budget and
earn nothing — the strongest anti-hallucination incentive I know of.

## Step 3 — benchmark reproduction

Before a novel claim is trusted, the agent must re-derive a known result from public
data (the analogue of "don't break the passing tests"). Contribution only unlocks
after reproduction passes.

## Step 4 — the dossier gate

A candidate that clears the oracle is packaged — claim, evidence chain, computed
scores, limitations — and parked at the human gate. **Nothing is published, ordered,
or sent.** Article V is literal: no procurement credentials, no lab actuators, no
network to ordering systems; the netns sandbox already enforces the network half.

## Step 5 — a real computational assay

Only after 1–4: one open, deterministic scorer (public docking or a public ML
benchmark) as a second oracle tier.

## Needs from you

- A **domain choice** for the first real oracle — the machinery is identical whether
  it's chemistry, materials, or ML research; pick the one you can judge best.
- Confirmation of the **scope line** in RESEARCH.md §4 (therapeutic/beneficial work
  under human oversight; nothing hazardous or weaponizable, enforced by the gate and
  the credential-free sandbox).

## Definition of "v1 shipped"

An agent proposes ten claims, the oracle rejects the unsupported ones without a human
reading a word, and a human approves one dossier that traces cleanly back to its
evidence.

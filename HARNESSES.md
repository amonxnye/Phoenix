# The Harness Family — one constitution, many domains

Phoenix's governance layer is domain-agnostic. Roughly **85–90%** of the system — the
anchor (permanent memory, careers, lessons), the board with disjoint evidence, the
economy (budgets, promotion, reaping), the lineage engine, Article IX liveness, the
eval harness, the console — transfers unchanged. What changes per domain is small and
always the same two things:

> **What is the cheap oracle?** (the objective thing that scores progress)
> **What is the irreversible act?** (the thing that stops at a human)

Where both have crisp answers, this works. Where they don't, it shouldn't be tried.
That is the whole selection rule.

## Branches

| Branch | Domain | The oracle | The gate | State |
|---|---|---|---|---|
| `main` | The settlement (AoE economy) | game state | advancing the Age | **running live** |
| `main` | Software (toy) | the test suite | (none — sandbox) | **shipped** (`workspace.py`, `worker.py`, `/work`) |
| `harness/builder` | Software at real scale | the repo's CI | **the pull request / merge** | design |
| `harness/research` | Science & discovery, biology & healthcare | computational assays, benchmark reproduction, and **real citations** — Europe PMC evidence, quoted verbatim and checked | anything physical or public (reagents, protocols, publishing, IP) | **shipped** (steps 1–2 + the real-literature oracle: `research_world.py`, `researcher.py`, `literature.py`) |
| `harness/security` | Defensive hardening | a PoC that reproduces in a sandboxed replica | any system outside the sandbox; disclosure; prod deploys | design |
| `harness/quant` | Strategy research | out-of-sample backtest on an unreadable holdout | placing a trade (tacit consent disabled) | design |
| `harness/ops` | Reliability engineering | the SLO recovering and holding | every production mutation | design |
| `harness/data` | Data engineering & analysis | data contracts passing on staging | any production write, deletion, backfill or export | design |

## Candidates not yet branched

Strong fit (the oracle exists, the gate is obvious):
- **Formal verification / theorem proving** — the proof checker is a *perfect* oracle.
- **Hardware & chip design** — simulation and DRC as oracle; the gate is tapeout, the
  most expensive irreversible action in industry.
- **Logistics & supply chain** — network simulation; gate = purchase orders.
- **Energy & grid** — load simulators; gate = physical dispatch.
- **Clinical trial design** — power calculations and protocol simulation; gate =
  enrolling a human being.
- **Legal & compliance analysis** — claims checkable against statute and case law;
  gate = filing or advising.

Softer oracle (governance matters *more*, not less): marketing (A/B measurement;
gate = publishing under your name), support (resolution/CSAT; gate = refunds and
commitments), education (assessment; gate = anything affecting a student's record).

## Where this should not be pointed

Domains where the "oracle" is a person's wellbeing or opinion, and the irreversible
action can't be undone: autonomous weapons, surveillance, medical treatment decisions,
anything where the gate would have to be "trust the model". The honest test is simple:
**if the oracle can be gamed, the agents will game it.** Tests can't be gamed from a
sandbox that cannot edit them; a human approval can't be gamed at all. That is why the
gate is the foundation and not a feature.

## The shared build order (every harness follows it)

1. **A toy world with a real oracle** — no model required to prove the machinery.
2. **One worker agent** through the existing brain seam; contribution = what the
   oracle measures, never what the agent claims.
3. **A console page** for the domain (the settlement, the workboard, the lab bench…).
4. **The dossier gate** — package the irreversible proposal with its evidence and
   lineage, park it for the human, price the wait (IV.4), escalate on silence (IV.6/7).
5. **Board staffing + scored governor reports** — verbatim from the constitution.
6. **The eval race** — which frontier model runs the best governed organization *in
   this domain*. That question is a franchise, not a one-off.

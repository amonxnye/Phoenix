# Next — `harness/logistics`

**Where it stands:** steps 1–4 are code. A deterministic supply network
(`gov/logistics_world.py`), a planner agent that is paid only for what the simulator
measures (`gov/planner.py`), four disruption scenarios, and a purchase-order gate that
no model can walk through — proven by 36 acceptance checks in CI
(`gov/verify_logistics.py`, ~3s, no model and no network required).

```
naive reorder point   fill 94.8%  capital $17,157  waste $3,980  →  score 73.6
after 20 proposals    fill 99.3%  capital $14,903  waste     $0  →  score 91.6
                                            (nominal 100.0, worst case 78.9)
```

## Done

- **Step 1 — the toy network and its oracle.** 4 SKUs × 2 nodes (vendor → DC → store),
  180 days from a fixed seed, FIFO lots with shelf life, storage caps, vendor capacity,
  emergency freight that arrives a day *late*. `oracle()` returns the joint scorecard;
  `demand_history()` refuses the holdout outright.
- **Step 2 — the planner.** Proposes through the one brain seam when a model is
  configured, and through inventory theory plus a coordinate search when one is not, so
  the whole loop is provable with no API key. Contribution = points of joint score won
  on the holdout. `plan-01` made foreman on +717.
- **Step 3 — disruptions.** A closed lane, doubled lead times, a demand spike, a
  capacity cut. The verdict blends the nominal run with the worst of them.
- **Step 4 — the gate.** A dossier carries quantity, vendor, value, the forecast it
  rests on, the rollback (cancellable until when, at what fee), the board's three
  disjoint ballots, and the running price of every day of silence (IV.4). Tacit consent
  (IV.7) reaches only inside a pre-approved envelope the human sets in the environment;
  unset by default, so nothing is ever bought by silence.

## Step 5 — real data, read-only

An adapter for exported demand history, lead times and on-hand positions, replacing
`SKUS` / `NODE_CAPACITY` and nothing else. **Never a live ERP write** — the gate stays
the human, permanently. The first honest question this raises: with real demand, the
train/holdout split becomes a *date*, not a constant, and holdout leakage becomes a
thing to re-prove rather than a thing to assert once.

## Then, in rough order

1. **Tighten the mandate now that it is met.** The nominal holdout is saturated
   (100.0/100) — a scoreboard that cannot tell two good plans apart has stopped being
   an oracle. The Board already proposes a bolder Vision when the goal is met with
   budget to spare (`board.propose_vision`); the same move belongs here: raise the fill
   floor, drop the capital ceiling, or weight robustness harder.
2. **A console page** — the network, the leaderboard of scored policies, and the parked
   dossiers, in the shape the settlement page already has (harness build order §3).
3. **Director integration.** Today `run_search` is the loop. The Director should staff
   planners against the *shortfall the oracle names* — service, capital, or robustness —
   and reap the ones that stop finding anything.
4. **The eval race** (`EVAL.md`): which frontier model runs the best governed planning
   organization. The model-free search is the floor every model must beat; a model that
   cannot is a finding, not a failure.

## Needs from you

- Whether to point step 5 at real exports, and from what.
- The **pre-approved envelope**, if you ever want one: `GOV_LOGISTICS_ENVELOPE` as
  `{"skus": [...], "vendors": [...], "max_value": N}`. My recommendation is still
  **none** — every commitment waits for a human until the simulator has earned trust.

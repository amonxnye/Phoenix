# Next — `harness/logistics`

**Where it stands:** LOGISTICS.md defines the design — oracle = a network simulator
replaying demand the planner never saw; gate = the purchase order. No code yet.

This is the shortest jump in the family: the settlement *already* models stock levels,
storage caps, spoilage, decaying assets, a market that converts surplus, and a balance
sheet. Logistics is that machine pointed at real goods.

## Step 1 — the toy network and its simulator

- `logistics_world.py`: 3–5 SKUs across two nodes, stochastic demand and lead times
  from a **fixed seed**, replayed deterministically.
- `oracle()`: given a policy, simulate a held-out period and return the joint
  scorecard — fill rate, stockouts, working capital, expedite spend, spoilage.
- **Acceptance:** `verify_logistics.py` — a sane reorder-point policy beats a
  do-nothing policy; an absurd policy (order everything) wins on service and *loses*
  on capital, proving the score can't be gamed on one axis.

## Step 2 — the planner agent

Propose a policy → simulate on the holdout → contribution = **service achieved per
unit of working capital**. Same economy, careers, and lineage as every other agent.
The holdout is unreadable to the planner (the quant harness's discipline; forecasting
invites the same overfitting).

## Step 3 — disruption scenarios

A second oracle tier: a lane closes, a lead time doubles, demand spikes. Robustness
scored alongside averages — the settlement's raid mechanic, wearing a suit.

## Step 4 — the commitment gate

A purchase/dispatch dossier: quantity, vendor, cost, the forecast it rests on, and the
**rollback** (can it be cancelled, by when, at what fee). Parked for a human. No ERP
or procurement credentials exist in the sandbox, so nothing can be ordered even by a
model that decides it should.

## Step 5 — real data, read-only

Adapter for exported demand history, lead times and on-hand positions. **Never a live
ERP write** — the gate stays the human, permanently.

## Needs from you

- The **toy network shape** for step 1 (how many SKUs and nodes — smaller is better).
- The **pre-approved envelope**, if any: which routine reorders may ever proceed on
  silence under Article IV.7. My recommendation for v1: **none**. Every commitment
  waits for a human until the simulator has earned trust.

## Definition of "v1 shipped"

Twenty policies proposed and scored on held-out demand *and* disruption scenarios, the
best beating a naive reorder-point baseline on both service and capital — and not one
simulated order becoming a real one without a human click.

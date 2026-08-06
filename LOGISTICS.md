# Phoenix Logistics — a governed organization that moves things

**Status:** design. The constitution pointed at **supply chain and logistics**:
planning replenishment, routing, allocation and capacity — proposing decisions a human
commits to.

> **The oracle is the simulator; the gate is the purchase order.**

Of all the harnesses, this one is the closest cousin to the settlement itself. Phoenix
already runs an economy with stock levels, storage caps, spoilage, decaying assets,
a market that converts surplus, and a balance sheet. Logistics is that same machine
pointed at real goods.

---

## 1. The translation table (the shortest one in the family)

| Settlement | Logistics |
|---|---|
| The World (oracle) | A **network simulator** over real history: demand, lead times, capacity, cost |
| Vision | "98% fill rate at ≤ X working capital, zero stockouts on the A-list" |
| Progress 0–100% | % of the service/cost mandate met in simulation on held-out periods |
| Resources (food/wood/gold) | SKUs, inventory positions, cash |
| Gathering | A planner agent proposing one replenishment/routing/allocation decision |
| Structures & developments | Warehouses, lanes, contracts, safety-stock policies |
| **Decay and spoilage** | Literally perishability, obsolescence and shrinkage — already modelled |
| **The market (surplus → gold)** | Literally markdown, transfer, or liquidation of excess |
| Compute cap | Planning budget; also the real spend cap the board guards |
| The gate | **The purchase order, the dispatch, the contract commitment** |
| Lineage | Why we hold this stock, under whose approval, from which forecast |

## 2. The oracle — simulate on history the planner cannot see

1. **Backtest against held-out periods.** A policy is scored by replaying real demand
   the planner never saw — the same unreadable-holdout discipline as the quant harness,
   because forecasting invites the same overfitting.
2. **Service *and* cost, jointly.** A policy that hits 100% fill by drowning in stock
   is a failure; the scorecard carries fill rate, stockouts, working capital, expedite
   spend, and waste (spoilage/obsolescence) together.
3. **Stress, not just averages.** Disruption scenarios (a lane closes, a lead time
   doubles, demand spikes) are part of the score, so plans are judged on robustness —
   the settlement's raid mechanic, wearing a suit.

## 3. The gate

Reversible (runs free): forecasting, simulating, re-planning, comparing policies,
drafting a purchase plan, what-if analysis.

Irreversible (human, Article IV): **issuing a purchase order, dispatching a shipment,
committing to a contract or a rate, releasing allocation to customers, scrapping or
marking down stock.** Money leaves and goods move — there is no undo. Article V is
literal: no ERP/procurement credentials in the sandbox, so the planner *cannot* place
an order even if a model decides it should. Tacit consent (IV.7) may cover *routine
reorders inside a pre-approved envelope* (SKU list, value ceiling, approved vendors)
and nothing else — the envelope is the human's to set, and it is checked in code.

## 4. Why governance is the whole value here

Planning software has existed for decades; what it lacks is an accountable planner.
Phoenix adds:

- **A board with disjoint evidence on every commitment** — cash and working capital
  (Ledger), service level and demand risk (Growth), supplier/lane concentration and
  disruption exposure (Prudence). That is a sourcing committee that reads the data.
- **Provenance for every position**: *why do we hold 40 weeks of this SKU?* walks back
  to the forecast, the policy, the lesson, and the human who approved it. Try getting
  that answer from a spreadsheet three quarters later.
- **Decay economics that already exist** — perishability and obsolescence are the
  settlement's spoilage mechanic, and deferred maintenance is a liability on the
  balance sheet Phoenix already prints.
- **Liveness (IX)**: a planning org that goes quiet during a disruption is the worst
  failure mode in the domain; a stall must name its binding constraint — including
  when that constraint is an unanswered approval.

## 5. Build order

1. **Toy network + simulator oracle**: a few SKUs, two nodes, stochastic demand and
   lead times, replayed from a fixed seed — the logistics twin of `calculator.py`.
   No cloud, no ERP, deterministic.
2. **Planner agent v1**: propose a replenishment policy → simulate on held-out demand
   → contribution = service achieved per unit of working capital. Same economy, same
   careers, same lineage.
3. **Disruption scenarios** as a second oracle tier (robustness, not just averages).
4. **The commitment gate**: a purchase/dispatch dossier — quantity, vendor, cost,
   the forecast it rests on, the rollback (can it be cancelled? by when?) — parked for
   a human, with the pre-approved envelope as the only tacit-consent path.
5. **Real data adapter** (read-only exports first: demand history, lead times, on-hand)
   — never a live ERP write.

## 6. Needs from the operator

- A **toy network shape** to start (how many SKUs/nodes — smaller is better).
- The **pre-approved envelope**, if any: which reorders may ever proceed on silence.
  My recommendation for v1: none. Every commitment waits for a human until the
  simulator has earned trust.

## 7. The honest line

This harness plans and proposes; a human commits. It holds no ERP credentials, issues
no orders, and its safety is structural rather than behavioural. Simulated performance
is not guaranteed performance — the oracle scores decisions against history and
scenarios, which is a much smaller promise than predicting the world.

## Definition of "v1 shipped"

On the toy network: twenty policies proposed, scored on held-out demand *and*
disruption scenarios, the best one beating a naive reorder-point baseline on both
service and capital — and not a single simulated order becoming a real one without a
human click.

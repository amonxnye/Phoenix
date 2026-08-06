# Phoenix Logistics — a governed organization that moves things

**Status: v1 shipped.** The constitution pointed at **supply chain and logistics**:
planning replenishment, routing, allocation and capacity — proposing decisions a human
commits to. Steps 1–4 of the build order are code, with three suites in CI and no model, network or API key needed by any of them:
**107 unit tests**, **50 acceptance checks**, and an **end-to-end smoke test that
measures the agents** (`test_logistics.py`, `verify_logistics.py`,
`smoke_logistics.py` — about 25 seconds together).

> **The oracle is the simulator; the gate is the purchase order.**

| | |
|---|---|
| The world | `gov/logistics_world.py` — 4 SKUs × 2 nodes, 180 days, one fixed seed |
| The agent | `gov/planner.py` — propose a policy, get scored on demand it never saw |
| The console | `gov/logistics_console.py` + `phoenix-command.html` — the surface a planner works in |
| The proof | `test_logistics.py` (107 unit tests) · `verify_logistics.py` (50 acceptance checks) · `smoke_logistics.py` (end-to-end, with agent performance) |
| Try it | `python3 gov/logistics_console.py --seed` → <http://127.0.0.1:8790> |

### The console

The page answers the four questions the domain asks, and nothing else: **are we meeting
the mandate** (fill, capital, waste, freight — each against its target, from the oracle,
never from an agent's report), **what is the plan** (every SKU's reorder point and
order-up-to level at both nodes), **would it survive a bad week** (the same plan under
four disruptions), and **what needs me** (purchase orders parked at the gate, priced by
what the wait is costing, with the board's ballots attached).

Planning runs from a button because it is reversible. The one irreversible act waits for
the person reading the page — and even their approval does not place an order. It records
a decision for them to take to their own system; `place_order` refuses always, and the
page has a link that makes it refuse in front of you.

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

1. ✅ **Toy network + simulator oracle**: 4 SKUs across 2 nodes (vendor → DC → store),
   stochastic demand and lead times replayed from a fixed seed — the logistics twin of
   `calculator.py`. No cloud, no ERP, no model, deterministic to the cent.
2. ✅ **Planner agent v1**: propose a replenishment policy → simulate on held-out demand
   → contribution = points of the joint service-and-capital score won. Same economy,
   same careers, same lineage. `plan-01` earned foreman on measured service.
3. ✅ **Disruption scenarios** as a second oracle tier — four of them, and the verdict
   blends the nominal run with the *worst* one (robustness, not just averages).
4. ✅ **The commitment gate**: a purchase dossier — quantity, vendor, cost, the forecast
   it rests on, the rollback (cancellable until when, at what fee) and the price of
   each day of silence — parked for a human, with the pre-approved envelope as the only
   tacit-consent path.
5. ⬜ **Real data adapter** (read-only exports first: demand history, lead times,
   on-hand) — never a live ERP write.

### What the oracle says (all figures from `gov/verify_logistics.py`, reproducible)

| Policy | Blended | Nominal | Worst case | Fill | Working capital | Waste | Freight |
|---|---|---|---|---|---|---|---|
| do nothing | 38.5 | 38.5 | 38.5 | 60.2% | $282 | $0 | $15,162 |
| naive reorder point (the baseline) | 73.6 | 75.9 | 70.2 | 94.8% | $17,157 | $3,980 | $2,097 |
| order everything (the absurd one) | 62.0 | 66.1 | 55.9 | **100.0%** | $110,332 | $35,928 | $0 |
| **the planner, after 20 proposals** | **91.1** | **100.0** | **77.8** | **98.7%** | **$14,558** | **$0** | **$776** |

Read the third row: the absurd policy *wins outright on service* and still loses,
because capital and waste are on the same scorecard. That is the whole anti-gaming
argument, and it is an assertion in CI rather than a claim in a README.

Read the fourth row: the nominal holdout is now **saturated** (100.0) while the worst
disruption sits at 77.8. All the remaining headroom is robustness — which is exactly
where a supply chain's remaining headroom actually is, and the planner says so itself:
every run ends by naming its binding constraint (Article IX).

### And what the agents cost

The smoke test reports the fleet the way the settlement's production audit reported
its own, because those turned out to be the numbers that mattered:

| | |
|---|---|
| proposals that moved the plan | 5 of 20 — **25% liveness** |
| rejected per adoption | 3.0 : 1 |
| score points won | **+17.5** on held-out demand |
| points per second of simulation | 8.1 |
| simulations run | 100 (every proposal against every disruption) |
| throughput | **46 simulations/second**, 0.11s per proposal |
| read path | p95 **158 ms** |

Those floors are asserted, not just printed: a run that clears every stage but leaves
the fleet having improved nothing exits non-zero.

## 6. What the operator still sets

- The **toy network shape** is set for v1 (4 SKUs × 2 nodes). A real shape replaces
  `SKUS` / `NODE_CAPACITY` and nothing else.
- The **pre-approved envelope**: `GOV_LOGISTICS_ENVELOPE` (SKU list, vendor list, value
  ceiling). **Unset by default, and unset is the recommendation** — every commitment
  waits for a human until the simulator has earned trust. Tacit consent (IV.7) reaches
  nothing outside it, however long the silence runs; the acceptance suite asserts that.

### One thing the simulator refused to agree with

§1 asked for "zero stockouts on the A-list". The simulator prices that at a safety
factor of z≈4 and ~$21,500 of working capital — 50% over any ceiling worth the name.
A mandate that cannot be met at any affordable capital is a slogan, so the A-list
clause shipped as a **99% fill floor**: demanding, reachable, and met. The raw stockout
count stays on the scorecard, because that is the number a human asks about first.
This is the oracle doing its job on the *goal*, not just on the agents.

## 7. The honest line

This harness plans and proposes; a human commits. It holds no ERP credentials, issues
no orders, and its safety is structural rather than behavioural. Simulated performance
is not guaranteed performance — the oracle scores decisions against history and
scenarios, which is a much smaller promise than predicting the world.

And the network it is proven on is a toy: 4 SKUs, 2 nodes, synthetic demand from a
seed. What is proven is the *machinery* — the holdout discipline, the joint score, the
robustness tier, the economy, the dossier gate — not a forecast of anyone's business.
Step 5 is where it meets real numbers, read-only.

## Definition of "v1 shipped" — met

> On the toy network: twenty policies proposed, scored on held-out demand *and*
> disruption scenarios, the best one beating a naive reorder-point baseline on both
> service and capital — and not a single simulated order becoming a real one without a
> human click.

All four clauses are assertions in `gov/verify_logistics.py`:

- 20 policies proposed and scored (§5 of the suite), 6 adopted, 14 rejected;
- the best beats the baseline on **service** (94.8% → 99.3%) *and* on **capital**
  ($17,157 → $14,903), and still scores 78.9 on its worst disruption;
- `place_order()` refuses even a commitment the human has already approved, because
  `PROCUREMENT_ADAPTER is None` and there is no code path that could set it.

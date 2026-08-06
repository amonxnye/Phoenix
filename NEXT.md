# Next — `harness/logistics`

**Where it stands:** steps 1–4 are code, and so is the console. A deterministic supply
network (`gov/logistics_world.py`), a planner agent that is paid only for what the
simulator measures (`gov/planner.py`), four disruption scenarios, a purchase-order gate
that no model can walk through, and an operator surface a planner can actually work in
(`gov/logistics_console.py` + `phoenix-command.html`) — proven by three suites in CI, none of
which needs a model, a network or an API key: **107 unit tests** (`test_logistics.py`),
**50 acceptance checks** (`verify_logistics.py`) and an **end-to-end smoke test that
measures the agents and fails if they underperform** (`smoke_logistics.py`).

```
naive reorder point   fill 94.8%  capital $17,157  waste $3,980  →  score 73.6
after 20 proposals    fill 98.7%  capital $14,558  waste     $0  →  score 91.1
                                            (nominal 100.0, worst case 77.8)

5 of 20 proposals moved the plan · 3.0 rejected per adoption · +17.5 points won
46 simulations/second · 0.11s per proposal · read path p95 158ms
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
- **Tests, at three levels.** 107 unit tests (one behaviour per function, hermetic),
  50 acceptance checks (the constitution's claims, in the language a human reads), and
  a smoke test that boots the real console on a real socket, drives the whole visitor
  journey, and reports the fleet's performance against floors it must clear to exit 0.
  Writing them found four real defects: a safety factor could push a perishable's
  reorder point past its shelf life, `rounds: 0` silently became the default, reported
  lead-time demand did not reconcile with the mean it was derived from, and one
  disruption scenario was decoration — halving vendor capacity left a tuned plan
  completely untouched.
- **Multi-user, no accounts.** Everyone is a guest with a derived, stable handle that
  signs everything they do. One planning run at a time (a non-blocking lock), a
  per-guest cooldown, and a compare-and-set gate so of two people deciding the same
  dossier only the first lands and the second is told.
- **The console** (build order §3, brought forward). `gov/logistics_console.py` serves
  `phoenix-command.html` — the mandate as four meters, the plan per SKU, the four
  disruption scores, and the gate queue with approve/reject. Planning runs from a
  button; the purchase order does not. Spectating is free, acting needs
  `CONSOLE_TOKEN`, and the page has a link that makes the procurement guard refuse in
  front of you.

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
2. **Director integration.** Today `run_search` is the loop. The Director should staff
   planners against the *shortfall the oracle names* — service, capital, or robustness —
   and reap the ones that stop finding anything.
3. **The eval race** (`EVAL.md`): which frontier model runs the best governed planning
   organization. The model-free search is the floor every model must beat; a model that
   cannot is a finding, not a failure.

## Needs from you

- Whether to point step 5 at real exports, and from what.
- The **pre-approved envelope**, if you ever want one: `GOV_LOGISTICS_ENVELOPE` as
  `{"skus": [...], "vendors": [...], "max_value": N}`. My recommendation is still
  **none** — every commitment waits for a human until the simulator has earned trust.

# World Dynamics — decay, assets & liabilities, threats & defence

**Status:** ideas → design. Requested study: should developments decay? What are the
settlement's assets and liabilities? Who attacks us, and what defends us? Nothing here
is built yet — this is the menu to choose from.

---

## 1. Why decay matters

Today a development is a one-time purchase with a permanent buff. That has two bad
consequences:

1. **No upkeep pressure** — once built, a structure is free forever, so the optimal
   strategy is "build everything once, then park". Real economies (and real software
   estates) don't work like that: everything you own costs you something to keep.
2. **No portfolio decisions** — with no carrying cost there is never a reason to
   *retire* a development, so the board never has to weigh keep-vs-drop. The most
   interesting governance decisions are exactly those.

Decay turns every asset into a recurring decision — which is what a governed
organisation is *for*.

## 2. Assets and liabilities — the settlement's balance sheet

Reframe what we already have in accounting terms:

| Item | Class | Why |
|---|---|---|
| Resource stockpiles (food/wood/gold) | **Current assets** | liquid, spendable now |
| Structures & developments | **Fixed assets** | produce yield, but (with decay) carry upkeep |
| Skills & lessons (anchor) | **Intangible assets** | raise future returns, zero upkeep |
| Agents (villagers/foremen/delegates) | **Assets AND liabilities** | produce contribution, but burn compute every turn |
| Compute spent vs cap | **Liability / burn** | the one truly scarce input |
| Un-actioned human gates (a parked herald) | **Hidden liability** | frozen progress — we just saw a 600-turn stall |
| Disrepair (decayed structures) | **Liability** | lost yield until repaired |

A tiny `balance_sheet()` in the console — assets on one side, upkeep+burn on the
other, one net line — would make the whole economy legible at a glance and give the
board a real number to argue about ("net worth fell three turns running" is a better
trigger for a retrospective than a timer).

## 3. Decay & maintenance — the mechanic (small, buildable)

- Every structure/development gets `condition` (100 → 0) and a per-N-turns
  `decay_rate` by rank (grander = costlier to keep: rank 2 decays slow, rank 4 fast).
- **Effect scales with condition**: a mill at 60% condition gives 60% of its bonus.
  No cliff, no sudden breakage — smooth, legible pressure.
- **Repair is a work order**: a villager turn + resources (some fraction of build
  cost) restores condition. Repairing competes with gathering — a real allocation
  decision for the director, and a new `career` event ("repaired the mill").
- **Abandonment is a board decision**: if upkeep of a low-value development exceeds
  its yield, the board may vote to let it fall — recorded, reasoned, reversible only
  by rebuilding. (Adopt/abandon stays human-gated above a cost threshold.)
- The balance sheet makes the loop visible: deferred maintenance shows up as a
  growing liability, not a silent nerf.

## 4. Threats — who attacks an agent settlement?

Two layers, and both are worth simulating because they map to real agent-org risk:

**In-world (game-flavoured) threats**
- **Raiders** — periodic events that steal a % of the *largest* stockpile. Punishes
  exactly the lopsided hoarding we just fixed; rewards balance and storage tech.
- **Rot/blight** — perishability: food above a granary-set cap spoils each turn.
  (A 125,000-food treasury should never happen again — it would rot.)
- **Sabotage** — a raid that damages a structure's condition instead of stealing;
  makes the repair economy matter.
- **Siege** — a rare, telegraphed big raid ("scouts report movement — 10 turns'
  warning") so defence is a *preparation* decision, not a dice roll.

**System-level threats (the real ones, simulated in-world)**
- **Token-drain attack** = an agent stuck in a burn loop (we already reap by budget —
  that IS a defence; name it in the threat model).
- **Prompt-injection / bad knowledge** = poisoned facts entering via `ingest()` —
  the disputed-knowledge flag in LINEAGE.md §4.4 is the planned immune response.
- **Rogue operator command** = the human gate and board quorum already defend this.

## 5. Defence — military as a budget line, not a new game

Keep it economy-shaped (no unit micro, no combat sim):

- **Militia** — a new agent role: costs food upkeep every turn, gathers nothing,
  contributes `defence` points. The first agent class that is a *pure liability in
  peacetime* — which is the entire point: how much insurance does the board buy?
- **Walls / watchtower** — rank-3/4 developments contributing static defence and
  decaying like everything else (an unmaintained wall is theatre).
- **Resolution rule, dead simple**: raid strength vs defence points → fraction of
  losses avoided. Fully deterministic given the event; auditable in one line.
- **Standing-army ratio is a board power**: "keep defence ≥ X% of net worth" is a
  policy the board votes on and the ledger enforces — token-maxing-style power,
  quorum-gated, so militarism can't eat the treasury unsupervised.
- **After-action retrospective**: every raid triggers a retrospective, so defence
  lessons enter the permanent skill memory ("walls at 40% condition cost us 800
  food — repair before winter").

## 6. Suggested build order (each step ships alone)

1. **Balance sheet panel** — pure read of existing data, immediate insight, no rules
   change. (smallest)
2. **Condition + decay + repair orders** on structures; effect scales with condition.
3. **Food spoilage cap** (granary sets the ceiling) — kills treasury-hoarding for good.
4. **Raid events + defence points** (militia role + watchtower), telegraphed sieges.
5. **Board policy: defence ratio**; after-action retrospectives feed skill memory.

Steps 1–3 are pure economy and cheap. Steps 4–5 introduce the first adversarial
pressure — worth doing only after 1–3 make the costs visible.

# Phoenix

**A self-directing agent organization that is safe because it is governed, not because it is trusted.**

Phoenix runs a fleet of LLM agents as an *organization*: it has one measurable goal, a
board that approves spending, a ledger that retires agents when their budget runs out,
permanent memory that outlives every agent, and a human gate in front of anything
irreversible. The rules are not a prompt — they are a constitution, and every article
names the code that enforces it.

> ## The two laws
> **No autonomous action without an oracle or an undo.**
> **No indefinite inaction without an escalation.**
>
> If a goal can't be scored, it can't be pursued. If an action can't be undone, a
> human decides it. And a system that quietly stops is a failure the constitution
> names, prices, and escalates. Everything else in Phoenix is those two sentences,
> applied.

---

## The problem

Multi-agent systems fail in a specific, boring way: they converge on talking.
Deliberation is cheap, unbounded, and looks like progress. Action is gated behind
budgets, approvals, and side effects. A system with that asymmetry finds the same
equilibrium every time — a fleet that reaches consensus, files reports, scores itself
well, and produces nothing.

Phoenix is an attempt to make that failure *impossible by construction* rather than
*unlikely by prompting*. (It found that equilibrium once, in its own logs — see
[What the data shows](#what-the-data-shows) — and the second law exists because of it.)

---

## Vision

**An organization of agents you can point at real work and leave running.**

The settlement — villagers, resources, ages — is a testbed with a cheap oracle, not
the destination. The destination is a fleet that can be handed a real objective,
given a compute budget, and trusted to work unattended overnight because:

- it cannot exceed its budget,
- it cannot take an irreversible action without you,
- it cannot quietly stop,
- and it can tell you exactly what is blocking it.

The economic simulation exists because progress toward "reach the Castle Age" is
objective, instant, and free to read. That property — a **cheap oracle** — is what
makes governance testable at all. Swap the oracle for a test suite and the same
machinery runs against real software: that adapter is **already built**
(`gov/workspace.py` — the test suite is the oracle, failing tests are the open
tasks). The first sandboxed code task has already landed and been paid for:

```
dev-01 patched calculator.py — 6 tests newly green, suite 9/9,
+600 measured contribution → promoted to foreman
```

---

## How it works

```
                 ┌──────────────┐
   human ───────►│    VISION    │  one 0–100% score, the oracle
                 └──────┬───────┘
                        │ re-brief
                 ┌──────▼───────┐
                 │   DIRECTOR   │  staffs, re-tasks, reaps
                 └──────┬───────┘
              board ◄───┤          quorum required to spawn
           (Prudence,   │
            Growth,     ▼
            Ledger)  ┌──────────┐
                     │  AGENTS  │  villager → foreman → delegate
                     └────┬─────┘
                          │ reversible work runs free
                 ┌────────▼────────┐
                 │      WORLD      │  gather · build · trade · repair
                 │  or WORKSPACE   │  read task · patch · run tests
                 └────────┬────────┘
                          │ irreversible?
                 ┌────────▼────────┐
                 │      GATE       │──► waits for the human (priced, bounded)
                 └─────────────────┘
   governor ────────────────────────►  caps, liveness, escalation
   anchor   ────────────────────────►  lessons, careers, lineage — permanent
```

Each turn: the Director reads the Vision, staffs or re-tasks agents against the
shortfall, the Board votes on anything that could run away, agents do reversible
work, and the liveness invariant checks that something actually happened. Anything
irreversible parks at the gate — priced by its cost of waiting, escalated on a fresh
channel if ignored, and stood down by timeout ("taken by timeout", never "approved").

---

## The Constitution

The constitution is the primary artifact in this repository
([`CONSTITUTION.md`](CONSTITUTION.md)). It is not documentation of the code — it *is*
the specification, and every article names its enforcing module.

| Article | Rule | Code |
|---|---|---|
| **I** | The Vision governs. One goal, one score, one oracle. Only the human adopts; a met vision is consumed, never re-won. | `vision.py`, `sim_console.py` |
| **II** | Agents are born by quorum, promoted on merit, retired *before* the overshooting spend. No immortals, no zombies, never reaped to empty. | `economy.py`, `sim_console.py` |
| **III** | Limits are read before acting. The cap has one writer; a blocked action escalates **once**, naming the value, then mutes until it changes. | `governor.py`, `sim_console._breaker` |
| **IV** | Irreversible actions stop at the gate. The wait is priced, re-routed if unanswered, and bounded by timeout. Reversible work never pauses. | `sim.py` interrupt, `sim_console.py` |
| **V** | Remove the capability, don't police it. Workers run with no credentials, no network — and cannot edit the tests that judge them. | `workspace.py` |
| **VI** | Knowledge grows, carries sources, dedups, **expires**, and revives on re-confirmation. Lessons pay net of waste. | `anchor.py` |
| **VII** | Nothing runs unseen. Every decision carries its why, inputs, authorizer, and measured outcome, traceable both ways. | `anchor.py` (decisions, `caused_by`) |
| **VIII** | Runaway powers go to a Board with **disjoint evidence per seat** and a duty to escalate what it blocks. The Governor is scored on vision points per compute — zero movement caps at 3/10. | `board.py`, `sim_console._governor_report` |
| **IX** | Inaction is an action, and it is gated too. Ten dead turns or a frozen score is a stall that names its binding constraint and escalates. | `sim_console.py` liveness |

### Amending it

> **Change the rules by changing the code they name, and update the article.**
> **A rule with no enforcing code is not a rule — it's a wish.**
> Keep them the same file, the same commit.

This is the contribution bar. A PR that adds a constraint in prose without the
assertion that enforces it will be asked for the assertion.

---

## Quickstart

```bash
git clone https://github.com/amonxnye/Phoenix.git
cd Phoenix
pip install -r requirements.txt

# the acceptance suites (CI runs all four on every push)
python3 gov/verify.py          # 16 — runtime, gate, governor, durability
python3 gov/verify_sim.py      # 36 — economy, upkeep, permanence, lineage, governance
python3 gov/verify_work.py     # 12 — real-work oracle, sandbox guards, worker loop
python3 gov/console_smoke.py   # headless console API smoke

# the live console
python3 gov/sim_console.py --seed     # → http://127.0.0.1:8788
```

No key needed — a rule-based brain runs everything. To bring the organization to
life, configure ONE provider; every model call in the system flows through a single
instrumented seam (`gov/brain.py`) that logs real tokens, latency, and errors:

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | simplest path — DeepSeek's OpenAI-compatible API |
| `BRAIN_BASE_URL` / `BRAIN_API_KEY` / `BRAIN_MODEL` | any OpenAI-compatible endpoint, or native Anthropic |
| `GOV_DATA_DIR` | durable volume (e.g. `/data`) — memory survives redeploys |
| `CONSOLE_TOKEN` | locks every mutating endpoint; pages stay readable |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` | deep traces via LangSmith (Article VII) |

Deploying: [`DEPLOY.md`](DEPLOY.md) — a `Procfile` is included; the console reads
`$PORT` and binds `0.0.0.0`.

### The console

| Page | What you govern there |
|---|---|
| `/` | Vision & progress, world meters, **balance sheet** (assets, disrepair, net worth), the human gate, development tree, board proposals, permanent event log |
| `/agents` | System health (stalls, burn, value per 1k compute), per-agent vitals & token math, gated Terminate, and the **Hall of Records** — every agent ever, permanently, with downloadable careers & health telemetry |
| `/work` | **Real work**: the test suite as a progress bar, tasks derived from failing tests, a button that sends a live agent to fix the code — oracle-scored, auto-reverting |
| `/chats` | Talk to any agent, the board, or the Chief Governor; watch votes narrated with live evidence (runway, momentum, affordability) |
| `/skills` | Lessons across generations, the capability ladder, and every decision's reasoning with a **trace-lineage** link (why ⇠ inputs, credit ⇢ outcomes) |
| `/leaderboard` | The **Phoenix Eval** — same world, different frontier models, scorecards side by side |
| `/logs` | The permanent log — filter by kind/text/time, export txt·csv·jsonl |
| `/rules` | The constitution (editable — only the human adopts), era prices, tiers |

---

## What the data shows

Phoenix is instrumented well enough to audit itself. From a 19.6-hour production run
(33,589 events, 5,311 turns, 381 agents), before and after Article IX and the
evaluation rewrite landed mid-run:

| Metric | Before | After |
|---|---|---|
| Turns that changed world state | 21% | **100%** |
| Talk events : action events | 3.78 : 1 | **0.32 : 1** |
| Contribution per 1k tokens | ~10.7 | **75.8** |
| Reaps respecting the budget ceiling | 0 / 31 | **306 / 306** |
| Reports naming the binding constraint | none | **all** |

Article IX detects a stall, identifies the binding constraint, and escalates —
including when the binding constraint is the human:

```
STALL (Article IX) — vision score frozen for 25 turns;
binding constraint: the human gate — a herald awaits your decision on the console
```

Those before-numbers are the "converge on talking" equilibrium from
[The problem](#the-problem), observed in this system's own logs. The constitution's
second law is the direct product of that audit.

---

## The Phoenix Eval

Because the rules are code and the brain is one env var, Phoenix doubles as a
benchmark no leaderboard covers: **can your model run an organisation?** Economy,
governance discipline, learning across generations, and real token cost — measured
against an objective oracle with an identical auto-human for every model.

```bash
python3 gov/evalrun.py --turns 120 --fresh --label my-model
```

Scorecards store permanently and rank at `/leaderboard`. Design: [`EVAL.md`](EVAL.md).

---

## Architecture

| Module | Responsibility |
|---|---|
| `vision.py` | the goal, the 0–100% scorecard, the oracle |
| `director.py` | staffing, re-tasking, reaping against the Vision shortfall |
| `board.py` | Prudence / Growth / Ledger — disjoint evidence, quorum, degeneracy detection |
| `governor.py` | the read view over the checkpointer, the spend cap |
| `economy.py` | budgets, tiers, promotion, the ledger |
| `sim.py` | the settlement — resources, builds, decay, trade, era pricing, the gate |
| `workspace.py` | the real-work world — the test-suite oracle, tasks, the closed sandbox |
| `worker.py` | the coding agent — patch, test, get paid for green |
| `anchor.py` | permanent memory — lessons, careers, lineage, telemetry, the event log |
| `brain.py` | the ONE model seam — any provider, every call cost-logged |
| `evalrun.py` | headless reproducible runs → scorecards |
| `sim_console.py` | the operator console and API surface |

The architecture bet: the LangGraph checkpointer database **is** the backend — the
governor is a read view plus a resume call, the console is stdlib HTTP, and the whole
governing layer is small enough to audit in one sitting. Two databases: the world
(resettable) and the anchor (permanent).

**Non-goals.** Phoenix is not an agent framework or an autonomy maximiser. It does
not try to make agents smarter. It tries to make an organization of them accountable.

---

## Roadmap

- [x] Bounded gates — `(timeout, cost_of_waiting, re-route)` on every irreversible decision
- [x] Net-of-waste learning — spoilage debits the lesson that caused it
- [x] Real board dissent — disjoint evidence, `unknown` votes, degeneracy detection
- [x] The sandbox — Article V's execution path, first code task shipped
- [x] Oracle adapter #1 — the test suite (`workspace.py`)
- [ ] **The merge gate** — heralds carrying git diffs; human-approved merges to main
- [ ] **The eval race** — two+ frontier models on the leaderboard, constitution probes
- [ ] Merit decoupled from budget — agent quality measurable independently of survival
- [ ] Disputed-knowledge flag — contradictory facts quarantined at write time
- [ ] Raids & defence — adversarial pressure for the settlement ([`WORLD-DYNAMICS.md`](WORLD-DYNAMICS.md))

## Contributing

1. Read the constitution first. It's the spec.
2. If your change adds or alters a rule, change the article **and** the code it
   names, in the same commit.
3. New invariants ship as assertions that halt or escalate, not warnings that
   scroll. A warning in a busy log is indistinguishable from silence.
4. If you can't name the enforcing code, you've written a wish. Wishes go in the
   roadmap.

## The documents

[`CONSTITUTION.md`](CONSTITUTION.md) · [`SRS-Project-Phoenix-v2.md`](SRS-Project-Phoenix-v2.md) ·
[`EVAL.md`](EVAL.md) · [`REALWORK.md`](REALWORK.md) · [`LINEAGE.md`](LINEAGE.md) ·
[`WORLD-DYNAMICS.md`](WORLD-DYNAMICS.md) · [`DEPLOY.md`](DEPLOY.md) ·
[`GOVERNOR.md`](GOVERNOR.md) / [`PROJECT-RECORD.md`](PROJECT-RECORD.md)

## License

Not yet chosen — pick one before public launch (MIT/Apache-2.0 are the usual
candidates for this kind of project).

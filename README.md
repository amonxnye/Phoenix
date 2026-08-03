# Project Phoenix

**A governed organization of AI agents — with a constitution, an economy, a board of
governors, permanent memory, and a human gate on everything irreversible.**

Phoenix started as one question: *how do you let AI agents run autonomously without
burning tokens or trust?* The answer became a working civilization: agents that are
born by quorum, earn promotion by measured contribution, learn lessons that outlive
them, decay if unmaintained, stand down at budget, and stop at a human gate for
anything that can't be undone — all observable down to the single decision, forever.

The whole system reduces to two laws (see [`CONSTITUTION.md`](CONSTITUTION.md)):

> **No autonomous action without an oracle or an undo.**
> **No indefinite inaction without an escalation.**

It runs in two worlds with the same machinery:

- **The settlement** — an Age-of-Empires-style economy where villager agents gather,
  build, trade and advance eras (each era costs 100× the last). The game state is the
  oracle; the Age-up is the gate.
- **The workspace** — real software development. The test suite is the oracle;
  failing tests are the open tasks; a sandboxed worker agent patches code and is paid
  only for tests it turns green. *(First live cycle: the model fixed 3 bugs, wrote 3
  new functions, went 9/9, and earned a promotion.)*

## Quick start

```bash
pip install -r requirements.txt

# the acceptance suites (run in CI on every push)
python3 gov/verify.py          # 16 checks — runtime, gate, governor, durability
python3 gov/verify_sim.py      # 36 checks — economy, upkeep, permanence, lineage, governance
python3 gov/verify_work.py     # 12 checks — the real-work oracle, sandbox, worker loop
python3 gov/console_smoke.py   # headless smoke test of the console API

# the live console
python3 gov/sim_console.py --seed    # → http://127.0.0.1:8788
```

No key needed — a rule-based brain runs everything. To bring the organization to
life with a model, set ONE provider (all calls flow through a single instrumented
seam, `gov/brain.py`):

```bash
export DEEPSEEK_API_KEY=sk-...                     # simplest: DeepSeek
# or any OpenAI-compatible endpoint / native Anthropic:
export BRAIN_BASE_URL=https://api.deepseek.com     # or api.openai.com, api.anthropic.com, ...
export BRAIN_API_KEY=...
export BRAIN_MODEL=deepseek-v4-flash
```

Optional: `GOV_DATA_DIR=/data` (durable volume — memory survives redeploys),
`CONSOLE_TOKEN=...` (locks every mutating endpoint). Deploying: [`DEPLOY.md`](DEPLOY.md)
(a `Procfile` is included; the console reads `$PORT` and binds `0.0.0.0`).

## The console

| Page | What you govern there |
|---|---|
| `/` | The settlement: vision & progress, world meters, fleet, **balance sheet** (assets, disrepair liability, net worth), command queue (the human gate), ranked development tree, board proposals, permanent event log |
| `/agents` | System health (stalls, burn, lifetime compute, value per 1k), per-agent vitals & token math, gated Terminate, and the **Hall of Records** — every agent that ever lived, permanently, with downloadable careers & 5-second health telemetry |
| `/work` | **Real work**: the test suite as a progress bar, tasks derived from failing tests, and a button that sends a live agent to fix the code — oracle-scored, auto-reverting, unable to edit the tests |
| `/chats` | Talk to any agent, the board, or the Chief Governor; watch them debate each other (votes are narrated with live evidence: runway, momentum, affordability) |
| `/skills` | Lessons learned across generations, the capability ladder, and every decision's **reasoning with a "trace lineage" link** — why() back to its inputs, credit() forward to its outcomes |
| `/leaderboard` | The **Phoenix Eval**: same world, different frontier models, scorecards side by side + real per-call token/latency telemetry |
| `/logs` | The permanent log — filter by kind, text, and UTC time window; export as txt/csv/jsonl |
| `/rules` | The constitution (editable — only the human adopts), world rules, era prices, tiers |

## How it's governed

- **The Vision** (Article I) — one measurable goal; the board proposes, only the human
  adopts; a met vision is consumed and the world holds in stewardship, never frozen.
- **The economy** (Article II) — agents enlist at villager tier and climb by measured
  contribution; budgets end careers *before* the overshooting turn; no zombies, no
  immortals, and never reaped to empty.
- **Limits** (Article III) — a compute cap on the living fleet with a single writer;
  blocked actions escalate **once**, naming the blocking value, then mute until it changes.
- **The gate** (Article IV) — irreversible actions stop for the human, priced by the
  cost of waiting; unanswered requests re-route, time out ("taken by timeout", never
  "approved"), and never pause the reversible work around them.
- **The sandbox** (Article V) — workers run with no credentials, no network, and
  cannot edit the tests that judge them.
- **Memory** (Article VI) — the anchor never forgets and never hoards: lessons dedup,
  expire, revive on re-confirmation, and pay their authors.
- **Observability** (Article VII) — every decision carries its why, its inputs, its
  authorizer, and its measured outcome (`decisions` + `caused_by`); invariants are
  asserted in code, not just visible.
- **The board** (Article VIII) — three governors with disjoint evidence and a duty to
  escalate what they block; the Governor is scored on **vision points per compute** —
  zero movement caps the score at 3/10, good work under a binding cap earns +5%.
- **Liveness** (Article IX) — a turn where nothing happens is a failed turn; ten in a
  row is a stall that names its binding constraint and escalates. Inaction is an
  action, and it is gated too.

## The Phoenix Eval

Because the rules are code and the brain is one env var, Phoenix doubles as a
benchmark no leaderboard covers: **can your model run an organisation?**

```bash
python3 gov/evalrun.py --turns 120 --fresh --label my-model
```

Headless, reproducible, identical auto-human policy for every model; scorecards
(economy, governance discipline, stalls, learning, real token cost) stored
permanently and ranked at `/leaderboard`. Design: [`EVAL.md`](EVAL.md).

## The documents

| File | What it is |
|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | The nine articles, each naming the code that enforces it |
| [`SRS-Project-Phoenix-v2.md`](SRS-Project-Phoenix-v2.md) | The specification and roadmap |
| [`EVAL.md`](EVAL.md) | The multi-provider benchmark design |
| [`REALWORK.md`](REALWORK.md) | The game→software translation table and build order |
| [`LINEAGE.md`](LINEAGE.md) | The decision-provenance engine (why/credit walks) |
| [`WORLD-DYNAMICS.md`](WORLD-DYNAMICS.md) | Decay, balance sheet, threats & defence design |
| [`GOVERNOR.md`](GOVERNOR.md) / [`PROJECT-RECORD.md`](PROJECT-RECORD.md) | The build narrative and session record |

## The architecture bet

The LangGraph checkpointer database **is** the backend — every agent's state and
pending interrupt already persist there, so the governor is a read view plus a resume
call, and the whole console is standard-library HTTP with no framework. Auditability
is a feature: the code that governs the agents is small enough to read in one
sitting, and the constitution demands it stays that way. Two databases: the game
world (resettable) and the anchor (permanent — knowledge, skills, careers, lineage,
telemetry survive every reset and redeploy).

## Status

All four suites green in CI. The settlement runs live with a real model brain; the
first real-work agent has shipped oracle-verified code and been promoted for it.
Next: the merge gate (heralds carrying git diffs), the multi-model eval race, and
raids & defence for the settlement. The roadmap lives in the SRS and the tracked
tasks in the session record.

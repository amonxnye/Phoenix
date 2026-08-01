# Project Phoenix

An operator control plane for LangGraph agent fleets — **The Governor** — plus the
design record that produced it.

> Everyone has a loop. The scarce things are an **oracle** that tells you whether the
> loop is working and an **undo** that makes iterating safe. Coding agents work
> because git and the test suite give them both.

## Quick start

```bash
pip install -r requirements.txt
python3 gov/verify.py          # → 16/16 checks passed
python3 gov/console.py --seed  # → live operator console at http://127.0.0.1:8787
python3 gov/verify_sim.py      # → 15/15 — the Age of Empires MVP
python3 gov/sim_console.py --seed  # → live AoE console at http://127.0.0.1:8788
```

Deploying the console (Railway): see [`DEPLOY.md`](DEPLOY.md). The console reads `$PORT`
and binds `0.0.0.0`, and a `Procfile` is included, so it runs on Railway as-is.

### MVP: the Governor over an Age of Empires economy

Villagers gather food/wood/gold — reversible and cheap, so it runs free. Advancing to
the next Age spends resources you can't get back — the irreversible action, so it stops
at the human gate. The game state (the World) is the oracle: objective, instant, and
resettable. The DeepSeek brain (`brain.py`) decides what to gather and when to advance;
a rule-based policy runs with no key. To use DeepSeek: `pip install openai` and set
`DEEPSEEK_API_KEY` in the environment (its API is OpenAI-compatible).

No credentials, no network, no model calls — work nodes are deterministic so the
control plane is testable on its own. The console is standard-library only (no web
framework) and renders live from the checkpointer — the spend meter, the idle-villager
alert, and a working approve/reject queue, no fixtures.

## What's here

| Path | What it is |
|---|---|
| `gov/runtime.py` | The agent runtime: a LangGraph graph whose irreversible step pauses for a human |
| `gov/governor.py` | The control plane: a read view over the checkpointer + hard cap + idle detection |
| `gov/verify.py` | 16 acceptance checks, including durability across a real process boundary |
| `gov/console.py` | Live operator console (stdlib HTTP): fleet grid, spend meter, idle alert, approval queue |
| `gov/console_smoke.py` | Headless smoke test of the console's HTTP layer (runs in CI) |
| `gov/sim.py` | **MVP** — Age of Empires economy: villagers gather (reversible), advancing the Age is the irreversible gated action, the World is the oracle |
| `gov/brain.py` | The agent brain: rule-based by default, DeepSeek when `DEEPSEEK_API_KEY` is set |
| `gov/verify_sim.py` | 15 acceptance checks for the MVP, reusing `governor.py` unchanged |
| `gov/sim_console.py` | Live AoE operator console: World meters, Age, idle alert, command queue, gated Age-up |
| `SRS-Project-Phoenix-v2.md` | The specification — requirements, 12 acceptance criteria, open questions, roadmap |
| `GOVERNOR.md` | Build doc: the architectural bet, results, and rationale (narrative) |
| `PROJECT-RECORD.md` | Session record: the eight insights and decisions behind the design |

## The bet

The LangGraph checkpointer database *is* the backend. Every unit's state, position,
and pending interrupt already persist there, so there is no separate state store,
event bus, or polling daemon — the governor is a read view over
`checkpointer.list(None)` + `graph.get_state()`, plus a resume call. That is why the
control plane is ~106 lines.

Swap SQLite for Postgres by changing one function (`runtime.connect`) — the
checkpointer interface is identical.

## Status

`gov/` is a working spike: **AC-1..12 pass** — `gov/verify.py` (16 checks) covers the
runtime, read layer, approval gate, and governor; `gov/console_smoke.py` covers the
live console. The next increment is swapping the simulated work nodes for real agent
work against a repo-with-tests (the domain that already owns the oracle). See the
roadmap in the SRS.

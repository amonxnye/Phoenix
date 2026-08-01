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
```

No credentials, no network, no model calls — work nodes are deterministic so the
control plane is testable on its own.

## What's here

| Path | What it is |
|---|---|
| `gov/runtime.py` | The agent runtime: a LangGraph graph whose irreversible step pauses for a human |
| `gov/governor.py` | The control plane: a read view over the checkpointer + hard cap + idle detection |
| `gov/verify.py` | 16 acceptance checks, including durability across a real process boundary |
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

`gov/` is a working spike: AC-1..11 pass (16/16 executable checks, pinned deps). The
next increment is **AC-12** — wiring a live operator console to `governor.units()`
instead of rendering fixtures. See the roadmap in the SRS.

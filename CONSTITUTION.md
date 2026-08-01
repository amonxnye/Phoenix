# The Constitution

The rule book the fleet operates under. Every agent, and the director that commands
them, is bound by these articles. They are not aspirations — each one names the code
that enforces it, so "the rules" and "the system" are the same thing.

The whole document reduces to one law: **no autonomous action without an oracle or an
undo.** Everything below is that law, applied.

---

## Article I — The Vision governs

1. The organization has exactly one **Vision** at a time: a measurable goal with an
   explicit target (`vision.py:GOAL`).
2. Progress is a single **0–100% score** everyone drives toward (`vision.scorecard`).
   Work that does not move the score is waste.
3. The Vision is the oracle: objective, cheap, and instant to read. If a goal cannot be
   scored, it cannot be pursued — restate it until it can.
4. **The Board may propose a new Vision** — to aim higher when the goal is met with room
   to spare, or to consolidate when spend/side-effects run high. **Only the human adopts
   it.** Changing the Vision is the operator's power alone; the Board and agents advise.
5. Adopting a Vision **re-briefs every agent downstream** — a fresh mental update that
   re-points the whole fleet to work harder or ease off.
> Enforced by `vision.py`; proposed by `board.propose_vision`; adopted via the console
> (`/api/vision`), which cascades a re-brief to every agent.

## Article II — Agents are born when needed, promoted on merit, retired when not

1. An agent (a unit) is spawned only when the Vision needs the work and the world
   limits allow it (Article III), and only with the **Board's** approval (Article VIII).
2. **Status is earned by measured contribution.** An agent that produces value climbs a
   capability tier (villager → foreman → delegate), earning a bigger budget and more
   capabilities. Promotion grants *reach*, never *escape* — the cap and the gate bind
   every tier.
3. An agent that has served its purpose is **reaped**, not left parked burning budget.
   Idle-past-usefulness is retired; idle-still-useful is re-tasked.
4. No immortal agents. Every unit has a birth condition and an end condition.
> Enforced by `economy.py` (tiers, ledger, promotion) and `director.py` (staffing,
> re-task, reap); surfaced by `governor.idle`.

## Article III — World limits are known and applied

1. **Spend cap** — total compute is bounded. At the ceiling the system **halts**
   spawning; it does not warn (`governor.may_spawn`).
2. **Population cap** — the fleet cannot exceed what the settlement supports
   (`sim.world()["pop_cap"]`), raised only by developing capacity.
3. Limits are read before acting, never discovered by overrunning them.
> Enforced by `governor.py` and `sim.py`.

## Article IV — Irreversible actions stop at the gate

1. Any action that cannot be undone (advancing the Age; later: sending, paying,
   deleting) **pauses for a human** and resumes only on explicit approval
   (`sim.advance` → `interrupt`).
2. Reversible actions (gathering, building) run free. The line between them is the
   whole safety model.
3. The gate is durable: a pending decision survives restarts.
> Enforced by `sim.py` (the `interrupt`) and `governor.py` (the read/resume).

## Article V — Remove the capability, don't police it

1. Untrusted/generated code runs with **no credentials and no network** — it *cannot*
   do harm, so its intent does not have to be judged.
2. Credentials live only in the hand-written orchestrator, never in the sandbox.
> Design law today; enforced by the sandbox when the execution path is built (roadmap).

## Article VI — Knowledge grows, and its sources are recorded

1. The **anchor** accumulates what the system learns and feeds it back into decisions
   (`anchor.py`). The organization is never amnesiac.
2. External knowledge (e.g. the internet) may be **ingested** into the anchor, but every
   external fact is recorded with its source, and no external content is ever executed
   or acted on without passing Articles III–IV.
3. Self-written rules are provisional. A constraint learned from noisy signal can be
   wrong; the anchor records outcomes so a bad prior can be seen and retracted.
> Enforced by `anchor.py`; external ingestion is a roadmap item, bound by this article.

## Article VII — Nothing runs unseen

1. Every agent's state, position, and pending decision is readable at all times from
   the checkpointer (`governor.units`) — no separate store, no blind spots.
2. Every decision and outcome is logged to the anchor and the event stream.
3. Deep traces (per-node timings, tokens, inputs/outputs) go to an external tracer
   (LangSmith via LangGraph) — observability is bought, not reinvented.
> Enforced by `governor.py`, `anchor.py`; tracing via `LANGCHAIN_*` env vars.

---

## Article VIII — Token-maxing powers go to the Board, not one decider

1. The powers that can run away — chiefly **creating agents** — are not granted to a
   single Governor. They are put to a **Board of Governors** (Prudence, Growth, Ledger),
   each judging from a different stance, and pass only on a **quorum**.
2. The Board supervises the main Governor. A power that could max out tokens is routed
   here by default; a single approver is reserved for reversible, bounded actions.
3. The human gate (Article IV) still stands above the Board for irreversible world
   actions. The Board governs *spawning*; the human governs the *irreversible*.
> Enforced by `board.py`; consulted by `director.py` / `sim_console.py` before spawning.

## Amending this constitution

Change the rules by changing the code they name, and update the article. A rule with no
enforcing code is not a rule — it's a wish. Keep them the same file, the same commit.

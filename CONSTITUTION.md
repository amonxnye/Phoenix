# The Constitution

The rule book the fleet operates under. Every agent, and the director that commands
them, is bound by these articles. They are not aspirations — each one names the code
that enforces it, so "the rules" and "the system" are the same thing.

The whole document reduces to two laws — a safety law and its governance dual:
**no autonomous action without an oracle or an undo**, and
**no indefinite inaction without an escalation** (Article IX).
Everything below is those two laws, applied.

---

## Article I — The Vision governs

1. The organization has exactly one **Vision** at a time: a measurable goal with an
   explicit target (`vision.py:GOAL`).
2. Progress is a single **0–100% score** everyone drives toward (`vision.scorecard`).
   Work that does not move the score is waste, **and waste is counted**: every work
   report states the Vision delta, compute spent, spoilage lost and failed turns
   (`sim_console._governor_report`).
3. The Vision is **the only** oracle: objective, cheap, and instant to read. If a goal
   cannot be scored, it cannot be pursued — restate it until it can. Two disagreeing
   scores means the world has no oracle at all.
4. **The Board may propose a new Vision** — to aim higher when the goal is met with room
   to spare, or to consolidate when spend/side-effects run high. **Only the human adopts
   it.** Changing the Vision is the operator's power alone; the Board and agents advise.
5. Adopting a Vision **re-briefs every agent downstream** — a fresh mental update that
   re-points the whole fleet to work harder or ease off.
6. **A met Vision is consumed.** A vision identical to the standing one cannot be
   re-adopted, so success is never re-won and a re-brief is never a no-op
   (`sim_console._adopt_vision`).
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
4. No immortal agents. Every unit has a birth condition and an end condition — and no
   zombies: a thread that can no longer work is struck off the roster the moment it is
   seen (`sim_console` zombie guard).
5. **The fleet has a floor.** Below minimum staffing with replacement blocked, reaping
   is suspended and the shortage escalates to the human (Article IX). A world may run
   understaffed; it may not run empty.
6. When the Vision needs the work and a limit forbids it, **that conflict is itself the
   finding**: the blocking limit and its value are escalated once (Article VIII.4),
   never silently retried.
> Enforced by `economy.py` (tiers, ledger, promotion) and `director.py` (staffing,
> re-task, reap); surfaced by `governor.idle`.

## Article III — World limits are known and applied

1. **Spend cap** — total compute of the living fleet is bounded. At the ceiling the
   system **halts** spawning immediately and without negotiation. It does not warn
   repeatedly; it **escalates once**, naming the blocking value, and stays muted until
   that value changes (`governor.may_spawn`, `sim_console._breaker`).
2. **Population cap** — the fleet cannot exceed what the settlement supports
   (`sim.world()["pop_cap"]`), raised only by developing capacity.
3. Limits are read before acting, never discovered by overrunning them. **The check
   precedes the commit**: an agent is retired before the turn that would overshoot its
   budget, not after. The cap has a single writer that logs its caller and accepts one
   mutation per turn (`sim_console._set_cap`).
> Enforced by `governor.py`, `sim.py` and `sim_console.py`.

## Article IV — Irreversible actions stop at the gate

1. Any action that cannot be undone (advancing the Age; later: sending, paying,
   deleting) **pauses for a human** and resumes only on explicit approval
   (`sim.advance` → `interrupt`).
2. Reversible actions (gathering, building) run free. The line between them is the
   whole safety model.
3. The gate is durable: a pending decision survives restarts.
4. **Waiting has a price, and the gate must show it.** Every pending decision carries
   the running cost of not deciding — turns waited, resources rotting — displayed with
   the request (`_snapshot.human_gate`).
5. **A gate blocks only the irreversible action itself, never the reversible work
   around it.** Surplus above capacity flows into repair, builds and the market while
   the gate waits (`sim.trade_surplus`). A decision may pause the Age; it may not
   pause the settlement.
6. An unanswered request means the channel failed, not that the human refused. It is
   escalated once through a **different** channel, never repeated into the same one
   (`sim_console._fleet_speaks`).
7. **Tacit consent.** A decision queued for the human for more than one hour goes
   back to the Board for a final vote on live evidence. A quorum yes proceeds *as if
   approved* — unmistakably labelled "by tacit consent", logged, and reported to the
   human; a blocked vote stands the request down. This covers the gate, board-approved
   development proposals, and the adoption of the Board's *proposed* next Vision after
   a goal is met (silence never adopts an invented goal). The human may decide at any
   moment before the hour, and may reverse anything reversible after it. *(Adopted by
   the human, 2026-08-04 — silence is a decision, and this article names its owner.)*
   Enforced by `sim_console.py` (`TACIT_CONSENT_S`, the IV.7 block in `_one_turn`).
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
4. **Knowledge expires.** Only a bounded set of recent lessons steers decisions; older
   guidance is marked stale and becomes history, not policy — contradictions age out
   instead of coexisting forever (`anchor.skill_prune`). A lesson is recorded as
   learned only when it is actually new (deduplicated at write time).
> Enforced by `anchor.py`; external ingestion is a roadmap item, bound by this article.

## Article VII — Nothing runs unseen

1. Every agent's state, position, and pending decision is readable at all times from
   the checkpointer (`governor.units`) — no separate store, no blind spots.
2. Every decision and outcome is logged to the anchor and the event stream.
3. Deep traces (per-node timings, tokens, inputs/outputs) go to an external tracer
   (LangSmith via LangGraph) — observability is bought, not reinvented.
4. **Observability is not perception.** The invariants this constitution names are
   checked in code every turn — liveness, the fleet floor, budget-before-commit — and
   a failed check escalates (Article IX). A property that is merely visible is not
   governed.
5. Records are complete: telemetry is not truncated at the point where it becomes
   informative, and outcomes name the decision that caused them (`decisions`,
   `knowledge.caused_by`).
> Enforced by `governor.py`, `anchor.py`, `sim_console.py`; tracing via `LANGCHAIN_*` env vars.

---

## Article VIII — Token-maxing powers go to the Board, not one decider

1. The powers that can run away — chiefly **creating agents** — are not granted to a
   single Governor. They are put to a **Board of Governors** (Prudence, Growth, Ledger),
   each judging from a **disjoint evidence set it alone reads** — Prudence: runway and
   the side-effect budget; Growth: Vision momentum and staffing; Ledger: affordability —
   and pass only on a **quorum**. A member may vote *unknown*; a member whose vote is
   constant across twenty consecutive decisions is flagged as non-functional
   (`board.degenerate_members`).
2. The Board supervises the main Governor. A power that could max out tokens is routed
   here by default; a single approver is reserved for reversible, bounded actions.
3. The human gate (Article IV) still stands above the Board for irreversible world
   actions. The Board governs *spawning*; the human governs the *irreversible*.
4. **The Board must escalate what it blocks.** A power denied three times for the same
   unchanged reason is reported once to the human, naming the blocking value and what
   changing it would unblock. The Board may withhold a power; it may not withhold the
   fact that it did (`sim_console._breaker`).
5. **Evaluation follows Article I.** The Governor is scored on Vision points gained per
   compute spent, less waste — never on capacity authorised, cap size or activity
   volume. A period with zero Vision movement scores at most 3 of 10. Every report
   begins with the Vision delta (`sim_console._governor_report`).
> Enforced by `board.py`; consulted by `director.py` / `sim_console.py` before spawning.

## Article IX — Inaction is an action, and it is gated too

1. A turn in which nothing is gathered, built, spawned, repaired or traded is a
   **failed turn**, counted and reported alongside spend.
2. Ten consecutive failed turns, or a Vision score frozen for twenty-five turns, is a
   **stall**. On a stall the world names the first binding constraint and escalates to
   the human — it does not continue quietly (`sim_console` liveness check).
3. The Board and the Governor may not report a stalled world as healthy: a report
   covering a stalled period leads with that fact, and its score is capped (VIII.5).
4. The constitution's brakes — the cap, the gate, the quorum, the reaper — each have
   the power to stop the world. Liveness is the article that owns the consequence:
   stopping is a state that must always be visible, priced, and escalated.
> Enforced by `sim_console.py` (failed-turn counter, `_binding_constraint`, stall
> escalation), surfaced on `/agents` SYSTEM HEALTH and in every work report.

## Amending this constitution

Change the rules by changing the code they name, and update the article. A rule with no
enforcing code is not a rule — it's a wish. Keep them the same file, the same commit.

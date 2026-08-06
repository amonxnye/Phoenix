# What the Settlement taught us about making agents work

Every principle below is here because a measurement forced it, and every one names the
code that enforces it and the evidence that produced it. Where the evidence is thin,
this document says so — an unproven principle is a preference, and preferences do not
belong in a file that reads like findings.

**Sources of evidence used here**

| Tag | What it is |
|---|---|
| `PROD-19.6h` | A 19.6-hour production run: 33,589 events, 5,311 turns, 381 agents, measured before and after Article IX landed mid-run (README, *What the data shows*) |
| `SMOKE-150` | `python3 gov/smoke.py --turns 150 --pollers 8 --fresh`, rule-based brain, reproducible from a clean world |
| `TESTS` | The behaviour is pinned by a named test — if the principle stops holding, CI fails |

---

## 1. Score the outcome, never the activity

**The finding.** Agents optimise whatever you count. When the system counted messages
and capacity, it produced messages and capacity: `PROD-19.6h` measured a talk-to-action
ratio of **3.78 : 1** and only **21%** of turns changing world state. Re-scoring the
Governor on Vision points gained per compute spent — with a hard rule that zero
movement scores at most 3 out of 10 — moved the same system to **0.32 : 1** and **100%**
of turns changing state, and contribution per 1k tokens from **~10.7 to 75.8**.

**Why it works.** An organisation of agents converges on the cheapest behaviour that
scores. Talking is always the cheapest. The only durable fix is to make the score
unable to see talk.

**Enforced by** `sim_console._governor_report` (Article VIII.5).

## 2. Truth comes from a world, not from the agent's own report

**The finding.** Every number in `SMOKE-150` — 100% vision progress, 7,310 net worth,
6 promotions, 2 retirements — is read back out of the world oracle after the run, not
accumulated by the code that did the work. When the workspace harness needed a score
for real code, the same rule applied: the test suite is the oracle, and a patch that
breaks tests is auto-reverted regardless of what the agent claims.

**Why it works.** Self-reported success is unfalsifiable, and an LLM judge inherits the
biases of the thing it judges. A world that computes the score independently makes the
question "did it work?" cheap and objective.

**Enforced by** `vision.scorecard`, `workspace.oracle`. **Evidence** `SMOKE-150`, `TESTS`.

## 3. Give each judge its own evidence, or you have one judge in three hats

**The finding.** The board's three seats read **disjoint** evidence: Prudence sees
runway and the side-effect budget, Growth sees Vision momentum and staffing, Ledger
sees affordability. A member whose vote never varies across twenty consecutive decisions
is flagged as non-functional, because a reason that never changes is a label, not a
judgement.

**Why it works.** Three agents given the same context produce one opinion three times —
expensive agreement that feels like review. Disjointness is what makes a quorum mean
something.

**Enforced by** `board.vote`, `board.degenerate_members` (Article VIII.1).
**Evidence** `TESTS` — the full ballot matrix, including the cases where a seat has no
evidence at all and must answer *unknown* rather than yes.

## 4. "I don't know" must be a first-class answer

**The finding.** Seats vote `None` when their evidence is missing, and quorum counts
only yes votes — so an abstention can never become consent. The same rule extends to
infrastructure: a seat whose model is unreachable, or whose configuration is broken,
abstains and the abstention is logged with the dead model named.

**Why it works.** Systems that force a binary answer manufacture confidence. The
expensive failure is not an agent saying "unknown"; it is an agent guessing and the
guess being indistinguishable from knowledge afterwards.

**Enforced by** `board.vote`, `models.offline_seats`, `brain.provider_for`
(Articles VIII.1, X.3). **Evidence** `TESTS` — including that a broken seat is *not*
answered by the default model even when one is available.

## 5. Never substitute silently — a gap in the record is worse than a gap in service

**The finding.** This one was a bug in this codebase, found by re-reading the routing
code with the question "what happens when a seat is misconfigured?": a seat assigned a
provider with no key fell through to the default brain and answered in its place. The
vote still happened, the record still looked complete, and the attribution was wrong.

**Why it works.** Degradation that isn't recorded corrupts every measurement downstream
— per-model scores, hit rates, leaderboard rows. A loud gap is recoverable; a quiet
substitution is not.

**Enforced by** `models.resolve_detail` (which distinguishes *unset* from *broken*) and
`brain.provider_for`, which raises rather than falls back.
**Evidence** `TESTS` — `test_broken_seat_never_substitutes`.

## 6. Count the rescues

**The finding.** Every rule-based fallback that stands in for a failed model call is
counted, per role, and reported on the scorecard as `fallbacks` / `fallback_rate`. The
fallback still runs — a live settlement must not stall on a provider hiccup — but no
scorecard may credit the rules' work to the model that failed.

**Why it works.** Fallbacks make a system look healthier than it is. If they are not
counted, a model with a 40% error rate and a good fallback scores like a model that
works.

**Enforced by** `models.note_fallback`, `brain.*`, `evalrun`. **Evidence** `TESTS`.

## 7. Make waiting expensive, visible, and bounded

**The finding.** Irreversible actions stop at a human gate, and `PROD-19.6h` showed the
failure mode: a herald can wait hundreds of turns while the world quietly rots around
it. The response was not to remove the gate but to price the wait, name it as the
binding constraint, and bound it — after an hour of silence, the board's vote carries
(tacit consent, IV.7).

**Why it works.** A gate with no clock is an outage waiting for a human who has gone to
lunch. A gate that prices its own delay turns "waiting on approval" into a number
somebody can act on.

**Enforced by** `sim.advance` (interrupt), `sim_console` gate pricing and tacit consent
(Articles IV.4, IV.7, IX.2). **Evidence** `PROD-19.6h` (the stall escalation naming the
human as the binding constraint), `SMOKE-150` (1 gate opened, 1 answered, 0 stall turns
under the auto-human).

## 8. Inaction is an action, and it must be gated too

**The finding.** Before Article IX, 79% of turns changed nothing and the system reported
itself healthy. A turn in which nothing is gathered, built, spawned, repaired or traded
is now a *failed turn*; ten consecutive failed turns, or a Vision score frozen for
twenty-five, is a stall that names its first binding constraint and escalates.

**Why it works.** Every brake in a governed system — the cap, the gate, the quorum, the
reaper — has the power to stop the world. Somebody has to own the consequence of
stopping, or the safest possible system is one that does nothing.

**Enforced by** `sim_console` liveness checks, `_binding_constraint` (Article IX).
**Evidence** `PROD-19.6h`: turns changing world state 21% → 100%.

## 9. Retire agents before their budget does

**The finding.** `PROD-19.6h` recorded **0 of 31** reaps respecting the budget ceiling
before the rewrite and **306 of 306** after. `SMOKE-150` shows the mature behaviour: 4
agents lived, 2 were retired, 6 promotions were earned, and the fleet's outstanding
compute stayed at 676,600 of a 1,000,000 cap.

**Why it works.** An agent that runs until it exhausts its budget spends its last tokens
doing its worst work. Retiring on merit — and never reaping to empty — keeps the fleet's
average quality above the average of what it has hired.

**Enforced by** `economy.py`, `director.py` (Article II).

## 10. Memory must forget, and restatements are not new knowledge

**The finding.** `SMOKE-150` produced **six** lessons, of which five were the same
sentence with a different decimal ("Prioritise food early — avg 33.1/round" …
"33.3/round"). De-duplication matched on exact text, so restatement accumulated. Since
lessons are read back before decisions, that is a memory that says one thing loudly.
Matching on the *gist* (numbers normalised away) collapsed the same run to **one**
lesson, carrying the most recent measurement.

**Why it works.** Retrospectives naturally restate; that is what a retrospective is. A
learning store that cannot tell restatement from discovery will report progress it is
not making — and will steer the next generation with an echo.

**Enforced by** `anchor.lesson_key`, `anchor.skill_add`, `anchor.skill_prune`
(Article VI.4). **Evidence** `SMOKE-150` before/after (6 → 1), `TESTS`.

## 11. A decision without its inputs is an opinion

**The finding.** Decisions record why, what they derived from, who authorised them, and
what measurably happened. `SMOKE-150` measured **28 decisions, 27 closed, a 100% hit
rate — and 4% grounded**: almost none cited a real input.

**What that means.** The hit rate is real but weak evidence on its own, because the
rule-based path closes decisions with outcomes it also chose the wording for. Grounding
is the honest number here, and 4% is *bad*. It is reported as-is rather than quietly
dropped, and it is the clearest open defect this document has: the rule-based decision
sites do not cite the yields, lessons and events they actually used.

**Enforced by** `anchor.reason_add` / `decision_close` (Article VII, LINEAGE.md).
**Evidence** `SMOKE-150`. **Status: open.** Fixing it means threading `derived_from`
through the director and console decision sites.

## 12. Serve readers from one computation

**The finding.** `SMOKE-150` ran 8 concurrent pollers against a live world: **1,437
reads, p50 0.0ms, p95 0.01ms, max 300ms**. All but a handful of reads were free,
because one computation per second serves every viewer (stale-while-revalidate); the
max coincides with the slowest turn, since that is when the one rebuilding reader waits
on the world lock.

**Why it works.** The naive version — a full snapshot per request — is how popularity
becomes an outage. This matters more, not less, for agent systems: the state is
expensive to compute and everyone wants to watch it.

**Enforced by** `sim_console._snapshot` (cache + single-flight rebuild).
**Evidence** `SMOKE-150`.

## 13. Attribute every action, because "the human" is not a person

**The finding.** One settlement, many anonymous visitors: "the human approved the gate"
is not a useful record when six people are watching. Every human power now records who
used it (`operator`, or a stable per-visitor `guest:ab12cd34`), and two people answering
the same gate cannot both win — the second gets a conflict naming what changed.

**Why it works.** Shared control surfaces without attribution produce an audit trail
that cannot answer the only question anyone asks afterwards: who did this?

**Enforced by** `sim_console._actor`, `_gate_fingerprint`, and the 409 path on
`/api/resume`. **Evidence** `TESTS` — two guests racing one gate produce exactly one
decision.

## 14. Refuse to measure what you cannot measure

**The finding.** The platform ships **no** price table and **no** default model names.
A budget ceiling set over an unpriced model is refused rather than enforced, and a
pre-flight estimate with no observed history returns "no estimate" with a reason
instead of a plausible number.

**Why it works.** A guessed price multiplies through every dollar column while looking
authoritative; a guessed model id ends up in a scorecard and a leaderboard row. The
failure mode of a wrong number is not that it is wrong, but that nobody can tell.

**Enforced by** `models.SEED_PRICES` (empty), `models.BUILTIN` (no model ids),
`models.check_budget`, `models.preflight`. **Evidence** `TESTS`.

---

## How to reproduce the evidence

```bash
python3 -m unittest discover -s gov/tests -t gov/tests -v   # the behavioural pins
python3 gov/smoke.py --turns 150 --pollers 8 --fresh        # the measured run
```

`SMOKE-150` numbers in this document come from a rule-based run — no model key was
configured, and the report says so rather than implying one was involved. With a model
configured, the same command reports its calls, latency, errors and dollars alongside
the same world measurements, which is the comparison the Arena exists to make.

# Project Phoenix — session record

**Date:** 31 July 2026
**Participants:** Amonte · Claude
**Outcome:** SRS rewritten, one project split into two, scope narrowed to a working spike

---

## Where we started and where we landed

**Started:** an SRS for "Project Phoenix — Autonomous Revenue-Seeking Swarm." A
`while True` loop running multi-agent AI on DeepSeek, generating and executing
Python via `exec()`, learning by appending rules to `blacklist.txt`, aimed at
independently generating revenue. The question on the table was narrow: add a
Slack approval webhook, or migrate storage to Firestore?

**Landed:** neither. The SRS had structural problems that made both questions
premature. After working through them, the project split cleanly in two — a
marketing bot for Silk NOVA, and an agent-orchestration platform — and we built
the second one down to a running spike with 16 passing acceptance checks.

---

## The eight things we worked out

These are in the order we found them. Each one changed the design.

### 1. A loop that grades itself on "did it run" converges on code that runs, not money

v1's Critic fired only on stack traces. That's an error-avoidance loop. It reaches
"nothing crashes" within a few dozen iterations and then plateaus forever, because
nothing in the system ever asks whether a hypothesis made money.

**Fix:** a fourth record (`results.md`), a Measurer role, and hypotheses that must
state a falsifiable predicted movement in a named metric before they're allowed to
run. A hypothesis nothing can measure gets rejected at authoring time.

### 2. `exec()` is not a sandbox

v1 called it an "isolated `exec()` runtime." It isn't isolated in any sense — same
process, same filesystem, same `os.environ`, including every credential the process
holds. Generated code doesn't need to be malicious to print your Stripe key into a log.

**Fix:** an explicit trust boundary. Orchestrator (hand-written, holds all
credentials) vs Sandbox (generated code, holds nothing, default-deny egress,
destroyed between iterations).

### 3. You cannot gate money by reading code

v1 proposed matching generated Python against "strict local verification rules."
Any practical allowlist is defeated by `getattr(__import__('stripe'), 'Charge')` —
and this was guarding the highest-consequence action in the system.

**Fix:** remove the capability instead of detecting its misuse. Zero financial
credentials in the sandbox; money-touching intent becomes a structured proposal in
a human-drained queue. This is what made the safety property testable rather than
aspirational.

### 4. Iteration rate has to match feedback rate

A 60-second loop runs 1,440 times a day. Revenue signals resolve in days or weeks.
That's roughly 10,000 actions per unit of actual information — not a fast learner,
just a machine generating uninformed variation and billing you for it. The throttle
should be set by how often the outcome signal updates, not by API cost.

### 5. Self-written constraints are permanent, and can be confidently wrong

When a hypothesis is refuted the cause is ambiguous — bad idea, bad execution, wrong
timing, insufficient sample. The Critic can't do credit assignment, but it will
produce a fluent, plausible constraint anyway, and that constraint then shapes every
future iteration with no mechanism to retract it. A system that writes its own priors
from noisy signals doesn't just plateau; it can walk steadily away from good ideas.

Related: on a small account, engagement differences are pure noise. Below some
minimum reach the loop doesn't fail to learn — it poisons its own instructions.
Hence draft-only mode until the numbers can carry a signal.

### 6. The verifier is the bottleneck, not the loop

This was the turning point. Long-horizon autonomy works in coding because there's a
cheap, fast, objective oracle: the compiler runs, tests pass or fail. You can afford
200 iterations because each gets ground truth in seconds.

And even there it's hard. Long-Horizon-Terminal-Bench (~231 episodes, ~85 min per
task — exactly the "run for hours" regime) puts the **best model at 15.2% pass@1**
and the **mean at 4.3%**. The authors had to invent dense intermediate rewards
because grading on final outcome alone was too sparse to be learnable.

So "nobody's building this" was wrong — the field is investing heavily. Everyone has
a loop. Almost nobody has an oracle.

### 7. Reversibility is the hidden precondition

Git is the quiet reason coding agents work. An agent can try something disastrous
500 times and revert each one for free, so brute-force iteration is viable.

Now price a retry elsewhere: a sent email, a public post, spent ad budget, a banned
account. Irreversible actions destroy the property that makes agent loops work — you
cannot iterate toward quality when every mistake is permanent and cumulative.

**The design rule that came out of this:** for everyday autonomy you must either
**manufacture an oracle** or **manufacture an undo**. Drafts, staging, dry-runs,
sandboxes, reversible transactions. Every domain where agents work in the real world
has one of the two. An approve-before-post gate isn't squeamishness — it's a
hand-built `git revert`.

### 8. There were two projects tangled together

A marketing bot for Silk NOVA, and an agent-orchestration platform. They kept getting
designed as one system, but the platform doesn't need Silk NOVA and Silk NOVA doesn't
need the platform. Separating them made every subsequent decision easy.

---

## Decisions on record

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | SRS holes vs. features first | **Fix the SRS** | Approval webhook and Firestore were both premature |
| 2 | Financial exposure in v1 | **Zero live credentials** | Makes FR-3.3 structurally enforceable |
| 3 | Which project to pursue | **Orchestration platform** | Silk NOVA can't produce the signal to prove a platform works |
| 4 | RTS metaphor, which axis | **Command interface** | Buildable now; simulation is a sim-to-real trap for markets |
| 5 | Framework | **LangGraph, not LangChain** | `interrupt()` + checkpointer *are* the approval queue and restart safety |
| 6 | Runtime scope | **LangGraph-only** | Keeps the checkpointer-as-backend shortcut |
| 7 | Audience for v1 | **Own tooling first** | No auth, no multi-tenancy; you're the user |

---

## What got built

| Artifact | What it is |
|---|---|
| `SRS-Project-Phoenix-v2.md` | Rewritten spec — trust boundary, Measurer role, capability-based safety, context compaction, spend cap, kill switch, 12 acceptance criteria with full FR coverage |
| `phoenix-command.html` | Interactive RTS-style operator console mockup — resource meters, unit grid, idle alert, approval queue, event log |
| `gov/runtime.py` | LangGraph graph, durable checkpointer, `interrupt()` at the irreversible step |
| `gov/governor.py` | The control plane — read view over the checkpointer, hard spend cap, idle detection |
| `gov/verify.py` | 16 acceptance checks, including durability across real process boundaries |
| `gov/GOVERNOR.md` | Build doc with rationale, results, and all source inline |

**203 lines of product code.** The reason it's that small is the architectural bet:
the LangGraph checkpointer database *is* the backend. Every unit's state, position,
and pending interrupt already persist there, so there's no separate state store, no
event bus, no polling daemon.

**Verification:** 16/16, including a genuinely separate-process restart test.

---

## Corrections made during the session

Worth recording, since both changed conclusions:

- **"Nobody is building autonomous agent platforms"** — wrong. Heavy investment
  across OpenHands, Devin, SWE-agent, Cursor/Codex background agents, plus academic
  benchmarks. The constraint is verifiers, not interest.
- **"Nobody has built a fleet console"** — overstated. Multi-agent dashboards for
  Claude Code and Grok, AgentCenter, agentsroom, PI Dashboard all exist. What's
  still thin is *governing* (caps, idle reclamation, durable approval gates) rather
  than *viewing*. That narrowing is what the spike targets.
- **`deepseek-v4-flash`** — checked and correct, following the July 2026 rename from
  `deepseek-chat`.
- **"Idle" changed meaning mid-build.** The planned fixture was a unit spinning
  without progress. The real idle villager in a human-in-the-loop system is a unit
  that *finished its work and is parked on a human who isn't looking*. That falls out
  of the interrupt model for free and is probably the most valuable alert in the product.

---

## Still open

**What Silk NOVA is, and who buys it.** Asked twice, not yet answered. A web search
found nothing, so it has little public footprint. This matters beyond curiosity: an
agent writing daily posts needs a sharper answer to "what is this and who's it for"
than a human does, because a human improvises around vague positioning and a language
model just generates vague copy. The bot inherits and amplifies whatever clarity
exists upstream of it.

**Whether there's any existing audience or traffic.** Determines whether the
measurement loop can work at all, and whether draft-only mode needs to run for two
weeks or two months.

**Platform blockers not yet solved:** no sandbox (generated code would still run
in-process — LangGraph does not help here), work nodes still simulated, console not
yet wired to live data.

---

## Next

Immediate, in order:

1. **Wire the console to `governor.units()`** so it renders live instead of fixtures.
   A couple of hours, and it makes everything after it easier to debug.
2. **Swap simulated nodes for real agent work** against a repo with tests — a domain
   that already has the oracle.
3. **Sandbox the execution path.**

Parked, whenever Silk NOVA gets picked back up: 14 days of manual posting to
establish a baseline, then a single draft-only agent, then the Critic once the
numbers can support it.

---

## The one-line version

Everyone has a loop. The scarce things are an oracle that tells you whether the loop
is working and an undo that makes iterating safe — and the reason coding agents work
is that they happen to have both.

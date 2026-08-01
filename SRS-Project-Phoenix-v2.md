# SRS — Project Phoenix v2

**Status:** draft v0.1 · consolidated from `GOVERNOR.md` and `PROJECT-RECORD.md`
**Author:** Amonte · Claude
**Supersedes:** the v1 "Autonomous Revenue-Seeking Swarm" SRS

---

## 0. How to read this

This is the specification the session record (`PROJECT-RECORD.md`) refers to but that
had not yet been written. It codifies the decisions already made, states the
requirements they imply, and defines what "done" means for the first shippable
version. Where a thing is genuinely undecided it is marked **[OPEN]** rather than
guessed.

Two products came out of the v1 teardown. This SRS specifies **Product A (The
Governor)** in full and scopes **Product B (Silk NOVA bot)** only enough to keep the
boundary between them clean.

---

## 1. Purpose and background

### 1.1 The problem v1 got wrong

v1 proposed a `while True` loop of multi-agent AI that generated and `exec()`'d
Python to independently earn revenue, learning by appending rules to a text file.
The teardown established why that cannot work as specified, and each conclusion is a
requirement here:

| v1 assumption | Why it fails | Requirement it forces |
|---|---|---|
| An `exec()` runtime is "isolated" | Same process, filesystem, and `os.environ` — every credential is exposed | Explicit trust boundary (§4.1) |
| Money can be gated by reading code | `getattr(__import__('stripe'),'Charge')` defeats any allowlist | Remove the capability, don't detect misuse (§4.2) |
| A loop that avoids crashes is learning | "Did it run" converges on code that runs, not money | Falsifiable, measurable hypotheses (Product B) |
| Self-written constraints improve the system | Bad credit assignment writes confident, permanent, wrong priors | Human-drained decisions; draft-only until signal exists |
| Fast iteration = fast learning | 1,440 loops/day against week-long revenue signals is uninformed variation | Throttle set by feedback rate, not API cost |

### 1.2 The governing insight

> Everyone has a loop. The scarce things are an **oracle** (does the loop tell you
> whether it's working?) and an **undo** (is iterating safe?). Coding agents work
> because git and the test suite give them both.

**Design rule (normative):** for any autonomous action the system must either
*manufacture an oracle* (a cheap, fast, objective verifier) or *manufacture an undo*
(draft, staging, dry-run, sandbox, reversible transaction). An action with neither
must stop at a human. The approval gate is not caution — it is a hand-built
`git revert`.

---

## 2. Scope

### 2.1 In scope (v1 of The Governor)

An operator control plane for a fleet of LangGraph agent units, providing the three
controls that turn *viewing* into *governing*:

1. **Hard resource cap** — halts spawning at a ceiling; halts, does not warn.
2. **Idle reclamation** — surfaces units that finished work and are parked on a
   human who isn't looking.
3. **Durable approval gate** — irreversible actions pause until a human resumes
   them, surviving process restarts.

### 2.2 Out of scope (v1)

Auth, multi-tenancy, RBAC (own-tooling-first — you are the only user); non-LangGraph
runtimes; a real execution sandbox (**[OPEN]**, see §7); real agent work nodes
(simulated in v1); Product B (Silk NOVA marketing bot).

### 2.3 The two-product split

Product A does not need Silk NOVA, and Silk NOVA does not need the platform. They are
specified and built separately. Product B is parked until its positioning is answered
(§7) and is referenced here only to fix the trust boundary.

---

## 3. Stakeholders and the architectural bet

| Stakeholder | Interest |
|---|---|
| Operator (you) | Sees the fleet, caps spend, drains approvals, reclaims idle units |
| Agent unit | A LangGraph graph instance; runs work, pauses at irreversible steps |
| Checkpointer DB | The single source of truth (SQLite in dev, Postgres in prod) |

**The bet:** *the LangGraph checkpointer database is the backend.* Every unit's state,
current node, and pending interrupt already persist in the checkpointer tables. So
there is no separate state store, event bus, or polling daemon — the governor is a
read view over `checkpointer.list(None)` + `graph.get_state()`, plus a resume call.
This is why the control plane is ~106 lines.

---

## 4. Functional requirements

### 4.1 Trust boundary

- **FR-1.1** The system SHALL separate an **Orchestrator** (hand-written, holds all
  credentials) from a **Sandbox** (generated/agent code, holds none).
- **FR-1.2** The Sandbox SHALL default-deny egress and hold zero financial
  credentials. *(v1: boundary specified; enforcement is [OPEN], §7.)*

### 4.2 Capability-based safety

- **FR-2.1** Money-touching intent SHALL NOT be gated by inspecting generated code.
  Instead the capability SHALL be absent from the Sandbox, and the intent SHALL
  surface as a structured proposal in a human-drained queue.
- **FR-2.2** An irreversible action SHALL block at an `interrupt()` and resume only
  on an explicit human decision (`approve` / `reject`).

### 4.3 The read layer (governor)

- **FR-3.1** The console SHALL derive every unit's identity, state, position, and
  pending interrupt from the checkpointer alone (no separate store).
- **FR-3.2** For each unit the view SHALL expose: id, task, status ∈
  {`running`, `awaiting_approval`, `idle`, `done`}, current node, steps, tokens, age.
- **FR-3.3** Status SHALL be derived: pending interrupt aged past `IDLE_AFTER_S`
  ⇒ `idle`; pending but fresh ⇒ `awaiting_approval`; no next node ⇒ `done`;
  otherwise `running`.

### 4.4 The controls

- **FR-4.1 (hard cap)** `may_spawn` SHALL return `(False, reason)` when
  `spent + projected_cost > TOKEN_CAP`, halting the spawn. **[OPEN]** whether the cap
  counts lifetime spend (current behavior) or only live units (§7, review finding).
- **FR-4.2 (idle reclamation)** The system SHALL list every unit whose status is
  `idle` — finished, tokens spent, parked on an absent human.
- **FR-4.3 (durable gate)** `resume(unit, decision)` SHALL drain one approval;
  the outcome SHALL persist across a genuine process restart.

### 4.5 Operator surface

- **FR-5.1** An operator console SHALL render the fleet: resource meter, unit grid,
  idle alert, approval queue, event log.
- **FR-5.2** The console SHALL render from **live** `governor.units()`, not fixtures.
  *(v1 gap — currently renders fixtures; this is the first build increment.)*

---

## 5. Non-functional requirements

- **NFR-1 (durability)** State SHALL survive process death; verified by a check that
  reads identical state from a separately-spawned process.
- **NFR-2 (portability)** Swapping SQLite → Postgres SHALL touch exactly one function
  (`runtime.connect`); the checkpointer interface is identical.
- **NFR-3 (verifiability)** Each acceptance check SHALL pass or fail with no
  interpretation. Work nodes SHALL be deterministic so the control plane is testable
  without spending tokens or waiting on a model.
- **NFR-4 (size/legibility)** The control plane SHALL stay small enough to audit in
  one sitting (current: ~203 lines product code + 130 lines checks).
- **NFR-5 (reproducibility)** Dependencies SHALL be pinned; `pip install -r
  requirements.txt && python3 gov/verify.py` SHALL reproduce the result on a clean
  clone.

---

## 6. Acceptance criteria

Twelve criteria, each mapped to the requirement it proves. AC-1..12 are the
spec-level bar; the runnable `gov/verify.py` suite (16 checks) is the executable
witness for the subset marked ✅.

| # | Criterion | Proves | Witness |
|---|---|---|---|
| AC-1 | N units spawn concurrently and each reaches the gate | FR-3.1/3.2 | ✅ verify §1 |
| AC-2 | Each unit completes its work steps before the gate | FR-2.2 | ✅ verify §1 |
| AC-3 | A separate process reads identical fleet state | NFR-1 | ✅ verify §1 |
| AC-4 | Thread list comes only from the checkpointer | FR-3.1 | ✅ verify §2 |
| AC-5 | Pending interrupt payload is readable and marked irreversible | FR-2.1 | ✅ verify §2 |
| AC-6 | Approve continues the unit to `published` | FR-4.3 | ✅ verify §3 |
| AC-7 | Reject aborts the unit | FR-4.3 | ✅ verify §3 |
| AC-8 | Untouched units stay parked while others resolve | FR-4.3 | ✅ verify §3 |
| AC-9 | Decisions persist across a real restart | NFR-1 | ✅ verify §3 |
| AC-10 | Cap **halts** spawning (returns False), not warns | FR-4.1 | ✅ verify §4 |
| AC-11 | A parked unit ages from `awaiting_approval` → `idle` | FR-3.3/4.2 | ✅ verify §4 |
| AC-12 | Console renders from live `governor.units()`, not fixtures | FR-5.2 | ✅ `gov/console.py` + `console_smoke.py` |

**Current status:** AC-1..12 pass. `gov/verify.py` (16 checks) covers AC-1..11;
`gov/console_smoke.py` covers AC-12 by driving the live HTTP read view and approval
endpoint. Both green against pinned deps.

---

## 7. Open questions

- **[OPEN-1] Cap semantics** — lifetime spend vs. live-unit spend (§4.4). `spent()`
  currently sums *all* units including `done`/`aborted`, so budget never reclaims.
  Decide and document.
- **[OPEN-2] Sandbox** — FR-1.2 enforcement. LangGraph does not solve `exec()`-grade
  isolation; a real boundary (subprocess/container/microVM, egress deny) is unbuilt.
- **[OPEN-3] The oracle for v1** — to leave simulated nodes, point the platform at a
  domain that already has a verifier (a repo with tests is the canonical one).
- **[OPEN-4] Product B positioning** — what Silk NOVA is and who buys it. Blocks the
  measurement loop and the draft-only duration.
- **[OPEN-5] External claims** — the Long-Horizon-Terminal-Bench figures (15.2%
  pass@1 / 4.3% mean) and the `deepseek-v4-flash` rename need source links before
  they're load-bearing.

---

## 8. Roadmap

1. ~~**Wire the console to `governor.units()`** (AC-12)~~ — **done** (`gov/console.py`,
   stdlib-only, live read view + approval queue + hard-cap-aware spawn).
2. **Swap simulated nodes for real agent work** against a repo-with-tests (OPEN-3) —
   the domain that already owns the oracle. ← next
3. **Sandbox the execution path** (OPEN-2).

---

## Appendix A — Traceability (insight → requirement)

| Insight (PROJECT-RECORD §) | Requirement |
|---|---|
| 1 · grades-on-"did it run" | measurable hypotheses (Product B) |
| 2 · `exec()` is not a sandbox | FR-1.1/1.2, OPEN-2 |
| 3 · can't gate money by reading code | FR-2.1 |
| 4 · iteration rate ↔ feedback rate | throttle (Product B) |
| 5 · self-written constraints are permanent | human-drained decisions |
| 6 · verifier is the bottleneck | OPEN-3, NFR-3 |
| 7 · reversibility is the precondition | §1.2 rule, FR-2.2/4.3 |
| 8 · two tangled projects | §2.3 split |

# Executors — Phoenix governs; something else may execute

**Status:** design. `worker.py` already isolates the one seam that matters:

> **given a task and a failing oracle, produce a patch.**

Today that seam calls `brain._chat` and applies the result in our own sandbox. It
could equally dispatch a run to an external coding agent in a cloud sandbox and take
the diff back. This document defines that boundary so Phoenix can adopt strong
execution infrastructure **without adopting anyone's control plane**.

The line to hold: **we do not compete with coding agents — we govern them.**

---

## 1. What is ours, permanently

Never delegated, because this *is* Phoenix:

- **The oracle** — what counts as done (the test suite, run by us, not the executor).
- **The gate** — the irreversible act (merge/deploy) stops at a human, always.
- **The economy** — budgets, promotion, reaping; an executor's run is charged to an
  agent's budget like any other work.
- **The board** — quorum before spending, disjoint evidence, duty to escalate.
- **The anchor** — careers, lessons, and the lineage of every decision.
- **Liveness** — a stalled executor is a stall we name and escalate (Article IX).

## 2. The interface

```python
class Executor(Protocol):
    name: str                      # "native" | "open-swe" | ...
    def run(self, task: Task, repo: RepoRef, budget: Budget) -> Result: ...

# Result = {diff: str, log: str, tokens: int, sandbox: str, error: str|None}
```

Rules any executor must satisfy to be legal under the constitution:

1. **It returns a diff, never a merge.** No executor gets push rights to a protected
   branch; the gate is ours (Article IV).
2. **It never scores itself.** We re-run the oracle on the returned diff, in *our*
   sandbox. A green claim from the executor is worth nothing (Article I.3).
3. **It is charged.** Real tokens/time come back in `Result` and debit the agent's
   budget; an executor that burns budget without moving the oracle gets its agent
   reaped (Article II).
4. **It declares its isolation.** `Result.sandbox` records what actually contained the
   run, and we report it — same honesty rule as `workspace.sandbox_mode()`
   (Article V.4).
5. **It is timed and breakered.** A hung run is a failed turn; three identical
   failures escalate once (Articles IX, III.1).

## 3. Candidate executors

| Executor | What it brings | What we keep doing |
|---|---|---|
| **`native`** (shipped) | our own `brain._chat` + netns sandbox; zero dependencies | everything |
| **Open SWE** (LangChain) | a hardened SWE agent on LangGraph — cloud sandboxes (E2B/Modal/Daytona), repo cloning, draft-PR plumbing, ~1.2k commits | oracle, gate, economy, board, lineage |
| **Cloudflare Computer** | persistent agent filesystem + container/isolate execution with real Linux userland and **real network** | as above, plus: network is a *capability grant*, see §4 |

Rationale for Open SWE specifically: it solved the expensive, unglamorous half —
isolated execution environments and GitHub plumbing — on the same LangGraph substrate
we already use, MIT-licensed. Rebuilding that is not where Phoenix's value is.

## 4. Network is a capability, not a default (the browser question)

A recurring request: *can agents browse the web?* Cloudflare Computer's container
backend advertises "full Linux userland, real binaries, real network" — so yes,
technically it could host a browsing agent. Under this constitution that is a
**capability grant**, and grants are governed:

- Default remains **no network** (Article V; our netns sandbox enforces it where the
  platform allows and reports honestly where it doesn't).
- A browsing executor is a **separate, named capability** — `can("browse")` — granted
  by tier and **board quorum**, not by an agent deciding it needs the internet.
- **Egress is allowlisted** per run (domains declared up front, recorded in the
  decision), because "real network" without a boundary is precisely the thing Article
  V exists to remove.
- **Everything fetched is ingested as data, never executed** (Article VI.2), with its
  source recorded — the anchor already enforces this shape for external knowledge.
- **The gate is unchanged**: browsing is reversible (reading), so it can run free
  under its grant; anything that *writes* to the world — posting, purchasing,
  submitting a form — is irreversible and stops at a human.

That is the honest answer: the capability is available, and the value of Phoenix is
that granting it is a governed decision with a scope, a budget, an audit trail, and a
revocation path — instead of an agent that quietly has the internet.

## 5. Build order

1. Extract the `Executor` protocol; move today's logic into `executors/native.py`
   (pure refactor, `verify_work` must stay 13/13).
2. `executors/openswe.py` — dispatch a task, collect the diff, charge the budget,
   re-run *our* oracle on the result. Feature-flagged by env; native stays default.
3. Record the executor on every work decision (lineage already has the field shape),
   so `credit(decision)` can compare executors on measured value.
4. **The eval race extends for free**: governance-over-native vs
   governance-over-OpenSWE on identical tasks and identical oracles — the first
   apples-to-apples comparison of a coding agent *inside* a constitution.
5. Only then consider a browsing executor, behind §4's grant machinery.

## 6. The honest line

Adding an executor adds a dependency, a vendor, and a bill — and the more capable the
sandbox, the more the gate matters. So: no executor gets credentials to the gate, no
executor scores its own work, every run is charged and timed, and the default stays
the boring, dependency-free native path that already ships green tests.

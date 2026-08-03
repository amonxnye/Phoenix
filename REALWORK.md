# Real Work — converting Phoenix from game to app development

**Status:** design → build next. The AoE world was the proving ground; this is the
plan for pointing the SAME governance machinery at real software development. Nothing
here replaces the constitution — every article transfers.

---

## 1. The translation table

The deep insight of the whole project: the game was never about the game. Each game
concept was a stand-in for a real one, and the mapping is exact:

| Game | Real work |
|---|---|
| The World (resources, oracle) | A git repository + its **test suite** |
| Vision ("reach the Castle Age") | A milestone ("ship feature X", "tests green on module Y") |
| Progress score 0–100% | % of the milestone's acceptance tests passing |
| Villager gathering | A sandboxed coding agent making a failing test pass |
| Structures & developments | Tooling the fleet builds itself (linters, CI jobs, scaffolds) |
| Decay & repair | Tech debt: code health metrics that degrade and need maintenance |
| Food spoilage | Stale branches / unmerged work losing value over time |
| Age advance (irreversible, gated) | **Merge to main / deploy / publish** — human gate |
| The market (trade surplus) | Converting surplus effort: docs, refactors, test coverage |
| Compute cap & agent budgets | Real token budgets per agent, real caps per project |
| Careers, lineage, skills | Identical — they transfer with zero changes |

The one-line law survives translation: **tests are the oracle; the merge is the gate.**

## 2. What stays literally the same

The anchor (memory, skills, careers, lineage, health telemetry), the board with
disjoint evidence, the governor's scored work reports, Article IX liveness, the
breaker, the chat layer, the permanent logs, the eval harness. This is why the game
phase was worth it: ~90% of the system is world-agnostic and already battle-tested.

## 3. What gets built (the new 10%)

### 3.1 The Workspace world (`gov/workspace.py`)
Replaces `sim.py`'s tables with:
- `tasks` — units of work: {id, title, acceptance (a test command), status,
  assigned_to, budget}. Tasks come from the human or the governor (board-voted).
- `oracle()` — runs the acceptance tests in the sandbox; returns pass/fail counts.
  **This is the score. No LLM judges whether work is done — pytest does.**
- `world()` → {tests_passing, tests_total, tasks_open, tasks_done, debt_score}.

### 3.2 The worker agent (Article V, finally enforced for real)
A coding agent that receives: one task, the relevant files, the failing test output.
It proposes a patch. The patch is applied and tested **in a sandbox with no
credentials and no network** — the sandbox is the enforcement of Article V, exactly
as the constitution promised ("roadmap" no longer).

Worker loop (one "gather" turn):
```
read task → read failing test → propose patch → apply in sandbox →
run acceptance → measured contribution = tests newly passing
```
Contribution feeds the SAME economy: promotion, budgets, reaps, careers.

### 3.3 The gate (Article IV, unchanged in spirit)
- Reversible: editing files in the sandbox branch, running tests. Runs free.
- Irreversible: **merging to main, pushing, deploying**. Herald parks at the gate
  with the diff, the test delta, and the cost of waiting (tasks blocked behind it).
- The human approves from the same console; careers record who wrote what, under
  whose approval, from which lesson — the lineage engine's real purpose (LINEAGE.md §5).

### 3.4 Real budgets
Game tokens become real tokens: each worker call's usage (already logged per call in
`model_calls`) debits the agent's budget. Article II reaps agents that burn budget
without moving tests. The cap is a real dollar ceiling.

## 4. Safety posture (why this is safe to try)

1. **Sandbox**: workers have no credentials, no network — they physically cannot
   exfiltrate or deploy (Article V: remove the capability).
2. **Oracle**: contribution is measured by tests, not self-report — reward hacking
   requires hacking pytest inside a no-network sandbox.
3. **Gate**: nothing reaches main without the human. The diff is the pending action.
4. **Budgets + liveness**: a stuck agent burns out and is reaped (II); a stuck world
   stalls loudly (IX). Detection-side monitoring (uber/ADR-style telemetry fields)
   can be layered on the worker later.

## 5. Build order (each step ships alone)

1. **Workspace world + oracle** on a toy repo inside the project (a `sandbox/`
   directory with a tiny module + failing tests). No model needed to test it.
2. **Worker agent v1**: one villager-class agent, one task, patch-propose loop via
   `brain._chat`; contribution = tests newly passing. Careers/lineage wired from day 1.
3. **Console: Workboard page** — tasks instead of resources; same health/hall pages.
4. **The merge gate**: git worktree branch per task; herald carries the diff; human
   approves → merge. (Push stays off until CONSOLE_TOKEN is set — enforced.)
5. **Board staffing** for real tasks; governor reports scored on tests-passing delta
   per real token (VIII.5 transfers verbatim).
6. **Scale out**: multiple workers, real repos, the eval harness pointed at real
   work — "which model governs software development best" is the endgame benchmark.

## 6. Sequencing with the Eval

The Eval (EVAL.md) and Real Work share the provider seam and the scorecard machinery.
Order: Eval first on the game world (cheap, safe, comparable), then Real Work step 1–2,
then the eval harness runs on the workspace world too — same leaderboard, real stakes.

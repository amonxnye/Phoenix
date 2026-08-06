# Next — `harness/builder`

**Where it stands:** the loop is proven on `main` (`workspace.py`, `worker.py`,
`/work`, a live DeepSeek agent that fixed 6 tests and got promoted), and **Step 1 is
now shipped** — the oracle is configuration, not a hardcoded sandbox. This branch also
holds BUILDER.md (scale-up design) and EXECUTORS.md (how external coding agents plug
in without taking the control plane).

## Step 1 — point the oracle at a real repository — **SHIPPED**

The single highest-value change in the whole project, and it was small.

- **Config:** `WORKSPACE_REPO` (path) + `WORKSPACE_TEST_CMD` (e.g. `python -m pytest -q`),
  plus `WORKSPACE_TEST_TIMEOUT`, `WORKSPACE_PROTECTED`, `WORKSPACE_ENV_PASSTHROUGH`.
  All default to today's toy sandbox, so nothing that worked before changed.
  `workspace.configure()` takes the same settings in-process.
- **`oracle()` reads whatever runner the repo uses** — pytest node ids
  (`tests/test_x.py::TestC::test_a`, verbose lines *and* the short-summary section)
  and unittest's verbose output, now fully qualified so two modules can't collide.
  A run that times out, crashes, or collects nothing returns `ok: False` and **no
  verdict** — it never reads as "zero failures", so an agent cannot be paid for
  breaking the suite into silence.
- **The target file is derived from the failing test**, not hardcoded: `test_calc.py`
  → `calc.py`, wherever it lives in the tree. Many modules, one loop.
- **The protected set grew with the blast radius**: tests, `conftest.py`, runner
  config, packaging, `.git`, and CI workflows are all unwritable. On a real repo the
  exam is not just the tests — it is everything that decides how they run.
- **Tasks and snapshots are scoped per repo**, so re-pointing the workspace can never
  mix two backlogs. Every file the fleet touches is snapshotted on first write, so
  `reset` works on any repo, git or not.
- **Acceptance:** `verify_work.py` is **39/39** — the original toy checks, the runner
  adapters against real pytest and unittest output, the no-verdict rule, and a full
  cycle against a *second* repository with a different root, layout, and test command.

**Answering "needs from you" #1:** no target repo was required after all. The proof is
a temp repo the acceptance suite builds, breaks, and fixes on every run — repeatable,
and it does not make the demo depend on one particular codebase.

## Step 2 — worktree isolation — **SHIPPED** (`gov/worktree.py`)

Every task gets its own `git worktree` on its own branch, cut from the base commit.
Your checkout is never written to — not even while agents are running — and two
agents cannot collide. It is also what makes the undo real: everything an agent does
is a branch, so a bad run costs a deleted branch and nothing else.

## Step 3 — the merge gate — **SHIPPED** (`gov/builder.py`)

The herald carries a **diff + test delta + lineage**, exactly as designed. A solved
task parks a dossier at the gate; a human runs `gate` / `show` / `approve` / `reject`.
Merging is the only irreversible act and it is never taken autonomously.

- **Risk classes are enforced, not advisory.** Paths touching auth/secrets, data
  migrations, infrastructure, CI or payments classify the dossier HIGH by pattern
  match — deliberately not a model call, since the risk gate must not depend on a
  judgement an agent could argue with.
- **Push rights: agents have none.** `worktree.push` exists, is only reachable from
  the gate's approve path, and only when the operator sets `BUILDER_ALLOW_PUSH`.
  Approving a merge is not approving a publish. (This implements the recommendation
  below rather than waiting on it — it is the safe default, and reversible if you
  decide otherwise.)
- **Tacit consent is NOT wired to this gate yet.** IV.7 for green low-risk diffs is
  deliberately left off until a human has watched a few runs. Every dossier waits.

## Step 4 — executor protocol — **seam shipped** (`gov/solver.py`)

The `Executor` boundary from EXECUTORS.md §2 is real: a solver is any callable taking
a request and returning `{files, notes}`. `native_solver` calls the one brain seam;
`verify_builder.py` injects scripted solvers and exercises the entire autonomous loop
with no model and no network. What remains is an *external* executor (Open SWE et al)
behind a feature flag — the interface it must satisfy now exists and is enforced by
the caller: it returns files not merges, it never scores itself, and it is charged.

One git worktree per agent so a team works in parallel without collisions; the diff is
taken from the worktree, never the main checkout. Keeps the netns sandbox for the test
run itself.

## Step 5 — the fleet — **SHIPPED** (`gov/campaign.py`, CAMPAIGN.md)

Not on the original list, because the original list assumed one agent per task. A
campaign points many agents at ONE problem and keeps pushing:

- a **champion commit** every round branches from, crowned only on strictly more
  passing tests — the score is monotonic, so an experiment's downside is zero;
- a **strategy ladder** so four agents try four ideas, not one idea four times, and
  each is told what has already been rejected;
- **escalation**: conservative and cool in round 1, stranger and hotter as rounds
  fail — creativity as a response to evidence;
- **staffing**: agents that spend their budget are reaped and successors enlisted, so
  the organization outlives its agents;
- stops on solved / dry / campaign budget, and parks the whole thing as ONE dossier.

Proven by `verify_campaign.py` (37/37), including a campaign where every agent is
ruinous on every round: the repository ends byte-for-byte unchanged.

## What is still missing

- **A board pre-vote on each dossier.** The design calls for disjoint evidence (does
  CI pass / does it serve the goal / what risk class) before a diff reaches a human.
  Today the gate carries the evidence but the board does not vote on it.
- **A PR instead of a local branch.** `approve` merges locally. Opening a real pull
  request through the GitHub API is the same act with a different transport, gated by
  the same token check.
- **Tacit consent (IV.7)** for green, low-risk diffs — off by choice, see step 3.
- **Task sources beyond failing tests**: issues, TODOs, a human's feature request.
  The oracle is unchanged; only where the backlog comes from grows.
- **A console page for the gate.** The CLI is complete; the workboard still shows only
  the workspace, not the pending dossiers.

## Needs from you

- **A repository to point it at.** The machinery is verified against generated repos
  on every CI run; what it has not had is a night against something you care about.
  Start with `--limit 1` on a repo whose tests you trust.
- **Whether to overrule the push default.** Agents cannot push; approval merges
  locally unless you set `BUILDER_ALLOW_PUSH=1`. Say so if you want the gate to open
  PRs instead, and it becomes the transport rather than the policy.

## Definition of "v1 shipped"

An agent opens a PR against a real repo, CI green, a human merges it, and the Hall of
Records shows the career that produced it.

**Where that stands:** everything but the "PR" is done — an agent takes a real repo's
failing test, works it in isolation, proves the fix with the repo's own suite, and
parks a reviewable diff that a human merges, with the career and lineage recorded.
Swapping the local merge for a pull request is the last mile.

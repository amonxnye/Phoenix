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

## Step 2 — worktree isolation — next

One git worktree per agent so a team works in parallel without collisions; the diff is
taken from the worktree, never the main checkout. Keeps the netns sandbox for the test
run itself.

## Step 3 — the merge gate (the headline)

The herald carries a **diff + test delta + lineage** instead of an Age. Human reviews
and merges. Board pre-vote with disjoint evidence (CI / goal served / risk class:
auth, infra, migrations). Tacit consent (IV.7) applies **only** to green, low-risk
diffs — never to anything touching the risk classes, and never to a red suite.

## Step 4 — executor protocol

Extract `Executor` (EXECUTORS.md §2), move today's logic to `executors/native.py` as a
pure refactor, then add `executors/openswe.py` behind a feature flag. Native stays the
default; the external executor is charged, timed, breakered, and never scores itself.

## Needs from you

- A decision on **push rights**: my recommendation is agents never get them; the merge
  gate posts a branch + PR and a human clicks merge. Step 3 is blocked on this and
  nothing else.
- Optionally, a **real target repository** to point a live run at. Step 1 no longer
  needs one to be verified, but the first genuinely external run should be a repo you
  chose:  `WORKSPACE_REPO=/path/to/repo WORKSPACE_TEST_CMD="python -m pytest -q"
  python3 gov/worker.py --agent dev-01`

## Definition of "v1 shipped"

An agent opens a PR against a real repo, CI green, a human merges it, and the Hall of
Records shows the career that produced it.

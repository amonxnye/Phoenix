# Next — `harness/builder`

**Where it stands:** the loop is *already proven* on `main` (`workspace.py`,
`worker.py`, `/work`, 13/13 checks, a live DeepSeek agent that fixed 6 tests and got
promoted). This branch holds BUILDER.md (scale-up design) and EXECUTORS.md (how
external coding agents plug in without taking the control plane).

## Step 1 — point the oracle at a real repository

The single highest-value change in the whole project, and it's small.

- Config: `WORKSPACE_REPO` (path) + `WORKSPACE_TEST_CMD` (e.g. `pytest -q`), defaulting
  to today's toy sandbox so nothing breaks.
- `oracle()` parses pass/fail from the real runner (pytest's summary line, or
  `--json-report` when available) instead of unittest's verbose output.
- Tasks come from failing tests first; issues/TODOs later.
- **Acceptance:** `verify_work` still 13/13 on the toy, and a second run against a
  small real repo (with deliberately failing tests) yields correct task sync.

## Step 2 — worktree isolation

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

- A **target repository** for step 1 (a small one with a test suite — could even be
  Phoenix itself, which would be a delightfully recursive demo).
- A decision on **push rights**: my recommendation is agents never get them; the merge
  gate posts a branch + PR and a human clicks merge.

## Definition of "v1 shipped"

An agent opens a PR against a real repo, CI green, a human merges it, and the Hall of
Records shows the career that produced it.

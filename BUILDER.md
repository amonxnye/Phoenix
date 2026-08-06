# Phoenix Builder — a governed organization that ships software

**Status:** foundation shipped (`workspace.py`, `worker.py`, the `/work` console);
**step 1 of §5 shipped** — the repo and its test command are now configuration, so the
loop runs on any tested codebase. This branch extends it from a toy sandbox to **real
repositories and systems**.
Where RESEARCH.md discovers and REALWORK.md set the thesis, Builder is the thesis
running at production scale.

> **Tests are the oracle; the merge is the gate.**

---

## 1. What already works (on `main`)

A sandboxed worker agent reads failing tests, patches code through the one brain
seam, and is paid only for tests it turns green — verified by the test runner, not
its own claim. It cannot edit the tests that judge it, has no credentials and no
network, and a patch that breaks the suite auto-reverts. It feeds the same economy,
careers, and lineage as the settlement's villagers. First live cycle: 3 bugs fixed,
3 functions written, 9/9, promoted to foreman.

That is the whole machine, proven on a toy module. Builder scales each axis.

## 2. The axes to scale

| Axis | Toy (shipped) | Real (this branch) |
|---|---|---|
| Codebase | one `calculator.py` | a real git repo, many modules — **done** |
| Oracle | one unittest file | the repo's full CI (pytest, lint, type-check, build) — **test command done** |
| Task source | failing tests | issues, TODOs, failing CI, a human's feature request |
| Isolation | in-process file writes | a git **worktree per agent** (already supported by the Agent layer) |
| The gate | none (toy) | **the pull request**: a human reviews the diff + green CI and merges |
| Fleet | one agent | a board-staffed team splitting a backlog |

## 3. The gate is the pull request

This is the natural end-state of Article IV. Reversible work — editing files in an
isolated worktree, running the test suite, iterating — runs free. The one
irreversible act is **merging to the main branch / deploying**. So the herald
becomes a **PR**: the agent that clears CI packages its diff, the test delta, and the
lineage of why each change was made, and parks at the human gate. You review and
merge (or the board, for low-risk changes under a policy you set; the human for
anything touching auth, migrations, or infra). Tacit consent (Article IV.7) applies:
a green, low-risk PR you ignore for an hour can proceed by board quorum — with the
full audit trail, and revertable because git.

## 4. Why the governance matters here specifically

Autonomous coding agents already exist; what they lack is the organization around
them. Builder adds:
- **Budgets that end runaway agents** — a coder burning tokens without moving tests
  green is reaped (Article II), not left thrashing.
- **A board that won't let one agent merge unreviewed** (Article VIII) — disjoint
  evidence: does it pass CI (Ledger), does it serve the goal (Growth), is it risky —
  auth/infra/migrations (Prudence).
- **Provenance as the product** (Article VII, the lineage engine): *which agent
  changed this line, under whose approval, to satisfy which test, derived from which
  lesson.* That is the artifact every eng org wishes it had.
- **Liveness** (Article IX): a coding org that stalls says exactly why — flaky test,
  blocked dependency, an unanswered review — instead of going quiet.

## 5. Build order

1. ~~Point `workspace.oracle()` at a real repo's `pytest` (config: repo path + test
   command) — the toy sandbox generalizes to any tested codebase.~~ **Shipped:**

   ```
   WORKSPACE_REPO=/path/to/repo WORKSPACE_TEST_CMD="python -m pytest -q" \
       python3 gov/worker.py --agent dev-01
   ```

   The oracle reads pytest node ids or unittest's verbose output; the file to edit is
   derived from the failing test rather than named in code; a run that cannot report
   results returns **no verdict** rather than "no failures", so silencing the suite
   pays nothing; and tests, runner config, packaging and CI are all unwritable —
   on a real repo the exam includes everything that decides how the exam is run.
   Verified by `gov/verify_work.py` (39/39), which builds, breaks and fixes a second
   repository with a different layout and test command on every run.
2. Worktree-per-agent isolation so a team works in parallel without collisions
   (the Agent tool's `isolation: worktree` is the model).
3. The **PR gate**: agent → branch + diff + CI result → human review/merge, via the
   GitHub API, token-gated. Careers/lineage record authorship and approval.
4. Board staffing over a real backlog (issues/TODOs), governor reports scored on
   tests-passing delta per token (VIII.5 transfers verbatim).
5. The eval race on real work: which frontier model runs the best governed
   engineering team — the endgame benchmark.

## 6. The honest safety line

Builder writes and merges code, so the gate is load-bearing. Defaults: no direct push
to protected branches, no CI secrets in the sandbox, human review required for
auth/infra/data-migration paths, everything revertable by git, and every merge
carries its provenance. The point is not an agent you trust; it is an organization
whose blast radius is structural — capped, gated, reviewed, and reversible.

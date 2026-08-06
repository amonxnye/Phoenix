# Next — `harness/data`

**Where it stands:** DATA.md defines the design — oracle = data contracts passing on
staging with before/after diffs; gate = any production write, deletion, backfill or
export. No code yet.

## Step 1 — a toy warehouse and contracts as the oracle

- `sandbox_data/`: a small SQLite/DuckDB warehouse with a few tables and deliberately
  broken transformations — the data twin of `calculator.py`, no cloud required.
- `data_world.py`: contracts (schema, ranges, nulls, uniqueness, referential
  integrity, freshness, row-count deltas) run against a **staging copy**; the oracle
  counts passing contracts.
- **Acceptance:** `verify_data.py` — a broken transformation fails its contract, a
  fixed one passes, and no check ever touches the production copy.

## Step 2 — the engineer agent

Failing contract → transformation patch → validated on staging → contribution =
contracts turned green. Identical shape to the code worker, which is the point:
the machinery is domain-agnostic.

## Step 3 — the diff discipline

An agent claiming "improved" must show it: row counts, distributions and key metrics
before/after, attached to the decision. A change with no diff is not a change worth
approving.

## Step 4 — the write gate

Migration/backfill dossier: the diff, the affected rows, the **rollback plan**, and
the lineage — parked for a human. Staging-only credentials in the sandbox, PII masked
in the agents' view by default. Production write access does not exist to be misused.

## Step 5 — decay economics

Freshness lapses and upstream schema drift are the settlement's decay mechanic,
unchanged: quality degrades, repair costs resources, deferred maintenance shows up as
a liability on the balance sheet.

## Step 6 — the analyst role

Claims in a report must **re-run** to be published (the research harness's evidence
discipline applied to BI). A number whose query doesn't reproduce is not a number.

## Needs from you

- Whether v1 stays on the local toy warehouse (my recommendation) or targets a real
  one — and if real, staging credentials only, never production.

## Definition of "v1 shipped"

A broken pipeline is detected by its contract, fixed by an agent, validated on
staging, and lands in production only after a human approves a dossier that includes
a rollback plan.

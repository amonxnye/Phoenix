# Next — `harness/ops`

**Where it stands:** OPS.md defines the design — oracle = the SLO recovering *and
holding*; gate = every production mutation, with one policy dial for a pre-authorized
low-risk class. No code yet. Phoenix's own outages (the silent stall, the OOM
crash-loop, the 502 wedge) wrote this design.

## Step 1 — read-only, against a toy service

- `ops_world.py`: a small service with a health endpoint and metrics; the oracle reads
  SLIs (error rate, latency, saturation) and scores the SLO.
- **Zero mutation capability** in v1 — no restart, no deploy, no scale. The agent
  observes and diagnoses only.
- **Acceptance:** `verify_ops.py` — a degraded service is detected, a healthy one
  scores clean, and the harness holds no credentials that could change either.

## Step 2 — the diagnosis agent

Signal → hypothesis → evidence gathered from logs/metrics → a **remediation proposal
dossier** (what, why, blast radius, rollback). Contribution is paid on *accepted*
diagnoses, never on activity.

## Step 3 — the durable-fix rule

This is the design's whole trick: contribution is paid on the **regression test or
alert** that would catch the incident next time, not on the restart that made the
symptom go away. An ops org that only restarts things earns nothing.

## Step 4 — the change gate + the policy dial

Every production mutation stops at a human. You may pre-authorize one narrow class
(e.g. "restart a stateless worker, ≤3/hour, business hours"), and **only that class**
may proceed under tacit consent. Everything else waits indefinitely — no exceptions,
no matter how long. Circuit breakers ban the "restart it again" loop (Article III.1).

## Step 5 — point it at Phoenix itself

The most honest demo in the family: the organization that keeps *itself* alive, with
its own watchdog, stall detection and permanent post-incident lessons. Everything
needed already exists in the live deployment's telemetry.

## Needs from you

- Whether step 5 targets the **live Railway service** (real, and therefore real risk)
  or a staging clone first. My recommendation: staging clone, then live read-only,
  then the policy dial — in that order, no shortcuts.

## Definition of "v1 shipped"

An induced incident on the toy service is detected, diagnosed with evidence, and
closed with a regression alert — and every proposed mutation waited for a human.

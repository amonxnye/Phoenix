# Phoenix Ops — a governed organization that runs systems

**Status:** design. The constitution pointed at **operations and reliability**:
watching services, diagnosing incidents, proposing and (under policy) applying fixes.

> **The oracle is the SLO; the gate is production.**

Phoenix has already lived this domain from the inside — its own audit found a 43-minute
silent stall, an OOM crash-loop, and a 502 wedge, and Article IX exists because of them.
This harness turns that experience outward.

---

## 1. The translation table

| Settlement | Ops |
|---|---|
| The World (oracle) | Live SLIs: error rate, latency percentiles, saturation, health checks |
| Vision | "99.9% availability, p95 < 300ms, zero unacknowledged pages this week" |
| Progress 0–100% | % of SLO budget intact / incidents closed with a durable fix |
| Villager gathering | An SRE agent investigating one signal or running one safe diagnostic |
| Structures | Runbooks, dashboards, alerts, regression tests for past incidents |
| Decay/repair | Literally alert rot, stale runbooks, expiring certs — assets that degrade |
| The gate | **Any production mutation**: deploy, rollback, restart, scale, config change |
| Liveness (IX) | An ops org that goes quiet during an incident is the worst failure mode there is |

## 2. The oracle

An incident is not "resolved" because an agent says so. Tiers:

1. **The SLI recovers** and stays recovered for a hold-down window — objective,
   instant, machine-read.
2. **A regression test or alert exists** that would have caught it — contribution is
   paid on the *durable* fix, not the restart. (This is Phoenix's central trick:
   pay for the thing you actually want.)
3. **The post-incident lesson enters the anchor** and steers future decisions
   (Article VI) — an ops org that learns is the entire promise.

## 3. The gate, and the one policy dial

Reversible (free): reading metrics, logs, traces; querying read replicas; running
diagnostics in a sandbox; drafting a fix; simulating a change.

Irreversible (human): deploys, rollbacks, restarts, scaling, config and DNS changes,
anything touching customer data. **The policy dial**: you may pre-authorize a narrow
class of low-risk, reversible-in-practice actions (e.g. "restart a stateless worker,
max 3/hour, during business hours") — and Article IV.7 tacit consent can apply *only*
to that class. Everything else waits for a human, forever, no matter how long.

## 4. Why governance beats a bare agent here

- **Budgets** stop an agent from thrashing an incident (II).
- **The board** brings disjoint evidence to a change: blast radius (Prudence), does it
  fix the SLO (Growth), can we afford the downtime (Ledger) — a change-advisory board
  that actually reads telemetry.
- **Circuit breakers** (III.1): three identical failed remediations escalate once,
  naming the blocker, instead of looping — the "restart it again" antipattern, banned.
- **Provenance**: every prod change traces to the alert, the diagnosis, and the
  approver. That's the audit trail compliance regimes already demand.
- **Liveness**: an org that stalls during an incident announces its binding constraint
  — and if the binding constraint is a sleeping human, it says so.

## 5. Build order

1. Read-only oracle against a toy service (metrics + health endpoint) — no mutations.
2. SRE agent v1: signal → diagnosis with evidence → proposed remediation dossier.
3. The change gate with the policy dial (pre-authorized safe class vs. human-only).
4. Post-incident lessons into the anchor; alerts/runbooks as decaying assets.
5. Point it at Phoenix's own Railway deployment — the org that keeps itself alive is
   the most honest demo in the family.

## 6. The honest line

Ops is where an autonomous mistake is instantly, expensively real. Defaults are
maximally conservative: read-only by default, no credentials for mutations in the
sandbox, human approval for every state change until an operator explicitly widens
the policy, and everything logged with its approver. The goal is an org that makes
*you* faster at operating, not one that operates without you.

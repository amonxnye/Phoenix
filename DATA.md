# Phoenix Data — a governed organization that builds and guards data

**Status:** design. The constitution pointed at **data engineering and analysis**:
pipelines that are validated before they land, analyses whose claims are checkable,
and datasets whose quality is a measured, decaying asset.

> **The oracle is the validation suite; the gate is a production write.**

---

## 1. The translation table

| Settlement | Data |
|---|---|
| The World (oracle) | Data contracts + quality checks: schema, ranges, nulls, referential integrity, row-count deltas, freshness |
| Vision | "Every table in the gold layer passes its contract; freshness < 1h; zero silent schema drift" |
| Progress 0–100% | % of contracts passing across the warehouse |
| Villager gathering | An engineer agent fixing one failing contract or building one validated model |
| Structures | Reusable assets: contracts, transformations, seeds, documentation, lineage maps |
| Decay | **Data quality genuinely decays** — freshness lapses, upstream schemas drift, definitions rot |
| The gate | **Any write to production tables, any deletion, any backfill, any external share** |
| Lineage | The engine Phoenix already has, applied to its native home: column-level provenance |

## 2. The oracle

1. **Contracts pass on a staging copy** — the transformation runs against sampled or
   staged data and every declared expectation holds. No pass, no contribution.
2. **The change is diffed, not asserted** — row counts, distributions, and key metrics
   before/after; an agent claiming "improved" must show the delta.
3. **Analyses are citation-checked against the data** — every number in a report must
   be reproducible by a query the harness re-runs (the research harness's evidence
   discipline, applied to BI). A claim whose query doesn't reproduce is not published.

## 3. The gate

Reversible (free): reading, profiling, building in a staging schema, running
validations, drafting migrations, writing analyses.

Irreversible (human): writing to production tables, dropping or altering columns,
backfills, deletions, retention changes, and **any export of data outside the
boundary**. Article V is literal: the sandbox holds staging credentials only — no
production write access exists to be misused, and PII-bearing columns are masked in
the agents' view by default.

## 4. Why an army fits this domain unusually well

Data work is embarrassingly parallel and objectively scoreable — the two properties
Phoenix needs. A fleet can hold hundreds of contracts, chase drift the moment it
appears, and keep documentation alive, while the board arbitrates what's worth doing:
freshness vs. cost (Ledger), does it serve the analytics goal (Growth), blast radius
of a migration (Prudence). And **decay is not a metaphor here** — the settlement's
maintenance mechanic maps one-to-one onto stale pipelines and rotting definitions.

## 5. Build order

1. A toy warehouse (SQLite/duckdb) + contracts as the oracle — the data twin of
   `sandbox/calculator.py`, no cloud needed.
2. Engineer agent v1: failing contract → transformation patch → validated on staging
   → contribution = contracts turned green.
3. The write gate: a migration/backfill dossier (diff, row deltas, rollback plan)
   parked for a human.
4. Freshness/drift as decaying assets with repair economics.
5. The analyst role: claims that must re-run to be published.

## 6. The honest line

Data harnesses fail dangerously when an agent can silently corrupt a source of truth.
Hence: staging-only credentials, masked PII, human approval for every production
mutation, a mandatory rollback plan in every dossier, and full lineage on anything
that lands. Governance turns "an agent touched our warehouse" from a nightmare into
an audit trail.

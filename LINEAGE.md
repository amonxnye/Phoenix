# Lineage — decision provenance for Phoenix

**Status:** study → design. The plan for our own small provenance engine, native to
the anchor, inspired by Semantica's ideas without adopting its weight.

---

## 1. The problem

Our log records *that things happened*, flat and chronological:

```
t6  [proposal]     governor proposes 'granary'
t6  [board]        [3/3] approved
t9  [development]  human adopted 'granary'
t10 [build]        built granary (+15% food yield)
```

A human infers the story; **the system cannot**. It doesn't know these four lines are
one causal chain, so it can't answer "why does the granary exist?", can't assign
credit to the *decision* that produced value, and can't detect when a new belief
contradicts the one a past decision was built on.

The reasoning store (`anchor.reasoning`, shipped with the /skills page) captures the
**why at decision time**. Lineage is the next step: connecting decisions to their
**inputs and their consequences** so the chain is machine-traversable both ways.

## 2. What Semantica gets right (the ideas we adopt)

1. **Decisions as first-class objects** — not log lines but records with inputs,
   concurrence (votes), authorization (the human), and effects.
2. **Provenance arrows** (their W3C PROV-O compliance is just standard names for
   these): `wasDerivedFrom` (decision ← lesson/fact), `wasAuthorizedBy` (build ←
   human/board), `wasGeneratedBy` (artifact ← decision).
3. **Conflict detection** — a new fact that contradicts a stored one is flagged
   *before* downstream reasoning builds on it (Constitution Art. VI.3, mechanized).

## 3. What we reject (the weight)

Neo4j/RDF backends, reasoning engines, a fast-moving v0.6 dependency — replacing our
auditable stdlib+SQLite anchor with a platform to *operate*. Violates NFR-4 (small
enough to audit in one sitting). Everything in §4 is plain SQLite + ~150 lines.

## 4. Design: the small engine

### 4.1 Schema (two additions to the anchor)

```sql
-- every event gets identity + a causal parent (already have id via knowledge.id)
ALTER TABLE knowledge ADD COLUMN caused_by INTEGER;      -- event id, nullable

-- decisions as first-class objects
CREATE TABLE decisions(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  turn        INT,
  actor       TEXT,        -- director | board | governor | human | <agent-id>
  decision    TEXT,        -- "build granary"
  why         TEXT,        -- reasoning at decision time
  derived_from TEXT,       -- JSON list of inputs: skill ids, fact ids, event ids
  authorized_by TEXT,      -- 'human' | 'board:3/3' | 'policy'
  effect_event INTEGER,    -- the event this decision produced (FK → knowledge.id)
  outcome     TEXT         -- filled in later: measured effect ("food yield 20→30")
);
```

### 4.2 API (anchor)

```python
did = decision_open(turn, actor, decision, why, derived_from, authorized_by)
record(turn, kind, note, caused_by=eid)      # events chain to their cause
decision_close(did, effect_event, outcome)   # link consequence + measured result
lineage(event_or_decision_id) -> list        # walk caused_by/derived_from to roots
consequences(decision_id) -> list            # walk forward
```

### 4.3 The two queries that justify the whole feature

- **`why(x)`** — walk backwards: build ← adopted-by-human ← board 3/3 ← proposed
  because lesson #12 ← lesson from retrospective at t30 ← the events it digested.
  *Every artifact explainable to its roots.*
- **`credit(decision)`** — walk forwards: decision → builds → yield change → extra
  resources banked. Attribute measured value to *decisions* (and to the lessons
  behind them), not just to gathering. This upgrades the economy: promote agents —
  and score *lessons* — by the value their decisions caused.

### 4.4 Conflict detection (v2, cheap version)

On `ingest()` / `skill_add()`: embed-free heuristic — same topic/subject with an
opposing numeric or categorical claim → mark both `disputed`, surface in the console,
and exclude disputed knowledge from `_situation()` until the human or a retrospective
resolves it. No reasoner required; a WHERE clause and a flag.

### 4.5 UI

- `/skills` reasoning table gains a "trace" link per decision → expands the backward
  chain inline (one recursive SQL query).
- A `lineage` panel on the dev-proposal card: "proposed because …" already; add
  "descended from lesson #N" once `derived_from` is populated.

## 5. Why this matters beyond the game

When Phoenix governs real work (the coding-agent layer), provenance is the product:
*which agent changed this code, under whose approval, based on what lesson.* This
engine is that answer, already running, before the first real task executes.

## 6. Build order

1. `caused_by` on events + thread the ids through the existing record sites (small).
2. `decisions` table + open/close around the five decision points we already reason
   about (staff, retask, build, gate, proposal).
3. `lineage()/consequences()` walkers + /skills trace UI.
4. Outcome measurement: close decisions with before/after deltas (yield, progress).
5. Disputed-knowledge flag on ingest.

Each step ships alone and is testable in `verify_sim.py`.

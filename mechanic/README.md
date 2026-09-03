# The Software Mechanic

Phoenix's governance model pointed at other people's code. A mechanic opens the
machine, measures what it finds, and tells you what is worn, what is unreachable and
what is unsafe — in the order that matters. It does not rebuild the engine.

Spec: `phoenix-analyst-srs-and-product-spec.md` · Build order: the engineering
handoff notes · Rules: [`CHARTER.md`](CHARTER.md)

## Where it stands — Milestone 1

The handoff says build the index first and *do not proceed until it works*. This is
that milestone, done to its gate:

| Gate | Required | Measured |
|---|---|---|
| corpus | ≥ 100k LOC | 297k LOC (the Python stdlib, 670 files) |
| index build | < 15 min | ~3.7 s (17,165 symbols, 188k edges) |
| each of the four queries | < 1 s | slowest ~530 ms (`unreachable_from`) |
| answers | correct | proven on Phoenix itself — see below |

**Correct is the hard word.** The first run on `gov/` reported 27 dead functions.
All 27 were reachable — only through `do_GET`/`do_POST` on a `BaseHTTPRequestHandler`
subclass, which the analyst correctly refused to judge and then failed to propagate.
That one design error, fixed at the index (`Index.entry_points`), left **one**
finding, and it is real: `economy.can` is defined and never called.

Every wrong answer from that run is now a regression test in `verify_mechanic.py`.

## Try it

```
python -m mechanic analyse gov --name phoenix-gov      # index + liveness + report
python -m mechanic query gov callers_of anchor.record   # one of the four queries
python -m mechanic query gov unreachable
python -m mechanic repos                                # the fleet under watch
python mechanic/verify_mechanic.py                      # the Milestone 1 gate
```

Local paths only. Cloning by URL arrives with the ingestion service (R1); the
read-only credential it needs is the part that must not be improvised.

## What is deliberately not here yet

In the handoff's order, because each milestone answers a question that decides
whether the next is worth building:

- **M2** Decomposer (units, centrality, the cost manifest) and the Historian
- **M3** the first model-driven analyst — drift
- **M4** the full panel, the adversarial reviewer, the Governor
- **M5** the report writer proper
- **M6** disclosure routing and the public dashboard — *only after the reports are good*

`store.py` is already multi-repo and `summary()` already counts what the landing
page will show, so M6 is a rendering task when its turn comes, not a data-model one.

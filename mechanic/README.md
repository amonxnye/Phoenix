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

## Every language in the tree

The first public run was pointed at a TypeScript repository and came back in 0.4 s
with no findings and no refusal: only `.py` files were read, and nothing said so.
Now every source file the tree contains is a unit (`polyglot.py`), and what each
one gets is stated rather than assumed:

| | Python | JavaScript / TypeScript, Go, Rust, Java, Ruby, PHP, C#, C/C++, Swift, Kotlin, Scala, shell, SQL |
|---|---|---|
| dead code with a call-graph proof | yes (the index) | **no** — recorded as a refusal per language |
| a top-level function whose name appears nowhere else in the tree | (the graph says more) | yes, as a text fact in those words (JS/TS, Go, Rust, PHP) |
| security text facts — secrets, `eval`, SQL built from values, TLS off, HTML sinks, weak randomness for tokens | yes | yes |
| slop text facts — empty catch, stubs, commented-out code, unused imports, duplicated bodies | yes (`slop.py`) | yes |
| known-vulnerable dependencies (OSV.dev) | requirements | package-lock.json |
| a direct dependency a major version behind its registry | requirements | package.json |
| the judged panel — quality, security, drift, critic, slop analysts | yes | yes, with a checklist read from the text |

A tree with no recognised source at all is a refusal too, not "complete, 0 findings".

## The verdict — the repository as a system

A list of line-level findings is not what an operator reads first. `posture.py` reads
the repository the way a security reviewer does and says, in one sentence, whether it
may face the Internet — then a table, severity-ranked, with an **assessment** per row:

| Severity | Finding | Assessment |
|---|---|---|
| critical | authentication is optional: the check on `CONSOLE_TOKEN` is skipped when it is unset | Fix before Internet exposure |
| high | the code sandbox is a subprocess with no filesystem, user or resource isolation | Fix before real autonomous coding |
| medium | request bodies have no size ceiling | DoS risk |
| medium | a credential is kept in browser storage; the HTML escaper leaves quotes unescaped | XSS could compromise the operator token |
| medium | 2 of 4 dependencies are not pinned to an exact version | Supply-chain / reproducibility risk |
| low | `.gitignore` does not exclude .env or key files | Secret-leak prevention gap |

That table is the mechanic's own reading of Phoenix, and `verify_mechanic.py` pins it:
the run on this repository must reproduce every row. Under the table comes **what held
up** — no committed secret, TLS never disabled, no SQL built from a value, no vulnerable
package — so the reader knows what was examined and passed, not only what failed.
Every row is a text fact with a file and a line; a check about absence names exactly
what it looked for, so it can be refuted by pointing at the line it missed.

## Error handling — read the way the paper reads it

Rubio-González and Liblit ("Finding Error-Handling Bugs in Systems Code Using Static
Analysis", 2011) confirmed 312 error-handling bugs across Linux file systems, and 86%
were one shape: a function returned an error and the caller never saved it. Their
strongest signal was **inconsistency** — "unsaved at 35 call sites, but saved at 17
others" — and their reports carried the **path** the error took. `errors.py` asks the
same questions with what the mechanic has (Python's AST; declarations by shape for
JavaScript and TypeScript), and every finding carries its path as evidence:

- a status or value-or-None result **discarded** at a call site while other callers save
  it — the saving callers are the specification;
- a value that **may be None** used as a value with no check on the path (the paper's
  "there is a check for NULL, but the error check is missing");
- a docstring that says **never raises** while a raise leaves the function, or a
  `Raises:` section that misses one.

Resolution is by name, so a name the tree defines twice is not judged, and a receiver
the tree does not define (`ast.parse`, `thread.start`) never matches a same-named
function. Run on itself, the analyst found a queued analysis that could be dropped
between a lock release and the next start, and a docstring that promised more than
the code kept; both are fixed, and the mechanic on itself is back at zero.

## Measured, not judged — complexity, and where to look first

`metrics.py` measures every unit: McCabe's cyclomatic complexity per function, Halstead
volume, the maintainability index, and churn from the history where there is one. The
numbers are facts; "too complex" is an opinion (Charter §2), so they are never findings.
They order the work — after centrality, the riskiest unit is analysed first, so a budget
that halts partway has read the likeliest code — and they appear as **where to look
first** in the report and on the page, and in each analyst's context. The risk score is
the transparent product of the measured factors with its weights in the code; it is not
a fitted model, because there is no bug history here to fit one to.

A framework for AI code review, sent with the brief, asks for more than this. What the
mechanic does with each of its pillars, and why:

| The framework asks for | The mechanic does | Why |
|---|---|---|
| interprocedural dataflow (weighted pushdown systems) for error propagation | the same three questions, per function, on the AST and by shape (`errors.py`), every finding with its path | a WPDS over Python and TypeScript is a research project; the per-function facts are checkable today and stated as facts |
| cyclomatic complexity, Halstead, maintainability, churn entropy | measured for every unit; order the work; shown as a reading order | facts, cheap, and honest as measurements — never as findings |
| a fitted risk model (logistic regression on bug history) | a transparent score with stated weights | no bug history to fit; an invented coefficient would read as evidence |
| Markov failure probability, Bayesian root cause | not adopted | no transition or cause data exists; a number without a measurement behind it is a wish |
| symbolic execution with an SMT solver | not adopted | out of scope for a stdlib-only mechanic; the judged panel reads the paths instead |
| differential (diff-aware) analysis | the watch: a run per new commit, findings carried across by fingerprint, fixed-upstream when they vanish | already the mechanic's model of change |
| chain-of-thought with the path stated | every analyst is asked for the path: produced, received, used | the paper's sample trace, in the prompt |

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

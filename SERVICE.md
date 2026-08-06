# The gate, as a service

`gov/gate.py` serves the review surface over HTTP: the workspace, the fleet, the
dossiers waiting at the gate, and the ledger of what the agents actually did. It runs
in two shapes from one codebase.

```bash
python3 gov/gate.py --repo ~/yourrepo     # one operator, one repository, loopback
python3 gov/gate.py --serve --port 8788   # several visitors, a workspace each
```

`index.html` is both the landing page and the console. Served by an instance it finds
`/api/state` and fills with that instance's real data; opened anywhere else it says
plainly that nothing is running behind it. It never shows a sample queue — a page that
invented a dossier would be lying about the one thing this system asks you to trust.

---

## 1. Identity without accounts

Nobody signs up. On first contact a visitor is issued a **guest session**: a random
id in a cookie, signed with a server-held secret (`guests.py`).

- `HttpOnly` — page script cannot read it.
- `SameSite=Lax` — another site's form cannot carry it.
- HMAC-signed, compared in constant time — it cannot be forged, edited or guessed.
- Fourteen days idle and both the session and its workspace are swept.

This is **not** authentication. Lose the cookie and you lose the workspace; anyone
holding it is you. That is the right trade for a disposable sandbox and the reason
nothing valuable may live in one. `verify_guests.py` spends 48 checks trying to break
it, because a signing bug here is not cosmetic: the gate performs an irreversible act.

## 2. Authority comes from the session, never from the request

No route accepts a repository path, a branch name or an agent id from the caller.
`Service.repo_for(sid)` is the only function that answers *what may this visitor act
on*, and it reads the session, not the request.

```
POST /api/gate/approve  {"id": 7}
    ↓
service.owns(sid, 7)        # gate_get(7).repo == repo_for(sid)?
    ↓  no  → 403, before anything happens
    ↓  yes → builder.approve(7, by=<guest label>)
```

The ownership check runs against the stored row immediately before the merge. A guest
cannot reach another guest's branch by guessing its number, and `verify_gate.py`
proves it over real HTTP with two real sessions.

**The UI adds no authority.** Approving goes through `builder.approve` exactly as the
CLI does — same merge guard, same branch deletion, same lineage record, same refusal
to push without `BUILDER_ALLOW_PUSH`. A page that could merge by another path would be
a second gate, and two gates are no gate.

## 3. What isolates a guest

| | |
|---|---|
| **Workspace** | Its own directory, its own `git init`, named by hash of the session id |
| **Backlog** | Tasks are keyed by repository, so no two guests share one |
| **Budget** | Agent ids are namespaced (`guest-3f9a2c-01`). Budgets are keyed by name, so a shared `dev-01` would let one visitor spend another's |
| **Metrics** | Every aggregate is scoped by repository; two workspaces never mix into one number |
| **Lessons** | Deliberately **shared**. What the organization paid to learn is organizational, exactly as in the settlement |

## 4. What bounds a stranger

Article II already bounds what a fleet may spend. These bound how often an anonymous
visitor may start one.

| Variable | Default | What it limits |
|---|---|---|
| `GATE_MAX_RUNS` | 8 | runs per guest session |
| `GATE_MAX_CONCURRENT` | 2 | runs in flight across the instance |
| `GATE_SECRET` | generated | set it, or a restart signs everyone out |
| `GOV_DATA_DIR` | beside the code | put it on a volume and the ledger survives |

Requests must be JSON (`415` otherwise) and same-origin (`403` otherwise) — an HTML
form can be submitted across origins and a JSON body cannot.

**A public deployment merges code.** Put it behind TLS and something that limits who
can reach it. `--host` warns when you bind past loopback.

## 5. Starter workspaces

A guest needs a repository that behaves like one of theirs. `starter.py` builds one on
demand: real `git init`, real modules with real defects, real `unittest` suite. It then
**runs the suite to confirm the tests it claims are red actually are** — a starter that
provisions green is refused rather than handed out, because it would waste a run and
quietly break the only measurement the system rests on.

## 6. The ledger

`metrics.py` records **one row per sortie, kept or thrown away**, written by the caller
that ran the oracle — never by the agent. Failures are the point: a store that only kept
successes would make every fleet look brilliant and answer none of the questions worth
asking.

- `by_agent` — who earns their tokens
- `by_strategy` — which of the six approaches actually pays
- `by_attempt` — whether the retry ladder is worth its cost
- `outcomes` — a closed vocabulary: `refused`, `no-verdict`, `regression`, `no-change`,
  `executor-error`, `partial`, `kept`

The vocabulary is matched against the *literal* strings `builder.try_patch` produces.
`verify_metrics.py` pins those strings, so a reworded message fails the suite rather
than silently filing real failures under the wrong heading.

```bash
python3 gov/smoke.py           # scripted executors — measures the harness
python3 gov/smoke.py --live    # the configured model — measures the model
```

The report says which mode it ran in, because rehearsal numbers measure this code and
live numbers measure a model, and quoting one as the other would be dishonest.

## 7. Lessons, from the settlement

The settlement distils a retrospective when a vision completes and files it in the
skills store, where it steers every later decision. The Builder now does the same:

- **Every sortie is handed the lessons already paid for** — including the reasons humans
  gave when they rejected work at the gate. Previously only campaigns were; a lone agent
  was the one path that never saw what the organization had learned.
- **A run distils new lessons from its own ledger** (`builder.retrospective`), derived
  from measured outcomes rather than from a model call — so they have to be true, and an
  instance with no API key still learns.
- **Knowledge expires.** `anchor` keeps a bounded live set; duplicates are dropped.

---

## Acceptance

| Suite | Checks | What it defends |
|---|---|---|
| `verify_guests.py` | 48 | forged, edited, truncated and replayed cookies |
| `verify_metrics.py` | 65 | the ledger's arithmetic, its scoping, the starters |
| `verify_gate.py` | 67 | the service over real HTTP: isolation, CSRF, a real merge |
| `smoke.py` | 21 steps | the whole product end to end, with the numbers |

Nothing below the HTTP client is mocked. Real servers, real ports, real `git init`,
real merges. The scripted solver stands in for the model so the failures these suites
catch are the harness's, not a model's bad day.

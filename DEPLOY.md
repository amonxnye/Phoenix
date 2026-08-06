# Deploying

Both surfaces are standard-library HTTP servers, so a platform can run them with no
web framework. Everything they need is in the repo.

| Surface | Start command | Who it is for |
|---|---|---|
| **The gate** (default) | `python gov/gate.py --serve --host 0.0.0.0` | visitors — a guest session and a starter repository each |
| Your own repository | `python gov/gate.py --repo /path/to/repo` | one operator, on their own machine |
| The settlement console | `python gov/sim_console.py --seed` | the Age of Empires simulation |

`Procfile` runs the first. Switching the deployment back to the settlement console is
a one-line edit to that file.

---

## What the image contains

`Dockerfile` is the build. It installs `git` — not optional, since every attempt runs
in a worktree and the branch is the only undo — and `requirements-gate.txt`, which is
just `openai`.

The gate imports no langgraph, langsmith or langchain; that tree belongs to the
settlement console and is ~70MB of install the service never loads. `requirements.txt`
remains the full set for running everything from a checkout.

Verified by building and running the image, not by reading it: `git available: True`,
a real workspace provisions inside the container, and both `verify_gate.py` (87/87)
and `smoke.py` (21/21) pass in it.

## The gate as a public service

`gate.py` reads `$PORT` and the `Procfile` binds `0.0.0.0`. `index.html` is served from
the repository root — it is both the landing page and the console.

**Read [`SERVICE.md`](SERVICE.md) before exposing it.** The short version: authority
comes from a signed guest session, never from the request, and approving still merges
through `builder.approve` exactly as the CLI does.

### Environment

| Variable | Purpose |
|---|---|
| `GATE_SECRET` | signs guest sessions. **Set it** — without it a key is generated per boot on an ephemeral filesystem, so every redeploy signs everyone out |
| `GOV_DATA_DIR=/data` | a mounted Volume, so the gate queue, sessions and the ledger survive a redeploy |
| `GATE_TRUST_PROXY=1` | set in the `Procfile`: behind a platform proxy the socket peer is the proxy, so `X-Forwarded-For` is the visitor |
| `BRAIN_BASE_URL` / `BRAIN_API_KEY` | the model the agents use. **Without it the service still runs** — visitors get a workspace and can read the console, the gate and the ledger, and the Run buttons are disabled with the reason |
| `DEEPSEEK_API_KEY` | the simpler alternative (needs `pip install openai`) |

### What a stranger may spend

Agent budgets (Article II) bound what one fleet spends. These bound how often an
anonymous visitor may start one. A per-session cap alone is not a cap — clearing a
cookie buys a new session — so a run must clear all three.

| Variable | Default | Bounds |
|---|---|---|
| `GATE_MAX_RUNS` | 3 | runs per guest session |
| `GATE_MAX_RUNS_PER_IP` | 6 | runs per address per hour |
| `GATE_MAX_RUNS_PER_HOUR` | 25 | runs per instance per hour — **the one that bounds the bill** |
| `GATE_MAX_CONCURRENT` | 2 | runs in flight |
| `GATE_MAX_WORKSPACES_PER_IP` | 12 | starter repositories built per address per hour |

Defaults are sized for a public demo with a real API key behind it, because that is
the case where being wrong costs money. Raise them deliberately.

**Start with no key.** Deploy, watch how people use it, then add `BRAIN_API_KEY` once
you have chosen your ceilings. The console degrades honestly without one.

### Disk

Each guest gets a real git repository and the agents cut worktrees inside it. Sessions
idle for a fortnight are swept along with their workspaces, on an hourly timer
(`gate.py --sweep` runs it once by hand). Without a Volume the whole lot is ephemeral,
which is fine for a demo and wrong for anything else.

---

## The settlement console

`web: python gov/sim_console.py --seed` in the `Procfile`.

| Variable | Purpose |
|---|---|
| `SEED=1` | seed a demo game on boot |
| `GOV_DATA_DIR=/data` | a Volume, so game state survives redeploys |
| `CONSOLE_TOKEN` | locks every mutating endpoint; pages stay readable |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | the brain |

It has **no auth and no multi-tenancy** — it was built for one operator. Don't expose
a public URL you wouldn't want a stranger approving Age-ups on. That gap is exactly
what the guest sessions in `gate.py` exist to close, and only `gate.py` has them.

## State persistence

The container filesystem is ephemeral. For durable state either attach a **Volume**,
mount it at `/data`, and set `GOV_DATA_DIR=/data`; or swap SQLite for Postgres — the
design isolates that to one function (`connect()`).

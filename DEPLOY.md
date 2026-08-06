# Deploying to Railway

Both consoles are standard-library HTTP servers, so Railway can run either with no web
framework. Everything either needs is already in the repo.

## Two consoles, one Procfile

`Procfile` names exactly one process. Point it at whichever console this deployment is
for:

```
web: python gov/lab_console.py        # Phoenix Lab — evidence, replication, the gate
web: python gov/sim_console.py --seed # the settlement (AoE economy) — the original testbed
```

## What's wired for Railway

- **`$PORT` / `0.0.0.0`** — both consoles read Railway's injected `PORT` and bind
  `0.0.0.0` in production. Locally (no `PORT` set) they default to `127.0.0.1:8788`
  instead — `HOST`, if set, always wins over that default.
  **If a console binds `127.0.0.1` inside the container, Railway's proxy cannot reach
  it, the deploy never becomes healthy, and Railway keeps serving the last successful
  build.** That reads exactly like "my push didn't ship" when it actually means "the
  new build can't be reached" — check this first if a deploy looks stuck on old code.
- **`requirements.txt`** — Railway's Python builder installs it automatically.

## Deploy steps

1. In Railway: **New Project → Deploy from GitHub repo → `amonxnye/Phoenix`**, and pick
   the branch this environment should track (Settings → Source → Branch). Enable
   "Auto deploys when pushed to GitHub" if you want every push to redeploy.
2. Set `Procfile` to the console you want (see above) and push.
3. Railway detects Python, installs `requirements.txt`, and runs the `Procfile`.
4. Open the generated URL.

**If the live URL doesn't match what you just pushed**, check, in order:
1. **Settings → Deploy → Start Command.** A Start Command set here *overrides the
   Procfile entirely* and does not update itself when the Procfile changes. Clear it
   if you want the Procfile to be the source of truth.
2. **Deployments tab** — does the newest entry match your latest commit, and does it
   say "Success"? A failed or still-building deploy means the previous build is still
   what's live.
3. **The host-binding note above** — a console that can't be reached on `0.0.0.0`
   never passes Railway's health check, so the platform falls back to the last good
   build without necessarily surfacing that clearly.

## Environment variables (Railway → Variables)

| Variable | Purpose |
|---|---|
| `PORT` | Injected by Railway automatically — don't set this by hand |
| `GOV_DATA_DIR=/data` | Point the SQLite DBs at a mounted **Volume** so state survives redeploys |
| `CONSOLE_TOKEN` | Gate every write (decide/run/spawn/etc.) behind a shared secret; unset means open on that host |
| `DEEPSEEK_API_KEY` | Turns on the DeepSeek brain (`brain.py`); also `pip install openai` |
| `DEEPSEEK_MODEL` | Optional model override (default `deepseek-chat`) |
| `SEED=1` | Settlement console only — seed a demo game on boot (or keep `--seed` in the Procfile) |

## State persistence

Railway's container filesystem is ephemeral — without a Volume, state resets on every
redeploy. For durable state either:

- attach a **Railway Volume**, mount it (e.g. `/data`), and set `GOV_DATA_DIR=/data`; or
- swap SQLite for Postgres — the design already isolates this to one function per
  module (`connect()`/`_conn()`), and Railway offers managed Postgres as a plugin.

## Note

**Phoenix Lab has no accounts, by design** — every visitor is a guest, and the evidence
store, the gate, and skills learnt are shared collective state for anyone who can reach
the URL. Anyone with the link can decide at the gate unless `CONSOLE_TOKEN` is set.
Don't expose a public URL you wouldn't want anyone approving a critique or dossier on —
set `CONSOLE_TOKEN`, or keep it private, until real auth exists.

The **settlement console** is operator tooling with no auth or multi-tenancy either —
same rule applies: keep it private or set `CONSOLE_TOKEN` before exposing it.

# Deploying the Governor console to Railway

The console is a standard-library HTTP server, so Railway can run it with no web
framework. Everything it needs is already in the repo.

## What's wired for Railway

- **`Procfile`** — `web: python gov/sim_console.py --seed` is the start command.
- **`$PORT` / `0.0.0.0`** — `sim_console.py` reads Railway's injected `PORT` and binds
  `0.0.0.0` (locally it still defaults to `127.0.0.1:8788`).
- **`requirements.txt`** — Railway's Python builder installs it automatically.

## Deploy steps

1. Push this branch (already done) and, in Railway, **New Project → Deploy from GitHub
   repo → `amonxnye/Phoenix`**, branch `claude/project-review-1l2hho`.
2. Railway detects Python, installs `requirements.txt`, and runs the `Procfile`.
3. Open the generated URL — the AoE console renders live.

## Environment variables (Railway → Variables)

| Variable | Purpose |
|---|---|
| `SEED=1` | Seed a demo game on boot (or keep `--seed` in the Procfile) |
| `GOV_DATA_DIR=/data` | Point the SQLite DB at a mounted **Volume** so game state survives redeploys |
| `DEEPSEEK_API_KEY` | Turns on the DeepSeek brain (`brain.py`); also `pip install openai` |
| `DEEPSEEK_MODEL` | Optional model override (default `deepseek-chat`) |

## State persistence

Railway's container filesystem is ephemeral — without a Volume, the game resets on
every redeploy. For durable state either:

- attach a **Railway Volume**, mount it (e.g. `/data`), and set `GOV_DATA_DIR=/data`; or
- swap SQLite for Postgres — the design already isolates this to one function
  (`connect()`), and Railway offers managed Postgres as a plugin.

## Note

This deploys the operator console for **your own tooling** — there is no auth or
multi-tenancy yet (that's out of scope for the MVP). Don't expose a public URL you
wouldn't want anyone approving Age-ups on; keep it private or add auth first.

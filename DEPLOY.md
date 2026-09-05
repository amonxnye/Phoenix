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
| `BRAIN_API_KEY` | Turns on the brain for EVERY model call (settlement and mechanic) against the platform's own gateway, `https://api.ripaplatform.com/v1`, model `qwen3:30b` |
| `BRAIN_BASE_URL` / `BRAIN_MODEL` | Optional: another OpenAI-compatible endpoint, or another model the gateway serves (`GET /v1/models` lists them) |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | The original provider; used only when `BRAIN_API_KEY` is unset. Remove it once the switch is made so a missing key fails loudly instead of falling back |
| `MECHANIC_BASE_URL` / `MECHANIC_API_KEY` / `MECHANIC_MODEL` | The mechanic alone on its own server, e.g. `https://api.ripaplatform.com/v1` + `qwen3:30b`; the settlement keeps its provider. All three are required — a gateway never picks the model |
| `MECHANIC_PRICE` | `self-hosted` (0¢, the default for an Ollama tag), `cheap` or `strong` — which price table meters the mechanic's budget |
| `BRAIN_TIMEOUT_S` | Per-attempt timeout for model calls (default 300) |
| `NET_RETRIES` / `NET_BACKOFF_S` | Every outbound request (model, GitHub archive, OSV.dev) is retried on timeouts, connection loss, 429 and 5xx — including Cloudflare's 52x — with exponential backoff and jitter: `NET_RETRIES` retries after the first attempt (default 5), first wait `NET_BACKOFF_S` seconds (default 1, doubling, capped at 30). 401/402/404 are never retried; a request not declared idempotent is made once |
| `NET_BREAK_AFTER` / `NET_COOL_S` | Circuit breaker per host: after `NET_BREAK_AFTER` calls (default 2) have exhausted their retries against one host, calls to it fail at once for `NET_COOL_S` seconds (default 60), then one probe is let through |
| `MECHANIC_PROVIDER_DOWN_AFTER` | The mechanic halts a run after this many consecutive failed model calls (default 5) instead of asking every unit |

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

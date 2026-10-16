# Deploying Phoenix

Phoenix is one process — the Governor console and the Article XI improvement
cycle — on a standard-library HTTP server. It needs Python 3.11, the packages
in `requirements.txt`, a writable data directory, and a model endpoint.

## RupertCloud (own host) — the current production target

Running on the same machine as the Ollama gateway removes the Cloudflare 100 s cap and
the public round trip from every model call, and gives the record a real disk.

### With Docker Compose

```
git clone https://github.com/amonxnye/Phoenix && cd Phoenix
cp .env.example .env            # BRAIN_API_KEY, CONSOLE_TOKEN, BRAIN_MODEL
PHOENIX_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
curl -s http://127.0.0.1:8788/healthz
```

- The record is the named volume `phoenix-data`, mounted at `/data`. `docker compose down`
  keeps it; only `down -v` erases it.
- `BRAIN_BASE_URL=http://host.docker.internal:11434/v1` reaches Ollama on the host
  directly (compose maps that name to the host gateway).
- `security_opt: seccomp=unconfined` lets the worker sandbox and the improvement oracle
  open a private network namespace (`unshare -rn`). Without it they run
  credential-stripped only and the console says so — never assumed.
- Updating: `git pull && PHOENIX_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build`.
- Put a reverse proxy (Caddy or nginx) in front for TLS; the console serves plain HTTP.

### With Coolify (what RupertCloud runs)

Coolify builds the branch with Railpack from the `Procfile` (Python 3.13 works — the
pinned packages install and the suites pass) and fronts it with Traefik. Three settings
decide whether it comes up:

- **Persistent Storage** — one volume, destination path **`/data`** (never `/`: Docker
  refuses a volume mounted at the root and the rolling update fails with "destination
  can't be '/'"), and the environment variable `GOV_DATA_DIR=/data`.
- **Port** — `Ports Exposes` = `8788` and the variable `PORT=8788`; the console binds
  `0.0.0.0` when `PORT` is set. Traefik's plain-text "404 page not found" on the domain
  means no healthy container is behind the router, not that a page is missing.
- **Environment** — the table below; `BRAIN_BASE_URL=http://host.docker.internal:11434/v1`
  reaches Ollama on the same host only if the container can resolve that name (add
  `host.docker.internal:host-gateway` under extra hosts, or use the host's LAN IP).

Build pack "Dockerfile" uses the `Dockerfile` here instead of Railpack and adds
`unshare` for the sandbox's private network namespace; under Railpack the sandbox runs
credential-stripped only and the console says so.

### Without a container (systemd)

`deploy/phoenix.service` carries the unit and, in its header, the six commands that set
up a `phoenix` user, a venv, `/var/lib/phoenix` for the record and `/etc/phoenix.env` for
the variables. For the private network namespace set
`sysctl kernel.unprivileged_userns_clone=1`.

### Health

`GET /healthz` answers 200 with `{ok, commit, uptime_s, data_dir, brain, improve,
isolation}` when the data directory is writable, 503 otherwise. Compose and the
Dockerfile poll it.

## Railway (legacy)

- **`Procfile`** — `web: python gov/sim_console.py --seed` is the start command.
- **`$PORT` / `0.0.0.0`** — the console reads the injected `PORT` and binds `0.0.0.0`.
- Attach a Volume at `/data` and set `GOV_DATA_DIR=/data`, or every redeploy erases the
  record — measured, twice.

## Environment variables (all hosts)

| Variable | Purpose |
|---|---|
| `SEED=1` | Seed a demo game on boot (or keep `--seed` in the Procfile) |
| `GOV_DATA_DIR=/data` | Point the SQLite DB at a mounted **Volume** so game state survives redeploys |
| `BRAIN_API_KEY` | Turns on the brain for EVERY model call  against the platform's own gateway, `https://api.ripaplatform.com/v1`, model `qwen3:30b` |
| `BRAIN_BASE_URL` / `BRAIN_MODEL` | Optional: another OpenAI-compatible endpoint, or another model the gateway serves (`GET /v1/models` lists them). The gateway's `qwen3:30b` is the thinking-only build and reasons on every call whatever the request says; pull `qwen3:30b-instruct` (non-thinking) on the gateway and set `BRAIN_MODEL=qwen3:30b-instruct` |
| `BRAIN_MODEL_FALLBACKS` | Comma-separated models to use, in order, when the gateway answers `403 model_disabled` for the current one (the administrator switched it off). The record names the model that actually answered |
| Services (`svc.ripaplatform.com`) | `BRAIN_BASE_URL=https://svc.ripaplatform.com/v1` and `BRAIN_MODEL=<service-name>` use a managed endpoint: the gateway routes to the best available model with fallback and keeps it warm; the response names the model that served and the record logs that. A `503 model_warming` (Retry-After 30) is retried; a 503 without that code, a 403 `model_disabled` and a 429 service-limit are not — they need an administrator |
| `WORLD_DIFFICULTY` | `easy`, `medium` (default) or `hard`: the total food + wood + gold spent climbing all ten ages, Stone to Tech — 100 million, 1 billion, 10 billion. Each leap costs the previous one times the same factor, solved from the total. `WORLD_MATURITY` sets any total; `AGE_GROWTH` an exact factor |
| (research) | No variable: every development the cycle queues is preceded by a research entry (advantage, risk, blast radius, detection, undo, alternatives, confidence) written by the brain into an append-only hash-chained ledger, shown at `/research`. Without a working brain a development is held back as *unresearched* — no research, no proposal |
| `IMPROVE` / `IMPROVE_INTERVAL_S` / `IMPROVE_MAX_TRIES` | Article XI improvement cycle: on by default every hour; each cycle reads public sources about up to 10 topics for the settlement's age, checks every citation, proposes developments from verified facts, researches them and queues them for the Board and the human. `IMPROVE=0` disables |
| `BRAIN_NATIVE=1` | Call a tagged model on Ollama's native `/api/chat` with `think: false` instead of `/v1`. Only for a model that honours the switch — on a thinking-only build it moves the reasoning into the answer |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | The original provider; used only when `BRAIN_API_KEY` is unset. Remove it once the switch is made so a missing key fails loudly instead of falling back |
| `BRAIN_TIMEOUT_S` | Per-attempt timeout for model calls (default 300) |
| `BRAIN_THINK_TOKENS` / `BRAIN_THINK_TOK_PER_S` | A model observed to think (its reasoning outweighs its answers in the call log) gets this many extra tokens for reasoning on every call (default 2500), so the answer is not crowded out; the per-attempt timeout stretches to fit at the measured speed (default 8 tokens/s). A call inside a director turn whose budget cannot cover a thought is not made — the rules answer at once. `0` turns both off. A non-thinking model needs none of this |
| `BRAIN_STREAM` / `BRAIN_STREAM_GAP_S` / `BRAIN_EDGE_SAFE_S` | A gateway's edge (Cloudflare) answers 524 to a request silent for ~100 s, and a thinking model given room to reason takes minutes. So a model observed to think (`BRAIN_STREAM=auto`, the default; `1` always, `0` never) has its reply **streamed**: reasoning tokens arrive as they are made, the connection is never silent, and a call can run for minutes. `BRAIN_STREAM_GAP_S` (default 90) is the longest silence tolerated inside a stream. A host that rejects streaming (HTTP 400/404/405/415/422/501) is remembered and called plainly from then on; a plain call gets only the reasoning allowance that finishes within `BRAIN_EDGE_SAFE_S` (default 85 s) at `BRAIN_THINK_TOK_PER_S` |
| `DRIVER_MODEL_BUDGET_S` | The most model time one director turn may spend, retries included (default 180, always kept under the watchdog's 300 s). A slow model makes a turn fall back to its rules instead of wedging the world into a restart. Background work — the improvement cycle, research — is not bounded by it |
| `NET_RETRIES` / `NET_BACKOFF_S` | Every outbound request (model, public sources) is retried on timeouts, connection loss, 429 and 5xx — including Cloudflare's 52x — with exponential backoff and jitter: `NET_RETRIES` retries after the first attempt (default 5), first wait `NET_BACKOFF_S` seconds (default 1, doubling, capped at 30). 401/402/404 are never retried; a request not declared idempotent is made once |
| `NET_BREAK_AFTER` / `NET_COOL_S` | Circuit breaker per host: after `NET_BREAK_AFTER` calls (default 2) have exhausted their retries against one host, calls to it fail at once for `NET_COOL_S` seconds (default 60), then one probe is let through |
| `ADMIN_TOKEN` | The stronger key: it opens everything `CONSOLE_TOKEN` does, and it alone opens the **full reset** (export, then delete every database and the event log, then restart) and the **whole-world JSON export**. Unset, the full reset takes `CONSOLE_TOKEN` instead, and is disabled only when neither is set; the export is open. Paste it into the box at the top of `/admin` (or answer the prompt); it is kept in that browser only |
| `EFFICIENCY_TOKENS_PER_DAY` | Model tokens per day the efficiency review may spend (default 50000). The review runs after every improvement cycle and on demand at `/admin`; over budget it still writes the rule-based reading to the innovation journal. `IMPROVE_EFFICIENCY_REVIEW=0` stops the automatic one |
| `UTOPIA_TOKENS_PER_DAY` / `UTOPIA_PRICE_SHARE` | The architect's daily model-token budget for designing civic works (default 200000; over it the pattern book designs), and the share of the current age-up a 10-point work costs (default 0.04) |

## Admin

`/admin` shows every system (world, brain, network breakers, improvement cycle,
research chain, disk), compute over time (model tokens per hour and per
purpose, tokens wasted on failed calls, agent compute and the contribution it bought,
tokens per research entry, idea or adopted development), the agents ranked with flags
for low efficiency, chaos sources grouped from the incident log, questionable
decisions, and the innovation journal (ideas, research, proposals, cycles, lessons,
free choices, efficiency reviews, operator notes — downloadable as Markdown to write
papers from). Every log and the whole world as one JSON file download from there.

The danger zone has a world reset (the game world and economy; memory, research and
logs stay) and a full reset (needs `ADMIN_TOKEN` — or `CONSOLE_TOKEN` when no admin token is set — and the words `RESET EVERYTHING`):
the whole world is exported to `<data>/exports/world-<time>.json` first, the nightly
archives are moved beside it, then every database and the event log are deleted and
the process restarts from nothing.

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

Done well

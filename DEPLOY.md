# Deploying Phoenix Audit

The app is a single standard-library HTTP server (`gov/sim_console.py`). The front door
`/` is the **audit workbench** (scan a repo → rebuild the flaw → exploit it → prove it);
the settlement console lives at `/console`.

## Start command

The `Procfile` provides it:

```
web: python gov/sim_console.py --seed
```

If your platform needs an explicit **Start Command** (e.g. after removing a custom one),
paste exactly:

```
python gov/sim_console.py
```

`--seed` is optional and harmlessly ignored by this entrypoint. The server reads the
platform's injected `$PORT` and binds `0.0.0.0`; locally it defaults to `127.0.0.1:8788`.
Confirm a deploy is live by opening `/` — the footer shows the running commit — or hitting
`GET /api/version` → `{"sha": "...", "node_range": true|false}`.

## Build requirements

| Need | Why | If missing |
|---|---|---|
| **Python 3.11+** | the app + `requirements.txt` | build fails |
| **Node.js 20+** on the image | JS **and SQLi** reproductions run in real Node (`node:sqlite`, permission-model sandbox) | app still runs; JS/SQLi proofs report "node range unavailable"; Python proofs (rce/traversal/sqli/cmdi) still work. `node_range:false` in `/api/version` |
| ~~git~~ | not required — repos are fetched as HTTPS tarballs from `codeload.github.com` | — |

A Python-only buildpack won't include Node. To get JS/SQLi proofs on the deploy, add a
Node toolchain to the image (a Nixpacks/Docker/buildpack that provides both Python and
Node). Everything else works without it.

## Environment variables

| Variable | Purpose |
|---|---|
| `CONSOLE_TOKEN` | Gates the **settlement** power actions and operator-only local-path scans. The **audit** surface (`/api/scan`, `/api/prove`) stays open to guests and rate-limited. Set this on any public deploy. |
| `GOV_DATA_DIR` | Point the SQLite state at a mounted volume so it survives redeploys (e.g. `/data`). |
| `PORT` / `HOST` | Injected by the platform; `PORT` triggers the `0.0.0.0` bind. |
| `PHOENIX_BUILD` / `SOURCE_VERSION` / `RENDER_GIT_COMMIT` | Any one is shown as the build SHA in the workbench footer (else it reads `.git`). |
| `DEEPSEEK_API_KEY` / `BRAIN_*` | Optional model for the autonomous analyst/campaign (the audit pipeline itself is model-free). |

## Guest audit surface — security posture

There are no accounts; audit is open to guests by design. It is safe because:

- **Input is constrained**: a guest may only submit a public `https://github.com/<owner>/<repo>`
  URL (validated) or the sample — never an arbitrary server path (operator-only, behind
  `CONSOLE_TOKEN`).
- **Isolation**: each scan clones into its own job dir; each reproduction runs in its own
  sandbox (Node permission model / Python audit cage — no network, no subprocess, no
  out-of-dir writes). Jobs carry a TTL and are cleaned up.
- **Rate limited**: scans and proves are throttled per visitor.

## Scaling caveat (multiple instances)

Background scan jobs are held in memory. On a single instance that's fine. If you run
**multiple instances/workers**, a `scanId` started on one won't be found when the status
poll lands on another. To scale horizontally, persist scan-job state (SQLite/Redis) so any
worker can serve the poll — the code isolates this to the `_SCANS` store in
`sim_console.py`.

## State persistence

The container filesystem is ephemeral. Without a mounted volume, settlement state resets
on redeploy (the audit engine is stateless per request, so it doesn't matter there). For
durable settlement state, mount a volume and set `GOV_DATA_DIR`.

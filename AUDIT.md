# Phoenix Audit — scan real code, rebuild the flaw, host it, hack it, prove it

**Status:** shipped (offline pipeline), with one declared seam for production execution.

The security harness proved the discipline on a hand-written toy. This points it at
**real source**: take a repository, find the flaws, **rebuild each one as a runnable
system in a sandbox, exploit it, and let the working exploit be the proof.** A claim is
worth nothing; a reproduction is everything (SECURITY.md §3), now applied to code no one
wrote for us to break.

## The pipeline

```
  scanner.py        →   reproducer.py      →   range.py           →   redteam gate
  scan real source      rebuild the flaw       host + exploit it      dossier for a human
  (read-only)           as target/inv/poc      in an isolated cage    before anything real
       │                      │                      │
   candidates            reproduction             verdict (from the trace,
   (file:line, class)    (a runnable system)      never the exploit's claim)
```

1. **Scan — `gov/scanner.py`.** Walks a checkout and reports *candidates*: class, file,
   line, snippet, why. Read-only; it never runs the code it reads. It is tuned to
   **locate, not judge** — it narrows a hundred thousand lines to a handful of leads and
   leaves the verdict to a reproduction. (On the scanned `@cloudflare/computer`: 133k
   lines → ~90 candidates, and most of *those* are still false positives — test fixtures,
   parameterised SQL. That is the point: only a working exploit separates signal from noise.)

2. **Rebuild — `gov/reproducer.py`.** Turns a candidate into a *reproduction*: a minimal
   system that exhibits the **same class** of flaw (`target.py`), the invariant that
   judges it (`invariant.py`), and the exploit that triggers it (`poc.py`). It ships a
   **hardened variant** too, so the same exploit can be shown to stop working after the
   fix — the regression proof.

3. **Host & hack — `gov/range.py`.** A *Range* hosts a reproduction and runs the exploit
   against it, returning the invariant's trace-based verdict. Two backends, one interface:
   - `InProcessRange` — runs here, in an audit cage: **no network, no subprocess, no write
     outside the box** (Article V). Runs anywhere Python does; it is what the tests use.
   - `ComputerRange` — the production target: each reproduction gets its **own Cloudflare
     Computer workspace** (a VFS in a Durable Object with a real execution backend), where
     the system is *actually rebuilt and run* with real per-workspace isolation as the
     blast radius. A declared seam — the reproduction contract is identical, only the host
     changes — not yet wired, because it needs the Cloudflare runtime.

4. **The gate — `gov/redteam.py`.** A confirmed finding is packaged with its provenance,
   evidence, exploit and fix. Disclosure, or pointing anything at a real deployed system,
   is irreversible and stops at a human (SECURITY.md §4). No backend here can perform it.

## The two laws that make it safe and honest

- **Confirmed = vulnerable-build reproduces AND hardened-build refuses the same exploit.**
  That pairing kills the false positive that "reproduces" no matter what.
- **Rebuild, don't attack.** We host and hack a *reproduction we built*, never someone's
  running service. Recreating a pattern to prove it is exploitable is defensive research;
  aiming it at live infrastructure is the gated, human-only act — and the cage removes the
  capability to do so, so it cannot happen by accident or by a model's bad idea.

## The army that drives it — `gov/campaign.py`

The settlement's machinery for running autonomous agents, with the game removed: a
persistent loop that points a **squad** at a problem and works until the oracle is
satisfied or a real stop condition fires (`SOLVED` / `GATE_BLOCKED` / `DRY` / `BUDGET`).

- **Persistence** — it does not stop at the first failure; it works round after round.
- **Creativity that escalates, bounded** — a strategy ladder (`recon → probe → diversify
  → creative`): conservative while winning, climbing one rung each stalled round, dropping
  back the moment progress resumes. Escalation, not thrashing.
- **It never destroys value** — the score is ratcheted (the oracle reverts anything that
  breaks a test; the campaign refuses to let its high-water mark fall), a persistent
  thrasher is reaped so it can't spend the whole budget, and every decision is recorded
  with its lineage.

## Where it runs

`gov/verify_audit.py` (11 checks) and `gov/verify_campaign.py` (10 checks) prove the whole
pipeline and the driver **model-free**, wired into CI alongside the security and sentinel
suites. Point the scanner at any checkout:

```
python3 gov/scanner.py /path/to/repo --limit 40
```

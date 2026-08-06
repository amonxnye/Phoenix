# Next — `harness/security`

**Where it stands:** SECURITY.md defines the design — oracle = a proof-of-concept that
**reproduces** in a sandboxed replica; gate = any system outside the sandbox, plus
disclosure and production deploys. No code yet.

## Step 0 — the scope gate (do this first, before any agent exists)

Authorization must be *code*, not a paragraph. A run declares its scope (repos, hosts,
accounts) as part of the Vision; anything not in scope is **unreachable**, not merely
forbidden. Phoenix already enforces the network half — `workspace.sandbox_mode()` runs
the oracle in a private netns — so step 0 is mostly wiring the declaration into the
Vision and refusing to start without one.

**Acceptance:** the harness refuses a Vision with no declared scope, and the sandbox
reports `network-isolated` before any analyst agent runs.

## Step 1 — the repro oracle on a deliberately vulnerable toy app

`sandbox_security/` holds a small app with known planted flaws. A finding is only a
finding if a PoC **reproduces** against the replica. Everything else is noise, earns
nothing, and is logged as such — this alone kills the false-positive flood that makes
AI security tooling unusable.

**Acceptance:** a valid PoC scores; a plausible-but-unreproducible claim scores zero.

## Step 2 — the analyst agent + adversarial verification

Hunt one vulnerability class → PoC → independent agents try to **refute** it (Phoenix's
existing verify pattern) → majority refutation kills it. Contribution is paid only on
survivors.

## Step 3 — the fix loop (where the real value is)

Patch + **regression test that fails before and passes after**. Contribution moves from
"found something" to "closed something durably" — the same trick that made the ops and
builder designs honest.

## Step 4 — the dossier gate

Finding + scope + evidence + PoC + fix + lineage, parked for a human. **No disclosure,
no third-party contact, no production deploy** without that click.

## Needs from you

- The **authorized scope** for the first real run (your own repo or infrastructure —
  nothing third-party, ever, without written engagement).
- A decision on whether findings stay internal or feed a disclosure workflow later.

## Scope of this harness

Defensive and authorized only: your systems, your CTFs, your engagements. No mass
scanning, no third-party targets, and no capability the gate doesn't stop at a human.

## Definition of "v1 shipped"

Ten findings on the toy app where every one either reproduces or is discarded, each
survivor arrives with a regression test that proves its fix, and nothing left the
sandbox without a human.

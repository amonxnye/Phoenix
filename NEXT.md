# Next — `harness/quant`

**Where it stands:** QUANT.md defines the design — oracle = an out-of-sample backtest
on a holdout the agents cannot read; gate = placing a trade, with tacit consent
**disabled** for capital. No code yet.

## Step 1 — the holdout boundary (build this before anything else)

The holdout is this harness's equivalent of the unwritable tests directory. If agents
can read it, everything downstream is theatre.

- `quant_world.py` splits public data into `search/` (readable) and `holdout/` (only
  the oracle process opens it).
- The evaluation runs in the same netns sandbox, and the researcher agent never
  receives holdout rows — only a score.
- **Acceptance:** a scripted agent that tries to open holdout data fails hard, and the
  attempt is logged as a violation.

## Step 2 — pre-registration + the charged hypothesis budget

A hypothesis must state its rationale and acceptance criteria **before** testing (the
decision record is written first — Phoenix already records the *why* at decision time).
Every holdout evaluation is **charged** to the agent's budget, and the running
test-count travels with the result in the lineage: a strategy that survived 1 test is
not the same object as one that survived 400.

**Acceptance:** the scorecard for any strategy shows its multiple-comparisons count.

## Step 3 — adversarial verification

Independent agents attack each surviving result along distinct lenses: transaction
costs, capacity, regime dependence, look-ahead/survivorship leakage. Majority
refutation kills it. Contribution is paid only on survivors, penalized by tests
consumed.

## Step 4 — decay and re-validation

Strategies age (the settlement's decay mechanic, unchanged): a validated edge must be
re-confirmed on fresh data or it goes stale and stops steering (Article VI.4).

## Step 5 — the dossier gate

Strategy + evidence + test-count + limitations + capacity estimate, parked for a
human. **No broker credentials exist in the sandbox**; tacit consent never applies to
capital. Silence does not move money — that exemption is written into the design and
should never be relaxed.

## Needs from you

- A **public dataset** to start on (equities daily bars, crypto OHLCV — anything
  freely redistributable), so the harness is reproducible by others.
- Confirmation that this stays a **research** harness: it proposes, a human allocates.

## The honest line

Not financial advice, no market access, no live trading. The safety comes from a
structural boundary (no venue credentials, no network) rather than a promise.

## Definition of "v1 shipped"

Twenty pre-registered hypotheses, an honest report of how many survived out-of-sample
with the test-count attached to each, and at least one killed by the adversarial panel
that a naive backtest would have called a winner.

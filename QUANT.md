# Phoenix Quant — a governed organization that researches strategies

**Status:** design. The constitution pointed at **quantitative research**: hypothesis
→ backtest → out-of-sample validation → a proposal a human decides on.

> **The oracle is an out-of-sample backtest; the gate is placing a trade.**

Of every domain in the family, this one has the sharpest gate — money leaves the
account and does not come back — and the most notorious oracle-gaming problem
(overfitting). Phoenix's design attacks both directly.

---

## 1. The translation table

| Settlement | Quant research |
|---|---|
| The World (oracle) | Historical market data + a **held-out** period the agents never see during search |
| Vision | "A strategy with out-of-sample Sharpe > X, max drawdown < Y, capacity > Z" |
| Progress 0–100% | % of the mandate's acceptance criteria met on held-out data |
| Villager gathering | A researcher agent proposing and backtesting one hypothesis |
| Structures | Reusable assets: cleaned datasets, feature libraries, risk models, execution simulators |
| Compute cap | Literally the research budget — and a **hypothesis budget** (see §3) |
| The gate | **Any live trade, capital allocation, or broker connection** |
| Lineage | Which strategy came from which hypothesis, tested how many times, by whom |

## 2. The oracle problem: overfitting *is* reward hacking

An agent army that can run unlimited backtests will find beautiful nonsense. This is
the same failure Phoenix already solved for tests (an agent that could edit its own
tests would). Countermeasures, all constitutional:

- **The held-out period is not readable by researchers** — same principle as the
  workspace's unwritable test directory. It is the sandbox boundary.
- **A hypothesis budget**: every backtest against held-out data is *charged*, and the
  multiple-comparisons count travels with the result (a strategy that survived 1 test
  ≠ one that survived 400). The lineage engine already records exactly this.
- **Pre-registration**: a hypothesis must state its rationale and acceptance criteria
  *before* it is tested; the decision record is written first (Phoenix records the
  *why* at decision time by design).
- **Adversarial verification**: independent agents attack the result — regime
  dependence, look-ahead bias, survivorship, transaction costs, capacity.
- **Decay as a first-class mechanic** (already in the settlement): strategies age;
  a validated edge must be re-confirmed or it goes stale (Article VI.4).

## 3. The gate

Reversible (free): data work, feature engineering, in-sample search, simulation,
paper-trading against a simulator, writing the strategy dossier.

Irreversible (human, Article IV): connecting to a broker, allocating capital, placing
any order, changing live risk limits. Article V is literal — **no broker credentials
in the sandbox**; the researcher cannot trade even if it decides it should. Tacit
consent (IV.7) is *disabled* for capital allocation: silence never moves money.

## 4. Why the governance is the product

Research shops already have quants and backtesters. What they don't have: a fleet
whose every hypothesis is pre-registered, whose test-count is auditable, whose
strategies decay unless re-validated, whose budget reaps unproductive lines of
inquiry, and whose every allocation proposal arrives with the full provenance of how
it was found. That's the difference between an idea and a defensible one.

## 5. Build order

1. Simulator + strict held-out split on public data; the oracle scores a strategy.
2. Researcher agent v1: pre-register → in-sample search → one charged out-of-sample
   evaluation → contribution = validated edge, penalized by tests consumed.
3. Adversarial verify panel (costs, capacity, regimes, leakage).
4. The dossier gate: strategy + evidence + test-count + limitations, parked for a
   human. No capital moves without a click, ever.
5. Decay/re-validation loop; the eval race across models.

## 6. The honest line

This is a **research** harness: it discovers and proposes, a human allocates. It is
not a trading bot, has no market access, and its safety comes from a boundary that is
structural (no credentials, no network to venues) rather than behavioral. Nothing here
constitutes financial advice, and any live deployment is the operator's decision and
risk.

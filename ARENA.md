# The Arena — running Phoenix on any model, and measuring what happens

**Status:** steps 1-3 built (`models.py`, `/providers`, budget ceilings); steps 4-6
(ablation matrix, `/parliament`, probe pack) still to come. Proven by
`gov/verify_arena.py` — 58 acceptance checks, no API key required.

Phoenix is a governance layer with exactly one seam where a model plugs in
(`brain._chat`). That makes a question answerable that no benchmark currently asks:

> **Can your model run an organization?**

And a second, stranger one that nobody has data on at all:

> **Does a board of *different* models govern better than a board of one?**

This document is the plan for both: model routing per role, a provider performance
page, the mixed-model parliament, and the measurement discipline that makes any of it
believable.

---

## 1. Providers

**Direct, first-class (ranked):** OpenAI · Google Gemini · Anthropic · DeepSeek.
Three of the four speak the OpenAI-compatible protocol our seam already uses
(Gemini via its OpenAI-compat endpoint); Anthropic has its native branch already
built. Adding a fifth is a registry entry, not a code change.

**Extensible by design:** any OpenAI-compatible endpoint can be registered with a
base URL, key and model name — self-hosted, regional, or a lab we haven't heard of
yet. That is the whole point of having one seam.

### On OpenRouter (and routers generally)

Support it — but **not for ranked results**, and the reason is methodological, not
technical.

A router is one key and hundreds of models: wonderful for breadth. But it sits
between us and the lab, and that breaks three things an eval depends on:

1. **Model identity drifts.** A routed model may be served by different upstreams
   with different quantisation, context limits and sampling behaviour. Run A and
   run B may not be the same artifact — fatal for reproducibility.
2. **Cost and latency measure the router**, not the provider. Our "$ per vision
   point" and p95 columns would be describing infrastructure we're not evaluating.
3. **Silent failover is exactly what our constitution forbids.** Routers substitute
   providers on error by default; Phoenix's rule is that a failed seat **abstains and
   says so**. A substitution that isn't recorded corrupts every attribution
   downstream.

So the design gives runs a **tier**:

- **Ranked** — direct provider key, fallbacks off, model identity fixed. Eligible for
  the leaderboard.
- **Scouting** — via a router. Cheap breadth: screen many models, find the promising
  ones, then re-run the winners on direct keys. Clearly labelled, never mixed into
  ranked tables.

A routed run *can* be promoted to ranked if it satisfies three conditions: fallbacks
disabled, the serving upstream pinned **and recorded per call**, and its latency/cost
columns marked router-inclusive. Where OpenRouter reports the serving provider, we
record it; where it can't be pinned, the run stays scouting. Honest labels beat
purity theatre — but they have to be labels, not footnotes.

## 2. Model routing per role

Roles: **governor · each board seat (Prudence/Growth/Ledger) · fleet agents ·
worker · retrospective**. Each resolves to a registered provider; unset roles inherit
a default. Every model call already carries a `purpose`, so routing is a lookup, not
a refactor.

Three constitutional rules ship with it, because they are not implementation details:

- **Assigning models is a human-only power.** Who *thinks* for the organization is
  nearer to adopting a Vision than to any operational choice. The board may *propose*
  a swap ("Prudence abstained on 40% of votes"); only the human assigns. Tacit consent
  (IV.7) never applies to a model change.
- **Every decision records its model.** Article VII is incomplete if we know what was
  decided and why, but not by which mind. This also makes per-model scoring a query
  instead of a bolt-on.
- **An outage is an abstention, never a substitution.** A seat whose provider fails
  votes `unknown` (already supported) and it is logged. Silently borrowing another
  model would change who governs, in secret.

## 3. The three modes (and what each may claim)

| Mode | Setup | Legitimate claim | Status |
|---|---|---|---|
| **Solo** | one model in every role | "model X runs the best organization" | built (`evalrun.py`) |
| **Ablation matrix** | everything fixed; swap **one seat at a time**; repeat across seeds | "this seat, this model, this much difference" | to build — the rigorous core |
| **Parliament** | mixed board + a different governor | interaction effects, disagreement, diversity | routing built (`models.py`); the page is step 5 |

**Seats are not equal.** Growth's chair may be structurally more influential than
Prudence's, so a model can look good merely by sitting in it. Every matrix run is
therefore **counterbalanced**: each model rotates through each seat across
repetitions, and results are averaged. Otherwise we measure the chair, not the
occupant.

Parliament without the matrix is an anecdote. The matrix without parliament is
correct but boring. Ship both; keep their claims separate.

## 4. What the provider page measures

Not tokens per second. The interesting columns are governance ones:

- **Economic** — vision points per million tokens · turns to milestone · net worth ·
  waste (spoilage, failed builds)
- **Governance** — constitution-probe refusal rate · stall turns · did it escalate
  blocked actions correctly · budget discipline (overshoots, cap behaviour)
- **Reasoning** — share of decisions citing real inputs · **decision hit rate**: the
  share of decisions that closed with a *positive measured outcome* (the lineage
  engine already stores this; it is the metric I would headline)
- **Learning** — the with-memory vs memory-wiped delta on a repeat run
- **Reliability & cost** — p50/p95 latency · error and abstention rate · **$ per run
  and $ per vision point**
- **Parliament only** — pairwise disagreement rate between seats, and how often a
  mixed board blocked something a monoculture board waved through

That last row is a real research question with no published answer: **is a diverse
board better, or just slower and more expensive?** The platform can measure it.

## 5. Budget ceilings (approved)

Real money, unattended, across several providers — so the cap is structural, like
everything else here:

- **Price table** in the anchor (model → $/1M in, $/1M out), human-maintained, since
  prices change; cost is computed and stored **per call** at log time.
- **Pre-flight estimate** before a run starts: expected calls × observed average
  tokens × price. Shown, and refused if it exceeds the ceiling.
- **`EVAL_BUDGET_USD` per run** and an optional **per-provider sub-cap**. On breach:
  the run **halts**, the scorecard is stored and marked `incomplete: budget`. It does
  not quietly finish on a cheaper model — that would be a substitution.
- The in-world compute cap stays exactly as it is. Simulated compute governs the
  *settlement*; dollars govern the *experiment*. Conflating them would corrupt both.

## 6. Fairness rules (extends EVAL.md §5)

Same seed · same turn budget · same auto-human policy · same temperature per call
site · rule-based fallback **disabled** during evals (a model that errors scores the
error) · at least three runs per configuration, medians reported · memory wiped
between models, retained between a model's own runs (that delta *is* the learning
score) · counterbalanced seats · every run records: models by role, tier
(ranked/scouting), seed, turn budget, cost, and any incompleteness.

## 7. Deployment

The arena runs as a **separate Railway service** with its own volume. The live
settlement is the launch demo and stays untouched — an experiment that swaps its
governor mid-flight would corrupt both the experiment and the demo.

Env shape (sketch): a JSON registry of providers, a role→provider map, the run tier,
the budget ceiling. Nothing about the arena changes how the settlement runs.

## 8. Build order (each step ships alone)

1. ✅ **Provider registry + per-role routing**, model recorded on every decision and
   call — `gov/models.py`, the seam in `brain._chat`, `decisions.model`,
   `model_calls.role`/`.cost_usd`. With every role unset, nothing changes.
2. ✅ **`/providers` page** — computed from `model_calls` + `decisions` plus the price
   table, cost stored per call at log time. Roles are assigned from this page, behind
   the operator token.
3. ✅ **Budget ceilings** — pre-flight estimate, `EVAL_BUDGET_USD`, per-provider
   sub-caps, halt-and-mark-`incomplete: budget` on breach.
4. **Ablation matrix runner** — `evalrun --matrix`, counterbalanced, N seeds,
   medians, scorecards stored with full configuration.
5. **`/parliament` page** — live mixed world: seats with model badges, votes coloured
   by model, each seat's reasoning in its own voice, the running disagreement matrix.
6. **Constitution probe pack**, scored per model; refusal rate becomes a column.
7. **The report** — the first cross-model comparison of governance. That is the
   artifact worth publishing.

## 9. How to run it today

```bash
# one lab per seat — a mixed board, on direct keys
export OPENAI_API_KEY=... GEMINI_API_KEY=... ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=...
export MODEL_ROLE_GOVERNOR=anthropic MODEL_ROLE_PRUDENCE=openai \
       MODEL_ROLE_GROWTH=gemini MODEL_ROLE_LEDGER=deepseek
export EVAL_BUDGET_USD=5.00
python3 gov/evalrun.py --turns 120 --fresh --label mixed-board-1
```

Any other lab is a registry entry, not a code change:

```bash
export MODEL_REGISTRY='{"myLab":{"base_url":"https://api.mylab.ai/v1",
  "key_env":"MYLAB_KEY","model":"m-1","kind":"openai"}}'
export MODEL_ROLE_GOVERNOR=myLab:m-1-pro
```

Roles can also be assigned live from `/providers` (operator token required — X.1), and
the price table overridden with `MODEL_PRICES` or the same page.

## 10. Open items

- Keys for OpenAI, Gemini and Anthropic (DeepSeek is live).
- The dollar ceiling per run (`EVAL_BUDGET_USD`), and whether per-provider sub-caps are
  wanted (`EVAL_PROVIDER_CAP_USD` is built and unset).
- The price table's initial values — seeded in `models.SEED_PRICES`; **please confirm**,
  since an unconfirmed price makes every $-per-vision-point column wrong in the same
  direction. An unpriced model is reported as unpriced, never guessed.
- Whether scouting-tier (routed) runs appear on the public leaderboard at all, or
  only in a separate table. My recommendation: separate table, clearly marked. Today
  the tier is a column on both `/leaderboard` and `/providers`.

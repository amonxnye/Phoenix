# Next — `platform/eval` (the Arena)

**Where it stands:** ARENA.md holds the approved plan. **Steps 1-3 are built**: the
provider registry with per-role routing, the `/providers` page, and the dollar ceilings.
Article X carries their three constitutional rules, and `gov/verify_arena.py` proves all
of it — 58 acceptance checks, no API key required.

Deployment decision (made): the arena runs on its **own Railway service and volume**.
The live settlement is untouched.

## ✅ Step 1 — provider registry + per-role routing

- `models.py`: named providers (`openai`, `gemini`, `anthropic`, `deepseek`, plus
  `openrouter` as scouting-only and any OpenAI-compatible entry via `MODEL_REGISTRY`)
  and a role → provider map (governor · Prudence · Growth · Ledger · fleet · worker ·
  retrospective · default).
- `brain._chat` resolves the provider **by role**; unset roles inherit the default brain.
- **Model identity is on every call and every decision** — `model_calls.role`,
  `model_calls.cost_usd`, `decisions.model`. Per-model scoring is now a query.
- Assignment is human-only (`/providers` behind the operator token, or `MODEL_ROLE_*`);
  a failed provider **abstains** — `board.vote` returns `unknown` for that seat, logs
  the dead model by name, and never substitutes.
- **Acceptance (met):** all roles unset → identical behaviour; a routed role carries its
  own model and its decisions name it; a forced failure produces an `unknown` vote, a
  log line, and no substitution.

## ✅ Step 2 — `/providers` page

Per model: calls, tokens, p50/p95 latency, error rate, dollars, decisions taken, share
closed, **decision hit rate**, and grounding. Per run: tier, cost, **$ per vision
point**, and any incompleteness. Plus the registry (never the keys), the live seat
health, and the assignment controls. Ranked and scouting are labelled, never merged.

Outcome classification is explicit and conservative: an outcome text that cannot be
read as a hit or a miss counts as neither. Guessing would inflate every model equally
and prove nothing.

## ✅ Step 3 — budget ceilings

Pre-flight estimate (expected calls × observed average tokens × price) printed before a
run and **refused** over `EVAL_BUDGET_USD`; the seam refuses further calls on breach and
`evalrun` **halts** and stores the scorecard marked `incomplete: budget`. Optional
per-provider sub-caps via `EVAL_PROVIDER_CAP_USD`. The in-world compute cap is untouched.

## Step 4 — the ablation matrix

`evalrun --matrix`: hold everything constant, swap one seat at a time, **counterbalance**
seats across repetitions, N seeds, medians. This is what makes the leaderboard
defensible rather than anecdotal. The scorecard already records `models_by_role`, `tier`
and `cost_usd`, so the matrix runner is a loop over configurations plus a median —
not new plumbing.

## Step 5 — `/parliament`

The mixed world, live: seats with model badges, votes coloured by model, each seat's
reasoning in its own voice, the governor's model up top, and a running disagreement
matrix. The routing it needs exists; what is missing is the page and the pairwise
disagreement counter.

## Step 6 — probes, then the report

Constitution probes scored per model (refusal rate becomes a leaderboard column), then
the first cross-model comparison of *governance* — the artifact worth publishing.

## Needs from you

1. **Keys**: OpenAI, Gemini, Anthropic (DeepSeek is live). Set on the arena service
   only — the settlement service keeps its single key. Nothing else is needed to route:
   `MODEL_ROLE_GOVERNOR=anthropic` and friends are enough.
2. **The dollar ceiling** per run (`EVAL_BUDGET_USD`), and whether you want the
   per-provider sub-caps switched on (`EVAL_PROVIDER_CAP_USD`, built and unset).
3. **Price table confirmation** — seeded in `models.SEED_PRICES`. Every $-per-vision-point
   number depends on it.
4. Whether scouting-tier (routed) runs appear on the public leaderboard at all — my
   recommendation is a separate, clearly-marked table. Today it is a labelled column.

## Definition of "v1 shipped"

Three models ranked on identical seeds with medians and dollar costs, one ablation
matrix showing what a single seat is worth, and a parliament run where the
disagreement matrix is non-trivial — i.e. the models actually govern differently.

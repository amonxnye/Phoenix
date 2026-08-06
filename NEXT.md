# Next — `platform/eval` (the Arena)

**Where it stands:** ARENA.md holds the approved plan — four direct providers plus an
extensible registry, routers allowed only as a *scouting* tier, per-role model
routing, budget ceilings, and the three measurement modes. No code yet.

Deployment decision (made): the arena runs on its **own Railway service and volume**.
The live settlement is untouched.

## Step 1 — provider registry + per-role routing

- `models.py`: a registry of named providers (`openai`, `gemini`, `anthropic`,
  `deepseek`, plus any OpenAI-compatible entry) and a role → provider map
  (governor · Prudence · Growth · Ledger · fleet · worker · retrospective).
- `brain._chat` resolves the provider **by role**; unset roles inherit the default.
- **Model identity is recorded on every decision and every call** — per-model scoring
  becomes a query, not a bolt-on.
- Assignment is a human-only power (console + env); a failed provider **abstains**,
  never substitutes.
- **Acceptance:** with all roles unset, behaviour is byte-identical to today; with a
  role assigned, that role's calls carry the new model and the decision record names
  it; a provider forced to fail produces an `unknown` vote, a log line, and no
  substitution.

## Step 2 — `/providers` page

Computed from data already logged (`model_calls` + `decisions`) plus a human-maintained
price table, with cost stored per call at log time. Columns per ARENA.md §4 —
economic, governance, reasoning (decision hit rate), reliability, and $ per vision
point. Ranked and scouting tiers shown separately.

## Step 3 — budget ceilings

Pre-flight estimate → refuse if over `EVAL_BUDGET_USD`; halt mid-run on breach and
store the scorecard marked `incomplete: budget`. Optional per-provider sub-caps. The
in-world compute cap is untouched — dollars govern the experiment, simulated compute
governs the settlement.

## Step 4 — the ablation matrix

`evalrun --matrix`: hold everything constant, swap one seat at a time, **counterbalance**
seats across repetitions, N seeds, medians. This is what makes the leaderboard
defensible rather than anecdotal.

## Step 5 — `/parliament`

The mixed world, live: seats with model badges, votes coloured by model, each seat's
reasoning in its own voice, the governor's model up top, and a running disagreement
matrix. The screenshot people share.

## Step 6 — probes, then the report

Constitution probes scored per model (refusal rate becomes a leaderboard column), then
the first cross-model comparison of *governance* — the artifact worth publishing.

## Needs from you

1. **Keys**: OpenAI, Gemini, Anthropic (DeepSeek is live). Set on the arena service
   only — the settlement service keeps its single key.
2. **The dollar ceiling** per run, and whether you want per-provider sub-caps.
3. **Price table** confirmation once I seed it.
4. Whether scouting-tier (routed) runs appear on the public leaderboard at all — my
   recommendation is a separate, clearly-marked table.

## Definition of "v1 shipped"

Three models ranked on identical seeds with medians and dollar costs, one ablation
matrix showing what a single seat is worth, and a parliament run where the
disagreement matrix is non-trivial — i.e. the models actually govern differently.

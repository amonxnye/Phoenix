# Next — the trunk

**Where it stands:** the settlement runs live under the full constitution (nine
articles, all enforced), the real-work loop ships oracle-verified code, the eval
harness and leaderboard exist, and the launch kit (README, thread, article, MIT
license) is written. Six harness branches carry designs for other domains.

## Immediately actionable

| # | Item | Owner | Notes |
|---|---|---|---|
| 1 | Merge the sandbox-hardening branch | you | Article V is now *enforced* (netns) and honestly reported; also fixes a career-wiping bug. Live code — worth merging soon. |
| 2 | Point Railway at `main` and delete the stale work branch | you | The two are byte-identical today; they'll drift the moment either moves. |
| 3 | `CONSOLE_TOKEN` decision | you | Dormant by design. Chaos is a strategy with a shelf life — the day someone finds `/api/reset`, you'll want it set. |
| 4 | Post-Imperial vision | you | Generation 1 completed the whole ambition chain. The world holds in stewardship until a human names what comes after empire. |

## Queued build work (mine, on request)

1. **Merge gate** (task #11) — heralds carrying real git diffs; a human-approved merge
   to main. Completes the real-work story and is the natural demo.
2. **Eval race** (task #10) — needs one more provider's `BRAIN_*` keys from you; the
   runner, scorecard and leaderboard are already built.
3. **Constitution probe pack** — scripted attempts to talk the fleet into breaking its
   rules, scored as refusal rate; adapt the published attack taxonomy for credibility.
4. **Disputed-knowledge flag** (task #9, LINEAGE.md §4.4) — contradictory facts
   quarantined at write time rather than coexisting.
5. **Channel notifications** (task #12) — `GATE_WEBHOOK_URL`, outbound-only, so gate
   requests and stall alerts reach your pocket. ~30 lines, no third-party relay.
6. **Raids & defence** (task #5, WORLD-DYNAMICS.md) — the settlement's first
   adversarial pressure, now that upkeep makes costs visible.

## Standing decisions

- **CI** is blocked at runner provisioning (account billing) — every suite passes
  locally; the badge stays red until that's lifted.
- **Executors**: adopt strong execution infrastructure behind our interface, never
  anyone's control plane (`harness/builder` → EXECUTORS.md).
- **Network is a capability grant**, not a default — off unless tier + board quorum
  say otherwise, allowlisted and recorded.

## Definition of "v1 shipped"

The live world governed for a week without a wedge, one real merge-gated code change
in `main` authored by an agent, and two models on the leaderboard.

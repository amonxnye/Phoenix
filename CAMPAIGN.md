# Campaigns — a fleet that works one problem until it gives

**Status: shipped** (`gov/campaign.py`, `gov/verify_campaign.py`, 37/37).

```
python3 gov/campaign.py run --repo /path/to/repo --agents 4 --rounds 6
python3 gov/builder.py gate            # the whole campaign, as one reviewable diff
python3 gov/builder.py approve --id 1
```

`builder.py` sends one agent at one task and gives up after a few tries. That is a
worker. This is the organization: a fleet that keeps pushing on a problem that does
not fall over on the first contact, gets more inventive as it gets stuck, and cannot
lose ground while doing it.

---

## The problem with "just retry"

Two failure modes kill autonomous fleets, and they pull in opposite directions.

**Give up too early** and the system only ever fixes the easy things — every hard
problem reads as impossible because nobody pushed twice.

**Retry forever** and the system burns budget thrashing, and — worse — a later
attempt undoes an earlier one's progress. Value goes backwards while the meter runs.
That is the failure mode that makes people switch autonomy off, and it is not solved
by being cleverer at the retry. It is solved structurally.

---

## The four mechanisms

### 1. The champion — ground is never lost

The campaign holds the best *oracle-proven* state so far as a commit.

- Every round's agents branch from the **champion**, not from the start.
- A new champion is crowned only when the oracle counts **strictly more tests
  passing, with nothing broken**.
- A sortie that does not beat the champion leaves nothing behind but a note. Its
  branch is deleted.

So the campaign's score is **monotonic**. It can stall; it cannot regress. And that
single property is what makes the rest safe: the downside of any experiment, however
strange, is bounded at exactly zero. A fleet that cannot lose can afford to gamble.

> Proven: a campaign where *every agent on every round* wrecks the repository leaves
> the working file byte-for-byte unchanged, the base commit unmoved, no branches
> behind, and nothing at the gate — and says why it failed.

### 2. Different agents genuinely try different things

Running one prompt four times gives four rewordings of one idea. Each round deals
every agent a **distinct strategy** from a ladder:

| strategy | the brief |
|---|---|
| `direct` | smallest change that satisfies the assertion; refactor nothing |
| `read-the-test` | state the contract the test implies, then honour it |
| `widen` | the defect is probably not in the obvious file — fix the real cause |
| `rewrite` | stop patching around the existing shape; rebuild from the contract |
| `contrarian` | assume every previous diagnosis was wrong; name a different one |
| `decompose` | make the smallest sub-behaviour provably right, even if partial |

Every agent is also told **what has already been tried and rejected**, and **what the
champion has already achieved** ("you are building on this, not starting over").

### 3. It gets stranger as it gets stuck — not sooner

Round 1 deals the conservative end of the ladder at low temperature. Each round
slides the slate further toward the strange end and raises the temperature.
Creativity is a response to evidence, not a setting — cheap ideas first, because most
problems are cheap, and wild ideas later, because by then you have *earned* the right
to try them and the champion means they cost nothing.

### 4. It stops, and says why

- **Solved** — the goal tests are green.
- **Dry** — N consecutive rounds moved nothing. Stop.
- **Budget** — the campaign has its own ceiling (Article III), separate from any one
  agent's (Article II).
- **Reaped and replaced** — an agent that spends its budget is retired for good and a
  successor is enlisted. The organization outlives its agents; ids are never reused,
  because the careers behind them are permanent.

A campaign that achieved nothing **names its binding constraint** instead of exiting
quietly (Article IX). A campaign that achieved something writes a **lesson** into the
anchor, and the next campaign is handed it — creativity compounding across runs, not
resetting each time.

---

## What it looks like

```
CAMPAIGN make 5 failing test(s) pass
  repo army · base 1/6 · fleet dev-01..dev-04 · budget 240,000
round 1  [direct, read-the-test, widen, rewrite]  from champion 1/6
   ** dev-01   direct         kept — 1 newly green, suite 2/6
   ** dev-02   read-the-test  kept — 1 newly green, suite 2/6
      dev-03   widen          no files
   ** dev-04   rewrite        kept — 2 newly green, suite 3/6
   -> CHAMPION 3/6 (dev-04, rewrite)
round 3  [widen, rewrite, contrarian, decompose]  from champion 4/6
      ... no improvement (1/2 dry rounds)
round 6  [decompose, direct, read-the-test, widen]  from champion 5/6
   ** dev-04   widen          kept — 1 newly green, suite 6/6
   -> CHAMPION 6/6 (dev-04, widen)

SOLVED — 1/6 -> 6/6 in 6 round(s)
branch phoenix/campaign/... waiting at the gate as #1
```

Three agents lost every round after the first. It cost nothing, and the campaign
still finished — because the one that won each round was building on every previous
winner. Note round 3 and round 5: dry rounds did not end it, and the score did not
fall.

---

## What is still ours, always

The campaign changes *how hard the fleet pushes*. It changes nothing about what the
fleet is allowed to do:

- **The oracle scores it.** No agent's claim counts, ever.
- **Isolation.** Every sortie is a worktree; your checkout is never written to, even
  with four agents running at once.
- **The protected set.** Tests, runner config, packaging, CI, `.git` — unwritable.
- **The gate.** The campaign's whole accumulated diff parks as **one dossier** for a
  human, with its risk class. Nothing merges autonomously, however many agents and
  rounds it took. Agents have no push rights.
- **The record.** Every round, every champion, every reaped agent is in the careers
  and the lineage.

## Not yet

- A **board pre-vote** on whether a campaign is worth staffing at all.
- **Cross-campaign strategy learning** — which strategy tends to win on which kind of
  failure. The data is being recorded; nothing reads it yet.
- **Sub-goals**: a campaign currently targets a set of failing tests. Splitting a big
  goal into a tree of campaigns is the natural next step.
- A **console page** — campaigns are CLI-only today.

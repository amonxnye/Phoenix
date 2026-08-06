# The Campaign Engine — an organization that works a problem until it has an answer

The settlement was scaffolding. What it was really building was this: a fleet that can
be pointed at a problem and **kept on it** — one that gets more inventive when it is
stuck instead of louder, and cannot wreck what it has already established while doing
so.

A campaign has three legal endings and no fourth:

| ending | means |
|---|---|
| **settled** | an oracle says the problem is corroborated, diverged, or refuted |
| **escalated** | it can name the *specific* thing blocking it, and hands that to a human |
| **exhausted** | the budget ran out — said plainly, with what was and wasn't done |

It may never stop quietly, and it may never declare victory on its own say-so
(Articles I and IX). "Undetermined" is a legitimate answer; **"undetermined" without a
named blocker is not.**

---

## 1. Creativity is a ladder, not a mood

You cannot make a system inventive by telling it to be. The engine climbs:

```
0  recompute            redo the arithmetic already in hand         free, certain
1  search-store         look through evidence already retrieved     free, certain
2  widen-query          retrieve literature phrased a new way       costs, reversible
3  declare-equivalence  treat two names as the same thing           judgement, capped
4  weaken-claim         test the weaker claim evidence could settle reframing, capped
5  escalate             name the blocker, hand it over              the end of the road
```

**A barren round climbs. A productive round drops back to the cheapest live rung**,
because the free checks now have new material to chew on. A rung whose work is finished
forever — every statistic recomputed, every equivalence declared — is skipped rather
than burned as a round.

So the system is ordinary while ordinary works, and reaches for judgement calls only
after the free and certain moves are spent. Every reach is a **named, logged move** a
reviewer can audit, not a longer prompt and a hope.

## 2. Creativity that cannot destroy value

Rungs 3 and 4 are where a system starts fooling itself — where "be creative" turns into
"redefine the problem until you've solved it". They are fenced:

- **Findings are append-only.** A later round may contradict an earlier one; both stay
  on the record. Nothing is deleted to make the story tidy.
- **Equivalences come only from a human's candidate list.** The engine may *use* the
  judgement that "very low calorie diet" is "calorie restriction"; it may not *invent*
  it. Every finding records which equivalence made the match, so a reviewer can reject
  the finding by rejecting the equivalence.
- **Weakening never replaces.** The weaker claim is a *new* claim under test. The strong
  one stays open and unsettled — you do not get to answer an easier question and call it
  the same one.
- **The goalposts are frozen.** Once a claim has findings, it cannot be edited away.
- **The gate is unconditional.** However the campaign ends, every irreversible act —
  publishing, contacting authors, ordering anything — stops at a human.

## 3. The first campaign type: replication

Given a paper, can its conclusion be reached again *without taking its word for it*, and
do its own numbers survive being redone? Three oracles, none of them an opinion:

1. **Fidelity** — a claim under test is quote-checked against the target first. You
   cannot replicate, or refute, a claim the authors never made. No strawmen.
2. **Arithmetic** (`statcheck.py`) — percentages, odds ratios, confidence intervals and
   p-values recomputed from the paper's own counts; GRIM and SD bounds catch means no
   sample size can produce. Dependency-free: the distributions are implemented from
   their series, so the oracle installs anywhere.
3. **Independence** — the same claim sought in *other* papers, the target excluded, each
   hit graded by where it sits. A paper's **result** or **conclusion** is evidence. Its
   **title** is a topic. Its **introduction** is usually a restatement of someone else —
   quite possibly of the very paper under test. And a **hedge** is neither: *"a future
   study looking at the feasibility of a low-energy diet to induce remission"* has the
   grammar of a finding and none of the content.

Verdict: **corroborated · diverged · refuted · undetermined**, and the end of the road
is a critique dossier parked for a human — saying in public that a paper is wrong is
irreversible and lands on real people's names.

## 4. What it did on a real paper

Pointed at the DIREM randomised trial (`PMID:41964971`, *diet and diabetes remission*),
offline except for six retrievals:

```
r1  recompute   9/40 = 22.500%, 12/40 = 30.000%, 1/40 = 2.500%          all consistent
r1  recompute   OR CCR vs control: 2x2 [9,31;1,39] → 11.32 (95% CI 1.36–94.3,
                Wald p 0.0248) against a reported 11.7, p 0.024         consistent
r1  recompute   CI shape, IFCCR vs CCR: [0.6, 4.3] is centred on 1.606
                against a reported 1.5                                  DIVERGENT
r2  search-store   supported [methods]    PMID:40701606
r4  widen-query    (nothing new) → climbing
r5  declare-equivalence  'very low calorie diet' counted as 'calorie restriction'
r9  search-store   supported [background] PMID:40994182
r12 weaken-claim   'calorie restriction induces diabetes remission' now also under test
r15 widen-query    (nothing new) → climbing → escalate

VERDICT: UNDETERMINED  (escalated after 16 rounds)
  supported only in a title or introduction (2 sources) — find a paper whose own
  results say it
```

The paper's arithmetic **recomputes cleanly** — every percentage exact, every odds ratio
and p-value within method choice. One real wrinkle: the reported OR of 1.5 sits against
a confidence interval centred on 1.61, so the point estimate and the interval did not
come from the same model.

And then the interesting part: across 55 stored papers, the *only* result-grade,
unhedged statement of the claim was **the target paper itself** — which is excluded from
its own replication. So the campaign refused to declare replication, and said exactly
what it needed instead. That refusal is the feature. A system that would rather be
useful than right would have counted a title.

## 5. The console

```bash
python3 gov/lab_console.py     # → http://127.0.0.1:8788
```

`phoenix-command.html` is the page it serves, and it is built around the one thing
that actually needs a person: **Awaiting your decision** sits at the top, because a
parked critique or dossier is the only place the system stops. Below it, every paper
under replication with its verdict; open one and you get the paper's claims *in its own
words* with the sentence each was quoted from, its arithmetic reported against
recomputed, the independent evidence graded and labelled with the claim it answers and
the equivalence that matched it, and what is still open.

Two things you can do besides deciding: **run a campaign** and watch the ladder climb
round by round, and **check one claim against one paper** — paste the sentence, get the
verdict. That last one is the oracle with the lid off, and it is the fastest way to see
what this system will and will not accept as evidence.

Approving in the console *is* the human approval Article IV waits for. With
`CONSOLE_TOKEN` set, reading stays free and every action needs the token.

## 6. Running one headless

```bash
python3 gov/campaign.py --spec sandbox/campaigns/PMID-41964971.json --fresh
python3 gov/campaign.py --spec ... --offline      # never retrieve; use what is held
python3 gov/verify_replication.py                 # 37 checks, no network, no model
```

A spec is the human's half of the work: which claims the paper makes (each with the
sentence it comes from), which reported statistics are recomputable from its own counts,
which searches may be tried, and which equivalences the campaign is *allowed* to declare.
The machine checks. It does not invent claims, and it does not invent equivalences.

## 7. Pointing it at something else

The engine is domain-agnostic; a campaign type needs three things:

- a **done condition an oracle can check** (not a vibe),
- a **ladder** of moves ordered cheap-and-certain → expensive-and-judgemental,
- a **gate**: the irreversible act that stops at a human.

Replication is the first. The same shape fits a bug that must be reproduced before it is
believed fixed, a migration that must be verified before it is switched over, an outage
whose root cause must survive an adversarial check. Where the oracle can be gamed, don't
point it there — that rule from [`HARNESSES.md`](HARNESSES.md) has not changed.

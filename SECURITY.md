# Phoenix Security — a governed organization that hardens systems

**Status:** shipped (build order §6.1–§6.4). The constitution pointed at **defensive
security work on systems you own or are authorized to test**: finding vulnerabilities
in your own code, writing the regression tests that prove they're fixed, hardening
configurations, and running CTF-style exercises.

**What runs today**

| Piece | File | The safety property, made structural |
|---|---|---|
| Sandboxed replica | `sandbox/replica/vault.py` | a deliberately vulnerable toy service (sqli, traversal, authz, rce) — no socket, no credential |
| The oracle | `sandbox/replica/invariants.py` | judges a *recorded request trace*, not the PoC's claim; agents cannot write it |
| Behaviour contract | `sandbox/replica/tests/` | a fix that deletes the feature fails here — hardening must keep the service working |
| The PoC cage | `gov/pocrunner.py` | an audit hook removes network, subprocess and out-of-box writes before the PoC imports — Article V, not policy |
| The world + gate | `gov/redteam.py` | reproduce-twice-honestly oracle; `apply_patch` refuses the oracle/tests/corpus; the dossier gate parks the irreversible |
| The analyst agent | `gov/analyst.py` | find→prove→fix through the one brain seam; paid by the oracle, recorded in careers/lineage |
| Console | `/security` in `gov/sim_console.py` | findings, the checklist, and the human's gate decisions, live |
| Acceptance | `gov/verify_security.py` | 22 model-free checks proving all of the above (wired into CI) |

> **The oracle is a reproducing proof-of-concept in a sandbox; the gate is any system
> you do not own.**

---

## 1. Authorization is the first-class constraint

Most security tooling treats scope as documentation. Here it is **code**: a run
declares its authorized scope (repos, hosts, accounts) as part of the Vision, and the
sandbox has **no credentials and no network route to anything outside it** (Article V,
literally). An agent cannot reach an out-of-scope system even if a model hallucinates
that it should — the capability was never granted. Out-of-scope targets aren't
policed, they're unreachable.

Everything below assumes: **your own systems, or a written engagement.** The harness
refuses a Vision without a declared scope, and every finding carries the authorization
under which it was produced (the lineage engine, doing exactly its job).

## 2. The translation table

| Settlement | Security |
|---|---|
| The World (oracle) | A **sandboxed replica** of the in-scope system + its test suite |
| Vision | "No high-severity findings in service X; all known classes covered by regression tests" |
| Progress 0–100% | % of the hardening checklist met / findings closed with a passing regression test |
| Villager gathering | An analyst agent hunting one vulnerability class in scope |
| Structures | Reusable assets: detection rules, regression tests, hardened configs, threat models |
| The gate | **Anything outside the sandbox**: testing live systems, disclosure, deploying a fix to prod |
| Careers/lineage | Which agent found what, under whose authorization, from which evidence |

## 3. The oracle — a finding is real only if it reproduces

A claim of a vulnerability is worth nothing; Phoenix's whole discipline is that the
oracle judges, not the agent. Tiers:

1. **Reproduces in the sandbox** — a proof-of-concept that demonstrably triggers the
   flaw against the replica. No repro → not a finding, logged as noise (this alone
   kills most LLM security false positives).
2. **Adversarial verification** — independent agents try to *refute* the finding
   (Phoenix's existing verify pattern). Majority refutation kills it.
3. **The fix is proven by a regression test** — contribution is paid on *closed*
   findings: a test that fails before the patch and passes after. Same worker loop as
   the builder harness.

## 4. The gate

Reversible (free): reading code, static analysis, fuzzing the sandbox replica,
writing PoCs *inside* the sandbox, drafting fixes and regression tests.

Irreversible (human, Article IV): touching any live system, running anything against
a third party, **disclosure** (writing to a vendor, filing a CVE, publishing),
deploying a patch to production, or exporting a finding dossier off-box.

## 5. Why governance changes the economics

Security automation drowns teams in unverified findings. Here: **an unreproduced
finding earns nothing** (Article I.2 — work that doesn't move the score is waste, and
waste is counted), a thrashing agent is reaped at budget (II), the board triages with
disjoint evidence — severity (Prudence), exploitability (Ledger), coverage of the
Vision (Growth) — and every finding arrives with provenance and a regression test
attached. That's a *report you can act on*, not a scanner dump.

## 6. Build order

1. ✅ Sandbox replica + repro oracle on a deliberately vulnerable toy app (no network,
   no creds) — the security twin of `sandbox/calculator.py`. → `sandbox/replica/`
2. ✅ Analyst agent v1: hunt one class → PoC → adversarial verify → contribution.
   The first two adversarial refutations are structural and free: a PoC that fabricates
   its evidence, or one that doesn't reproduce identically twice, is killed before any
   model opinion is asked. → `gov/analyst.py`, `gov/redteam.py`
3. ✅ Fix loop: patch + regression test, paid only when the test proves the fix *and*
   the service's own behaviour suite stays green. → `gov/analyst.py`
4. ✅ The dossier gate: findings packaged with scope, evidence, PoC, fix, and lineage —
   parked for the human before any disclosure or prod deploy. Approval releases the
   *dossier*; no code path performs the irreversible act. → `redteam.gate_request`, `/security`
5. Detection-side telemetry for the org itself (what its own agents did) — the
   ADR-style watch layer, native. *(next)*

## 7. Scope of this harness

Defensive and authorized only: your systems, your CTFs, your engagements. It does not
target third parties, does not automate mass scanning, and produces no capability the
gate doesn't stop at a human. The credential-free, network-free sandbox is the
enforcement; the constitution is the record.

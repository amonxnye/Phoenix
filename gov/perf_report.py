"""A real, measured performance and smoke report — not fabricated numbers.

Every figure below comes from actually running the deterministic parts of the system
in this environment, with real wall-clock timers, isolated from the tracked repo state
(a temp GOV_DATA_DIR, a temp evidence-aliases copy). Nothing here is estimated.

**What this cannot measure, and why.** `brain.available()` is checked first and
reported honestly. Generating a hypothesis, proposing a patch, or writing a claim is a
*model* call — it needs BRAIN_API_KEY + BRAIN_BASE_URL (or DEEPSEEK_API_KEY) set in the
environment, and none are present here. Faking a token/sec or latency number would be a
lie dressed as a benchmark, so this script refuses to: it names exactly what a live run
would add on top of everything measured below (network round-trip + inference latency,
per model call), and stops.

**What this DOES measure — the honest floor.** Every agent cycle in this system is
"a decision, then the governed machinery around it": apply a patch and score it, submit
a claim and check it, climb the campaign ladder. The *machinery* — sandboxed
subprocess scoring, the citation oracle, SQLite-backed lineage, the campaign ladder's
own bookkeeping — runs today, for real, with no model in the loop (the acceptance
suites already prove it does the right thing; this proves how fast). A live model call
adds latency on top of these numbers. It never subtracts from them — this is the floor
production throughput cannot beat, only approach.

Run:  python3 gov/perf_report.py
"""

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_args = argparse.ArgumentParser()
_args.add_argument("--live", action="store_true",
                   help="if a brain is configured, spend ONE real model call to time "
                        "it — off by default, since this script must never spend "
                        "tokens or money without being explicitly told to")
ARGS = _args.parse_args()

BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"
GOOD, WARN = "\033[32m", "\033[33m"


def section(title):
    print(f"\n{BOLD}{title}{OFF}")


def stat(label, values, unit="ms", n_label="cycles"):
    n = len(values)
    mean = statistics.mean(values) * 1000
    lo, hi = min(values) * 1000, max(values) * 1000
    rate = n / sum(values) if sum(values) > 0 else float("inf")
    print(f"   {label:<42} {mean:>7.1f} {unit} avg  "
          f"({lo:.1f}-{hi:.1f} range, {n} {n_label}, {rate:.1f}/s)")
    return {"label": label, "n": n, "mean_ms": round(mean, 2),
            "min_ms": round(lo, 2), "max_ms": round(hi, 2), "per_sec": round(rate, 2)}


REPORT = {"sections": {}}

_TMP = tempfile.mkdtemp(prefix="phoenix-perf-")
os.environ["GOV_DATA_DIR"] = _TMP

import anchor
import literature as L
import replication as REP

anchor.init()

_ALIASES = L.ALIASES_PATH
if os.path.exists(_ALIASES):
    shutil.copy(_ALIASES, os.path.join(_TMP, "aliases.json"))
L.ALIASES_PATH = os.path.join(_TMP, "aliases.json")

try:
    # ── 0. what a model would add, honestly declared before anything else ────
    import brain
    section("0. Brain — is a live model configured in this environment?")
    available = brain.available()
    print(f"   available: {GOOD if available else WARN}{available}{OFF}  "
         f"(brain.brain_name() = {brain.brain_name()!r})")
    if not available:
        print(f"   {DIM}no BRAIN_API_KEY/BRAIN_BASE_URL and no DEEPSEEK_API_KEY in this "
             f"environment — every number below is the governed MACHINERY around a\n"
             f"   decision, never the decision itself. A live model call adds real "
             f"network + inference latency (typically hundreds of ms to a few seconds\n"
             f"   per call, provider-dependent) ON TOP of every figure here — set one "
             f"of those env vars and re-run to get that number for real, rather than\n"
             f"   an invented one.{OFF}")
    REPORT["brain_available"] = available
    REPORT["brain_name"] = brain.brain_name()
    if available and ARGS.live:
        print(f"   {WARN}--live set: spending one real model call to time it{OFF}")
        t0 = time.perf_counter()
        try:
            out = brain._chat([{"role": "user", "content": "Reply with only: ok"}],
                              20, 0.0, "perf-report-live-probe")
            live_ms = (time.perf_counter() - t0) * 1000
            print(f"   one real model call: {live_ms:.0f} ms  (reply: {out[:40]!r})")
            REPORT["live_call_ms"] = round(live_ms, 1)
        except Exception as e:
            print(f"   {WARN}the live call failed: {type(e).__name__}: {str(e)[:120]}{OFF}")
            REPORT["live_call_error"] = str(e)[:200]
    elif available:
        print(f"   a brain IS configured, but this run did not spend a real call — "
             f"pass --live to time one for real (never spent by default)")

    # ── 1. the worker's cycle — sandboxed subprocess scoring, real cost ───────
    section("1. Worker cycle — workspace.oracle() + apply_patch, repeated for real")
    import workspace as W
    W.init()
    pristine = W.read_file("calculator.py")
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        W.apply_patch("calculator.py", pristine)
        W.oracle()
        times.append(time.perf_counter() - t0)
    REPORT["sections"]["worker_cycle"] = stat(
        "one patch-apply + sandboxed test-suite score", times)
    print(f"   {DIM}this is what runs on every worker turn regardless of what the "
         f"model proposes — a real subprocess boot + test discovery{OFF}")

    # ── 2. the researcher's cycle — the toy-corpus citation oracle ────────────
    section("2. Researcher cycle — submit_and_score against the toy corpus")
    import economy
    import research_world as RW
    import researcher as RS
    RW.init()
    economy.init()
    truth = RW.assay("CMP-101", "KIN-X")["affinity"]
    RS.reproduce_and_score("perf-test", "CMP-101", "KIN-X", truth)   # pass the gate once
    claims = [({"subject": "CMP-101", "relation": "inhibits", "object": "KIN-X"}, ["P-001"]),
             ({"subject": "KIN-X", "relation": "upregulates", "object": "PATH-INFLAM"},
              ["P-002", "P-003"])]
    times = []
    for claim, cites in claims * 3:
        t0 = time.perf_counter()
        RS.submit_and_score(f"perf-{time.perf_counter_ns()}", claim, cites)
        times.append(time.perf_counter() - t0)
    REPORT["sections"]["researcher_cycle"] = stat(
        "one claim submitted + oracle-scored + recorded to the anchor", times)

    # ── 3. the literature oracle — pure, offline, no I/O beyond the read ──────
    section("3. Literature oracle — supports() on real retrieved evidence")
    rec = L.store_get("PMID:41884158")
    quote = next(s for s in L.sentences(L.record_text(rec)) if "AMPK/Drp1 pathway" in s)
    claim = {"subject": "metformin", "relation": "activates", "object": "AMPK"}
    times = []
    for _ in range(200):
        t0 = time.perf_counter()
        L.supports(claim, rec, quote)
        times.append(time.perf_counter() - t0)
    REPORT["sections"]["literature_oracle"] = stat(
        "one claim checked against one paper's stored text", times, n_label="checks")
    print(f"   {DIM}this is the check a human (or, in the console, a browser) gets an "
         f"answer from instantly — no network, no model{OFF}")

    # ── 4. the campaign ladder — a full, real, offline run ────────────────────
    section("4. Campaign engine — one full run against a real paper, offline")
    import campaign as CAM
    REP.reset()
    spec = CAM.load_spec(os.path.join(os.path.dirname(HERE), "sandbox", "campaigns",
                                      "PMID-41964971.json"))
    CAM.prepare(spec)
    t0 = time.perf_counter()
    result = CAM.Campaign(spec, offline=True, log=lambda *_: None).run()
    elapsed = time.perf_counter() - t0
    print(f"   {'one full replication campaign':<42} {elapsed * 1000:>7.1f} ms total  "
         f"({result['rounds']} rounds, {elapsed / result['rounds'] * 1000:.1f} ms/round)")
    print(f"   verdict: {result['verdict']} ({result['how']}), "
         f"{len(result['skills_learnt'])} lessons learnt this run")
    REPORT["sections"]["campaign_run"] = {
        "total_ms": round(elapsed * 1000, 2), "rounds": result["rounds"],
        "ms_per_round": round(elapsed / result["rounds"] * 1000, 2),
        "verdict": result["verdict"], "how": result["how"]}

    # ── 5. the settlement play loop — a real multi-turn run ───────────────────
    section("5. Director — a real settlement run, turns/sec")
    import director as D
    import sim
    sim.connect()
    t0 = time.perf_counter()
    dres = D.run(turns=15, target_villagers=3, verbose=False)
    elapsed = time.perf_counter() - t0
    print(f"   {'15-turn settlement run':<42} {elapsed * 1000:>7.1f} ms total  "
         f"({elapsed / 15 * 1000:.1f} ms/turn, {len(dres['events'])} events)")
    REPORT["sections"]["director_run"] = {
        "total_ms": round(elapsed * 1000, 2), "turns": 15,
        "ms_per_turn": round(elapsed / 15 * 1000, 2), "events": len(dres["events"])}

    # ── summary ────────────────────────────────────────────────────────────
    section("Summary")
    print(f"   All figures above measured in-process, this run, on this machine — "
         f"not representative of a different host's CPU/disk.")
    print(f"   Brain configured: {available}."
         + (f" Live call: {REPORT['live_call_ms']:.0f} ms (see section 0)."
            if "live_call_ms" in REPORT else
            " No live-model figures exist in this report — none were invented, and "
            "none were spent without --live." if available else
            " No live-model figures exist in this report; none were invented."))
finally:
    L.ALIASES_PATH = _ALIASES
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{DIM}(GOV_DATA_DIR was a temp dir, discarded; the tracked repo was not "
     f"touched){OFF}\n")

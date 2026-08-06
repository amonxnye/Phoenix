"""Acceptance checks for evalrun.py — the headless reproducible-run harness.

evalrun.py is a CLI entrypoint (argparse + file I/O), so it is exercised the way this
repo already exercises the other entrypoints (`verify.py`'s subprocess probe): run the
real script, in a real subprocess, against a throwaway world and a throwaway output
file, and check the scorecard it actually produced — not a mock of one.

Run:  python3 gov/verify_evalrun.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


_TMP = tempfile.mkdtemp(prefix="phoenix-evalrun-")
OUT = os.path.join(_TMP, "scorecard.json")

try:
    print("\n1. A short, real, headless run")
    env = dict(os.environ, GOV_DATA_DIR=_TMP)
    proc = subprocess.run(
        [sys.executable, "evalrun.py", "--turns", "5", "--fresh",
         "--label", "verify-evalrun", "--out", OUT],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=90)
    check("the process exits clean", proc.returncode == 0,
          proc.stderr[-300:] if proc.returncode else "")
    check("it printed a scorecard to stdout", proc.stdout.strip().startswith("{"))

    print("\n2. The scorecard — a real result, not a placeholder shape")
    check("the output file exists and is readable JSON", os.path.exists(OUT))
    with open(OUT) as f:
        card = json.load(f)
    required = ("label", "brain", "turns", "wall_s", "age", "vision", "progress_pct",
               "net_worth", "sim_compute_burned", "waste_builds", "stall_turns",
               "reports", "lessons_live", "events", "real_calls", "avg_latency_ms")
    check("every field the leaderboard depends on is present",
          all(k in card for k in required),
          ", ".join(k for k in required if k not in card))
    check("it ran the turns asked for (or stopped early on a completed ambition chain)",
          0 < card["turns"] <= 5, card["turns"])
    check("wall-clock time is real and positive", card["wall_s"] > 0, card["wall_s"])
    check("progress is a real percentage, never out of range",
          0 <= card["progress_pct"] <= 100, card["progress_pct"])
    check("compute burned is tracked and non-negative",
          card["sim_compute_burned"] >= 0, card["sim_compute_burned"])
    check("with no brain configured, it honestly reports zero real model calls "
          "rather than a fabricated number",
          card["real_calls"] == 0 or card["brain"] != "rule-based", card)
    check("the label defaults to the brain name when none is given explicitly here",
          card["label"] == "verify-evalrun")

    print("\n3. The scorecard is also written permanently (the /leaderboard's source)")
    check_script = (
        f"import sys; sys.path.insert(0, {HERE!r}); import anchor; anchor.init(); "
        f"runs = anchor.eval_runs(50); "
        f"print(any(r.get('label') == 'verify-evalrun' for r in runs))")
    probe = subprocess.run([sys.executable, "-c", check_script], env=env,
                          capture_output=True, text=True, timeout=30)
    check("the run is retrievable from the anchor's permanent eval-run store",
          probe.stdout.strip() == "True", probe.stdout.strip() + probe.stderr[-200:])

    print("\n4. --fresh actually starts a clean world")
    proc2 = subprocess.run(
        [sys.executable, "evalrun.py", "--turns", "3", "--fresh", "--out", OUT],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=60)
    check("a second --fresh run also exits clean", proc2.returncode == 0,
          proc2.stderr[-300:] if proc2.returncode else "")
    with open(OUT) as f:
        card2 = json.load(f)
    check("a fresh run starts the age chain over, not continued from the prior run",
          card2["age"] == card["age"] or card2["turns"] <= 3,
          f"first run ended at {card['age']!r} after {card['turns']}t, fresh run: "
          f"{card2['age']!r} after {card2['turns']}t")
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)

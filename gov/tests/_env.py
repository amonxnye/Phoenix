"""Test environment: an isolated anchor, and a clean slate between tests.

Imported FIRST by every test module. ``anchor`` resolves its data directory at import
time, so GOV_DATA_DIR has to be set before anything imports it — otherwise a test run
would write into (and read from) a real settlement's permanent memory, which is exactly
the thing this project promises never to lose.

Run:  python3 -m unittest discover -s gov/tests -t gov/tests -v
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GOV = os.path.dirname(HERE)
if GOV not in sys.path:
    sys.path.insert(0, GOV)

# One temp anchor for the whole run: the modules under test cache the path at import.
os.environ.setdefault("GOV_DATA_DIR", tempfile.mkdtemp(prefix="phoenix-tests-"))

# Environment that must never leak in from the developer's shell — a real key or a
# real role assignment would make these tests pass or fail for the wrong reason.
DIRTY = ("BRAIN_BASE_URL", "BRAIN_API_KEY", "BRAIN_MODEL", "DEEPSEEK_API_KEY",
         "DEEPSEEK_MODEL", "MODEL_REGISTRY", "MODEL_PRICES", "EVAL_BUDGET_USD",
         "EVAL_PROVIDER_CAP_USD", "OPENAI_API_KEY", "GEMINI_API_KEY",
         "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "CONSOLE_TOKEN", "PUBLIC_CHAT",
         "LANGSMITH_TRACING")

import anchor  # noqa: E402  (must follow the GOV_DATA_DIR assignment)
import models  # noqa: E402

anchor.init()


def scrub() -> None:
    """Remove every environment variable that could steer the code under test."""
    for k in DIRTY:
        os.environ.pop(k, None)
    for role in models.ROLES:
        os.environ.pop(f"MODEL_ROLE_{role.upper()}", None)
    for key in ("models.roles", "models.registry", "models.prices"):
        anchor.config_set(key, "")
    models.reset_run_state()


class Base(unittest.TestCase):
    """Every test starts from a known-empty configuration AND its own anchor.

    Isolation is per test, not per run: the anchor is global mutable state, and tests
    that share it pass or fail depending on the order they happen to run in — which is
    how a suite quietly stops being evidence of anything."""

    ISOLATE_ANCHOR = True

    def setUp(self):
        scrub()
        if self.ISOLATE_ANCHOR:
            self._dir = tempfile.mkdtemp(prefix="phoenix-test-")
            self._db, self._events = anchor.DB, anchor.EVENTS_PATH
            anchor.DB = os.path.join(self._dir, "anchor.sqlite")
            anchor.EVENTS_PATH = os.path.join(self._dir, "events.jsonl")
            anchor.init()

    def tearDown(self):
        scrub()
        if self.ISOLATE_ANCHOR:
            anchor.DB, anchor.EVENTS_PATH = self._db, self._events
            import shutil
            shutil.rmtree(self._dir, ignore_errors=True)

    # a registry of fake labs: real endpoints are never contacted by these tests
    def register(self, **labs):
        import json
        os.environ["MODEL_REGISTRY"] = json.dumps(labs or {
            "labA": {"kind": "openai", "base_url": "https://a.invalid/v1",
                     "key": "k-a", "model": "model-a", "tier": "ranked"},
            "labB": {"kind": "openai", "base_url": "https://b.invalid/v1",
                     "key": "k-b", "model": "model-b", "tier": "ranked"},
        })

    def prices(self, **table):
        import json
        os.environ["MODEL_PRICES"] = json.dumps(table)

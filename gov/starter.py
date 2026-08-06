"""Starter workspaces — a real repository with real defects, built on demand.

A guest arriving at the gate has no repository of ours to point at, and handing an
anonymous visitor a clone of somebody's real code would be the wrong answer to the
wrong question. What they need is a repository that behaves exactly like one of
theirs: real git history, real source, a real test suite that really fails, and an
oracle that really has to be satisfied.

So the service builds one. Nothing here is a fixture or a transcript — provisioning
runs `git init`, writes modules with genuine defects, commits them, and then runs
the suite to confirm the tests it claims are red actually are. A starter that
provisions green is a broken starter, and ``provision`` says so rather than handing
out a workspace where the agents would have nothing to do.

The suites are stdlib ``unittest`` so a deployment needs no test runner installed,
and each defect is the kind that has an unambiguous right answer: the test states a
contract the code does not honour. That matters because the whole system's claim is
that the runner decides — a starter whose tests were vague would be measuring the
model's taste instead.
"""

import os
import shutil
import subprocess
import sys

TEST_CMD = f"{sys.executable} -m unittest discover -s tests -v"

_TEST_HEADER = '''import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

'''


# ── the catalogue ────────────────────────────────────────────────────────────
#
# Each entry is a small, complete project. `red` names the tests that must fail at
# the initial commit; provisioning verifies the claim instead of trusting it.

STARTERS = {
    "orders": {
        "title": "Order pricing",
        "blurb": "Shipping, delivery estimates and discount codes. Four behaviours "
                 "were stubbed out and never finished.",
        "red": ["test_discount_code", "test_eta_days", "test_eta_express",
                "test_shipping_express_surcharge", "test_shipping_scales_with_weight",
                "test_total_includes_shipping_and_tax"],
        "files": {
            "src/orders.py": '''"""Order pricing."""

TAX = 0.08
RATE_PER_KG = 1.5
EXPRESS_MULTIPLIER = 2
KM_PER_DAY = 167


def shipping(weight_kg, express=False):
    # STUB: a flat rate that ignores both the weight and the service level
    return 5.0


def eta_days(distance_km, express=False):
    # STUB: never replaced with a real estimate
    return 99


def discount(code, subtotal):
    # STUB: no code is honoured yet
    return 0.0


def total(subtotal, weight_kg, code="", express=False):
    taxed = (subtotal - discount(code, subtotal)) * (1 + TAX)
    return round(taxed + shipping(weight_kg, express), 2)
''',
            "tests/test_orders.py": _TEST_HEADER + '''import orders


class TestShipping(unittest.TestCase):
    def test_tax_rate_is_eight_percent(self):
        self.assertEqual(orders.TAX, 0.08)

    def test_shipping_scales_with_weight(self):
        self.assertEqual(orders.shipping(2.0), 3.0)
        self.assertEqual(orders.shipping(4.0), 6.0)

    def test_shipping_express_surcharge(self):
        self.assertEqual(orders.shipping(2.0, express=True), 6.0)


class TestDelivery(unittest.TestCase):
    def test_eta_days(self):
        self.assertEqual(orders.eta_days(500), 3)
        self.assertEqual(orders.eta_days(100), 1)

    def test_eta_express(self):
        self.assertEqual(orders.eta_days(500, express=True), 1)


class TestDiscount(unittest.TestCase):
    def test_discount_code(self):
        self.assertEqual(orders.discount("SAVE10", 200.0), 20.0)
        self.assertEqual(orders.discount("NOPE", 200.0), 0.0)


class TestTotal(unittest.TestCase):
    def test_total_includes_shipping_and_tax(self):
        # 100 subtotal, 10% off, 8% tax, 2kg standard shipping
        self.assertEqual(orders.total(100.0, 2.0, code="SAVE10"), 100.2)
''',
        },
    },

    "inventory": {
        "title": "Stock control",
        "blurb": "Reorder points and low-stock alerts. The arithmetic ignores lead "
                 "time and the boundary test is off by one.",
        "red": ["test_low_at_the_boundary", "test_reorder_point",
                "test_reorder_point_with_safety_stock", "test_restock_never_negative"],
        "files": {
            "src/inventory.py": '''"""Stock control helpers."""


def reorder_point(daily_usage, lead_time_days, safety_stock=0):
    # BUG: lead time is accepted and then ignored
    return daily_usage + safety_stock


def is_low(stock, point):
    # BUG: off by one — a stock level exactly at the reorder point is low
    return stock < point - 1


def restock_qty(stock, target):
    # BUG: goes negative when there is already more than the target
    return target - stock
''',
            "tests/test_inventory.py": _TEST_HEADER + '''import inventory


class TestReorderPoint(unittest.TestCase):
    def test_reorder_point(self):
        self.assertEqual(inventory.reorder_point(10, 3), 30)

    def test_reorder_point_with_safety_stock(self):
        self.assertEqual(inventory.reorder_point(10, 3, safety_stock=15), 45)


class TestLowStock(unittest.TestCase):
    def test_low_at_the_boundary(self):
        self.assertTrue(inventory.is_low(30, 30))

    def test_not_low_above_the_point(self):
        self.assertFalse(inventory.is_low(31, 30))


class TestRestock(unittest.TestCase):
    def test_restock_to_target(self):
        self.assertEqual(inventory.restock_qty(10, 30), 20)

    def test_restock_never_negative(self):
        self.assertEqual(inventory.restock_qty(50, 30), 0)
''',
        },
    },

    "textkit": {
        "title": "Text utilities",
        "blurb": "Slugs, truncation and word wrapping — each one written to the "
                 "easy case and left there.",
        "red": ["test_slugify_drops_punctuation", "test_slugify_hyphenates_spaces",
                "test_truncate_long_text", "test_wrap_keeps_words_whole"],
        "files": {
            "src/textkit.py": '''"""Small text helpers."""

ELLIPSIS = "\\u2026"


def slugify(name):
    # BUG: spaces and punctuation both survive
    return name.strip().lower()


def truncate(text, limit):
    # BUG: the limit is ignored entirely
    return text


def wrap_words(text, width):
    # BUG: cuts through the middle of words
    return [text[i:i + width] for i in range(0, len(text), width)]
''',
            "tests/test_textkit.py": _TEST_HEADER + '''import textkit


class TestSlugify(unittest.TestCase):
    def test_slugify_hyphenates_spaces(self):
        self.assertEqual(textkit.slugify("  Hello World  "), "hello-world")

    def test_slugify_drops_punctuation(self):
        self.assertEqual(textkit.slugify("Hello, World!"), "hello-world")


class TestTruncate(unittest.TestCase):
    def test_truncate_short_text_is_unchanged(self):
        self.assertEqual(textkit.truncate("abc", 5), "abc")

    def test_truncate_long_text(self):
        self.assertEqual(textkit.truncate("abcdefgh", 5), "abcd" + textkit.ELLIPSIS)


class TestWrap(unittest.TestCase):
    def test_wrap_keeps_words_whole(self):
        self.assertEqual(textkit.wrap_words("the quick brown fox", 10),
                         ["the quick", "brown fox"])
''',
        },
    },
}

ORDER = ("orders", "inventory", "textkit")


def catalogue() -> list[dict]:
    return [{"name": n, "title": STARTERS[n]["title"], "blurb": STARTERS[n]["blurb"],
             "red": len(STARTERS[n]["red"])} for n in ORDER]


def pick(seed: str = "") -> str:
    """Which starter a given guest gets. Derived from the session so the same visitor
    keeps the same project across resets, and different visitors spread across the
    catalogue without a shared counter."""
    if not seed:
        return ORDER[0]
    return ORDER[sum(seed.encode()) % len(ORDER)]


def git_available() -> bool:
    """Whether this machine has git at all.

    Worth asking before anything else: the Builder's undo *is* a branch, so an
    environment without git has no isolation to offer, and finding that out when a
    visitor clicks a button is finding out too late."""
    return shutil.which("git") is not None


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(("git", "-C", repo) + args, capture_output=True,
                              text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError(
            "git is not installed on this machine. The Builder cannot work without "
            "it — every attempt runs in a git worktree and rejecting a patch means "
            "deleting a branch.") from None


def provision(dest: str, name: str = "") -> dict:
    """Build a starter repository at `dest` and prove its tests are red.

    Returns the workspace description the rest of the service works from. Raises
    RuntimeError if git is unavailable or the suite does not fail as claimed —
    handing out a workspace with nothing wrong in it would waste a guest's run and
    quietly break the one measurement the whole system rests on."""
    if not git_available():
        raise RuntimeError(
            "git is not installed on this machine. The Builder cannot work without "
            "it — every attempt runs in a git worktree and rejecting a patch means "
            "deleting a branch.")
    name = name if name in STARTERS else ORDER[0]
    spec = STARTERS[name]
    os.makedirs(dest, exist_ok=True)

    for rel, content in spec["files"].items():
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)

    init = _git(dest, "init", "-q")
    if init.returncode != 0:
        raise RuntimeError(f"git init failed: {init.stderr.strip()[:200]}")
    # a server has no global git identity, and a commit without one fails
    _git(dest, "config", "user.email", "starter@phoenix.local")
    _git(dest, "config", "user.name", "Phoenix Starter")
    _git(dest, "config", "commit.gpgsign", "false")
    _git(dest, "add", "-A")
    commit = _git(dest, "commit", "-q", "-m",
                  f"initial: {spec['title'].lower()} with unfinished behaviour")
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        raise RuntimeError(f"initial commit failed: {commit.stderr.strip()[:200]}")

    red = _red_tests(dest)
    if not red:
        raise RuntimeError(f"starter '{name}' provisioned green — nothing to fix")

    return {"repo": os.path.realpath(dest), "starter": name, "title": spec["title"],
            "blurb": spec["blurb"], "test_cmd": TEST_CMD, "red": red,
            "base_branch": _current_branch(dest)}


def _current_branch(repo: str) -> str:
    out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return (out.stdout or "").strip() or "main"


def _red_tests(repo: str) -> list[str]:
    """Ask the same oracle the agents will be judged by. Imported lazily because
    workspace holds module-level configuration this function must not disturb."""
    import workspace as W

    saved = W.config()
    try:
        W.configure(repo=repo, test_cmd=TEST_CMD, key=repo)
        verdict = W.oracle()
        if not verdict["ok"]:
            raise RuntimeError(f"the starter suite gave no verdict: {verdict['error'][:200]}")
        return sorted(verdict["failures"])
    finally:
        W.configure(repo=saved["repo"], test_cmd=saved["test_cmd_str"],
                    timeout=saved["timeout"], protected=saved["protected"],
                    key=saved["key"])

"""Unit tests for the logistics harness — every public function, one behaviour each.

`verify_logistics.py` is the acceptance suite: it proves the *claims* the constitution
and LOGISTICS.md make, in the language a human reads. This file is the other half — the
engineering tests, one per function, that fail loudly and specifically when a refactor
breaks something small. Together they are the safety net; separately neither is enough.

Every test runs against a private temporary database, so the suite is hermetic: no
shared state, no ordering dependency, safe to run in parallel with anything else.

Run:  python3 -m unittest discover -s gov -p "test_*.py" -v
      python3 gov/test_logistics.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Every module below resolves its database at import time from GOV_DATA_DIR, so the
# temporary directory has to exist before the first import.
_TMP = tempfile.mkdtemp(prefix="phoenix-logistics-tests-")
os.environ["GOV_DATA_DIR"] = _TMP
os.environ.pop("GOV_LOGISTICS_ENVELOPE", None)
os.environ.pop("CONSOLE_TOKEN", None)

import anchor                                                        # noqa: E402
import board as B                                                    # noqa: E402
import economy as E                                                  # noqa: E402
import logistics_console as LC                                       # noqa: E402
import logistics_world as L                                          # noqa: E402
import planner as P                                                  # noqa: E402


def setUpModule():
    anchor.init()          # the permanent record's tables must exist before anything
    E.init()               # writes a decision, a career or a credit
    L.init()


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def _fresh_world():
    """A brand-new logistics database for one test. The anchor and the economy are
    shared (they are append-only records), but the world must not be."""
    for suffix in ("", "-wal", "-shm"):
        p = L.DB + suffix
        if os.path.exists(p):
            os.remove(p)
    L.init()


class WorldShape(unittest.TestCase):
    """The network definition itself — the things every other test assumes."""

    def test_every_sku_names_a_vendor_with_capacity(self):
        for sku, s in L.SKUS.items():
            self.assertIn(s["vendor"], L.VENDOR_CAPACITY, f"{sku} has no vendor capacity")
            self.assertGreater(s["lead"], 0, f"{sku} has a non-positive lead time")
            self.assertGreater(s["shelf_life"], 0, f"{sku} never expires")
            self.assertGreater(s["cost"], 0, f"{sku} is free")

    def test_the_a_list_is_derived_not_typed(self):
        self.assertEqual(set(L.A_LIST), {k for k, v in L.SKUS.items() if v["a_list"]})

    def test_every_node_has_a_capacity(self):
        self.assertEqual(set(L.NODES), set(L.NODE_CAPACITY))

    def test_the_weights_are_a_partition(self):
        self.assertAlmostEqual(sum(L.WEIGHTS.values()), 1.0, places=9)

    def test_money_is_formatted_in_one_place(self):
        self.assertEqual(L.m(1234.5), f"{L.CURRENCY}1,234")
        self.assertEqual(L.m(1234.5, 2), f"{L.CURRENCY}1,234.50")


class Demand(unittest.TestCase):
    """The oracle's raw material: deterministic, bounded, and split."""

    def test_demand_is_reproducible_in_any_order(self):
        forward = [L.demand("FRESH-01", t) for t in range(40)]
        backward = [L.demand("FRESH-01", t) for t in reversed(range(40))]
        self.assertEqual(forward, list(reversed(backward)))

    def test_demand_is_never_negative(self):
        for sku in L.SKUS:
            for t in range(0, L.PERIODS, 7):
                self.assertGreaterEqual(L.demand(sku, t), 0)

    def test_a_scenario_only_moves_demand_inside_its_window(self):
        sc = L.SCENARIOS["demand_spike"]
        lo, hi = sc["window"]
        self.assertEqual(L.demand("STAPLE-02", lo - 1, sc), L.demand("STAPLE-02", lo - 1))
        self.assertGreaterEqual(L.demand("STAPLE-02", lo, sc), L.demand("STAPLE-02", lo))

    def test_history_serves_the_train_window_only(self):
        h = L.demand_history()
        self.assertEqual(set(h), set(L.SKUS))
        self.assertEqual(len(h["FRESH-01"]), L.TRAIN[1] - L.TRAIN[0] + 1)

    def test_history_refuses_the_holdout(self):
        with self.assertRaises(ValueError) as e:
            L.demand_history(window=L.HOLDOUT)
        self.assertIn("REFUSED", str(e.exception))

    def test_history_refuses_a_window_that_merely_overlaps_the_holdout(self):
        with self.assertRaises(ValueError):
            L.demand_history(window=(0, L.TRAIN[1] + 1))

    def test_train_stats_describe_the_readable_window(self):
        st = L.train_stats()
        self.assertEqual(set(st), set(L.SKUS))
        for sku, s in st.items():
            self.assertGreater(s["mean_daily"], 0)
            self.assertGreaterEqual(s["stdev"], 0)
            self.assertEqual(s["vendor"], L.SKUS[sku]["vendor"])
            self.assertAlmostEqual(s["lead_demand"],
                                   round(s["mean_daily"] * s["lead_days"], 1), places=6)


class Policies(unittest.TestCase):
    """Normalising, compiling and the three reference policies."""

    def test_normalize_fills_in_every_sku_and_node(self):
        p = L.normalize_policy({})
        self.assertEqual(set(p), set(L.SKUS))
        for sku in p:
            self.assertEqual(set(p[sku]), set(L.NODES))

    def test_normalize_clamps_nonsense_rather_than_trusting_it(self):
        p = L.normalize_policy({"FRESH-01": {"dc": {"s": -50, "S": 10 ** 9}}})
        self.assertEqual(p["FRESH-01"]["dc"]["s"], 0)
        self.assertLessEqual(p["FRESH-01"]["dc"]["S"], L.NODE_CAPACITY["dc"])

    def test_normalize_survives_garbage_types(self):
        for junk in (None, [], "nope", {"FRESH-01": "nope"},
                     {"FRESH-01": {"dc": {"s": "abc", "S": None}}}):
            p = L.normalize_policy(junk)
            self.assertEqual(set(p), set(L.SKUS))

    def test_order_up_to_is_never_below_the_reorder_point(self):
        p = L.normalize_policy({s: {n: {"s": 300, "S": 5} for n in L.NODES} for s in L.SKUS})
        for sku in p:
            for node in L.NODES:
                self.assertGreaterEqual(p[sku][node]["S"], p[sku][node]["s"])

    def test_build_policy_never_orders_past_the_shelf_life(self):
        big = {s: {n: {"z": 3.0, "cover": 999.0} for n in L.NODES} for s in L.SKUS}
        p = L.build_policy(big)
        st = L.train_stats()
        for sku in L.SKUS:
            ceiling = st[sku]["mean_daily"] * L.SKUS[sku]["shelf_life"] * L.SHELF_TURN
            self.assertLessEqual(p[sku]["dc"]["S"], ceiling + 1,
                                 f"{sku} would order more than it can sell before it rots")

    def test_build_policy_is_monotonic_in_safety_stock(self):
        low = L.build_policy(L.textbook_knobs(z=0.5, cover=4))
        high = L.build_policy(L.textbook_knobs(z=2.5, cover=4))
        self.assertGreater(high["LUX-03"]["dc"]["s"], low["LUX-03"]["dc"]["s"])

    def test_build_policy_tolerates_missing_knobs(self):
        p = L.build_policy({"FRESH-01": {}})
        self.assertEqual(set(p), set(L.SKUS))

    def test_do_nothing_orders_nothing(self):
        p = L.do_nothing()
        self.assertTrue(all(p[s][n]["S"] == 0 for s in p for n in L.NODES))

    def test_the_reference_policies_are_distinct(self):
        seen = {json.dumps(p, sort_keys=True) for p in
                (L.do_nothing(), L.naive_reorder_point(), L.order_everything())}
        self.assertEqual(len(seen), 3)


class Simulator(unittest.TestCase):
    """The oracle's arithmetic — the part that must not quietly drift."""

    @classmethod
    def setUpClass(cls):
        cls.naive = L.naive_reorder_point()
        cls.sc = L.simulate(cls.naive, L.HOLDOUT)

    def test_the_scorecard_carries_every_axis(self):
        for k in ("fill_rate", "a_list_fill", "working_capital", "waste_cost",
                  "expedite_spend", "stockout_events", "a_list_stockouts",
                  "total_cost", "efficiency", "score", "mandate_met"):
            self.assertIn(k, self.sc)

    def test_only_the_scored_window_is_counted(self):
        self.assertEqual(self.sc["periods"], L.HOLDOUT[1] - L.HOLDOUT[0] + 1)

    def test_shipped_never_exceeds_demanded(self):
        self.assertLessEqual(self.sc["units_shipped"], self.sc["units_demanded"])
        self.assertLessEqual(self.sc["fill_rate"], 1.0)

    def test_lost_units_reconcile_with_shipped(self):
        self.assertEqual(self.sc["units_demanded"] - self.sc["units_shipped"],
                         self.sc["lost_units"])

    def test_simulation_is_deterministic(self):
        again = L.simulate(L.naive_reorder_point(), L.HOLDOUT)
        self.assertEqual(json.dumps(self.sc, sort_keys=True),
                         json.dumps(again, sort_keys=True))

    def test_ordering_nothing_holds_no_capital(self):
        idle = L.simulate(L.do_nothing(), L.HOLDOUT)
        self.assertLess(idle["working_capital"], self.sc["working_capital"])
        self.assertLess(idle["fill_rate"], self.sc["fill_rate"])

    def test_a_perishable_sku_can_actually_spoil(self):
        glut = L.simulate(L.order_everything(), L.HOLDOUT)
        self.assertGreater(glut["waste_cost"], 0)

    def test_storage_capacity_is_a_wall(self):
        """No policy, however greedy, can hold more than the network can store."""
        glut = L.simulate(L.order_everything(), L.HOLDOUT)
        ceiling = sum(L.NODE_CAPACITY.values()) * max(s["cost"] for s in L.SKUS.values())
        self.assertLess(glut["working_capital"], ceiling)

    def test_every_scenario_actually_bites(self):
        """A robustness test that never changes the outcome is decoration — the same
        defect Article VIII.1 names for a governor whose vote never varies. Every
        scenario must move a TUNED plan's scorecard, not just a bad one's: `capacity_cut`
        originally halved supply, which a tuned plan did not notice at all.
        """
        tuned = L.build_policy(L.textbook_knobs())
        base = L.simulate(tuned, L.HOLDOUT)
        for name, sc in L.SCENARIOS.items():
            hurt = L.simulate(tuned, L.HOLDOUT, sc)
            self.assertLess(hurt["score"], base["score"] - 0.01,
                            f"{name} left a tuned plan untouched — it tests nothing")

    def test_a_disruption_never_improves_service_on_a_tuned_plan(self):
        """Fill rate, not volume: a demand spike ships MORE units than a calm week
        while serving a smaller share of what was asked for.

        Stated for a tuned plan deliberately. On an OVER-ORDERING plan a supply
        constraint can genuinely raise the fill rate, because the binding loss there is
        spoilage rather than supply — see the next test. Asserting a universal
        monotonicity would have been asserting something false.
        """
        tuned = L.build_policy(L.textbook_knobs())
        base = L.simulate(tuned, L.HOLDOUT)
        for name, sc in L.SCENARIOS.items():
            hurt = L.simulate(tuned, L.HOLDOUT, sc)
            self.assertLessEqual(hurt["fill_rate"], base["fill_rate"] + 1e-9,
                                 f"{name} raised the share of demand served")

    def test_a_supply_constraint_can_flatter_an_over_ordering_plan(self):
        """Pinning a real finding, so it is a documented property of the model and not
        a surprise the next person has to rediscover: starve a plan that over-orders
        perishables and its waste falls, its capital falls, and its fill rate can even
        rise — the stock it does receive is fresher."""
        base = L.simulate(self.naive, L.HOLDOUT)
        cut = L.simulate(self.naive, L.HOLDOUT, {"vendor_cap_x": 0.5})
        self.assertLess(cut["waste_cost"], base["waste_cost"])
        self.assertLess(cut["working_capital"], base["working_capital"])


class Scoring(unittest.TestCase):
    """The joint score — the anti-gaming argument, in arithmetic."""

    def _card(self, **kw):
        base = {"fill_rate": 1.0, "a_list_fill": 1.0, "working_capital": 1.0,
                "waste_cost": 0.0, "expedite_spend": 0.0}
        base.update(kw)
        return base

    def test_a_perfect_card_scores_one_hundred(self):
        self.assertAlmostEqual(L.score(self._card()), 100.0, places=6)

    def test_no_axis_can_score_above_its_weight(self):
        generous = self._card(fill_rate=1.0, working_capital=0.0001)
        for k, v in L.components(generous).items():
            self.assertLessEqual(v, 1.0, f"{k} exceeded its cap")

    def test_missing_the_target_by_a_third_zeroes_the_service_axis(self):
        dead = L.TARGET_FILL * (1 - 1.0 / L.SERVICE_SLOPE)
        self.assertAlmostEqual(L.components(self._card(fill_rate=dead))["service"], 0.0,
                               places=6)

    def test_capital_is_scored_against_the_ceiling(self):
        at = L.components(self._card(working_capital=L.CAPITAL_CEILING))["capital"]
        over = L.components(self._card(working_capital=L.CAPITAL_CEILING * 2))["capital"]
        self.assertAlmostEqual(at, 1.0, places=6)
        self.assertAlmostEqual(over, 0.5, places=6)

    def test_service_cannot_buy_off_capital(self):
        """The whole reason 'order everything' loses: a perfect fill with runaway
        capital must score below a good fill with disciplined capital."""
        greedy = L.score(self._card(fill_rate=1.0,
                                    working_capital=L.CAPITAL_CEILING * 8,
                                    waste_cost=L.WASTE_BUDGET * 40))
        lean = L.score(self._card(fill_rate=L.TARGET_FILL,
                                  working_capital=L.CAPITAL_CEILING))
        self.assertGreater(lean, greedy)

    def test_the_mandate_needs_all_three_clauses(self):
        good = L.simulate(L.build_policy(L.textbook_knobs()), L.HOLDOUT)
        self.assertEqual(good["mandate_met"],
                         good["fill_rate"] >= L.TARGET_FILL
                         and good["working_capital"] <= L.CAPITAL_CEILING
                         and good["a_list_fill"] >= L.A_LIST_FILL_FLOOR)


class Oracle(unittest.TestCase):
    def test_the_verdict_blends_the_nominal_run_with_the_worst_disruption(self):
        v = L.oracle(L.naive_reorder_point())
        expected = ((1 - L.ROBUST_WEIGHT) * v["nominal"]["score"]
                    + L.ROBUST_WEIGHT * v["robust_score"])
        self.assertAlmostEqual(v["score"], round(expected, 2), places=2)

    def test_every_scenario_is_scored(self):
        v = L.oracle(L.naive_reorder_point())
        self.assertEqual(set(v["scenarios"]), set(L.SCENARIOS))

    def test_the_named_worst_is_the_lowest_scoring_scenario(self):
        v = L.oracle(L.naive_reorder_point())
        low = min(v["scenarios"], key=lambda k: v["scenarios"][k]["score"])
        self.assertIn(v["worst"], (low, ""))

    def test_scenarios_can_be_skipped_for_speed(self):
        v = L.oracle(L.naive_reorder_point(), with_scenarios=False)
        self.assertEqual(v["scenarios"], {})
        self.assertEqual(v["score"], v["nominal"]["score"])


class PolicyLedger(unittest.TestCase):
    def setUp(self):
        _fresh_world()

    def test_recording_and_adopting_leaves_exactly_one_incumbent(self):
        a = L.record_policy("t", L.do_nothing(), L.oracle(L.do_nothing(), False))
        b = L.record_policy("t", L.naive_reorder_point(),
                            L.oracle(L.naive_reorder_point(), False))
        L.adopt(a)
        L.adopt(b)
        self.assertEqual(L.incumbent()["id"], b)
        self.assertEqual(L.policy_count(), 2)

    def test_knobs_survive_the_round_trip(self):
        knobs = L.textbook_knobs(z=1.9, cover=6)
        pid = L.record_policy("t", L.build_policy(knobs),
                              L.oracle(L.build_policy(knobs), False), knobs=knobs)
        L.adopt(pid)
        self.assertAlmostEqual(L.incumbent()["knobs"]["FRESH-01"]["dc"]["z"], 1.9)

    def test_the_leaderboard_is_ordered_by_score(self):
        for pol in (L.do_nothing(), L.naive_reorder_point(), L.order_everything()):
            L.record_policy("t", pol, L.oracle(pol, False))
        scores = [p["score"] for p in L.leaderboard(10)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_incumbent_reads_as_none_not_a_crash(self):
        self.assertIsNone(L.incumbent())
        self.assertEqual(L.world()["progress_pct"], 0)


class TheGate(unittest.TestCase):
    def setUp(self):
        _fresh_world()
        P.ensure_incumbent()

    def test_a_dossier_carries_everything_a_human_needs(self):
        d = L.propose_commitment("t", "FRESH-01", 100)
        for k in ("qty", "vendor", "value", "eta", "forecast", "rollback",
                  "cancel_fee", "wait_cost_per_day"):
            self.assertIn(k, d)
        self.assertEqual(d["status"], "pending")

    def test_the_cancellation_fee_follows_the_value(self):
        d = L.propose_commitment("t", "LUX-03", 10)
        self.assertAlmostEqual(d["cancel_fee"], round(d["value"] * L.CANCEL_FEE_PCT, 2))

    def test_an_unknown_sku_is_refused(self):
        with self.assertRaises(ValueError):
            L.propose_commitment("t", "NOPE-99", 10)

    def test_a_quantity_is_always_at_least_one(self):
        self.assertEqual(L.propose_commitment("t", "FRESH-01", 0)["qty"], 1)
        self.assertEqual(L.propose_commitment("t", "FRESH-01", -5)["qty"], 1)

    def test_waiting_costs_more_the_longer_it_waits(self):
        d = L.propose_commitment("t", "FRESH-01", 100)
        first = next(x for x in L.commitments() if x["id"] == d["id"])
        c = L._conn()
        try:
            c.execute("UPDATE commitments SET ts=? WHERE id=?",
                      (time.time() - 86_400, d["id"]))
            c.commit()
        finally:
            c.close()
        later = next(x for x in L.commitments() if x["id"] == d["id"])
        self.assertGreater(later["cost_of_waiting"], first["cost_of_waiting"])
        self.assertAlmostEqual(later["cost_of_waiting"], later["wait_cost_per_day"],
                               delta=later["wait_cost_per_day"] * 0.05)

    def test_a_decision_lands_once_and_only_once(self):
        d = L.propose_commitment("t", "FRESH-01", 100)
        self.assertTrue(L.decide(d["id"], "approve", "alice")[0])
        self.assertFalse(L.decide(d["id"], "reject", "bob")[0])
        row = next(x for x in L.commitments() if x["id"] == d["id"])
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["decided_by"], "alice")

    def test_an_invented_decision_is_refused(self):
        d = L.propose_commitment("t", "FRESH-01", 100)
        self.assertFalse(L.decide(d["id"], "maybe", "alice")[0])

    def test_the_board_ballots_ride_with_the_dossier(self):
        v = B.vote("test", P.board_context(100.0, 100.0))
        d = L.propose_commitment("t", "FRESH-01", 100, board=v)
        row = next(x for x in L.commitments() if x["id"] == d["id"])
        self.assertEqual(row["board"]["tally"], v["tally"])

    def test_no_order_can_ever_be_placed(self):
        d = L.propose_commitment("t", "FRESH-01", 100)
        L.decide(d["id"], "approve", "alice")
        ok, why = L.place_order(d["id"])
        self.assertFalse(ok)
        self.assertIn("REFUSED", why)
        self.assertIsNone(L.PROCUREMENT_ADAPTER)


class TheEnvelope(unittest.TestCase):
    def setUp(self):
        _fresh_world()
        P.ensure_incumbent()
        self.d = L.propose_commitment("t", "FRESH-01", 100)

    def tearDown(self):
        os.environ.pop("GOV_LOGISTICS_ENVELOPE", None)

    def test_unset_by_default(self):
        env = L.envelope()
        self.assertEqual(env["skus"], [])
        self.assertEqual(env["max_value"], 0)

    def test_nothing_is_inside_an_unset_envelope(self):
        ok, why = L.within_envelope(self.d)
        self.assertFalse(ok)
        self.assertIn("no pre-approved envelope", why)

    def test_malformed_configuration_is_treated_as_unset_not_as_open(self):
        os.environ["GOV_LOGISTICS_ENVELOPE"] = "{not json"
        self.assertEqual(L.envelope()["max_value"], 0)
        self.assertFalse(L.within_envelope(self.d)[0])

    def test_each_clause_can_exclude_on_its_own(self):
        inside = {"skus": ["FRESH-01"], "vendors": ["AgriCo"],
                  "max_value": self.d["value"] + 1}
        os.environ["GOV_LOGISTICS_ENVELOPE"] = json.dumps(inside)
        self.assertTrue(L.within_envelope(self.d)[0])
        for change in ({"skus": ["OTHER"]}, {"vendors": ["OtherCo"]},
                       {"max_value": self.d["value"] - 1}):
            os.environ["GOV_LOGISTICS_ENVELOPE"] = json.dumps({**inside, **change})
            self.assertFalse(L.within_envelope(self.d)[0], change)

    def test_silence_outside_the_envelope_decides_nothing(self):
        c = L._conn()
        try:
            c.execute("UPDATE commitments SET ts=?", (time.time() - L.TACIT_CONSENT_S - 60,))
            c.commit()
        finally:
            c.close()
        swept = P.sweep()
        self.assertEqual(swept[0]["action"], "still waiting")
        self.assertEqual(L.commitments("pending")[0]["id"], self.d["id"])

    def test_a_fresh_request_is_never_swept(self):
        self.assertEqual(P.sweep(), [])


class ThePlanner(unittest.TestCase):
    def setUp(self):
        _fresh_world()

    def test_the_baseline_is_seeded_before_anything_is_measured_against_it(self):
        inc = P.ensure_incumbent()
        self.assertEqual(inc["agent"], "baseline")
        self.assertEqual(P.ensure_incumbent()["id"], inc["id"])   # idempotent

    def test_the_prompt_is_built_from_the_readable_window_only(self):
        inc = P.ensure_incumbent()
        prompt = P.prompt_for(inc)
        holdout = L.simulate(inc["policy"], L.HOLDOUT)
        self.assertNotIn(f"{holdout['working_capital']:,.0f}", prompt)
        self.assertIn("cannot see", prompt)

    def test_rewriting_the_holdout_cannot_change_the_prompt(self):
        inc = P.ensure_incumbent()
        before = P.prompt_for(inc)
        real = L.demand
        try:
            L.demand = lambda sku, t, sc=None: 9_999 if t > L.TRAIN[1] else real(sku, t, sc)
            after = P.prompt_for(inc)
        finally:
            L.demand = real
        self.assertEqual(before, after)

    def test_the_rule_based_proposer_starts_from_inventory_theory(self):
        pol, knobs = P.propose_rule_based(P.ensure_incumbent(), 0)
        self.assertEqual(knobs["FRESH-01"]["dc"]["z"], L.DEFAULT_KNOBS["z"])
        self.assertEqual(pol, L.build_policy(L.textbook_knobs()))

    def test_later_rounds_move_one_coordinate_at_a_time(self):
        inc = P.ensure_incumbent()
        inc["knobs"] = L.textbook_knobs()
        _, knobs = P.propose_rule_based(inc, 3)
        changed = [(s, n) for s in L.SKUS for n in L.NODES
                   if knobs[s][n] != L.textbook_knobs()[s][n]]
        self.assertEqual(len(changed), 1)

    def test_the_search_is_reproducible(self):
        first = P.propose_rule_based(P.ensure_incumbent(), 7)[0]
        second = P.propose_rule_based(P.ensure_incumbent(), 7)[0]
        self.assertEqual(first, second)

    def test_a_worse_policy_is_never_adopted_and_earns_nothing(self):
        P.ensure_incumbent()
        before = L.incumbent()["id"]
        r = P.evaluate_and_score("t-worse", L.do_nothing())
        self.assertEqual(r["did"], "rejected")
        self.assertEqual(L.incumbent()["id"], before)
        self.assertNotIn("contribution", r)

    def test_a_better_policy_is_adopted_and_paid_for(self):
        P.ensure_incumbent()
        r = P.evaluate_and_score("t-better", L.build_policy(L.textbook_knobs()))
        self.assertEqual(r["did"], "adopted")
        self.assertGreater(r["contribution"], 0)
        self.assertEqual(L.incumbent()["id"], r["policy_id"])
        paid = next(x for x in E.roster(alive_only=False) if x["agent"] == "t-better")
        self.assertEqual(paid["contribution"], r["contribution"])

    def test_contribution_is_not_wiped_by_a_second_cycle(self):
        """economy.enlist deliberately resets a row; a planner lives across cycles."""
        P.ensure_incumbent()
        E.enlist("t-multi")
        P.evaluate_and_score("t-multi", L.build_policy(L.textbook_knobs(z=1.0, cover=4)))
        first = next(x for x in E.roster(alive_only=False) if x["agent"] == "t-multi")
        P.evaluate_and_score("t-multi", L.build_policy(L.textbook_knobs(z=1.2, cover=5)))
        second = next(x for x in E.roster(alive_only=False) if x["agent"] == "t-multi")
        self.assertGreaterEqual(second["contribution"], first["contribution"])

    def test_the_planned_quantity_is_one_definition(self):
        inc = P.ensure_incumbent()
        q = P.planned_quantity("FRESH-01", "dc", inc["policy"])
        d = P.commit("t", "FRESH-01", "dc")
        self.assertEqual(d["qty"], q)

    def test_a_planned_order_is_affordable_and_an_oversized_one_is_not(self):
        P.ensure_incumbent()
        # Against the naive baseline the plan is already over the capital ceiling, so
        # NOTHING is affordable — correctly. Ledger only has room to judge once the
        # plan is inside its ceiling, so put a real plan in place first.
        P.evaluate_and_score("t-afford", L.build_policy(L.textbook_knobs()))
        planned = B.vote("planned", P.board_context(500.0, 500.0))
        oversized = B.vote("oversized", P.board_context(500_000.0, 0.0))
        self.assertTrue(planned["ballots"]["Ledger"])
        self.assertFalse(oversized["ballots"]["Ledger"])

    def test_the_board_reaches_different_tallies_on_different_evidence(self):
        P.ensure_incumbent()
        weak = B.vote("x", P.board_context(600.0, 600.0))["tally"]
        P.evaluate_and_score("t-board", L.build_policy(L.textbook_knobs()))
        strong = B.vote("x", P.board_context(600.0, 600.0))["tally"]
        self.assertNotEqual(weak, strong)

    def test_a_run_that_moves_nothing_names_its_binding_constraint(self):
        P.ensure_incumbent()
        P.evaluate_and_score("t-primed", L.build_policy(L.textbook_knobs()))
        r = P.run_search("t-stall", 1)
        self.assertIn("why", r["binding_constraint"])
        self.assertTrue(r["binding_constraint"]["why"])

    def test_the_binding_constraint_names_a_real_axis(self):
        P.ensure_incumbent()
        bc = P.binding_constraint()
        self.assertIn(bc["axis"], set(L.WEIGHTS) | {"robustness"})

    def test_performance_reports_what_the_run_actually_cost(self):
        P.ensure_incumbent()
        r = P.run_search("t-perf", 3)
        perf = r["performance"]
        self.assertEqual(perf["proposals"], 3)
        self.assertEqual(perf["simulations"], 3 * (1 + len(L.SCENARIOS)))
        self.assertGreater(perf["seconds_total"], 0)
        self.assertLessEqual(perf["adopted"], perf["proposals"])
        self.assertAlmostEqual(perf["adoption_rate"], perf["adopted"] / 3, places=3)

    def test_lessons_are_written_from_measured_numbers_and_read_back(self):
        P.ensure_incumbent()
        P.evaluate_and_score("t-lesson", L.build_policy(L.textbook_knobs()))
        P._learn(L.simulate(L.order_everything(), L.HOLDOUT))
        lessons = P.lessons_for_prompt()
        self.assertTrue(lessons)
        self.assertIn(lessons.splitlines()[0].lstrip("- ")[:20],
                      P.prompt_for(L.incumbent()))


class ConsoleHelpers(unittest.TestCase):
    """The pure functions behind the page — no socket needed."""

    def test_a_visitor_gets_a_stable_handle(self):
        a = LC.guest_id("1.2.3.4", "", "Firefox")
        self.assertEqual(a, LC.guest_id("1.2.3.4", "", "Firefox"))
        self.assertTrue(a.startswith("guest-"))

    def test_different_visitors_get_different_handles(self):
        self.assertNotEqual(LC.guest_id("1.2.3.4", "", "Firefox"),
                            LC.guest_id("5.6.7.8", "", "Firefox"))
        self.assertNotEqual(LC.guest_id("1.2.3.4", "", "Firefox"),
                            LC.guest_id("1.2.3.4", "", "Safari"))

    def test_a_proxy_header_identifies_the_real_visitor(self):
        self.assertEqual(LC.guest_id("10.0.0.1", "9.9.9.9, 10.0.0.1", "Firefox"),
                         LC.guest_id("9.9.9.9", "", "Firefox"))

    def test_the_mode_follows_the_token(self):
        self.assertEqual(LC.mode(), "guest")
        os.environ["CONSOLE_TOKEN"] = "x"
        try:
            self.assertEqual(LC.mode(), "operator")
        finally:
            os.environ.pop("CONSOLE_TOKEN")

    def test_budget_states_run_green_amber_red(self):
        self.assertEqual(LC._budget_state(10, 100), "good")
        self.assertEqual(LC._budget_state(90, 100), "warn")
        self.assertEqual(LC._budget_state(110, 100), "crit")

    def test_service_state_is_derived_from_the_mandate(self):
        self.assertEqual(LC._service_state(L.TARGET_FILL), "good")
        self.assertEqual(LC._service_state(L.TARGET_FILL * 0.95), "warn")
        self.assertEqual(LC._service_state(L.TARGET_FILL * 0.5), "crit")

    def test_scenario_state_is_derived_from_the_mandate(self):
        self.assertEqual(LC._scenario_state({"mandate_met": True, "fill_rate": 1.0}), "good")
        self.assertEqual(LC._scenario_state({"mandate_met": False, "fill_rate": 0.5}), "crit")

    def test_a_degraded_scenario_is_amber_not_red(self):
        """Every disruption puts fill below target — if that alone painted the meter
        red, all four scenarios would be red on a plan that is perfectly healthy, and
        the reader would learn nothing from any of them."""
        degraded = {"mandate_met": False, "fill_rate": L.TARGET_FILL * 0.92}
        self.assertEqual(LC._scenario_state(degraded), "warn")

    def test_config_carries_everything_the_page_would_otherwise_invent(self):
        cfg = LC.config()
        for k in ("nodes", "currency", "poll_ms", "default_rounds", "max_rounds",
                  "max_order_units", "mode", "mandate", "train", "holdout"):
            self.assertIn(k, cfg)
        self.assertEqual([n["key"] for n in cfg["nodes"]], list(L.NODES))

    def test_tiles_are_numbers_against_targets(self):
        _fresh_world()
        n = P.ensure_incumbent()["verdict"]["nominal"]
        tiles = LC._tiles(n)
        self.assertEqual(len(tiles), 4)
        for t in tiles:
            self.assertIn(t["state"], ("good", "warn", "crit"))
            self.assertLessEqual(t["pct"], 100.0)
            self.assertTrue(t["target"])


class ConsoleHTTP(unittest.TestCase):
    """The wire: every route, every refusal."""

    @classmethod
    def setUpClass(cls):
        _fresh_world()
        P.ensure_incumbent()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), LC.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path, ua="tests"):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()

    def post(self, path, body, ua="tests", token=None):
        h = {"Content-Type": "application/json", "User-Agent": ua}
        if token:
            h["X-Console-Token"] = token
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_the_page_is_served(self):
        status, page = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Phoenix Logistics", page)

    def test_the_page_contains_no_data_of_its_own(self):
        """Everything on the page must arrive from the API. If a SKU name is baked
        into the HTML, the page has started lying about what it is showing."""
        _, page = self.get("/")
        for sku in L.SKUS:
            self.assertNotIn(sku, page, f"{sku} is hardcoded in the page")
        for vendor in {s["vendor"] for s in L.SKUS.values()}:
            self.assertNotIn(vendor, page, f"{vendor} is hardcoded in the page")

    def test_the_snapshot_is_complete(self):
        status, raw = self.get("/api/logistics")
        snap = json.loads(raw)
        self.assertEqual(status, 200)
        for k in ("you", "watchers", "config", "score", "tiles", "scenarios",
                  "network", "gate", "decided", "leaderboard", "log"):
            self.assertIn(k, snap)
        self.assertEqual(len(snap["network"]), len(L.SKUS))

    def test_the_visitor_is_identified_and_stable(self):
        a = json.loads(self.get("/api/logistics", ua="alpha")[1])["you"]
        b = json.loads(self.get("/api/logistics", ua="beta")[1])["you"]
        self.assertEqual(a, json.loads(self.get("/api/logistics", ua="alpha")[1])["you"])
        self.assertNotEqual(a, b)

    def test_config_is_served_on_its_own(self):
        status, raw = self.get("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["currency"], L.CURRENCY)

    def test_unknown_routes_are_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/nope")
        self.assertEqual(e.exception.code, 404)

    def test_drafting_parks_an_order_signed_by_the_visitor(self):
        status, d = self.post("/api/propose", {"sku": "FRESH-01"}, ua="drafter")
        self.assertEqual(status, 200)
        self.assertEqual(d["commitment"]["status"], "pending")
        self.assertTrue(d["commitment"]["agent"].startswith("guest-"))
        self.assertIn(d["commitment"]["id"], [g["id"] for g in d["state"]["gate"]])

    def test_a_quantity_beyond_the_house_limit_is_refused(self):
        status, d = self.post("/api/propose",
                              {"sku": "FRESH-01", "qty": LC.MAX_ORDER_UNITS + 1})
        self.assertEqual(status, 400)
        self.assertIn("qty", d["error"])

    def test_a_non_numeric_quantity_is_refused(self):
        status, _ = self.post("/api/propose", {"sku": "FRESH-01", "qty": "many"})
        self.assertEqual(status, 400)

    def test_an_unknown_sku_is_refused(self):
        status, d = self.post("/api/propose", {"sku": "NOPE"})
        self.assertEqual(status, 400)
        self.assertIn("unknown sku", d["error"])

    def test_an_unknown_node_falls_back_rather_than_crashing(self):
        status, d = self.post("/api/propose", {"sku": "FRESH-01", "node": "moon"})
        self.assertEqual(status, 200)
        self.assertEqual(d["commitment"]["node"], L.NODES[0])

    def test_the_gate_resolves_once_even_under_two_visitors(self):
        _, made = self.post("/api/propose", {"sku": "STAPLE-02"}, ua="drafter")
        cid = made["commitment"]["id"]
        first = self.post("/api/decide", {"id": cid, "decision": "approve"}, ua="alpha")
        second = self.post("/api/decide", {"id": cid, "decision": "reject"}, ua="beta")
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 409)
        self.assertFalse(second[1]["ok"])

    def test_a_decision_is_signed_and_annotated(self):
        _, made = self.post("/api/propose", {"sku": "LUX-03"}, ua="drafter")
        cid = made["commitment"]["id"]
        _, out = self.post("/api/decide",
                           {"id": cid, "decision": "approve", "why": "covered by contract"},
                           ua="signer")
        row = next(d for d in out["state"]["decided"] if d["id"] == cid)
        self.assertTrue(row["decided_by"].startswith("guest-"))
        self.assertEqual(row["why"], "covered by contract")

    def test_a_malformed_decision_is_refused(self):
        status, _ = self.post("/api/decide", {"id": 1, "decision": "perhaps"})
        self.assertEqual(status, 400)

    def test_placing_an_order_always_refuses(self):
        status, d = self.post("/api/place", {"id": 1})
        self.assertEqual(status, 403)
        self.assertFalse(d["ok"])
        self.assertIn("REFUSED", d["message"])

    def test_planning_runs_and_reports_its_own_performance(self):
        status, d = self.post("/api/plan", {"rounds": 2}, ua="planner-1")
        self.assertEqual(status, 200)
        self.assertEqual(d["result"]["performance"]["proposals"], 2)
        self.assertIn("binding_constraint", d["result"])

    def test_planning_again_too_soon_is_refused_not_queued(self):
        self.post("/api/plan", {"rounds": 1}, ua="planner-2")
        status, d = self.post("/api/plan", {"rounds": 1}, ua="planner-2")
        self.assertEqual(status, 429)
        self.assertIn("shared", d["error"])

    def test_rounds_are_bounded(self):
        for bad in (0, LC.MAX_ROUNDS + 1, "lots"):
            status, _ = self.post("/api/plan", {"rounds": bad}, ua=f"bounds-{bad}")
            self.assertEqual(status, 400, bad)

    def test_only_one_planning_run_happens_at_a_time(self):
        LC._plan_lock.acquire()
        try:
            status, d = self.post("/api/plan", {"rounds": 1}, ua="planner-3")
        finally:
            LC._plan_lock.release()
        self.assertEqual(status, 409)
        self.assertIn("someone else is planning", d["error"])

    def test_operator_mode_locks_every_mutation_and_nothing_else(self):
        os.environ["CONSOLE_TOKEN"] = "sesame"
        try:
            self.assertEqual(self.post("/api/propose", {"sku": "FRESH-01"})[0], 401)
            self.assertEqual(self.get("/api/logistics")[0], 200)
            self.assertEqual(
                self.post("/api/propose", {"sku": "FRESH-01"}, token="sesame")[0], 200)
        finally:
            os.environ.pop("CONSOLE_TOKEN")


if __name__ == "__main__":
    unittest.main(verbosity=2)

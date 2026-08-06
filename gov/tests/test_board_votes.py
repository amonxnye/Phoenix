"""Unit tests for board.py — three seats, disjoint evidence, and the abstention rule.

Article VIII says three seats must be three signals, not one `if` wearing three hats;
Article X.3 says a seat that cannot answer abstains and is never filled by another.
These tests cover the whole ballot matrix, including the cases where a seat has no
evidence at all — which must read as *unknown*, never as consent.
"""

import os

import _env
import board
import models as M


def ctx(**over):
    base = {"affordable": True, "within_budget": True, "spent": 1_000, "cap": 100_000,
            "burn_per_turn": 100, "progress_delta": 5, "understaffed": False,
            "offline": []}
    base.update(over)
    return base


class TestLedger(_env.Base):

    def test_ledger_votes_on_affordability_alone(self):
        self.assertTrue(board.vote("p", ctx(affordable=True))["ballots"]["Ledger"])
        self.assertFalse(board.vote("p", ctx(affordable=False))["ballots"]["Ledger"])

    def test_ledger_rationale_carries_live_numbers(self):
        why = board.vote("p", ctx(spent=2_500, cap=10_000))["reasons"]["Ledger"]
        self.assertIn("2,500", why)
        self.assertIn("25%", why)


class TestPrudence(_env.Base):

    def test_no_burn_telemetry_is_unknown_not_yes(self):
        v = board.vote("p", ctx(burn_per_turn=None))
        self.assertIsNone(v["ballots"]["Prudence"])
        self.assertIn("unknown", v["reasons"]["Prudence"])

    def test_short_runway_is_a_no(self):
        v = board.vote("p", ctx(spent=99_000, cap=100_000, burn_per_turn=1_000))
        self.assertFalse(v["ballots"]["Prudence"])
        self.assertIn("runway", v["reasons"]["Prudence"])

    def test_blown_side_effect_budget_is_a_no_regardless_of_runway(self):
        v = board.vote("p", ctx(within_budget=False))
        self.assertFalse(v["ballots"]["Prudence"])
        self.assertIn("side-effect", v["reasons"]["Prudence"])

    def test_long_runway_is_a_yes(self):
        self.assertTrue(board.vote("p", ctx())["ballots"]["Prudence"])

    def test_zero_burn_does_not_divide_by_zero(self):
        self.assertTrue(board.vote("p", ctx(burn_per_turn=0))["ballots"]["Prudence"])


class TestGrowth(_env.Base):

    def test_momentum_is_a_yes(self):
        self.assertTrue(board.vote("p", ctx(progress_delta=3))["ballots"]["Growth"])

    def test_flat_progress_with_a_full_fleet_is_a_no(self):
        v = board.vote("p", ctx(progress_delta=0, understaffed=False))
        self.assertFalse(v["ballots"]["Growth"])

    def test_flat_progress_but_understaffed_is_a_yes(self):
        v = board.vote("p", ctx(progress_delta=0, understaffed=True))
        self.assertTrue(v["ballots"]["Growth"])
        self.assertIn("blocker", v["reasons"]["Growth"])

    def test_no_telemetry_and_a_full_fleet_is_unknown(self):
        v = board.vote("p", ctx(progress_delta=None, understaffed=False))
        self.assertIsNone(v["ballots"]["Growth"])


class TestQuorum(_env.Base):

    def test_two_yes_votes_carry(self):
        v = board.vote("p", ctx())
        self.assertEqual(v["yes"], 3)
        self.assertTrue(v["approved"])

    def test_one_yes_does_not(self):
        v = board.vote("p", ctx(affordable=False, within_budget=False,
                                progress_delta=0, understaffed=False))
        self.assertEqual(v["yes"], 0)
        self.assertFalse(v["approved"])

    def test_unknown_never_approves(self):
        # two abstentions and one yes must not reach quorum
        v = board.vote("p", ctx(burn_per_turn=None, progress_delta=None,
                                understaffed=False, affordable=True))
        self.assertEqual(v["yes"], 1)
        self.assertFalse(v["approved"])


class TestAbstention(_env.Base):

    def test_an_offline_seat_votes_unknown_and_says_why(self):
        v = board.vote("p", ctx(offline=["Prudence"]))
        self.assertIsNone(v["ballots"]["Prudence"])
        self.assertIn("abstains", v["reasons"]["Prudence"])
        self.assertIn("not filled by another", v["reasons"]["Prudence"])
        self.assertEqual(v["abstained"], ["Prudence"])

    def test_the_other_seats_still_vote(self):
        v = board.vote("p", ctx(offline=["Prudence"]))
        self.assertTrue(v["ballots"]["Growth"])
        self.assertTrue(v["ballots"]["Ledger"])
        self.assertTrue(v["approved"])                      # 2/3 still carries

    def test_a_wholly_offline_board_approves_nothing(self):
        v = board.vote("p", ctx(offline=list(board.GOVERNORS)))
        self.assertEqual(v["yes"], 0)
        self.assertFalse(v["approved"])

    def test_an_offline_seat_cannot_be_forced_to_a_yes(self):
        v = board.vote("p", ctx(offline=["Ledger"], affordable=True))
        self.assertIsNone(v["ballots"]["Ledger"])

    def test_unknown_names_in_the_offline_list_are_ignored(self):
        v = board.vote("p", ctx(offline=["Emperor"]))
        self.assertEqual(v["abstained"], [])

    def test_the_switchboard_is_consulted_when_the_caller_says_nothing(self):
        # the rule must hold at call sites that predate it, so vote() asks models
        # itself when the context carries no `offline` key at all
        os.environ["MODEL_ROLE_GROWTH"] = "openai"          # registered, no key
        c = ctx()
        del c["offline"]
        v = board.vote("p", c)
        self.assertIsNone(v["ballots"]["Growth"])
        self.assertEqual(v["abstained"], ["Growth"])

    def test_an_explicit_empty_list_means_nobody_is_offline(self):
        M.note_call("Growth", "labA", "model-a", 0.0, False, "boom")
        v = board.vote("p", ctx(offline=[]))
        self.assertTrue(v["ballots"]["Growth"])


class TestDegeneracy(_env.Base):

    def setUp(self):
        super().setUp()
        for h in board._history.values():
            h.clear()

    def test_a_constant_seat_is_flagged_after_the_window(self):
        for _ in range(board.DEGENERACY_WINDOW):
            board.vote("p", ctx())                          # identical every time
        self.assertIn("Prudence", board.degenerate_members())

    def test_a_varying_seat_is_not_flagged(self):
        for i in range(board.DEGENERACY_WINDOW):
            board.vote("p", ctx(affordable=bool(i % 2)))
        self.assertNotIn("Ledger", board.degenerate_members())

    def test_nothing_is_flagged_before_the_window_fills(self):
        for _ in range(board.DEGENERACY_WINDOW - 1):
            board.vote("p", ctx())
        self.assertEqual(board.degenerate_members(), [])


class TestProposals(_env.Base):

    def test_a_met_vision_with_budget_to_spare_proposes_a_bolder_one(self):
        sc = {"goal_met": True, "within_budget": True, "side_effects": 0}
        p = board.propose_vision(sc, 0.2, "feudal")
        self.assertTrue(p is None or p["action"] == "Aim higher")

    def test_a_blown_budget_proposes_consolidation(self):
        sc = {"goal_met": False, "within_budget": False, "side_effects": 9}
        p = board.propose_vision(sc, 0.95, "castle")
        self.assertEqual(p["vision"], "consolidate")

    def test_a_healthy_run_proposes_nothing(self):
        sc = {"goal_met": False, "within_budget": True, "side_effects": 0}
        self.assertIsNone(board.propose_vision(sc, 0.5, "feudal"))

    def test_the_cap_is_proposed_up_when_spend_is_near_the_ceiling(self):
        p = board.propose_cap(0.95, {"goal_met": False, "side_effects": 0}, 100_000)
        self.assertEqual(p["action"], "Raise the cap")
        self.assertGreater(p["cap"], 100_000)

    def test_the_cap_is_proposed_down_when_headroom_is_wasted(self):
        p = board.propose_cap(0.1, {"goal_met": False, "side_effects": 0}, 100_000)
        self.assertEqual(p["action"], "Tighten the cap")
        self.assertLess(p["cap"], 100_000)

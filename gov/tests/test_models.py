"""Unit tests for models.py — the switchboard behind the one model seam.

Covers every public function. The rules being protected here are constitutional, not
cosmetic: a broken seat must not be papered over with another model, a price nobody
supplied must not be invented, and a ceiling that cannot be measured must not be
reported as enforced.
"""

import json
import os
import time

import _env
import models as M


class TestRegistry(_env.Base):

    def test_builtins_are_places_not_minds(self):
        # A base URL is a stable fact about a protocol; a model id is not. Shipping a
        # guessed model name would put a wrong-but-plausible value in every scorecard.
        for name, entry in M.BUILTIN.items():
            self.assertTrue(entry["base_url"].startswith("https://"), name)
            self.assertEqual(entry["model"], "", f"{name} ships a guessed model id")

    def test_registry_returns_builtins_by_default(self):
        self.assertEqual(set(M.registry()), set(M.BUILTIN))

    def test_env_registry_adds_a_lab_without_a_code_change(self):
        self.register(myLab={"base_url": "https://my.invalid/v1", "key": "k",
                             "model": "m-1", "kind": "openai"})
        self.assertIn("myLab", M.registry())
        self.assertEqual(M.registry()["myLab"]["model"], "m-1")

    def test_env_registry_can_override_a_builtin_field(self):
        self.register(openai={"model": "pinned-model"})
        self.assertEqual(M.registry()["openai"]["model"], "pinned-model")
        self.assertEqual(M.registry()["openai"]["base_url"],
                         M.BUILTIN["openai"]["base_url"])   # untouched fields survive

    def test_malformed_registry_json_is_ignored_not_fatal(self):
        os.environ["MODEL_REGISTRY"] = "{not json"
        self.assertEqual(set(M.registry()), set(M.BUILTIN))

    def test_catalog_reports_usability_but_never_keys(self):
        self.register()
        blob = json.dumps(M.catalog())
        self.assertNotIn("k-a", blob)
        self.assertNotIn("k-b", blob)
        entry = next(p for p in M.catalog() if p["name"] == "labA")
        self.assertTrue(entry["keyed"])
        self.assertFalse(next(p for p in M.catalog() if p["name"] == "openai")["keyed"])


class TestRoleResolution(_env.Base):

    def test_unset_roles_resolve_to_nothing(self):
        for role in M.ROLES:
            provider, reason = M.resolve_detail(role)
            self.assertIsNone(provider)
            self.assertEqual(reason, M.UNSET)
        self.assertFalse(M.active())

    def test_env_role_beats_stored_role(self):
        self.register()
        M.assign("governor", "labB")                       # stored by the console
        os.environ["MODEL_ROLE_GOVERNOR"] = "labA"         # pinned by the deployment
        self.assertEqual(M.resolve("governor")["name"], "labA")

    def test_provider_colon_model_pins_the_model(self):
        self.register()
        os.environ["MODEL_ROLE_GROWTH"] = "labB:model-b-pro"
        self.assertEqual(M.resolve("Growth")["model"], "model-b-pro")

    def test_default_role_backs_every_unset_role(self):
        self.register()
        os.environ["MODEL_ROLE_DEFAULT"] = "labA"
        self.assertEqual(M.resolve("Ledger")["name"], "labA")

    def test_missing_key_is_a_fault_not_an_unset_role(self):
        os.environ["MODEL_ROLE_PRUDENCE"] = "openai"
        self.assertEqual(M.resolve_detail("Prudence")[1], M.NO_KEY)
        self.assertEqual(M.misconfigured("Prudence"), M.NO_KEY)

    def test_missing_model_is_a_fault(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        os.environ["MODEL_ROLE_PRUDENCE"] = "openai"
        self.assertEqual(M.resolve_detail("Prudence")[1], M.NO_MODEL)
        self.assertIn("never guesses", M.explain(M.NO_MODEL, "Prudence"))

    def test_unknown_provider_is_a_fault(self):
        os.environ["MODEL_ROLE_PRUDENCE"] = "ghostlab"
        self.assertEqual(M.resolve_detail("Prudence")[1], M.UNKNOWN_PROVIDER)

    def test_unset_is_never_reported_as_misconfigured(self):
        self.assertEqual(M.misconfigured("Growth"), "")

    def test_seat_faults_lists_only_broken_seats(self):
        self.register()
        os.environ["MODEL_ROLE_GOVERNOR"] = "labA"          # fine
        os.environ["MODEL_ROLE_LEDGER"] = "openai"          # no key
        self.assertEqual(list(M.seat_faults()), ["Ledger"])

    def test_assignments_name_the_model_per_seat(self):
        self.register()
        os.environ["MODEL_ROLE_GOVERNOR"] = "labA"
        self.assertEqual(M.assignments()["governor"], "labA:model-a")
        self.assertEqual(M.assignments()["fleet"], "")


class TestAssignment(_env.Base):

    def test_assign_rejects_unknown_role(self):
        ok, msg = M.assign("emperor", "labA")
        self.assertFalse(ok)
        self.assertIn("unknown role", msg)

    def test_assign_rejects_unregistered_provider(self):
        ok, msg = M.assign("governor", "ghostlab")
        self.assertFalse(ok)
        self.assertIn("unknown provider", msg)

    def test_assign_persists_where_the_console_reads_it(self):
        self.register()
        ok, _ = M.assign("governor", "labA:model-a")
        self.assertTrue(ok)
        import anchor
        self.assertEqual(json.loads(anchor.config_get("models.roles"))["governor"],
                         "labA:model-a")

    def test_empty_spec_clears_a_role(self):
        self.register()
        M.assign("governor", "labA")
        M.assign("governor", "")
        self.assertIsNone(M.resolve("governor"))


class TestTier(_env.Base):

    def test_unrouted_is_ranked(self):
        self.assertEqual(M.tier(), "ranked")

    def test_direct_providers_are_ranked(self):
        self.register()
        os.environ["MODEL_ROLE_GOVERNOR"] = "labA"
        self.assertEqual(M.tier(), "ranked")

    def test_one_router_anywhere_makes_the_run_scouting(self):
        self.register()
        os.environ["OPENROUTER_API_KEY"] = "k"
        os.environ["MODEL_ROLE_GOVERNOR"] = "labA"
        os.environ["MODEL_ROLE_FLEET"] = "openrouter:vendor/model"
        self.assertEqual(M.tier(), "scouting")

    def test_a_broken_seat_makes_the_run_incomplete(self):
        self.register()
        os.environ["MODEL_ROLE_GOVERNOR"] = "labA"
        os.environ["MODEL_ROLE_LEDGER"] = "openai"          # no key
        self.assertEqual(M.tier(), "incomplete")

    def test_router_detection_is_by_url_not_by_trust(self):
        self.assertTrue(M.is_router("https://openrouter.ai/api/v1"))
        self.assertTrue(M.is_router("http://localhost:4000/litellm"))
        self.assertFalse(M.is_router("https://api.openai.com/v1"))


class TestPrices(_env.Base):

    def test_no_prices_ship_with_the_platform(self):
        self.assertEqual(M.SEED_PRICES, {})
        self.assertEqual(M.prices(), {})

    def test_unpriced_model_costs_zero_and_says_so(self):
        self.assertTrue(M.unpriced("anything"))
        self.assertEqual(M.cost_usd("anything", 10**6, 10**6), 0.0)

    def test_loaded_table_prices_input_and_output_separately(self):
        self.prices(**{"model-a": {"in": 2.0, "out": 8.0}})
        self.assertEqual(M.cost_usd("model-a", 1_000_000, 0), 2.0)
        self.assertEqual(M.cost_usd("model-a", 0, 1_000_000), 8.0)
        self.assertEqual(M.cost_usd("model-a", 500_000, 250_000), 1.0 + 2.0)

    def test_longest_prefix_wins(self):
        self.prices(**{"claude": {"in": 1.0, "out": 1.0},
                       "claude-sonnet": {"in": 3.0, "out": 15.0}})
        self.assertEqual(M.cost_usd("claude-sonnet-5-20260101", 1_000_000, 0), 3.0)

    def test_prefix_matching_never_borrows_a_neighbours_price(self):
        self.prices(**{"model-a": {"in": 2.0, "out": 8.0}})
        self.assertTrue(M.unpriced("model-b"))

    def test_price_lookup_is_case_insensitive(self):
        self.prices(**{"Model-A": {"in": 2.0, "out": 8.0}})
        self.assertFalse(M.unpriced("model-a-2026"))


class TestBudget(_env.Base):

    def test_no_ceiling_by_default(self):
        self.assertEqual(M.budget_usd(), 0.0)
        self.assertFalse(M.budget_state()["over"])
        M.check_budget("anything")                          # must not raise

    def test_malformed_ceiling_is_treated_as_no_ceiling(self):
        os.environ["EVAL_BUDGET_USD"] = "five dollars"
        self.assertEqual(M.budget_usd(), 0.0)

    def test_spend_accumulates_in_total_and_per_provider(self):
        M.note_call("governor", "labA", "model-a", 0.25, True)
        M.note_call("fleet", "labB", "model-b", 0.10, True)
        M.note_call("governor", "labA", "model-a", 0.25, True)
        self.assertEqual(M.spent_usd(), 0.60)
        self.assertEqual(M.spent_usd("labA"), 0.50)
        self.assertEqual(M.spent_usd("labB"), 0.10)

    def test_ceiling_breach_is_visible_and_refuses_further_calls(self):
        os.environ["EVAL_BUDGET_USD"] = "1.00"
        self.prices(**{"model-a": {"in": 1.0, "out": 1.0}})
        M.note_call("governor", "labA", "model-a", 0.99, True)
        M.check_budget("model-a")                           # still under
        M.note_call("governor", "labA", "model-a", 0.02, True)
        self.assertTrue(M.budget_state()["over"])
        with self.assertRaises(M.BudgetExceeded):
            M.check_budget("model-a")

    def test_provider_sub_cap_breaches_on_its_own(self):
        os.environ["EVAL_BUDGET_USD"] = "100"
        os.environ["EVAL_PROVIDER_CAP_USD"] = json.dumps({"labA": 0.10})
        self.prices(**{"model-a": {"in": 1.0, "out": 1.0}})
        M.note_call("governor", "labA", "model-a", 0.15, True)
        st = M.budget_state()
        self.assertEqual(st["breached_providers"], ["labA"])
        self.assertTrue(st["over"])

    def test_a_ceiling_over_an_unpriced_model_is_refused_not_faked(self):
        # counting unpriced calls as $0 would let a run sail past its ceiling while
        # the page reported a tidy $0.00
        os.environ["EVAL_BUDGET_USD"] = "5.00"
        with self.assertRaises(M.BudgetExceeded) as cm:
            M.check_budget("unpriced-model")
        self.assertIn("unpriced", str(cm.exception))

    def test_unpriced_model_is_fine_when_no_ceiling_is_set(self):
        M.check_budget("unpriced-model")                    # must not raise

    def test_reset_clears_spend_seats_and_fallbacks(self):
        M.note_call("governor", "labA", "model-a", 1.0, False, "boom")
        M.note_fallback("governor")
        M.reset_run_state()
        self.assertEqual(M.spent_usd(), 0.0)
        self.assertEqual(M.seat_status(), {})
        self.assertEqual(M.fallbacks(), 0)


class TestPreflight(_env.Base):

    def test_no_history_yields_no_estimate_and_a_reason(self):
        pf = M.preflight(100)
        self.assertIsNone(pf["estimate_usd"])
        self.assertIn("no call history", pf["why"])

    def test_explicit_averages_are_honoured(self):
        self.prices(**{"model-a": {"in": 2.0, "out": 8.0}})
        pf = M.preflight(1000, "model-a", 1000, 200)
        self.assertEqual(pf["estimate_usd"], round(2.0 + 1.6, 4))
        self.assertEqual(pf["basis"], "given")

    def test_estimate_over_the_ceiling_is_refused(self):
        os.environ["EVAL_BUDGET_USD"] = "1.00"
        self.prices(**{"model-a": {"in": 2.0, "out": 8.0}})
        self.assertTrue(M.preflight(1000, "model-a", 1000, 200)["refused"])

    def test_estimate_under_the_ceiling_is_allowed(self):
        os.environ["EVAL_BUDGET_USD"] = "100"
        self.prices(**{"model-a": {"in": 2.0, "out": 8.0}})
        self.assertFalse(M.preflight(1000, "model-a", 1000, 200)["refused"])

    def test_unpriced_model_yields_no_estimate_and_refuses_under_a_ceiling(self):
        os.environ["EVAL_BUDGET_USD"] = "5"
        pf = M.preflight(1000, "no-price", 1000, 200)
        self.assertIsNone(pf["estimate_usd"])
        self.assertTrue(pf["refused"])
        self.assertIn("unpriced", pf["why"])

    def test_observed_averages_come_from_logged_calls(self):
        import anchor
        for _ in range(4):
            anchor.model_call_log("https://a.invalid/v1", "model-a", "think", 100,
                                  1000, 200, True, role="governor", cost_usd=0.0)
        self.prices(**{"model-a": {"in": 2.0, "out": 8.0}})
        pf = M.preflight(10, "model-a")
        self.assertIn("observed", pf["basis"])
        self.assertGreaterEqual(pf["samples"], 4)
        self.assertIsNotNone(pf["estimate_usd"])


class TestSeatHealth(_env.Base):

    def test_a_failed_call_takes_a_seat_offline(self):
        M.note_call("Prudence", "labA", "model-a", 0.0, False, "connection refused")
        self.assertEqual(M.offline_seats(), ["Prudence"])
        self.assertIn("Prudence", M.seat_status())

    def test_a_successful_call_brings_it_back(self):
        M.note_call("Prudence", "labA", "model-a", 0.0, False, "boom")
        M.note_call("Prudence", "labA", "model-a", 0.01, True)
        self.assertEqual(M.offline_seats(), [])

    def test_the_offline_window_expires(self):
        M.note_call("Growth", "labB", "model-b", 0.0, False, "boom")
        later = time.time() + M.OFFLINE_WINDOW_S + 1
        self.assertEqual(M.offline_seats(now=later), [])

    def test_a_misconfigured_seat_is_offline_before_it_is_ever_called(self):
        # a chair nobody can sit in is empty whether or not anyone has tried it
        os.environ["MODEL_ROLE_LEDGER"] = "openai"          # no key
        self.assertEqual(M.offline_seats(), ["Ledger"])

    def test_only_board_seats_are_reported_as_offline(self):
        M.note_call("worker", "labA", "model-a", 0.0, False, "boom")
        self.assertEqual(M.offline_seats(), [])

    def test_fallbacks_are_counted_per_role_and_in_total(self):
        M.note_fallback("governor")
        M.note_fallback("governor")
        M.note_fallback("fleet")
        self.assertEqual(M.fallbacks(), 3)
        self.assertEqual(M.fallbacks("governor"), 2)
        self.assertEqual(M.fallbacks("fleet"), 1)


class TestActorMapping(_env.Base):

    def test_board_actors_map_to_their_seat(self):
        self.register()
        os.environ["MODEL_ROLE_PRUDENCE"] = "labA"
        self.assertEqual(M.model_for_actor("Prudence"), "model-a")

    def test_fleet_actors_map_to_the_fleet_seat(self):
        self.register()
        os.environ["MODEL_ROLE_FLEET"] = "labB"
        self.assertEqual(M.model_for_actor("vil-03"), "model-b")

    def test_worker_actors_map_to_the_worker_seat(self):
        self.register()
        os.environ["MODEL_ROLE_WORKER"] = "labA"
        self.assertEqual(M.model_for_actor("dev-01"), "model-a")

    def test_unknown_actors_fall_to_the_default_brain(self):
        self.assertEqual(M.model_for_actor("something-else"), "rule-based")

    def test_model_for_role_reports_the_default_brain_when_unrouted(self):
        self.assertEqual(M.model_for_role("governor"), "rule-based")

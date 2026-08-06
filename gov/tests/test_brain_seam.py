"""Unit tests for brain.py — the ONE seam where a model plugs in.

No network: the transport is replaced, so these test the seam's behaviour (routing,
accounting, refusal, abstention) rather than any provider's availability.

The load-bearing test here is test_broken_seat_never_substitutes. Everything else in
the platform's attribution — scorecards, per-model hit rates, the leaderboard — is only
true if a seat that cannot answer stays silent instead of being covered by another model.
"""

import os

import _env
import anchor
import brain
import models as M


class FakeTransport:
    """Stands in for a provider. Records what it was asked, returns what it was told."""

    def __init__(self, reply="ok", usage=None, raises=None):
        self.reply, self.usage, self.raises = reply, usage or {}, raises
        self.calls = []

    def __call__(self, p, messages, max_tokens, temperature):
        self.calls.append({"model": p["model"], "base_url": p["base_url"],
                           "messages": messages, "max_tokens": max_tokens,
                           "temperature": temperature})
        if self.raises:
            raise self.raises
        return self.reply, self.usage


class SeamBase(_env.Base):

    def setUp(self):
        super().setUp()
        self._real = brain._anthropic_chat
        self.transport = FakeTransport(usage={"input_tokens": 1000, "output_tokens": 500})
        brain._anthropic_chat = self.transport
        # a fake lab that routes through the anthropic branch, i.e. our transport
        self.register(labX={"kind": "anthropic", "base_url": "https://x.invalid",
                            "key": "k-x", "model": "model-x", "tier": "ranked"})

    def tearDown(self):
        brain._anthropic_chat = self._real
        super().tearDown()

    def default_brain(self, model="default-model"):
        os.environ["BRAIN_BASE_URL"] = "https://anthropic.invalid"
        os.environ["BRAIN_API_KEY"] = "k-default"
        os.environ["BRAIN_MODEL"] = model


class TestProviderSelection(SeamBase):

    def test_no_configuration_means_rule_based(self):
        self.assertIsNone(brain.provider())
        self.assertFalse(brain.available())
        self.assertEqual(brain.brain_name(), "rule-based")

    def test_default_brain_serves_every_unset_role(self):
        self.default_brain()
        for role in M.ROLES:
            self.assertEqual(brain.provider_for(role)["model"], "default-model")

    def test_a_routed_role_uses_its_own_provider(self):
        self.default_brain()
        os.environ["MODEL_ROLE_PRUDENCE"] = "labX"
        self.assertEqual(brain.provider_for("Prudence")["model"], "model-x")
        self.assertEqual(brain.provider_for("Growth")["model"], "default-model")

    def test_broken_seat_never_substitutes(self):
        # THE rule (Article X.3). With a default brain available, a seat assigned a
        # provider it cannot use must NOT quietly be answered by that default brain.
        self.default_brain()
        os.environ["MODEL_ROLE_PRUDENCE"] = "openai"        # no key
        with self.assertRaises(M.SeatMisconfigured):
            brain.provider_for("Prudence")
        self.assertFalse(brain.available("Prudence"))

    def test_available_answers_false_instead_of_raising(self):
        os.environ["MODEL_ROLE_PRUDENCE"] = "ghostlab"
        self.assertFalse(brain.available("Prudence"))       # must not raise

    def test_brains_by_role_names_every_seat(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        self.assertEqual(brain.brains_by_role()["governor"], "labX:model-x")


class TestChatAccounting(SeamBase):

    def test_a_call_returns_the_reply_and_logs_role_and_model(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        self.transport.reply = "a short directive"
        before = anchor.model_calls_stats()["calls"]
        out = brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "governor")
        self.assertEqual(out, "a short directive")
        self.assertEqual(anchor.model_calls_stats()["calls"], before + 1)
        row = next(r for r in anchor.model_calls_by_model() if r["model"] == "model-x")
        self.assertIn("governor", row["roles"])

    def test_purpose_maps_to_a_role_when_none_is_given(self):
        os.environ["MODEL_ROLE_RETROSPECTIVE"] = "labX"
        brain._chat([{"role": "user", "content": "hi"}], 100, 0.4, "retrospective")
        self.assertEqual(self.transport.calls[-1]["model"], "model-x")

    def test_tokens_and_dollars_are_recorded_at_log_time(self):
        self.prices(**{"model-x": {"in": 3.0, "out": 15.0}})
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "governor")
        row = next(r for r in anchor.model_calls_by_model() if r["model"] == "model-x")
        # 1000 in @ $3/1M + 500 out @ $15/1M
        self.assertAlmostEqual(row["cost_usd"], 0.003 + 0.0075, places=6)

    def test_a_failed_call_is_logged_as_an_error_and_re_raised(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        brain._anthropic_chat = FakeTransport(raises=RuntimeError("read timeout"))
        with self.assertRaises(RuntimeError):
            brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "governor")
        row = next(r for r in anchor.model_calls_by_model() if r["model"] == "model-x")
        self.assertGreaterEqual(row["errors"], 1)
        self.assertEqual(M.offline_seats(), [])             # governor is not a board seat

    def test_a_failed_board_seat_goes_offline(self):
        os.environ["MODEL_ROLE_PRUDENCE"] = "labX"
        brain._anthropic_chat = FakeTransport(raises=RuntimeError("read timeout"))
        with self.assertRaises(RuntimeError):
            brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "Prudence")
        self.assertEqual(M.offline_seats(), ["Prudence"])

    def test_a_misconfigured_seat_records_a_fault_and_calls_nothing(self):
        os.environ["MODEL_ROLE_LEDGER"] = "openai"          # no key
        with self.assertRaises(M.SeatMisconfigured):
            brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "Ledger")
        self.assertEqual(self.transport.calls, [])          # nothing was contacted
        self.assertTrue(any("cannot be used" in e for e in anchor.event_log(20)))


class TestBudgetAtTheSeam(SeamBase):

    def test_the_ceiling_stops_a_call_before_it_is_made(self):
        self.prices(**{"model-x": {"in": 1.0, "out": 1.0}})
        os.environ["EVAL_BUDGET_USD"] = "0.01"
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        M.note_call("governor", "labX", "model-x", 0.02, True)   # already over
        with self.assertRaises(M.BudgetExceeded):
            brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "governor")
        self.assertEqual(self.transport.calls, [])

    def test_a_ceiling_over_an_unpriced_model_refuses_rather_than_pretending(self):
        os.environ["EVAL_BUDGET_USD"] = "5.00"              # no price table loaded
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        with self.assertRaises(M.BudgetExceeded) as cm:
            brain._chat([{"role": "user", "content": "hi"}], 100, 0.5, "think", "governor")
        self.assertIn("unpriced", str(cm.exception))
        self.assertEqual(self.transport.calls, [])


class TestPublicHelpers(SeamBase):

    def test_think_returns_none_and_counts_a_fallback_on_failure(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        brain._anthropic_chat = FakeTransport(raises=RuntimeError("boom"))
        self.assertIsNone(brain.think("the Chief Governor", "situation", "task"))
        self.assertEqual(M.fallbacks("governor"), 1)

    def test_think_returns_the_models_words_when_it_answers(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        self.transport.reply = "Press toward the vision."
        self.assertEqual(brain.think("gov", "sit", "task"), "Press toward the vision.")
        self.assertEqual(M.fallbacks(), 0)

    def test_think_routes_to_the_named_seat(self):
        os.environ["MODEL_ROLE_GROWTH"] = "labX"
        brain.think("Growth", "sit", "task", role="Growth")
        self.assertEqual(self.transport.calls[-1]["model"], "model-x")

    def test_no_model_means_no_call_and_no_fallback_counted(self):
        # with nothing configured the rules were always going to do the work; that is
        # not a model failing, so it must not be counted against one
        self.assertIsNone(brain.think("gov", "sit", "task"))
        self.assertEqual(M.fallbacks(), 0)

    def test_reply_falls_back_to_none_on_failure(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        brain._anthropic_chat = FakeTransport(raises=RuntimeError("boom"))
        self.assertIsNone(brain.reply("chief", "sit", "hello"))
        self.assertEqual(M.fallbacks("governor"), 1)

    def test_retrospective_falls_back_to_rule_based_lessons(self):
        os.environ["MODEL_ROLE_RETROSPECTIVE"] = "labX"
        brain._anthropic_chat = FakeTransport(raises=RuntimeError("boom"))
        lessons = brain.retrospective({"waste": 3, "progress": 40}, [])
        self.assertTrue(lessons)                            # learning never stops
        self.assertEqual(M.fallbacks("retrospective"), 1)

    def test_retrospective_uses_the_model_when_it_answers(self):
        os.environ["MODEL_ROLE_RETROSPECTIVE"] = "labX"
        self.transport.reply = "Build the mill first.\nRetire idle agents sooner."
        self.assertEqual(brain.retrospective({"progress": 40}, []),
                         ["Build the mill first.", "Retire idle agents sooner."])

    def test_choose_resource_falls_back_to_the_rule(self):
        os.environ["MODEL_ROLE_FLEET"] = "labX"
        brain._anthropic_chat = FakeTransport(raises=RuntimeError("boom"))
        self.assertEqual(brain.choose_resource(1, {"food": 1, "wood": 1, "gold": 1}),
                         brain.RESOURCES[1])
        self.assertEqual(M.fallbacks("fleet"), 1)

    def test_choose_resource_accepts_prose_around_the_answer(self):
        os.environ["MODEL_ROLE_FLEET"] = "labX"
        self.transport.reply = "I would gather wood this round."
        self.assertEqual(brain.choose_resource(0, {"food": 1, "wood": 1, "gold": 1}), "wood")

    def test_should_advance_never_proposes_what_cannot_be_paid(self):
        os.environ["MODEL_ROLE_FLEET"] = "labX"
        self.transport.reply = "yes"
        self.assertFalse(brain.should_advance({"food": 10, "gold": 10},
                                              {"food": 500, "gold": 300}))
        self.assertEqual(self.transport.calls, [])          # not even asked

    def test_should_advance_asks_when_it_is_affordable(self):
        os.environ["MODEL_ROLE_FLEET"] = "labX"
        self.transport.reply = "yes"
        self.assertTrue(brain.should_advance({"food": 900, "gold": 900},
                                             {"food": 500, "gold": 300}))

    def test_propose_development_rejects_output_outside_the_vocabulary(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        self.transport.reply = '{"name":"x","kind":"mind_control","value":9}'
        self.assertIsNone(brain.propose_development("sit", [], []))

    def test_propose_development_accepts_valid_json_in_a_fence(self):
        os.environ["MODEL_ROLE_GOVERNOR"] = "labX"
        self.transport.reply = ('```json\n{"name":"water_mill","cost":{"food":1,"wood":1,'
                                '"gold":1},"kind":"yield_pct","value":10,"resource":"food",'
                                '"rank":2,"why":"because"}\n```')
        got = brain.propose_development("sit", [], [])
        self.assertEqual(got["name"], "water_mill")

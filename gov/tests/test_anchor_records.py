"""Unit tests for the anchor's permanent record — the queries the Arena reports from.

Article VII says nothing runs unseen; Article X.2 adds that a decision without its
model is an incomplete record. These tests hold both to their word, and pin down the
one judgement call in the whole reporting path: how a free-text outcome becomes a hit,
a miss, or neither.
"""

import os

import _env
import anchor as A


class TestModelCallLog(_env.Base):

    def test_empty_stats_on_a_fresh_anchor(self):
        st = A.model_calls_stats()
        self.assertEqual((st["calls"], st["errors"], st["cost_usd"]), (0, 0, 0.0))
        self.assertEqual(A.model_calls_by_model(), [])

    def test_a_call_records_role_model_tokens_and_dollars(self):
        A.model_call_log("https://a.invalid/v1", "model-a", "think", 120,
                         900, 100, True, role="Prudence", cost_usd=0.0021)
        row = A.model_calls_by_model()[0]
        self.assertEqual(row["model"], "model-a")
        self.assertEqual(row["roles"], ["Prudence"])
        self.assertEqual((row["prompt_tokens"], row["completion_tokens"]), (900, 100))
        self.assertAlmostEqual(row["cost_usd"], 0.0021)

    def test_errors_are_counted_and_excluded_from_latency(self):
        A.model_call_log("u", "m", "think", 100, 10, 10, True, role="r")
        A.model_call_log("u", "m", "think", 9000, 0, 0, False, "timeout", role="r")
        row = A.model_calls_by_model()[0]
        self.assertEqual((row["calls"], row["errors"], row["error_rate"]), (2, 1, 50))
        # a failed call's latency is the timeout, not the model's speed
        self.assertEqual(row["p95_ms"], 100)

    def test_percentiles_come_from_the_distribution_not_the_mean(self):
        for ms in [10, 20, 30, 40, 1000]:
            A.model_call_log("u", "m", "think", ms, 1, 1, True, role="r")
        row = A.model_calls_by_model()[0]
        self.assertEqual(row["p50_ms"], 30)
        self.assertEqual(row["p95_ms"], 1000)

    def test_models_are_aggregated_separately(self):
        A.model_call_log("u", "m1", "think", 10, 1, 1, True, role="governor")
        A.model_call_log("u", "m2", "think", 10, 1, 1, True, role="fleet")
        self.assertEqual({r["model"] for r in A.model_calls_by_model()}, {"m1", "m2"})

    def test_stats_total_across_models(self):
        A.model_call_log("u", "m1", "think", 10, 100, 10, True, cost_usd=0.5)
        A.model_call_log("u", "m2", "think", 30, 200, 20, True, cost_usd=0.25)
        st = A.model_calls_stats()
        self.assertEqual((st["calls"], st["prompt_tokens"], st["completion_tokens"]),
                         (2, 300, 30))
        self.assertEqual(st["cost_usd"], 0.75)
        self.assertEqual(st["avg_latency_ms"], 20)

    def test_since_filter_scopes_a_run(self):
        import time
        A.model_call_log("u", "old", "think", 10, 1, 1, True)
        cut = time.time() + 0.01
        time.sleep(0.02)
        A.model_call_log("u", "new", "think", 10, 1, 1, True)
        self.assertEqual([r["model"] for r in A.model_calls_by_model(since=cut)], ["new"])


class TestOutcomeVerdict(_env.Base):
    """The one judgement call in the reporting path, pinned down explicitly."""

    def test_measured_gains_are_hits(self):
        for text in ("+840 food", "condition 62% → 100%", "human adopted",
                     "promoted to artisan", "suite green"):
            self.assertEqual(A.outcome_verdict(text), "hit", text)

    def test_stated_failures_are_misses(self):
        for text in ("failed — not enough wood", "human rejected", "board blocked it",
                     "timeout", "adopt failed: unaffordable"):
            self.assertEqual(A.outcome_verdict(text), "miss", text)

    def test_an_unclosed_decision_is_open(self):
        self.assertEqual(A.outcome_verdict(""), "open")
        self.assertEqual(A.outcome_verdict(None), "open")

    def test_unclassifiable_text_is_open_not_a_hit(self):
        # guessing would inflate every model equally and prove nothing
        self.assertEqual(A.outcome_verdict("the herald went to the gate"), "open")

    def test_a_failure_mentioning_a_gain_still_counts_as_a_miss(self):
        self.assertEqual(A.outcome_verdict("+200 food but the build failed"), "miss")


class TestDecisionRecords(_env.Base):

    def test_a_decision_records_the_model_that_made_it(self):
        self.register()
        os.environ["MODEL_ROLE_PRUDENCE"] = "labA"
        did = A.reason_add(1, "Prudence", "hold the spawn", "runway is short")
        row = next(r for r in A.reasons_top(5) if r["id"] == did)
        self.assertEqual(row["model"], "model-a")

    def test_an_explicit_model_overrides_the_lookup(self):
        did = A.reason_add(1, "Prudence", "d", "w", model="pinned")
        self.assertEqual(A.reasons_top(1)[0]["model"], "pinned")
        self.assertTrue(did)

    def test_unrouted_decisions_name_the_default_brain(self):
        did = A.reason_add(1, "governor", "d", "w")
        self.assertEqual(A.reasons_top(1)[0]["model"], "rule-based")
        self.assertTrue(did)

    def test_hit_rate_counts_only_closed_decisions(self):
        d1 = A.reason_add(1, "governor", "a", "w")
        d2 = A.reason_add(1, "governor", "b", "w")
        A.reason_add(1, "governor", "c", "w")               # left open
        A.decision_close(d1, outcome="+120 food")
        A.decision_close(d2, outcome="failed — no wood")
        row = A.decisions_by_model()[0]
        self.assertEqual((row["decisions"], row["closed"]), (3, 2))
        self.assertEqual(row["hit_rate"], 50)
        self.assertEqual(row["closed_pct"], 67)

    def test_hit_rate_is_none_when_nothing_has_closed(self):
        A.reason_add(1, "governor", "a", "w")
        self.assertIsNone(A.decisions_by_model()[0]["hit_rate"])

    def test_grounding_counts_decisions_citing_real_inputs(self):
        A.reason_add(1, "governor", "a", "w", derived_from=["skill:1", "event:2"])
        A.reason_add(1, "governor", "b", "w")
        row = A.decisions_by_model()[0]
        self.assertEqual(row["grounded"], 1)
        self.assertEqual(row["grounded_pct"], 50)

    def test_decisions_split_by_model(self):
        A.reason_add(1, "governor", "a", "w", model="m1")
        A.reason_add(1, "governor", "b", "w", model="m2")
        self.assertEqual({r["model"] for r in A.decisions_by_model()}, {"m1", "m2"})

    def test_lineage_carries_the_model(self):
        did = A.reason_add(1, "governor", "advance", "affordable", model="m1")
        self.assertEqual(A.lineage(did)["decision"]["model"], "m1")

    def test_lineage_of_a_missing_decision_is_empty(self):
        self.assertEqual(A.lineage(999999), {})


class TestEvalRuns(_env.Base):

    def test_scorecards_round_trip(self):
        A.eval_run_save({"label": "run-1", "tier": "ranked", "progress_pct": 80})
        runs = A.eval_runs(10)
        self.assertEqual(runs[0]["label"], "run-1")
        self.assertIn("_ts", runs[0])

    def test_newest_first(self):
        A.eval_run_save({"label": "older"})
        A.eval_run_save({"label": "newer"})
        self.assertEqual([r["label"] for r in A.eval_runs(10)], ["newer", "older"])

    def test_no_runs_is_an_empty_list_not_an_error(self):
        self.assertEqual(A.eval_runs(10), [])


class TestSchemaMigration(_env.Base):
    """An upgrade must never lose an existing settlement's memory."""

    def test_columns_are_added_to_a_pre_arena_database(self):
        import sqlite3
        c = sqlite3.connect(A.DB)
        try:
            c.execute("DROP TABLE IF EXISTS model_calls")
            c.execute("CREATE TABLE model_calls(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                      "ts REAL, provider TEXT, model TEXT, purpose TEXT, latency_ms INT, "
                      "prompt_tokens INT, completion_tokens INT, ok INT, error TEXT)")
            c.execute("INSERT INTO model_calls(ts, provider, model, purpose, latency_ms, "
                      "prompt_tokens, completion_tokens, ok, error) "
                      "VALUES(1,'u','legacy','think',10,5,5,1,'')")
            c.commit()
        finally:
            c.close()
        A.init()                                            # the upgrade path
        A.model_call_log("u", "new", "think", 10, 1, 1, True, role="governor", cost_usd=0.5)
        models_seen = {r["model"] for r in A.model_calls_by_model()}
        self.assertEqual(models_seen, {"legacy", "new"})    # the old row survived
        legacy = next(r for r in A.model_calls_by_model() if r["model"] == "legacy")
        self.assertEqual(legacy["roles"], [])               # unknown, not invented
        self.assertEqual(legacy["cost_usd"], 0.0)

"""Unit tests for the lesson engine — Article VI, the part that makes agents improve.

Lessons are not decoration: they are read back before decisions, so what accumulates
here steers the next generation. The rules that matter are that a restatement does not
become a second lesson, that re-confirming an expired lesson revives it rather than
duplicating it, and that nothing is ever deleted from the record.

The de-duplication rule here came out of a measured smoke run: 150 turns produced six
lessons, five of which were the same sentence with a different decimal.
"""

import _env
import anchor as A


class TestLessonKey(_env.Base):

    def test_the_same_advice_with_different_measurements_is_one_lesson(self):
        a = "Prioritise food early — it has paid best (avg 33.1/round); build its camp first."
        b = "Prioritise food early — it has paid best (avg 41.8/round); build its camp first."
        self.assertEqual(A.lesson_key(a), A.lesson_key(b))

    def test_different_advice_stays_different(self):
        a = "Prioritise food early — it has paid best (avg 33.1/round)."
        b = "Prioritise wood early — it has paid best (avg 33.1/round)."
        self.assertNotEqual(A.lesson_key(a), A.lesson_key(b))

    def test_punctuation_and_case_do_not_split_a_lesson(self):
        self.assertEqual(A.lesson_key("Build the mill FIRST!"),
                         A.lesson_key("build the mill, first"))

    def test_percentages_and_thousands_separators_are_measurements(self):
        self.assertEqual(A.lesson_key("spend was 96% of 1,000,000"),
                         A.lesson_key("spend was 12% of 250,000"))


class TestSkillAdd(_env.Base):

    def test_a_lesson_is_stored_and_citable(self):
        sid = A.skill_add(1, "Build the mill before the third villager.")
        self.assertTrue(sid)
        self.assertEqual(A.skills_top(5)[0]["lesson"],
                         "Build the mill before the third villager.")

    def test_an_empty_lesson_is_not_stored(self):
        self.assertIsNone(A.skill_add(1, "   "))
        self.assertEqual(A.skills_count(), 0)

    def test_an_exact_duplicate_does_not_grow_the_record(self):
        A.skill_add(1, "Retire idle agents sooner.")
        self.assertIsNone(A.skill_add(2, "Retire idle agents sooner."))
        self.assertEqual(A.skills_count(), 1)

    def test_a_restatement_refreshes_rather_than_accumulates(self):
        A.skill_add(1, "Prioritise food early — it has paid best (avg 33.1/round).")
        A.skill_add(2, "Prioritise food early — it has paid best (avg 41.8/round).")
        A.skill_add(3, "Prioritise food early — it has paid best (avg 39.0/round).")
        self.assertEqual(A.skills_count(), 1)
        # the surviving wording carries the CURRENT numbers, not the oldest
        self.assertIn("39.0", A.skills_top(5)[0]["lesson"])

    def test_a_genuinely_new_lesson_is_added(self):
        A.skill_add(1, "Prioritise food early (avg 33.1/round).")
        A.skill_add(2, "Trade surplus food for gold before it rots.")
        self.assertEqual(A.skills_count(), 2)

    def test_re_confirming_a_stale_lesson_revives_it(self):
        sid = A.skill_add(1, "Build the lumber camp second.")
        A.skill_prune(keep=0)                              # everything goes stale
        self.assertEqual(A.skills_top(10), [])
        revived = A.skill_add(50, "Build the lumber camp second.")
        self.assertEqual(revived, sid)                     # same row, not a new one
        self.assertEqual(len(A.skills_top(10)), 1)

    def test_a_restatement_revives_a_stale_lesson_too(self):
        A.skill_add(1, "Prioritise food early (avg 33.1/round).")
        A.skill_prune(keep=0)
        A.skill_add(50, "Prioritise food early (avg 44.4/round).")
        live = A.skills_top(10)
        self.assertEqual(len(live), 1)
        self.assertIn("44.4", live[0]["lesson"])

    def test_pruning_never_deletes_from_the_record(self):
        A.skill_add(1, "One.")
        A.skill_add(2, "Two.")
        before = A.skills_count()
        A.skill_prune(keep=1)
        self.assertEqual(A.skills_count(), before)         # counted, still there
        self.assertEqual(len(A.skills_top(10)), 1)         # but only one steers


class TestRetrospectiveIntegration(_env.Base):
    """The rule-based distillation must produce lessons that survive de-duplication —
    otherwise the learning loop reports progress it is not making."""

    def test_rule_based_lessons_are_produced_from_a_digest(self):
        import brain
        lessons = brain.retrospective(
            {"best_resource": "food", "yields": {"food": 33.1}, "waste": 2,
             "cap_hits": 1, "reaps": 1, "promotions": 0, "progress": 40}, [])
        self.assertTrue(lessons)
        for text in lessons:
            self.assertTrue(text.strip())

    def test_repeated_retrospectives_do_not_inflate_the_lesson_count(self):
        import brain
        for avg in (33.1, 33.3, 41.8, 39.0):
            for text in brain.retrospective(
                    {"best_resource": "food", "yields": {"food": avg}, "waste": 0,
                     "cap_hits": 0, "reaps": 0, "promotions": 1, "progress": 40}, []):
                A.skill_add(1, text)
        # four retrospectives, one piece of advice
        self.assertEqual(A.skills_count(), 1)


class TestBootState(_env.Base):
    """Persistence has to be observed, not assumed — the difference between a volume
    that is configured and one that actually carried the record forward."""

    def test_a_fresh_directory_is_reported_as_fresh(self):
        import os
        import tempfile
        # a path nothing has ever written to — the case a detached volume produces
        was = A.DB
        A.DB = os.path.join(tempfile.mkdtemp(prefix="phoenix-fresh-"), "anchor.sqlite")
        try:
            A._BOOT.clear()
            A.init()
            boot = A.boot_state()
            self.assertFalse(boot["db_existed"])
            self.assertEqual(boot["events_at_boot"], 0)
            self.assertEqual(boot["careers_at_boot"], 0)
        finally:
            A.DB = was
            A._BOOT.clear()

    def test_an_existing_record_is_counted_at_boot(self):
        A.record(1, "test", "something happened")
        A.career_add("vil-01", 1, "born", "for the test")
        A._BOOT.clear()
        A.init()                                    # simulate a restart on the same disk
        boot = A.boot_state()
        self.assertTrue(boot["db_existed"])
        self.assertGreaterEqual(boot["events_at_boot"], 1)
        self.assertGreaterEqual(boot["careers_at_boot"], 1)

    def test_boot_state_is_captured_once_not_per_call(self):
        A._BOOT.clear()
        A.init()
        first = A.boot_state()
        A.record(1, "test", "later event")
        A.init()
        self.assertEqual(A.boot_state(), first)     # a later init must not rewrite it

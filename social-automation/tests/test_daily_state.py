import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_connection, init_db  # noqa: E402
from core.models import TopicCandidate  # noqa: E402
from core.scoring import evaluate_candidate  # noqa: E402
from pipeline import daily_state  # noqa: E402


def make_candidate(candidate_id, topic, **overrides):
    base = dict(
        candidate_id=candidate_id, topic=topic, category="benefits", summary="s",
        timeliness_signal=0.9, practical_value_signal=0.9, population_reach_signal=0.9,
        verification_availability_signal=0.9, save_share_signal=0.9, duplication_penalty_signal=0.05,
        has_authoritative_source=True,
    )
    base.update(overrides)
    return TopicCandidate(**base)


class TestFingerprint(unittest.TestCase):
    def test_normalization_makes_near_identical_topics_match(self):
        fp1 = daily_state.compute_topic_fingerprint("병원비 환급 신청")
        fp2 = daily_state.compute_topic_fingerprint("  병원비   환급 신청  ")
        fp3 = daily_state.compute_topic_fingerprint("병원비 환급 신청!")
        self.assertEqual(fp1, fp2)
        self.assertEqual(fp1, fp3)

    def test_different_topics_differ(self):
        fp1 = daily_state.compute_topic_fingerprint("병원비 환급 신청")
        fp2 = daily_state.compute_topic_fingerprint("보이스피싱 주의보")
        self.assertNotEqual(fp1, fp2)


class TestDedupe(unittest.TestCase):
    def test_dedupe_keeps_first_occurrence_only(self):
        candidates = [
            make_candidate("c1", "병원비 환급 신청"),
            make_candidate("c2", "병원비   환급   신청"),  # same normalized topic
            make_candidate("c3", "다른 주제"),
        ]
        deduped = daily_state.dedupe_candidates(candidates)
        self.assertEqual([c.candidate_id for c in deduped], ["c1", "c3"])


class TestRunsTableAndRecency(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_upsert_and_get_run_roundtrip(self):
        daily_state.upsert_run(self.conn, "acct", "2026-08-20", status="COMPLETE", content_id="acct-2026-08-20",
                                topic_fingerprint="fp1", started_at="t0", finished_at="t1")
        row = daily_state.get_run(self.conn, "acct", "2026-08-20")
        self.assertEqual(row["status"], "COMPLETE")
        self.assertEqual(row["topic_fingerprint"], "fp1")

    def test_second_upsert_updates_same_row_not_duplicate(self):
        daily_state.upsert_run(self.conn, "acct", "2026-08-20", status="RUNNING", started_at="t0")
        daily_state.upsert_run(self.conn, "acct", "2026-08-20", status="COMPLETE", content_id="acct-2026-08-20",
                                topic_fingerprint="fp1", finished_at="t1")
        rows = self.conn.execute("SELECT * FROM runs WHERE account_id='acct'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "COMPLETE")
        self.assertEqual(rows[0]["started_at"], "t0")  # preserved, not overwritten by None

    def test_recent_topic_fingerprints_respects_window(self):
        daily_state.upsert_run(self.conn, "acct", "2026-08-01", status="COMPLETE", topic_fingerprint="fp-old")
        daily_state.upsert_run(self.conn, "acct", "2026-08-19", status="COMPLETE", topic_fingerprint="fp-recent")
        recent = daily_state.recent_topic_fingerprints(self.conn, "acct", "2026-08-22", window_days=7)
        self.assertIn("fp-recent", recent)
        self.assertNotIn("fp-old", recent)

    def test_apply_recency_penalty_triggers_scoring_rejection(self):
        candidates = [make_candidate("c1", "반복 주제")]
        fp = daily_state.compute_topic_fingerprint("반복 주제")
        daily_state.apply_recency_penalty(candidates, {fp})
        accepted, _breakdown, reason = evaluate_candidate(candidates[0], min_score=70)
        self.assertFalse(accepted)
        self.assertIn("duplication", reason)


class TestSameStoryDetection(unittest.TestCase):
    """A-E: the same-story identity check must catch a same-event
    duplicate even when reworded, without over-rejecting a genuinely
    different (or genuinely updated) story."""

    def test_a_exact_same_candidate_id_is_treated_as_duplicate_upstream(self):
        # is_same_story isn't even reached for this case in production --
        # reject_previously_used_candidates short-circuits on candidate_id
        # equality first (see the record/reject roundtrip test below).
        self.assertEqual("fss-press-223841", "fss-press-223841")

    def test_b_same_story_slightly_reworded_headline_rejected(self):
        self.assertTrue(daily_state.is_same_story(
            "fss-press-223841", "제2차 보이스피싱 근절 협의회 개최",
            "fss-press-300001", "8.20 제2차 보이스피싱 근절 협의회, 이렇게 달라진다",
        ))

    def test_c_same_policy_substantially_different_wording_rejected(self):
        self.assertTrue(daily_state.is_same_story(
            "nts-press-1000", "국세청, 호우 피해 특별재난지역 납세자 세정지원 실시",
            "nts-press-2000", "국세청 세정지원 대상, 호우 피해 특별재난지역까지 대폭 확대 발표",
        ))

    def test_d_same_category_genuinely_different_story_allowed(self):
        self.assertFalse(daily_state.is_same_story(
            "nts-press-1354268", "거제 등 남부지방 호우 피해 지역 중소기업에 대한 신속한 세정지원 실시",
            "nts-press-1353907", "호우 피해 특별재난지역 납세자에 대한  적극적인 세정지원 실시",
        ))

    def test_e_material_new_development_allowed(self):
        # A 3rd session with new measures is real new user value, not a
        # repeat of the 2nd session -- the differing "2차"/"3차" anchor
        # keeps this below the same-story bar.
        self.assertFalse(daily_state.is_same_story(
            "fss-press-223841", "제2차 보이스피싱 근절 협의회 개최",
            "fss-press-300000", "제3차 보이스피싱 근절 협의회, 새로운 대응책 대거 발표",
        ))


class TestRejectPreviouslyUsedCandidates(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_recorded_topic_permanently_rejects_exact_and_reworded_repeats(self):
        daily_state.record_selected_topic(
            self.conn, "acct", "fss-press-223841", "제2차 보이스피싱 근절 협의회 개최",
            "finance_savings", 77.8, "COMPLETE", "2026-08-24T00:00:00Z",
        )
        candidates = [
            make_candidate("fss-press-223841", "제2차 보이스피싱 근절 협의회 개최"),  # A: exact repeat
            make_candidate("fss-press-300001", "8.20 제2차 보이스피싱 근절 협의회, 이렇게 달라진다"),  # B
            make_candidate("nts-press-9999", "완전히 다른 주제의 세금 공제 안내"),  # genuinely new
        ]
        all_fps = daily_state.recent_topic_fingerprints(self.conn, "acct", "2099-01-01", daily_state.PERMANENT_WINDOW_DAYS)
        daily_state.reject_previously_used_candidates(self.conn, "acct", candidates, all_fps)

        for c in candidates[:2]:
            accepted, _b, reason = evaluate_candidate(c, min_score=70)
            self.assertFalse(accepted, f"{c.candidate_id} should have been rejected as a duplicate")
            self.assertIn("duplication", reason)

        accepted, _b, _reason = evaluate_candidate(candidates[2], min_score=70)
        self.assertTrue(accepted, "a genuinely new topic must not be rejected")


if __name__ == "__main__":
    unittest.main()

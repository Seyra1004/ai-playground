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


if __name__ == "__main__":
    unittest.main()

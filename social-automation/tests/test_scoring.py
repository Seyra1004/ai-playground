import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import TopicCandidate  # noqa: E402
from core.scoring import evaluate_candidate, score_candidate  # noqa: E402


def make_candidate(**overrides) -> TopicCandidate:
    base = dict(
        candidate_id="c1",
        topic="t",
        category="benefits",
        summary="s",
        urgent=False,
        timeliness_signal=0.8,
        practical_value_signal=0.8,
        population_reach_signal=0.8,
        verification_availability_signal=0.8,
        save_share_signal=0.8,
        duplication_penalty_signal=0.0,
        has_authoritative_source=True,
    )
    base.update(overrides)
    return TopicCandidate(**base)


class TestScoring(unittest.TestCase):
    def test_score_is_deterministic(self):
        c = make_candidate()
        s1 = score_candidate(c).total
        s2 = score_candidate(c).total
        self.assertEqual(s1, s2)

    def test_urgent_override_accepts_below_min_score_with_authoritative_source(self):
        c = make_candidate(
            urgent=True,
            timeliness_signal=0.1,
            practical_value_signal=0.1,
            population_reach_signal=0.1,
            verification_availability_signal=0.1,
            save_share_signal=0.1,
            has_authoritative_source=True,
        )
        accepted, breakdown, reason = evaluate_candidate(c, min_score=70)
        self.assertTrue(accepted)
        self.assertLess(breakdown.total, 70)

    def test_urgent_override_rejected_without_authoritative_source(self):
        c = make_candidate(urgent=True, has_authoritative_source=False)
        accepted, _breakdown, _reason = evaluate_candidate(c, min_score=70)
        self.assertFalse(accepted)

    def test_low_score_rejected(self):
        c = make_candidate(
            timeliness_signal=0.1,
            practical_value_signal=0.1,
            population_reach_signal=0.1,
            verification_availability_signal=0.1,
            save_share_signal=0.1,
        )
        accepted, breakdown, _reason = evaluate_candidate(c, min_score=70)
        self.assertFalse(accepted)
        self.assertLess(breakdown.total, 70)

    def test_missing_authoritative_source_rejected_even_with_high_score(self):
        c = make_candidate(has_authoritative_source=False)
        accepted, _breakdown, _reason = evaluate_candidate(c, min_score=70)
        self.assertFalse(accepted)

    def test_strong_duplication_rejected(self):
        c = make_candidate(duplication_penalty_signal=0.9)
        accepted, _breakdown, _reason = evaluate_candidate(c, min_score=70)
        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()

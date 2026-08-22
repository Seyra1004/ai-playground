import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.factcheck import validate_fact_sheet_claims  # noqa: E402
from core.models import Claim, ClaimType, QAStatus, Source, SourceType, VerificationStatus  # noqa: E402


class TestFactcheck(unittest.TestCase):
    def test_missing_critical_claim_source_fails(self):
        claim = Claim(
            claim_id="c1",
            claim_type=ClaimType.AMOUNT,
            text="amount claim",
            source_ids=[],
        )
        status, results = validate_fact_sheet_claims([claim], [])
        self.assertEqual(status, QAStatus.FAIL)
        self.assertEqual(results[0].status, QAStatus.FAIL)

    def test_critical_claim_with_only_news_source_fails(self):
        news = Source(
            source_id="s1", url="http://news.example", source_type=SourceType.NEWS_MEDIA, publisher="News"
        )
        claim = Claim(claim_id="c1", claim_type=ClaimType.DEADLINE, text="deadline", source_ids=["s1"])
        status, _results = validate_fact_sheet_claims([claim], [news])
        self.assertEqual(status, QAStatus.FAIL)

    def test_critical_claim_with_authoritative_source_passes(self):
        gov = Source(
            source_id="s1", url="http://gov.example", source_type=SourceType.GOVERNMENT, publisher="Gov"
        )
        claim = Claim(claim_id="c1", claim_type=ClaimType.DEADLINE, text="deadline", source_ids=["s1"])
        status, _results = validate_fact_sheet_claims([claim], [gov])
        self.assertEqual(status, QAStatus.PASS)

    def test_conflicting_authoritative_sources_needs_review(self):
        gov = Source(
            source_id="s1", url="http://gov.example", source_type=SourceType.GOVERNMENT, publisher="Gov"
        )
        claim = Claim(
            claim_id="c1",
            claim_type=ClaimType.AMOUNT,
            text="amount",
            source_ids=["s1"],
            status=VerificationStatus.CONFLICTING,
        )
        status, _results = validate_fact_sheet_claims([claim], [gov])
        self.assertEqual(status, QAStatus.NEEDS_REVIEW)

    def test_non_critical_claim_without_source_passes(self):
        claim = Claim(claim_id="c1", claim_type=ClaimType.OTHER, text="fyi", source_ids=[])
        status, _results = validate_fact_sheet_claims([claim], [])
        self.assertEqual(status, QAStatus.PASS)


if __name__ == "__main__":
    unittest.main()

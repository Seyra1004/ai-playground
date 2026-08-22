import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.page_selector import PageCountInputs, select_page_count  # noqa: E402


class TestPageSelector(unittest.TestCase):
    def test_stays_within_configured_range_for_weak_content(self):
        inputs = PageCountInputs(
            critical_info_blocks=0,
            eligibility_conditions=1,
            exclusions_count=0,
            procedure_steps=1,
            has_comparison=False,
            volatility_risk=False,
            estimated_text_density=0.0,
        )
        count = select_page_count(inputs, pages_min=4, pages_max=8)
        self.assertGreaterEqual(count, 4)
        self.assertLessEqual(count, 8)

    def test_weak_content_not_padded_to_max(self):
        inputs = PageCountInputs(
            critical_info_blocks=0,
            eligibility_conditions=1,
            exclusions_count=0,
            procedure_steps=1,
            has_comparison=False,
            volatility_risk=False,
            estimated_text_density=0.0,
        )
        count = select_page_count(inputs, pages_min=4, pages_max=8)
        self.assertEqual(count, 4)

    def test_dense_content_stays_within_max(self):
        inputs = PageCountInputs(
            critical_info_blocks=10,
            eligibility_conditions=5,
            exclusions_count=5,
            procedure_steps=5,
            has_comparison=True,
            volatility_risk=True,
            estimated_text_density=1.0,
        )
        count = select_page_count(inputs, pages_min=4, pages_max=8)
        self.assertEqual(count, 8)

    def test_stays_within_narrower_account_range(self):
        inputs = PageCountInputs(
            critical_info_blocks=10,
            eligibility_conditions=5,
            exclusions_count=5,
            procedure_steps=5,
            has_comparison=True,
            volatility_risk=True,
            estimated_text_density=1.0,
        )
        count = select_page_count(inputs, pages_min=4, pages_max=6)
        self.assertEqual(count, 6)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import CarouselPage  # noqa: E402
from pipeline import photo_acquisition as pa  # noqa: E402
from pipeline.editorial_asset_planner import PageAssetPlan, plan_content_assets, plan_page_assets  # noqa: E402

_REQUIRED_FIELDS = [
    "page_number", "page_role", "primary_message", "supporting_facts",
    "primary_asset_type", "secondary_asset_type", "photo_value", "distinctive_subject",
    "search_queries", "concept_pairs", "negative_concepts", "information_object_type",
    "composition_intent", "fallback_chain", "asset_status",
]


class TestEditorialAssetPlanner(unittest.TestCase):
    def test_planner_output_has_required_fields(self):
        page = CarouselPage(1, "hook", "눈치 보여서 미루던 휴가, 이제 4일 유급으로 당당하게",
                             "난임치료를 받고 있다면 유급 휴가가 2일에서 4일로 늘어납니다.",
                             "stat", {"type": "stat_hero", "big_text": "2일→4일"})
        plan = plan_page_assets(page)
        self.assertIsInstance(plan, PageAssetPlan)
        for f in _REQUIRED_FIELDS:
            self.assertTrue(hasattr(plan, f), f"missing field {f}")
        self.assertEqual(plan.page_role, "hook")
        self.assertEqual(plan.primary_message, page.headline)

    def test_pure_data_role_gets_low_photo_value_no_search(self):
        # A comparison/DATA-shaped page should not trigger a photo search --
        # DATA can legitimately outperform photography (product rule).
        page = CarouselPage(3, "comparison", "무엇이 달라지나요", "2일에서 4일로, 16.8만원에서 33.7만원으로.",
                             "compare", {"type": "comparison", "metrics": [{"label": "유급일수", "before": "2일", "after": "4일"}]})
        plan = plan_page_assets(page)
        self.assertEqual(plan.photo_value, "LOW")
        self.assertEqual(plan.search_queries, [])
        self.assertEqual(plan.concept_pairs, [])

    def test_cta_role_never_becomes_generic_stock_search(self):
        page = CarouselPage(6, "cta", "이제 이렇게 신청하면 돼요", "① 확인 → ② 준비 → ③ 요청", "cta", {"type": "cta_panel"})
        plan = plan_page_assets(page)
        self.assertNotEqual(plan.photo_value, "HIGH")

    def test_plan_content_assets_covers_every_page_in_order(self):
        pages = [
            CarouselPage(1, "hook", "H1", "B1", "v", {"type": "stat_hero", "big_text": "X"}),
            CarouselPage(2, "eligibility", "H2", "B2", "v", {"type": "checklist", "items": ["a"]}),
        ]
        plans = plan_content_assets(pages)
        self.assertEqual([p.page_number for p in plans], [1, 2])
        self.assertEqual([p.page_role for p in plans], ["hook", "eligibility"])

    def test_generalization_across_four_unrelated_domains(self):
        """Distinctive-subject extraction must generalize -- proven here with
        four topics from unrelated domains the planner has zero hardcoded
        knowledge of (housing / consumer fraud / utility billing / transport).
        Each hits the existing, already-approved glossary deterministically
        (no CLI call), so this stays fast and offline."""
        cases = [
            ("housing", "전세보증금 못 돌려받을 위기라면 지금 확인하세요",
             "임대차 계약이 끝났는데 보증금을 못 받고 있다면 이 절차부터 시작하세요."),
            ("consumer_fraud", "모르는 번호로 온 스미싱 문자, 이렇게 대처하세요",
             "택배 사칭 스미싱 문자를 받았다면 절대 링크를 누르지 말고 즉시 신고하세요."),
            ("utility_billing", "카드로 낸 관리비, 환급받을 수 있는지 확인하세요",
             "아파트 관리비를 카드로 이중 결제했다면 환급 신청이 가능합니다."),
            ("transport_travel", "항공권 환불 위약금, 얼마나 낼까",
             "항공권을 취소하면 환불 규정에 따라 위약금이 달라집니다."),
        ]
        failures = []
        for domain, headline, body in cases:
            page = CarouselPage(1, "hook", headline, body, "v", {"type": "stat_hero", "big_text": "X"})
            plan = plan_page_assets(page)
            if not plan.distinctive_subject:
                failures.append(f"{domain}: no distinctive subject derived")
                continue
            if pa._is_generic_concept(plan.distinctive_subject):
                failures.append(f"{domain}: subject '{plan.distinctive_subject}' is generic")
            if not plan.search_queries:
                failures.append(f"{domain}: no search queries generated")
        self.assertEqual(failures, [], f"generalization failures: {failures}")


class TestPhotoAcquisitionConsumesPlan(unittest.TestCase):
    def test_acquire_photo_uses_provided_concepts_without_rederiving(self):
        # If the planner already computed concepts, acquire_photo_for_page
        # must use them verbatim -- proven by asserting derive_concepts is
        # never called when concepts= is supplied.
        with patch.object(pa, "derive_concepts") as mock_derive, \
             patch.object(pa, "search_pexels", return_value=[]), \
             patch.object(pa, "search_openverse", return_value=[]), \
             patch.object(pa, "search_commons", return_value=[]):
            result = pa.acquire_photo_for_page(
                "hook", "h", "b", 1, os.path.join("data", "assets", "_test_planner_wiring"), set(),
                concepts=[("fertility clinic", "fertility clinic room")],
            )
            mock_derive.assert_not_called()
            self.assertIsNone(result)  # no providers configured/mocked to return anything -> NO_PHOTO, not an error

    def test_empty_concepts_short_circuits_with_no_network_calls(self):
        with patch.object(pa, "derive_concepts") as mock_derive, \
             patch.object(pa, "search_pexels") as mock_pexels:
            result = pa.acquire_photo_for_page(
                "cta", "h", "b", 6, os.path.join("data", "assets", "_test_planner_wiring"), set(),
                concepts=[],
            )
            mock_derive.assert_not_called()
            mock_pexels.assert_not_called()
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_account_config, load_brand_config  # noqa: E402
from core.models import CanonicalContent, CarouselPage  # noqa: E402
from platforms.instagram.adapter import build_instagram_content  # noqa: E402
from platforms.threads.adapter import build_threads_content  # noqa: E402
from qa.content_qa import run_content_qa  # noqa: E402
from qa.render_qa import run_render_qa  # noqa: E402
from renderer.html_renderer import build_renderer_input  # noqa: E402
from scripts.demo_fixture import build_demo_fact_sheet  # noqa: E402


def build_canonical(pages=None):
    fact_sheet = build_demo_fact_sheet()
    if pages is None:
        pages = [
            CarouselPage(1, "hook", fact_sheet.reader_value, fact_sheet.amount_or_benefit, "hook_visual"),
            CarouselPage(2, "why_now", fact_sheet.why_it_matters, fact_sheet.deadline, "deadline_visual"),
            CarouselPage(3, "eligibility", "대상", fact_sheet.eligibility, "eligibility_visual"),
            CarouselPage(4, "cta", "확인하세요", "지금 신청", "cta_visual"),
        ]
    return CanonicalContent(
        content_id=fact_sheet.content_id,
        fact_sheet=fact_sheet,
        page_count=len(pages),
        page_plan=[p.role for p in pages],
        pages=pages,
    )


class TestAdaptersShareCanonicalContent(unittest.TestCase):
    def test_instagram_and_threads_use_same_canonical_content(self):
        account = load_account_config("swipe_info")
        brand = load_brand_config(account.brand_config_path)
        canonical = build_canonical()

        ig = build_instagram_content(canonical, brand)
        th = build_threads_content(canonical)

        self.assertIs(ig.pages, canonical.pages)
        self.assertIn(canonical.fact_sheet.deadline, th.text)
        # Threads copy must not simply be the Instagram carousel text.
        self.assertNotEqual(th.text, ig.caption)

    def test_instagram_adapter_fails_clearly_without_generated_pages(self):
        canonical = build_canonical(pages=[])
        account = load_account_config("swipe_info")
        brand = load_brand_config(account.brand_config_path)
        with self.assertRaises(ValueError):
            build_instagram_content(canonical, brand)

    def test_threads_adapter_fails_clearly_without_generated_pages(self):
        canonical = build_canonical(pages=[])
        with self.assertRaises(ValueError):
            build_threads_content(canonical)


class TestQAGate(unittest.TestCase):
    def test_qa_prevents_complete_when_missing_cta(self):
        canonical = build_canonical(
            pages=[
                CarouselPage(1, "hook", "headline", "body", "visual"),
                CarouselPage(2, "why_now", "headline2", "body2", "visual2"),
            ]
        )
        account = load_account_config("swipe_info")
        brand = load_brand_config(account.brand_config_path)
        ig = build_instagram_content(canonical, brand)
        result = run_content_qa(canonical, ig, account.content.pages_min, account.content.pages_max)
        self.assertNotEqual(result.status.value, "PASS")
        self.assertIn("final_page_missing_cta", result.checks_failed)

    def test_brand_yaml_drives_canvas_settings(self):
        account = load_account_config("swipe_info")
        brand = load_brand_config(account.brand_config_path)
        canonical = build_canonical()
        renderer_input = build_renderer_input(canonical, brand)
        for page_render in renderer_input:
            self.assertEqual(page_render["width"], 1080)
            self.assertEqual(page_render["height"], 1350)
        qa = run_render_qa(renderer_input, brand)
        self.assertEqual(qa.status.value, "PASS")


if __name__ == "__main__":
    unittest.main()

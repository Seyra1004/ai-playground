from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest


SCRIPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "build_review_page.py")
SPEC = importlib.util.spec_from_file_location("build_review_page", SCRIPT_PATH)
review_page = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(review_page)


class ReviewPagePrivacyTest(unittest.TestCase):
    def test_public_review_page_never_renders_internal_sources(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "social-automation", "output", "swipe_info", "2026-08-24")
            ig_dir = os.path.join(out_dir, "instagram")
            os.makedirs(ig_dir)
            with open(os.path.join(ig_dir, "page_01.png"), "wb") as f:
                f.write(b"not-a-real-png-but-copyable")
            payloads = {
                "run_summary.json": {"status": "COMPLETE", "selected_topic": "테스트 주제"},
                "qa_report.json": {"content_qa_status": "PASS", "render_qa_status": "PASS"},
                "sources.json": [{"publisher": "내부 공식 출처", "url": "https://private-source.example/secret"}],
                "fact_sheet.json": {"verified_at": "2026-08-24T00:00:00Z"},
            }
            for name, payload in payloads.items():
                with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
            for name, value in {"instagram_caption.txt": "캡션", "threads.txt": "쓰레드"}.items():
                with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
                    f.write(value)

            old_root, old_docs = review_page.REPO_ROOT, review_page.DOCS_ROOT
            try:
                review_page.REPO_ROOT = root
                review_page.DOCS_ROOT = os.path.join(root, "docs", "v2", "reports", "swipe-info")
                review_page.build("swipe_info", "2026-08-24")
                for page in ("2026-08-24", "latest"):
                    with open(os.path.join(review_page.DOCS_ROOT, page, "index.html"), encoding="utf-8") as f:
                        html = f.read()
                    self.assertNotIn("private-source.example", html)
                    self.assertNotIn("공식 출처", html)
                    self.assertNotIn("sources.json", html)
                    self.assertIn("PNG 전체 ZIP 다운로드", html)
                    self.assertTrue(os.path.isfile(os.path.join(review_page.DOCS_ROOT, page, "assets", "SWIPE_INFO_2026-08-24_업로드용_PNG.zip")))
            finally:
                review_page.REPO_ROOT, review_page.DOCS_ROOT = old_root, old_docs


if __name__ == "__main__":
    unittest.main()

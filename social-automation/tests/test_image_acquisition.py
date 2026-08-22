import io
import os
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import CarouselPage, Source, SourceType  # noqa: E402
from pipeline import image_acquisition as ia  # noqa: E402

TEST_OUT_DIR = os.path.join("data", "assets", "_test_image_acquisition")


def _real_jpeg_bytes(size=(300, 300)):
    # A solid-color image JPEG-compresses to well under our tiny-file
    # rejection threshold (unlike any real photo/screenshot); random noise
    # gives a realistic file size for the test.
    import random

    from PIL import Image

    im = Image.new("RGB", size)
    pixels = [(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(size[0] * size[1])]
    im.putdata(pixels)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _gov_source(url="https://example.go.kr/notice/1"):
    return Source(
        source_id="s1", url=url, source_type=SourceType.GOVERNMENT,
        publisher="테스트기관", published_at="2026-08-23", retrieved_at="2026-08-23",
    )


class TestImageAcquisition(unittest.TestCase):
    def setUp(self):
        if os.path.isdir(TEST_OUT_DIR):
            shutil.rmtree(TEST_OUT_DIR)

    def tearDown(self):
        if os.path.isdir(TEST_OUT_DIR):
            shutil.rmtree(TEST_OUT_DIR)

    def test_source_with_embedded_usable_image_is_accepted(self):
        html = '<html><body><img src="/photo.jpg" alt="notice photo"></body></html>'
        jpeg = _real_jpeg_bytes()

        def fake_fetch(url, timeout=12):
            if url.endswith(".jpg"):
                return jpeg
            return html.encode("utf-8")

        with patch.object(ia, "_fetch", side_effect=fake_fetch):
            result = ia.discover_and_acquire_images(_gov_source(), TEST_OUT_DIR)

        self.assertEqual(len(result), 1)
        self.assertTrue(os.path.isfile(result[0]["path"]))
        self.assertTrue(os.path.isfile(os.path.join(TEST_OUT_DIR, "asset_sources.json")))
        self.assertEqual(result[0]["publisher"], "테스트기관")

    def test_source_with_no_usable_images_returns_empty(self):
        html = "<html><body><p>공지사항 내용만 있고 이미지가 없습니다.</p></body></html>"

        with patch.object(ia, "_fetch", side_effect=lambda url, timeout=12: html.encode("utf-8")):
            result = ia.discover_and_acquire_images(_gov_source(), TEST_OUT_DIR)

        self.assertEqual(result, [])
        self.assertFalse(os.path.isfile(os.path.join(TEST_OUT_DIR, "asset_sources.json")))

    def test_duplicate_image_rejected(self):
        html = '<html><body><img src="/a.jpg"><img src="/b.jpg"></body></html>'
        jpeg = _real_jpeg_bytes()  # identical bytes for both "different" URLs

        def fake_fetch(url, timeout=12):
            if url.endswith(".jpg"):
                return jpeg
            return html.encode("utf-8")

        with patch.object(ia, "_fetch", side_effect=fake_fetch):
            result = ia.discover_and_acquire_images(_gov_source(), TEST_OUT_DIR)

        self.assertEqual(len(result), 1)  # second identical image rejected as duplicate

    def test_missing_rights_source_rejected(self):
        news_source = Source(
            source_id="s2", url="https://example-news.co.kr/article/1", source_type=SourceType.NEWS_MEDIA,
            publisher="어떤뉴스", published_at="2026-08-23", retrieved_at="2026-08-23",
        )
        # No network mock needed: rights gate rejects before any fetch.
        with patch.object(ia, "_fetch", side_effect=AssertionError("must not fetch an ineligible source")):
            result = ia.discover_and_acquire_images(news_source, TEST_OUT_DIR)
        self.assertEqual(result, [])

    def test_tiny_image_rejected(self):
        accepted, seen = [], set()
        tiny = _real_jpeg_bytes(size=(50, 50))
        ia._maybe_accept(tiny, "small.jpg", "https://example.go.kr/small.jpg", "linked-on-official-page",
                          _gov_source(), TEST_OUT_DIR, accepted, seen, max_images=4)
        self.assertEqual(accepted, [])

    def test_decorative_filename_rejected(self):
        accepted, seen = [], set()
        img = _real_jpeg_bytes()
        ia._maybe_accept(img, "site_logo.jpg", "https://example.go.kr/site_logo.jpg", "linked-on-official-page",
                          _gov_source(), TEST_OUT_DIR, accepted, seen, max_images=4)
        self.assertEqual(accepted, [])

    def test_cached_result_reused_without_network(self):
        html = '<html><body><img src="/photo.jpg"></body></html>'
        jpeg = _real_jpeg_bytes()

        def fake_fetch(url, timeout=12):
            return jpeg if url.endswith(".jpg") else html.encode("utf-8")

        with patch.object(ia, "_fetch", side_effect=fake_fetch):
            first = ia.discover_and_acquire_images(_gov_source(), TEST_OUT_DIR)

        with patch.object(ia, "_fetch", side_effect=AssertionError("must not re-fetch a cached source")):
            second = ia.discover_and_acquire_images(_gov_source(), TEST_OUT_DIR)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["file"], second[0]["file"])

    def test_assign_images_to_pages_uses_role_priority_not_page_number(self):
        pages = [
            CarouselPage(1, "hook", "hook headline", "body", "v"),
            CarouselPage(2, "cta", "cta headline", "body", "v"),
            CarouselPage(3, "why_now", "why headline", "body", "v"),
        ]
        images = [{"path": "/x/a.jpg"}, {"path": "/x/b.jpg"}]
        changed = ia.assign_images_to_pages(pages, images)
        # why_now (priority 1) and cta (priority 2) get images; hook never does.
        self.assertEqual(set(changed), {2, 3})
        self.assertEqual(pages[0].visual_data, {})
        self.assertEqual(pages[1].visual_data["type"], "real_image")
        self.assertEqual(pages[2].visual_data["type"], "real_image")


if __name__ == "__main__":
    unittest.main()

"""report.image_enrichment: og:image/twitter:image extraction from a real
article page's own HTML, and the read-only fetch wrapper. http_get is
always injected as a fake in these tests -- no real network call."""

import pytest

from report.image_enrichment import extract_og_image_from_html, fetch_article_image_url


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_extracts_og_image_property_then_content():
    html = '<html><head><meta property="og:image" content="https://example.com/real.jpg"></head></html>'
    assert extract_og_image_from_html(html) == "https://example.com/real.jpg"


def test_extracts_og_image_content_then_property_order():
    html = '<meta content="https://example.com/real2.jpg" property="og:image">'
    assert extract_og_image_from_html(html) == "https://example.com/real2.jpg"


def test_falls_back_to_twitter_image_when_no_og_image():
    html = '<meta name="twitter:image" content="https://example.com/twitter.jpg">'
    assert extract_og_image_from_html(html) == "https://example.com/twitter.jpg"


def test_og_image_preferred_over_twitter_image_when_both_present():
    html = (
        '<meta property="og:image" content="https://example.com/og.jpg">'
        '<meta name="twitter:image" content="https://example.com/twitter.jpg">'
    )
    assert extract_og_image_from_html(html) == "https://example.com/og.jpg"


def test_no_meta_tags_returns_none():
    assert extract_og_image_from_html("<html><body>no image here</body></html>") is None


def test_empty_html_returns_none():
    assert extract_og_image_from_html("") is None
    assert extract_og_image_from_html(None) is None


def test_non_http_og_image_content_rejected():
    html = '<meta property="og:image" content="javascript:alert(1)">'
    assert extract_og_image_from_html(html) is None


def test_fetch_returns_none_on_non_200_status():
    def fake_get(url, timeout):
        return _FakeResponse(status_code=404, text="")
    assert fetch_article_image_url("https://example.com/article", http_get=fake_get) is None


def test_fetch_returns_none_on_network_exception():
    def fake_get(url, timeout):
        raise ConnectionError("boom")
    assert fetch_article_image_url("https://example.com/article", http_get=fake_get) is None


def test_fetch_returns_none_for_invalid_article_url():
    assert fetch_article_image_url("not-a-real-url", http_get=lambda url, timeout: _FakeResponse()) is None
    assert fetch_article_image_url(None, http_get=lambda url, timeout: _FakeResponse()) is None


def test_fetch_returns_real_og_image_on_success():
    html = '<meta property="og:image" content="https://cdn.example.com/hero.jpg">'
    def fake_get(url, timeout):
        assert url == "https://example.com/article"
        return _FakeResponse(status_code=200, text=html)
    assert fetch_article_image_url("https://example.com/article", http_get=fake_get) == "https://cdn.example.com/hero.jpg"

"""ingestion.article_image_enrichment: og:image/twitter:image extraction
(pure parsing, no network) and the raw_items.extra_json enrichment/cache
contract. http_get is always injected -- no real network call in this
file."""

import json

import pytest

from db.database import connect, init_db
from ingestion.article_image_enrichment import (
    enrich_article_image,
    enrich_pending_article_images,
    extract_meta_image,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_raw_item(conn, source_url, extra_json=None, category="MUSIC_INDUSTRY_NEWS"):
    conn.execute(
        """INSERT INTO raw_items
           (source_name, source_item_key, source_type, source_url, title, collected_at, category, extra_json)
           VALUES ('rollingstone_music_rss', ?, 'rss', ?, 'Real headline', '2026-08-16T00:00:00+00:00', ?, ?)""",
        (source_url, source_url, category, extra_json),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class _FakeResponse:
    def __init__(self, text):
        self.text = text


# ---- extract_meta_image: pure parsing ---------------------------------


def test_extracts_real_og_image():
    html = '<html><head><meta property="og:image" content="https://example.com/real.jpg"></head></html>'
    assert extract_meta_image(html) == "https://example.com/real.jpg"


def test_falls_back_to_twitter_image_when_no_og_image():
    html = '<html><head><meta name="twitter:image" content="https://example.com/tw.jpg"></head></html>'
    assert extract_meta_image(html) == "https://example.com/tw.jpg"


def test_og_image_wins_over_twitter_image_when_both_present():
    html = (
        '<html><head>'
        '<meta name="twitter:image" content="https://example.com/tw.jpg">'
        '<meta property="og:image" content="https://example.com/og.jpg">'
        '</head></html>'
    )
    assert extract_meta_image(html) == "https://example.com/og.jpg"


def test_relative_path_image_never_returned():
    html = '<html><head><meta property="og:image" content="/relative/path.jpg"></head></html>'
    assert extract_meta_image(html) is None


def test_data_uri_image_never_returned():
    html = '<html><head><meta property="og:image" content="data:image/png;base64,AAAA"></head></html>'
    assert extract_meta_image(html) is None


def test_no_meta_image_returns_none():
    html = "<html><head><title>No image here</title></head><body>text</body></html>"
    assert extract_meta_image(html) is None


def test_empty_html_returns_none():
    assert extract_meta_image("") is None
    assert extract_meta_image(None) is None


def test_malformed_html_never_crashes():
    html = '<html><head><meta property="og:image" content="https://example.com/a.jpg"><<<broken'
    assert extract_meta_image(html) == "https://example.com/a.jpg"


# ---- enrich_article_image: DB read/write, network injected -------------


def test_found_image_persisted_to_extra_json(conn):
    raw_id = _insert_raw_item(conn, "https://rollingstone.com/real-article")
    result = enrich_article_image(
        conn, raw_id, "https://rollingstone.com/real-article",
        http_get=lambda url: _FakeResponse('<meta property="og:image" content="https://rollingstone.com/img.jpg">'),
    )
    assert result == "https://rollingstone.com/img.jpg"
    row = conn.execute("SELECT extra_json FROM raw_items WHERE id = ?", (raw_id,)).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["image_url"] == "https://rollingstone.com/img.jpg"
    assert extra["image_checked"] is True


def test_no_image_found_still_marks_checked_never_fabricates(conn):
    raw_id = _insert_raw_item(conn, "https://rollingstone.com/no-image-article")
    result = enrich_article_image(
        conn, raw_id, "https://rollingstone.com/no-image-article",
        http_get=lambda url: _FakeResponse("<html><head></head></html>"),
    )
    assert result is None
    row = conn.execute("SELECT extra_json FROM raw_items WHERE id = ?", (raw_id,)).fetchone()
    extra = json.loads(row["extra_json"])
    assert "image_url" not in extra
    assert extra["image_checked"] is True


def test_network_failure_marks_checked_safely_no_crash(conn):
    raw_id = _insert_raw_item(conn, "https://rollingstone.com/unreachable")

    def _raise(url):
        raise ConnectionError("simulated network failure")

    result = enrich_article_image(conn, raw_id, "https://rollingstone.com/unreachable", http_get=_raise)
    assert result is None
    row = conn.execute("SELECT extra_json FROM raw_items WHERE id = ?", (raw_id,)).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["image_checked"] is True


def test_existing_extra_json_keys_preserved(conn):
    raw_id = _insert_raw_item(conn, "https://rollingstone.com/x", extra_json=json.dumps({"some_other_key": "kept"}))
    enrich_article_image(
        conn, raw_id, "https://rollingstone.com/x",
        http_get=lambda url: _FakeResponse('<meta property="og:image" content="https://rollingstone.com/img.jpg">'),
    )
    row = conn.execute("SELECT extra_json FROM raw_items WHERE id = ?", (raw_id,)).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["some_other_key"] == "kept"
    assert extra["image_url"] == "https://rollingstone.com/img.jpg"


# ---- enrich_pending_article_images: idempotent cache/skip logic --------


def test_already_checked_row_never_refetched(conn):
    raw_id = _insert_raw_item(conn, "https://rollingstone.com/y", extra_json=json.dumps({"image_checked": True}))
    calls = []
    result = enrich_pending_article_images(conn, [raw_id], http_get=lambda url: calls.append(url))
    assert result == {"checked": 0, "found": 0}
    assert calls == []


def test_row_with_existing_real_image_never_refetched(conn):
    raw_id = _insert_raw_item(
        conn, "https://rollingstone.com/z", extra_json=json.dumps({"image_url": "https://rollingstone.com/existing.jpg"}),
    )
    calls = []
    result = enrich_pending_article_images(conn, [raw_id], http_get=lambda url: calls.append(url))
    assert result == {"checked": 0, "found": 0}
    assert calls == []


def test_row_with_empty_source_url_skipped_safely(conn):
    raw_id = _insert_raw_item(conn, "")
    result = enrich_pending_article_images(conn, [raw_id])
    assert result == {"checked": 0, "found": 0}


def test_pending_rows_enriched_and_counted(conn):
    id1 = _insert_raw_item(conn, "https://rollingstone.com/a")
    id2 = _insert_raw_item(conn, "https://rollingstone.com/b")

    def fake_get(url):
        if url.endswith("/a"):
            return _FakeResponse('<meta property="og:image" content="https://rollingstone.com/a.jpg">')
        return _FakeResponse("<html><head></head></html>")

    result = enrich_pending_article_images(conn, [id1, id2], http_get=fake_get)
    assert result == {"checked": 2, "found": 1}


def test_empty_id_list_is_a_safe_noop(conn):
    assert enrich_pending_article_images(conn, []) == {"checked": 0, "found": 0}

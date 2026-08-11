"""TEST U, V, W from the Phase 2A test matrix: RSS adapter identity
resolution and published_at handling, driven from a local fixture file —
never a real network call."""

from pathlib import Path
from unittest.mock import patch

from ingestion.adapters import rss
from ingestion.registry import RetryPolicy, SourceConfig

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "rss_sample.xml"


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _rss_source_config():
    return SourceConfig(
        source_name="test_rss_source",
        enabled=True,
        source_type="rss",
        category="AI_NEWS",
        region="GLOBAL",
        endpoint="https://example.com/feed.xml",
        timeout_seconds=10,
        retry=RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, backoff_jitter_seconds=0.5),
        auth_mode="none",
    )


def _fetch_from_fixture():
    content = FIXTURE_PATH.read_bytes()
    with patch("ingestion.adapters.rss.request_with_retry", return_value=_FakeResponse(content)):
        return rss.fetch_source(_rss_source_config())


# ---- TEST U: RSS GUID identity -----------------------------------------------


def test_U_entry_with_guid_uses_guid_as_identity():
    outcome = _fetch_from_fixture()
    by_title = {r.title: r for r in outcome.records}
    assert by_title["Article With GUID"].source_item_key == "urn:example:article-1"


# ---- TEST V: RSS URL fallback identity ---------------------------------------


def test_V_entry_without_guid_uses_canonical_url():
    outcome = _fetch_from_fixture()
    by_title = {r.title: r for r in outcome.records}
    record = by_title["Article Without GUID"]
    assert record.source_item_key == "https://example.com/articles/2?id=2"  # utm_source stripped, tracking-safe
    assert record.source_item_key != record.source_url  # source_url keeps the original, uncanonicalized value


# ---- TEST W: RSS published_at absent -> never faked from collected_at -------


def test_W_missing_pubdate_is_none_not_fabricated():
    outcome = _fetch_from_fixture()
    by_title = {r.title: r for r in outcome.records}
    record = by_title["Article Without PubDate"]
    assert record.published_at is None
    assert record.collected_at is not None
    assert record.collected_at != record.published_at


def test_all_three_fixture_entries_parsed_with_no_errors():
    outcome = _fetch_from_fixture()
    assert len(outcome.records) == 3
    assert outcome.parse_errors == 0


def test_entry_missing_link_counts_as_parse_error_not_crash():
    content = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title><link>https://example.com</link>
<description>d</description>
<item><title>No Link Entry</title><description>desc</description></item>
</channel></rss>"""
    with patch("ingestion.adapters.rss.request_with_retry", return_value=_FakeResponse(content)):
        outcome = rss.fetch_source(_rss_source_config())
    assert outcome.records == []
    assert outcome.parse_errors == 1

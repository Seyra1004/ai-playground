"""RSS/Atom ingestion adapter.

Fetches over ingestion.http (bounded timeout+retry) rather than letting
feedparser fetch the URL itself, so the shared retry/timeout policy always
applies. feedparser then handles the RSS-vs-Atom structural differences
and malformed markup without hand-rolled XML parsing (see Section 29 of
the Phase 2A contract).
"""

import logging
from datetime import datetime, timezone
from html.parser import HTMLParser

import feedparser

from ingestion.http import request_with_retry
from ingestion.identity import resolve_source_item_key
from ingestion.records import AdapterOutcome, IngestionRecord

logger = logging.getLogger(__name__)


class _TextOnlyExtractor(HTMLParser):
    """Collects only the visible text data from an HTML fragment, dropping
    tags/attributes entirely. Used below because feedparser preserves a
    feed's raw <description>/<summary> markup verbatim rather than
    stripping it -- most feeds' descriptions are plain text already (a
    no-op here), but some (Google News' search RSS is the extreme,
    already-seen case) wrap the whole thing in an <a href="...impossibly-
    long-redirect-url...">...</a> with no real excerpt at all. Left
    unstripped, that raw markup -- including the multi-hundred-character
    URL itself -- would reach the dashboard as if it were real body text:
    a display bug (unbreakable text overflows the layout) and a content-
    quality bug (it was never a real summary to begin with) at once."""

    def __init__(self):
        super().__init__()
        self.chunks = []

    def handle_data(self, data):
        self.chunks.append(data)


def _clean_summary(raw_summary):
    """Extracts visible text only. Never invents a summary where the feed
    provided no real prose -- an all-markup description collapses to
    None, same as if the field had been empty."""
    if not raw_summary:
        return raw_summary
    extractor = _TextOnlyExtractor()
    try:
        extractor.feed(raw_summary)
        extractor.close()
    except Exception:
        return raw_summary
    text = " ".join("".join(extractor.chunks).split())
    return text or None


_MIN_PLAUSIBLE_YEAR = 1990  # see _entry_published_at's own docstring


def _entry_published_at(entry):
    """Only a real published/updated timestamp from the feed is used —
    never fabricated from the current time. collected_at (set separately,
    below) is the actual fetch time and must never be confused with this.

    Some real feeds (confirmed: nocutnews.co.kr's category feeds) emit a
    literal placeholder `<updated>Mon, 01 Jan 0001 00:00:00 GMT</updated>`
    sentinel on entries that have no real published date -- feedparser
    parses that into a structurally valid (year=1) struct_time, so a bare
    "did parsing succeed" check isn't enough to catch it. A year below
    _MIN_PLAUSIBLE_YEAR is never a real article date, so it's treated the
    same as no date at all rather than silently trusted -- an implausible
    date is strictly worse than an honest None (see report/candidate_
    selection.py's own "unknown age is never assumed fresh or stale"
    contract, which this feeds into)."""
    parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_struct:
        return None
    if parsed_struct.tm_year < _MIN_PLAUSIBLE_YEAR:
        return None
    try:
        return datetime(*parsed_struct[:6], tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _entry_guid(entry):
    """feedparser normalizes an entry's GUID into `id`. When the feed
    didn't provide a GUID distinct from the link, feedparser sets
    id == link — that's not a distinct stable identity beyond the URL
    itself, so it must not be treated as a GUID (would defeat the
    GUID > URL priority)."""
    entry_id = entry.get("id")
    if entry_id and entry_id != entry.get("link"):
        return entry_id
    return None


def _entry_to_record(source_config, entry, collected_at):
    url = entry.get("link")
    if not url:
        return None  # nothing usable to build source_url/identity from

    guid = _entry_guid(entry)
    title = entry.get("title")
    published_at = _entry_published_at(entry)

    source_item_key = resolve_source_item_key(
        source_name=source_config.source_name,
        guid=guid,
        url=url,
        title=title,
        published_at=published_at,
    )

    return IngestionRecord(
        source_name=source_config.source_name,
        source_item_key=source_item_key,
        source_type=source_config.source_type,
        source_url=url,
        title=title,
        snippet=_clean_summary(entry.get("summary")),
        published_at=published_at,
        collected_at=collected_at,
        region=source_config.region,
    )


def fetch_source(source_config, sleep=None):
    """Fetch and parse an RSS/Atom feed into IngestionRecords. Raises
    ingestion.http.HttpTransientError / HttpClientError on total fetch
    failure — the caller (ingestion.pipeline) is responsible for turning
    that into a FAILED run_source_status row."""
    response = request_with_retry(
        "GET",
        source_config.endpoint,
        source_config.retry,
        source_config.timeout_seconds,
        sleep=sleep,
    )
    collected_at = datetime.now(timezone.utc).isoformat()
    parsed = feedparser.parse(response.content)

    records = []
    parse_errors = 0
    for entry in parsed.entries:
        record = _entry_to_record(source_config, entry, collected_at)
        if record is None:
            parse_errors += 1
            continue
        records.append(record)

    return AdapterOutcome(records=records, parse_errors=parse_errors)

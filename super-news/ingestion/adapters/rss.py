"""RSS/Atom ingestion adapter.

Fetches over ingestion.http (bounded timeout+retry) rather than letting
feedparser fetch the URL itself, so the shared retry/timeout policy always
applies. feedparser then handles the RSS-vs-Atom structural differences
and malformed markup without hand-rolled XML parsing (see Section 29 of
the Phase 2A contract).
"""

import logging
from datetime import datetime, timezone

import feedparser

from ingestion.http import request_with_retry
from ingestion.identity import resolve_source_item_key
from ingestion.records import AdapterOutcome, IngestionRecord

logger = logging.getLogger(__name__)


def _entry_published_at(entry):
    """Only a real published/updated timestamp from the feed is used —
    never fabricated from the current time. collected_at (set separately,
    below) is the actual fetch time and must never be confused with this."""
    parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_struct:
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
        snippet=entry.get("summary"),
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

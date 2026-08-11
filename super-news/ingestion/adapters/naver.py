"""Naver News Search API ingestion adapter.

Credentials are read from the environment (never stored in sources.yaml —
only the env var names are, via SourceConfig.credential_env). HTML
entity/tag cleanup on title/description is limited to source-payload
cleanup (unescape entities, strip the `<b>` highlight tags Naver wraps
matched keywords in) — not general HTML stripping or semantic rewriting
(Section 27 of the Phase 2A contract).
"""

import html
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import logging_setup
from config import get_required_env
from ingestion.http import HttpClientError, request_with_retry
from ingestion.identity import resolve_source_item_key
from ingestion.records import AdapterOutcome, IngestionRecord

logger = logging.getLogger(__name__)

_HTML_HIGHLIGHT_TAG_RE = re.compile(r"</?b>")


def _clean_naver_text(text):
    """Source-payload cleanup only: unescape HTML entities and strip the
    <b>/</b> highlight tags Naver wraps matched keywords in. No further
    HTML parsing, sanitization, or semantic rewriting."""
    if text is None:
        return None
    return html.unescape(_HTML_HIGHLIGHT_TAG_RE.sub("", text)).strip()


def _parse_pub_date(pub_date):
    """Naver's pubDate is RFC 2822 (e.g. 'Mon, 11 Aug 2026 12:00:00 +0900').
    Converted to UTC ISO-8601 when parseable; None (never fabricated) when
    absent or malformed."""
    if not pub_date:
        return None
    try:
        parsed = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _item_to_record(source_config, item, collected_at):
    raw_title = item.get("title")
    # Naver has no official per-article ID/GUID; originallink (the
    # publisher's own URL) is preferred over Naver's own copy link, per
    # the source_item_key priority (Section 11): use the most authoritative
    # URL available before falling back further.
    url = item.get("originallink") or item.get("link")
    if not raw_title or not url:
        return None  # required-field validation failure -> caller counts as a parse error

    title = _clean_naver_text(raw_title)
    snippet = _clean_naver_text(item.get("description"))
    published_at = _parse_pub_date(item.get("pubDate"))

    source_item_key = resolve_source_item_key(
        source_name=source_config.source_name,
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
        snippet=snippet,
        published_at=published_at,
        collected_at=collected_at,
        region=source_config.region,
    )


def fetch_source(source_config, sleep=None):
    """Fetch and parse Naver News Search API results into
    IngestionRecords. Raises ingestion.http.HttpTransientError on
    exhausted transient retries, or HttpClientError (status_code set) on a
    deterministic 4xx — including 401/403, which the caller
    (ingestion.pipeline) classifies as a credential/config failure rather
    than a generic FAILED, without ever logging the credential values
    themselves."""
    client_id_env = source_config.credential_env["client_id"]
    client_secret_env = source_config.credential_env["client_secret"]
    client_id = get_required_env(client_id_env)
    client_secret = get_required_env(client_secret_env)
    logging_setup.register_secret(client_id)
    logging_setup.register_secret(client_secret)

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query": source_config.params.get("query", ""),
        "display": source_config.params.get("display", 10),
        "sort": source_config.params.get("sort", "date"),
    }

    try:
        response = request_with_retry(
            "GET",
            source_config.endpoint,
            source_config.retry,
            source_config.timeout_seconds,
            sleep=sleep,
            headers=headers,
            params=params,
        )
    except HttpClientError:
        # Re-raised as-is: status_code is already attached, and neither
        # client_id/client_secret nor the Authorization-equivalent headers
        # are part of the exception message.
        raise

    try:
        body = response.json()
    except ValueError as exc:
        raise HttpClientError(
            f"Naver News Search API returned a non-JSON body (status={response.status_code}).",
            status_code=response.status_code,
        ) from exc

    items = body.get("items") if isinstance(body, dict) else None
    if items is None:
        raise HttpClientError(
            "Naver News Search API response did not contain an 'items' field.",
            status_code=response.status_code,
        )

    collected_at = datetime.now(timezone.utc).isoformat()
    records = []
    parse_errors = 0
    for item in items:
        record = _item_to_record(source_config, item, collected_at)
        if record is None:
            parse_errors += 1
            continue
        records.append(record)

    return AdapterOutcome(records=records, parse_errors=parse_errors)

"""Best-effort, non-paid article image enrichment (FINAL REAL-CONTENT
ACTIVATION PASS): for a selected primary editorial article whose feed
provided no image metadata at ingestion time, fetch the article's OWN
page (same source_url only -- never a search engine, never a generated/
stock image, never an unrelated photo) and extract ONLY a real og:image/
twitter:image meta tag, if the page actually has one.

Runs as a data-enrichment step over already-ingested raw_items rows,
writing the result into raw_items.extra_json -- the SAME field
report.web_data_v2._extract_trustworthy_image_url already reads for
ingestion/adapters/rss.py's own feed-provided images, so the existing
rendering pipeline picks up an enriched image with zero renderer changes.
Never called from inside HTML rendering -- a page render must never
depend on a live remote fetch.

Idempotent per row: once a row has been checked (found an image or
confirmed none exists), extra_json.image_checked marks that so it is
never re-fetched on a later run, independent of whether an image was
actually found."""

import json
import logging

from html.parser import HTMLParser

from ingestion.http import HttpClientError, HttpTransientError, request_with_retry
from ingestion.registry import RetryPolicy

logger = logging.getLogger(__name__)

# Deliberately small and fixed -- this is a best-effort enrichment fetch
# against an arbitrary publisher page, not a registered ingestion source
# with its own tuned sources.yaml retry policy.
_ENRICHMENT_RETRY_POLICY = RetryPolicy(max_attempts=2, backoff_base_seconds=1.0, backoff_jitter_seconds=0.5)
_ENRICHMENT_TIMEOUT_SECONDS = 10
# Meta tags are always in <head>, near the top of the document -- never
# read/parse a whole large page body just to find them.
_MAX_HTML_CHARS_TO_SCAN = 300_000


class _MetaImageParser(HTMLParser):
    """Extracts the FIRST real og:image, else the first real twitter:image,
    <meta> tag's content attribute -- pure parsing, no network, no
    execution of any script/style content on the page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og_image = None
        self.twitter_image = None

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attr_dict = dict(attrs)
        prop = (attr_dict.get("property") or "").strip().lower()
        name = (attr_dict.get("name") or "").strip().lower()
        content = (attr_dict.get("content") or "").strip()
        if not content:
            return
        if prop == "og:image" and self.og_image is None:
            self.og_image = content
        elif name == "twitter:image" and self.twitter_image is None:
            self.twitter_image = content


def extract_meta_image(html_text):
    """Pure parsing, no network: og:image first, twitter:image second --
    only a real http(s) URL string is ever returned, never a relative
    path/data URI (matches report.web_render_v2._valid_image_url's own
    trust contract, checked again defensively at render time). A
    malformed/incomplete page must never crash enrichment -- returns None,
    the same outcome as "no image found"."""
    if not html_text:
        return None
    parser = _MetaImageParser()
    try:
        parser.feed(html_text[:_MAX_HTML_CHARS_TO_SCAN])
    except Exception:
        return None
    for candidate in (parser.og_image, parser.twitter_image):
        if candidate and candidate.startswith(("http://", "https://")):
            return candidate
    return None


def _merge_extra_json(existing_extra_json, updates):
    try:
        extra = json.loads(existing_extra_json) if existing_extra_json else {}
    except (json.JSONDecodeError, TypeError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    extra.update(updates)
    return json.dumps(extra, ensure_ascii=False)


def enrich_article_image(conn, raw_item_id, source_url, http_get=None):
    """Fetches source_url ONCE (bounded retry/timeout, shared ingestion
    HTTP policy -- a real browser User-Agent, same as every RSS adapter),
    extracts a real og:image/twitter:image if present, and persists the
    result into raw_items.extra_json. Returns the discovered image URL, or
    None if none was found/the page was unreachable -- never raises; a
    network failure is recorded as "checked, no image" exactly like a real
    page with no meta image, so it is never retried on every future run.

    http_get: injectable for tests -- a callable(url) -> an object with a
    real `.text` attribute (matching requests.Response's own shape).
    Defaults to the real ingestion HTTP client; never used to bypass the
    real network path in production."""
    image_url = None
    try:
        if http_get is not None:
            response = http_get(source_url)
        else:
            response = request_with_retry(
                "GET", source_url, _ENRICHMENT_RETRY_POLICY, _ENRICHMENT_TIMEOUT_SECONDS,
            )
        image_url = extract_meta_image(response.text)
    except (HttpTransientError, HttpClientError) as exc:
        logger.info("article image enrichment: %s unreachable (%s)", source_url, type(exc).__name__)
    except Exception:
        logger.warning("article image enrichment: unexpected failure for %s", source_url, exc_info=True)

    row = conn.execute("SELECT extra_json FROM raw_items WHERE id = ?", (raw_item_id,)).fetchone()
    existing = row["extra_json"] if row else None
    updates = {"image_checked": True}
    if image_url:
        updates["image_url"] = image_url
    conn.execute(
        "UPDATE raw_items SET extra_json = ? WHERE id = ?",
        (_merge_extra_json(existing, updates), raw_item_id),
    )
    return image_url


def enrich_pending_article_images(conn, raw_item_ids, http_get=None):
    """Enriches every id in raw_item_ids that doesn't already carry a real
    image_url AND hasn't already been checked -- idempotent, safe to call
    repeatedly (e.g. once per report-generation run) without re-fetching
    already-checked rows or overwriting a real feed-provided image_url.
    Returns {"checked": n, "found": n} counts (n=0 for a genuinely empty/
    already-fully-checked input, never an error)."""
    if not raw_item_ids:
        return {"checked": 0, "found": 0}
    placeholders = ",".join("?" for _ in raw_item_ids)
    rows = conn.execute(
        f"SELECT id, source_url, extra_json FROM raw_items WHERE id IN ({placeholders})",
        list(raw_item_ids),
    ).fetchall()
    checked = 0
    found = 0
    for row in rows:
        try:
            extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
        except (json.JSONDecodeError, TypeError):
            extra = {}
        if not isinstance(extra, dict) or extra.get("image_checked") or extra.get("image_url"):
            continue
        if not row["source_url"]:
            continue
        checked += 1
        if enrich_article_image(conn, row["id"], row["source_url"], http_get=http_get):
            found += 1
    conn.commit()
    return {"checked": checked, "found": found}

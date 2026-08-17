"""Article-page og:image/twitter:image fallback for a SELECTED DAILY/
MUSIC card whose RSS feed provided no media:thumbnail/media:content/
enclosure image at ingestion (see ingestion/adapters/rss.py's own
_entry_image_url -- that real, feed-provided path is completely
unchanged; this is a second, later-stage fallback, never a replacement).

Real article-page metadata only: og:image first, twitter:image only as a
fallback -- never a search engine, a stock/placeholder image, or an
AI-generated one. Reuses the `requests` library already a dependency of
this codebase (see report/release_v2.py's own external-verification GET)
-- no new HTTP framework. Never raises: a fetch/parse failure simply
means no image, exactly like today's existing "no image" behavior when a
feed itself carries none.

Only ever called for an item that is ACTUALLY selected for the final
report (see report.web_data_v2._lookup_item_detail's own call site) --
never for the full raw candidate pool -- and result is cached back into
raw_items.extra_json (report.web_data_v2._enrich_image_from_article_page)
so a given article's page is fetched AT MOST ONCE across every future
render, success or failure alike."""

import re

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]*\scontent=["\']([^"\']+)["\']', re.IGNORECASE
)
_OG_IMAGE_RE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*\sproperty=["\']og:image(?::secure_url)?["\']', re.IGNORECASE
)
_TWITTER_IMAGE_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]*\scontent=["\']([^"\']+)["\']', re.IGNORECASE
)
_TWITTER_IMAGE_RE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*\sname=["\']twitter:image(?::src)?["\']', re.IGNORECASE
)

_FETCH_TIMEOUT_SECONDS = 8
_USER_AGENT = "Mozilla/5.0 (compatible; SuperNewsBot/1.0; +https://seyra1004.github.io/ai-playground)"


def _valid_http_url(url):
    return isinstance(url, str) and bool(url.strip()) and url.strip().startswith(("http://", "https://"))


def extract_og_image_from_html(html_text):
    """Returns the first real og:image URL found in `html_text`, else the
    first real twitter:image URL, else None. Never any other heuristic
    (no largest-image-on-page guess, no first <img> tag, no screenshot)."""
    if not html_text:
        return None
    for pattern in (_OG_IMAGE_RE, _OG_IMAGE_RE_REV):
        match = pattern.search(html_text)
        if match and _valid_http_url(match.group(1)):
            return match.group(1).strip()
    for pattern in (_TWITTER_IMAGE_RE, _TWITTER_IMAGE_RE_REV):
        match = pattern.search(html_text)
        if match and _valid_http_url(match.group(1)):
            return match.group(1).strip()
    return None


def fetch_article_image_url(article_url, http_get=None, timeout=_FETCH_TIMEOUT_SECONDS):
    """Real, read-only GET of the article's OWN page. Returns None on ANY
    failure (network error, non-200, no og:image/twitter:image meta tag)
    -- image enrichment must never break report generation. `http_get`
    (signature (url, timeout) -> object with .status_code/.text) defaults
    to `requests.get`; tests inject a fake so this module never makes a
    real network call under pytest."""
    if not _valid_http_url(article_url):
        return None
    if http_get is None:
        import requests

        def http_get(url, timeout):
            return requests.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT})

    try:
        response = http_get(article_url, timeout)
    except Exception:
        return None
    if getattr(response, "status_code", None) != 200:
        return None
    return extract_og_image_from_html(getattr(response, "text", None))

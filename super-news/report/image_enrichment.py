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
from urllib.parse import urlparse

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

# IMAGE QUALITY GATE (EDITORIAL QUALITY PASS, 2026-08-18, confirmed real
# defect: a TikTok candidate ingested via the `tiktok_music_news_google`
# Google News search-aggregator feed carries a source_url that is a
# news.google.com REDIRECT wrapper, not the real publisher's article --
# Google's own interstitial page requires JavaScript to reach the real
# article, so a plain GET (this module never runs a browser/JS) only ever
# returns Google's OWN page, whose og:image is Google News's generic
# icon/logo, not article content. A missing image is better than a
# misleading one (see module docstring) -- so a known aggregator-redirect
# host is never even fetched; real direct-publisher feeds (Billboard/NME/
# Rolling Stone/Spotify Newsroom/etc) are completely unaffected.
_AGGREGATOR_REDIRECT_HOSTS = ("news.google.com",)
# UNRELIABLE IMAGE HOSTS (EDITORIAL QUALITY PASS, 2026-08-18, confirmed
# real defect via live verification -- see is_unreliable_image_url's own
# docstring): the SAME Google News aggregation path also feeds a
# media:content/media:thumbnail image URL hosted on Google's own image
# proxy (lh3/4/5/6.googleusercontent.com) directly into raw_items.
# extra_json at ingestion time (report.web_data_v2._extract_trustworthy_
# image_url reads it as if it were a normal, trustworthy feed-provided
# image, exactly like a direct publisher's own CDN URL). A live GET of
# one such real URL captured from this pipeline returned HTTP 400 (the
# proxy URL requires a size-suffix query parameter Google News RSS
# entries don't always include) -- so this is not a hypothetical "might
# be a logo" risk, it is a CONFIRMED broken/non-image response, which
# fails the "HTTP 200 AND image/*" requirement outright. Rejected at the
# same domain-allowlist-by-exclusion gate as the redirect-wrapper check
# above, reused by report.web_data_v2._extract_trustworthy_image_url too
# (see is_unreliable_image_url) so the SAME rule protects both the
# RSS-feed-provided image path and the article-page-enrichment fallback
# path, never duplicated as two separate keyword lists.
_UNRELIABLE_IMAGE_HOSTS = _AGGREGATOR_REDIRECT_HOSTS + (
    "lh3.googleusercontent.com", "lh4.googleusercontent.com",
    "lh5.googleusercontent.com", "lh6.googleusercontent.com",
)
# A handful of filename/path fragments that are never real article
# imagery on any publisher's page -- a generic site favicon/logo/share-
# card default, not something "visually/article-context relevant" (see
# module docstring). Deliberately narrow (exact known asset-naming
# patterns only) -- never a broad heuristic that could reject a real
# photo whose filename simply happens to contain an unrelated word.
_GENERIC_ASSET_URL_MARKERS = (
    "favicon", "apple-touch-icon", "default-share-image", "og-image-default",
    "placeholder", "sprite-", "/logo.", "-logo.", "social-share-default",
)


def _valid_http_url(url):
    return isinstance(url, str) and bool(url.strip()) and url.strip().startswith(("http://", "https://"))


def _is_aggregator_redirect_url(url):
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _AGGREGATOR_REDIRECT_HOSTS)


def is_unreliable_image_url(url):
    """Public (no leading underscore -- report.web_data_v2._extract_
    trustworthy_image_url imports this too): True when `url` is hosted on
    a known unreliable-image domain (Google News's own redirect wrapper,
    or Google's lh3-6.googleusercontent.com image proxy -- see
    _UNRELIABLE_IMAGE_HOSTS's own docstring for the confirmed real
    defect). Never a broad heuristic -- exact host allowlist-by-exclusion
    only, so a real publisher's own CDN (including one that happens to
    also be Google-operated infrastructure the publisher legitimately
    uses, e.g. a GCS bucket under the PUBLISHER's own subdomain) is never
    caught by this."""
    if not _valid_http_url(url):
        return False
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _UNRELIABLE_IMAGE_HOSTS)


def _looks_like_generic_asset(url):
    lowered = url.lower()
    return any(marker in lowered for marker in _GENERIC_ASSET_URL_MARKERS)


def extract_og_image_from_html(html_text):
    """Returns the first real og:image URL found in `html_text`, else the
    first real twitter:image URL, else None. Never any other heuristic
    (no largest-image-on-page guess, no first <img> tag, no screenshot).
    IMAGE QUALITY GATE: a candidate matching a known generic-asset
    filename pattern (favicon/site-logo/default-share-card -- see
    _looks_like_generic_asset) is treated the same as no match at all and
    skipped in favor of the next candidate, since a missing image is
    better than a misleading one."""
    if not html_text:
        return None
    for pattern in (_OG_IMAGE_RE, _OG_IMAGE_RE_REV):
        match = pattern.search(html_text)
        if (match and _valid_http_url(match.group(1)) and not _looks_like_generic_asset(match.group(1))
                and not is_unreliable_image_url(match.group(1))):
            return match.group(1).strip()
    for pattern in (_TWITTER_IMAGE_RE, _TWITTER_IMAGE_RE_REV):
        match = pattern.search(html_text)
        if (match and _valid_http_url(match.group(1)) and not _looks_like_generic_asset(match.group(1))
                and not is_unreliable_image_url(match.group(1))):
            return match.group(1).strip()
    return None


def fetch_article_image_url(article_url, http_get=None, timeout=_FETCH_TIMEOUT_SECONDS):
    """Real, read-only GET of the article's OWN page. Returns None on ANY
    failure (network error, non-200, no og:image/twitter:image meta tag)
    -- image enrichment must never break report generation. `http_get`
    (signature (url, timeout) -> object with .status_code/.text) defaults
    to `requests.get`; tests inject a fake so this module never makes a
    real network call under pytest.

    IMAGE QUALITY GATE: a known aggregator-redirect URL (see
    _is_aggregator_redirect_url) is never fetched at all -- a plain GET
    of a Google News redirect wrapper only ever returns Google's OWN
    interstitial page, never the real publisher's article, so its
    og:image would be a generic Google News icon, not real article
    imagery."""
    if not _valid_http_url(article_url):
        return None
    if _is_aggregator_redirect_url(article_url):
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

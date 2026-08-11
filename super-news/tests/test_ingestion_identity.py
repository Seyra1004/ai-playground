"""TEST E-I from the Phase 2A test matrix: source_item_key strict
priority, deterministic fallback, and URL canonicalization / payload_hash
behavior."""

from ingestion.identity import (
    canonicalize_url,
    compute_fallback_fingerprint,
    compute_payload_hash,
    resolve_source_item_key,
)


# ---- TEST E: stable official ID present -> used as source_item_key ---------


def test_E_official_id_used_when_present():
    key = resolve_source_item_key(
        source_name="naver_news",
        official_id="official-123",
        guid="guid-456",
        url="https://example.com/a",
        title="Title",
        published_at="2026-08-11T00:00:00+00:00",
    )
    assert key == "official-123"


# ---- TEST F: GUID present -> takes priority over URL -------------------------


def test_F_guid_takes_priority_over_url():
    key = resolve_source_item_key(
        source_name="rss_source",
        guid="guid-456",
        url="https://example.com/a",
        title="Title",
        published_at="2026-08-11T00:00:00+00:00",
    )
    assert key == "guid-456"


# ---- TEST G: no ID/GUID, URL present -> canonical URL identity -------------


def test_G_url_used_when_no_id_or_guid():
    key = resolve_source_item_key(
        source_name="rss_source",
        url="https://Example.com:443/a/b/?utm_source=x&z=1&a=2",
        title="Title",
        published_at="2026-08-11T00:00:00+00:00",
    )
    assert key == canonicalize_url("https://Example.com:443/a/b/?utm_source=x&z=1&a=2")
    assert key == "https://example.com/a/b?a=2&z=1"


# ---- TEST H: no ID/GUID/usable URL -> deterministic fallback ---------------


def test_H_fallback_used_when_nothing_else_available():
    key = resolve_source_item_key(
        source_name="rss_source",
        title="Title",
        published_at="2026-08-11T00:00:00+00:00",
    )
    expected = compute_fallback_fingerprint("rss_source", None, "Title", "2026-08-11T00:00:00+00:00")
    assert key == expected
    assert len(key) == 64  # sha256 hex digest


# ---- TEST I: same fallback input -> same key across calls/processes --------


def test_I_fallback_is_deterministic_across_calls():
    key1 = resolve_source_item_key(source_name="rss_source", title="T", published_at="2026-08-11T00:00:00+00:00")
    key2 = resolve_source_item_key(source_name="rss_source", title="T", published_at="2026-08-11T00:00:00+00:00")
    assert key1 == key2


def test_fallback_never_uses_builtin_hash_or_random():
    # A deterministic implementation must be independent of PYTHONHASHSEED;
    # cross-verify against a hand-computed sha256 to make sure this isn't
    # secretly routed through Python's randomized hash().
    import hashlib

    expected = hashlib.sha256(
        "source_name=s\x1furl=\x00NULL\x00\x1ftitle=t\x1fpublished_at=\x00NULL\x00".encode("utf-8")
    ).hexdigest()
    assert compute_fallback_fingerprint("s", None, "t", None) == expected


# ---- URL canonicalization: safe transformations only ------------------------


def test_url_canonicalization_lowercases_scheme_and_host():
    assert canonicalize_url("HTTPS://Example.COM/path") == "https://example.com/path"


def test_url_canonicalization_drops_fragment():
    assert canonicalize_url("https://example.com/path#section") == "https://example.com/path"


def test_url_canonicalization_drops_default_port():
    assert canonicalize_url("http://example.com:80/path") == "http://example.com/path"
    assert canonicalize_url("https://example.com:443/path") == "https://example.com/path"


def test_url_canonicalization_drops_trailing_slash_on_bare_path():
    assert canonicalize_url("https://example.com/path/") == "https://example.com/path"
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_url_canonicalization_drops_known_tracking_params_only():
    result = canonicalize_url("https://example.com/path?utm_source=x&id=123&gclid=y")
    assert "utm_source" not in result
    assert "gclid" not in result
    assert "id=123" in result  # meaningful param preserved, never stripped


def test_url_canonicalization_sorts_remaining_query_params():
    a = canonicalize_url("https://example.com/path?b=2&a=1")
    b = canonicalize_url("https://example.com/path?a=1&b=2")
    assert a == b


# ---- payload_hash: diagnostics only, deterministic, not a global identity --


def test_payload_hash_is_deterministic():
    assert compute_payload_hash("a", "b") == compute_payload_hash("a", "b")


def test_payload_hash_differs_on_content_change():
    assert compute_payload_hash("a", "b") != compute_payload_hash("a", "c")


def test_payload_hash_distinguishes_none_from_empty_string():
    assert compute_payload_hash(None) != compute_payload_hash("")

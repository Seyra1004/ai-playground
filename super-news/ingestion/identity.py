"""Identity utilities shared by every adapter: deterministic
source_item_key resolution, minimal-safe URL canonicalization, and
payload_hash computation.

Nothing here is a global identity — raw_items identity is always the DB's
UNIQUE(source_name, source_item_key). payload_hash exists only for
diagnostics/change-detection (see the module docstring on
compute_payload_hash).
"""

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = {"gclid", "fbclid", "igshid", "mc_cid", "mc_eid"}

_FIELD_SEPARATOR = "\x1f"  # unit separator; never appears in real article text
_NULL_MARKER = "\x00NULL\x00"


def canonicalize_url(url):
    """Minimal, SAFE URL canonicalization for identity comparison only —
    the original `source_url` passed to persistence is never replaced by
    this. Applies only transformations that cannot change article
    identity: lowercase scheme/host, drop fragment, drop default port,
    drop a bare trailing slash, drop known tracking params, sort the
    remaining query params. Does not touch query params that could carry
    real article identity (e.g. ?id=, ?article=)."""
    if not url:
        return url
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[: -len(":80")]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]

    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    query_pairs = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAM_NAMES
        and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
    )
    query = urlencode(query_pairs)

    return urlunsplit((scheme, netloc, path, query, ""))


def _canonicalize_component(value):
    if value is None:
        return _NULL_MARKER
    return str(value).strip()


def compute_payload_hash(*parts):
    """Deterministic SHA-256 over a UTF-8, trimmed, unit-separator-joined
    tuple of string parts. NOT a global identity — never used as (or
    substituted for) a UNIQUE constraint. Used for diagnostics / detecting
    that a previously-seen item's content has changed."""
    canonical = _FIELD_SEPARATOR.join(_canonicalize_component(p) for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_fallback_fingerprint(source_name, canonical_url, title, published_at):
    """Deterministic SHA-256 fallback identity — used ONLY when a source
    item has no stable official ID, no GUID, and no usable URL. Never
    random (no uuid4), never Python's built-in hash() (randomized per
    process via PYTHONHASHSEED, not stable across restarts)."""
    return compute_payload_hash(
        "source_name=" + _canonicalize_component(source_name),
        "url=" + _canonicalize_component(canonical_url),
        "title=" + _canonicalize_component(title),
        "published_at=" + _canonicalize_component(published_at),
    )


def resolve_source_item_key(source_name, official_id=None, guid=None, url=None, title=None, published_at=None):
    """Strict-priority deterministic source_item_key:
      1. source-provided stable official ID
      2. RSS/Atom stable GUID
      3. canonical original article URL
      4. deterministic SHA-256 fallback fingerprint
    A higher-priority identity, once present, is always used — lower
    fallbacks are never consulted once a higher one is available."""
    if official_id:
        return official_id
    if guid:
        return guid
    canonical_url = canonicalize_url(url) if url else None
    if canonical_url:
        return canonical_url
    return compute_fallback_fingerprint(source_name, canonical_url, title, published_at)

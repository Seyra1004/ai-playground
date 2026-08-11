"""RAW -> NORMALIZED FACT (Phase 2C).

Turns a raw_items row into a normalized_items row without ever performing
semantic inference: no LLM, no NER, no importance/sentiment judgment, no
music-entity resolution. normalized_title must remain meaning-equivalent
to the original title (or, if the title itself is unusable, the
snippet) — only markup/whitespace/encoding cleanup is applied.

Category provenance: raw_items.category is an ingestion-time snapshot of
SourceConfig.category (see ingestion/persistence.py's save_raw_items()),
so a later sources.yaml edit can never change an already-collected item's
classification. The CURRENTLY loaded source registry is consulted only as
a legacy fallback for rows collected before this column existed
(raw_items.category IS NULL) — never to override a category a raw row
already has. If the fallback is needed and the source_name no longer
exists in the registry, the item is REJECTED rather than guessed — see
normalize_raw_item().
"""

import hashlib
import html
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from ingestion.identity import canonicalize_url

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# Naver's News Search API is documented/scoped to Korean-language results
# — this is a property of the SOURCE ITSELF (a fixed, deterministic fact
# about what that API returns), not content-based language detection, so
# it's a safe rule to encode directly. No other source_type currently
# gets a language value; anything else is genuinely unknown and is
# stored as NULL rather than guessed (Section 7 of the Phase 2C contract).
_SOURCE_TYPE_LANGUAGE = {
    "naver_news_api": "ko",
}


def clean_html_text(text):
    """Source-payload cleanup only: strip HTML tags, unescape entities,
    NFC-normalize, collapse whitespace. Never rewrites meaning — no
    summarization, truncation, or judgment calls. Returns None for
    None/whitespace-only/empty-after-cleanup input."""
    if text is None:
        return None
    stripped = _TAG_RE.sub(" ", text)
    unescaped = html.unescape(stripped)
    normalized = unicodedata.normalize("NFC", unescaped)
    collapsed = _WHITESPACE_RE.sub(" ", normalized).strip()
    return collapsed or None


def normalize_title(raw_title, raw_snippet):
    """normalized_title source-of-truth: the cleaned raw title if it
    yields usable text, else the cleaned snippet. Returns None only when
    NEITHER yields anything usable — callers treat that as REJECTED, not
    as a fabricated placeholder title."""
    return clean_html_text(raw_title) or clean_html_text(raw_snippet)


def determine_language(source_type):
    """Deterministic, source-characteristic-based only — see the module
    docstring. Never a content-detection model/dependency."""
    return _SOURCE_TYPE_LANGUAGE.get(source_type)


def determine_entity(extra_json):
    """V1 never performs NER/LLM/music-entity inference — only a source
    that has ALREADY structured an explicit entity into raw_items.extra_json
    would be used here. No current adapter (rss, naver_news_api) populates
    that field, so this is always (None, None) today. Kept as an explicit
    function, not inlined, so a future structured source has one clear
    place to plug into without this code silently guessing anything now."""
    return None, None


def compute_title_fingerprint(normalized_title):
    """Deterministic SHA-256 over a case-insensitive, whitespace-
    collapsed, NFC-normalized form of the title — used for EXACT (never
    semantic/fuzzy) title matching. Never Python's hash() (randomized per
    process via PYTHONHASHSEED), never random."""
    matching_form = unicodedata.normalize("NFC", normalized_title).casefold()
    matching_form = _WHITESPACE_RE.sub(" ", matching_form).strip()
    return hashlib.sha256(matching_form.encode("utf-8")).hexdigest()


def resolve_event_key(source_url, normalized_title):
    """Deterministic, conservative event_key — NON-unique by design (many
    normalized_items may legitimately share one event_key; see
    normalized_items.event_key's index, not a UNIQUE constraint).

    Priority: an exact canonical-URL match is the highest-confidence
    "same content" signal available without semantic clustering, and
    every raw_item always has a source_url (raw_items.source_url is
    NOT NULL), so this is the primary signal in practice — reusing Phase
    2A's canonicalize_url() rather than a new URL-normalization routine.
    The title-fingerprint fallback exists for architectural completeness
    (a future source type without a real per-article URL) and is
    independently deterministic/tested on its own.

    Ambiguous cases are never forced together: two different URLs with
    different titles simply get different event_keys — false split is
    preferred over false merge (Section 10 of the Phase 2C contract)."""
    canonical_url = canonicalize_url(source_url) if source_url else None
    if canonical_url:
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        return f"url:{digest}"
    return f"title:{compute_title_fingerprint(normalized_title)}"


@dataclass(frozen=True)
class NormalizationOutcome:
    status: str  # "normalized" | "already_normalized" | "rejected" | "failed"
    raw_item_id: int
    normalized_item_id: int = None
    reason: str = None


def _existing_normalized_id(conn, raw_item_id):
    """Application-level idempotency check — normalized_items.raw_item_id
    has no UNIQUE constraint (Phase 2C is not permitted to add one), so
    re-normalizing the same raw_item is prevented here, not by the DB."""
    row = conn.execute(
        "SELECT id FROM normalized_items WHERE raw_item_id = ?", (raw_item_id,)
    ).fetchone()
    return row["id"] if row is not None else None


def normalize_raw_item(conn, raw_item, registry):
    """Normalize one raw_items row (a sqlite3.Row or equivalent mapping
    with id/source_name/source_type/source_url/title/snippet/extra_json)
    into one normalized_items row. `registry` is the currently loaded
    dict[source_name -> SourceConfig] (ingestion.registry.load_source_registry
    output), used only to resolve category — see the module docstring.

    Idempotent: a raw_item_id that already has a normalized_items row
    returns "already_normalized" without inserting a duplicate. Rejects
    (no row created, no error) when normalization is legitimately
    impossible from available data. Isolates unexpected failures per-item
    — never raises out of this function; the caller's batch loop is never
    at risk from one bad item."""
    try:
        existing_id = _existing_normalized_id(conn, raw_item["id"])
        if existing_id is not None:
            return NormalizationOutcome(
                status="already_normalized", raw_item_id=raw_item["id"], normalized_item_id=existing_id
            )

        category = raw_item["category"]
        if category is None:
            # Legacy fallback ONLY — a raw row that already has a category
            # (the normal case since Category Provenance Correction) is
            # never re-resolved against the current registry, so a later
            # sources.yaml edit can't change an already-collected item's
            # classification.
            source_config = registry.get(raw_item["source_name"])
            if source_config is None:
                return NormalizationOutcome(
                    status="rejected", raw_item_id=raw_item["id"],
                    reason=(
                        f"no category on raw row and source_name "
                        f"{raw_item['source_name']!r} not found in current registry"
                    ),
                )
            category = source_config.category

        normalized_title = normalize_title(raw_item["title"], raw_item["snippet"])
        if normalized_title is None:
            return NormalizationOutcome(
                status="rejected", raw_item_id=raw_item["id"],
                reason="no usable title or snippet after cleanup",
            )

        event_key = resolve_event_key(raw_item["source_url"], normalized_title)
        language = determine_language(raw_item["source_type"])
        entity_type, entity_name = determine_entity(raw_item["extra_json"])
        created_at = datetime.now(timezone.utc).isoformat()

        cursor = conn.execute(
            """INSERT INTO normalized_items
               (raw_item_id, category, event_key, entity_type, entity_name,
                normalized_title, language, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                raw_item["id"], category, event_key, entity_type,
                entity_name, normalized_title, language, created_at,
            ),
        )
        conn.commit()
        return NormalizationOutcome(
            status="normalized", raw_item_id=raw_item["id"], normalized_item_id=cursor.lastrowid
        )
    except Exception as exc:
        conn.rollback()
        logger.error("raw_item_id=%s normalization FAILED: %s", raw_item["id"], type(exc).__name__)
        return NormalizationOutcome(
            status="failed", raw_item_id=raw_item["id"], reason=f"unexpected error: {type(exc).__name__}"
        )


def normalize_batch(conn, registry, raw_item_ids=None):
    """Normalize a set of raw_items (or all of them if raw_item_ids is
    None) in deterministic raw_items.id ASC order. One item's failure
    never affects the others — each goes through normalize_raw_item's own
    small transaction, never one giant batch transaction."""
    if raw_item_ids is None:
        rows = conn.execute("SELECT * FROM raw_items ORDER BY id ASC").fetchall()
    else:
        placeholders = ",".join("?" for _ in raw_item_ids)
        rows = conn.execute(
            f"SELECT * FROM raw_items WHERE id IN ({placeholders}) ORDER BY id ASC",
            tuple(raw_item_ids),
        ).fetchall()

    outcomes = [normalize_raw_item(conn, row, registry) for row in rows]
    counts = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    logger.info("normalize_batch complete: %s", counts)
    return outcomes

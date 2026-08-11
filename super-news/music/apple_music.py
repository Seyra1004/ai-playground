"""Apple Music KR most-played songs -> music_entities / music_entity_aliases
/ music_observations vertical slice.

Scope, deliberately narrow (see the Apple KR Observation Vertical Slice
contract this satisfies): SOURCE=APPLE_MUSIC, MARKET=KR,
CHART=MOST_PLAYED_SONGS, LIMIT=25, metric=apple_music_chart_position only.
No other chart type, market, or metric is implemented here.

Identity: Apple's own song-level `id` (APPLE_MUSIC_ID alias) is the sole
identity signal -- no artist/title fuzzy matching, no MusicBrainz/ISRC
enrichment (out of scope for this slice).

evidence_type mapping: music_observations.evidence_type only accepts
'MEASURED_PLATFORM_SIGNAL' or 'REPORTED_PLATFORM_SIGNAL' (frozen CHECK).
A chart position read directly from Apple's own official feed is Apple's
own direct report of its data -- no intermediary -- so it maps to
MEASURED_PLATFORM_SIGNAL, not a new schema value.

observed_at/collected_at: Apple's feed provides no historical measurement
timestamp, only a current snapshot -- both are set to the time SUPER NEWS
retrieved this snapshot (never release_date, never invented).
"""

import logging
import sqlite3
from datetime import datetime, timezone

from ingestion.http import request_with_retry
from ingestion.registry import RetryPolicy

logger = logging.getLogger(__name__)

SOURCE_NAME = "apple_music"
MARKET = "KR"
CHART_LIMIT = 25
FEED_URL = f"https://rss.marketingtools.apple.com/api/v2/kr/music/most-played/{CHART_LIMIT}/songs.json"
METRIC_NAME = "apple_music_chart_position"

_TIMEOUT_SECONDS = 10
_RETRY_POLICY = RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, backoff_jitter_seconds=0.5)


def fetch_kr_most_played(sleep=None):
    """Fetch the current Apple Music KR most-played songs snapshot via the
    public, no-auth Marketing Tools feed. Returns the parsed song list in
    chart order (index 0 = position 1). Raises ingestion.http's
    HttpTransientError/HttpClientError on fetch failure, or ValueError if
    the response doesn't have the expected shape."""
    response = request_with_retry("GET", FEED_URL, _RETRY_POLICY, _TIMEOUT_SECONDS, sleep=sleep)
    body = response.json()
    results = body.get("feed", {}).get("results")
    if results is None:
        raise ValueError("Apple Music feed response did not contain feed.results.")
    return results


def _resolve_or_create_entity(conn, apple_id, artist_name, song_name, observed_at):
    existing = conn.execute(
        "SELECT music_entity_id FROM music_entity_aliases WHERE alias_type='APPLE_MUSIC_ID' AND alias_value=?",
        (apple_id,),
    ).fetchone()
    if existing is not None:
        return existing["music_entity_id"]

    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', ?, ?)""",
        (artist_name, song_name, observed_at, SOURCE_NAME),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_entity_aliases
           (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, 'APPLE_MUSIC_ID', ?, ?, ?)""",
        (entity_id, apple_id, SOURCE_NAME, observed_at),
    )
    return entity_id


def collect_kr_most_played_observations(conn, songs, observed_at=None):
    """Persist one apple_music_chart_position observation per song, where
    the position is the song's absolute 1-indexed order in `songs`
    (already the official KR MOST_PLAYED_SONGS chart order). Idempotent:
    retrying the exact same observed_at snapshot inserts nothing new for a
    song already recorded at that observed_at (caught via the existing
    music_observations UNIQUE constraint, not an app-level guess); a
    different observed_at (a later day's snapshot) always creates a new
    observation.

    Returns a list of per-song outcome dicts for caller logging."""
    if observed_at is None:
        observed_at = datetime.now(timezone.utc).isoformat()
    collected_at = observed_at

    outcomes = []
    for position, song in enumerate(songs[:CHART_LIMIT], start=1):
        apple_id = song.get("id")
        artist_name = song.get("artistName")
        song_name = song.get("name")
        if not apple_id or not song_name:
            outcomes.append({"position": position, "status": "rejected", "reason": "missing id/name"})
            continue

        entity_id = _resolve_or_create_entity(conn, apple_id, artist_name, song_name, observed_at)

        try:
            conn.execute(
                """INSERT INTO music_observations
                   (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
                    unit, region, evidence_type, observed_at, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, None, SOURCE_NAME, METRIC_NAME, position,
                    "chart_position", MARKET, "MEASURED_PLATFORM_SIGNAL", observed_at, collected_at,
                ),
            )
            conn.commit()
            outcomes.append({"position": position, "apple_id": apple_id, "entity_id": entity_id, "status": "inserted"})
        except sqlite3.IntegrityError:
            conn.rollback()
            outcomes.append({"position": position, "apple_id": apple_id, "entity_id": entity_id, "status": "duplicate_snapshot"})

    logger.info(
        "apple_music KR most-played snapshot processed: %d inserted, %d duplicate, %d rejected",
        sum(1 for o in outcomes if o["status"] == "inserted"),
        sum(1 for o in outcomes if o["status"] == "duplicate_snapshot"),
        sum(1 for o in outcomes if o["status"] == "rejected"),
    )
    return outcomes

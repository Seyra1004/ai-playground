"""Spotify official chart data -- Layer 1 of the Spotify collector.

Uses the same public, no-auth JSON service that powers charts.spotify.com's
own frontend (verified by direct HTTP probe on 2026-08-13, no credentials
needed). This is an UNOFFICIAL, UNDOCUMENTED service with no public API
contract -- Spotify could change its shape or availability without notice.
Treat fetch failures as an ordinary degraded-source outcome, exactly like
any other collector.

V1 scope, deliberately narrow (mirrors music/apple_music.py's own "Apple KR
Observation Vertical Slice" precedent -- CHART_LIMIT, one region, one
metric). Confirmed via direct probing on 2026-08-13:
- the bare endpoint (no query params) returns GLOBAL / WEEKLY / TOP_TRACK
  with real rank, previousRank, entryStatus, and per-track metadata
  (trackName, trackUri, artist name+uri) -- no auth required.
- country-specific (e.g. KR) and daily-cadence chart variants exist on the
  frontend (a "regional-kr-daily" page returns 200) but their backend query
  parameters could not be determined from public probing within a bounded
  effort -- NOT implemented here, not guessed.
- stream counts are NOT present in this response shape -- NOT implemented.
- a Viral 50 chart URL redirected during probing -- its current alias is
  unconfirmed -- NOT implemented.
Polling this WEEKLY-cadence chart daily is still safe: unchanged snapshots
are naturally deduplicated by music_observations' existing UNIQUE
constraint (same pattern Apple Music's collector already relies on) --
day-over-day rank deltas simply won't appear until the underlying chart
itself updates.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from ingestion.http import request_with_retry
from ingestion.registry import RetryPolicy
from music.entity_resolution import resolve_existing_entity

logger = logging.getLogger(__name__)

SOURCE_NAME = "spotify_chart"
REGION = "GLOBAL"
CHART_LIMIT = 10  # Spotify TOP 10, per the locked V2 requirement
METRIC_NAME = "spotify_chart_rank"
CHARTS_API_URL = "https://charts-spotify-com-service.spotify.com/public/v0/charts"

_TIMEOUT_SECONDS = 10
_RETRY_POLICY = RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, backoff_jitter_seconds=0.5)
_SPOTIFY_TRACK_URI_PREFIX = "spotify:track:"


def fetch_global_top_tracks(sleep=None):
    """Fetch the current default chart response (confirmed: GLOBAL, WEEKLY,
    TOP_TRACK) from Spotify's own public, no-auth chart-data service.
    Returns (entries, chart_date) where entries is the parsed entry list in
    rank order and chart_date is the chart's own reported "latestDate"
    (str, 'YYYY-MM-DD') -- more accurate provenance than a fetch-time
    timestamp, since the chart itself is only as fresh as its last update.
    Raises ingestion.http's HttpTransientError/HttpClientError on fetch
    failure, or ValueError if the response doesn't have the expected shape."""
    response = request_with_retry("GET", CHARTS_API_URL, _RETRY_POLICY, _TIMEOUT_SECONDS, sleep=sleep)
    body = response.json()
    responses = body.get("chartEntryViewResponses")
    if not responses:
        raise ValueError("Spotify charts response did not contain chartEntryViewResponses.")
    chart = responses[0]
    entries = chart.get("entries")
    if entries is None:
        raise ValueError("Spotify charts response did not contain entries.")
    dimensions = chart.get("displayChart", {}).get("chartMetadata", {}).get("dimensions", {})
    chart_date = dimensions.get("latestDate")
    return entries, chart_date


def _extract_spotify_track_id(track_uri):
    if not track_uri or not track_uri.startswith(_SPOTIFY_TRACK_URI_PREFIX):
        return None
    return track_uri[len(_SPOTIFY_TRACK_URI_PREFIX):]


def _resolve_or_create_entity(conn, spotify_track_id, artist_name, track_name, observed_at):
    existing = conn.execute(
        "SELECT music_entity_id FROM music_entity_aliases WHERE alias_type='SPOTIFY_ID' AND alias_value=?",
        (spotify_track_id,),
    ).fetchone()
    if existing is not None:
        return existing["music_entity_id"]

    # Not a track this collector has seen before -- check whether ANOTHER
    # source already created an entity for the same recording (music/
    # entity_resolution.py) before creating a duplicate. UNRESOLVED falls
    # through to the exact same new-entity path this always used.
    resolution = resolve_existing_entity(conn, artist_name, track_name)
    if resolution["entity_id"] is not None:
        entity_id = resolution["entity_id"]
        conn.execute(
            """INSERT INTO music_entity_aliases
               (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
               VALUES (?, 'SPOTIFY_ID', ?, ?, ?)""",
            (entity_id, spotify_track_id, SOURCE_NAME, observed_at),
        )
        return entity_id

    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', ?, ?)""",
        (artist_name, track_name, observed_at, SOURCE_NAME),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_entity_aliases
           (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, 'SPOTIFY_ID', ?, ?, ?)""",
        (entity_id, spotify_track_id, SOURCE_NAME, observed_at),
    )
    return entity_id


def collect_global_top_tracks_observations(conn, entries, chart_date, observed_at=None):
    """Persist up to CHART_LIMIT chart_rank observations, one per track, in
    rank order. Identity resolution is keyed on the track's own Spotify ID
    (via a SPOTIFY_ID alias) -- never fuzzy artist/title matching -- so a
    remix/sped-up/slowed version with its own distinct Spotify track ID
    always gets its own distinct music_entities row, never silently merged
    with the original. Idempotent: retrying the same chart snapshot inserts
    nothing new (caught via the existing music_observations UNIQUE
    constraint). Returns a list of per-track outcome dicts for caller
    logging."""
    if observed_at is None:
        if chart_date:
            observed_at = datetime.fromisoformat(chart_date).replace(tzinfo=timezone.utc).isoformat()
        else:
            observed_at = datetime.now(timezone.utc).isoformat()
    collected_at = datetime.now(timezone.utc).isoformat()

    outcomes = []
    for position, entry in enumerate(entries[:CHART_LIMIT], start=1):
        track_meta = entry.get("trackMetadata") or {}
        track_uri = track_meta.get("trackUri")
        spotify_id = _extract_spotify_track_id(track_uri)
        track_name = track_meta.get("trackName")
        artists = track_meta.get("artists") or []
        artist_name = artists[0]["name"] if artists else None
        rank = entry.get("chartEntryData", {}).get("currentRank") or position

        if not spotify_id or not track_name:
            outcomes.append({"position": position, "status": "rejected", "reason": "missing id/name"})
            continue

        entity_id = _resolve_or_create_entity(conn, spotify_id, artist_name, track_name, observed_at)

        try:
            conn.execute(
                """INSERT INTO music_observations
                   (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
                    unit, region, evidence_type, observed_at, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, None, SOURCE_NAME, METRIC_NAME, rank,
                    "chart_rank", REGION, "MEASURED_PLATFORM_SIGNAL", observed_at, collected_at,
                ),
            )
            conn.commit()
            outcomes.append({"position": position, "spotify_id": spotify_id, "entity_id": entity_id, "status": "inserted"})
        except sqlite3.IntegrityError:
            conn.rollback()
            outcomes.append({"position": position, "spotify_id": spotify_id, "entity_id": entity_id, "status": "duplicate_snapshot"})

    logger.info(
        "spotify_chart GLOBAL top-%d snapshot processed: %d inserted, %d duplicate, %d rejected",
        CHART_LIMIT,
        sum(1 for o in outcomes if o["status"] == "inserted"),
        sum(1 for o in outcomes if o["status"] == "duplicate_snapshot"),
        sum(1 for o in outcomes if o["status"] == "rejected"),
    )
    return outcomes

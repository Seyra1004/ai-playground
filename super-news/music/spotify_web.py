"""Spotify Web API canonical-metadata enrichment -- Layer 2 of the Spotify
collector, distinct from music/spotify_chart.py (chart RANK observations,
no auth).

This layer enriches an already-identified Spotify entity (created by the
chart collector or a future collector) with canonical metadata: artist,
album, release date where available, ISRC, and the canonical Spotify track
URL. Requires a Spotify Developer app (Client Credentials flow) --
SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.

V1 scope, deliberately conservative:
- Never creates a new music_entities row on its own -- only enriches an
  entity that already has a SPOTIFY_ID alias. Web-API search-based track
  discovery risks fuzzy-matching the wrong recording (e.g. a remix) to an
  existing entity, which is exactly what must never happen.
- release_date is fetched but NOT persisted -- music_entities has no
  release_date column (a known, flagged schema gap from the V2 design
  research; adding one needs approval, not decided here). Returned to the
  caller for logging/future use only.
- ISRC, once fetched, is stored as an ISRC alias (already a valid
  music_entity_aliases.alias_type) -- only if one isn't already recorded
  for that entity, never overwriting an existing value.
- Absence of SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET is treated as "this
  layer is not yet available", never as an error -- matches the existing
  news-pipeline convention (report/orchestrator.py) of never requiring an
  unconfigured credential.
"""

import base64
import logging
from datetime import datetime, timezone

from config import get_optional_env
from ingestion.http import HttpClientError, HttpTransientError, request_with_retry
from ingestion.registry import RetryPolicy

logger = logging.getLogger(__name__)

SOURCE_NAME = "spotify_web"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TRACK_URL_TEMPLATE = "https://api.spotify.com/v1/tracks/{track_id}"

_TIMEOUT_SECONDS = 10
_RETRY_POLICY = RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, backoff_jitter_seconds=0.5)


class SpotifyWebNotConfiguredError(RuntimeError):
    """Raised when SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET aren't both set.
    Callers must treat this as "layer unavailable", not a failure --
    matching report/orchestrator.py's "zero-candidate day must never
    require an unconfigured credential" convention."""


def credentials_configured():
    return bool(get_optional_env("SPOTIFY_CLIENT_ID")) and bool(get_optional_env("SPOTIFY_CLIENT_SECRET"))


def get_client_credentials_token(sleep=None):
    """Client Credentials OAuth flow -- app-level auth, no user login.
    Raises SpotifyWebNotConfiguredError if credentials aren't set (checked
    BEFORE any network call). Raises HttpTransientError/HttpClientError on
    an ordinary request failure."""
    client_id = get_optional_env("SPOTIFY_CLIENT_ID")
    client_secret = get_optional_env("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SpotifyWebNotConfiguredError(
            "SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not both configured."
        )

    basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("ascii")).decode("ascii")
    response = request_with_retry(
        "POST", TOKEN_URL, _RETRY_POLICY, _TIMEOUT_SECONDS, sleep=sleep,
        headers={"Authorization": f"Basic {basic_auth}"},
        data={"grant_type": "client_credentials"},
    )
    body = response.json()
    access_token = body.get("access_token")
    if not access_token:
        raise ValueError("Spotify token response did not contain access_token.")
    return access_token


def fetch_track_metadata(spotify_track_id, access_token, sleep=None):
    """Returns a dict: {artist, album, release_date, isrc, canonical_url}.
    Any field Spotify doesn't supply for this track is None -- never
    guessed or fabricated. Raises HttpTransientError/HttpClientError on an
    ordinary request failure."""
    response = request_with_retry(
        "GET", TRACK_URL_TEMPLATE.format(track_id=spotify_track_id), _RETRY_POLICY, _TIMEOUT_SECONDS,
        sleep=sleep, headers={"Authorization": f"Bearer {access_token}"},
    )
    body = response.json()
    artists = body.get("artists") or []
    album = body.get("album") or {}
    external_ids = body.get("external_ids") or {}
    external_urls = body.get("external_urls") or {}
    return {
        "artist": artists[0]["name"] if artists else None,
        "album": album.get("name"),
        "release_date": album.get("release_date"),
        "isrc": external_ids.get("isrc"),
        "canonical_url": external_urls.get("spotify"),
    }


def enrich_entity(conn, music_entity_id, spotify_track_id, access_token, sleep=None):
    """Fetches canonical metadata for spotify_track_id and, if an ISRC is
    returned and this entity doesn't already have one recorded, adds an
    ISRC alias. Never overwrites an existing ISRC alias for this entity
    (first-recorded wins, consistent with the project's existing
    refresh-token-retention pattern for "don't clobber a good value with a
    possibly-different one"). Returns the fetched metadata dict regardless
    (release_date/album/canonical_url are not persisted in V1 -- see
    module docstring)."""
    metadata = fetch_track_metadata(spotify_track_id, access_token, sleep=sleep)

    isrc = metadata.get("isrc")
    if isrc:
        existing = conn.execute(
            "SELECT 1 FROM music_entity_aliases WHERE music_entity_id = ? AND alias_type = 'ISRC'",
            (music_entity_id,),
        ).fetchone()
        if existing is None:
            confirmed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO music_entity_aliases
                   (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
                   VALUES (?, 'ISRC', ?, ?, ?)""",
                (music_entity_id, isrc, SOURCE_NAME, confirmed_at),
            )
            conn.commit()

    return metadata

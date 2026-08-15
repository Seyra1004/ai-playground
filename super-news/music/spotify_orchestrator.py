"""Spotify daily collection entry point -- runs both Spotify layers under
one daily run, same shape as music/orchestrator.py's Apple KR wrapper.

Layer 1 (chart, music/spotify_chart.py) always runs -- no auth needed.
Layer 2 (Web API enrichment, music/spotify_web.py) only runs if
SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are configured; its absence is
recorded as a normal SKIPPED source outcome, never a failure -- this
project has no Spotify Developer app registered yet, so Layer 2 is
expected to be skipped in every run until that separate, still-pending
step happens.
"""

import logging
from datetime import datetime, timedelta, timezone

from ingestion.http import HttpClientError, HttpTransientError
from ingestion.orchestrator import finalize_run, start_run
from ingestion.persistence import record_run_source_status
from music.spotify_chart import (
    collect_global_top_tracks_observations,
    fetch_global_top_tracks,
)
from music.spotify_web import (
    SpotifyWebNotConfiguredError,
    credentials_configured,
    enrich_entity,
    get_client_credentials_token,
)

logger = logging.getLogger(__name__)

CATEGORY = "MUSIC"

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))


def run_spotify_collection(conn, run_id, run_date=None, sleep=None):
    """Runs one Spotify collection as its own daily run: start_run -> chart
    fetch+persist -> optional Web API enrichment of newly-inserted entities
    -> record_run_source_status (one row per layer) -> finalize_run.

    Returns a dict describing the outcome; never raises for an ordinary
    fetch/response/persistence failure on either layer -- those are
    recorded as FAILED/SKIPPED run_source_status rows instead. Only
    start_run's own GlobalFailureError subclasses propagate."""
    effective_run_date = run_date or datetime.now(_KST).strftime("%Y-%m-%d")
    runs_row_id = start_run(conn, run_id, effective_run_date, registry_hash=None)

    chart_started_at = datetime.now(timezone.utc).isoformat()
    chart_status, chart_items, chart_failure, new_entity_ids = _collect_chart(conn, sleep)
    chart_finished_at = datetime.now(timezone.utc).isoformat()
    record_run_source_status(
        conn, run_id=runs_row_id, category=CATEGORY, source_name="spotify_chart",
        status=chart_status, started_at=chart_started_at, finished_at=chart_finished_at,
        items_collected=chart_items, retry_count=0, failure_reason=chart_failure,
    )
    conn.commit()

    web_started_at = datetime.now(timezone.utc).isoformat()
    web_status, web_items, web_failure = _enrich_web(conn, new_entity_ids, sleep)
    web_finished_at = datetime.now(timezone.utc).isoformat()
    record_run_source_status(
        conn, run_id=runs_row_id, category=CATEGORY, source_name="spotify_web",
        status=web_status, started_at=web_started_at, finished_at=web_finished_at,
        items_collected=web_items, retry_count=0, failure_reason=web_failure,
    )
    conn.commit()

    logger.info(
        "source=spotify_chart status=%s items_collected=%d; source=spotify_web status=%s items_collected=%d",
        chart_status, chart_items, web_status, web_items,
    )

    source_results = [
        {"source_name": "spotify_chart", "category": CATEGORY, "status": chart_status,
         "items_collected": chart_items, "failure_reason": chart_failure},
        {"source_name": "spotify_web", "category": CATEGORY, "status": web_status,
         "items_collected": web_items, "failure_reason": web_failure},
    ]
    final_status = finalize_run(conn, runs_row_id, source_results)
    return {
        "run_id": run_id,
        "runs_row_id": runs_row_id,
        "status": final_status,
        "source_results": source_results,
    }


def _collect_chart(conn, sleep):
    """Returns (status, items_collected, failure_reason, new_entity_ids)."""
    try:
        entries, chart_date = fetch_global_top_tracks(sleep=sleep)
    except (HttpTransientError, HttpClientError) as exc:
        return "FAILED", 0, f"FETCH_FAILED: {type(exc).__name__}", []
    except ValueError as exc:
        return "FAILED", 0, f"INVALID_RESPONSE: {exc}", []

    if not entries:
        return "FAILED", 0, "INVALID_RESPONSE: empty chart response", []

    try:
        outcomes = collect_global_top_tracks_observations(conn, entries, chart_date)
    except Exception as exc:
        return "FAILED", 0, f"PERSISTENCE_FAILED: {type(exc).__name__}", []

    inserted = [o for o in outcomes if o["status"] == "inserted"]
    duplicate = sum(1 for o in outcomes if o["status"] == "duplicate_snapshot")
    rejected = sum(1 for o in outcomes if o["status"] == "rejected")
    new_entity_ids = [(o["entity_id"], o["spotify_id"]) for o in inserted]

    if len(inserted) + duplicate == 0:
        return "FAILED", 0, f"PERSISTENCE_FAILED: all {rejected} entries rejected", []
    if rejected > 0:
        return "PARTIAL", len(inserted), f"{rejected} entr{'y' if rejected == 1 else 'ies'} rejected", new_entity_ids
    return "SUCCESS", len(inserted), None, new_entity_ids


def _enrich_web(conn, new_entity_ids, sleep):
    """Returns (status, items_enriched, failure_reason). SKIPPED (not
    FAILED) when credentials aren't configured -- this is an expected,
    normal outcome until a Spotify Developer app is registered, not an
    error condition."""
    if not credentials_configured():
        return "SKIPPED", 0, "SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET not configured"
    if not new_entity_ids:
        return "SKIPPED", 0, "no newly-inserted entities to enrich"

    try:
        access_token = get_client_credentials_token(sleep=sleep)
    except SpotifyWebNotConfiguredError as exc:
        return "SKIPPED", 0, str(exc)
    except (HttpTransientError, HttpClientError) as exc:
        return "FAILED", 0, f"AUTH_FAILED: {type(exc).__name__}"

    enriched = 0
    failures = 0
    for entity_id, spotify_id in new_entity_ids:
        try:
            enrich_entity(conn, entity_id, spotify_id, access_token, sleep=sleep)
            enriched += 1
        except (HttpTransientError, HttpClientError):
            failures += 1

    if enriched == 0 and failures > 0:
        return "FAILED", 0, f"all {failures} enrichment call(s) failed"
    if failures > 0:
        return "PARTIAL", enriched, f"{failures} enrichment call(s) failed"
    return "SUCCESS", enriched, None

"""Apple KR daily music collection entry point.

ingestion.orchestrator.run_daily_ingestion() is NOT reused here — it is
coupled to Phase 2A's news registry/adapter contract (SourceConfig,
run_source_ingestion, raw_items). That coupling is real, not incidental,
so forcing Apple's structured platform observation through it would mean
inventing a fake raw_items row and a fake SourceConfig just to satisfy an
interface built for a different kind of source. Apple observations never
pass through raw_items/normalized_items.

What IS genuinely generic and safe to reuse:
- ingestion.orchestrator.start_run / finalize_run (only touch runs /
  run_metadata; take no news-specific arguments)
- ingestion.persistence.record_run_source_status (run_source_status.category
  and .source_name are free TEXT, not news-specific)

RETRIEVAL_TIME_SNAPSHOT_SEMANTICS (documented, not solved): if Apple's
chart content is byte-for-byte identical between two distinct daily
collections, this still records two distinct observations (different
observed_at). SUPER NEWS has no way to distinguish "the chart genuinely
didn't change" from "we didn't check closely enough" -- and isn't meant
to; observed_at means "this is what SUPER NEWS saw at this retrieval
time," never a claim about when the underlying value last changed. No
content-hash/dedup subsystem is introduced to paper over this.
"""

import logging
from datetime import datetime, timedelta, timezone

from ingestion.http import HttpClientError, HttpTransientError
from ingestion.orchestrator import finalize_run, start_run
from ingestion.persistence import record_run_source_status
from music.apple_music import collect_kr_most_played_observations, fetch_kr_most_played

logger = logging.getLogger(__name__)

CATEGORY = "MUSIC"
SOURCE_NAME = "apple_music"

# See ingestion/orchestrator.py's _KST for why a fixed +09:00 offset is
# used instead of zoneinfo.ZoneInfo("Asia/Seoul") (no tzdata dependency;
# KST has no DST). run_date only -- observed_at/collected_at below remain
# UTC, unchanged.
_KST = timezone(timedelta(hours=9))


def run_apple_kr_collection(conn, run_id, run_date=None, sleep=None):
    """Run one Apple KR most-played collection as its own daily run:
    start_run -> fetch ONE snapshot -> persist observations under ONE
    shared observed_at -> record run_source_status -> finalize_run.

    `run_metadata.source_registry_hash` is left NULL -- there is no
    source-registry config governing this single-source collector, so a
    fabricated hash would be dishonest, not informative.

    Returns a dict describing the outcome; never raises for an ordinary
    fetch/response/persistence failure -- those are recorded as a FAILED/
    PARTIAL run_source_status row instead, matching the news orchestrator's
    "one source's failure is a normal operational outcome" convention.
    Only start_run's own GlobalFailureError subclasses (duplicate run_id,
    run_metadata failure) propagate, exactly as they do for news runs."""
    # run_date is the logical Korean calendar date (see _KST above) --
    # distinct from started_at/finished_at/observed_at below, which stay UTC.
    effective_run_date = run_date or datetime.now(_KST).strftime("%Y-%m-%d")
    runs_row_id = start_run(conn, run_id, effective_run_date, registry_hash=None)

    started_at = datetime.now(timezone.utc).isoformat()
    status, items_collected, failure_reason = _collect(conn, sleep)
    finished_at = datetime.now(timezone.utc).isoformat()

    record_run_source_status(
        conn,
        run_id=runs_row_id,
        category=CATEGORY,
        source_name=SOURCE_NAME,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        items_collected=items_collected,
        retry_count=0,
        failure_reason=failure_reason,
    )
    conn.commit()
    logger.info(
        "source=%s category=%s status=%s items_collected=%d",
        SOURCE_NAME, CATEGORY, status, items_collected,
    )

    source_result = {
        "source_name": SOURCE_NAME,
        "category": CATEGORY,
        "status": status,
        "items_collected": items_collected,
        "failure_reason": failure_reason,
    }
    final_status = finalize_run(conn, runs_row_id, [source_result])
    return {
        "run_id": run_id,
        "runs_row_id": runs_row_id,
        "status": final_status,
        "source_result": source_result,
    }


def _collect(conn, sleep):
    """Returns (run_source_status_value, items_collected, failure_reason)."""
    try:
        songs = fetch_kr_most_played(sleep=sleep)
    except (HttpTransientError, HttpClientError) as exc:
        return "FAILED", 0, f"FETCH_FAILED: {type(exc).__name__}"
    except ValueError as exc:
        return "FAILED", 0, f"INVALID_RESPONSE: {exc}"

    if not songs:
        return "FAILED", 0, "INVALID_RESPONSE: empty chart response"

    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        outcomes = collect_kr_most_played_observations(conn, songs, observed_at=observed_at)
    except Exception as exc:
        return "FAILED", 0, f"PERSISTENCE_FAILED: {type(exc).__name__}"

    inserted = sum(1 for o in outcomes if o["status"] == "inserted")
    duplicate = sum(1 for o in outcomes if o["status"] == "duplicate_snapshot")
    rejected = sum(1 for o in outcomes if o["status"] == "rejected")

    if inserted + duplicate == 0:
        return "FAILED", 0, f"PERSISTENCE_FAILED: all {rejected} entries rejected"
    if rejected > 0:
        return "PARTIAL", inserted, f"{rejected} entr{'y' if rejected == 1 else 'ies'} rejected"
    return "SUCCESS", inserted, None

"""Orchestrates one source's ingestion attempt: fetch -> parse -> persist
-> run_source_status, with source-level failure isolation.
"""

import logging
from datetime import datetime, timezone

from ingestion.adapters import get_adapter
from ingestion.http import HttpClientError, HttpTransientError
from ingestion.persistence import record_run_source_status, save_raw_items

logger = logging.getLogger(__name__)

CREDENTIAL_STATUS_CODES = frozenset({401, 403})


def run_source_ingestion(conn, run_id, source_config, sleep=None):
    """Run one source's ingestion attempt end to end and commit its own
    outcome (raw_items + run_source_status) independently of every other
    source. A failure here (network error, credential error, parse
    failure) is caught and recorded as a FAILED/PARTIAL/SUCCESS
    run_source_status row — it never propagates and never rolls back
    another source's already-committed raw rows (Section 22/35 of the
    Phase 2A contract: source failure isolation).

    Returns a dict describing the outcome, for the caller's own
    logging/aggregation across sources in a run."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not source_config.enabled:
        outcome = _finalize(
            conn, run_id, source_config, status="SKIPPED", started_at=started_at,
            items_collected=0, retry_count=0, failure_reason=None,
        )
        logger.info(
            "source=%s category=%s status=SKIPPED (disabled)",
            source_config.source_name, source_config.category,
        )
        return outcome

    adapter = get_adapter(source_config.source_type)

    try:
        adapter_outcome = adapter(source_config, sleep=sleep)
    except HttpClientError as exc:
        if exc.status_code in CREDENTIAL_STATUS_CODES:
            reason = f"credential/config failure (status={exc.status_code})"
        else:
            reason = f"client error (status={exc.status_code})"
        outcome = _finalize(
            conn, run_id, source_config, status="FAILED", started_at=started_at,
            items_collected=0, retry_count=0, failure_reason=reason,
        )
        logger.error(
            "source=%s category=%s status=FAILED reason=%s",
            source_config.source_name, source_config.category, reason,
        )
        return outcome
    except HttpTransientError:
        reason = "transient failure: retries exhausted"
        outcome = _finalize(
            conn, run_id, source_config, status="FAILED", started_at=started_at,
            items_collected=0, retry_count=max(source_config.retry.max_attempts - 1, 0),
            failure_reason=reason,
        )
        logger.error(
            "source=%s category=%s status=FAILED reason=%s",
            source_config.source_name, source_config.category, reason,
        )
        return outcome

    inserted, duplicates = save_raw_items(conn, adapter_outcome.records, source_config.category)

    if adapter_outcome.parse_errors > 0 and inserted == 0 and not adapter_outcome.records:
        # Every fetched item failed to parse and nothing usable resulted —
        # a real failure, not a legitimate zero-result search.
        status = "FAILED"
        reason = f"all {adapter_outcome.parse_errors} item(s) failed to parse"
    elif adapter_outcome.parse_errors > 0:
        status = "PARTIAL"
        reason = f"{adapter_outcome.parse_errors} item(s) failed to parse"
    else:
        # Includes the legitimate "0 results" case (Section 23 of the
        # contract): a normal request that found nothing is SUCCESS, not
        # FAILED.
        status = "SUCCESS"
        reason = None

    outcome = _finalize(
        conn, run_id, source_config, status=status, started_at=started_at,
        items_collected=inserted, retry_count=0, failure_reason=reason,
    )
    logger.info(
        "source=%s category=%s status=%s inserted=%d duplicates=%d parse_errors=%d",
        source_config.source_name, source_config.category, status,
        inserted, duplicates, adapter_outcome.parse_errors,
    )
    return outcome


def _finalize(conn, run_id, source_config, status, started_at, items_collected, retry_count, failure_reason):
    finished_at = datetime.now(timezone.utc).isoformat()
    record_run_source_status(
        conn,
        run_id=run_id,
        category=source_config.category,
        source_name=source_config.source_name,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        items_collected=items_collected,
        retry_count=retry_count,
        failure_reason=failure_reason,
    )
    conn.commit()
    return {
        "source_name": source_config.source_name,
        "category": source_config.category,
        "status": status,
        "items_collected": items_collected,
        "failure_reason": failure_reason,
    }

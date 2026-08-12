"""Daily ingestion run orchestrator.

Builds on top of Phase 2A's ingestion.pipeline.run_source_ingestion() —
this module owns run lifecycle (start/execute/aggregate/finalize) only. It
never re-implements HTTP, retry, parsing, source_item_key resolution, or
raw persistence; those all stay in ingestion/{http,identity,persistence,
adapters}.py, reused as-is.

runs.status has no CHECK constraint (free TEXT) — this module fixes its
own minimal vocabulary: 'running' (set at start), 'completed' (all/degraded
sources finished AND normalization completed — detailed per-source truth
lives in run_source_status, never inferred from this), 'failed' (every
enabled source failed, OR the required normalization stage itself raised).
No new status value is invented beyond this.

RAW ingestion alone is not sufficient for 'completed': normalization
(ingestion.normalize.normalize_batch) is a required stage of this daily
run, not an optional/best-effort afterthought — a run that only reached
RAW must not be reported as a successful completion.

run_category_status is intentionally NOT written anywhere in this module —
it belongs to the future report pipeline (Section 4 of the Phase 2B
contract); ingestion-only success/failure lives entirely in
run_source_status.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from ingestion.config_hash import compute_registry_hash
from ingestion.normalize import normalize_batch
from ingestion.persistence import record_run_source_status
from ingestion.pipeline import run_source_ingestion

logger = logging.getLogger(__name__)

# Korea Standard Time has a fixed +09:00 offset year-round (no DST, no
# scheduled future changes) -- a plain stdlib fixed-offset timezone is
# exact for KST specifically and needs no tz database. zoneinfo.ZoneInfo
# was considered but rejected: on this project's Windows dev/test
# environment, ZoneInfo("Asia/Seoul") raises ZoneInfoNotFoundError because
# no system tzdata is present and the `tzdata` PyPI package isn't
# installed -- confirmed by direct test, not assumed. This avoids that
# dependency entirely while remaining exactly correct for KST.
_KST = timezone(timedelta(hours=9))


class GlobalFailureError(RuntimeError):
    """Base class for failures that stop the whole run before/without
    executing any source (as opposed to a single source's own FAILED
    status, which is a normal operational outcome)."""


class NoEnabledSourcesError(GlobalFailureError):
    """Raised when the registry has zero enabled sources. Treated as a
    global prerequisite failure (Section 7 of the Phase 2B contract): no
    `runs` row is created, no adapter is ever called."""


class DuplicateRunIdError(GlobalFailureError):
    """Raised when the requested business run_id already exists in
    `runs`. The existing run's sources are never silently re-executed —
    callers must use a new run_id."""


def select_ordered_sources(registry):
    """Deterministic (category, source_name) ordering — never relies on
    YAML/dict insertion order. Includes BOTH enabled and disabled sources;
    run_source_ingestion() itself is what turns a disabled source into a
    SKIPPED row without ever calling its adapter."""
    return sorted(registry.values(), key=lambda cfg: (cfg.category, cfg.source_name))


def start_run(conn, run_id, run_date, registry_hash):
    """Atomically create the `runs` row and its 1:1 `run_metadata` row for
    one daily ingestion execution. Either both are committed or neither is
    — a run_metadata failure never leaves an orphan `runs` row, and a
    duplicate run_id never leaves a half-written run_metadata row either,
    since nothing is committed until both inserts succeed."""
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        conn.execute(
            "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, 'running')",
            (run_id, run_date, started_at),
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise DuplicateRunIdError(f"run_id {run_id!r} already exists.") from exc

    runs_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        conn.execute(
            "INSERT INTO run_metadata (run_id, source_registry_hash, created_at) VALUES (?, ?, ?)",
            (runs_row_id, registry_hash, started_at),
        )
    except Exception:
        conn.rollback()
        raise

    conn.commit()
    logger.info("run_id=%s runs_row_id=%s status=running (started)", run_id, runs_row_id)
    return runs_row_id


def _aggregate_run_status(results):
    """Section 17/18 of the Phase 2B contract: run-level status is a
    coarse lifecycle summary, not a substitute for run_source_status.
    SUCCESS and PARTIAL sources both count as usable outcomes — only
    "every enabled source FAILED" maps to a failed run."""
    enabled_results = [r for r in results if r["status"] != "SKIPPED"]
    if not enabled_results:
        return "failed", "no_enabled_source_results"
    if all(r["status"] == "FAILED" for r in enabled_results):
        return "failed", "all_enabled_sources_failed"
    return "completed", None


def finalize_run(conn, runs_row_id, results, override_status=None, override_failure_stage=None):
    """Small, independent transaction that only updates the `runs` row —
    never re-touches raw_items/run_source_status, which each source
    already committed for itself. Exceptions here are NOT swallowed —
    callers must surface a run-finalization failure as a non-zero exit,
    per Section 20 of the contract.

    `override_status`/`override_failure_stage` let a caller force the
    final status instead of deriving it from per-source `results` — used
    when a required run-wide stage (normalization) fails outright, which
    isn't attributable to any single source and must not be masked by
    otherwise-successful source results."""
    if override_status is not None:
        status, failure_stage = override_status, override_failure_stage
    else:
        status, failure_stage = _aggregate_run_status(results)
    finished_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = ?, failure_stage = ? WHERE id = ?",
        (status, finished_at, failure_stage, runs_row_id),
    )
    conn.commit()
    logger.info("runs_row_id=%s status=%s (finalized)", runs_row_id, status)
    return status


def run_daily_ingestion(conn, registry, run_id, run_date=None, sleep=None):
    """Run one full daily ingestion execution: validate prerequisites,
    hash the effective config, atomically start the run, execute every
    source in deterministic order (reusing Phase 2A's
    run_source_ingestion for each), isolate any source-level failure —
    including a truly unexpected exception a source adapter was never
    supposed to raise — and finalize the run's overall status.

    `registry` must already be a validated dict[str, SourceConfig] (i.e.
    ingestion.registry.load_source_registry() must have already
    succeeded) — an invalid registry is a configuration error the caller
    handles before ever reaching this function, so no `runs` row is ever
    created for one.

    Raises NoEnabledSourcesError before creating any `runs` row if zero
    sources are enabled. Raises DuplicateRunIdError if run_id already
    exists. Both are GlobalFailureError subclasses — the caller (CLI) is
    expected to map these to a non-zero exit code."""
    ordered_sources = select_ordered_sources(registry)
    enabled_count = sum(1 for cfg in ordered_sources if cfg.enabled)
    if enabled_count == 0:
        raise NoEnabledSourcesError(
            "Registry has zero enabled sources; refusing to create a run."
        )

    registry_hash = compute_registry_hash(registry)
    # run_date is the logical Korean calendar date, not the UTC calendar
    # date -- a run during 00:00-08:59 Asia/Seoul is still UTC "yesterday".
    # This is distinct from observed_at/collected_at/started_at/etc., which
    # remain UTC instants unchanged by this fix.
    effective_run_date = run_date or datetime.now(_KST).strftime("%Y-%m-%d")
    runs_row_id = start_run(conn, run_id, effective_run_date, registry_hash)

    results = []
    for source_config in ordered_sources:
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            outcome = run_source_ingestion(conn, runs_row_id, source_config, sleep=sleep)
        except Exception as exc:
            # A source adapter raising something outside Phase 2A's own
            # HttpClientError/HttpTransientError contract (a bug, an
            # unexpected data shape, ...) must not take the rest of the
            # run down with it. Roll back only this source's own
            # not-yet-committed partial work, then record it FAILED using
            # the same Phase 2A persistence helper every other source
            # uses — no bespoke error path.
            conn.rollback()
            finished_at = datetime.now(timezone.utc).isoformat()
            reason = f"unexpected error: {type(exc).__name__}"
            record_run_source_status(
                conn,
                run_id=runs_row_id,
                category=source_config.category,
                source_name=source_config.source_name,
                status="FAILED",
                started_at=started_at,
                finished_at=finished_at,
                items_collected=0,
                retry_count=0,
                failure_reason=reason,
            )
            conn.commit()
            outcome = {
                "source_name": source_config.source_name,
                "category": source_config.category,
                "status": "FAILED",
                "items_collected": 0,
                "failure_reason": reason,
            }
            logger.error(
                "source=%s category=%s status=FAILED reason=%s",
                source_config.source_name, source_config.category, reason,
            )
        results.append(outcome)

    # Normalization is a REQUIRED stage, not a best-effort afterthought —
    # a run that only reached RAW must not be reported "completed". This
    # sweeps every not-yet-normalized raw_item (not just this run's new
    # ones): normalize_batch is already idempotent, so this also catches
    # any backlog from a prior run, at the cost of a full-table scan each
    # time -- an accepted tradeoff at current volume, not optimized here.
    try:
        normalize_batch(conn, registry)
    except Exception as exc:
        logger.error(
            "run_id=%s normalization stage FAILED: %s", run_id, type(exc).__name__,
        )
        final_status = finalize_run(
            conn, runs_row_id, results,
            override_status="failed", override_failure_stage="normalization_stage_failed",
        )
        return {
            "run_id": run_id,
            "runs_row_id": runs_row_id,
            "status": final_status,
            "source_results": results,
        }

    final_status = finalize_run(conn, runs_row_id, results)
    return {
        "run_id": run_id,
        "runs_row_id": runs_row_id,
        "status": final_status,
        "source_results": results,
    }

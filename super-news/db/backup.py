"""SQLite-consistent snapshot creation, local verification, manifest
building, and R2 capacity/alert classification -- the parts of the
DATABASE BACKUP INVARIANT (see SUPER_NEWS_HANDOFF.md) that never need a
real R2 connection, so they're fully unit-testable against fake/local
DBs. Real R2 transport lives in db/r2_client.py -- this module never
imports boto3.

DATABASE BACKUP INVARIANT (permanent, see HANDOFF): production DB and
offsite backup are always separate; a backup is never accepted as final
until it lives at a genuinely different location AND account than the
primary DB; only a SQLite-consistent snapshot (never a raw OS file copy)
is used; a backup is "verified" only after integrity_check + checksum
pass locally; a remote upload succeeding is NOT the same claim as a
verified backup; a real remote-retrieval restore test is required before
anything is trusted; restore tests never touch the production DB path;
secrets are never written into a manifest or log; automatic backup
deletion is never performed by this module.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# See ingestion/orchestrator.py's own _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

BACKUP_TYPES = ("MANUAL", "PRE", "POST")

# "Important" tables checked/reported on IF they exist in the real schema
# (Phase 3D-BACKUP section 7/10's own instruction: "실제 schema에 존재하는
# 것만 검사") -- never an error for one to be absent (e.g. a fresh/minimal
# test DB), only ever skipped.
IMPORTANT_TABLES = ("raw_items", "normalized_items", "runs", "translation_cache", "llm_interpretations")

# Reference threshold for Cloudflare R2 Standard Storage's free monthly
# storage allowance. A config constant, not hardcoded at every call site,
# because Cloudflare's actual published policy can change -- override via
# R2_FREE_STORAGE_GB if it ever does.
DEFAULT_R2_FREE_STORAGE_GB = 10

_ALERT_OK = "OK"
_ALERT_WARNING_70 = "R2_STORAGE_WARNING_70"
_ALERT_IMPORTANT_85 = "R2_STORAGE_WARNING_85"
_ALERT_CRITICAL_95 = "R2_STORAGE_CRITICAL_95"
_ALERT_EXCEEDED_100 = "R2_STORAGE_EXCEEDED"

_FORECAST_WINDOW_DAYS = 30


class BackupSeparationError(RuntimeError):
    """Raised when a proposed backup destination violates the DATABASE
    BACKUP INVARIANT's separation rules (inside the repo, or literally the
    same path as the primary DB) -- caught here, deterministically, before
    any snapshot is ever written there, never as an afterthought."""


def reject_unsafe_destination(dest_path, primary_db_path, repo_root):
    """Raises BackupSeparationError if `dest_path` is inside `repo_root`
    or is the same path as `primary_db_path` (resolved, so a relative/
    symlinked alias can't sneak past this). Never mutates anything --
    pure validation, called before ANY snapshot write."""
    dest = Path(dest_path).resolve()
    primary = Path(primary_db_path).resolve()
    repo = Path(repo_root).resolve()
    if dest == primary:
        raise BackupSeparationError(f"Backup destination must not be the primary DB path itself: {dest}")
    try:
        dest.relative_to(repo)
    except ValueError:
        pass
    else:
        raise BackupSeparationError(f"Backup destination must not be inside the repository: {dest} (repo={repo})")


def create_consistent_snapshot(source_db_path, dest_path):
    """Transaction-consistent snapshot via sqlite3.Connection.backup() --
    never a raw OS file copy (which could capture a mid-write, torn
    state). Safe against a concurrently-open, actively-written source: the
    backup API takes its own read lock and copies page-by-page under
    SQLite's own consistency guarantees. The source connection is opened
    read-only-in-spirit (only ever read from, never written to) and always
    closed in a `finally` -- this function never deletes, renames, moves,
    or writes a single byte to `source_db_path`."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source_db_path))
    try:
        dest_conn = sqlite3.connect(str(dest_path))
        try:
            source_conn.backup(dest_conn)
            dest_conn.commit()
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    return dest_path


def compute_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_names(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def get_row_counts(conn, tables=IMPORTANT_TABLES):
    """Row counts for whichever of `tables` actually exist in this DB's
    real schema -- a table that doesn't exist is simply absent from the
    result, never an error (Phase 3D-BACKUP's own "실제 schema상 존재할
    때만" instruction)."""
    existing = _table_names(conn)
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in tables
        if t in existing
    }


def local_verify_snapshot(snapshot_path, expected_row_counts=None, required_tables=IMPORTANT_TABLES):
    """Runs every pre-upload check the DATABASE BACKUP INVARIANT requires:
    file exists, size > 0, SQLite opens, integrity_check == ok, whichever
    `required_tables` are present, and (if `expected_row_counts` given --
    normally the source DB's own counts, computed BEFORE snapshotting)
    those match exactly. Returns {"valid": bool, "errors": [str, ...],
    "size_bytes": int|None, "sha256": str|None, "integrity_check": str|None,
    "tables_present": [str,...], "row_counts": {...}}. NEVER raises for an
    invalid snapshot -- an invalid snapshot is a normal, expected outcome
    (BACKUP_INVALID), not a programming error; the caller decides what to
    do (never upload it)."""
    errors = []
    result = {
        "valid": False, "errors": errors, "size_bytes": None, "sha256": None,
        "integrity_check": None, "tables_present": [], "row_counts": {},
    }
    path = Path(snapshot_path)
    if not path.exists():
        errors.append("snapshot file does not exist")
        return result
    size = path.stat().st_size
    result["size_bytes"] = size
    if size <= 0:
        errors.append("snapshot file size is 0")
        return result

    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        errors.append(f"sqlite3 could not open snapshot: {exc}")
        return result

    try:
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            errors.append(f"integrity_check query failed (likely corrupt/not a DB): {exc}")
            return result
        result["integrity_check"] = integrity
        if integrity != "ok":
            errors.append(f"integrity_check returned {integrity!r}, not 'ok'")

        existing = _table_names(conn)
        present_required = [t for t in required_tables if t in existing]
        missing_required = [t for t in required_tables if t not in existing and t in (expected_row_counts or {})]
        result["tables_present"] = sorted(existing)
        if missing_required:
            errors.append(f"required tables missing from snapshot: {missing_required}")

        row_counts = get_row_counts(conn, required_tables)
        result["row_counts"] = row_counts
        if expected_row_counts:
            for table, expected_count in expected_row_counts.items():
                actual_count = row_counts.get(table)
                if actual_count != expected_count:
                    errors.append(
                        f"row count mismatch for {table}: source={expected_count} snapshot={actual_count}"
                    )
    finally:
        conn.close()

    result["sha256"] = compute_sha256(path)
    result["valid"] = not errors
    return result


def build_manifest(*, backup_type, source_db_path, snapshot_verify_result, r2_bucket=None, r2_object_key=None,
                    r2_account_label=None, upload_verified=None, remote_verified=None, restore_verified=None,
                    now=None):
    """Assembles the backup manifest dict -- every field the DATABASE
    BACKUP INVARIANT requires, and explicitly NEVER a secret: no access
    key, secret key, API token, password, or .env content is ever a field
    here (see module docstring). `r2_account_label` is a non-secret
    identifier only (e.g. "cloudflare-r2-super-news-backups"), never a
    credential value."""
    now = now or datetime.now(_KST)
    source_path = Path(source_db_path)
    return {
        "backup_timestamp_kst": now.isoformat(),
        "backup_type": backup_type,
        "source_db_basename": source_path.name,
        "source_size_bytes": source_path.stat().st_size if source_path.exists() else None,
        "backup_size_bytes": snapshot_verify_result.get("size_bytes"),
        "sha256": snapshot_verify_result.get("sha256"),
        "integrity_check_result": snapshot_verify_result.get("integrity_check"),
        "table_inventory": snapshot_verify_result.get("tables_present", []),
        "row_counts": snapshot_verify_result.get("row_counts", {}),
        "local_verification_valid": snapshot_verify_result.get("valid"),
        "local_verification_errors": snapshot_verify_result.get("errors", []),
        "r2_bucket": r2_bucket,
        "r2_object_key": r2_object_key,
        "r2_account_label": r2_account_label,
        "upload_verified": upload_verified,
        "remote_verified": remote_verified,
        "restore_verified": restore_verified,
        "sqlite_version": sqlite3.sqlite_version,
        "python_sqlite3_module_version": sqlite3.version,
    }


def backup_filename(backup_type, now=None):
    """e.g. MANUAL_20260815T001500+0900.db -- timestamp-unique, no
    collision risk under normal (non-sub-second-repeated) invocation."""
    now = now or datetime.now(_KST)
    stamp = now.strftime("%Y%m%dT%H%M%S%z")
    return f"{backup_type}_{stamp}"


def r2_object_prefix(now=None):
    """database/YYYY/MM/ -- matches the requested R2 object layout."""
    now = now or datetime.now(_KST)
    return f"database/{now.strftime('%Y')}/{now.strftime('%m')}/"


def classify_capacity(usage_bytes, free_allowance_gb=DEFAULT_R2_FREE_STORAGE_GB):
    """Returns (alert_level, usage_percent). Boundaries per the required
    contract: <70% OK, [70,85) WARNING_70, [85,95) WARNING_85 (IMPORTANT),
    [95,100) CRITICAL_95, >=100 EXCEEDED."""
    free_allowance_bytes = free_allowance_gb * (1024 ** 3)
    if free_allowance_bytes <= 0:
        raise ValueError("free_allowance_gb must be > 0")
    usage_percent = (usage_bytes / free_allowance_bytes) * 100
    if usage_percent >= 100:
        level = _ALERT_EXCEEDED_100
    elif usage_percent >= 95:
        level = _ALERT_CRITICAL_95
    elif usage_percent >= 85:
        level = _ALERT_IMPORTANT_85
    elif usage_percent >= 70:
        level = _ALERT_WARNING_70
    else:
        level = _ALERT_OK
    return level, usage_percent


def forecast_capacity(history, free_allowance_gb=DEFAULT_R2_FREE_STORAGE_GB, now=None):
    """`history`: a list of (datetime, usage_bytes) real observations,
    any order. Returns a dict {"forecast": "INSUFFICIENT_HISTORY_FOR_
    FORECAST"} when fewer than 2 real data points exist -- NEVER
    fabricates a growth rate from a single data point (Phase 3D-BACKUP's
    own "추측값 사용 금지" instruction). Otherwise computes a simple linear
    growth rate between the earliest and latest observation and reports
    {"forecast": "CAPACITY_FORECAST_WARNING"|"OK", "estimated_days_to_
    threshold": float|None} -- WARNING if projected to reach 100% of
    `free_allowance_gb` within 30 days from `now`."""
    now = now or datetime.now(_KST)
    if len(history) < 2:
        return {"forecast": "INSUFFICIENT_HISTORY_FOR_FORECAST", "estimated_days_to_threshold": None}

    ordered = sorted(history, key=lambda pair: pair[0])
    earliest_ts, earliest_bytes = ordered[0]
    latest_ts, latest_bytes = ordered[-1]
    elapsed_days = (latest_ts - earliest_ts).total_seconds() / 86400
    if elapsed_days <= 0:
        return {"forecast": "INSUFFICIENT_HISTORY_FOR_FORECAST", "estimated_days_to_threshold": None}

    growth_bytes_per_day = (latest_bytes - earliest_bytes) / elapsed_days
    free_allowance_bytes = free_allowance_gb * (1024 ** 3)
    if growth_bytes_per_day <= 0:
        return {"forecast": "OK", "estimated_days_to_threshold": None}

    remaining_bytes = free_allowance_bytes - latest_bytes
    if remaining_bytes <= 0:
        return {"forecast": "CAPACITY_FORECAST_WARNING", "estimated_days_to_threshold": 0.0}

    estimated_days = remaining_bytes / growth_bytes_per_day
    forecast = "CAPACITY_FORECAST_WARNING" if estimated_days <= _FORECAST_WINDOW_DAYS else "OK"
    return {"forecast": forecast, "estimated_days_to_threshold": estimated_days}

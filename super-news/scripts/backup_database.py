"""Offsite database backup CLI (Phase 3D-BACKUP) -- SQLite-consistent
snapshot -> local verification -> Cloudflare R2 upload -> remote
verification -> R2 capacity/alert check, plus an explicit real
remote-retrieval restore drill.

    .venv\\Scripts\\python.exe scripts\\backup_database.py --type manual
    .venv\\Scripts\\python.exe scripts\\backup_database.py --type pre
    .venv\\Scripts\\python.exe scripts\\backup_database.py --type post
    .venv\\Scripts\\python.exe scripts\\backup_database.py --restore-test
    .venv\\Scripts\\python.exe scripts\\backup_database.py --capacity-only

See db/backup.py's own module docstring for the full DATABASE BACKUP
INVARIANT this script exists to uphold -- production DB is only ever
read from (sqlite3.Connection.backup(), never a raw file copy, never
deleted/renamed/moved/overwritten), local staging is explicitly NOT the
final backup (only a genuinely-verified R2 upload is), and a restore test
always downloads to a FRESH temp directory, never the production path.

Exit code contract:
  0 = backup created + verified locally + (if R2 configured) uploaded +
      remote-verified, OR a real restore test passed (RESTORE_VERIFIED),
      OR --capacity-only ran successfully
  1 = BACKUP_INVALID (local verification failed -- never uploaded) or
      BACKUP_SYSTEM_FAIL (restore test failed) or a real R2 upload/
      download failure
  2 = CLI invocation error (argparse's own handling)
  3 = R2_CONFIGURATION_REQUIRED (local snapshot+verification succeeded,
      but no R2 credentials are configured, so nothing was uploaded)
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH, PROJECT_ROOT, get_optional_env
from db.backup import (
    IMPORTANT_TABLES,
    BackupSeparationError,
    backup_filename,
    build_manifest,
    classify_capacity,
    create_consistent_snapshot,
    forecast_capacity,
    get_row_counts,
    local_verify_snapshot,
    r2_object_prefix,
    reject_unsafe_destination,
)

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

EXIT_OK = 0
EXIT_INVALID_OR_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_R2_CONFIG_REQUIRED = 3

REPRESENTATIVE_SELECT_TABLES = ("raw_items", "normalized_items", "runs", "translation_cache", "llm_interpretations")


def _default_staging_dir():
    """Outside the repo, per the DATABASE BACKUP INVARIANT's separation
    rules -- under the OS user profile, a sibling of (never inside)
    PROJECT_ROOT. Overridable via SUPER_NEWS_BACKUP_STAGING_DIR for tests/
    alternate environments."""
    override = get_optional_env("SUPER_NEWS_BACKUP_STAGING_DIR")
    if override:
        return Path(override)
    return Path.home() / "super_news_backup_staging"


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="SUPER NEWS offsite database backup (Cloudflare R2).")
    parser.add_argument("--type", choices=["manual", "pre", "post"], default="manual",
                         help="Backup type, recorded in the manifest (default: manual).")
    parser.add_argument("--db-path", type=Path, default=None,
                         help="Override the primary SQLite DB path (default: the project's configured data/super_news.db).")
    parser.add_argument("--staging-dir", type=Path, default=None,
                         help="Override the local staging directory (default: outside the repo, under the user profile).")
    parser.add_argument("--restore-test", action="store_true",
                         help="Perform a real remote-retrieval restore drill instead of creating a new backup.")
    parser.add_argument("--object-key", type=str, default=None,
                         help="R2 object key to restore-test (default: the key just uploaded in the same invocation).")
    parser.add_argument("--capacity-only", action="store_true",
                         help="Only check/report R2 storage capacity -- creates no new backup.")
    return parser.parse_args(argv)


def _print_capacity_status(usage_bytes, free_gb, forecast_history=None):
    level, percent = classify_capacity(usage_bytes, free_gb)
    forecast = forecast_capacity(forecast_history or [], free_gb)
    print(f"R2_STORAGE_BYTES={usage_bytes}")
    print(f"R2_STORAGE_GB={usage_bytes / (1024 ** 3):.4f}")
    print(f"R2_FREE_ALLOWANCE_GB={free_gb}")
    print(f"R2_USAGE_PERCENT={percent:.2f}")
    print(f"R2_ALERT_LEVEL={level}")
    print(f"R2_ESTIMATED_DAYS_TO_THRESHOLD={forecast['estimated_days_to_threshold']}")
    print(f"R2_CAPACITY_FORECAST={forecast['forecast']}")
    if level != "OK":
        logger.warning("R2 capacity alert: %s (%.2f%% of %sGB free allowance)", level, percent, free_gb)
    if forecast["forecast"] == "CAPACITY_FORECAST_WARNING":
        logger.warning(
            "R2 capacity forecast: projected to reach the free allowance within %s days.",
            f"{forecast['estimated_days_to_threshold']:.1f}",
        )
    return level, forecast


def _run_capacity_only():
    from db import r2_client

    if not r2_client.is_configured():
        print("R2_CONFIGURATION_REQUIRED")
        return EXIT_R2_CONFIG_REQUIRED
    client = r2_client.build_client()
    usage_bytes = r2_client.get_bucket_usage_bytes(client, r2_client.bucket_name())
    _print_capacity_status(usage_bytes, r2_client.free_storage_gb())
    return EXIT_OK


def _run_restore_test(db_path, object_key, uploaded_manifest=None):
    from db import r2_client

    if not r2_client.is_configured():
        print("R2_CONFIGURATION_REQUIRED")
        return EXIT_R2_CONFIG_REQUIRED
    if not object_key:
        print("BACKUP_SYSTEM_FAIL: no --object-key given and no backup was uploaded in this same invocation.")
        return EXIT_INVALID_OR_FAILED

    client = r2_client.build_client()
    bucket = r2_client.bucket_name()

    restore_dir = Path.home() / "super_news_restore_test" / datetime.now(_KST).strftime("%Y%m%dT%H%M%S%z")
    restore_dir.mkdir(parents=True, exist_ok=True)
    restored_db_path = restore_dir / Path(object_key).name

    print(f"RESTORE_TEST_DOWNLOADING key={object_key} -> {restored_db_path}")
    try:
        r2_client.download_object(client, bucket, object_key, restored_db_path)
    except Exception as exc:
        logger.error("Restore-test download failed: %s", type(exc).__name__)
        print(f"BACKUP_SYSTEM_FAIL: remote download failed: {type(exc).__name__}: {exc}")
        return EXIT_INVALID_OR_FAILED

    expected_row_counts = uploaded_manifest.get("row_counts") if uploaded_manifest else None
    verify = local_verify_snapshot(restored_db_path, expected_row_counts=expected_row_counts)

    errors = list(verify["errors"])
    if uploaded_manifest and uploaded_manifest.get("sha256") and verify["sha256"] != uploaded_manifest["sha256"]:
        errors.append(
            f"SHA-256 mismatch: expected={uploaded_manifest['sha256']} actual={verify['sha256']}"
        )

    representative_ok = True
    if not errors:
        import sqlite3
        conn = sqlite3.connect(str(restored_db_path))
        try:
            existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for table in REPRESENTATIVE_SELECT_TABLES:
                if table not in existing:
                    continue
                try:
                    conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchall()
                except Exception as exc:
                    representative_ok = False
                    errors.append(f"representative SELECT on {table} failed: {exc}")
        finally:
            conn.close()

    print(f"restore_test_path={restored_db_path}")
    print(f"restore_integrity_check={verify['integrity_check']}")
    print(f"restore_row_counts={verify['row_counts']}")
    print(f"restore_sha256={verify['sha256']}")
    print(f"restore_representative_selects_ok={representative_ok}")

    if errors:
        print("BACKUP_SYSTEM_FAIL")
        for err in errors:
            print(f"  reason={err}")
        return EXIT_INVALID_OR_FAILED

    print("RESTORE_VERIFIED")
    print("(restored test file NOT deleted -- deletion requires explicit user approval)")
    return EXIT_OK


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH

    if args.capacity_only:
        return _run_capacity_only()

    if args.restore_test and args.object_key:
        return _run_restore_test(db_path, args.object_key)

    # --- production DB immutability check: measure BEFORE touching anything ---
    import sqlite3
    primary_conn = sqlite3.connect(str(db_path))
    try:
        primary_integrity_before = primary_conn.execute("PRAGMA integrity_check").fetchone()[0]
        primary_row_counts_before = get_row_counts(primary_conn)
    finally:
        primary_conn.close()
    if primary_integrity_before != "ok":
        print(f"BACKUP_INVALID: primary DB itself failed integrity_check ({primary_integrity_before!r}) -- refusing to back up a DB already reported unhealthy.")
        return EXIT_INVALID_OR_FAILED

    backup_type = args.type.upper()
    staging_dir = args.staging_dir if args.staging_dir is not None else _default_staging_dir()
    reject_unsafe_destination(staging_dir, db_path, PROJECT_ROOT)

    now = datetime.now(_KST)
    snapshot_path = staging_dir / f"{backup_filename(backup_type, now)}.db"
    create_consistent_snapshot(db_path, snapshot_path)

    verify = local_verify_snapshot(snapshot_path, expected_row_counts=primary_row_counts_before)
    if not verify["valid"]:
        print("BACKUP_INVALID")
        for err in verify["errors"]:
            print(f"  reason={err}")
        return EXIT_INVALID_OR_FAILED
    print(f"local_snapshot_verified path={snapshot_path} sha256={verify['sha256']} size_bytes={verify['size_bytes']}")

    manifest = build_manifest(
        backup_type=backup_type, source_db_path=db_path, snapshot_verify_result=verify, now=now,
    )

    from db import r2_client

    if not r2_client.is_configured():
        print("R2_CONFIGURATION_REQUIRED")
        print("(local snapshot was created and verified, but NOT uploaded -- staging is not a final backup)")
        return EXIT_R2_CONFIG_REQUIRED

    client = r2_client.build_client()
    bucket = r2_client.bucket_name()
    object_key = f"{r2_object_prefix(now)}{snapshot_path.name}"
    manifest_key = f"{r2_object_prefix(now)}{snapshot_path.stem}.manifest.json"

    try:
        r2_client.upload_object(client, bucket, object_key, snapshot_path)
        head = r2_client.head_object(client, bucket, object_key)
    except Exception as exc:
        logger.error("R2 upload failed: %s", type(exc).__name__)
        print(f"BACKUP_SYSTEM_FAIL: R2 upload failed: {type(exc).__name__}: {exc}")
        print("(local snapshot was created and verified, but the remote upload failed -- staging is not a final backup)")
        return EXIT_INVALID_OR_FAILED
    upload_verified = head["exists"] and head["size_bytes"] == verify["size_bytes"]
    manifest["r2_bucket"] = bucket
    manifest["r2_object_key"] = object_key
    manifest["r2_account_label"] = "cloudflare-r2"
    manifest["upload_verified"] = upload_verified
    manifest["remote_verified"] = upload_verified  # same head_object check IS the remote verification here

    import json
    manifest_path = staging_dir / f"{snapshot_path.stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    r2_client.upload_object(client, bucket, manifest_key, manifest_path)

    print(f"r2_object_key={object_key}")
    print(f"r2_bucket={bucket}")
    print(f"upload_verified={upload_verified}")
    if not upload_verified:
        print("BACKUP_SYSTEM_FAIL: remote object missing or size mismatch after upload.")
        return EXIT_INVALID_OR_FAILED

    usage_bytes = r2_client.get_bucket_usage_bytes(client, bucket)
    _print_capacity_status(usage_bytes, r2_client.free_storage_gb())

    # --- production DB immutability check: re-measure AFTER everything ---
    primary_conn = sqlite3.connect(str(db_path))
    try:
        primary_integrity_after = primary_conn.execute("PRAGMA integrity_check").fetchone()[0]
        primary_row_counts_after = get_row_counts(primary_conn)
    finally:
        primary_conn.close()
    mutated = primary_integrity_after != primary_integrity_before or primary_row_counts_after != primary_row_counts_before
    print(f"primary_db_mutated_by_backup={mutated}")
    if mutated:
        logger.error("Primary DB changed during backup -- this must never happen. Investigate immediately.")
        return EXIT_INVALID_OR_FAILED

    if args.restore_test:
        return _run_restore_test(db_path, object_key, uploaded_manifest=manifest)

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

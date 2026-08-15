"""db/backup.py + db/r2_client.py + scripts/backup_database.py: SQLite-
consistent snapshot, local verification, manifest (no secrets), R2
capacity/alert classification, and the backup/restore-test CLI flow.
Uses only fake/local SQLite DBs and an in-memory fake R2 backend --
never a real network call, never real credentials."""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from db.backup import (
    BackupSeparationError,
    build_manifest,
    classify_capacity,
    compute_sha256,
    create_consistent_snapshot,
    forecast_capacity,
    local_verify_snapshot,
    reject_unsafe_destination,
)

_KST = timezone(timedelta(hours=9))


def _make_real_db(path, rows=3):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE raw_items (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO raw_items (title) VALUES (?)", (f"Real headline {i}",))
    conn.commit()
    conn.close()
    return path


# ---- A: consistent snapshot creation ---------------------------------------


def test_A_consistent_snapshot_created_and_matches_source(tmp_path):
    source = _make_real_db(tmp_path / "source.db")
    dest = tmp_path / "staging" / "snapshot.db"
    create_consistent_snapshot(source, dest)
    assert dest.exists()
    conn = sqlite3.connect(str(dest))
    assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 3
    conn.close()
    # source untouched
    src_conn = sqlite3.connect(str(source))
    assert src_conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 3
    src_conn.close()


# ---- B: row counts match ----------------------------------------------------


def test_B_row_counts_match_source(tmp_path):
    source = _make_real_db(tmp_path / "source.db", rows=7)
    dest = tmp_path / "snapshot.db"
    create_consistent_snapshot(source, dest)
    src_conn = sqlite3.connect(str(source))
    expected = {"raw_items": src_conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]}
    src_conn.close()
    result = local_verify_snapshot(dest, expected_row_counts=expected, required_tables=("raw_items",))
    assert result["valid"] is True
    assert result["row_counts"]["raw_items"] == 7


# ---- C: checksum ------------------------------------------------------------


def test_C_checksum_generated_and_deterministic(tmp_path):
    source = _make_real_db(tmp_path / "source.db")
    dest = tmp_path / "snapshot.db"
    create_consistent_snapshot(source, dest)
    sha1 = compute_sha256(dest)
    sha2 = compute_sha256(dest)
    assert sha1 == sha2
    assert len(sha1) == 64


# ---- D: corrupted backup rejected -------------------------------------------


def test_D_corrupted_backup_rejected(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a real sqlite file, just garbage bytes 12345")
    result = local_verify_snapshot(corrupt)
    assert result["valid"] is False
    assert result["errors"]


# ---- E: missing required table rejected -------------------------------------


def test_E_missing_required_table_rejected(tmp_path):
    source = tmp_path / "no_tables.db"
    conn = sqlite3.connect(str(source))
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    result = local_verify_snapshot(
        source, expected_row_counts={"raw_items": 3}, required_tables=("raw_items",),
    )
    assert result["valid"] is False
    assert any("missing" in e.lower() for e in result["errors"])


# ---- G/H: unsafe destination rejected ---------------------------------------


def test_G_repo_internal_destination_rejected(tmp_path):
    repo_root = tmp_path / "repo"
    primary = repo_root / "data" / "super_news.db"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"x")
    bad_dest = repo_root / "data" / "backups" / "snap.db"
    with pytest.raises(BackupSeparationError):
        reject_unsafe_destination(bad_dest, primary, repo_root)


def test_H_same_path_as_primary_rejected(tmp_path):
    repo_root = tmp_path / "repo"
    primary = repo_root / "data" / "super_news.db"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"x")
    with pytest.raises(BackupSeparationError):
        reject_unsafe_destination(primary, primary, repo_root)


def test_outside_repo_destination_accepted(tmp_path):
    repo_root = tmp_path / "repo"
    primary = repo_root / "data" / "super_news.db"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"x")
    good_dest = tmp_path / "outside_staging" / "snap.db"
    reject_unsafe_destination(good_dest, primary, repo_root)  # must not raise


# ---- I: manifest never contains a secret ------------------------------------


def test_I_manifest_contains_no_secret_values(tmp_path):
    source = _make_real_db(tmp_path / "source.db")
    dest = tmp_path / "snapshot.db"
    create_consistent_snapshot(source, dest)
    verify = local_verify_snapshot(dest, required_tables=("raw_items",))
    manifest = build_manifest(
        backup_type="MANUAL", source_db_path=source, snapshot_verify_result=verify,
        r2_bucket="super-news-backups", r2_object_key="database/2026/08/MANUAL_x.db",
        r2_account_label="cloudflare-r2", upload_verified=True, remote_verified=True,
    )
    serialized = json.dumps(manifest).lower()
    for forbidden in ("access_key", "secret_access_key", "secret_key", "api_token", "password", "r2_secret"):
        assert forbidden not in serialized
    assert "backup_timestamp_kst" in manifest
    assert manifest["backup_type"] == "MANUAL"


# ---- L-P: capacity classification -------------------------------------------


@pytest.mark.parametrize("percent,expected_level", [
    (69, "OK"),
    (70, "R2_STORAGE_WARNING_70"),
    (85, "R2_STORAGE_WARNING_85"),
    (95, "R2_STORAGE_CRITICAL_95"),
    (100, "R2_STORAGE_EXCEEDED"),
])
def test_LMNOP_capacity_thresholds(percent, expected_level):
    free_gb = 10
    usage_bytes = int(free_gb * (1024 ** 3) * (percent / 100))
    level, actual_percent = classify_capacity(usage_bytes, free_allowance_gb=free_gb)
    assert level == expected_level


# ---- Q: 30-day forecast -------------------------------------------------


def test_Q_forecast_warns_within_30_days():
    now = datetime(2026, 8, 15, tzinfo=_KST)
    free_gb = 10
    free_bytes = free_gb * (1024 ** 3)
    # Growing fast enough to cross the free allowance well within 30 days.
    history = [
        (now - timedelta(days=5), int(free_bytes * 0.50)),
        (now, int(free_bytes * 0.90)),
    ]
    result = forecast_capacity(history, free_allowance_gb=free_gb, now=now)
    assert result["forecast"] == "CAPACITY_FORECAST_WARNING"
    assert result["estimated_days_to_threshold"] is not None
    assert result["estimated_days_to_threshold"] <= 30


def test_forecast_insufficient_history_with_fewer_than_2_points():
    result = forecast_capacity([], free_allowance_gb=10)
    assert result["forecast"] == "INSUFFICIENT_HISTORY_FOR_FORECAST"
    single_point_result = forecast_capacity(
        [(datetime.now(_KST), 1000)], free_allowance_gb=10,
    )
    assert single_point_result["forecast"] == "INSUFFICIENT_HISTORY_FOR_FORECAST"


def test_forecast_slow_growth_reports_ok_not_a_guess():
    now = datetime(2026, 8, 15, tzinfo=_KST)
    free_gb = 10
    free_bytes = free_gb * (1024 ** 3)
    history = [
        (now - timedelta(days=30), int(free_bytes * 0.10)),
        (now, int(free_bytes * 0.11)),  # tiny real growth -- nowhere near 30-day threshold
    ]
    result = forecast_capacity(history, free_allowance_gb=free_gb, now=now)
    assert result["forecast"] == "OK"


# =============================================================================
# CLI-level tests (F, J, K, R) -- in-process main() with a fake R2 backend.
# =============================================================================


class _FakeR2Backend:
    """In-memory object store standing in for real R2 -- monkeypatched
    onto db.r2_client's module-level functions so scripts/backup_
    database.py's real code runs unmodified against it."""

    def __init__(self):
        self.objects = {}  # (bucket, key) -> bytes
        self.upload_should_fail = False
        self.download_should_fail = False

    def upload(self, bucket, key, local_path):
        if self.upload_should_fail:
            raise RuntimeError("simulated upload failure")
        self.objects[(bucket, key)] = Path(local_path).read_bytes()

    def head(self, bucket, key):
        data = self.objects.get((bucket, key))
        if data is None:
            return {"exists": False, "size_bytes": None}
        return {"exists": True, "size_bytes": len(data)}

    def download(self, bucket, key, dest_path):
        if self.download_should_fail:
            raise RuntimeError("simulated download failure")
        data = self.objects.get((bucket, key))
        if data is None:
            raise RuntimeError("object not found")
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(data)
        return dest_path

    def usage_bytes(self, bucket, prefix=None):
        return sum(len(v) for (b, k), v in self.objects.items() if b == bucket and (not prefix or k.startswith(prefix)))


@pytest.fixture
def fake_r2(monkeypatch):
    import db.r2_client as r2_client_module

    backend = _FakeR2Backend()
    monkeypatch.setattr(r2_client_module, "is_configured", lambda: True)
    monkeypatch.setattr(r2_client_module, "build_client", lambda: object())
    monkeypatch.setattr(r2_client_module, "bucket_name", lambda: "test-bucket")
    monkeypatch.setattr(r2_client_module, "free_storage_gb", lambda: 10)
    monkeypatch.setattr(r2_client_module, "upload_object", lambda client, bucket, key, path: backend.upload(bucket, key, path))
    monkeypatch.setattr(r2_client_module, "head_object", lambda client, bucket, key: backend.head(bucket, key))
    monkeypatch.setattr(r2_client_module, "download_object", lambda client, bucket, key, dest: backend.download(bucket, key, dest))
    monkeypatch.setattr(r2_client_module, "get_bucket_usage_bytes", lambda client, bucket, prefix=None: backend.usage_bytes(bucket, prefix))
    return backend


def _make_primary_db(tmp_path, rows=3):
    path = tmp_path / "primary" / "super_news.db"
    path.parent.mkdir(parents=True)
    _make_real_db(path, rows=rows)
    return path


def test_backup_then_restore_test_end_to_end(tmp_path, fake_r2, monkeypatch):
    import backup_database as cli

    primary = _make_primary_db(tmp_path)
    staging = tmp_path / "staging"
    exit_code = cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    assert exit_code == cli.EXIT_OK
    assert len(fake_r2.objects) == 2  # snapshot + manifest

    manifest_key = next(k for (b, k) in fake_r2.objects if k.endswith(".manifest.json"))
    object_key = next(k for (b, k) in fake_r2.objects if k.endswith(".db"))

    exit_code2 = cli.main(["--db-path", str(primary), "--restore-test", "--object-key", object_key])
    assert exit_code2 == cli.EXIT_OK


# ---- F: restore test never overwrites production ----------------------------


def test_F_restore_test_never_touches_production_path(tmp_path, fake_r2):
    import backup_database as cli

    primary = _make_primary_db(tmp_path)
    primary_bytes_before = primary.read_bytes()
    staging = tmp_path / "staging"
    cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    object_key = next(k for (b, k) in fake_r2.objects if k.endswith(".db"))

    exit_code = cli.main(["--db-path", str(primary), "--restore-test", "--object-key", object_key])
    assert exit_code == cli.EXIT_OK
    assert primary.read_bytes() == primary_bytes_before  # byte-for-byte untouched


# ---- J: upload failure never reported as success -----------------------------


def test_J_upload_failure_not_treated_as_success(tmp_path, fake_r2):
    import backup_database as cli

    primary = _make_primary_db(tmp_path)
    staging = tmp_path / "staging"
    fake_r2.upload_should_fail = True
    exit_code = cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    assert exit_code != cli.EXIT_OK
    assert len(fake_r2.objects) == 0


# ---- K: download/checksum-mismatch restore fails ------------------------------


def test_K_checksum_mismatch_fails_restore(tmp_path, fake_r2):
    import backup_database as cli

    primary = _make_primary_db(tmp_path)
    staging = tmp_path / "staging"
    cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    object_key = next(k for (b, k) in fake_r2.objects if k.endswith(".db"))

    # Corrupt the "remote" object after upload, before restore -- simulates a
    # backup that uploaded successfully but is not actually retrievable intact.
    fake_r2.objects[("test-bucket", object_key)] = b"corrupted bytes, not a real sqlite db"

    exit_code = cli.main(["--db-path", str(primary), "--restore-test", "--object-key", object_key])
    assert exit_code != cli.EXIT_OK


def test_K_download_failure_fails_restore(tmp_path, fake_r2):
    import backup_database as cli

    primary = _make_primary_db(tmp_path)
    staging = tmp_path / "staging"
    cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    object_key = next(k for (b, k) in fake_r2.objects if k.endswith(".db"))

    fake_r2.download_should_fail = True
    exit_code = cli.main(["--db-path", str(primary), "--restore-test", "--object-key", object_key])
    assert exit_code != cli.EXIT_OK


# ---- R: capacity alert never auto-deletes -------------------------------------


def test_R_capacity_alert_never_deletes_backups(tmp_path, fake_r2, monkeypatch):
    import backup_database as cli
    import db.r2_client as r2_client_module

    # Force a >=100% usage report.
    monkeypatch.setattr(r2_client_module, "get_bucket_usage_bytes", lambda client, bucket, prefix=None: 20 * (1024 ** 3))

    primary = _make_primary_db(tmp_path)
    staging = tmp_path / "staging"
    objects_before_count = len(fake_r2.objects)
    cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    # The backup+manifest just uploaded are still present -- nothing was
    # deleted as a side effect of a high capacity reading.
    assert len(fake_r2.objects) == objects_before_count + 2


def test_capacity_only_reports_status_without_creating_backup(tmp_path, fake_r2):
    import backup_database as cli

    exit_code = cli.main(["--capacity-only"])
    assert exit_code == cli.EXIT_OK
    assert len(fake_r2.objects) == 0  # no backup was created


def test_r2_not_configured_stops_before_upload(tmp_path, monkeypatch):
    import backup_database as cli
    import db.r2_client as r2_client_module

    monkeypatch.setattr(r2_client_module, "is_configured", lambda: False)
    primary = _make_primary_db(tmp_path)
    staging = tmp_path / "staging"
    exit_code = cli.main(["--type", "manual", "--db-path", str(primary), "--staging-dir", str(staging)])
    assert exit_code == cli.EXIT_R2_CONFIG_REQUIRED
    # local snapshot must still exist -- only the upload step was skipped.
    snapshots = list(staging.glob("*.db"))
    assert len(snapshots) == 1

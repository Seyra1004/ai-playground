"""TEST Z, AA, AB from the Phase 2B test matrix: CLI --validate-config is
side-effect-free, invalid config exits non-zero, and a global run failure
(e.g. duplicate run_id) exits non-zero — driven through main() directly
(no subprocess), with the real network boundary (requests.request) mocked
so no test ever makes an external call."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_ingestion as cli  # noqa: E402

VALID_REGISTRY = """
sources:
  - source_name: source_a
    enabled: true
    source_type: rss
    category: AI_NEWS
    region: GLOBAL
    endpoint: https://example.com/feed.xml
    timeout_seconds: 10
    retry:
      max_attempts: 2
      backoff_base_seconds: 0.01
      backoff_jitter_seconds: 0.0
    auth:
      mode: none
"""

INVALID_REGISTRY = "sources: [not: valid: [yaml"


class _FakeFeedResponse:
    status_code = 200
    content = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title><link>https://example.com</link>
<description>d</description></channel></rss>"""


def _write(tmp_path, content, name="sources.yaml"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ---- TEST Z: --validate-config makes no network call and no DB write --------


def test_Z_validate_config_is_side_effect_free(tmp_path):
    registry_path = _write(tmp_path, VALID_REGISTRY)
    db_path = tmp_path / "should_not_be_created.db"

    with patch("requests.request") as mock_request:
        exit_code = cli.main(["--registry", str(registry_path), "--validate-config", "--db-path", str(db_path)])

    assert exit_code == cli.EXIT_OK
    mock_request.assert_not_called()
    assert not db_path.exists()


# ---- TEST AA: invalid config -> clear non-zero (config-error) exit ----------


def test_AA_invalid_config_exits_with_config_error_code(tmp_path):
    registry_path = _write(tmp_path, INVALID_REGISTRY)
    exit_code = cli.main(["--registry", str(registry_path), "--validate-config"])
    assert exit_code == cli.EXIT_CONFIG_ERROR


def test_AA_invalid_config_in_live_mode_also_exits_config_error_with_no_db_created(tmp_path):
    registry_path = _write(tmp_path, INVALID_REGISTRY)
    db_path = tmp_path / "should_not_be_created.db"
    exit_code = cli.main(["--registry", str(registry_path), "--db-path", str(db_path)])
    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert not db_path.exists()


# ---- TEST AB: a global run failure (duplicate run_id) exits non-zero --------


def test_AB_duplicate_run_id_via_cli_exits_non_zero(tmp_path):
    registry_path = _write(tmp_path, VALID_REGISTRY)
    db_path = tmp_path / "test.db"

    with patch("requests.request", return_value=_FakeFeedResponse()):
        first_exit = cli.main(["--registry", str(registry_path), "--run-id", "cli-run-1", "--db-path", str(db_path)])
        second_exit = cli.main(["--registry", str(registry_path), "--run-id", "cli-run-1", "--db-path", str(db_path)])

    assert first_exit == cli.EXIT_OK
    assert second_exit == cli.EXIT_RUN_FAILURE


def test_successful_live_run_via_cli_exits_ok_and_writes_expected_rows(tmp_path):
    registry_path = _write(tmp_path, VALID_REGISTRY)
    db_path = tmp_path / "test.db"

    with patch("requests.request", return_value=_FakeFeedResponse()):
        exit_code = cli.main(["--registry", str(registry_path), "--run-id", "cli-run-ok", "--db-path", str(db_path)])

    assert exit_code == cli.EXIT_OK
    import sqlite3

    raw = sqlite3.connect(db_path)
    try:
        run_row = raw.execute("SELECT status FROM runs WHERE run_id='cli-run-ok'").fetchone()
        assert run_row == ("completed",)
        status_row = raw.execute(
            "SELECT status FROM run_source_status WHERE source_name='source_a'"
        ).fetchone()
        assert status_row == ("SUCCESS",)
    finally:
        raw.close()


def test_all_disabled_registry_via_cli_exits_run_failure_with_no_run_created(tmp_path):
    disabled_registry = VALID_REGISTRY.replace("enabled: true", "enabled: false")
    registry_path = _write(tmp_path, disabled_registry)
    db_path = tmp_path / "test.db"

    with patch("requests.request") as mock_request:
        exit_code = cli.main(["--registry", str(registry_path), "--db-path", str(db_path)])

    assert exit_code == cli.EXIT_RUN_FAILURE
    mock_request.assert_not_called()

    import sqlite3

    raw = sqlite3.connect(db_path)
    try:
        count = raw.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert count == 0
    finally:
        raw.close()

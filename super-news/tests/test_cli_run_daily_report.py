"""scripts/run_daily_report.py: exit-code mapping, thin CLI->DB->orchestrator
integration. LLM is mocked via report.orchestrator.build_llm -- no live
network/API call."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_report as cli  # noqa: E402

from report.llm_interface import LLMResponse, StructuredLLM


class FakeLLM(StructuredLLM):
    def generate_structured(self, system_prompt, user_prompt, schema):
        return LLMResponse(
            parsed={"AI": [], "ECONOMY": [], "SOCIETY": []},
            raw_text="{}", model_used="fake-model", input_tokens=1, output_tokens=1,
        )


def _seed_music(db_path):
    from db.database import connect, init_db

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'B', 'ORIGINAL', 'RESOLVED', '2026-08-12T00:00:00+00:00', 'apple_music')"""
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, 'apple_music', 'apple_music_chart_position', 1, 'chart_position', 'KR',
                   'MEASURED_PLATFORM_SIGNAL', '2026-08-12T00:00:00+00:00', '2026-08-12T00:00:00+00:00')""",
        (entity_id,),
    )
    conn.commit()
    conn.close()


# ---- successful outcome -> exit 0 -------------------------------------------


def test_successful_run_exits_ok(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_music(db_path)
    exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_OK


# ---- failed outcome (all-empty day) -> exit 1 -------------------------------


def test_all_empty_day_exits_run_failure(tmp_path):
    db_path = tmp_path / "test.db"
    exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_RUN_FAILURE


# ---- invalid invocation -> exit 2 (argparse) --------------------------------


def test_invalid_invocation_exits_config_error():
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--bogus-flag"])
    assert excinfo.value.code == cli.EXIT_CONFIG_ERROR


# ---- --db-path override honored; default DB untouched ----------------------


def test_db_path_override_honored_default_untouched(tmp_path, monkeypatch):
    custom_db = tmp_path / "custom.db"
    decoy_default_db = tmp_path / "should_not_be_touched.db"
    monkeypatch.setattr(cli, "DB_PATH", decoy_default_db)
    _seed_music(custom_db)

    exit_code = cli.main(["--db-path", str(custom_db)])
    assert exit_code == cli.EXIT_OK
    assert custom_db.exists()
    assert not decoy_default_db.exists()


# ---- thin CLI -> DB -> orchestrator integration, real writes ----------------


def test_thin_integration_writes_real_rows_and_calls_llm(tmp_path):
    db_path = tmp_path / "test.db"
    from db.database import connect, init_db

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, title, collected_at)
           VALUES ('s', 'k1', 'rss', 'https://x', 'AI news', '2026-08-12T00:00:00+00:00')"""
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, 'AI_NEWS', 'ev-1', 'AI news', '2026-08-12T00:00:00+00:00')""",
        (raw_id,),
    )
    conn.commit()
    conn.close()

    with patch("report.orchestrator.build_llm", return_value=FakeLLM()):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_OK

    raw = sqlite3.connect(db_path)
    try:
        run_count = raw.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE 'daily-report-%'").fetchone()[0]
        assert run_count == 1
        interp_count = raw.execute("SELECT COUNT(*) FROM llm_interpretations").fetchone()[0]
        assert interp_count == 1
        report_categories = {
            row[0] for row in raw.execute("SELECT category FROM reports").fetchall()
        }
        assert report_categories == {"AI", "ECONOMY", "SOCIETY"}
    finally:
        raw.close()

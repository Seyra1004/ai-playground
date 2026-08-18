"""report.llm_usage_summary: read-only aggregation, never invents a
token/cost figure when the underlying data doesn't have one."""

import pytest

from db.database import connect, init_db
from report.llm_usage_summary import summarize_llm_usage


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id="run-1", run_date="2026-08-18"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_interpretation(conn, run_row_id, category, model_used, input_tokens, output_tokens, estimated_cost):
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at,
            input_tokens, output_tokens, estimated_cost)
           VALUES (?, ?, ?, 'v1', '{}', 'MEDIUM', 'x', ?, ?, ?)""",
        (run_row_id, category, model_used, input_tokens, output_tokens, estimated_cost),
    )
    conn.commit()


def test_honest_empty_when_no_llm_calls_that_day(conn):
    summary = summarize_llm_usage(conn, "2026-08-18")
    assert summary["calls"] == 0
    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert summary["total_tokens"] is None
    assert summary["estimated_cost_usd"] is None
    assert summary["token_usage_available"] is False
    assert summary["purposes"] == {}


def test_never_reports_zero_when_tokens_are_unknown(conn):
    """A real subscription/CLI call whose usage dict lacked a field must
    show as None ('unknown'), never fabricated as 0."""
    run_row_id = _insert_run(conn)
    _insert_interpretation(conn, run_row_id, "MUSIC_PRODUCER_INTELLIGENCE", "claude-sonnet-5", None, None, None)
    summary = summarize_llm_usage(conn, "2026-08-18")
    assert summary["calls"] == 1
    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert summary["estimated_cost_usd"] is None
    assert summary["token_usage_available"] is False
    assert summary["purposes"] == {"producer_ar_intelligence": 1}


def test_real_reported_tokens_are_summed_and_purposes_distinguished(conn):
    run_row_id = _insert_run(conn)
    _insert_interpretation(conn, run_row_id, "MUSIC_TREND_INTELLIGENCE", "claude-sonnet-5", 2, 4312, None)
    _insert_interpretation(conn, run_row_id, "MUSIC_PRODUCER_INTELLIGENCE", "claude-sonnet-5", 2, 3617, None)
    summary = summarize_llm_usage(conn, "2026-08-18")
    assert summary["calls"] == 2
    assert summary["input_tokens"] == 4
    assert summary["output_tokens"] == 7929
    assert summary["total_tokens"] == 7933
    assert summary["token_usage_available"] is True
    assert summary["estimated_cost_usd"] is None  # never guessed for a subscription/CLI call
    assert summary["purposes"] == {"music_trend_intelligence": 1, "producer_ar_intelligence": 1}
    assert summary["models"] == ["claude-sonnet-5"]


def test_real_reported_cost_is_summed_when_present(conn):
    run_row_id = _insert_run(conn)
    _insert_interpretation(conn, run_row_id, "MUSIC_PRODUCER_INTELLIGENCE", "claude-sonnet-5", 100, 200, 0.0015)
    summary = summarize_llm_usage(conn, "2026-08-18")
    assert summary["estimated_cost_usd"] == pytest.approx(0.0015)


def test_only_counts_calls_for_the_requested_run_date(conn):
    run_row_id_other = _insert_run(conn, run_id="run-other", run_date="2026-08-17")
    _insert_interpretation(conn, run_row_id_other, "MUSIC_TREND_INTELLIGENCE", "claude-sonnet-5", 10, 20, None)
    summary = summarize_llm_usage(conn, "2026-08-18")
    assert summary["calls"] == 0


def test_translation_cache_hits_never_counted_only_new_rows_that_day(conn):
    conn.execute(
        """INSERT INTO translation_cache
           (cache_key, target_lang, original_text, translated_text, status, attempt_count, provider, created_at, updated_at)
           VALUES ('k1', 'ko', 'hello', '안녕', 'TRANSLATED', 1, 'claude_cli', '2026-08-18T01:00:00+00:00', '2026-08-18T01:00:00+00:00')""",
    )
    conn.execute(
        """INSERT INTO translation_cache
           (cache_key, target_lang, original_text, translated_text, status, attempt_count, provider, created_at, updated_at)
           VALUES ('k2', 'ko', 'world', '세계', 'TRANSLATED', 1, 'claude_cli', '2026-08-15T01:00:00+00:00', '2026-08-18T01:00:00+00:00')""",
    )
    conn.commit()
    summary = summarize_llm_usage(conn, "2026-08-18")
    assert summary["purposes"] == {"translation": 1}
    assert summary["calls"] == 1

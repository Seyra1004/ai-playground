"""report.orchestrator: end-to-end integration with an injected FakeLLM
(never a live network/API call). Covers: valid-selection success path,
total-LLM-failure semantics, zero-news + zero-music aggregate behavior."""

import pytest

from db.database import connect, init_db
from report.llm_interface import LLMResponse, StructuredLLM
from report.orchestrator import run_daily_report


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


class FakeLLM(StructuredLLM):
    def __init__(self, response=None, raise_exc=None):
        self.calls = 0
        self._response = response
        self._raise_exc = raise_exc

    def generate_structured(self, system_prompt, user_prompt, schema):
        self.calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


def _insert_ai_candidate(conn, key="k1"):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, title, collected_at)
           VALUES ('s', ?, 'rss', 'https://x', 'AI news', '2026-08-11T16:00:00+00:00')""",
        (key,),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        # normalized_items.category is the collection-layer source category
        # (see report.candidate_selection.NEWS_CATEGORY_SOURCE_MAP) -- the
        # report-output category 'AI' is what select_news_candidates() is
        # called with, never what's stored here.
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, 'AI_NEWS', ?, 'AI news', '2026-08-11T16:00:00+00:00')""",
        (raw_id, f"ev-{key}"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---- valid selection produces a report --------------------------------------


def test_valid_selection_produces_report_and_completed_run(conn):
    item_id = _insert_ai_candidate(conn)
    llm = FakeLLM(response=LLMResponse(
        parsed={"AI": [{"id": item_id, "reason": "중요한 소식이다"}], "ECONOMY": [], "SOCIETY": []},
        raw_text="{}", model_used="fake-model", input_tokens=1, output_tokens=1,
    ))
    result = run_daily_report(conn, "run-1", run_date="2026-08-12", llm=llm)
    assert result["status"] == "completed"
    assert result["category_outcomes"]["AI"]["status"] == "REPORT_GENERATED"
    assert llm.calls == 1


# ---- total-LLM-failure semantics --------------------------------------------


def test_total_llm_failure_marks_categories_with_candidates_failed(conn):
    _insert_ai_candidate(conn)
    llm = FakeLLM(raise_exc=RuntimeError("connection reset"))
    result = run_daily_report(conn, "run-2", run_date="2026-08-12", llm=llm)

    assert result["category_outcomes"]["AI"]["status"] == "REPORT_FAILED"
    assert result["category_outcomes"]["ECONOMY"]["status"] == "NOT_READY"  # no candidates -> not a failure
    assert result["category_outcomes"]["SOCIETY"]["status"] == "NOT_READY"
    assert result["category_outcomes"]["MUSIC"]["status"] == "NOT_READY"
    # Every enabled (non-skipped) result failed -> run-level "failed".
    assert result["status"] == "failed"

    row = conn.execute(
        "SELECT failure_stage, failure_reason FROM run_category_status WHERE run_id=? AND category='AI'",
        (result["runs_row_id"],),
    ).fetchone()
    assert row["failure_stage"] == "LLM"
    assert "connection reset" in row["failure_reason"]


# ---- zero-news + zero-music: documented aggregate-status edge case ---------


def test_zero_news_and_zero_music_is_all_not_ready(conn):
    llm = FakeLLM()  # never called -- zero candidates everywhere
    result = run_daily_report(conn, "run-3", run_date="2026-08-12", llm=llm)
    assert llm.calls == 0
    for category in ("AI", "ECONOMY", "SOCIETY", "MUSIC"):
        assert result["category_outcomes"][category]["status"] == "NOT_READY"
    # ingestion.orchestrator._aggregate_run_status treats "all results
    # SKIPPED" as "failed"/"no_enabled_source_results" -- reused as-is, not
    # reinterpreted here. Documented, not silently worked around.
    assert result["status"] == "failed"


def test_music_success_alone_makes_run_completed(conn):
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

    llm = FakeLLM()  # zero news candidates -> never called
    result = run_daily_report(conn, "run-4", run_date="2026-08-12", llm=llm)
    assert result["category_outcomes"]["MUSIC"]["status"] == "REPORT_GENERATED"
    assert result["status"] == "completed"

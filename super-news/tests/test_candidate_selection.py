"""report.candidate_selection: determinism, event_key dedup, source_count,
previous-day-only stale exclusion, KST day-boundary correctness."""

import pytest

from db.database import connect, init_db
from report.candidate_selection import select_news_candidates


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id, run_date="2026-08-12"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_item(conn, source_name, source_item_key, category, event_key, title,
                  collected_at, entity_name=None):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url,
              title, collected_at, category)
           VALUES (?, ?, 'rss', 'https://x/'||?, ?, ?, ?)""",
        (source_name, source_item_key, source_item_key, title, collected_at, category),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items
           (raw_item_id, category, event_key, entity_name, normalized_title, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (raw_id, category, event_key, entity_name, title, collected_at),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---- KST boundary ------------------------------------------------------


def test_kst_boundary_excludes_items_outside_the_kst_day(conn):
    # 2026-08-11 23:59:59 KST == 2026-08-11 14:59:59 UTC -- previous KST day.
    _insert_item(conn, "s1", "k1", "AI_NEWS","ev-before", "before", "2026-08-11T14:59:59+00:00")
    # 2026-08-12 00:00:00 KST == 2026-08-11 15:00:00 UTC -- start of target KST day.
    _insert_item(conn, "s1", "k2", "AI_NEWS","ev-start", "start", "2026-08-11T15:00:00+00:00")
    # 2026-08-13 00:00:00 KST == 2026-08-12 15:00:00 UTC -- start of the NEXT KST day.
    _insert_item(conn, "s1", "k3", "AI_NEWS","ev-after", "after", "2026-08-12T15:00:00+00:00")

    result = select_news_candidates(conn, ["AI"], "2026-08-12")
    keys = {c["event_key"] for c in result["AI"]}
    assert keys == {"ev-start"}


# ---- event_key dedup + source_count -------------------------------------


def test_event_key_dedup_and_source_count(conn):
    _insert_item(conn, "source_a", "k1", "AI_NEWS","ev-1", "title", "2026-08-11T16:00:00+00:00")
    _insert_item(conn, "source_b", "k2", "AI_NEWS","ev-1", "title", "2026-08-11T17:00:00+00:00")
    _insert_item(conn, "source_a", "k3", "AI_NEWS","ev-2", "other", "2026-08-11T16:30:00+00:00")

    result = select_news_candidates(conn, ["AI"], "2026-08-12")
    by_key = {c["event_key"]: c for c in result["AI"]}
    assert by_key["ev-1"]["source_count"] == 2
    assert len(by_key["ev-1"]["item_ids"]) == 2
    assert by_key["ev-2"]["source_count"] == 1


# ---- deterministic ordering ----------------------------------------------


def test_candidate_ordering_is_deterministic(conn):
    _insert_item(conn, "s1", "k1", "AI_NEWS","ev-b", "b", "2026-08-11T16:00:00+00:00")
    _insert_item(conn, "s1", "k2", "AI_NEWS","ev-a", "a", "2026-08-11T16:00:00+00:00")
    _insert_item(conn, "s2", "k3", "AI_NEWS","ev-a", "a", "2026-08-11T16:01:00+00:00")

    first = select_news_candidates(conn, ["AI"], "2026-08-12")
    second = select_news_candidates(conn, ["AI"], "2026-08-12")
    assert first == second
    # ev-a has source_count=2 (higher priority) so it sorts first.
    assert [c["event_key"] for c in first["AI"]] == ["ev-a", "ev-b"]


# ---- previous-day-only stale exclusion -----------------------------------


def test_previous_day_selected_event_key_is_excluded(conn):
    run_row_id = _insert_run(conn, "run-prev", "2026-08-11")
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'NEWS_COMBINED', 'test-model', 'v1', '{}', 'HIGH', 'x')""",
        (run_row_id,),
    )
    interp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    item_id = _insert_item(conn, "s1", "k-old", "AI_NEWS","ev-repeat", "repeat", "2026-08-10T16:00:00+00:00")
    conn.execute(
        "INSERT INTO interpretation_items (interpretation_id, normalized_item_id) VALUES (?, ?)",
        (interp_id, item_id),
    )
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, '2026-08-11', 'AI', 'AI', 'body', 'hash', 'x')""",
        (run_row_id,),
    )
    conn.commit()

    # Same event_key resurfaces the next KST day -- must be excluded.
    _insert_item(conn, "s1", "k-new", "AI_NEWS","ev-repeat", "repeat", "2026-08-11T16:00:00+00:00")
    # A two-days-ago report existing must NOT affect exclusion for a
    # different, unrelated event_key.
    _insert_item(conn, "s1", "k-fresh", "AI_NEWS","ev-fresh", "fresh", "2026-08-11T16:00:00+00:00")

    result = select_news_candidates(conn, ["AI"], "2026-08-12")
    keys = {c["event_key"] for c in result["AI"]}
    assert keys == {"ev-fresh"}


def test_no_previous_report_means_no_exclusion(conn):
    _insert_item(conn, "s1", "k1", "AI_NEWS","ev-1", "title", "2026-08-11T16:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-12")
    assert len(result["AI"]) == 1


# ---- zero-news behavior ---------------------------------------------------


def test_category_with_no_items_returns_empty_list_not_missing_key(conn):
    result = select_news_candidates(conn, ["AI", "ECONOMY", "SOCIETY"], "2026-08-12")
    assert result == {"AI": [], "ECONOMY": [], "SOCIETY": []}


# ---- report-category -> normalized_items source-category mapping ----------


def test_AI_reads_AI_NEWS(conn):
    _insert_item(conn, "s1", "k1", "AI_NEWS", "ev-1", "title", "2026-08-11T16:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-12")
    assert len(result["AI"]) == 1


def test_ECONOMY_reads_ECONOMY_NEWS(conn):
    _insert_item(conn, "s1", "k1", "ECONOMY_NEWS", "ev-1", "title", "2026-08-11T16:00:00+00:00")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-12")
    assert len(result["ECONOMY"]) == 1


def test_SOCIETY_reads_SOCIETY_NEWS(conn):
    _insert_item(conn, "s1", "k1", "SOCIETY_NEWS", "ev-1", "title", "2026-08-11T16:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-12")
    assert len(result["SOCIETY"]) == 1


def test_a_category_never_reads_a_different_categorys_source_rows(conn):
    # An ECONOMY_NEWS-labeled row must never leak into the AI report,
    # and vice versa -- proves the mapping is per-category, not a
    # blanket "any news category" query.
    _insert_item(conn, "s1", "k1", "ECONOMY_NEWS", "ev-econ", "econ title", "2026-08-11T16:00:00+00:00")
    _insert_item(conn, "s1", "k2", "AI_NEWS", "ev-ai", "ai title", "2026-08-11T16:00:00+00:00")
    result = select_news_candidates(conn, ["AI", "ECONOMY", "SOCIETY"], "2026-08-12")
    assert [c["event_key"] for c in result["AI"]] == ["ev-ai"]
    assert [c["event_key"] for c in result["ECONOMY"]] == ["ev-econ"]
    assert result["SOCIETY"] == []


def test_returned_keys_are_report_output_categories_not_source_categories(conn):
    _insert_item(conn, "s1", "k1", "AI_NEWS", "ev-1", "title", "2026-08-11T16:00:00+00:00")
    result = select_news_candidates(conn, ["AI", "ECONOMY", "SOCIETY"], "2026-08-12")
    assert set(result.keys()) == {"AI", "ECONOMY", "SOCIETY"}
    # The candidate dicts themselves also carry the report-output category,
    # never the source category string.
    assert result["AI"][0]["category"] == "AI"


def test_unknown_report_category_fails_clearly():
    from report.candidate_selection import _source_category

    with pytest.raises(ValueError, match="Unknown report category"):
        _source_category("NOT_A_REAL_CATEGORY")


def test_unknown_report_category_passed_to_select_news_candidates_raises(conn):
    with pytest.raises(ValueError, match="Unknown report category"):
        select_news_candidates(conn, ["NOT_A_REAL_CATEGORY"], "2026-08-12")

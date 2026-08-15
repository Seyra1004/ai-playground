"""report.web_data_v2: TikTok honestly UNAVAILABLE, Spotify real chart
data, intelligence sections honest-empty absent real evidence."""

import json

import pytest

from db.database import connect, init_db
from report.web_data_v2 import (
    MUSIC_TREND_INTELLIGENCE_CATEGORY,
    PRODUCER_INTELLIGENCE_CATEGORY,
    build_dashboard_data_v2,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_spotify_observation(conn, rank, observed_at, spotify_id="1"):
    existing = conn.execute(
        "SELECT music_entity_id FROM music_entity_aliases WHERE alias_type='SPOTIFY_ID' AND alias_value=?",
        (spotify_id,),
    ).fetchone()
    if existing:
        entity_id = existing["music_entity_id"]
    else:
        conn.execute(
            """INSERT INTO music_entities
               (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
               VALUES ('Artist', 'Title', 'ORIGINAL', 'RESOLVED', ?, 'spotify_chart')""",
            (observed_at,),
        )
        entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO music_entity_aliases (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
               VALUES (?, 'SPOTIFY_ID', ?, 'spotify_chart', ?)""",
            (entity_id, spotify_id, observed_at),
        )
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, 'spotify_chart', 'spotify_chart_rank', ?, 'chart_rank', 'GLOBAL',
                   'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, rank, observed_at, observed_at),
    )
    conn.commit()
    return entity_id


def test_empty_db_never_raises_and_reports_honest_states(conn):
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["report_date_kst"] == "2026-08-13"
    assert data["tiktok_chart"]["state"] == "UNAVAILABLE"
    assert data["spotify_chart"]["state"] == "UNAVAILABLE"
    assert data["intelligence"]["cross_platform"] == []
    for category in ("AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        assert data["news"][category]["state"] == "DEGRADED"


def test_tiktok_chart_always_unavailable_never_fabricated(conn):
    _insert_spotify_observation(conn, 1, "2026-08-13T00:00:00+00:00")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["tiktok_chart"] == {"state": "UNAVAILABLE", "top10": [], "new_entries": [], "trend": None}


def test_spotify_chart_reflects_real_data(conn):
    _insert_spotify_observation(conn, 1, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["spotify_chart"]["state"] == "NORMAL"
    assert len(data["spotify_chart"]["top10"]) == 1
    # Only one snapshot exists at all (no prior observation to diff
    # against) -- this is a first-observation baseline, not a real NEW
    # chart entry, so new_entries must be empty and the entry itself
    # carries is_first_observation=True (see music.signal_engine.
    # compute_chart_diff).
    assert data["spotify_chart"]["is_first_observation"] is True
    assert data["spotify_chart"]["new_entries"] == []
    # V2 data-boundary contract (credential-independent architecture
    # audit): is_new is True ONLY for status == "NEW" -- a FIRST_OBSERVED
    # baseline entry (no real prior data to compare against at all) must
    # never read as is_new == True, so a future consumer reading is_new
    # alone can't misreport a baseline day as a real new chart entry.
    assert data["spotify_chart"]["top10"][0]["status"] == "FIRST_OBSERVED"
    assert data["spotify_chart"]["top10"][0]["is_new"] is False
    assert data["spotify_chart"]["top10"][0]["is_first_observation"] is True


def test_intelligence_forecast_outlook_is_insufficient_history_when_thin(conn):
    _insert_spotify_observation(conn, 1, "2026-08-13T00:00:00+00:00")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["intelligence"]["outlook"]["spotify_chart"]["status"] == "INSUFFICIENT_HISTORY"


def test_intelligence_early_signal_and_catalog_revival_are_per_source_dicts(conn):
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert set(data["intelligence"]["early_signal"].keys()) == {"apple_music", "spotify_chart"}
    assert set(data["intelligence"]["catalog_revival"].keys()) == {"apple_music", "spotify_chart"}


# ---- V2.1: outlook progress_ratio -------------------------------------------


def test_outlook_progress_ratio_derived_from_real_days_of_history(conn):
    data = build_dashboard_data_v2(conn, "2026-08-13")
    outlook = data["intelligence"]["outlook"]["spotify_chart"]
    assert outlook["progress_ratio"] == outlook["days_of_history"] / outlook["min_required_days"]


# ---- V2.1: spotify_chart entry enrichment + trend ---------------------------


def test_spotify_top10_entries_carry_peak_rank_and_days_on_chart(conn):
    _insert_spotify_observation(conn, 5, "2026-08-12T00:00:00+00:00", spotify_id="1")
    _insert_spotify_observation(conn, 2, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["peak_rank"] == 2  # MIN(metric_value) across both observations
    assert entry["days_on_chart"] == 2


def test_spotify_trend_present_and_none_when_unavailable(conn):
    data_empty = build_dashboard_data_v2(conn, "2026-08-13")
    assert data_empty["spotify_chart"]["trend"] is None

    _insert_spotify_observation(conn, 1, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    trend = data["spotify_chart"]["trend"]
    # First-observation day (only one snapshot exists) -- honest baseline,
    # not a real NEW entry; see test_spotify_chart_reflects_real_data.
    assert trend["new_count"] == 0
    assert trend["first_observation_count"] == 1
    assert trend["volatility"] in ("LOW", "MEDIUM", "HIGH")


# ---- V2.1: news item snippet/source_count/tier ------------------------------


def _insert_run(conn, run_id="run-1", run_date="2026-08-13"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_normalized_item(conn, key, category, title, snippet=None, source_name="s1",
                             collected_at="2026-08-13T01:00:00+00:00", event_key=None, published_at=None):
    conn.execute(
        """INSERT INTO raw_items
           (source_name, source_item_key, source_type, source_url, title, snippet, published_at, collected_at)
           VALUES (?, ?, 'rss', ?, ?, ?, ?, ?)""",
        (source_name, key, f"https://example.com/{key}", title, snippet, published_at, collected_at),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (raw_id, category, event_key or f"ev-{key}", title, collected_at),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_interpretation(conn, run_row_id, output_dict):
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'NEWS_COMBINED', 'test-model', 'v1', ?, 'MEDIUM', 'x')""",
        (run_row_id, json.dumps(output_dict)),
    )
    conn.commit()


def _insert_reports_marker(conn, run_row_id, report_date="2026-08-13", category="AI"):
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, ?, ?, ?, 'x', 'hash', 'x')""",
        (run_row_id, report_date, category, category),
    )
    conn.commit()


def _insert_category_status(conn, run_row_id, category, status, items_collected, items_selected):
    conn.execute(
        """INSERT INTO run_category_status
           (run_id, category, status, items_collected, items_selected, retry_count)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (run_row_id, category, status, items_collected, items_selected),
    )
    conn.commit()


def test_first_selected_item_is_tier_lead_second_is_standard_third_is_brief(conn):
    run_row_id = _insert_run(conn)
    id1 = _insert_normalized_item(conn, "a1", "AI", "First story")
    id2 = _insert_normalized_item(conn, "a2", "AI", "Second story")
    id3 = _insert_normalized_item(conn, "a3", "AI", "Third story")
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 3, 3)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "r1"}, {"id": id2, "reason": "r2"}, {"id": id3, "reason": "r3"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["AI"]["items"]
    assert [i["tier"] for i in items] == ["LEAD", "STANDARD", "BRIEF"]


def test_snippet_dropped_when_redundant_with_reason(conn):
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "a1", "AI", "Story title", snippet="Story title")
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "Story title"}],  # reason == snippet, redundant
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["snippet"] is None


def test_snippet_kept_when_distinct_from_reason(conn):
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(
        conn, "a1", "AI", "Story title", snippet="A much longer original summary from the RSS feed"
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "matters for producers"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["snippet"] == "A much longer original summary from the RSS feed"


def test_source_count_reflects_distinct_outlets_same_day(conn):
    run_row_id = _insert_run(conn)
    # Two raw_items from two different outlets, same event_key, same day.
    id1 = _insert_normalized_item(conn, "a1", "AI", "Story", source_name="outlet-a", event_key="ev-shared")
    _insert_normalized_item(conn, "a2", "AI", "Story", source_name="outlet-b", event_key="ev-shared")
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 2, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "covered widely"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["source_count"] == 2


def test_source_count_scoped_to_kst_day_window_not_all_history(conn):
    run_row_id = _insert_run(conn)
    id1 = _insert_normalized_item(
        conn, "a1", "AI", "Story", source_name="outlet-a", event_key="ev-recurring",
        collected_at="2026-08-13T01:00:00+00:00",
    )
    # Same event_key, but collected on an EARLIER day -- must not inflate today's count.
    _insert_normalized_item(
        conn, "a2", "AI", "Story", source_name="outlet-b", event_key="ev-recurring",
        collected_at="2026-08-01T01:00:00+00:00",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "r"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["source_count"] == 1


# ---- V2.1: producer_intelligence read ---------------------------------------


def test_producer_intelligence_unavailable_when_no_row(conn):
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"] == {"state": "UNAVAILABLE", "insights": []}


def test_producer_intelligence_normal_when_valid_row_exists(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('pi-run', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    output = {"insights": [{
        "what_is_moving": "Test hook-first intro", "why_it_matters": "signals agree",
        "what_to_watch": "next observation", "what_could_i_make_now": "a demo hook",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY, json.dumps(output)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"]["state"] == "NORMAL"
    assert data["producer_intelligence"]["insights"][0]["what_is_moving"] == "Test hook-first intro"


def test_producer_intelligence_resolves_evidence_refs_to_readable_summaries(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('pi-run3', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = {
        "insights": [{
            "what_is_moving": "Test hook-first intro", "why_it_matters": "signals agree",
            "what_to_watch": "next observation", "what_could_i_make_now": "a demo hook",
            "evidence_refs": ["E1"], "confidence": "MEDIUM",
        }],
        "catalog": [{"ref": "E1", "type": "EARLY_SIGNAL", "summary": "[spotify_chart] Artist - Title (+8 rank)"}],
    }
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY, json.dumps(stored, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["producer_intelligence"]["insights"][0]["evidence"]
    assert evidence == [{"ref": "E1", "summary": "[spotify_chart] Artist - Title (+8 rank)"}]


def test_producer_intelligence_evidence_falls_back_to_bare_ref_when_catalog_missing(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('pi-run4', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = {"insights": [{
        "what_is_moving": "x", "why_it_matters": "y", "what_to_watch": "z", "what_could_i_make_now": "w",
        "evidence_refs": ["E1"], "confidence": "LOW",
    }]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY, json.dumps(stored)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["producer_intelligence"]["insights"][0]["evidence"]
    assert evidence == [{"ref": "E1", "summary": "E1"}]


def test_producer_intelligence_degrades_safely_on_malformed_row(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('pi-run2', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', 'not valid json', 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"] == {"state": "UNAVAILABLE", "insights": []}


# ---- MUSIC INTELLIGENCE COMPLETION: music_trend_intelligence read ----------


def _music_trend_output(**overrides):
    base = {"genre_signals": [], "production_notes": [], "producer_references": [], "kpop_ar_notes": []}
    base.update(overrides)
    return base


def test_music_trend_intelligence_unavailable_when_no_row(conn):
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"] == {
        "state": "UNAVAILABLE", "genre_signals": [], "production_notes": [],
        "producer_references": [], "kpop_ar_notes": [],
    }


def test_music_trend_intelligence_unavailable_when_row_has_all_empty_lists(conn):
    """A real run that legitimately found nothing in any of the 4
    categories is still an honest, valid row -- but with nothing to show,
    the section-level state must read UNAVAILABLE rather than a
    misleading NORMAL-with-nothing-in-it."""
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run0', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(_music_trend_output())),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["state"] == "UNAVAILABLE"


def test_music_trend_intelligence_normal_when_valid_row_has_a_signal(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run1', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    output = _music_trend_output(genre_signals=[{
        "observed": "Article names the genre explicitly", "interpretation": "real listener interest",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["state"] == "NORMAL"
    assert data["music_trend_intelligence"]["genre_signals"][0]["observed"] == "Article names the genre explicitly"
    assert data["music_trend_intelligence"]["production_notes"] == []


def test_music_trend_intelligence_each_category_independently_honest(conn):
    """A signal in ONE category must never force the other 3 to appear
    populated -- each of the 4 lists reads back exactly what was stored
    for it, independently."""
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run2', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    output = _music_trend_output(producer_references=[{
        "observed": "Article states X produced the track", "interpretation": "a real, named credit",
        "evidence_refs": ["E2"], "confidence": "HIGH",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert len(data["music_trend_intelligence"]["producer_references"]) == 1
    assert data["music_trend_intelligence"]["genre_signals"] == []
    assert data["music_trend_intelligence"]["production_notes"] == []
    assert data["music_trend_intelligence"]["kpop_ar_notes"] == []


def test_music_trend_intelligence_resolves_evidence_refs_to_readable_summaries(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run3', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = _music_trend_output(genre_signals=[{
        "observed": "x", "interpretation": "y", "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    stored["catalog"] = [{"ref": "E1", "type": "MUSIC_INDUSTRY_NEWS", "summary": "Real article title — real snippet"}]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(stored, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["music_trend_intelligence"]["genre_signals"][0]["evidence"]
    assert evidence == [{"ref": "E1", "summary": "Real article title — real snippet"}]


def test_music_trend_intelligence_evidence_falls_back_to_bare_ref_when_catalog_missing(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run4', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = _music_trend_output(kpop_ar_notes=[{
        "observed": "x", "interpretation": "y", "evidence_refs": ["E1"], "confidence": "LOW",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(stored)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["music_trend_intelligence"]["kpop_ar_notes"][0]["evidence"]
    assert evidence == [{"ref": "E1", "summary": "E1"}]


def test_music_trend_intelligence_degrades_safely_on_malformed_row(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run5', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', 'not valid json', 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"] == {
        "state": "UNAVAILABLE", "genre_signals": [], "production_notes": [],
        "producer_references": [], "kpop_ar_notes": [],
    }


# ---- V2.1 fact-ownership fields: previous_rank / region / true days_on_chart


def test_previous_rank_derived_correctly_for_a_riser(conn):
    _insert_spotify_observation(conn, 10, "2026-08-12T00:00:00+00:00", spotify_id="1")
    _insert_spotify_observation(conn, 2, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["rank"] == 2
    assert entry["previous_rank"] == 10
    assert entry["rank_delta"] == 8


def test_previous_rank_derived_correctly_for_a_faller(conn):
    _insert_spotify_observation(conn, 2, "2026-08-12T00:00:00+00:00", spotify_id="1")
    _insert_spotify_observation(conn, 6, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["rank"] == 6
    assert entry["previous_rank"] == 2
    assert entry["rank_delta"] == -4


def test_first_observation_entry_never_gets_a_fabricated_previous_rank(conn):
    # A single observation with no prior snapshot at all is a
    # FIRST_OBSERVED baseline (is_new == False per the V2 contract), not a
    # genuine NEW re-entry -- see test_genuine_new_entry_never_gets_a_
    # fabricated_previous_rank below for the real-NEW case.
    _insert_spotify_observation(conn, 3, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["status"] == "FIRST_OBSERVED"
    assert entry["is_new"] is False
    assert entry["previous_rank"] is None


def test_genuine_new_entry_never_gets_a_fabricated_previous_rank(conn):
    # A real second snapshot exists (a baseline already established by a
    # prior day) and a genuinely NEW entity appears in it for the first
    # time -- this IS a real re-entry event, distinct from the
    # FIRST_OBSERVED case above.
    _insert_spotify_observation(conn, 5, "2026-08-12T00:00:00+00:00", spotify_id="baseline")
    _insert_spotify_observation(conn, 3, "2026-08-13T00:00:00+00:00", spotify_id="new-entrant")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = next(e for e in data["spotify_chart"]["top10"] if e["rank"] == 3)
    assert entry["status"] == "NEW"
    assert entry["is_new"] is True
    assert entry["previous_rank"] is None


def test_days_on_chart_counts_distinct_kst_calendar_days_not_raw_observation_rows(conn):
    """Two observation ROWS on the SAME KST calendar day (e.g. a manual
    rerun) must not inflate days_on_chart to 2 -- it must count distinct
    days, matching what "N일째 차트인" honestly claims."""
    _insert_spotify_observation(conn, 5, "2026-08-13T01:00:00+00:00", spotify_id="1")
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES ((SELECT music_entity_id FROM music_entity_aliases WHERE alias_value='1'),
                   NULL, 'spotify_chart', 'spotify_chart_rank', 4, 'chart_rank', 'GLOBAL',
                   'MEASURED_PLATFORM_SIGNAL', '2026-08-13T14:00:00+00:00', '2026-08-13T14:00:00+00:00')"""
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["days_on_chart"] == 1


def test_days_on_chart_counts_two_genuinely_different_kst_days(conn):
    _insert_spotify_observation(conn, 5, "2026-08-12T01:00:00+00:00", spotify_id="1")
    _insert_spotify_observation(conn, 4, "2026-08-13T01:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["days_on_chart"] == 2


def test_region_is_read_verbatim_from_persisted_observation_never_hardcoded(conn):
    _insert_spotify_observation(conn, 1, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["region"] == "GLOBAL"  # exactly what the fixture persisted


def test_chart_entry_carries_shared_observed_at(conn):
    _insert_spotify_observation(conn, 1, "2026-08-13T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["spotify_chart"]["top10"][0]
    assert entry["observed_at"] == data["spotify_chart"]["top10"][0]["observed_at"]
    assert entry["observed_at"] is not None


# ---- News items: source_name / published_at propagated, title-dedup fix ----


def test_news_item_carries_source_name_and_published_at(conn):
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(
        conn, "a1", "AI", "Story title", source_name="TechOutlet",
        collected_at="2026-08-13T01:00:00+00:00", published_at="2026-08-13T00:30:00+00:00",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "r"}], "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["source_name"] == "TechOutlet"
    assert item["published_at"] is not None


def test_snippet_dropped_when_redundant_with_title_even_if_reason_differs(conn):
    """Real bug found in manual review: a snippet identical to the
    HEADLINE (not the LLM reason) was being shown a second time as
    'context.' The dedup check must compare against both."""
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "a1", "AI", "Exact headline text", snippet="Exact headline text")
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "A completely different reason text"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["snippet"] is None


# ---- Cross-platform: real per-source rank detail (entity resolution) -------


def test_cross_platform_entry_carries_real_per_source_rank_detail(conn):
    from music.apple_music import collect_kr_most_played_observations
    from music.derived_signals import compute_velocity_signals
    from music.spotify_chart import collect_global_top_tracks_observations

    def spotify_entry(spotify_id, name, artist, rank):
        return {
            "chartEntryData": {"currentRank": rank, "previousRank": 0, "entryStatus": "MOVED_UP"},
            "trackMetadata": {"trackName": name, "trackUri": f"spotify:track:{spotify_id}",
                               "artists": [{"name": artist}], "releaseDate": ""},
        }

    def apple_song(apple_id, name, artist):
        return {"id": apple_id, "name": name, "artistName": artist, "releaseDate": "2026-01-01",
                "kind": "songs", "artistId": "999", "url": f"https://music.apple.com/kr/song/{apple_id}"}

    day1, day2 = "2026-08-12T00:00:00+00:00", "2026-08-13T00:00:00+00:00"
    collect_global_top_tracks_observations(conn, [spotify_entry("sp5", "Hype Boy", "NewJeans", 9)],
                                            "2026-08-12", observed_at=day1)
    collect_kr_most_played_observations(
        conn, [apple_song("f1", "Filler", "Filler"), apple_song("am5", "Hype Boy", "NewJeans")], observed_at=day1
    )
    collect_global_top_tracks_observations(conn, [spotify_entry("sp5", "Hype Boy", "NewJeans", 3)],
                                            "2026-08-13", observed_at=day2)
    collect_kr_most_played_observations(conn, [apple_song("am5", "Hype Boy", "NewJeans")], observed_at=day2)
    compute_velocity_signals(conn, "2026-08-13", "spotify_chart")
    compute_velocity_signals(conn, "2026-08-13", "apple_music")

    data = build_dashboard_data_v2(conn, "2026-08-13")
    entry = data["intelligence"]["cross_platform"][0]
    detail_by_source = {d["source_name"]: d for d in entry["source_details"]}
    assert detail_by_source["spotify_chart"]["rank"] == 3
    assert detail_by_source["spotify_chart"]["previous_rank"] == 9
    assert detail_by_source["apple_music"]["rank"] == 1
    assert detail_by_source["apple_music"]["previous_rank"] == 2
    assert detail_by_source["spotify_chart"]["region"] == "GLOBAL"
    assert detail_by_source["apple_music"]["region"] == "KR"


# ---- LLM-unavailable fallback: real ingested news must stay visible -------


def _insert_raw_candidate(conn, key, source_category, title, source_name="outlet",
                           collected_at="2026-08-13T01:00:00+00:00", event_key=None, published_at=None):
    """Inserts directly into raw_items/normalized_items only -- deliberately
    bypasses any llm_interpretations/reports/run_category_status row, to
    simulate 'real ingestion succeeded, no LLM selection exists yet'."""
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, title, published_at, collected_at)
           VALUES (?, ?, 'rss', ?, ?, ?, ?)""",
        (source_name, key, f"https://example.com/{key}", title, published_at, collected_at),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (raw_id, source_category, event_key or f"ev-{key}", title, collected_at),
    )
    conn.commit()


def test_no_llm_selection_falls_back_to_real_ingested_candidates(conn):
    """The core LLM-single-point-of-failure fix: zero llm_interpretations/
    reports/run_category_status rows exist (as if the LLM provider is
    down), but real news WAS ingested -- it must still appear, never a
    blank DEGRADED page."""
    _insert_raw_candidate(conn, "a1", "AI_NEWS", "실제로 수집된 AI 뉴스 헤드라인")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    section = data["news"]["AI"]
    assert section["state"] == "UNINTERPRETED"
    assert len(section["items"]) == 1
    assert section["items"][0]["title"] == "실제로 수집된 AI 뉴스 헤드라인"
    assert section["items"][0]["reason"] is None  # never a fabricated "why it matters"


def test_fallback_never_invoked_when_real_llm_selection_exists(conn):
    """Once a real LLM selection exists, the fallback must not silently
    replace or duplicate it."""
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "a1", "AI", "LLM이 실제로 선택한 헤드라인")
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "실제 선택 이유"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    section = data["news"]["AI"]
    assert section["state"] == "NORMAL"
    assert len(section["items"]) == 1
    assert section["items"][0]["reason"] == "실제 선택 이유"


def test_no_candidates_at_all_stays_honestly_degraded(conn):
    """When nothing was ingested either, the fallback must not invent
    anything -- the original honest DEGRADED empty state is preserved."""
    data = build_dashboard_data_v2(conn, "2026-08-13")
    section = data["news"]["AI"]
    assert section["state"] == "DEGRADED"
    assert section["items"] == []


def test_fallback_items_are_deterministically_ranked_by_source_count(conn):
    _insert_raw_candidate(conn, "a1", "AI_NEWS", "단독 보도", source_name="outlet-solo")
    _insert_raw_candidate(conn, "a2", "AI_NEWS", "여러 매체 보도", source_name="outlet-x", event_key="ev-shared")
    _insert_raw_candidate(conn, "a3", "AI_NEWS", "여러 매체 보도", source_name="outlet-y", event_key="ev-shared")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["AI"]["items"]
    assert items[0]["title"] == "여러 매체 보도"  # 2 sources > 1, ranked first
    assert items[0]["source_count"] == 2


# ---- NEWS QUALITY: real near-duplicate-event suppression (report.web_data_v2._cluster_suppression) --


def test_near_duplicate_event_from_different_outlets_is_suppressed_not_listed_twice(conn):
    """Real production example this fixes: the same OpenAI 'Ultrafast mode'
    announcement covered independently by openai_news_rss and
    techcrunch_ai_rss got two different event_keys and both showed up as
    separate top-level items. report.story_clustering's own high-precision
    near-duplicate-event detection must now suppress the non-representative
    member from the displayed list (not just record it as footnote
    evidence), and the surviving representative must carry a real
    related_article_count/related_source_count instead."""
    _insert_raw_candidate(
        conn, "a1", "AI_NEWS", "OpenAI launches Ultrafast mode for GPT-5.6 speed boost",
        source_name="openai_news_rss", published_at="2026-08-13T10:00:00+00:00",
    )
    _insert_raw_candidate(
        conn, "a2", "AI_NEWS", "OpenAI launches Ultrafast mode for GPT-5.6 performance boost",
        source_name="techcrunch_ai_rss", published_at="2026-08-13T19:22:00+00:00",
    )
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["AI"]["items"]
    assert len(items) == 1
    survivor = items[0]
    assert survivor["related_article_count"] == 2
    assert survivor["related_source_count"] == 2


def test_genuinely_different_articles_are_never_over_merged(conn):
    """Precision guard: two unrelated real headlines from different
    outlets on the same day must never be collapsed into one cluster just
    because they share a category and rough recency."""
    _insert_raw_candidate(
        conn, "b1", "AI_NEWS", "Writer introduces new AI model to contain token costs",
        source_name="techcrunch_ai_rss", published_at="2026-08-13T21:13:00+00:00",
    )
    _insert_raw_candidate(
        conn, "b2", "AI_NEWS", "Microsoft's Clippy-like Mico character is no longer the face of Copilot",
        source_name="the_verge_ai_rss", published_at="2026-08-13T21:42:00+00:00",
    )
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["AI"]["items"]
    assert len(items) == 2
    assert all(not item.get("related_article_count") for item in items)


# ---- V2: News Intelligence (WHAT HAPPENED / WHY IT MATTERS / WHAT TO WATCH) -


def _selected_ai_item(conn, report_date_kst="2026-08-13"):
    run_row_id = _insert_run(conn, run_date=report_date_kst)
    item_id = _insert_normalized_item(conn, "ni1", "AI", "AI Story Title", snippet="A distinct snippet.")
    _insert_reports_marker(conn, run_row_id, report_date=report_date_kst)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "why it matters (V1 selection reason)"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    return item_id


def _insert_news_intelligence_row(conn, report_date_kst, output_dict, run_id="ni-run-1"):
    ni_run_row_id = _insert_run(conn, run_id=run_id, run_date=report_date_kst)
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'NEWS_INTELLIGENCE_V2', 'test-model', 'v1', ?, 'MEDIUM', 'x')""",
        (ni_run_row_id, json.dumps(output_dict)),
    )
    conn.commit()


def test_item_carries_stable_id_matching_normalized_item_id(conn):
    item_id = _selected_ai_item(conn)
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["id"] == item_id


def test_news_intelligence_unavailable_when_no_row_news_still_shown(conn):
    """No News Intelligence run has happened yet (the real current state,
    no credential configured) -- the item's real title/reason must still
    render exactly as before; only the AI layer is unavailable."""
    _selected_ai_item(conn)
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["ai_intelligence_status"] == "UNAVAILABLE"
    assert item["what_happened"] is None
    assert item["why_it_matters"] is None
    assert item["what_to_watch"] is None
    assert item["title"] == "AI Story Title"
    assert item["reason"] == "why it matters (V1 selection reason)"


def test_news_intelligence_available_when_valid_row_exists(conn):
    item_id = _selected_ai_item(conn)
    _insert_news_intelligence_row(conn, "2026-08-13", {"items": [{
        "id": item_id,
        "what_happened": "A concrete factual statement about what occurred.",
        "why_it_matters": "A grounded implication drawn from the given evidence.",
        "what_to_watch": "Whether the next data point confirms this trend.",
    }]})
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["ai_intelligence_status"] == "AVAILABLE"
    assert item["what_happened"] == "A concrete factual statement about what occurred."
    assert item["why_it_matters"] == "A grounded implication drawn from the given evidence."
    assert item["what_to_watch"] == "Whether the next data point confirms this trend."
    assert item["title"] == "AI Story Title"  # real news item still shown unchanged


def test_news_intelligence_degrades_safely_on_invalid_row_news_still_shown(conn):
    """An invalid field (e.g. a verbatim title copy) must degrade that
    item to UNAVAILABLE, never crash generation, and never hide the real
    news item."""
    item_id = _selected_ai_item(conn)
    _insert_news_intelligence_row(conn, "2026-08-13", {"items": [{
        "id": item_id,
        "what_happened": "AI Story Title",  # verbatim copy of the title -- invalid
        "why_it_matters": "A grounded implication.",
        "what_to_watch": "A follow-up point.",
    }]})
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["ai_intelligence_status"] == "UNAVAILABLE"
    assert item["title"] == "AI Story Title"


def test_news_intelligence_never_attached_for_tiktok_spotify(conn):
    """TIKTOK/SPOTIFY news items are Music Industry's own evidence -- News
    Intelligence is scoped to AI/ECONOMY/SOCIETY only, so these categories
    must not even carry the additive fields."""
    data = build_dashboard_data_v2(conn, "2026-08-13")
    for category in ("TIKTOK", "SPOTIFY"):
        for item in data["news"][category]["items"]:
            assert "ai_intelligence_status" not in item


# ---- Phase 3C: translation scoped to AI/ECONOMY/SOCIETY only --------------


def test_translation_provider_never_constructed_for_tiktok_spotify(conn, monkeypatch):
    """Production-pilot policy: real translation is scoped to AI/ECONOMY/
    SOCIETY only. TIKTOK/SPOTIFY items must never even construct the real
    provider (proves zero network-attempt risk, not just "happens to
    return UNAVAILABLE") -- their items still carry the same additive
    original_title/ko_title/translation_status fields (untouched, via
    NullTranslationProvider), just always TRANSLATION_UNAVAILABLE."""
    import report.web_data_v2 as web_data_v2

    _insert_raw_candidate(conn, "ai1", "AI_NEWS", "A Real English AI Headline")
    _insert_raw_candidate(conn, "tk1", "TIKTOK_NEWS", "A Real English TikTok Headline")
    _insert_raw_candidate(conn, "sp1", "SPOTIFY_NEWS", "A Real English Spotify Headline")

    real_build = web_data_v2.build_translation_provider
    calls = []

    def _tracking_build():
        calls.append(True)
        return real_build()

    monkeypatch.setattr(web_data_v2, "build_translation_provider", _tracking_build)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "none")

    data = build_dashboard_data_v2(conn, "2026-08-13")

    # _news_section constructs a provider up front, then (pre-existing,
    # harmless -- no network call happens at construction time) falls
    # through to _raw_fallback_items, which constructs its own -- so each
    # of the 3 translation-eligible categories (AI/ECONOMY/SOCIETY, no real
    # LLM selection exists in this test) contributes 2 calls = 6 total.
    # The real thing under test: TIKTOK/SPOTIFY contribute ZERO of those 6
    # -- if either had, the total would exceed 6.
    assert len(calls) == 6
    ai_item = data["news"]["AI"]["items"][0]
    tk_item = data["news"]["TIKTOK"]["items"][0]
    sp_item = data["news"]["SPOTIFY"]["items"][0]
    assert ai_item["translation_status"] == "TRANSLATION_UNAVAILABLE"  # no provider configured either way
    assert tk_item["translation_status"] == "TRANSLATION_UNAVAILABLE"
    assert sp_item["translation_status"] == "TRANSLATION_UNAVAILABLE"
    assert tk_item["original_title"] == "A Real English TikTok Headline"  # real text still preserved

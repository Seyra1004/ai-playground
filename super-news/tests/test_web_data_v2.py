"""report.web_data_v2: TikTok honestly UNAVAILABLE, Spotify real chart
data, intelligence sections honest-empty absent real evidence."""

import json

import pytest

from db.database import connect, init_db
from report.web_data_v2 import (
    MUSIC_TREND_INTELLIGENCE_CATEGORY,
    PRODUCER_INTELLIGENCE_CATEGORY,
    _collect_music_signal_candidates,
    build_dashboard_data_v2,
    music_industry_priority_rank,
    rank_economy_society_items,
    rank_music_industry_items,
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
    assert data["spotify_chart"]["chart_date"] == "2026-08-13T00:00:00+00:00"


# ---- CHART PULSE REAL-DATE CONTRACT: chart_date is the real Spotify
# source observation date -- NEVER the same field/value as
# report_date_kst just because they're usually close in practice. ----


def test_spotify_chart_date_can_differ_from_report_date_on_collector_lag(conn):
    """A real-world case this pipeline must handle honestly: the chart
    source's most recent snapshot was observed a day before the SUPER
    NEWS report_date_kst (collector lag) -- _latest_snapshot_on_or_before
    still finds and uses that real earlier snapshot, and chart_date must
    reflect its REAL observed_at, not silently become report_date_kst."""
    _insert_spotify_observation(conn, 1, "2026-08-15T00:00:00+00:00", spotify_id="1")
    data = build_dashboard_data_v2(conn, "2026-08-16")
    assert data["report_date_kst"] == "2026-08-16"
    assert data["spotify_chart"]["chart_date"] == "2026-08-15T00:00:00+00:00"
    assert data["spotify_chart"]["chart_date"] != data["report_date_kst"]


def test_spotify_chart_date_is_none_when_chart_unavailable(conn):
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["spotify_chart"]["state"] == "UNAVAILABLE"
    assert data["spotify_chart"]["chart_date"] is None


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
                             collected_at="2026-08-13T01:00:00+00:00", event_key=None, published_at=None,
                             extra_json=None):
    conn.execute(
        """INSERT INTO raw_items
           (source_name, source_item_key, source_type, source_url, title, snippet, published_at, collected_at, extra_json)
           VALUES (?, ?, 'rss', ?, ?, ?, ?, ?, ?)""",
        (source_name, key, f"https://example.com/{key}", title, snippet, published_at, collected_at, extra_json),
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
    # A second, independent source corroborating the SAME event_key as
    # id1 -- clears the source-trust LEAD-eligibility gate's corroboration
    # path (report.web_data_v2._is_lead_eligible_by_trust) so this test's
    # own subject (tier follows LLM selection ORDER) isn't confounded by
    # the separate trust gate a single unmapped-tier test source would
    # otherwise fail.
    _insert_normalized_item(conn, "a1b", "AI", "First story", source_name="s1b", event_key="ev-a1")
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


# ---- MUSIC EDITORIAL IMAGERY: image_url is read back from the SAME
# raw_items.extra_json ingestion/adapters/rss.py already populates from
# real feed image metadata -- never a new column/duplicate semantics. ----


def test_item_image_url_reads_real_trustworthy_url_from_extra_json(conn):
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(
        conn, "a1", "AI", "Story with a real image",
        extra_json=json.dumps({"image_url": "https://cdn.example.com/real-article-image.jpg"}),
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": item_id, "reason": "r1"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["AI"]["items"][0]["image_url"] == "https://cdn.example.com/real-article-image.jpg"


def test_item_image_url_is_none_when_extra_json_has_no_image():
    from report.web_data_v2 import _extract_trustworthy_image_url
    assert _extract_trustworthy_image_url(None) is None
    assert _extract_trustworthy_image_url("") is None
    assert _extract_trustworthy_image_url(json.dumps({})) is None
    assert _extract_trustworthy_image_url(json.dumps({"image_url": None})) is None
    assert _extract_trustworthy_image_url("not valid json") is None


def test_item_image_url_rejects_non_http_values():
    from report.web_data_v2 import _extract_trustworthy_image_url
    assert _extract_trustworthy_image_url(json.dumps({"image_url": "javascript:alert(1)"})) is None
    assert _extract_trustworthy_image_url(json.dumps({"image_url": "/relative/path.jpg"})) is None
    assert _extract_trustworthy_image_url(json.dumps({"image_url": "data:image/png;base64,xxx"})) is None
    assert _extract_trustworthy_image_url(json.dumps({"image_url": 12345})) is None
    assert _extract_trustworthy_image_url(json.dumps({"image_url": "https://cdn.example.com/real.jpg"})) == \
        "https://cdn.example.com/real.jpg"


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
        "what_is_moving": "훅 중심 인트로가 확산되는 중", "why_it_matters": "여러 신호가 일치함",
        "what_to_watch": "다음 관찰 포인트", "what_could_i_make_now": "데모 훅 제작",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"]["state"] == "NORMAL"
    assert data["producer_intelligence"]["insights"][0]["what_is_moving"] == "훅 중심 인트로가 확산되는 중"


# ---- PRODUCER/A&R FINAL QUALITY PASS: reject an insight that merely
# restates its own cited evidence, no real synthesis added ----


def test_producer_intelligence_rejects_insight_that_merely_restates_its_own_evidence(conn):
    run_row_id = _insert_run(conn, run_id="pi-restate")
    output = {
        "insights": [{
            "what_is_moving": "Spotify signs new licensing agreement",  # verbatim = its own cited evidence summary
            "why_it_matters": "여러 신호가 일치함", "what_to_watch": "다음 관찰 포인트",
            "what_could_i_make_now": "데모 훅 제작", "evidence_refs": ["E1"], "confidence": "MEDIUM",
        }],
        "catalog": [{"ref": "E1", "type": "MUSIC_INDUSTRY_NEWS", "summary": "Spotify signs new licensing agreement"}],
    }
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"] == {"state": "UNAVAILABLE", "insights": []}


def test_producer_intelligence_resolves_evidence_refs_to_readable_summaries(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('pi-run3', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = {
        "insights": [{
            "what_is_moving": "훅 중심 인트로가 확산되는 중", "why_it_matters": "여러 신호가 일치함",
            "what_to_watch": "다음 관찰 포인트", "what_could_i_make_now": "데모 훅 제작",
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
    # event_key is None here: an EARLY_SIGNAL catalog entry is a real
    # chart fact, not a real article -- honestly no event_key to resolve.
    assert evidence == [{"ref": "E1", "summary": "[spotify_chart] Artist - Title (+8 rank)", "event_key": None}]


def test_producer_intelligence_evidence_falls_back_to_bare_ref_when_catalog_missing(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('pi-run4', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = {"insights": [{
        "what_is_moving": "가나", "why_it_matters": "다라", "what_to_watch": "마바", "what_could_i_make_now": "사아",
        "evidence_refs": ["E1"], "confidence": "LOW",
    }]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, PRODUCER_INTELLIGENCE_CATEGORY, json.dumps(stored, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["producer_intelligence"]["insights"][0]["evidence"]
    assert evidence == [{"ref": "E1", "summary": "E1", "event_key": None}]


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
        "observed": "기사에서 하우스 장르를 명시적으로 언급함", "interpretation": "실제 청취자 관심 반영",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["state"] == "NORMAL"
    assert data["music_trend_intelligence"]["genre_signals"][0]["observed"] == "기사에서 하우스 장르를 명시적으로 언급함"
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
        "observed": "기사에서 X가 해당 트랙을 프로듀싱했다고 명시함", "interpretation": "실제로 이름이 명시된 크레딧",
        "evidence_refs": ["E2"], "confidence": "HIGH",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
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
        "observed": "하우스 장르 확산", "interpretation": "청취자 반응 증가", "evidence_refs": ["E1"], "confidence": "MEDIUM",
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
    # event_key is None: this catalog entry predates MUSIC EVENT-LEVEL
    # IDENTITY propagation (no event_key key on it at all) -- a real
    # legacy row, handled safely, never a crash.
    assert evidence == [{"ref": "E1", "summary": "Real article title — real snippet", "event_key": None}]


def test_music_trend_intelligence_evidence_falls_back_to_bare_ref_when_catalog_missing(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('mt-run4', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = _music_trend_output(kpop_ar_notes=[{
        "observed": "가나", "interpretation": "다라", "evidence_refs": ["E1"], "confidence": "LOW",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(stored, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["music_trend_intelligence"]["kpop_ar_notes"][0]["evidence"]
    assert evidence == [{"ref": "E1", "summary": "E1", "event_key": None}]


# ---- TRUE MUSIC EVENT-LEVEL IDENTITY (SECOND CORRECTIVE PASS): a
# MUSIC_INDUSTRY_NEWS evidence catalog entry now carries the real
# event_key DIRECTLY, propagated from the originating real news item at
# catalog-build time -- title-text matching is no longer the primary
# identity bridge, only a last backward-compatible fallback (see report.
# web_render_v2._resolve_entry_event_key) ----


def test_music_industry_news_evidence_carries_real_event_key_even_when_summary_is_paraphrased(conn):
    """A. the persisted evidence catalog carries the real event_key
    DIRECTLY, so a genre_signal's own evidence resolves to the real
    event_key even when its real summary text is a COMPLETELY DIFFERENT
    (paraphrased) sentence than the source article's title -- title-text
    matching is no longer required for a normal, article-backed
    citation."""
    from report.music_trend_synthesis import build_evidence_catalog
    industry_news = [{"title": "Spotify signs new licensing agreement", "snippet": None, "event_key": "ev-real-1"}]
    catalog = build_evidence_catalog({"state": "UNAVAILABLE"}, {"state": "UNAVAILABLE"}, industry_news)
    assert catalog[0]["event_key"] == "ev-real-1"

    run_row_id = _insert_run(conn, run_id="mt-run-paraphrase")
    output = {
        "genre_signals": [{
            "observed": "하우스 장르 라이선스 구조가 창작자 수익 배분에 영향을 준다",
            "interpretation": "장기적으로 수익 구조 변화가 예상된다",
            "evidence_refs": ["E1"], "confidence": "MEDIUM",
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
        "catalog": catalog,
    }
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["music_trend_intelligence"]["genre_signals"][0]["evidence"]
    # note: "새 라이선스 구조가..." never appears anywhere in "Spotify signs
    # new licensing agreement" -- exact/prefix title matching would fail
    # here; direct event_key propagation still resolves it correctly.
    assert evidence[0]["event_key"] == "ev-real-1"


def test_producer_synthesis_evidence_also_carries_real_event_key_directly():
    """Same real propagation in report.producer_synthesis's own catalog
    builder -- both synthesis modules independently propagate the SAME
    real event_key from the SAME real industry_news item."""
    from report.producer_synthesis import build_evidence_catalog
    empty_intel = {"early_signal": {}, "catalog_revival": {}, "cross_platform": []}
    industry_news = [{"title": "Label announces catalog acquisition", "event_key": "ev-real-2"}]
    catalog = build_evidence_catalog(empty_intel, {"state": "UNAVAILABLE"}, industry_news)
    assert catalog[0]["event_key"] == "ev-real-2"


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


# ---- MUSIC EVENT EXPOSURE BUDGET: real event_key carried on every news
# item (both the LLM-selected and no-LLM fallback paths) -- the smallest
# safe field addition, reusing the SAME real event_key already computed
# at selection time, needed downstream to suppress an ordinary duplicate
# of the LEAD's own real event from Music Industry (see
# report.web_render_v2._merge_music_industry_items) ----


def test_news_section_item_carries_real_event_key_llm_selected_path(conn):
    run_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "a1", "SPOTIFY", "실제 헤드라인", event_key="ev-real-1")
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "SPOTIFY", "REPORT_GENERATED", 1, 1)
    for cat in ("AI", "ECONOMY", "SOCIETY", "TIKTOK"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "SPOTIFY": [{"id": item_id, "reason": "r"}], "AI": [], "ECONOMY": [], "SOCIETY": [], "TIKTOK": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["SPOTIFY"]["items"][0]["event_key"] == "ev-real-1"


def test_news_section_item_carries_real_event_key_no_llm_fallback_path(conn):
    _insert_raw_candidate(conn, "a1", "SPOTIFY_NEWS", "실제 폴백 헤드라인", source_name="outlet-solo", event_key="ev-real-2")
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["news"]["SPOTIFY"]["items"][0]["event_key"] == "ev-real-2"


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


def test_google_news_aggregator_title_suffix_replaces_source_name_with_real_publisher(conn):
    """EDITORIAL INTEGRITY FIX (source presentation): a real Google-News-
    style aggregator feed (source_name containing "google") must never
    display the aggregator's own ingestion identifier as the byline --
    the real underlying publisher, safely extracted from the RSS
    convention's own literal " - <Publisher>" title suffix, replaces it,
    and the suffix itself is stripped from the displayed title."""
    _insert_raw_candidate(
        conn, "g1", "TIKTOK_NEWS", "TikTok Music returns to the stage - NJ.com",
        source_name="tiktok_music_news_google",
    )
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["TIKTOK"]["items"][0]
    assert item["source_name"] == "NJ.com"
    assert item["title"] == "TikTok Music returns to the stage"


def test_direct_rss_feed_title_ending_in_dash_words_is_never_altered(conn):
    """The publisher-suffix extraction is scoped ONLY to a known real
    Google-News-style aggregator feed -- a direct RSS feed (e.g.
    billboard_rss) that happens to have a real headline ending in
    " - some words" must be completely unaffected."""
    _insert_raw_candidate(
        conn, "d1", "SPOTIFY_NEWS", "Spotify signs licensing deal - report",
        source_name="billboard_rss",
    )
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["SPOTIFY"]["items"][0]
    assert item["source_name"] == "billboard_rss"
    assert item["title"] == "Spotify signs licensing deal - report"


def test_near_duplicate_from_same_aggregator_feed_now_clusters_via_extracted_real_publisher(conn):
    """EDITORIAL INTEGRITY FIX (event cluster dedup), confirmed real
    defect: many real articles about the SAME underlying event arriving
    through one Google-News-style aggregator feed all previously shared
    the identical raw source_name ("tiktok_music_news_google"), which
    made report.story_clustering._sources_independent() reject every
    pairwise merge regardless of title similarity -- even near-identical
    headlines. Extracting the real per-article publisher from the title
    suffix BEFORE clustering restores real source-independence, so this
    near-duplicate pair (same real event, two different real real-world
    publishers) now correctly collapses to one displayed item."""
    _insert_raw_candidate(
        conn, "n1", "TIKTOK_NEWS", "TikTok Music returns to the stage after ban - NJ.com",
        source_name="tiktok_music_news_google", published_at="2026-08-13T10:00:00+00:00",
    )
    _insert_raw_candidate(
        conn, "n2", "TIKTOK_NEWS", "TikTok Music returns to the stage after ban - Yahoo",
        source_name="tiktok_music_news_google", published_at="2026-08-13T11:00:00+00:00",
    )
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["TIKTOK"]["items"]
    assert len(items) == 1
    assert items[0]["related_article_count"] == 2


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
        "what_happened": "실제로 있었던 일에 대한 구체적인 사실 진술이다.",
        "why_it_matters": "주어진 근거로부터 도출된 합리적인 함의다.",
        "what_to_watch": "다음 데이터가 이 흐름을 확인해줄지 지켜볼 필요가 있다.",
    }]})
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["ai_intelligence_status"] == "AVAILABLE"
    assert item["what_happened"] == "실제로 있었던 일에 대한 구체적인 사실 진술이다."
    assert item["why_it_matters"] == "주어진 근거로부터 도출된 합리적인 함의다."
    assert item["what_to_watch"] == "다음 데이터가 이 흐름을 확인해줄지 지켜볼 필요가 있다."
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


def test_translation_provider_constructed_for_every_news_category(conn, monkeypatch):
    """MAJOR IA REBUILD policy: Music Industry news (TIKTOK/SPOTIFY) must
    read as an edited Korean briefing, so real translation is now scoped to
    every news category, not just AI/ECONOMY/SOCIETY (see _TRANSLATION_
    ELIGIBLE_CATEGORIES's own docstring for why this is now deliberately a
    WIDER set than _NEWS_INTELLIGENCE_CATEGORIES). Every category's items
    still carry the same additive original_title/ko_title/translation_
    status fields, and real original text is always preserved regardless of
    translation outcome."""
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
    # of the 5 translation-eligible categories (all of NEWS_CATEGORIES, no
    # real LLM selection exists in this test) contributes 2 calls = 10
    # total, TIKTOK/SPOTIFY included now.
    assert len(calls) == 10
    ai_item = data["news"]["AI"]["items"][0]
    tk_item = data["news"]["TIKTOK"]["items"][0]
    sp_item = data["news"]["SPOTIFY"]["items"][0]
    assert ai_item["translation_status"] == "TRANSLATION_UNAVAILABLE"  # no provider configured either way
    assert tk_item["translation_status"] == "TRANSLATION_UNAVAILABLE"
    assert sp_item["translation_status"] == "TRANSLATION_UNAVAILABLE"
    assert tk_item["original_title"] == "A Real English TikTok Headline"  # real text still preserved


# ---- DUPLICATE GATE on the PRIMARY (LLM-selected) path ---------------------
# report.web_data_v2._suppress_duplicate_selections: the LLM must never be
# the only duplicate-defense layer -- report.story_clustering's real,
# high-precision near-duplicate detection is also applied here, not only to
# the no-LLM fallback path.


def test_llm_path_suppresses_a_real_near_duplicate_the_llm_itself_selected(conn):
    run_row_id = _insert_run(conn)
    id1 = _insert_normalized_item(
        conn, "d1", "AI_NEWS", "삼성전자 3분기 실적 발표", source_name="outlet-a",
        event_key="ev-d1", published_at="2026-08-13T01:00:00+00:00",
    )
    id2 = _insert_normalized_item(
        conn, "d2", "AI_NEWS", "삼성전자 3분기 실적 공식 발표", source_name="outlet-b",
        event_key="ev-d2", published_at="2026-08-13T02:00:00+00:00",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 2, 2)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "r1"}, {"id": id2, "reason": "r2"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["AI"]["items"]
    # Two independent outlets covering the identical real event (near-
    # identical headline, same day, source-independent) must read as ONE
    # top-level story, not two -- even though the LLM "selected" both.
    assert len(items) == 1
    assert items[0]["id"] == id1  # representative: higher source_count tie-break (both 1 here, event_key order)
    assert items[0]["related_article_count"] == 2
    assert items[0]["related_source_count"] == 2
    # The real coverage is still visible as cluster evidence, never lost.
    assert len(data["news"]["AI"]["clusters"]) == 1


def test_llm_path_never_merges_genuinely_different_developments(conn):
    run_row_id = _insert_run(conn)
    id1 = _insert_normalized_item(
        conn, "e1", "AI_NEWS", "삼성전자 3분기 실적 발표", source_name="outlet-a",
        event_key="ev-e1", published_at="2026-08-13T01:00:00+00:00",
    )
    id2 = _insert_normalized_item(
        conn, "e2", "AI_NEWS", "테슬라 신규 공장 착공식 개최", source_name="outlet-b",
        event_key="ev-e2", published_at="2026-08-13T02:00:00+00:00",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 2, 2)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "r1"}, {"id": id2, "reason": "r2"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    items = data["news"]["AI"]["items"]
    # Two completely unrelated real stories must both survive -- the
    # duplicate gate must never reduce legitimate information quantity.
    assert len(items) == 2
    assert {i["id"] for i in items} == {id1, id2}
    assert data["news"]["AI"]["clusters"] == []


# ---- SOURCE TRUST GATE: structural LEAD-eligibility floor -------------------


def test_low_trust_single_source_item_cannot_become_lead(conn):
    run_row_id = _insert_run(conn)
    # "unmapped-source" has no sources.yaml/music.registry entry -> falls
    # back to the neutral DEFAULT_QUALITY_SCORE (0.5), below the LEAD trust
    # floor (0.8) -- and it is the ONLY source (source_count=1), so neither
    # the high-trust-tier path nor the corroboration path is satisfied.
    id1 = _insert_normalized_item(
        conn, "f1", "AI", "출처 미상 단일 보도", source_name="unmapped-source",
        event_key="ev-f1",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "r1"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    # Still shown (never hidden) -- just never the top-billed LEAD slot on
    # trust alone.
    assert item["tier"] != "LEAD"
    assert item["id"] == id1


def test_corroborated_low_tier_source_can_still_become_lead(conn):
    """Weaker sources may surface as LEAD when genuinely corroborated by a
    second independent outlet -- the trust gate downgrades unsupported
    single-source claims, it does not reduce overall coverage."""
    run_row_id = _insert_run(conn)
    id1 = _insert_normalized_item(
        conn, "g1", "AI", "두 매체가 함께 보도한 소식", source_name="unmapped-source-a",
        event_key="ev-g1",
    )
    _insert_normalized_item(
        conn, "g1b", "AI", "두 매체가 함께 보도한 소식", source_name="unmapped-source-b",
        event_key="ev-g1",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "r1"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["tier"] == "LEAD"
    assert item["source_count"] == 2


# ---- PROFESSIONAL EDITORIAL QUALITY PASS: Music Industry USER-IMPACT ranking ----


def test_music_industry_priority_rank_rights_beats_celebrity_lifestyle():
    rights_item = {"title": "Publisher signs new copyright deal", "ko_title": "", "snippet": "", "ko_snippet": ""}
    lifestyle_item = {"title": "Singer spotted on red carpet", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(rights_item) < music_industry_priority_rank(lifestyle_item)


def test_music_industry_priority_rank_touring_beats_unranked_generic_story():
    touring_item = {"title": "Artist announces stadium tour dates", "ko_title": "", "snippet": "", "ko_snippet": ""}
    generic_item = {"title": "An unrelated story with no priority keyword", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(touring_item) < music_industry_priority_rank(generic_item)


def test_music_industry_priority_rank_downranks_gossip_below_unranked():
    gossip_item = {"title": "Star gossip and rumor roundup", "ko_title": "", "snippet": "", "ko_snippet": ""}
    generic_item = {"title": "An unrelated story with no priority keyword", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(gossip_item) > music_industry_priority_rank(generic_item)


# ---- MUSIC EDITORIAL RANKING UPGRADE: SUPER_NEWS_SPEC.md section 8's
# exact 1-8 priority order + the explicit legal/rights downrank exception ----


def test_music_industry_priority_rank_licensing_beats_routine_artist_event():
    """A. rights/publishing/licensing outranks a routine artist/event
    story."""
    licensing_item = {"title": "Label announces new licensing deal for its catalog", "ko_title": "", "snippet": "", "ko_snippet": ""}
    routine_item = {"title": "Artist to perform at local weekend festival", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(licensing_item) == 1
    assert music_industry_priority_rank(licensing_item) < music_industry_priority_rank(routine_item)


def test_music_industry_priority_rank_ai_music_beats_routine_concert_announcement():
    """B. AI-music/creator-workflow outranks a routine concert
    announcement."""
    ai_item = {"title": "New generative AI music creator tool launches for producers", "ko_title": "", "snippet": "", "ko_snippet": ""}
    concert_item = {"title": "Artist announces concert dates for fall tour", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(ai_item) == 3
    assert music_industry_priority_rank(ai_item) < music_industry_priority_rank(concert_item)


def test_music_industry_priority_rank_copyright_litigation_not_penalized_by_legal_language():
    """C. LEGAL/RIGHTS EXCEPTION: a real copyright/licensing lawsuit must
    not be swept into the generic legal-language downrank bucket just
    because it contains court/lawsuit language -- confirmed real defect,
    since 'lawsuit against' was ALSO a generic downrank keyword."""
    litigation_item = {
        "title": "Songwriters group files copyright infringement lawsuit against AI startup",
        "ko_title": "", "snippet": "", "ko_snippet": "",
    }
    assert music_industry_priority_rank(litigation_item) == 1


def test_music_industry_priority_rank_personal_lawsuit_without_rights_terms_still_downranked():
    """Regression guard: the legal/rights exception is scoped to REAL
    rights/copyright/publishing/royalty/licensing keyword matches only --
    an ordinary personal-life lawsuit with no rights-class term is still
    correctly downranked."""
    personal_item = {
        "title": "Singer's ex-manager files lawsuit against him over an unpaid personal loan",
        "ko_title": "", "snippet": "", "ko_snippet": "",
    }
    assert music_industry_priority_rank(personal_item) == 10


def test_music_industry_priority_rank_consumption_chart_beats_touring():
    """Priority 6 (consumption/chart/audience-behavior) outranks priority
    7 (touring/ticketing/live-business) per SUPER_NEWS_SPEC.md section 8
    -- confirmed real defect: these two classes were previously swapped."""
    chart_item = {"title": "Streaming consumption patterns shift as chart behavior changes", "ko_title": "", "snippet": "", "ko_snippet": ""}
    touring_item = {"title": "Artist announces stadium tour dates", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(chart_item) == 6
    assert music_industry_priority_rank(touring_item) == 7
    assert music_industry_priority_rank(chart_item) < music_industry_priority_rank(touring_item)


def test_music_industry_ranking_unaffected_by_corrective_event_exposure_budget_pass():
    """G. this corrective task touches ONLY MUSIC EVENT EXPOSURE BUDGET
    (report.web_render_v2) -- the previous task's editorial priority
    order + legal/rights exception (this module) are untouched and still
    correct: licensing/rights stays priority 1, and a real copyright
    lawsuit is still never incorrectly down-ranked."""
    licensing_item = {"title": "Label announces new licensing deal for its catalog", "ko_title": "", "snippet": "", "ko_snippet": ""}
    routine_item = {"title": "Artist to perform at local weekend festival", "ko_title": "", "snippet": "", "ko_snippet": ""}
    litigation_item = {
        "title": "Songwriters group files copyright infringement lawsuit against AI startup",
        "ko_title": "", "snippet": "", "ko_snippet": "",
    }
    assert music_industry_priority_rank(licensing_item) == 1
    assert music_industry_priority_rank(licensing_item) < music_industry_priority_rank(routine_item)
    assert music_industry_priority_rank(litigation_item) == 1


def test_rank_music_industry_items_sorts_by_real_priority_class():
    items = [
        {"title": "Star gossip and rumor roundup", "ko_title": "", "snippet": "", "ko_snippet": ""},
        {"title": "Publisher signs new copyright deal", "ko_title": "", "snippet": "", "ko_snippet": ""},
        {"title": "Artist announces stadium tour dates", "ko_title": "", "snippet": "", "ko_snippet": ""},
    ]
    ranked = rank_music_industry_items(items)
    assert ranked[0]["title"] == "Publisher signs new copyright deal"
    assert ranked[-1]["title"] == "Star gossip and rumor roundup"


# ---- PROFESSIONAL EDITORIAL QUALITY PASS: Genre/Production Radar semantic gate ----


def test_genre_radar_rejects_non_genre_signal_as_genre_trend(conn):
    """An artist-discovery program or AI-tool launch mentioning no real
    genre/subgenre term must never be surfaced as a genre trend -- the
    section must show fewer items rather than mislabel it."""
    run_row_id = _insert_run(conn)
    output = _music_trend_output(genre_signals=[{
        "observed": "레이블이 신인 발굴 프로그램을 시작했다", "interpretation": "새로운 아티스트 확보 전략으로 보인다",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["genre_signals"] == []


def test_production_radar_rejects_non_production_signal_as_production_note(conn):
    """A creator-tool/licensing story with no real sonic/production
    technique term belongs in Music Industry, never Production Radar."""
    run_row_id = _insert_run(conn)
    output = _music_trend_output(production_notes=[{
        "observed": "AI 음악 생성 도구가 새로 출시되었다", "interpretation": "크리에이터 도구 시장이 확대되는 중",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["production_notes"] == []


def test_production_radar_accepts_real_production_technique_signal(conn):
    run_row_id = _insert_run(conn)
    output = _music_trend_output(production_notes=[{
        "observed": "곡의 드럼 패턴과 베이스라인이 뚜렷하게 변화했다", "interpretation": "새로운 그루브 감각을 보여준다",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert len(data["music_trend_intelligence"]["production_notes"]) == 1


# ---- STRICT GENRE/PRODUCTION RADAR (FAST COMPLETION product pass):
# marketing/virality/product-announcement framing rejected even when a
# real genre/production keyword appears incidentally ----


def test_genre_radar_rejects_tiktok_viral_framing_even_with_genre_keyword_present(conn):
    """A real genre word appearing inside a pure TikTok-virality story is
    NOT a real stylistic-movement signal -- must be rejected, not shown
    as genre intelligence."""
    run_row_id = _insert_run(conn)
    output = _music_trend_output(genre_signals=[{
        "observed": "이 하우스 트랙이 틱톡에서 화제가 되고 있다", "interpretation": "바이럴 챌린지로 조회수를 기록했다",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["genre_signals"] == []


def test_production_radar_rejects_tool_launch_mentioning_tempo_as_ui_label(conn):
    """A production-sounding word (템포) inside an ordinary AI-tool
    feature-launch announcement is not real observed production
    evidence -- must be rejected."""
    run_row_id = _insert_run(conn)
    output = _music_trend_output(production_notes=[{
        "observed": "이 앱이 새로운 템포 조절 기능을 출시했다", "interpretation": "크리에이터 도구 시장이 확대되는 중",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["production_notes"] == []


def test_production_radar_rejects_tool_version_launch_even_with_real_production_vocabulary_in_interpretation(conn):
    """EDITORIAL INTEGRITY FIX, confirmed real defect: Suno Studio 2.0's
    MIDI-support launch is a creator-tool/product VERSION announcement,
    never an observed musical/sonic characteristic -- even though its own
    real `interpretation` text speculates about downstream workflow
    consequences using real production vocabulary (편곡/믹싱), that
    speculation about a tool is not itself an observed production trait
    of an actual song, and must not survive the reject gate just because
    the earlier tempo/템포 pattern didn't literally match "출시했다"."""
    run_row_id = _insert_run(conn)
    output = _music_trend_output(production_notes=[{
        "observed": "Suno가 MIDI 지원을 포함한 Studio 2.0을 출시했다고 보도되었다",
        "interpretation": "프로듀서의 편곡과 믹싱 워크플로우에 영향을 줄 수 있다",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["production_notes"] == []


def test_genre_radar_still_accepts_real_genre_signal_without_marketing_framing(conn):
    """Regression guard: the new reject gate must not falsely reject a
    genuine, non-promotional genre signal."""
    run_row_id = _insert_run(conn)
    output = _music_trend_output(genre_signals=[{
        "observed": "신곡이 아마피아노 리듬 구조를 채택했다", "interpretation": "장르 혼합 트렌드가 확산되는 중",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert len(data["music_trend_intelligence"]["genre_signals"]) == 1


# ---- PROFESSIONAL EDITORIAL QUALITY PASS: final editorial gate rejects internal-ID leaks ----


def test_music_trend_intelligence_rejects_item_leaking_internal_evidence_ref(conn):
    run_row_id = _insert_run(conn)
    output = _music_trend_output(genre_signals=[{
        "observed": "E11은 하우스 장르 확산과 관련된 근거다", "interpretation": "실제 청취자 관심 반영",
        "evidence_refs": ["E11"], "confidence": "MEDIUM",
    }])
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["music_trend_intelligence"]["genre_signals"] == []


# ---- PROFESSIONAL EDITORIAL QUALITY PASS: semantic/event-level dedup in the Hero/Music Today pool ----


def _candidate_base():
    return {
        "news": {"SPOTIFY": {"items": []}, "TIKTOK": {"items": []}},
        "spotify_chart": {"state": "UNAVAILABLE"},
        "intelligence": {"cross_platform": [], "catalog_revival": {}},
        "music_trend_intelligence": {
            "state": "UNAVAILABLE", "genre_signals": [], "production_notes": [],
            "producer_references": [], "kpop_ar_notes": [],
        },
        "producer_intelligence": {"state": "UNAVAILABLE", "insights": []},
    }


def test_collect_music_signal_candidates_skips_second_candidate_sharing_evidence_refs():
    """The SAME underlying event (same evidence ref) analyzed through both
    a Genre Radar and a Production Radar lens must only occupy ONE slot in
    the shared Hero/Music Today candidate pool -- not two."""
    data = _candidate_base()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "하우스 신곡 확산", "interpretation": "청취자 반응 확인",
            "confidence": "MEDIUM", "evidence": [{"ref": "E1", "summary": "same underlying article"}],
        }],
        "production_notes": [{
            "observed": "곡의 드럼 패턴 변화", "interpretation": "그루브 감각 변화 확인",
            "confidence": "MEDIUM", "evidence": [{"ref": "E1", "summary": "same underlying article"}],
        }],
        "producer_references": [], "kpop_ar_notes": [],
    }
    candidates = _collect_music_signal_candidates(data)
    types = [c["type"] for c in candidates]
    assert "GENRE_SIGNAL" in types
    assert "PRODUCTION_SIGNAL" not in types  # skipped: same evidence ref already consumed


def test_collect_music_signal_candidates_keeps_both_when_evidence_refs_distinct():
    data = _candidate_base()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "하우스 신곡 확산", "interpretation": "청취자 반응 확인",
            "confidence": "MEDIUM", "evidence": [{"ref": "E1", "summary": "article A"}],
        }],
        "production_notes": [{
            "observed": "곡의 드럼 패턴 변화", "interpretation": "그루브 감각 변화 확인",
            "confidence": "MEDIUM", "evidence": [{"ref": "E2", "summary": "article B"}],
        }],
        "producer_references": [], "kpop_ar_notes": [],
    }
    candidates = _collect_music_signal_candidates(data)
    types = [c["type"] for c in candidates]
    assert "GENRE_SIGNAL" in types
    assert "PRODUCTION_SIGNAL" in types


def test_industry_news_candidate_enriched_from_matching_producer_insight_when_no_llm_reason():
    """LEAD STORY INTELLIGENCE GAP fix (PREMIUM INTELLIGENCE UPGRADE
    PASS): a no-LLM-fallback INDUSTRY_NEWS item (real reason=None) is
    enriched with a matching real Producer Intelligence insight's own
    why_it_matters/what_could_i_make_now -- never a new LLM call, never
    invented text."""
    data = _candidate_base()
    data["news"]["SPOTIFY"] = {"items": [{
        "title": "Spotify announces new licensing deal", "reason": None, "source_url": "https://example.com/a",
        "source_count": 1, "event_key": "ev-1",
    }]}
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            "what_is_moving": "라이선스 변화", "why_it_matters": "실제 왜 중요한가",
            "what_to_watch": "실제 지켜볼 점", "what_could_i_make_now": "실제 시도해볼 것",
            "confidence": "MEDIUM",
            "evidence": [{"ref": "E1", "summary": "Spotify announces new licensing deal"}],
        }],
    }
    candidates = _collect_music_signal_candidates(data)
    industry_candidate = next(c for c in candidates if c["type"] == "INDUSTRY_NEWS")
    assert industry_candidate["why_it_matters"] == "실제 왜 중요한가"
    assert industry_candidate["producer_implication"] == "실제 시도해볼 것"


def test_industry_news_candidate_enrichment_respects_low_confidence_watch_only():
    data = _candidate_base()
    data["news"]["SPOTIFY"] = {"items": [{
        "title": "Spotify royalty change", "reason": None, "source_url": "https://example.com/a",
        "source_count": 1, "event_key": "ev-1",
    }]}
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            "what_is_moving": "로열티 변화", "why_it_matters": "실제 왜 중요한가",
            "what_to_watch": "실제 지켜볼 점", "what_could_i_make_now": "실제 시도해볼 것",
            "confidence": "LOW",
            "evidence": [{"ref": "E1", "summary": "Spotify royalty change"}],
        }],
    }
    candidates = _collect_music_signal_candidates(data)
    industry_candidate = next(c for c in candidates if c["type"] == "INDUSTRY_NEWS")
    assert industry_candidate["producer_implication"] == "실제 지켜볼 점"


def test_music_today_never_pads_with_already_shown_hero_candidate():
    """MUSIC TODAY (confirmed real defect from actual generated-report
    QA): when the only real candidate today was already shown as TODAY'S
    MUSIC INTELLIGENCE's own Lead, MUSIC TODAY must show fewer items --
    down to zero -- never fall back to re-displaying that same
    already-shown card just to fill the section (which read as literal
    duplicate content immediately below the hero on an actual real thin
    day)."""
    from report.web_data_v2 import _build_music_today, _build_today_music_intelligence
    data = _candidate_base()
    data["news"]["SPOTIFY"] = {"items": [{
        "title": "단독 실제 기사", "reason": "이유", "source_url": "https://example.com/a",
        "source_count": 1, "event_key": "ev-1",
    }]}
    today_music_intelligence, used_keys = _build_today_music_intelligence(data)
    music_today = _build_music_today(data, exclude_keys=used_keys)
    assert today_music_intelligence  # the hero used the only real candidate
    assert music_today == []  # never padded with the same already-shown candidate


# ---- PROFESSIONAL EDITORIAL QUALITY PASS: ECONOMY/SOCIETY minor-story downrank ----


def test_rank_economy_society_items_moves_recruitment_notice_after_real_stories():
    items = [
        {"title": "인턴 모집 공고", "ko_title": "", "snippet": "", "ko_snippet": ""},
        {"title": "실질 GDP 성장률 발표", "ko_title": "", "snippet": "", "ko_snippet": ""},
    ]
    ranked = rank_economy_society_items(items)
    assert ranked[0]["title"] == "실질 GDP 성장률 발표"
    assert ranked[-1]["title"] == "인턴 모집 공고"


def test_rank_economy_society_items_never_reorders_among_real_stories():
    items = [
        {"title": "물가 상승률 발표", "ko_title": "", "snippet": "", "ko_snippet": ""},
        {"title": "기준 금리 동결", "ko_title": "", "snippet": "", "ko_snippet": ""},
    ]
    ranked = rank_economy_society_items(items)
    assert [i["title"] for i in ranked] == [i["title"] for i in items]


# ---- PROFESSIONAL EDITORIAL QUALITY PASS: known truncated publisher-name fix ----


def test_news_section_fixes_known_truncated_publisher_suffix(conn):
    run_row_id = _insert_run(conn)
    id1 = _insert_normalized_item(
        conn, "tw1", "AI", "Some AI headline - Music Wee",
        snippet="A completely distinct real summary sentence - Music Wee",
    )
    _insert_reports_marker(conn, run_row_id)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    for cat in ("ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"):
        _insert_category_status(conn, run_row_id, cat, "NOT_READY", 0, 0)
    _insert_interpretation(conn, run_row_id, {
        "AI": [{"id": id1, "reason": "why this matters"}],
        "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
    })
    data = build_dashboard_data_v2(conn, "2026-08-13")
    item = data["news"]["AI"]["items"][0]
    assert item["title"].endswith("Music Week")
    assert item["snippet"].endswith("Music Week")


def test_fix_known_truncated_publisher_suffix_handles_no_dash_variant():
    from report.web_data_v2 import _fix_known_truncated_publisher_suffix
    assert _fix_known_truncated_publisher_suffix(
        "TikTok LIVE announces return of Music On Stage global music programme Music Wee"
    ) == "TikTok LIVE announces return of Music On Stage global music programme Music Week"


def test_music_industry_priority_rank_health_story_not_promoted_by_incidental_tour_mention():
    """Confirmed real defect: a celebrity personal-health story that
    happens to mention an unrelated tour date must not be promoted to a
    real touring-economics priority class by that incidental word."""
    health_story = {
        "title": "", "ko_title": "NBA YoungBoy, 현재 한국에서 살고 있으며, 심장 질환을 밝히고 투어가 한 번 더 남아있다고 말해",
        "snippet": "", "ko_snippet": "",
    }
    real_touring_story = {"title": "Artist announces stadium tour dates", "ko_title": "", "snippet": "", "ko_snippet": ""}
    assert music_industry_priority_rank(health_story) > music_industry_priority_rank(real_touring_story)
    assert music_industry_priority_rank(health_story) == 10


def test_rank_economy_society_items_downranks_open_recruitment_announcement():
    """Confirmed real defect: a real 공개채용 (open recruitment) announcement
    did not match the narrower 채용공고-only keyword set and stayed in the
    primary slots."""
    items = [
        {"title": "전력거래소, 하반기 공개채용 돌입··· 재생에너지·AI 전문인력 확보", "ko_title": "", "snippet": "", "ko_snippet": ""},
        {"title": "빌 게이츠, 차세대 원자로 협력 논의 위해 한국 방문", "ko_title": "", "snippet": "", "ko_snippet": ""},
    ]
    ranked = rank_economy_society_items(items)
    assert ranked[0]["title"].startswith("빌 게이츠")
    assert ranked[-1]["title"].startswith("전력거래소")


def test_fix_known_truncated_publisher_suffix_strips_trailing_source_artifact():
    from report.web_data_v2 import _fix_known_truncated_publisher_suffix
    assert _fix_known_truncated_publisher_suffix(
        "Ticketmaster said eligible events are surfaced automatically. Source"
    ) == "Ticketmaster said eligible events are surfaced automatically."


def test_collect_music_signal_candidates_skips_synthesis_card_matching_industry_news_title():
    """NEWSLETTER x MUSIC INTELLIGENCE PRODUCT UPGRADE (confirmed real
    defect): a real event exposed once as the raw INDUSTRY_NEWS pick
    must not ALSO surface via a Genre/Production/K-pop synthesis card
    citing the exact same real article (matched by real title text
    against the evidence catalog's own real summary) in the same
    Hero/Music Today pool."""
    data = _candidate_base()
    data["news"]["TIKTOK"] = {"items": [{
        "title": "TikTok's Music on Stage returns in 2026", "ko_title": "", "snippet": "", "ko_snippet": "",
        "reason": None, "source_url": "https://example.com/a", "source_count": 1,
    }]}
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "장르 관찰", "interpretation": "장르 해석", "confidence": "MEDIUM",
            "evidence": [{"ref": "E1", "summary": "TikTok's Music on Stage returns in 2026"}],
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    candidates = _collect_music_signal_candidates(data)
    types = [c["type"] for c in candidates]
    assert "INDUSTRY_NEWS" in types
    assert "GENRE_SIGNAL" not in types

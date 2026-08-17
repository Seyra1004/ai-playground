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
                  collected_at, entity_name=None, published_at=None, source_url=None):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url,
              title, collected_at, category, published_at)
           VALUES (?, ?, 'rss', ?, ?, ?, ?, ?)""",
        (source_name, source_item_key, source_url or ("https://x/" + source_item_key),
         title, collected_at, category, published_at),
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
    from report.candidate_selection import _source_categories

    with pytest.raises(ValueError, match="Unknown report category"):
        _source_categories("NOT_A_REAL_CATEGORY")


def test_unknown_report_category_passed_to_select_news_candidates_raises(conn):
    with pytest.raises(ValueError, match="Unknown report category"):
        select_news_candidates(conn, ["NOT_A_REAL_CATEGORY"], "2026-08-12")


# ---- SPOTIFY report category pools SPOTIFY_NEWS + MUSIC_INDUSTRY_NEWS ------


def test_spotify_category_pools_official_and_trade_press_sources(conn):
    _insert_item(conn, "spotify_newsroom_rss", "k1", "SPOTIFY_NEWS", "ev-1", "official", "2026-08-12T01:00:00+00:00")
    _insert_item(conn, "billboard_rss", "k2", "MUSIC_INDUSTRY_NEWS", "ev-2", "trade press", "2026-08-12T02:00:00+00:00")

    result = select_news_candidates(conn, ["SPOTIFY"], "2026-08-12")
    titles = {c["normalized_title"] for c in result["SPOTIFY"]}
    assert titles == {"official", "trade press"}


def test_tiktok_category_uses_only_tiktok_news(conn):
    _insert_item(conn, "tiktok_music_news_google", "k1", "TIKTOK_NEWS", "ev-1", "tiktok item", "2026-08-12T01:00:00+00:00")
    _insert_item(conn, "spotify_newsroom_rss", "k2", "SPOTIFY_NEWS", "ev-2", "spotify item", "2026-08-12T01:00:00+00:00")

    result = select_news_candidates(conn, ["TIKTOK"], "2026-08-12")
    titles = {c["normalized_title"] for c in result["TIKTOK"]}
    assert titles == {"tiktok item"}


# ---- Low-value boilerplate genre filter (SOURCE EXPANSION + CONTENT QUALITY --
# HARDENING phase, 2026-08-15): real production examples pulled from the
# top-20-by-score SOCIETY audit that day.


def test_horoscope_column_is_scored_down_not_removed(conn):
    _insert_item(conn, "newsis_society_rss", "k1", "SOCIETY_NEWS", "ev-1",
                 "[녹유 오늘의 운세] 05년생 믿자 하는 약속 낙서가 되어요", "2026-08-14T15:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-15")
    candidate = result["SOCIETY"][0]
    assert candidate["is_boilerplate_genre"] is True
    assert candidate["final_score"] < 0.2  # still present, never dropped from the list


def test_obituary_notice_is_scored_down(conn):
    _insert_item(conn, "yonhap_society_rss", "k1", "SOCIETY_NEWS", "ev-1",
                 "[부고] 오택림(전북특별자치도 도민안전실장)씨 부친상", "2026-08-14T15:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-15")
    assert result["SOCIETY"][0]["is_boilerplate_genre"] is True


def test_daily_weather_bulletin_is_scored_down(conn):
    _insert_item(conn, "newsis_society_rss", "k1", "SOCIETY_NEWS", "ev-1",
                 "광복절 낮 최고 33도…전국 곳곳 비·소나기[오늘날씨]", "2026-08-14T15:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-15")
    assert result["SOCIETY"][0]["is_boilerplate_genre"] is True


def test_bracketed_copyright_notice_is_scored_down(conn):
    _insert_item(conn, "newsis_society_rss", "k1", "SOCIETY_NEWS", "ev-1",
                 "[알림]뉴시스 콘텐츠 저작권 고지", "2026-08-14T15:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-15")
    assert result["SOCIETY"][0]["is_boilerplate_genre"] is True


def test_real_copyright_law_story_is_never_flagged():
    """A bare '저작권' keyword must never alone trigger the filter -- a
    real news story about copyright law/litigation legitimately uses that
    word and must not be scored down."""
    from report.candidate_selection import _is_boilerplate_genre
    assert _is_boilerplate_genre("국회, AI 학습데이터 저작권법 개정안 통과") is False


def test_personnel_appointment_notice_is_never_flagged(conn):
    """Regulatory/institutional personnel notices ('[인사] ...') must
    never be treated as boilerplate -- section 8's explicit requirement
    that filtering never hides genuinely important regulatory news."""
    _insert_item(conn, "yonhap_economy_rss", "k1", "ECONOMY_NEWS", "ev-1",
                 "[인사] 공정거래위원회", "2026-08-14T15:00:00+00:00")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-15")
    assert result["ECONOMY"][0]["is_boilerplate_genre"] is False


def test_boilerplate_genre_never_outranks_equally_fresh_real_news(conn):
    _insert_item(conn, "newsis_society_rss", "k1", "SOCIETY_NEWS", "ev-1",
                 "[부고] 누군가씨 부친상", "2026-08-14T23:00:00+00:00")
    _insert_item(conn, "yonhap_society_rss", "k2", "SOCIETY_NEWS", "ev-2",
                 "실제 사회 뉴스 헤드라인입니다", "2026-08-14T23:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-15")
    titles = [c["normalized_title"] for c in result["SOCIETY"]]
    assert titles[0] == "실제 사회 뉴스 헤드라인입니다"


# ---- DAILY STALENESS POLICY (AI/ECONOMY/SOCIETY only, content-quality
# hardening pass, 2026-08-17) -----------------------------------------


def test_daily_category_excludes_article_older_than_72h(conn):
    """Confirmed real defect: a 3-day-old story (MIT Technology Review's
    real published_at, late-collected) sat in the AI candidate pool
    alongside same-day coverage. 72h+ must be excluded unconditionally."""
    _insert_item(conn, "mit_technology_review_rss", "k1", "AI_NEWS", "ev-stale",
                 "72시간 넘은 기사", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-13T00:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    assert result["AI"] == []


def test_daily_category_excludes_48_to_72h_article_without_important_exception(conn):
    _insert_item(conn, "techcrunch_ai_rss", "k1", "AI_NEWS", "ev-mid",
                 "50시간 지난 단일 출처 기사", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-14T06:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    assert result["AI"] == []


def test_daily_category_keeps_48_to_72h_article_with_important_exception(conn):
    """A real, corroborated (3+ sources) 50h-old follow-up/analysis story
    survives the default 48h cutoff -- the explicit 'important follow-up'
    exception, gated on an already-real signal, never a name hardcode."""
    for i in range(3):
        _insert_item(conn, f"source_{i}", f"k{i}", "AI_NEWS", "ev-important",
                     "여러 매체가 다룬 중요한 후속 분석", "2026-08-16T08:00:00+00:00",
                     published_at="2026-08-14T06:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    assert len(result["AI"]) == 1
    assert result["AI"][0]["source_count"] == 3


def test_daily_category_keeps_article_within_48h(conn):
    _insert_item(conn, "techcrunch_ai_rss", "k1", "AI_NEWS", "ev-fresh",
                 "24시간 이내 기사", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    assert len(result["AI"]) == 1


def test_daily_category_unknown_published_at_never_penalized(conn):
    """Missing published_at is not evidence of staleness -- the new 48h/
    72h gate only ever applies to a KNOWN age, matching the existing
    freshness_bucket's own unknown-age handling."""
    _insert_item(conn, "techcrunch_ai_rss", "k1", "AI_NEWS", "ev-unknown",
                 "발행 시각 미상 기사", "2026-08-16T08:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    assert len(result["AI"]) == 1


def test_music_categories_keep_existing_lenient_policy_untouched(conn):
    """SPOTIFY/TIKTOK (MUSIC) explicitly keep the existing lenient 30-day
    policy -- the new 48h/72h DAILY gate must never apply to them."""
    _insert_item(conn, "billboard_rss", "k1", "MUSIC_INDUSTRY_NEWS", "ev-old",
                 "5일 지난 음악 산업 뉴스", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-11T00:00:00+00:00")
    result = select_news_candidates(conn, ["SPOTIFY"], "2026-08-16")
    assert len(result["SPOTIFY"]) == 1


# ---- published_at RESOLUTION CHAIN / DATE_CONFLICT --------------------


def test_url_date_slug_used_as_fallback_when_published_at_missing(conn):
    _insert_item(conn, "newsis_economy_rss", "k1", "ECONOMY_NEWS", "ev-1",
                 "URL 날짜만 있는 기사", "2026-08-16T08:00:00+00:00",
                 source_url="https://www.newsis.com/view/NISX20260816_0001234567")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-16")
    assert result["ECONOMY"][0]["published_at"] is not None
    assert result["ECONOMY"][0]["date_conflict"] is False


def test_date_conflict_flagged_when_published_at_and_url_slug_disagree(conn):
    """published_at is still what's displayed (never silently overridden
    by the URL guess), but date_conflict=True and the item is treated
    with the OLDER (more conservative) age for staleness purposes."""
    _insert_item(conn, "newsis_economy_rss", "k1", "ECONOMY_NEWS", "ev-1",
                 "충돌하는 날짜 기사", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T05:00:00+00:00",
                 source_url="https://www.newsis.com/view/NISX20260815_0001234567")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-16")
    candidate = result["ECONOMY"][0]
    assert candidate["published_at"] == "2026-08-16T05:00:00+00:00"  # real value, never overridden
    assert candidate["date_conflict"] is True


def test_no_date_conflict_when_published_at_and_url_slug_agree(conn):
    _insert_item(conn, "newsis_economy_rss", "k1", "ECONOMY_NEWS", "ev-1",
                 "일치하는 날짜 기사", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T05:00:00+00:00",
                 source_url="https://www.newsis.com/view/NISX20260816_0001234567")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-16")
    assert result["ECONOMY"][0]["date_conflict"] is False


def test_date_conflict_causes_stale_article_to_be_excluded(conn):
    """A published_at that LOOKS fresh (<=72h) but disagrees by more than
    a day with the article's own URL date slug is treated conservatively
    -- the older, real-staleness age wins for the DAILY 72h hard-exclude
    gate, even though the raw published_at alone would have passed."""
    _insert_item(conn, "newsis_economy_rss", "k1", "ECONOMY_NEWS", "ev-1",
                 "발행일 위장 의심 기사", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T05:00:00+00:00",
                 source_url="https://www.newsis.com/view/NISX20260810_0001234567")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-16")
    assert result["ECONOMY"] == []


# ---- LEAD RANKING IMPROVEMENT (AI/ECONOMY/SOCIETY only) ----------------


def test_industry_significant_story_gets_score_bonus(conn):
    _insert_item(conn, "techcrunch_ai_rss", "k1", "AI_NEWS", "ev-ma",
                 "SpaceX가 Cursor 인수를 공식 마감", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    assert result["AI"][0]["is_industry_significant"] is True


def test_uncorroborated_personal_harm_allegation_is_downweighted(conn):
    _insert_item(conn, "techcrunch_ai_rss", "k1", "AI_NEWS", "ev-harm",
                 "여성이 의붓아버지가 Grok을 이용해 어린 시절 사진을 노골적인 이미지로 변환했다고 주장",
                 "2026-08-16T08:00:00+00:00", published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    candidate = result["AI"][0]
    assert candidate["is_personal_harm_uncorroborated"] is True


def test_industry_significant_story_outranks_uncorroborated_personal_harm_story(conn):
    """The confirmed real defect this pass fixes: an uncorroborated
    single-source personal-harm allegation must not outrank a real,
    equally-fresh industry M&A story for the LEAD slot."""
    _insert_item(conn, "techcrunch_ai_rss", "k1", "AI_NEWS", "ev-harm",
                 "여성이 의붓아버지가 Grok을 이용해 어린 시절 사진을 노골적인 이미지로 변환했다고 주장",
                 "2026-08-16T08:00:00+00:00", published_at="2026-08-16T00:00:00+00:00")
    _insert_item(conn, "techcrunch_ai_rss", "k2", "AI_NEWS", "ev-ma",
                 "SpaceX가 Cursor 인수를 공식 마감", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-16")
    titles = [c["normalized_title"] for c in result["AI"]]
    assert titles[0] == "SpaceX가 Cursor 인수를 공식 마감"


def test_corroborated_personal_harm_story_is_never_downweighted(conn):
    """A real, well-corroborated (3+ independent sources) personal-harm
    story is still real important news and is never penalized -- only
    the specific SINGLE-uncorroborated-source case is downweighted."""
    for i in range(3):
        _insert_item(conn, f"source_{i}", f"k{i}", "SOCIETY_NEWS", "ev-harm",
                     "아동학대 의혹, 경찰 수사 착수", "2026-08-16T08:00:00+00:00",
                     published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["SOCIETY"], "2026-08-16")
    assert result["SOCIETY"][0]["is_personal_harm_uncorroborated"] is False


def test_generic_claimed_verb_alone_never_triggers_personal_harm_downweight(conn):
    """A bare '주장했다' (claimed) reporting verb is far too common across
    ordinary legitimate hard news to use as a signal on its own -- must
    never match without a real crime/abuse-victim-specific term."""
    _insert_item(conn, "yonhap_economy_rss", "k1", "ECONOMY_NEWS", "ev-1",
                 "정부는 이번 조치가 물가 안정에 기여할 것이라고 주장했다",
                 "2026-08-16T08:00:00+00:00", published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["ECONOMY"], "2026-08-16")
    assert result["ECONOMY"][0]["is_personal_harm_uncorroborated"] is False


def test_industry_significance_and_personal_harm_signals_never_apply_to_music(conn):
    """MUSIC (SPOTIFY/TIKTOK) never gets the new AI/ECONOMY/SOCIETY-only
    lead-ranking signals -- this pass found no MUSIC lead-selection defect
    to justify touching its ranking."""
    _insert_item(conn, "billboard_rss", "k1", "MUSIC_INDUSTRY_NEWS", "ev-ma",
                 "Universal Music이 신생 레이블을 인수", "2026-08-16T08:00:00+00:00",
                 published_at="2026-08-16T00:00:00+00:00")
    result = select_news_candidates(conn, ["SPOTIFY"], "2026-08-16")
    assert result["SPOTIFY"][0]["is_industry_significant"] is False

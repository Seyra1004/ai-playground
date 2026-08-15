"""report.web_render_v2: presentation-only editorial Intelligence Dashboard
rendering from the exact structured shape report.web_data_v2.
build_dashboard_data_v2() produces. No DB access, no LLM call -- pure
function of its input dict.

Covers the V2.1 fact-ownership/specificity rules: TOP10 owns current
state only, Daily Music Trend owns the full previous->current movement
breakdown, Viral Hot/New require real qualification (not "any positive
delta"/"any new entry"), previous_rank is never fabricated for a NEW
entry, region/source identity is shown verbatim from persisted data, and
Producer Intelligence evidence never leaks a bare, unexplained ref code."""

from report.web_render_v2 import MIN_RANK_DELTA, VIRAL_NEW_NOTABLE_RANK, render_dashboard_html_v2


def _news(state="DEGRADED", items=None):
    return {"state": state, "items": items if items is not None else []}


def _news_item(title, tier="BRIEF", reason=None, snippet=None, source_url=None, source_count=1,
                source_name=None, published_at=None, related_article_count=None, related_source_count=None):
    return {"title": title, "reason": reason, "snippet": snippet, "source_url": source_url,
            "source_count": source_count, "tier": tier, "source_name": source_name, "published_at": published_at,
            "related_article_count": related_article_count, "related_source_count": related_source_count}


def _empty_dashboard():
    return {
        "report_date_kst": "2026-08-13",
        "news": {
            "AI": _news(), "ECONOMY": _news(), "SOCIETY": _news(),
            "TIKTOK": _news(), "SPOTIFY": _news(),
        },
        "tiktok_chart": {"state": "UNAVAILABLE", "top10": [], "new_entries": [], "trend": None},
        "spotify_chart": {"state": "UNAVAILABLE", "top10": [], "new_entries": [], "trend": None},
        "intelligence": {
            "early_signal": {"apple_music": [], "spotify_chart": []},
            "catalog_revival": {"apple_music": [], "spotify_chart": []},
            "cross_platform": [],
            "outlook": {
                "apple_music": {"status": "INSUFFICIENT_HISTORY", "days_of_history": 1,
                                 "min_required_days": 90, "progress_ratio": 1 / 90},
                "spotify_chart": {"status": "INSUFFICIENT_HISTORY", "days_of_history": 1,
                                   "min_required_days": 90, "progress_ratio": 1 / 90},
            },
        },
        "producer_intelligence": {"state": "UNAVAILABLE", "insights": []},
        "music_trend_intelligence": {
            "state": "UNAVAILABLE", "genre_signals": [], "production_notes": [],
            "producer_references": [], "kpop_ar_notes": [],
        },
    }


def _spotify_entry(rank, artist="Artist", title="Title", is_new=False, rank_delta=0,
                    peak_rank=None, days_on_chart=1, region="GLOBAL", observed_at="2026-08-13T00:00:00+00:00",
                    status=None):
    """status mirrors what report/web_data_v2._enrich_chart_entry's V2
    normalization would produce for these same is_new/rank_delta inputs on
    a genuine (non-first-observation) day -- these render-layer fixtures
    predate the FIRST_OBSERVED/status contract and don't need to exercise
    it themselves (see test_web_data_v2.py / test_credential_independent_
    architecture.py for FIRST_OBSERVED-specific coverage); `status` is
    still an explicit override param for a test that wants one directly."""
    previous_rank = None if is_new else rank + rank_delta
    if status is None:
        if is_new:
            status = "NEW"
        elif rank_delta > 0:
            status = "UP"
        elif rank_delta < 0:
            status = "DOWN"
        else:
            status = "FLAT"
    return {
        "music_entity_id": rank, "rank": rank, "canonical_artist": artist,
        "canonical_title": title, "is_new": is_new, "rank_delta": rank_delta,
        "previous_rank": previous_rank, "status": status,
        "peak_rank": peak_rank if peak_rank is not None else rank, "days_on_chart": days_on_chart,
        "region": region, "observed_at": observed_at,
    }


def _trend(top10):
    new_count = sum(1 for e in top10 if e["is_new"])
    up_count = sum(1 for e in top10 if not e["is_new"] and e["rank_delta"] > 0)
    down_count = sum(1 for e in top10 if not e["is_new"] and e["rank_delta"] < 0)
    total = new_count + up_count + down_count
    volatility = "HIGH" if total >= 6 else "MEDIUM" if total >= 3 else "LOW"
    return {"new_count": new_count, "up_count": up_count, "down_count": down_count, "volatility": volatility}


def _section(html_out, section_id):
    start = html_out.index(f'id="{section_id}"')
    end = html_out.index("</section>", start)
    return html_out[start:end]


# ---- TikTok: never fabricated -----------------------------------------------


def test_tiktok_always_unavailable_renders_honest_message_never_fabricated():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-TIKTOK")
    assert "TikTok 차트 데이터 소스가 아직 연동되지 않았습니다" in section
    assert "Apple Music" not in section


# ---- TOP10: current state ONLY, no movement breakdown (fact ownership) -----


def test_top10_shows_current_rank_artist_title():
    data = _empty_dashboard()
    entries = [_spotify_entry(1, artist="Artist", title="Title", is_new=True),
               _spotify_entry(2, artist="B", title="Song2", rank_delta=3)]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [entries[0]], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "Artist - Title" in section
    assert "B - Song2" in section
    assert "신규 진입 1" in section


def test_top10_never_shows_previous_rank_or_days_on_chart():
    """TOP10 owns 'who is #N right now' -- previous-rank movement detail
    and chart-history context belong to Daily Music Trend / Viral Hot."""
    data = _empty_dashboard()
    entries = [_spotify_entry(2, rank_delta=8, peak_rank=1, days_on_chart=5)]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    top10_section_start = html_out.index('id="section-SPOTIFY"')
    top10_section = html_out[top10_section_start:html_out.index('id="section-VIRAL"')]
    assert "일째 차트인" not in top10_section
    assert "전일" not in top10_section


def test_spotify_unavailable_state_never_shows_top10():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-SPOTIFY")
    assert "Spotify 차트 데이터가 아직 수집되지 않았습니다" in section


# ---- Daily Music Trend: owns the full movement breakdown -------------------


def test_daily_trend_lists_every_riser_new_entry_faller_with_previous_and_current():
    data = _empty_dashboard()
    riser = _spotify_entry(4, artist="Riser", title="Up", rank_delta=5)  # 9 -> 4
    faller = _spotify_entry(6, artist="Faller", title="Down", rank_delta=-4)  # 2 -> 6
    new = _spotify_entry(3, artist="Debut", title="New", is_new=True)
    entries = [riser, faller, new]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [new], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    assert "Riser - Up" in section
    assert "#9" in section and "#4" in section  # previous -> current for the riser
    assert "Faller - Down" in section
    assert "#2" in section and "#6" in section  # previous -> current for the faller
    assert "Debut - New" in section
    assert "NEW → 현재" in section
    assert "▲ RISERS" in section
    assert "▼ FALLERS" in section


def test_daily_trend_aggregate_counts_come_after_concrete_tracks():
    data = _empty_dashboard()
    riser = _spotify_entry(4, artist="Riser", title="Up", rank_delta=5)
    entries = [riser]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    track_pos = section.index("Riser - Up")
    aggregate_pos = section.index("오늘 TOP10 변화")
    assert track_pos < aggregate_pos


def test_daily_trend_aggregate_counts_equal_underlying_rendered_tracks():
    data = _empty_dashboard()
    entries = [
        _spotify_entry(4, artist="R1", title="Up1", rank_delta=5),
        _spotify_entry(5, artist="R2", title="Up2", rank_delta=1),
        _spotify_entry(6, artist="F1", title="Down1", rank_delta=-2),
        _spotify_entry(3, artist="N1", title="New1", is_new=True),
    ]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [entries[3]], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    assert "신규 1" in section
    assert "상승 2" in section
    assert "하락 1" in section
    # every rendered track individually present
    for name in ("R1 - Up1", "R2 - Up2", "F1 - Down1", "N1 - New1"):
        assert name in section


def test_daily_trend_never_fabricates_previous_rank_for_new_entry():
    data = _empty_dashboard()
    new = _spotify_entry(3, artist="Debut", title="New", is_new=True)
    data["spotify_chart"] = {"state": "NORMAL", "top10": [new], "new_entries": [new], "trend": _trend([new])}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    new_block_start = section.index("NEW")
    new_block = section[max(0, new_block_start - 200):new_block_start + 100]
    assert "전일" not in new_block


# ---- Viral Hot: requires the SAME real threshold as Early Signal -----------


def test_viral_hot_qualification_reuses_early_signal_threshold():
    data = _empty_dashboard()
    qualifies = _spotify_entry(2, artist="Riser", title="Big Jump", rank_delta=MIN_RANK_DELTA,
                                peak_rank=1, days_on_chart=3)
    too_small = _spotify_entry(5, artist="Small", title="Tiny Move", rank_delta=MIN_RANK_DELTA - 1)
    entries = [qualifies, too_small]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    viral_hot_block = section[section.index("Viral Hot"):section.index("Viral · New")]
    assert "Riser - Big Jump" in viral_hot_block
    assert "Small - Tiny Move" not in viral_hot_block


def test_viral_hot_shows_peak_rank_and_days_on_chart_context():
    data = _empty_dashboard()
    entry = _spotify_entry(2, artist="Riser", title="Big Jump", rank_delta=MIN_RANK_DELTA + 1,
                            peak_rank=1, days_on_chart=5)
    data["spotify_chart"] = {"state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry])}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    assert "최고 1위" in section
    assert "5일째 차트인" in section


def test_viral_hot_empty_when_nothing_meets_threshold():
    data = _empty_dashboard()
    entry = _spotify_entry(5, rank_delta=1)
    data["spotify_chart"] = {"state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry])}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    assert f"+{MIN_RANK_DELTA}" in section
    assert "급상승 곡이 없습니다" in section


# ---- Viral New: requires a genuinely distinct fact (notable entry rank) ----


def test_viral_new_qualifies_only_top_notable_rank():
    data = _empty_dashboard()
    notable = _spotify_entry(VIRAL_NEW_NOTABLE_RANK, artist="Debutant", title="First Song", is_new=True)
    ordinary = _spotify_entry(VIRAL_NEW_NOTABLE_RANK + 1, artist="Ordinary", title="Meh Debut", is_new=True)
    entries = [notable, ordinary]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": entries, "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    viral_new_block = section[section.index("Viral · New"):section.index("Daily Music Trend")]
    assert "Debutant - First Song" in viral_new_block
    assert "Ordinary - Meh Debut" not in viral_new_block


def test_viral_new_empty_when_no_notable_debut():
    data = _empty_dashboard()
    ordinary = _spotify_entry(VIRAL_NEW_NOTABLE_RANK + 2, artist="Ordinary", title="Meh Debut", is_new=True)
    data["spotify_chart"] = {"state": "NORMAL", "top10": [ordinary], "new_entries": [ordinary], "trend": _trend([ordinary])}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-VIRAL")
    viral_new_block = section[section.index("Viral · New"):section.index("Daily Music Trend")]
    assert "이례적으로 높은 순위로 데뷔한 곡이 없습니다" in viral_new_block
    assert "Ordinary - Meh Debut" not in viral_new_block


# ---- Music Industry: TikTok + Spotify news items, tiered, with byline -----


def test_industry_section_shows_tiktok_and_spotify_news_items():
    data = _empty_dashboard()
    data["news"]["TIKTOK"] = _news("NORMAL", [_news_item("TikTok industry news", tier="LEAD")])
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("Spotify industry news", tier="LEAD")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert "TikTok industry news" in section
    assert "Spotify industry news" in section


def test_lead_item_shows_source_outlet_and_published_date():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("Big AI headline", tier="LEAD", source_name="TechOutlet",
                   published_at="2026-08-13T01:00:00+00:00")
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "TechOutlet" in section
    assert "2026.08.13" in section


def test_lead_item_omits_byline_when_source_and_date_unavailable():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Headline only", tier="LEAD")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="item-byline"' not in section


# ---- News item tiering: LEAD / STANDARD / BRIEF render differently --------


def test_lead_item_shows_headline_snippet_and_why_it_matters():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("Big AI headline", tier="LEAD", reason="This matters a lot",
                   snippet="Extra context from the original article", source_url="https://x/a")
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="item-lead"' in section
    assert "Big AI headline" in section
    assert "Extra context from the original article" in section
    assert "왜 중요한가" in section
    assert "This matters a lot" in section


def test_lead_item_renders_full_intelligence_when_available():
    data = _empty_dashboard()
    item = _news_item("Big AI headline", tier="LEAD", reason="fallback reason (should not show)")
    item.update({
        "ai_intelligence_status": "AVAILABLE",
        "what_happened": "A concrete factual statement about what occurred.",
        "why_it_matters": "A grounded implication drawn from the evidence.",
        "what_to_watch": "Whether the next data point confirms this trend.",
    })
    data["news"]["AI"] = _news("NORMAL", [item])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "무슨 일이 있었나" in section
    assert "A concrete factual statement about what occurred." in section
    assert "왜 중요한가" in section
    assert "A grounded implication drawn from the evidence." in section
    assert "앞으로 지켜볼 점" in section
    assert "Whether the next data point confirms this trend." in section
    assert "fallback reason (should not show)" not in section


def test_lead_item_falls_back_to_reason_when_intelligence_unavailable():
    """The current real state (no News Intelligence run has happened yet)
    -- must render exactly as before this pass."""
    data = _empty_dashboard()
    item = _news_item("Big AI headline", tier="LEAD", reason="This matters a lot")
    data["news"]["AI"] = _news("NORMAL", [item])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "왜 중요한가" in section
    assert "This matters a lot" in section
    assert "무슨 일이 있었나" not in section
    assert "앞으로 지켜볼 점" not in section


def test_standard_item_has_no_why_it_matters_label():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Mid headline", tier="STANDARD", reason="Some reason")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="item-standard"' in section
    assert "왜 중요한가" not in section


# ---- Phase 3C: Korean-first display, real translation -> real UI --------


def test_lead_item_shows_korean_title_and_snippet_when_translated():
    data = _empty_dashboard()
    item = _news_item("Original English Headline", tier="LEAD", snippet="Original English snippet.")
    item.update({
        "translation_status": "TRANSLATED", "ko_title": "번역된 한국어 헤드라인",
        "snippet_translation_status": "TRANSLATED", "ko_snippet": "번역된 한국어 스니펫.",
    })
    data["news"]["AI"] = _news("NORMAL", [item])
    section = _section(render_dashboard_html_v2(data), "section-AI")
    assert "번역된 한국어 헤드라인" in section
    assert "번역된 한국어 스니펫." in section
    assert "Original English Headline" not in section
    assert "Original English snippet." not in section


def test_standard_item_shows_korean_title_when_not_required():
    """NOT_REQUIRED (already-sufficiently-Korean source) must also display
    as Korean -- it's real text, not a fabricated translation, same as a
    real TRANSLATED outcome."""
    data = _empty_dashboard()
    item = _news_item("이미 한국어인 원문 헤드라인", tier="STANDARD")
    item.update({"translation_status": "NOT_REQUIRED", "ko_title": "이미 한국어인 원문 헤드라인"})
    data["news"]["AI"] = _news("NORMAL", [item])
    section = _section(render_dashboard_html_v2(data), "section-AI")
    assert "이미 한국어인 원문 헤드라인" in section


def test_lead_item_falls_back_to_original_when_translation_unavailable():
    """A real UNAVAILABLE/FAILED translation outcome must never hide or
    blank the real news -- the original text keeps rendering exactly as
    before this pass."""
    data = _empty_dashboard()
    item = _news_item("Original English Headline", tier="LEAD", snippet="Original English snippet.")
    item.update({
        "translation_status": "TRANSLATION_UNAVAILABLE", "ko_title": None,
        "snippet_translation_status": "TRANSLATION_UNAVAILABLE", "ko_snippet": None,
    })
    data["news"]["AI"] = _news("NORMAL", [item])
    section = _section(render_dashboard_html_v2(data), "section-AI")
    assert "Original English Headline" in section
    assert "Original English snippet." in section


def test_item_with_no_translation_attempted_shows_original():
    """TIKTOK/SPOTIFY news items (Phase 3C: translation scoped to AI/
    ECONOMY/SOCIETY only) never get translation_status set at all -- must
    still resolve to their real original title/snippet, not crash or
    blank. Tests _display_title/_display_snippet directly -- section-TIKTOK
    has its own unrelated chart-unavailable state message layered on top
    that isn't this test's concern."""
    from report.web_render_v2 import _display_snippet, _display_title

    item = _news_item("Real TikTok News Headline", tier="STANDARD", snippet="Real TikTok snippet.")
    assert _display_title(item) == "Real TikTok News Headline"
    assert _display_snippet(item) == "Real TikTok snippet."


def test_todays_briefing_key_point_uses_korean_title_when_translated():
    data = _empty_dashboard()
    item = _news_item("Original English Headline", tier="LEAD", reason="Why it matters")
    item.update({"translation_status": "TRANSLATED", "ko_title": "번역된 브리핑 헤드라인"})
    data["news"]["AI"] = _news("NORMAL", [item])
    html_out = render_dashboard_html_v2(data)
    assert "번역된 브리핑 헤드라인" in html_out


def test_brief_item_is_compact_row_not_full_article():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Small headline", tier="BRIEF", reason="tiny reason")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="brief-row"' in section
    assert 'class="item-lead"' not in section
    assert 'class="item-standard"' not in section


def test_uninterpreted_state_shows_notice_and_real_items_never_degraded_message():
    """LLM-unavailable fallback: real ingested items must render with a
    clear 'AI interpretation pending' notice, never the DEGRADED failure
    message (which would incorrectly imply collection itself failed)."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("UNINTERPRETED", [
        _news_item("실제 수집된 헤드라인", tier="LEAD", source_count=3)
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "실제 수집된 헤드라인" in section
    assert "AI 해석 대기" in section
    assert "현재 데이터 수집 문제로 이 섹션의 브리핑이 제한됩니다" not in section


def test_source_count_chip_shown_only_when_multiple_sources():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Widely covered", tier="LEAD", source_count=5)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "5개 매체 보도" in section


def test_source_count_chip_absent_for_single_source():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Single source story", tier="LEAD", source_count=1)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "매체 보도" not in section


def test_source_count_alone_never_promotes_tier():
    """source_count is corroboration metadata only -- tier comes from
    report.web_data_v2's own selection-order logic, never re-derived here."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Heavily covered but BRIEF", tier="BRIEF", source_count=20)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="item-lead"' not in section
    assert 'class="brief-row"' in section


def test_cluster_chip_shown_when_related_articles_exist():
    """report.web_data_v2._cluster_suppression's related_article_count/
    related_source_count -- a real near-duplicate-event signal distinct
    from source_count -- must surface as its own chip."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("Representative headline", tier="LEAD", related_article_count=3, related_source_count=2)
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "관련 보도 3건" in section
    assert "2개 매체" in section


def test_cluster_chip_absent_when_no_related_articles():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("Standalone headline", tier="LEAD")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "관련 보도" not in section


def test_brief_item_shows_source_and_date_byline():
    """NEWS QUALITY pass: BRIEF used to render with zero visible date, so a
    week-old story was indistinguishable from a same-day one -- it must
    now carry the same real source/date facts as LEAD/STANDARD, just in a
    subtler, compact form."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("Old brief headline", tier="BRIEF", source_name="outlet-x", published_at="2026-08-04T20:30:00+00:00")
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="brief-meta"' in section
    assert "2026.08.05" in section  # UTC 2026-08-04T20:30 -> KST 2026-08-05


def test_brief_item_omits_byline_when_source_and_date_unavailable():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("No metadata headline", tier="BRIEF")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="brief-meta"' not in section


# ---- Intelligence: honest empty/insufficient states, never invented --------


def test_intelligence_section_shows_insufficient_history_and_progress_bar():
    """When Early Signal/Catalog Revival/Cross-Platform are ALL empty (the
    common short-observation-history case), the section consolidates into
    ONE compact status card instead of 4+ separate 'no signal' blocks --
    see report/web_render_v2.py's _render_intelligence_empty_status_card."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-INTELLIGENCE")
    assert "데이터 축적 현황" in section
    assert "관측" in section
    assert "TikTok" in section
    assert "미연동" in section


def test_intelligence_early_signal_shows_real_candidate():
    data = _empty_dashboard()
    data["intelligence"]["early_signal"]["spotify_chart"] = [
        {"source_name": "spotify_chart", "music_entity_id": 1, "canonical_artist": "코르티스",
         "canonical_title": "REDRED", "rank_delta": 7.0}
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INTELLIGENCE")
    assert "코르티스 - REDRED" in section
    assert "▲7" in section


# ---- Cross-Platform: names every real source, never counts TikTok ---------


def test_cross_platform_lists_only_real_supporting_sources():
    data = _empty_dashboard()
    data["intelligence"]["cross_platform"] = [
        {"music_entity_id": 1, "canonical_artist": "X", "canonical_title": "Y",
         "sources": ["apple_music", "spotify_chart"], "label": "CROSS_PLATFORM_HIT"}
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INTELLIGENCE")
    assert "Apple Music" in section
    assert "Spotify" in section
    assert "2개 소스에서 동시 확인" in section


def test_cross_platform_shows_real_per_source_metric_not_generic_placeholder():
    data = _empty_dashboard()
    data["intelligence"]["cross_platform"] = [
        {"music_entity_id": 1, "canonical_artist": "X", "canonical_title": "Y",
         "sources": ["apple_music", "spotify_chart"], "label": "CROSS_PLATFORM_HIT",
         "source_details": [
             {"source_name": "spotify_chart", "rank": 7, "previous_rank": 18, "rank_delta": 11,
              "is_new": False, "region": "GLOBAL"},
             {"source_name": "apple_music", "rank": 12, "previous_rank": 24, "rank_delta": 12,
              "is_new": False, "region": "KR"},
         ]}
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INTELLIGENCE")
    assert "#18" in section and "#7" in section
    assert "#24" in section and "#12" in section
    assert "Global" in section
    assert "KR" in section


def test_cross_platform_falls_back_honestly_when_source_detail_missing():
    """No fabricated number when source_details wasn't resolvable -- the
    generic 'verified signal' fallback, never an invented rank."""
    data = _empty_dashboard()
    data["intelligence"]["cross_platform"] = [
        {"music_entity_id": 1, "canonical_artist": "X", "canonical_title": "Y",
         "sources": ["apple_music", "spotify_chart"], "label": "CROSS_PLATFORM_HIT", "source_details": []}
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INTELLIGENCE")
    assert section.count("검증된 신호") == 2


def test_cross_platform_always_notes_tiktok_not_auto_detected():
    # All-empty dashboards render the consolidated compact status card
    # (see test_intelligence_section_shows_insufficient_history_and_
    # progress_bar) rather than the full Cross-Platform Movement group --
    # this note is specifically that group's own content, so force it to
    # render by giving early_signal a real candidate (all_empty becomes
    # False).
    data = _empty_dashboard()
    data["intelligence"]["early_signal"]["spotify_chart"] = [
        {"source_name": "spotify_chart", "music_entity_id": 1, "canonical_artist": "코르티스",
         "canonical_title": "REDRED", "rank_delta": 7.0}
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INTELLIGENCE")
    assert "TikTok" in section
    assert "미연동" in section
    assert "교차 플랫폼 자동 감지 대상 아님" in section


def test_cross_platform_never_counts_tiktok_as_a_verified_source():
    data = _empty_dashboard()
    data["intelligence"]["cross_platform"] = [
        {"music_entity_id": 1, "canonical_artist": "X", "canonical_title": "Y",
         "sources": ["apple_music", "spotify_chart"], "label": "CROSS_PLATFORM_HIT"}
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INTELLIGENCE")
    cross_platform_block = section[section.index("Cross-Platform Movement"):]
    verified_count = cross_platform_block.count("검증된 신호")
    assert verified_count == 2  # apple_music + spotify_chart only, never a 3rd for TikTok


# ---- Producer Intelligence: real synthesis, fail-safe empty state ----------


def test_producer_intelligence_empty_state_never_fabricates():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-PRODUCER")
    assert "오늘은 근거가 충분하지 않아 프로듀서 인사이트를 생성하지 않았습니다" in section


def _producer_insight(**overrides):
    base = {
        "what_is_moving": "Multiple rising signals show fast entries",
        "why_it_matters": "This pattern suggests early cross-platform traction",
        "what_to_watch": "Whether the rise continues past the next observation",
        "what_could_i_make_now": "Test a short hook-first intro in the next demo",
        "evidence_refs": ["E1"],
        "evidence": [{"ref": "E1", "summary": "[spotify_chart] Artist - Title (+8 rank)"}],
        "confidence": "MEDIUM",
    }
    base.update(overrides)
    return base


def test_producer_intelligence_renders_6_question_contract_with_observed_inference_split():
    """MUSIC INTELLIGENCE COMPLETION phase: the 4 required fields all
    render, and the observed fact / AI inference distinction is visible
    as separate, distinctly-labeled HTML regions -- never merged into one
    undifferentiated block."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [_producer_insight()]}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "Multiple rising signals show fast entries" in section
    assert "This pattern suggests early cross-platform traction" in section
    assert "Whether the rise continues past the next observation" in section
    assert "Test a short hook-first intro in the next demo" in section
    assert "[spotify_chart] Artist - Title (+8 rank)" in section
    assert "신뢰도" in section
    # observed fact and AI inference are visually distinct regions
    assert 'class="producer-observed-label"' in section
    assert 'class="producer-inference-label"' in section
    observed_pos = section.index('class="producer-observed-label"')
    inference_pos = section.index('class="producer-inference-label"')
    assert observed_pos < inference_pos


def test_producer_intelligence_never_shows_bare_evidence_ref_without_resolved_text():
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [_producer_insight(
        evidence=[{"ref": "E1", "summary": "Full readable evidence text"}],
    )]}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "Full readable evidence text" in section


# ---- Trend Radar: Genre/Production/Producer Reference/K-pop-A&R -----------


def test_music_trend_intelligence_unavailable_state_never_fabricates():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-TRENDS")
    assert "오늘은 근거가 충분하지 않아 트렌드 레이더를 생성하지 않았습니다" in section
    # must never show the DIFFERENT Producer Intelligence section's own
    # empty-state wording -- these are two distinct sections/capabilities.
    assert "프로듀서 인사이트를 생성하지 않았습니다" not in section


def _trend_signal(**overrides):
    base = {
        "observed": "Article explicitly names the genre as bedroom pop",
        "interpretation": "This suggests a real audience shift toward the genre",
        "evidence_refs": ["E1"],
        "evidence": [{"ref": "E1", "summary": "Real Article Title — real snippet naming the genre"}],
        "confidence": "MEDIUM",
    }
    base.update(overrides)
    return base


def test_music_trend_intelligence_renders_signal_with_observed_inference_split():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [_trend_signal()],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-TRENDS")
    assert "Article explicitly names the genre as bedroom pop" in section
    assert "This suggests a real audience shift toward the genre" in section
    assert "Real Article Title — real snippet naming the genre" in section
    assert "신뢰도" in section
    assert 'class="producer-observed-label"' in section
    assert 'class="producer-inference-label"' in section
    observed_pos = section.index('class="producer-observed-label"')
    inference_pos = section.index('class="producer-inference-label"')
    assert observed_pos < inference_pos


def test_music_trend_intelligence_each_category_has_own_honest_empty_message():
    """When state is NORMAL (at least one of the 4 categories has a real
    signal) but a specific OTHER category legitimately found nothing, that
    category shows its own honest, category-specific empty message --
    never silently omitted, never padded with a fabricated entry."""
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [_trend_signal()],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-TRENDS")
    assert "오늘 실제 원문에 프로덕션 특성에 대한 구체적 언급이 없습니다" in section
    assert "오늘 실제 원문에 명시된 프로듀서/협업자 크레딧이 없습니다" in section
    assert "오늘 근거 중 케이팝/A&amp;R과 명확히 연관된 시그널이 없습니다" in section


def test_music_trend_intelligence_never_shows_bare_evidence_ref_without_resolved_text():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "production_notes": [],
        "producer_references": [_trend_signal(
            observed="Article states X produced the track",
            evidence=[{"ref": "E1", "summary": "Full readable producer-credit evidence text"}],
        )],
        "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-TRENDS")
    assert "Full readable producer-credit evidence text" in section


def test_trends_section_precedes_producer_section():
    """Trend Radar sits between Intelligence and Producer Intelligence in
    the MUSIC domain (matches the phase's own Section 7 UI-integration
    instruction: new sub-capabilities inside the existing MUSIC
    INTELLIGENCE architecture, in this specific order)."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert html_out.index('id="section-INTELLIGENCE"') < html_out.index('id="section-TRENDS"')
    assert html_out.index('id="section-TRENDS"') < html_out.index('id="section-PRODUCER"')


# ---- Sources: only what the input data actually names -----------------------


def test_sources_section_lists_active_sources_and_honest_tiktok_status():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-SOURCES")
    assert "Apple Music" in section
    assert "Spotify" in section
    assert "TikTok" in section
    assert "미연동" in section


# ---- TODAY IN 30 SECONDS: mechanical restatement, never a new synthesis ----


def test_today_in_30_seconds_uses_top_spotify_entry():
    data = _empty_dashboard()
    entries = [_spotify_entry(1, artist="A", title="B")]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    key_points = html_out[html_out.index('class="key-points"'):html_out.index("</ul>")]
    assert "A - B" in key_points


def test_today_in_30_seconds_dominant_is_freshest_real_news_lead():
    """FINAL PREMIUM UI phase: the freshest real AI/ECONOMY/SOCIETY LEAD
    (by real published_at) gets the dominant slot; a real MUSIC entry
    (elevated, distinct styling) comes right after it -- ahead of the
    other, now-tertiary, real news leads -- and any generic chart/signal
    chip renders last of all."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("AI lead", tier="LEAD", published_at="2026-08-13T10:00:00+00:00")
    ])
    data["news"]["ECONOMY"] = _news("NORMAL", [
        _news_item("Economy lead", tier="LEAD", published_at="2026-08-13T20:00:00+00:00")
    ])
    data["news"]["SOCIETY"] = _news("NORMAL", [
        _news_item("Society lead", tier="LEAD", published_at="2026-08-13T05:00:00+00:00")
    ])
    entries = [_spotify_entry(1, artist="A", title="B")]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    key_points = html_out[html_out.index('class="key-points"'):html_out.index("</ul>")]
    dominant_start = key_points.index('class="key-point key-point-dominant"')
    economy_start = key_points.index("Economy lead")
    assert dominant_start < economy_start < key_points.index('class="key-point"', dominant_start + 1)
    # dominant -> MUSIC (elevated) -> remaining real news leads -> nothing after
    assert key_points.index("Economy lead") < key_points.index('key-point-music')
    assert key_points.index('key-point-music') < key_points.index("A - B")
    assert key_points.index("A - B") < key_points.index("AI lead") < key_points.index("Society lead")


def test_today_music_prefers_real_music_news_headline_over_bare_chart_fact():
    """FINAL PREMIUM UI phase: when a real Music Industry/Spotify NEWS
    item exists, it's used for the elevated MUSIC key-point (real
    editorial content) rather than a bare chart number -- and the
    Spotify chart leader still appears separately as its own signal chip
    (a different real fact, not a duplicate)."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("Real music industry headline", tier="LEAD")])
    entries = [_spotify_entry(1, artist="Artist", title="Track")]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    key_points = html_out[html_out.index('class="key-points"'):html_out.index("</ul>")]
    assert 'class="key-point key-point-music"' in key_points
    assert "Real music industry headline" in key_points
    assert "Artist - Track" in key_points  # the chart leader still shown, as its own separate chip


def test_today_music_falls_back_to_chart_leader_when_no_music_news():
    """No real Music Industry/Spotify news item exists that day -- the
    elevated MUSIC key-point falls back to the real Spotify chart leader
    rather than being silently omitted (music must not simply disappear
    from the first screen just because the news sub-track is empty that
    day), and no duplicate chip is added for the same fact."""
    data = _empty_dashboard()
    entries = [_spotify_entry(1, artist="Artist", title="Track")]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [], "trend": _trend(entries)}
    html_out = render_dashboard_html_v2(data)
    key_points = html_out[html_out.index('class="key-points"'):html_out.index("</ul>")]
    assert 'class="key-point key-point-music"' in key_points
    assert "Artist - Track" in key_points
    assert key_points.count("Artist - Track") == 1  # never shown twice


def test_today_music_omitted_when_no_real_music_data_at_all():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert 'class="key-point key-point-music"' not in html_out


def test_today_in_30_seconds_never_fabricates_tiktok():
    # An operational status message ("데이터 소스 미연동") is never given a
    # first-screen headline-card slot (see SUPER_NEWS_HANDOFF.md next-phase
    # punch list #9) -- on an all-empty dashboard there is nothing real to
    # show, so the key-points block is simply omitted rather than filled
    # with a status card standing in for a real finding.
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert 'class="key-points"' not in html_out
    assert "데이터 소스 미연동" not in html_out


# ---- No placeholder leakage from renderer fallback logic --------------------


def test_renderer_never_leaks_placeholder_names_on_its_own():
    """The renderer must not have any hardcoded placeholder/demo entity
    name baked into its fallback logic -- an empty dashboard must not
    surface anything resembling a fake artist/track name."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    for placeholder in ("Artist A", "Artist B", "Track A", "Song A", "New Artist", "New Song",
                         "Example Artist", "Example Track", "Sample Song"):
        assert placeholder not in html_out


# ---- Navigation: covers every rendered section ------------------------------


def test_nav_links_cover_every_section_id():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    for section_id in ("section-TIKTOK", "section-SPOTIFY", "section-VIRAL", "section-INTELLIGENCE",
                        "section-INDUSTRY", "section-TRENDS", "section-AI", "section-ECONOMY",
                        "section-SOCIETY", "section-PRODUCER", "section-SOURCES"):
        assert f'href="#{section_id}"' in html_out


def test_section_order_matches_docstring():
    """FINAL PREMIUM UI phase / MUSIC INTELLIGENCE COMPLETION: MUSIC
    INTELLIGENCE (industry news -> chart data -> signals -> trend radar
    -> producer intelligence) is now one consolidated domain, positioned
    entirely before AI/ECONOMY/SOCIETY news."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    order = ["section-INDUSTRY", "section-TIKTOK", "section-SPOTIFY", "section-VIRAL",
             "section-INTELLIGENCE", "section-TRENDS", "section-PRODUCER",
             "section-AI", "section-ECONOMY", "section-SOCIETY", "section-SOURCES"]
    positions = [html_out.index(f'id="{sid}"') for sid in order]
    assert positions == sorted(positions)


def test_music_domain_header_precedes_all_music_sections():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    header_pos = html_out.index('class="music-domain-header"')
    for section_id in ("section-INDUSTRY", "section-TIKTOK", "section-SPOTIFY", "section-VIRAL",
                        "section-INTELLIGENCE", "section-TRENDS", "section-PRODUCER"):
        assert header_pos < html_out.index(f'id="{section_id}"')


# ---- HTML escaping / no framework / mobile+desktop responsive --------------


def test_html_escapes_dangerous_content():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("<script>alert(1)</script>", tier="LEAD")])
    html_out = render_dashboard_html_v2(data)
    assert "<script>alert" not in html_out
    assert "&lt;script&gt;" in html_out


def test_dangerous_source_url_is_escaped_in_href():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("x", tier="BRIEF", source_url='https://x/"><script>alert(1)</script>')
    ])
    html_out = render_dashboard_html_v2(data)
    assert "<script>alert" not in html_out


def test_no_external_script_or_stylesheet():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert "<script" not in html_out
    assert '<link rel="stylesheet"' not in html_out


def test_mobile_viewport_present():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html_out


def test_desktop_canvas_wider_than_v1_800px_frame():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert "max-width: 1180px" in html_out


def test_mobile_nav_collapses_to_horizontal_strip():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert "@media (max-width: 960px)" in html_out
    assert "overflow-x: auto" in html_out


def test_mobile_key_point_music_grid_column_override_present():
    """Real defect found in Playwright QA (FINAL PREMIUM UI phase):
    .key-point-music's base `grid-column: span 2` has no 2nd explicit
    column on the 1fr mobile grid, so the browser auto-created an
    implicit column -- corrupting every sibling key-point's computed
    width (the dominant headline wrapped one syllable per line) even
    though .key-point-music isn't itself the dominant item. A mobile-
    specific override forcing it back to the single real column must
    stay present inside the mobile media query."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    mobile_block = html_out[html_out.index("@media (max-width: 600px)"):]
    assert "li.key-point-music { grid-column: 1 / -1;" in mobile_block


def test_deterministic_across_calls():
    data = _empty_dashboard()
    assert render_dashboard_html_v2(data) == render_dashboard_html_v2(data)


def test_dark_mode_tokens_defined():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert "prefers-color-scheme: dark" in html_out


# ---- V1 untouched -------------------------------------------------------


def test_v1_web_render_section_order_unchanged():
    from report.web_render import SECTION_ORDER
    assert SECTION_ORDER == ("AI", "MUSIC", "ECONOMY", "SOCIETY")

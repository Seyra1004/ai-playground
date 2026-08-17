"""report.kakao_render_v2: exactly one message, always <= MAX_TEXT_LENGTH,
never contains a full TOP10 list, never fabricates TikTok data."""

from report.kakao_render import split_message
from report.kakao_render_v2 import (
    MAX_TEXT_LENGTH,
    _compact_korean_display_text,
    render_daily_kakao_digest,
    render_full_digest_text,
    render_kakao_digest,
    render_music_kakao_digest,
)


def _empty_dashboard():
    return {
        "report_date_kst": "2026-08-13",
        "news": {
            "AI": {"state": "DEGRADED", "items": []},
            "ECONOMY": {"state": "DEGRADED", "items": []},
            "SOCIETY": {"state": "DEGRADED", "items": []},
            "TIKTOK": {"state": "DEGRADED", "items": []},
            "SPOTIFY": {"state": "DEGRADED", "items": []},
        },
        "tiktok_chart": {"state": "UNAVAILABLE", "top10": [], "new_entries": []},
        "spotify_chart": {"state": "UNAVAILABLE", "top10": [], "new_entries": []},
        "intelligence": {"early_signal": {}, "catalog_revival": {}, "cross_platform": [], "outlook": {}},
        "music_trend_intelligence": {
            "state": "UNAVAILABLE", "genre_signals": [], "production_notes": [],
            "producer_references": [], "kpop_ar_notes": [],
        },
        "producer_intelligence": {"state": "UNAVAILABLE", "insights": []},
    }


def test_always_within_kakao_char_limit_even_when_empty():
    text = render_kakao_digest(_empty_dashboard())
    assert len(text) <= MAX_TEXT_LENGTH


def test_tiktok_line_is_always_honest_never_fabricated():
    text = render_kakao_digest(_empty_dashboard())
    assert "TikTok: 데이터 소스 미가동" in text


def test_never_contains_a_full_top10_list():
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [
            {"music_entity_id": i, "rank": i, "canonical_artist": f"Artist{i}", "canonical_title": f"Song{i}",
             "is_new": False, "rank_delta": 0}
            for i in range(1, 11)
        ],
        "new_entries": [],
    }
    text = render_kakao_digest(data)
    # Only rank-1 should appear; ranks 2-10 must not.
    assert "Artist1" in text
    for i in range(2, 11):
        assert f"Artist{i}" not in text


def test_full_realistic_content_stays_within_limit_and_uses_real_data():
    data = _empty_dashboard()
    data["news"]["AI"] = {"state": "NORMAL", "items": [{
        "title": "From assistance to execution: How enterprises put AI to work",
        "reason": "x", "source_url": "https://openai.com/x"}]}
    data["news"]["ECONOMY"] = {"state": "NORMAL", "items": [{
        "title": "청년 취업자 45개월째 감소…정말 심각하다", "reason": "x", "source_url": "https://x"}]}
    data["news"]["SOCIETY"] = {"state": "NORMAL", "items": [{
        "title": "[서울로…청년들 '광주 탈출' 리포트] (2) 같은 일, 다른 임금", "reason": "x", "source_url": "https://x"}]}
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [{"music_entity_id": 1, "rank": 1, "canonical_artist": "Post Malone & Swae Lee",
                    "canonical_title": "Sunflower (Spider-Man: Into the Spider-Verse)",
                    "is_new": False, "rank_delta": 2}],
        "new_entries": [],
    }
    data["intelligence"]["early_signal"] = {
        "spotify_chart": [{"source_name": "spotify_chart", "music_entity_id": 1,
                            "canonical_artist": "코르티스", "canonical_title": "REDRED", "rank_delta": 7.0}],
    }

    text = render_kakao_digest(data)
    assert len(text) <= MAX_TEXT_LENGTH
    assert "SUPER NEWS | 8월 13일" in text
    assert "청년 취업자" in text or "청년 취업자"[:10] in text  # may be clipped, must appear at least partially
    assert "전체 브리핑" in text


def test_deterministic_across_calls():
    data = _empty_dashboard()
    assert render_kakao_digest(data) == render_kakao_digest(data)


def test_no_url_embedded_in_text():
    data = _empty_dashboard()
    data["news"]["AI"] = {"state": "NORMAL", "items": [{
        "title": "AI headline", "reason": "x", "source_url": "https://example.com/very/long/path"}]}
    text = render_kakao_digest(data)
    assert "https://" not in text
    assert "http://" not in text


# =============================================================================
# render_full_digest_text: the real, multi-message Music-Intelligence-
# included digest (FINAL MUSIC INTEGRATION / KAKAO E2E phase)
# =============================================================================


def test_full_digest_empty_state_is_all_honest_never_fabricated():
    text = render_full_digest_text(_empty_dashboard())
    assert "[TIKTOK]" in text and "미가동" in text
    assert "오늘은 근거 부족" in text  # Trend Radar / Producer Intelligence honest empty
    assert "데이터 부족" in text or "데이터 없음" in text  # Future Radar honest empty


def test_full_digest_splits_into_multiple_kakao_valid_chunks():
    text = render_full_digest_text(_empty_dashboard())
    chunks = split_message(text, MAX_TEXT_LENGTH)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert len(chunk) <= MAX_TEXT_LENGTH


def test_full_digest_includes_genre_radar_with_observed_inference_labels():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "Article explicitly names the genre as bedroom pop",
            "interpretation": "suggests a real audience shift toward the genre",
            "evidence_refs": ["E1"], "confidence": "MEDIUM",
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    text = render_full_digest_text(data)
    assert "관찰:" in text
    assert "추론:" in text
    # Observed content appears before the inference label for that item.
    assert text.index("관찰:") < text.index("추론:")


def test_full_digest_never_leaks_a_bare_evidence_ref_code():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "Real observed text", "interpretation": "Real inference text",
            "evidence_refs": ["E7"], "confidence": "HIGH",
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    text = render_full_digest_text(data)
    assert "E7" not in text  # ref codes are internal-only, never rendered


def test_full_digest_includes_producer_intelligence_observed_and_takeaway():
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [{
        "what_is_moving": "Multiple rising signals show fast entries",
        "why_it_matters": "x", "what_to_watch": "y",
        "what_could_i_make_now": "Test a short hook-first intro next demo",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }]}
    text = render_full_digest_text(data)
    assert "Multiple rising signals show fast entries" in text or "Multiple rising signals show fast" in text
    assert "hook-first" in text


def test_full_digest_future_radar_reports_real_days_not_fabricated():
    data = _empty_dashboard()
    data["intelligence"]["outlook"] = {
        "spotify_chart": {"status": "INSUFFICIENT_HISTORY", "days_of_history": 0, "min_required_days": 90},
        "apple_music": {"status": "INSUFFICIENT_HISTORY", "days_of_history": 0, "min_required_days": 90},
    }
    text = render_full_digest_text(data)
    assert "0/90" in text


def test_full_digest_deterministic_across_calls():
    data = _empty_dashboard()
    assert render_full_digest_text(data) == render_full_digest_text(data)


def test_full_digest_tiktok_chart_never_fabricated_even_with_real_music_content():
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [{"music_entity_id": 1, "rank": 1, "canonical_artist": "Artist", "canonical_title": "Title",
                    "is_new": False, "rank_delta": 0}],
        "new_entries": [],
    }
    text = render_full_digest_text(data)
    assert "데이터 소스 미가동" in text  # TikTok chart status unaffected by real Spotify data


# =============================================================================
# render_music_kakao_digest / render_daily_kakao_digest -- independent
# per-product messages (Kakao delivery split phase)
# =============================================================================


def test_music_digest_within_limit_and_never_contains_daily_content():
    data = _empty_dashboard()
    data["news"]["AI"] = {"state": "NORMAL", "items": [{
        "title": "AI 뉴스 제목", "reason": "x", "source_url": "https://x"}]}
    data["news"]["ECONOMY"] = {"state": "NORMAL", "items": [{
        "title": "경제 뉴스 제목", "reason": "x", "source_url": "https://x"}]}
    text = render_music_kakao_digest(data)
    assert len(text) <= MAX_TEXT_LENGTH
    assert "SUPER NEWS MUSIC" in text
    assert "AI 뉴스 제목" not in text
    assert "경제 뉴스 제목" not in text
    assert "LEAD:" in text and "INDUSTRY:" in text


def test_daily_digest_within_limit_and_never_contains_music_content():
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [{"music_entity_id": 1, "rank": 1, "canonical_artist": "Artist", "canonical_title": "Title",
                    "is_new": False, "rank_delta": 0}],
        "new_entries": [],
    }
    data["news"]["AI"] = {"state": "NORMAL", "items": [{
        "title": "AI 뉴스 제목", "reason": "x", "source_url": "https://x"}]}
    text = render_daily_kakao_digest(data)
    assert len(text) <= MAX_TEXT_LENGTH
    assert "SUPER NEWS DAILY" in text
    assert "AI 뉴스 제목" in text
    assert "Artist" not in text
    assert "TikTok:" not in text and "Spotify:" not in text


def test_music_digest_honest_empty_state():
    text = render_music_kakao_digest(_empty_dashboard())
    assert "LEAD: 오늘 보고할 소식 없음" in text
    assert "INDUSTRY: 오늘 보고할 뮤직 인더스트리 뉴스 없음" in text


def test_daily_digest_honest_empty_state():
    text = render_daily_kakao_digest(_empty_dashboard())
    assert "오늘 보고할 뉴스 없음" in text


def test_music_and_daily_digest_deterministic_across_calls():
    data = _empty_dashboard()
    assert render_music_kakao_digest(data) == render_music_kakao_digest(data)
    assert render_daily_kakao_digest(data) == render_daily_kakao_digest(data)


def test_music_digest_industry_line_skips_gossip_and_falls_back_to_next_item():
    """Closes the real gap where the HTML Producer/A&R gossip filter
    (report.web_render_v2, report.validation.is_low_value_gossip_takeaway)
    didn't reach the Kakao MUSIC digest's own separate Industry-line
    selection -- confirmed real: a "Chris Brown Allegedly Tells BTS Fan
    ... in Deleted TikTok Comment" item still reached an actual sent Kakao
    message even after the HTML page was already clean."""
    data = _empty_dashboard()
    data["news"]["TIKTOK"] = {"state": "NORMAL", "items": [
        {"title": "Chris Brown Allegedly Tells BTS Fan 'Pray Bout It Hoe' in Deleted TikTok Comment",
         "reason": "x", "source_url": "https://x"},
        {"title": "TikTok Campaigns Shaping the Future of the Music Industry",
         "reason": "x", "source_url": "https://x"},
    ]}
    text = render_music_kakao_digest(data)
    assert "Chris Brown" not in text
    assert "TikTok Campaigns Shaping" in text  # clipped by _FIELD_BUDGET, prefix still present


def test_music_digest_industry_line_omits_rather_than_shows_only_gossip():
    data = _empty_dashboard()
    data["news"]["TIKTOK"] = {"state": "NORMAL", "items": [
        {"title": "Chris Brown Allegedly Tells BTS Fan 'Pray Bout It Hoe' in Deleted TikTok Comment",
         "reason": "x", "source_url": "https://x"},
    ]}
    text = render_music_kakao_digest(data)
    assert "Chris Brown" not in text
    assert "오늘 보고할 뮤직 인더스트리 뉴스 없음" in text


# =============================================================================
# SUPER NEWS MUSIC KAKAO ALIGNMENT PASS (2026-08-18): Kakao must consume the
# SAME accepted MUSIC content web's report.web_render_v2.
# resolve_music_lead_and_industry() computes -- never an independently
# re-derived second selection.
# =============================================================================


def test_music_digest_industry_prioritizes_professional_item_regardless_of_list_order():
    """WEB/KAKAO ALIGNMENT: a genuine rights/AI-music-class story
    (report.web_data_v2.music_industry_priority_rank) must win the
    Industry line even when a generic story appears first in the raw
    list -- the SAME real ranking the web page's Industry section
    already applies, never plain "first selected" order."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Some Artist Announces Tour Dates", "reason": "x", "source_url": "https://x",
         "event_key": "ev-generic", "source_count": 1},
        {"title": "Platform bans fully AI-generated songs", "reason": "x", "source_url": "https://x",
         "event_key": "ev-ai", "source_count": 1},
    ]}
    text = render_music_kakao_digest(data)
    assert "AI-generated" in text
    assert "Tour Dates" not in text


def test_music_digest_industry_excludes_the_same_event_already_shown_as_lead():
    """WEB/KAKAO ALIGNMENT: the Industry line must never repeat the SAME
    real event already shown as LEAD -- matches the web page's own
    exclude_event_key rule (report.web_render_v2._merge_music_industry_
    items), never a second, independent notion of "already shown."""
    data = _empty_dashboard()
    lead_item = {"title": "Megan deal", "ko_title": "메간 계약", "translation_status": "TRANSLATED",
                 "event_key": "ev-lead", "source_url": "https://x"}
    data["today_music_intelligence"] = [
        {"type": "INDUSTRY_NEWS", "headline_item": lead_item, "is_strongest": True},
    ]
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Megan deal", "reason": "x", "source_url": "https://x", "event_key": "ev-lead", "source_count": 1},
        {"title": "Second real industry story", "reason": "x", "source_url": "https://x",
         "event_key": "ev-second", "source_count": 1},
    ]}
    text = render_music_kakao_digest(data)
    assert "LEAD: 메간 계약" in text
    assert "INDUSTRY: Second real industry story" in text
    assert "INDUSTRY: Megan deal" not in text


def test_music_digest_producer_line_skips_insight_about_the_same_event_as_lead():
    """WEB/KAKAO ALIGNMENT: the Producer/A&R line must not repeat the SAME
    real event already shown as LEAD -- falls through to the next
    genuinely distinct insight, reusing report.web_render_v2.
    _synthesis_entry_event_identity, the SAME real event-identity
    resolution the web page's own cross-section dedup already applies."""
    data = _empty_dashboard()
    lead_item = {"title": "Lead Story Title", "ko_title": "리드 스토리", "translation_status": "TRANSLATED",
                 "event_key": "ev-lead", "source_url": "https://x"}
    data["today_music_intelligence"] = [
        {"type": "INDUSTRY_NEWS", "headline_item": lead_item, "is_strongest": True},
    ]
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Lead Story Title", "reason": "x", "source_url": "https://x",
         "event_key": "ev-lead", "source_count": 1},
    ]}
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [
        {"what_is_moving": "리드와 같은 이벤트에 대한 재해석", "confidence": "HIGH",
         "evidence": [{"ref": "E1", "summary": "Lead Story Title"}]},
        {"what_is_moving": "완전히 다른 실제 프로듀서 인사이트", "confidence": "HIGH",
         "evidence": [{"ref": "E1", "summary": "Some Unrelated Fact"}]},
    ]}
    text = render_music_kakao_digest(data)
    assert "완전히 다른 실제 프로듀서 인사이트" in text
    assert "리드와 같은 이벤트에 대한 재해석" not in text


def test_music_digest_no_duplicate_event_across_lead_industry_and_producer_lines():
    """No-duplication requirement: LEAD, Industry, and Producer/A&R lines
    must each cover a genuinely distinct real event when the underlying
    data supports it."""
    data = _empty_dashboard()
    lead_item = {"title": "Lead Story", "ko_title": "리드 스토리", "translation_status": "TRANSLATED",
                 "event_key": "ev-lead", "source_url": "https://x"}
    data["today_music_intelligence"] = [
        {"type": "INDUSTRY_NEWS", "headline_item": lead_item, "is_strongest": True},
    ]
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Lead Story", "reason": "x", "source_url": "https://x", "event_key": "ev-lead", "source_count": 1},
        {"title": "Distinct industry story", "reason": "x", "source_url": "https://x",
         "event_key": "ev-industry", "source_count": 1},
    ]}
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [
        {"what_is_moving": "산업 뉴스와 같은 이벤트", "confidence": "HIGH",
         "evidence": [{"ref": "E1", "summary": "Distinct industry story"}]},
        {"what_is_moving": "진짜 별개의 프로듀서 인사이트", "confidence": "HIGH",
         "evidence": [{"ref": "E1", "summary": "Yet another unrelated fact"}]},
    ]}
    text = render_music_kakao_digest(data)
    assert "리드 스토리" in text
    assert "Distinct industry story" in text
    assert "진짜 별개의 프로듀서 인사이트" in text
    assert "산업 뉴스와 같은 이벤트" not in text


def test_music_digest_never_shows_empty_signal_line_when_no_real_signal():
    text = render_music_kakao_digest(_empty_dashboard())
    assert "Signal:" not in text


def test_music_digest_omits_signal_line_even_when_real_signal_data_exists():
    """EDITORIAL QUALITY PASS: cross-platform chart Signal is structurally
    low-value filler that crowds out real professional content on almost
    every real day -- never shown, even when real early_signal data is
    present."""
    data = _empty_dashboard()
    data["intelligence"]["early_signal"]["spotify_chart"] = [
        {"canonical_artist": "Shakira", "canonical_title": "Dai Dai", "rank_delta": 2},
    ]
    text = render_music_kakao_digest(data)
    assert "Signal:" not in text
    assert "Shakira" not in text


def test_music_digest_ar_line_omitted_when_only_medium_confidence_available():
    """PRODUCER/A&R RULE: a borderline (MEDIUM-confidence) insight must
    never be forced in merely to fill a third slot -- 2 excellent items
    beat 3 mediocre ones."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [
        {"what_is_moving": "애매한 신뢰도의 시그널", "confidence": "MEDIUM",
         "evidence": [{"ref": "E1", "summary": "Some Unrelated Fact"}]},
    ]}
    text = render_music_kakao_digest(data)
    assert "A&R:" not in text
    assert "애매한 신뢰도의 시그널" not in text


def test_music_digest_ar_line_included_when_high_confidence_and_distinct():
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [
        {"what_is_moving": "확실한 프로듀서 인사이트", "why_it_matters": "실행 가능한 근거 있는 시사점",
         "confidence": "HIGH", "evidence": [{"ref": "E1", "summary": "Some Unrelated Fact"}]},
    ]}
    text = render_music_kakao_digest(data)
    assert "A&R: 확실한 프로듀서 인사이트" in text
    assert "→ 실행 가능한 근거 있는 시사점" in text


def test_music_digest_industry_why_line_omitted_when_no_reason_and_no_class_match():
    """A backfilled Industry item (report.web_data_v2.
    professional_evidence_backfill) never has a real `reason`, and a
    title matching none of music_industry_priority_rank's real priority
    classes gets no class-fallback why either -- the "→" line must be
    omitted entirely, never fabricated."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Backfilled professional story", "reason": None, "source_url": "https://x",
         "event_key": "ev-1", "source_count": 1},
    ]}
    text = render_music_kakao_digest(data)
    assert "INDUSTRY: Backfilled professional story" in text
    assert "INDUSTRY: Backfilled professional story\n→" not in text


def test_music_digest_industry_why_line_uses_real_priority_class_when_no_reason():
    """A backfilled Industry item whose title DOES match a real priority
    class (report.web_data_v2.music_industry_priority_rank) gets that
    class's real, general (never title-specific) why-fallback -- never
    left with no professional framing just because it was backfilled
    rather than NEWS_COMBINED-selected."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Platform bans fully AI-generated songs", "reason": None, "source_url": "https://x",
         "event_key": "ev-1", "source_count": 1},
    ]}
    text = render_music_kakao_digest(data)
    assert "INDUSTRY: Platform bans fully AI-generated songs" in text
    assert "→ AI 음악 유통 기준 변화 신호" in text


def test_music_digest_industry_why_line_shown_when_real_reason_exists():
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Selected industry story", "reason": "실제 근거 있는 이유",
         "source_url": "https://x", "event_key": "ev-1", "source_count": 1},
    ]}
    text = render_music_kakao_digest(data)
    assert "INDUSTRY: Selected industry story" in text
    assert "→ 실제 근거 있는 이유" in text


def test_music_digest_does_not_clip_a_title_that_fits_the_budget():
    """TITLE RULE: a real title within budget must render complete, never
    truncated with an ellipsis just because it's on the longer side."""
    data = _empty_dashboard()
    long_but_fitting_title = "메건 디 스탤리언, 인터스코프 계약에도 마스터권 유지"
    lead_item = {"title": long_but_fitting_title, "ko_title": long_but_fitting_title,
                 "translation_status": "TRANSLATED", "event_key": "ev-lead", "source_url": "https://x"}
    data["today_music_intelligence"] = [
        {"type": "INDUSTRY_NEWS", "headline_item": lead_item, "is_strongest": True},
    ]
    text = render_music_kakao_digest(data)
    assert f"LEAD: {long_but_fitting_title}" in text
    assert "…" not in text


def test_music_digest_within_real_kakao_send_limit():
    """The real, documented Kakao API limit for the 기본 텍스트 템플릿 `text`
    field (kakao.client.MAX_TEXT_LENGTH) -- not an arbitrary internal
    choice -- must never be exceeded even on a full real 2026-08-18-shaped
    candidate (LEAD + INDUSTRY + A&R, each with a real why/interpretation
    line)."""
    from kakao.client import MAX_TEXT_LENGTH as REAL_KAKAO_LIMIT

    data = _empty_dashboard()
    lead_item = {"title": "Megan Thee Stallion inks deal with UMG's Interscope",
                 "ko_title": "Megan Thee Stallion, UMG의 Interscope와 계약 체결 – 마스터 소유권 유지",
                 "translation_status": "TRANSLATED", "event_key": "ev-lead", "source_url": "https://x"}
    data["today_music_intelligence"] = [
        {"type": "INDUSTRY_NEWS", "headline_item": lead_item, "is_strongest": True,
         "why_it_matters": "메간 디 스탤리언의 UMG 인터스코프 계약(마스터권 유지)은 아티스트 권리 협상의 새 기준을 제시한다."},
    ]
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Beatport is now banning fully AI-generated songs",
         "ko_title": "Beatport, 완전히 AI로 생성된 곡 금지", "translation_status": "TRANSLATED",
         "reason": None, "source_url": "https://x", "event_key": "ev-industry", "source_count": 1},
    ]}
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [
        {"what_is_moving": "확실한 프로듀서 인사이트", "why_it_matters": "실행 가능한 근거 있는 시사점",
         "confidence": "HIGH", "evidence": [{"ref": "E1", "summary": "Some Unrelated Fact"}]},
    ]}
    text = render_music_kakao_digest(data)
    assert len(text) <= REAL_KAKAO_LIMIT == MAX_TEXT_LENGTH


def test_daily_digest_unaffected_by_music_content_identity_consolidation():
    """DAILY Kakao is untouched by this pass: render_daily_kakao_digest
    never calls report.web_render_v2.resolve_music_lead_and_industry and
    its output shape is unchanged."""
    data = _empty_dashboard()
    data["news"]["AI"] = {"state": "NORMAL", "items": [{
        "title": "AI 뉴스 제목", "reason": "x", "source_url": "https://x"}]}
    text = render_daily_kakao_digest(data)
    assert "AI: AI 뉴스 제목" in text
    assert "LEAD:" not in text and "Industry:" not in text


# =============================================================================
# COPY QUALITY MICRO-FIX (2026-08-18): _compact_korean_display_text
# =============================================================================


def test_compact_korean_display_text_drops_redundant_topic_clause_after_paren():
    text = "메간 디 스탤리언의 UMG 인터스코프 계약(마스터권 유지)은 아티스트 권리 협상의 새 기준을 제시한다."
    assert _compact_korean_display_text(text) == "아티스트 권리 협상의 새 기준"


def test_compact_korean_display_text_leaves_verb_ending_sentence_untouched():
    """CORRECTNESS OVER COVERAGE: a real relative-clause verb ending
    ("지키는") must never be mistaken for a topic-marker split point --
    there is no closing-paren-anchored topic marker here, so the real
    sentence is returned completely unchanged rather than mangled."""
    text = "신보마다 연속으로 빌보드 200 정상을 지키는 것은 팬덤 기반 초동 판매력이 매우 안정적임을 보여준다."
    assert _compact_korean_display_text(text) == text


def test_compact_korean_display_text_leaves_short_class_fallback_untouched():
    text = "AI 음악 유통 기준 변화 신호"
    assert _compact_korean_display_text(text) == text


def test_compact_korean_display_text_handles_none():
    assert _compact_korean_display_text(None) is None


def test_music_digest_shows_no_ellipsis_for_current_2026_08_18_shaped_candidate():
    """HARD QUALITY RULE: the compact structural rewrite must eliminate
    the ellipsis entirely for a real Megan/Beatport-shaped candidate,
    never merely shrink it."""
    data = _empty_dashboard()
    lead_item = {"title": "Megan Thee Stallion inks deal with UMG's Interscope",
                 "ko_title": "Megan Thee Stallion, UMG의 Interscope와 계약 체결 – 마스터 소유권 유지",
                 "translation_status": "TRANSLATED", "event_key": "ev-lead", "source_url": "https://x"}
    data["today_music_intelligence"] = [
        {"type": "INDUSTRY_NEWS", "headline_item": lead_item, "is_strongest": True,
         "why_it_matters": "메간 디 스탤리언의 UMG 인터스코프 계약(마스터권 유지)은 아티스트 권리 협상의 새 기준을 제시한다."},
    ]
    data["news"]["SPOTIFY"] = {"state": "NORMAL", "items": [
        {"title": "Beatport is now banning fully AI-generated songs",
         "ko_title": "Beatport, 완전히 AI로 생성된 곡 금지", "translation_status": "TRANSLATED",
         "reason": None, "source_url": "https://x", "event_key": "ev-industry", "source_count": 1},
    ]}
    text = render_music_kakao_digest(data)
    assert "…" not in text
    assert "LEAD: Megan Thee Stallion, UMG의 Interscope와 계약 체결 – 마스터 소유권 유지" in text
    assert "→ 아티스트 권리 협상의 새 기준" in text
    assert "INDUSTRY: Beatport, 완전히 AI로 생성된 곡 금지" in text
    assert "→ AI 음악 유통 기준 변화 신호" in text

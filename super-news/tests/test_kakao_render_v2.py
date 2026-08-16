"""report.kakao_render_v2: exactly one message, always <= MAX_TEXT_LENGTH,
never contains a full TOP10 list, never fabricates TikTok data."""

from report.kakao_render import split_message
from report.kakao_render_v2 import (
    MAX_TEXT_LENGTH,
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
    assert "TikTok:" in text and "Spotify:" in text


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
    assert "데이터 소스 미가동" in text  # TikTok honest-empty


def test_daily_digest_honest_empty_state():
    text = render_daily_kakao_digest(_empty_dashboard())
    assert "오늘 보고할 뉴스 없음" in text


def test_music_and_daily_digest_deterministic_across_calls():
    data = _empty_dashboard()
    assert render_music_kakao_digest(data) == render_music_kakao_digest(data)
    assert render_daily_kakao_digest(data) == render_daily_kakao_digest(data)

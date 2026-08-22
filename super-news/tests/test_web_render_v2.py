"""report.web_render_v2: presentation-only editorial Intelligence Dashboard
rendering from the exact structured shape report.web_data_v2.
build_dashboard_data_v2() produces (INCLUDING its two curation surfaces,
today_music_intelligence and music_today). No DB access, no LLM call --
pure function of its input dict.

CATEGORY-CONTIGUOUS IA REFINEMENT: covers the music-primary,
category-contiguous architecture -- TODAY'S MUSIC INTELLIGENCE's MUSIC-
ONLY hero (<=5 signals, AI/ECONOMY/SOCIETY never mixed in), MUSIC TODAY's
FACT/ANALYSIS card rendering, Chart Pulse's merged TOP10+badges table, the
Genre/Production Radar "오늘 관측" (never a fabricated trend direction)
labeling, Producer/A&R Takeaways' 5-item combined cap with collapsed-by-
default evidence, AI's capped-with-archive vs. ECONOMY/SOCIETY's
capped-with-NO-archive contract, the simplified category-color system,
and the removal of per-section "AI 해석 대기" pipeline-status text from
primary content."""

from report.web_render_v2 import (
    is_korean_first_ready,
    is_korean_first_text_ready,
    render_dashboard_html_v2,
    render_daily_page_html_v2,
    render_music_page_html_v2,
)


def _news(state="DEGRADED", items=None):
    return {"state": state, "items": items if items is not None else []}


def _news_item(title, tier="BRIEF", reason=None, snippet=None, source_url=None, source_count=1,
                source_name=None, published_at=None, related_article_count=None, related_source_count=None,
                ai_intelligence_status="UNAVAILABLE", why_it_matters=None, what_to_watch=None,
                translation_status=None, ko_title=None, image_url=None, event_key=None):
    return {
        "title": title, "reason": reason, "snippet": snippet, "source_url": source_url,
        "source_count": source_count, "tier": tier, "source_name": source_name, "published_at": published_at,
        "related_article_count": related_article_count, "related_source_count": related_source_count,
        "ai_intelligence_status": ai_intelligence_status, "why_it_matters": why_it_matters,
        "what_to_watch": what_to_watch, "translation_status": translation_status, "ko_title": ko_title,
        "image_url": image_url, "event_key": event_key,
    }


def _trend_signal(observed, interpretation, confidence="MEDIUM", evidence=None):
    return {"observed": observed, "interpretation": interpretation, "confidence": confidence,
            "evidence": evidence or []}


def _producer_insight(what_is_moving, confidence="MEDIUM"):
    return {
        "what_is_moving": what_is_moving, "why_it_matters": "왜 중요한가 텍스트",
        "what_to_watch": "지켜볼 점 텍스트", "what_could_i_make_now": "시도해볼 것 텍스트",
        "confidence": confidence, "evidence": [],
    }


def _empty_dashboard():
    return {
        "report_date_kst": "2026-08-13",
        "news": {
            "AI": _news(), "ECONOMY": _news(), "SOCIETY": _news(),
            "TIKTOK": _news(), "SPOTIFY": _news(),
        },
        "tiktok_chart": {"state": "UNAVAILABLE", "top10": [], "new_entries": [], "trend": None},
        "spotify_chart": {"state": "UNAVAILABLE", "top10": [], "new_entries": [], "trend": None, "is_first_observation": False},
        "intelligence": {
            "early_signal": {"apple_music": [], "spotify_chart": []},
            "catalog_revival": {"apple_music": [], "spotify_chart": []},
            "cross_platform": [],
            "cross_platform_state": "INSUFFICIENT_HISTORY",
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
        "music_today": [],
        "today_music_intelligence": [],
    }


def _spotify_entry(rank, artist="Artist", title="Title", is_new=False, rank_delta=0,
                    peak_rank=None, days_on_chart=1, region="GLOBAL", observed_at="2026-08-13T00:00:00+00:00",
                    status=None):
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


def _trend(top10, is_first_observation=False):
    if is_first_observation:
        return {"new_count": 0, "up_count": 0, "down_count": 0, "first_observation_count": len(top10), "volatility": "LOW"}
    new_count = sum(1 for e in top10 if e["is_new"])
    up_count = sum(1 for e in top10 if not e["is_new"] and e["rank_delta"] > 0)
    down_count = sum(1 for e in top10 if not e["is_new"] and e["rank_delta"] < 0)
    total = new_count + up_count + down_count
    volatility = "HIGH" if total >= 6 else "MEDIUM" if total >= 3 else "LOW"
    return {"new_count": new_count, "up_count": up_count, "down_count": down_count, "volatility": volatility}


def _section(html_out, section_id):
    marker = f'id="{section_id}"'
    marker_pos = html_out.index(marker)
    start = html_out.rindex("<section", 0, marker_pos)
    end = html_out.index("</section>", marker_pos)
    return html_out[start:end]


def _primary_and_overflow(section, card_class):
    """Splits a section's HTML at its <details class="more-disclosure">
    progressive-disclosure boundary (if any) and counts `card_class`
    occurrences on each side -- a plain substring count over the whole
    section would double-count real overflow cards nested inside
    <details>, since they're still within the same <section>...</section>
    span. Splits specifically on the "more-disclosure" class, NOT any
    <details> -- individual cards may also contain their own collapsed
    <details class="evidence-disclosure">, which must never be mistaken
    for the section-level overflow boundary."""
    marker = '<details class="more-disclosure"'
    if marker in section:
        primary_html, overflow_html = section.split(marker, 1)
    else:
        primary_html, overflow_html = section, ""
    return primary_html.count(card_class), overflow_html.count(card_class)


def _today_signal(signal_type, is_strongest=False, headline_item=None, fact_text=None, meaning=None,
                   why_it_matters=None, watch_next=None, evidence_refs=None, evidence=None):
    return {
        "type": signal_type, "is_strongest": is_strongest, "headline_item": headline_item,
        "fact_text": fact_text, "meaning": meaning, "why_it_matters": why_it_matters, "watch_next": watch_next,
        "_evidence_refs": set(evidence_refs) if evidence_refs else None,
        # Real evidence citations in FULL (ref + real summary TEXT) --
        # only needed when this signal has no headline_item and must
        # resolve its own real event_key via title-matching (see report.
        # web_render_v2._resolve_entry_event_key).
        "_evidence": evidence,
    }


def _music_candidate(ctype, mode="FACT", headline_item=None, fact_text=None, why_it_matters=None,
                      producer_implication=None, source_url=None):
    return {
        "type": ctype, "mode": mode, "headline_item": headline_item, "fact_text": fact_text,
        "why_it_matters": why_it_matters, "producer_implication": producer_implication, "source_url": source_url,
    }


# ---- TODAY'S MUSIC INTELLIGENCE: newsletter LEAD STORY + <=3 secondary,
# MUSIC ONLY (NEWSLETTER LEAD REDESIGN) ----


def _today_intel_section(html_out):
    """The hero lives in a <div id="today-intel"> between the pub-nav and
    <main class="main"> -- not a <section>, so the _section() helper (which
    looks for a wrapping <section ...>) does not apply here."""
    return html_out[html_out.index('id="today-intel"'):html_out.index('<main class="main">')]


def test_today_music_intelligence_renders_one_lead_plus_max_three_secondary_music_only():
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("헤드라인1")),
        _today_signal("VIRAL_HOT", fact_text="음악 시그널1"),
        _today_signal("GENRE_SIGNAL", fact_text="음악 시그널2"),
        _today_signal("KPOP_AR", fact_text="음악 시그널3"),
        _today_signal("PRODUCER_INSIGHT", fact_text="음악 시그널4"),  # 4th secondary candidate, must be dropped
    ]
    html_out = render_dashboard_html_v2(data)
    assert html_out.count('class="lead-story"') == 1
    assert html_out.count('class="signal-card"') == 3
    assert "음악 시그널4" not in html_out


def test_today_music_intelligence_never_mixes_in_ai_economy_society():
    """CRITICAL: the hero must never show AI/ECONOMY/SOCIETY content --
    only real MUSIC signals belong here (category-contiguous IA)."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("음악 헤드라인")),
        _today_signal("VIRAL_HOT", fact_text="차트 시그널"),
    ]
    data["news"]["AI"] = _news("NORMAL", [_news_item("AI 헤드라인 절대 노출 금지")])
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "AI 헤드라인 절대 노출 금지" not in section
    assert "<h1>오늘의 뮤직 인텔리전스</h1>" in section


def test_today_music_intelligence_empty_renders_nothing():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert 'id="today-intel"' not in html_out


def test_today_music_intelligence_first_signal_becomes_lead_when_none_marked_strongest():
    """LEAD FALLBACK: is_strongest is a real upstream curation signal, not
    a guarantee -- the page must never render an empty lead while real
    music signals exist, so the first real signal safely becomes the lead
    when none of them has is_strongest=True."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", headline_item=_news_item("첫 신호가 리드가 된다")),
        _today_signal("VIRAL_HOT", fact_text="음악 시그널1"),
    ]
    html_out = render_dashboard_html_v2(data)
    assert html_out.count('class="lead-story"') == 1
    section = _today_intel_section(html_out)
    lead_pos = section.index('class="lead-story"')
    assert "첫 신호가 리드가 된다" in section[lead_pos:section.index("</article>")]
    assert html_out.count('class="signal-card"') == 1  # the remaining real signal, not dropped


def test_today_music_intelligence_strongest_signal_shows_why_and_watch_only_when_real():
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("PRODUCER_INSIGHT", is_strongest=True, headline_item=_news_item("실제 헤드라인"),
                      why_it_matters="실제 왜 중요한가", watch_next="실제 프로듀서 시사점"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "실제 왜 중요한가" in section
    assert "실제 프로듀서 시사점" in section
    assert "왜 중요한가" in section


def test_today_music_intelligence_never_fabricates_why_or_watch_when_absent():
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("실제 헤드라인")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "lead-why-row" not in section  # no WHY/WATCH row emitted at all when both are None


def test_today_music_intelligence_headline_is_real_clickable_link_when_source_url_exists():
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("클릭 가능한 헤드라인", source_url="https://example.com/lead-article")),
        _today_signal("VIRAL_HOT",
                      headline_item=_news_item("보조 신호 헤드라인", source_url="https://example.com/secondary-article")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    # lead: headline-link + meta "원문 보기" link; secondary: headline-link only
    assert section.count('target="_blank"') == 3
    assert section.count('rel="noopener noreferrer"') == 3
    assert 'href="https://example.com/lead-article"' in section
    assert 'href="https://example.com/secondary-article"' in section


def test_today_music_intelligence_analysis_only_signals_never_fake_clickable():
    """Synthesis/analysis-only candidates (fact_text, no real headline_item
    / source_url) must render as plain text -- never wrapped in an <a>."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="분석 전용 리드 텍스트"),
        _today_signal("KPOP_AR", fact_text="분석 전용 보조 텍스트"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "분석 전용 리드 텍스트" in section
    assert "분석 전용 보조 텍스트" in section
    assert "headline-link" not in section
    assert "<a " not in section


def test_today_music_intelligence_secondary_meaning_suppressed_when_it_duplicates_headline():
    data = _empty_dashboard()
    dup_item = _news_item("동일한 텍스트")
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", headline_item=dup_item, meaning="동일한 텍스트"),
        _today_signal("GENRE_SIGNAL", headline_item=_news_item("다른 헤드라인"), meaning="정말 다른 의미 설명"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert section.count("동일한 텍스트") == 1  # headline renders once, duplicate meaning line suppressed
    assert "정말 다른 의미 설명" in section  # genuinely different meaning still renders


# ---- MUSIC EDITORIAL IMAGERY: restrained, trust-gated images in the top
# MUSIC newsletter hero only -- LEAD STORY (<=1) + TODAY IN MUSIC (<=3) ----


def test_lead_story_renders_real_image_when_valid_image_url_exists():
    """A. a real lead article image renders when a valid image URL
    exists."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("리드 헤드라인", image_url="https://cdn.example.com/lead.jpg")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert 'class="lead-image-wrap"' in section
    assert 'src="https://cdn.example.com/lead.jpg"' in section
    assert 'class="lead-image"' in section
    assert 'loading="eager"' in section


def test_lead_story_with_no_image_remains_valid_and_has_no_placeholder():
    """B. lead with no image remains valid and does not render a
    placeholder."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("이미지 없는 리드")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert html_out.count('class="lead-story"') == 1
    assert "이미지 없는 리드" in section
    assert "lead-image" not in section
    assert "placeholder" not in section.lower()


def test_lead_story_analysis_only_never_receives_an_image():
    """C. analysis-only lead (no real headline_item) never receives an
    image, even if a same-shaped dict elsewhere in the payload has one."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="분석 전용 리드 텍스트"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "분석 전용 리드 텍스트" in section
    assert "lead-image" not in section


def test_today_in_music_renders_real_thumbnails_when_available():
    """D. Today in Music renders real thumbnails when available."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조1", image_url="https://cdn.example.com/s1.jpg")),
        _today_signal("GENRE_SIGNAL", headline_item=_news_item("보조2", image_url="https://cdn.example.com/s2.jpg")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert section.count('class="signal-thumb"') == 2
    assert 'src="https://cdn.example.com/s1.jpg"' in section
    assert 'src="https://cdn.example.com/s2.jpg"' in section


def test_today_in_music_never_shows_more_than_three_secondary_thumbnails():
    """E. no more than 3 secondary thumbnails can appear, even when more
    than 3 real candidates all carry a valid image."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조1", image_url="https://cdn.example.com/s1.jpg")),
        _today_signal("GENRE_SIGNAL", headline_item=_news_item("보조2", image_url="https://cdn.example.com/s2.jpg")),
        _today_signal("KPOP_AR", headline_item=_news_item("보조3", image_url="https://cdn.example.com/s3.jpg")),
        _today_signal("PRODUCER_INSIGHT", headline_item=_news_item("보조4", image_url="https://cdn.example.com/s4.jpg")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert section.count('class="signal-card"') == 3  # 4th secondary candidate dropped by the existing cap
    assert section.count('class="signal-thumb"') == 3
    assert "s4.jpg" not in section


def test_missing_image_url_renders_text_only():
    """F. missing image URL renders text-only, in both the lead and a
    secondary card."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드", image_url=None)),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조", image_url=None)),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "lead-image" not in section
    assert "signal-thumb" not in section


def test_invalid_or_non_http_image_values_are_rejected():
    """G. invalid/non-http image values are rejected -- relative paths,
    javascript:/data: URIs, and non-string values never render."""
    for bad_url in ("/relative/path.jpg", "javascript:alert(1)", "data:image/png;base64,xxx", ""):
        data = _empty_dashboard()
        data["today_music_intelligence"] = [
            _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드", image_url=bad_url)),
        ]
        html_out = render_dashboard_html_v2(data)
        section = _today_intel_section(html_out)
        assert "lead-image" not in section, f"rejected for {bad_url!r} failed"


def test_secondary_thumbnails_use_lazy_loading_lead_image_does_not():
    """H. secondary thumbnails use loading="lazy"; the lead image does not
    (first-screen critical -- lazy-loading it would hurt, not help)."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("리드", image_url="https://cdn.example.com/lead.jpg")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조", image_url="https://cdn.example.com/s1.jpg")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    lead_img_start = section.index('<img class="lead-image"')
    lead_img_tag = section[lead_img_start:section.index(">", lead_img_start)]
    assert 'loading="lazy"' not in lead_img_tag
    thumb_start = section.index('<img class="signal-thumb"')
    thumb_tag = section[thumb_start:section.index(">", thumb_start)]
    assert 'loading="lazy"' in thumb_tag


def test_image_alt_text_is_present_and_derived_from_real_article_title():
    """I. alt text is present and derived from the real article content
    (the Korean-first display title), for both the lead image and a
    secondary thumbnail."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("실제 기사 제목", image_url="https://cdn.example.com/lead.jpg")),
        _today_signal("VIRAL_HOT",
                      headline_item=_news_item("보조 기사 제목", image_url="https://cdn.example.com/s1.jpg")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert 'alt="실제 기사 제목"' in section
    assert 'alt="보조 기사 제목"' in section


def test_headline_link_contract_intact_with_images_present():
    """J. existing headline-link contract remains intact when an image is
    also present -- the headline itself (not the image) is the real link,
    and the image never blocks or substitutes for it."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("리드", source_url="https://example.com/lead-article",
                                                image_url="https://cdn.example.com/lead.jpg")),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert 'class="headline-link" href="https://example.com/lead-article" target="_blank" rel="noopener noreferrer"' in section
    # the image itself is a plain <img>, never wrapped in its own separate <a> to a different/unverified URL
    img_pos = section.index('class="lead-image"')
    assert "<a " not in section[max(0, img_pos - 60):img_pos]


# ---- MUSIC TODAY: real candidates only, never padded ----


def test_music_today_renders_real_candidates_with_fact_and_analysis_modes():
    data = _empty_dashboard()
    data["music_today"] = [
        _music_candidate("VIRAL_HOT", mode="FACT", fact_text="Artist - Track — 실제 상승폭 사실"),
        _music_candidate("GENRE_SIGNAL", mode="ANALYSIS", fact_text="관찰된 사실",
                          why_it_matters="실제 해석"),
        _music_candidate("PRODUCER_INSIGHT", mode="ANALYSIS", fact_text="관찰",
                          why_it_matters="왜 중요", producer_implication="프로듀서 시사점 텍스트"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-MUSICTODAY")
    assert "Artist - Track — 실제 상승폭 사실" in section
    assert 'mode-FACT' in section
    assert 'mode-ANALYSIS' in section
    assert "실제 해석" in section
    assert "프로듀서 시사점 텍스트" in section
    assert "block-quiet" not in section


def test_music_today_empty_shows_honest_message_and_is_quiet():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-MUSICTODAY")
    assert "block-quiet" in section
    assert "오늘은 근거가 충분한 음악 시그널이 없습니다" in section


def test_music_today_industry_news_uses_korean_first_display_title():
    data = _empty_dashboard()
    item = _news_item("English Original Title", translation_status="TRANSLATED", ko_title="한국어 제목")
    data["music_today"] = [_music_candidate("INDUSTRY_NEWS", mode="ANALYSIS", headline_item=item, why_it_matters="이유")]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-MUSICTODAY")
    assert "한국어 제목" in section
    assert "English Original Title" not in section


# ---- Chart Pulse: merged TOP10 + badges, TikTok folded into one quiet line ----


def test_chart_pulse_shows_top10_with_movement_badges():
    data = _empty_dashboard()
    entries = [
        _spotify_entry(1, artist="A", title="T1", is_new=True),
        _spotify_entry(2, artist="B", title="T2", rank_delta=3),
        _spotify_entry(3, artist="C", title="T3", rank_delta=-2),
    ]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [entries[0]],
                              "trend": _trend(entries), "is_first_observation": False}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "A - T1" in section and "badge-new" in section
    assert "B - T2" in section and "▲3" in section
    assert "C - T3" in section and "▼2" in section
    assert "TikTok" in section  # folded quiet status line, not its own section


def test_chart_pulse_never_repeats_top10_a_third_time_on_first_observation_day():
    data = _empty_dashboard()
    entries = [_spotify_entry(i, artist=f"A{i}", title=f"T{i}", is_new=True, status="FIRST_OBSERVED") for i in range(1, 4)]
    data["spotify_chart"] = {"state": "NORMAL", "top10": entries, "new_entries": [],
                              "trend": _trend(entries, is_first_observation=True), "is_first_observation": True}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    # each real track appears exactly once (the TOP10 row) -- never a second
    # verbatim restatement of the same first-observation baseline list
    for i in range(1, 4):
        assert section.count(f"A{i} - T{i}") == 1
    assert "첫 관측 (기준선 생성)" in section


def test_chart_pulse_unavailable_state_shows_honest_message_and_tiktok_line():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-CHARTPULSE")
    assert "Spotify 차트 데이터가 아직 수집되지 않았습니다" in section
    assert "TikTok" in section


def test_chart_pulse_cross_platform_track_gets_cross_platform_badge():
    data = _empty_dashboard()
    entry = _spotify_entry(1, artist="X", title="Y", rank_delta=1)
    data["spotify_chart"] = {"state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry]), "is_first_observation": False}
    data["intelligence"]["cross_platform"] = [{"music_entity_id": 1, "canonical_artist": "X", "canonical_title": "Y", "sources": ["apple_music", "spotify_chart"], "source_details": []}]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "badge-cross" in section


# ---- Chart Pulse: REAL chart-date contract -- report_date_kst (SUPER
# NEWS publication date) and chart_date (the real Spotify source
# observation date) are NOT the same thing and must never be conflated ----


def test_chart_pulse_renders_real_chart_date_distinct_from_report_date():
    data = _empty_dashboard()
    data["report_date_kst"] = "2026-08-16"
    entry = _spotify_entry(1, artist="A", title="T1", rank_delta=1)
    data["spotify_chart"] = {
        "state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry]),
        "is_first_observation": False, "chart_date": "2026-08-15T00:00:00+00:00",
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "2026.08.15 기준" in section  # the REAL chart date
    assert "2026.08.16 기준" not in section  # report_date never silently substituted for chart_date


def test_chart_pulse_missing_chart_date_shows_truthful_unavailable_state():
    data = _empty_dashboard()
    entry = _spotify_entry(1, artist="A", title="T1", rank_delta=1)
    data["spotify_chart"] = {
        "state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry]),
        "is_first_observation": False, "chart_date": None,
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "기준일 확인 필요" in section
    assert "2026.08.13 기준" not in section  # report_date_kst never used as a fabricated stand-in


def test_chart_pulse_first_observation_narrative_uses_real_chart_date():
    data = _empty_dashboard()
    entries = [_spotify_entry(i, artist=f"A{i}", title=f"T{i}", is_new=True, status="FIRST_OBSERVED") for i in range(1, 3)]
    data["spotify_chart"] = {
        "state": "NORMAL", "top10": entries, "new_entries": [],
        "trend": _trend(entries, is_first_observation=True), "is_first_observation": True,
        "chart_date": "2026-08-15T00:00:00+00:00",
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "2026년 8월 15일 Spotify Global Daily Chart 첫 관측입니다." in section
    assert "비교 가능한 이전 관측 데이터가 없어 이날을 기준선으로 설정합니다." in section
    assert "다음 관측부터 순위 변동(Δ)을 표시합니다." in section


def test_chart_pulse_first_observation_narrative_never_fabricates_date_when_missing():
    data = _empty_dashboard()
    entries = [_spotify_entry(1, artist="A1", title="T1", is_new=True, status="FIRST_OBSERVED")]
    data["spotify_chart"] = {
        "state": "NORMAL", "top10": entries, "new_entries": [],
        "trend": _trend(entries, is_first_observation=True), "is_first_observation": True,
        "chart_date": None,
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "Spotify Global Daily Chart 첫 관측입니다 (기준일 확인 필요)." in section
    narrative_start = section.index('class="pulse-narrative"')
    narrative = section[narrative_start:section.index("</p>", narrative_start)]
    assert "년" not in narrative  # no fabricated YYYY년 M월 D일 inserted when chart_date is unavailable


# ---- Music Industry: Korean-first, capped with progressive disclosure ----


def test_music_industry_merges_spotify_and_tiktok_and_caps_primary_at_ten():
    data = _empty_dashboard()
    spotify_items = [_news_item(f"Spotify Item {i}", source_count=2) for i in range(7)]
    tiktok_items = [_news_item(f"TikTok Item {i}", source_count=2) for i in range(7)]
    data["news"]["SPOTIFY"] = _news("NORMAL", spotify_items)
    data["news"]["TIKTOK"] = _news("NORMAL", tiktok_items)
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    primary, overflow = _primary_and_overflow(section, 'class="news-card')
    assert primary == 10
    assert overflow == 4  # 14 total - 10 primary = 4 real overflow, never dropped
    assert "더 보기 (4)" in section


def test_music_industry_quality_floor_excludes_downranked_items_even_from_overflow():
    """MUSIC INDUSTRY AGGRESSIVE NOISE CUT: a real DOWNRANKED item (estate
    dispute / minor crime / gossip) never appears in Music Industry at
    all -- not primary, not hidden inside "더 보기" either."""
    data = _empty_dashboard()
    real_item = _news_item("Label signs new licensing deal")
    tabloid_item = _news_item("가수 유산 분쟁, 상속 재판 시작")
    data["news"]["SPOTIFY"] = _news("NORMAL", [real_item, tabloid_item])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert "Label signs new licensing deal" in section
    assert "유산 분쟁" not in section


def test_music_industry_quiet_when_no_real_items():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-INDUSTRY")
    assert "block-quiet" in section


def test_music_industry_no_uninterpreted_pipeline_status_notice_anywhere():
    """Internal pipeline status ('AI 해석 대기') is not primary user content
    -- it must never appear anywhere in the rendered page, regardless of
    which category's data came from the raw/UNINTERPRETED fallback path."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("UNINTERPRETED", [_news_item("Real fallback item", source_count=2)])
    data["news"]["AI"] = _news("UNINTERPRETED", [_news_item("Real AI fallback item")])
    html_out = render_dashboard_html_v2(data)
    assert "해석 대기" not in html_out
    assert "Real fallback item" in html_out or "실제" in html_out  # the real item itself still renders


# ---- Spotify Watch: permanent required watch layer, compact, honest empty state ----


def test_spotify_watch_shows_honest_empty_state_when_no_qualifying_item():
    data = _empty_dashboard()
    data["spotify_watch_candidates"] = []
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "block-quiet" in section
    assert "오늘 확인된 중대한 Spotify 정책·비즈니스 변화 없음" in section


def test_spotify_watch_ignores_ordinary_promotional_item():
    """An item with no real priority-class keyword (ordinary promotion)
    must not qualify -- Spotify Watch still shows the honest empty
    state, never filler."""
    data = _empty_dashboard()
    data["spotify_watch_candidates"] = [
        _news_item("Spotify가 새로운 플레이리스트 커버아트를 공개했다", event_key="ev-1"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "block-quiet" in section


def test_spotify_watch_shows_real_high_priority_item_with_enrichment():
    data = _empty_dashboard()
    data["spotify_watch_candidates"] = [
        _news_item(
            "Spotify announces new licensing agreement for AI covers",
            source_url="https://example.com/a", event_key="ev-1",
        ),
    ]
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("스포티파이 라이선스 변화"),
            "evidence": [{"ref": "E1", "summary": "Spotify announces new licensing agreement for AI covers"}],
        }],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "block-quiet" not in section
    assert "Spotify announces new licensing agreement for AI covers" in section
    assert "<b>왜 중요한가</b>" in section and "왜 중요한가 텍스트" in section
    assert "<b>프로듀서 시사점</b>" in section and "시도해볼 것 텍스트" in section  # MEDIUM confidence: TRY shown


def test_spotify_watch_excludes_item_already_shown_as_lead():
    """EDITORIAL INTEGRITY FIX: when today's real qualifying Spotify move
    already IS the Lead, Spotify Watch must say so explicitly -- never the
    "no major Spotify change" message, which would be semantically false
    (a real move exists, it's just shown elsewhere on the same page)."""
    data = _empty_dashboard()
    lead_item = _news_item("Spotify announces new licensing agreement", event_key="ev-shared")
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=lead_item),
    ]
    data["spotify_watch_candidates"] = [
        _news_item("Spotify announces new licensing agreement (다른 매체)", event_key="ev-shared"),
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "block-quiet" in section  # only candidate is the lead's own event -- nothing distinct left
    assert "오늘의 주요 Spotify 변화는 Lead Story에서 다룹니다" in section
    assert "오늘 확인된 중대한 Spotify 정책·비즈니스 변화 없음" not in section


def test_spotify_watch_low_confidence_shows_watch_only_not_try():
    data = _empty_dashboard()
    data["spotify_watch_candidates"] = [
        _news_item("Spotify royalty policy update", source_url="https://example.com/a", event_key="ev-1"),
    ]
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("로열티 정책 변화", confidence="LOW"),
            "evidence": [{"ref": "E1", "summary": "Spotify royalty policy update"}],
        }],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "지켜볼 점 텍스트" in section
    assert "시도해볼 것 텍스트" not in section  # what_could_i_make_now suppressed at LOW confidence


# ---- Producer/A&R inference-distance control: LOW confidence never gets a prescriptive TRY ----


def test_producer_takeaway_low_confidence_shows_watch_only():
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [_producer_insight("낮은 신뢰도 인사이트", confidence="LOW")]}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "<b>지켜볼 점</b>" in section
    assert "<b>시도 · 지켜볼 점</b>" not in section
    assert "지켜볼 점 텍스트" in section
    assert "시도해볼 것 텍스트" not in section


def test_producer_takeaway_medium_confidence_keeps_try_and_watch():
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [_producer_insight("보통 신뢰도 인사이트", confidence="MEDIUM")]}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "<b>시도 · 지켜볼 점</b>" in section
    assert "시도해볼 것 텍스트" in section


def test_producer_takeaway_suppresses_cached_newsletter_advice():
    """FINAL 90+ QUALITY CORRECTION PASS: an already-cached insight (from
    before report.producer_synthesis's prompt fix) recommending a
    newsletter/explainer must never render as producer advice, even at
    MEDIUM/HIGH confidence -- falls back to WATCH-only instead."""
    data = _empty_dashboard()
    insight = {
        "what_is_moving": "실제 관측된 사실", "why_it_matters": "왜 중요한가 텍스트",
        "what_to_watch": "지켜볼 점 텍스트",
        "what_could_i_make_now": "이 이슈를 다루는 짧은 뉴스레터 섹션을 바로 만들 수 있다",
        "confidence": "HIGH", "evidence": [],
    }
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [insight]}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "뉴스레터" not in section


def test_producer_takeaway_suppresses_cached_analysis_memo_advice():
    """Confirmed real leak (2026-08-17): a cached what_could_i_make_now
    recommending a short analysis memo must never render as producer
    advice -- falls back to WATCH-only, same as the newsletter-advice
    guard above."""
    data = _empty_dashboard()
    insight = {
        "what_is_moving": "실제 관측된 사실", "why_it_matters": "왜 중요한가 텍스트",
        "what_to_watch": "지켜볼 점 텍스트",
        "what_could_i_make_now": "TikTok의 역할 축소가 마케팅에 미치는 영향을 짚는 짧은 분석 메모를 작성할 수 있다",
        "confidence": "HIGH", "evidence": [],
    }
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [insight]}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "분석 메모" not in section
    assert "지켜볼 점 텍스트" in section
    assert "<b>지켜볼 점</b>" in section
    assert "지켜볼 점 텍스트" in section


def test_producer_quality_cap_drops_low_confidence_insight_when_stronger_ones_exist():
    """PRODUCER/A&R QUALITY CAP (EDITORIAL INTEGRITY FIX), confirmed real
    defect: a real LOW-confidence insight (e.g. the John Summit
    stadium-scale inference) must not survive merely to pad the count
    when genuinely stronger real insights already exist that day -- a
    quality ceiling, not a quota."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [
            _producer_insight("강한 신뢰도 인사이트", confidence="HIGH"),
            _producer_insight("약한 신뢰도 인사이트", confidence="LOW"),
        ],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "강한 신뢰도 인사이트" in section
    assert "약한 신뢰도 인사이트" not in section


def test_producer_quality_cap_keeps_low_confidence_insight_when_nothing_stronger_exists():
    """Regression guard: a real LOW-confidence insight is never dropped
    down to an empty section just because it's the only real content
    that day -- the cap only removes LOW items when a stronger real
    insight already exists to replace them."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [_producer_insight("유일한 낮은 신뢰도 인사이트", confidence="LOW")],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "유일한 낮은 신뢰도 인사이트" in section


# ---- FACT/OBSERVATION/SIGNAL/TREND evidence discipline: real evidence-count-based labeling ----


def test_genre_radar_labels_single_evidence_as_observation_not_signal():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("관찰 사실", "해석", evidence=[{"ref": "E1", "summary": "s"}])],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-GENRE")
    assert 'evidence-level-observation">관측<' in section
    assert "시그널" not in section


def test_genre_radar_labels_multiple_evidence_as_signal():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal(
            "관찰 사실", "해석", evidence=[{"ref": "E1", "summary": "s1"}, {"ref": "E2", "summary": "s2"}],
        )],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-GENRE")
    assert 'evidence-level-signal">시그널<' in section


# ---- Genre / Production Radar: honest "오늘 관측" labeling, never fabricated trend direction ----


def test_genre_radar_labels_each_item_today_observation_not_a_fabricated_trend():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("실제 관찰 사실", "실제 해석", confidence="HIGH")],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-GENRE")
    assert "오늘 관측" in section
    assert "실제 관찰 사실" in section
    assert "실제 해석" in section
    assert "신뢰도 높음" in section
    # no fabricated directional arrow semantics anywhere in this section
    assert "↑" not in section and "↓" not in section


def test_production_radar_quiet_when_unavailable():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-PRODUCTION")
    assert "block-quiet" in section


# ---- Producer / A&R Takeaways: real insights + references + kpop, capped at 3 combined ----


def test_producer_takeaways_combines_and_caps_at_three_with_overflow():
    data = _empty_dashboard()
    data["producer_intelligence"] = {"state": "NORMAL", "insights": [_producer_insight(f"인사이트{i}") for i in range(4)]}
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "production_notes": [],
        "producer_references": [_trend_signal("레퍼런스1", "해석1")],
        "kpop_ar_notes": [_trend_signal("K팝1", "해석K1"), _trend_signal("K팝2", "해석K2")],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert section.count('class="takeaway-card') == 7  # 4 + 1 + 2 real cards total
    assert "더 보기 (4)" in section  # 7 total - 3 primary cap = 4 real overflow


def test_producer_takeaways_quiet_when_all_three_sources_empty():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-PRODUCER")
    assert "block-quiet" in section
    assert "오늘은 근거가 충분하지 않아" in section


def test_producer_reference_and_kpop_cards_render_with_their_own_labels():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "production_notes": [],
        "producer_references": [_trend_signal("프로듀서 X가 참여", "해석")],
        "kpop_ar_notes": [_trend_signal("K팝 그룹 Y", "해석2")],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "프로듀서 레퍼런스" in section
    assert "K-pop / A&amp;R" in section  # static label is HTML-escaped like any other text, correctly
    assert "프로듀서 X가 참여" in section
    assert "K팝 그룹 Y" in section


def test_producer_section_suppresses_kpop_note_that_exactly_duplicates_a_producer_insight():
    """PRODUCER/A&R FINAL QUALITY (confirmed real defect found in actual
    generated-report QA): a K-pop/A&R note whose real evidence resolves to
    the SAME real event_key as an already-shown Producer insight is a
    literal duplicate (zero incremental value) and must be suppressed --
    even though neither of them is today's Lead Story, and even though
    producer_intelligence/music_trend_intelligence each assign their OWN
    independent ref labels (so a raw ref/summary comparison would miss
    this -- only the real event_key bridges the two catalogs)."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("동일 사건 인사이트"),
            "evidence": [{"ref": "E1", "summary": "다른 요약1", "event_key": "ev-shared"}],
        }],
    }
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "production_notes": [], "producer_references": [],
        "kpop_ar_notes": [_trend_signal(
            "동일 사건 K팝 노트", "해석",
            evidence=[{"ref": "E7", "summary": "다른 요약2", "event_key": "ev-shared"}],
        )],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "동일 사건 인사이트" in section
    assert "동일 사건 K팝 노트" not in section


def test_producer_section_suppresses_narrow_entry_fully_covered_by_a_broader_earlier_one():
    """A narrower single-event entry whose one real event is already
    fully covered by an earlier, broader multi-event Producer insight is
    a literal duplicate (adds nothing new) and is suppressed."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("폭넓은 종합 인사이트"),
            "evidence": [
                {"ref": "E1", "summary": "s1", "event_key": "ev-shared"},
                {"ref": "E9", "summary": "s9", "event_key": "ev-new"},
            ],
        }],
    }
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "production_notes": [], "producer_references": [],
        "kpop_ar_notes": [_trend_signal(
            "좁은 K팝 노트", "해석", evidence=[{"ref": "E1", "summary": "s1", "event_key": "ev-shared"}],
        )],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "폭넓은 종합 인사이트" in section
    assert "좁은 K팝 노트" not in section  # fully covered by the already-shown ev-shared event: a literal duplicate


def test_producer_section_keeps_broader_synthesis_that_only_partially_overlaps():
    """Regression guard (DISTINCT INTELLIGENCE EXCEPTION): an entry that
    touches ONE already-shown real event but also introduces a genuinely
    NEW one is NOT suppressed -- only full subset coverage (every real
    event it resolves to is already shown) counts as a literal
    duplicate."""
    data = _empty_dashboard()
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("좁은 인사이트"),
            "evidence": [{"ref": "E1", "summary": "s1", "event_key": "ev-shared"}],
        }],
    }
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "production_notes": [], "producer_references": [],
        "kpop_ar_notes": [_trend_signal(
            "폭넓은 K팝 노트", "해석",
            evidence=[
                {"ref": "E1", "summary": "s1", "event_key": "ev-shared"},
                {"ref": "E9", "summary": "s9", "event_key": "ev-new"},
            ],
        )],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "좁은 인사이트" in section
    assert "폭넓은 K팝 노트" in section  # introduces ev-new: genuinely distinct, kept despite partial overlap


# ---- MUSIC EVENT EXPOSURE BUDGET: the real event that becomes today's
# LEAD STORY must not ALSO occupy Music Industry/Genre Radar/Production
# Radar/Producer as an ordinary, zero-new-information duplicate
# (SUPER_NEWS_SPEC.md section 9) ----


def test_lead_event_suppressed_from_music_industry_multiple_outlets_not_multiple_exposures():
    """D. the same real event, even reported by a different outlet
    (different title/source), does not occupy a second PRIMARY exposure
    in Music Industry once it's already the LEAD STORY."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("Label Signs Landmark Licensing Deal",
                                                ko_title="레이블, 대형 라이선싱 계약 체결", translation_status="TRANSLATED",
                                                event_key="ev-lic-1")),
    ]
    data["news"]["SPOTIFY"] = _news("NORMAL", [
        _news_item("Music Label's New Licensing Deal, Outlet B Reports", event_key="ev-lic-1"),
        _news_item("A Genuinely Different Real Story", ko_title="완전히 다른 실제 기사",
                   translation_status="TRANSLATED", event_key="ev-other", source_count=2),
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert "Music Label's New Licensing Deal, Outlet B Reports" not in section
    assert "완전히 다른 실제 기사" in section


def test_lead_event_suppression_uses_deterministic_event_key_not_text_similarity():
    """E. a translated/paraphrased headline of the exact same real event
    cannot bypass suppression -- the real event_key is the only identity
    used, never a title/text heuristic."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("Original English Headline About The Deal",
                                                ko_title="해당 사건에 대한 원문 영어 헤드라인", translation_status="TRANSLATED",
                                                event_key="ev-translate-1")),
    ]
    data["news"]["SPOTIFY"] = _news("NORMAL", [
        _news_item("완전히 다른 번역/의역된 텍스트로 보이는 동일 사건 보도", event_key="ev-translate-1"),
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert "완전히 다른 번역/의역된 텍스트로 보이는 동일 사건 보도" not in section
    assert section.count('class="news-card') == 0


def test_lead_event_ordinary_duplicate_suppressed_industry_state_stays_honest():
    """F. when Music Industry's only real item duplicates the lead's own
    event, the duplicate is suppressed (never shown twice) -- and the
    section's real coverage state is never falsely downgraded to "no news
    today" just because its one real item became the lead (state is
    derived from the real UNFILTERED news lists, independent of this
    display-level suppression)."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("오늘의 유일한 실제 기사", event_key="ev-solo")),
    ]
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("오늘의 유일한 실제 기사 (다른 매체)", event_key="ev-solo")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert section.count('class="news-card') == 0
    assert "block-quiet" not in section


def test_distinct_producer_interpretation_of_same_event_survives():
    """G. DISTINCT INTELLIGENCE EXCEPTION: a genuinely different real
    Producer/A&R interpretation (citing different real evidence than the
    lead) survives, even though the lead itself is also a MUSIC synthesis
    signal."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="장르 관찰 사실",
                      why_it_matters="장르 해석", evidence_refs={"E1"}),
    ]
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{**_producer_insight("완전히 다른 프로듀서 인사이트"), "evidence": [{"ref": "E2", "summary": "다른 실제 근거"}]}],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCER")
    assert "완전히 다른 프로듀서 인사이트" in section


def test_same_event_evidence_suppressed_from_radar_and_producer_sections_when_it_is_the_lead():
    """H. the same real event cannot occupy 4+ major MUSIC exposures --
    Genre Radar/Production Radar/Producer entries citing the SAME real
    evidence as the lead are suppressed there (never independently
    re-shown), collapsing what would otherwise be Lead + Genre +
    Production + Producer down to the lead alone."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="동일 사건 관찰",
                      why_it_matters="동일 사건 해석", evidence_refs={"E1"}),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("동일 사건 관찰", "동일 사건 해석", evidence=[{"ref": "E1", "summary": "s"}])],
        "production_notes": [_trend_signal("동일 사건, 프로덕션 관점", "동일 사건 해석2", evidence=[{"ref": "E1", "summary": "s"}])],
        "producer_references": [], "kpop_ar_notes": [],
    }
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{**_producer_insight("동일 사건, 프로듀서 관점"), "evidence": [{"ref": "E1", "summary": "s"}]}],
    }
    html_out = render_dashboard_html_v2(data)
    assert "block-quiet" in _section(html_out, "section-GENRE")
    assert "block-quiet" in _section(html_out, "section-PRODUCTION")
    assert "동일 사건, 프로듀서 관점" not in _section(html_out, "section-PRODUCER")
    assert "동일 사건 관찰" in _today_intel_section(html_out)  # the lead itself still shows the real story once


def test_unrelated_events_remain_unaffected_by_lead_suppression():
    """I. unrelated real events are completely unaffected by the lead's
    own suppression -- distinct event_key/evidence renders normally."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 기사", event_key="ev-lead")),
    ]
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("완전히 다른 실제 기사", event_key="ev-unrelated", source_count=2)])
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("관련 없는 장르 관찰", "관련 없는 해석", evidence=[{"ref": "E9", "summary": "s"}])],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    assert "완전히 다른 실제 기사" in _section(html_out, "section-INDUSTRY")
    assert "관련 없는 장르 관찰" in _section(html_out, "section-GENRE")


def test_lead_suppression_does_not_break_chart_pulse_image_or_link_contracts():
    """J. Chart Pulse's real chart_date, the lead image, and the real
    headline link all remain intact while MUSIC EVENT EXPOSURE BUDGET
    suppression is simultaneously active."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("리드 기사", source_url="https://example.com/lead",
                                                image_url="https://cdn.example.com/lead.jpg", event_key="ev-lead")),
    ]
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("리드 기사 (다른 매체)", event_key="ev-lead")])
    entry = _spotify_entry(1, artist="A", title="T1", rank_delta=1)
    data["spotify_chart"] = {
        "state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry]),
        "is_first_observation": False, "chart_date": "2026-08-15T00:00:00+00:00",
    }
    html_out = render_dashboard_html_v2(data)
    lead_section = _today_intel_section(html_out)
    assert 'src="https://cdn.example.com/lead.jpg"' in lead_section
    assert 'href="https://example.com/lead"' in lead_section
    assert "2026.08.15 기준" in _section(html_out, "section-CHARTPULSE")
    assert "리드 기사 (다른 매체)" not in _section(html_out, "section-INDUSTRY")


# ---- TRUE MUSIC EVENT-LEVEL EXPOSURE BUDGET (corrective pass): closes
# the real gap where "different evidence_refs" alone was incorrectly
# treated as proof of a different real event -- now resolves evidence ref
# -> real article -> real event_key wherever structured data allows it,
# and enforces a real hard cap of 2 total visible exposures (Lead + at
# most 1 further genuinely distinct real interpretation) ----


def _multi_outlet_news_items():
    """4 real items reporting the SAME real event_key (as 4 different
    real outlets would), plus 1 real item on a completely unrelated real
    event_key."""
    return [
        _news_item("Outlet A Headline", ko_title="매체 A 헤드라인", translation_status="TRANSLATED", event_key="ev-shared"),
        _news_item("Outlet B Headline", event_key="ev-shared"),
        _news_item("Outlet C Headline", event_key="ev-shared"),
        _news_item("Outlet D Headline", event_key="ev-shared"),
        _news_item("Completely Unrelated Headline", event_key="ev-unrelated"),
    ]


def test_lead_genre_duplicate_citing_same_evidence_suppressed_adds_no_distinct_value():
    """A. a Genre Radar entry citing the SAME real evidence ref as the
    lead (the literal same real source) is suppressed -- citing the
    IDENTICAL real source the lead already shows adds no distinct real
    value, regardless of which real event_key it resolves to."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="리드 관찰", why_it_matters="리드 해석",
                      evidence_refs={"E1"}),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("동일 근거 인용", "동일 근거 해석", evidence=[{"ref": "E1", "summary": "s"}])],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    assert "block-quiet" in _section(html_out, "section-GENRE")
    assert "동일 근거 인용" not in html_out


def test_lead_plus_genre_plus_production_plus_producer_never_reach_four_exposures():
    """B. Lead EV1 + Genre EV1 + Production EV1 + Producer EV1 (each via
    a DIFFERENT real outlet/evidence ref) can never produce 4 visible
    major exposures -- only the Lead plus the FIRST real downstream match
    (Genre, per the fixed real editorial order Genre -> Production ->
    Producer) survive."""
    data = _empty_dashboard()
    items = _multi_outlet_news_items()
    data["news"]["SPOTIFY"] = _news("NORMAL", items)
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=items[0]),  # Outlet A, ev-shared
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("장르 관찰", "장르 해석", evidence=[{"ref": "E2", "summary": "Outlet B Headline"}])],
        "production_notes": [_trend_signal("프로덕션 관찰", "프로덕션 해석", evidence=[{"ref": "E3", "summary": "Outlet C Headline"}])],
        "producer_references": [], "kpop_ar_notes": [],
    }
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{**_producer_insight("프로듀서 관찰"), "evidence": [{"ref": "E4", "summary": "Outlet D Headline"}]}],
    }
    html_out = render_dashboard_html_v2(data)
    assert "장르 관찰" in _section(html_out, "section-GENRE")  # exposure 2 of 2: allowed
    assert "프로덕션 관찰" not in _section(html_out, "section-PRODUCTION")  # budget already spent
    assert "프로듀서 관찰" not in _section(html_out, "section-PRODUCER")  # budget already spent


def test_three_outlets_same_event_key_recognized_as_one_underlying_event():
    """C. the same real event, reported by evidence refs E1/E2/E3 (three
    different real outlets), all resolving to the SAME real event_key, is
    recognized as ONE real underlying event -- not three independent
    ones -- even though the refs themselves are entirely disjoint."""
    data = _empty_dashboard()
    items = _multi_outlet_news_items()
    data["news"]["SPOTIFY"] = _news("NORMAL", items)
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=items[0]),  # Outlet A (E1-equivalent)
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("E2 근거 관찰", "해석", evidence=[{"ref": "E2", "summary": "Outlet B Headline"}])],
        "production_notes": [_trend_signal("E3 근거 관찰", "해석", evidence=[{"ref": "E3", "summary": "Outlet C Headline"}])],
        "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    # both E2 and E3 resolve to the SAME real event_key as the lead's own
    # article -- recognized as ONE event, so only the first (Genre) is
    # allowed; the second (Production) is suppressed even though its
    # evidence ref (E3) is completely disjoint from both the lead's and
    # Genre's own (E2).
    assert "E2 근거 관찰" in _section(html_out, "section-GENRE")
    assert "E3 근거 관찰" not in _section(html_out, "section-PRODUCTION")


def test_at_most_one_distinct_interpretation_of_lead_event_survives():
    """D. exactly ONE genuinely distinct real interpretation of the
    lead's own event may survive -- never zero (when one is genuinely
    available) and never more than one."""
    data = _empty_dashboard()
    items = _multi_outlet_news_items()
    data["news"]["SPOTIFY"] = _news("NORMAL", items)
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=items[0]),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("장르 해석1", "해석1", evidence=[{"ref": "E2", "summary": "Outlet B Headline"}])],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{**_producer_insight("프로듀서 해석1"), "evidence": [{"ref": "E4", "summary": "Outlet D Headline"}]}],
    }
    html_out = render_dashboard_html_v2(data)
    survived = ("장르 해석1" in _section(html_out, "section-GENRE")) + ("프로듀서 해석1" in _section(html_out, "section-PRODUCER"))
    assert survived == 1


def test_third_same_event_interpretation_suppressed_even_with_fully_distinct_evidence():
    """E. a THIRD same-event interpretation is suppressed even when it
    cites real evidence that is completely disjoint from both the lead's
    own evidence AND the one already-kept second exposure's evidence."""
    data = _empty_dashboard()
    items = _multi_outlet_news_items()
    data["news"]["SPOTIFY"] = _news("NORMAL", items)
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=items[0]),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("첫 번째 해석", "해석", evidence=[{"ref": "E2", "summary": "Outlet B Headline"}])],
        "production_notes": [], "producer_references": [],
        "kpop_ar_notes": [_trend_signal("세 번째 해석", "해석", evidence=[{"ref": "E3", "summary": "Outlet C Headline"}])],
    }
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{**_producer_insight("두 번째 해석"), "evidence": [{"ref": "E4", "summary": "Outlet D Headline"}]}],
    }
    html_out = render_dashboard_html_v2(data)
    assert "첫 번째 해석" in _section(html_out, "section-GENRE")  # 1st downstream match: kept
    assert "두 번째 해석" not in _section(html_out, "section-PRODUCER")  # 2nd: suppressed
    assert "세 번째 해석" not in _section(html_out, "section-PRODUCER")  # 3rd (kpop_ar_notes): suppressed


def test_completely_unrelated_event_key_remains_unaffected():
    """F. a completely unrelated real event (a different real event_key
    entirely) is never touched by the lead's own exposure budget."""
    data = _empty_dashboard()
    items = _multi_outlet_news_items()
    data["news"]["SPOTIFY"] = _news("NORMAL", items)
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=items[0]),  # ev-shared
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [
            _trend_signal("공유 사건 해석", "해석", evidence=[{"ref": "E2", "summary": "Outlet B Headline"}]),  # ev-shared
        ],
        "production_notes": [
            _trend_signal("무관한 사건 관찰", "무관한 해석", evidence=[{"ref": "E5", "summary": "Completely Unrelated Headline"}]),
        ],
        "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    assert "공유 사건 해석" in _section(html_out, "section-GENRE")
    assert "무관한 사건 관찰" in _section(html_out, "section-PRODUCTION")  # unrelated event -- never suppressed


def test_true_event_budget_does_not_break_chart_pulse_image_or_link_contracts():
    """H. Chart Pulse's real chart_date, the lead image, and the real
    headline link all remain intact while the TRUE event-level exposure
    budget (spanning Today in Music/Music Industry/Genre/Production/
    Producer at once) is simultaneously active."""
    data = _empty_dashboard()
    items = _multi_outlet_news_items()
    data["news"]["SPOTIFY"] = _news("NORMAL", items)
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("리드 기사", source_url="https://example.com/lead",
                                                image_url="https://cdn.example.com/lead.jpg", event_key="ev-shared")),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [_trend_signal("장르 해석", "해석", evidence=[{"ref": "E2", "summary": "Outlet B Headline"}])],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    entry = _spotify_entry(1, artist="A", title="T1", rank_delta=1)
    data["spotify_chart"] = {
        "state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry]),
        "is_first_observation": False, "chart_date": "2026-08-15T00:00:00+00:00",
    }
    html_out = render_dashboard_html_v2(data)
    lead_section = _today_intel_section(html_out)
    assert 'src="https://cdn.example.com/lead.jpg"' in lead_section
    assert 'href="https://example.com/lead"' in lead_section
    assert "2026.08.15 기준" in _section(html_out, "section-CHARTPULSE")
    assert "Outlet A Headline" not in _section(html_out, "section-INDUSTRY")  # same event as lead: suppressed
    assert "장르 해석" in _section(html_out, "section-GENRE")  # genuinely distinct exposure: kept


# ---- SECOND CORRECTIVE PASS -- TRUE EVENT-LEVEL IDENTITY (not
# title-text matching): a synthesis evidence citation now carries the
# real event_key DIRECTLY (propagated from the originating real news item
# at catalog-build time -- see report.music_trend_synthesis.
# build_evidence_catalog / report.producer_synthesis.build_evidence_
# catalog), so resolution no longer depends on the evidence summary text
# matching the source article's title at all. Title matching remains
# ONLY as a last, backward-compatible fallback for legacy rows. ----


def test_paraphrased_evidence_summaries_still_obey_max_two_exposure_contract():
    """B. Lead EV1 + Genre EV1 + Production EV1 + Producer EV1, with ALL
    evidence summaries paraphrased and NOT matching any real article
    title, still obeys the max-2 exposure contract -- event_key is
    carried DIRECTLY on the evidence, so title matching never has to
    succeed for this to work."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="리드 관찰", why_it_matters="리드 해석",
                      evidence_refs={"E1"},
                      evidence=[{"ref": "E1", "summary": "전혀 다른 문장 A", "event_key": "ev-shared"}]),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [],
        "production_notes": [
            _trend_signal("프로덕션 관찰", "프로덕션 해석",
                          evidence=[{"ref": "E2", "summary": "전혀 다른 문장 B", "event_key": "ev-shared"}]),
        ],
        "producer_references": [], "kpop_ar_notes": [],
    }
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("프로듀서 관찰"),
            "evidence": [{"ref": "E3", "summary": "전혀 다른 문장 C", "event_key": "ev-shared"}],
        }],
    }
    html_out = render_dashboard_html_v2(data)
    assert "프로덕션 관찰" in _section(html_out, "section-PRODUCTION")  # first downstream match: kept
    assert "프로듀서 관찰" not in _section(html_out, "section-PRODUCER")  # budget already spent


def test_different_outlets_mapped_to_same_event_key_still_count_as_one_event():
    """C. two different real outlets/articles (different evidence refs,
    completely different paraphrased summaries) both carrying the SAME
    real event_key directly are still recognized as ONE real event."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("Outlet A Original Headline",
                                                ko_title="매체 A 원문 헤드라인", translation_status="TRANSLATED",
                                                event_key="ev-shared")),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [
            _trend_signal("완전히 다른 표현의 관찰1", "해석1",
                          evidence=[{"ref": "E2", "summary": "완전히 다른 요약 문장", "event_key": "ev-shared"}]),
        ],
        "production_notes": [
            _trend_signal("완전히 다른 표현의 관찰2", "해석2",
                          evidence=[{"ref": "E3", "summary": "또 다른 완전히 다른 요약 문장", "event_key": "ev-shared"}]),
        ],
        "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    assert "완전히 다른 표현의 관찰1" in _section(html_out, "section-GENRE")
    assert "완전히 다른 표현의 관찰2" not in _section(html_out, "section-PRODUCTION")  # budget spent by Genre


def test_title_matching_not_required_when_event_key_already_present():
    """D. title matching is no longer required for normal, article-backed
    evidence -- _resolve_entry_event_key resolves correctly purely from
    the propagated real event_key, even against a completely EMPTY
    title_to_event_key map (proving title text plays no role at all when
    the real identity is already carried directly)."""
    from report.web_render_v2 import _resolve_entry_event_key
    entry = {"evidence": [{"ref": "E1", "summary": "아무 상관 없는 문장", "event_key": "ev-real"}]}
    assert _resolve_entry_event_key(entry, {}) == "ev-real"


def test_legacy_evidence_without_event_key_falls_back_to_title_matching():
    """E. a legacy evidence citation with no event_key field at all (a
    row persisted before this corrective pass) still resolves safely via
    the existing exact/prefix title-match fallback."""
    from report.web_render_v2 import _resolve_entry_event_key
    entry = {"evidence": [{"ref": "E1", "summary": "Outlet A Original Headline"}]}  # no event_key key at all
    title_to_event_key = {"Outlet A Original Headline": "ev-legacy"}
    assert _resolve_entry_event_key(entry, title_to_event_key) == "ev-legacy"


def test_chart_fact_only_evidence_never_assigned_fabricated_event_key():
    """F. chart-fact-only evidence (no corresponding real article) never
    resolves to a fabricated event_key -- honestly None, both when the
    field is explicitly None and when it's simply absent."""
    from report.web_render_v2 import _resolve_entry_event_key
    explicit_none = {"evidence": [{"ref": "E1", "summary": "#3 Artist - Title (GLOBAL, real chart snapshot)", "event_key": None}]}
    assert _resolve_entry_event_key(explicit_none, {}) is None
    absent_key = {"evidence": [{"ref": "E1", "summary": "#3 Artist - Title (GLOBAL, real chart snapshot)"}]}
    assert _resolve_entry_event_key(absent_key, {}) is None


def test_unrelated_event_key_remains_unaffected_with_direct_propagation():
    """G. a completely unrelated real event (a different real event_key,
    propagated directly, no title match involved) is never touched by the
    lead's own exposure budget."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("GENRE_SIGNAL", is_strongest=True, fact_text="리드", why_it_matters="리드 해석",
                      evidence_refs={"E1"}, evidence=[{"ref": "E1", "summary": "요약1", "event_key": "ev-shared"}]),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [],
        "production_notes": [
            _trend_signal("무관한 사건", "무관한 해석",
                          evidence=[{"ref": "E9", "summary": "완전 다른 요약", "event_key": "ev-unrelated"}]),
        ],
        "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_dashboard_html_v2(data)
    assert "무관한 사건" in _section(html_out, "section-PRODUCTION")


# ---- Cross-Platform Signals: compact, quiet when nothing real found ----


def test_signals_section_quiet_line_when_nothing_found():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-SIGNALS")
    assert "block-quiet" in section
    assert "관측" in section  # real per-source day-count status still shown, compactly


def test_signals_section_shows_real_cross_platform_and_catalog_revival_rows():
    data = _empty_dashboard()
    data["intelligence"]["cross_platform"] = [
        {"music_entity_id": 1, "canonical_artist": "X", "canonical_title": "Y", "sources": ["apple_music", "spotify_chart"], "source_details": []},
    ]
    data["intelligence"]["catalog_revival"]["spotify_chart"] = [
        {"canonical_artist": "Old", "canonical_title": "Song", "gap_days": 30, "age_days": 100},
    ]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SIGNALS")
    assert "block-quiet" not in section
    assert "X - Y" in section
    assert "Old - Song" in section
    assert "30일 공백" in section


# ---- 3-6 Month Outlook: exact honest compact line when insufficient ----


def test_outlook_shows_exact_honest_line_when_no_source_ready():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    section = _section(html_out, "section-OUTLOOK")
    assert "block-quiet" in section
    assert "장기 관측 데이터 축적 중 — 오늘은 단기 관측만 제공합니다." in section


def test_outlook_shows_per_source_status_when_any_source_ready():
    data = _empty_dashboard()
    data["intelligence"]["outlook"]["spotify_chart"] = {
        "status": "READY", "days_of_history": 91, "min_required_days": 90, "progress_ratio": 1.0,
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-OUTLOOK")
    assert "block-quiet" not in section
    assert "예측 가능" in section


# ---- AI / ECONOMY / SOCIETY: hard display caps, progressive disclosure ----


def test_ai_caps_primary_at_eight_with_real_overflow_preserved():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item(f"AI {i}") for i in range(11)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    primary, overflow = _primary_and_overflow(section, 'class="news-card')
    assert primary == 8
    assert overflow == 3
    assert "더 보기 (3)" in section
    for i in range(11):
        assert f"AI {i}" in html_out  # every real item still present somewhere, never dropped


def test_economy_hard_caps_at_exactly_five_with_no_archive():
    """CATEGORY-CONTIGUOUS IA REFINEMENT: ECONOMY gets an exact hard cap
    and explicitly NO 'more' archive -- real overflow beyond 5 is never
    rendered at all here, not even collapsed (unlike AI/Music Industry,
    which still offer a real, collapsed archive). REFERENCE DESIGN: as a
    real DAILY product article feed, ECONOMY now gets the same A/B/C
    editorial-card tiering as AI (1 lead + 1 secondary + 3 compact, in
    the primary_cap=5 window)."""
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item(f"경제 뉴스 {i}") for i in range(9)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert section.count('class="news-card') == 5
    assert "더 보기" not in section
    assert "details" not in section
    for i in range(5, 9):
        assert f"경제 뉴스 {i}" not in html_out  # real overflow never rendered anywhere, not just hidden


def test_society_hard_caps_at_exactly_five_with_no_archive():
    data = _empty_dashboard()
    data["news"]["SOCIETY"] = _news("NORMAL", [_news_item(f"사회 뉴스 {i}") for i in range(6)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SOCIETY")
    assert section.count('class="news-card') == 5
    assert "더 보기" not in section
    assert "사회 뉴스 5" not in html_out


def test_economy_fewer_than_cap_shows_no_more_disclosure():
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item("단독 경제 뉴스")])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert section.count('class="news-card') == 1
    assert "더 보기" not in section


def test_rhetorical_question_only_summary_is_suppressed():
    """SUMMARY QUALITY: confirmed real defect -- a cached snippet
    translation that's purely rhetorical teaser questions ("워터마킹은
    실제로 어떻게 작동할까요? 편집으로 숨길 수 있을까요?") is never shown
    as the summary line -- it's suppressed, never rewritten/fabricated,
    falling back to the card's own real declarative bullets."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item(
        "헤드라인", snippet="워터마킹은 실제로 어떻게 작동할까요? 편집으로 숨길 수 있을까요?",
        why_it_matters="Anthropic의 워터마크 기술 공개는 AI 콘텐츠 진위 판별에 중요한 진전이다.",
        ai_intelligence_status="AVAILABLE",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "어떻게 작동할까요" not in section
    assert 'class="ed-summary"' not in section
    assert "Anthropic의 워터마크 기술 공개는 AI 콘텐츠 진위 판별에 중요한 진전이다." in section  # real bullet still shown


def test_mixed_declarative_and_question_summary_is_never_suppressed():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item(
        "헤드라인", snippet="Anthropic이 새로운 워터마크 기술을 공개했다. 편집 후에도 탐지가 가능한지는 불확실하다.",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="ed-summary"' in section
    assert "Anthropic이 새로운 워터마크 기술을 공개했다" in section


def test_economy_first_item_gets_full_editorial_card():
    """REFERENCE DESIGN: ECONOMY is now a real DAILY product section, not
    a peripheral ultra-compact feed -- its first (Level A) item gets the
    same editorial card treatment (summary + key-point bullets) as AI."""
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item(
        "경제 헤드라인", snippet="실제 요약 문장입니다.", reason="실제 선정 사유",
        why_it_matters="실제 왜 중요한가", ai_intelligence_status="AVAILABLE",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert 'class="news-card ed-card ed-card-lead' in section
    assert "실제 요약 문장입니다" in section
    assert "실제 왜 중요한가" in section


def test_society_first_item_gets_full_editorial_card():
    data = _empty_dashboard()
    data["news"]["SOCIETY"] = _news("NORMAL", [_news_item(
        "사회 헤드라인", snippet="실제 사회 요약입니다.", why_it_matters="실제 사회 중요도",
        ai_intelligence_status="AVAILABLE",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SOCIETY")
    assert 'class="news-card ed-card ed-card-lead' in section
    assert "실제 사회 요약입니다" in section
    assert "실제 사회 중요도" in section


def test_untranslated_english_ai_item_never_becomes_lead_and_sorts_after_korean():
    """DAILY KOREAN-READINESS GUARD: an untranslated English-only item can
    never be Level A or Level B, only the compact Level C brief, and sorts
    AFTER Korean-ready items for display even if it was first in the real
    (unmutated) data list."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("Untranslated English Headline", translation_status="FAILED",
                    snippet="This is a long raw English summary that must never show."),
        _news_item("실제 한국어 헤드라인", snippet="실제 한국어 요약입니다."),
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="news-card ed-card ed-card-lead' in section
    lead_start = section.index('class="news-card ed-card ed-card-lead')
    lead_end = section.index("</article>", lead_start)
    lead_card = section[lead_start:lead_end]
    assert "실제 한국어 헤드라인" in lead_card
    assert "Untranslated English Headline" not in lead_card
    # Korean-ready card renders before the English-only card in the DOM.
    korean_pos = section.index("실제 한국어 헤드라인")
    english_pos = section.index("Untranslated English Headline")
    assert korean_pos < english_pos
    # English-only item is a compact Level C brief: headline shown, but
    # its long raw English summary never renders anywhere.
    assert "This is a long raw English summary that must never show." not in section
    assert data["news"]["AI"]["items"][0]["title"] == "Untranslated English Headline"  # real DB order untouched


def test_english_title_with_real_korean_ai_intelligence_still_requires_korean_title():
    """SUPERSEDED (2026-08-22, permanent Korean-first product requirement):
    the FINAL GOAL PASS (2026-08-17) version of this test asserted that an
    untranslated English TITLE with real Korean AI-intelligence bullets
    should keep its full tier -- real visual evidence on the live public
    page (raw English titles/snippets, e.g. LinkedIn/Nvidia/DeepMind/Ars
    Technica cards) proved that compromise itself was a Korean-first
    defect: the reader's first-seen headline is still raw English. The
    permanent Korean-first gate (report.web_render_v2.is_korean_first_
    ready) now excludes an item with no valid Korean title from AI/
    ECONOMY/SOCIETY/Industry entirely, real Korean AI-intelligence bullets
    or not -- "use the next valid item, or an honest empty state," never a
    raw-English card at any tier."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item(
            "Untranslated English Headline With Real Korean Intelligence",
            ai_intelligence_status="AVAILABLE",
            why_it_matters="이 소식이 중요한 이유에 대한 실제 한국어 분석입니다.",
            what_to_watch="앞으로 지켜봐야 할 점에 대한 실제 한국어 분석입니다.",
        ),
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "Untranslated English Headline With Real Korean Intelligence" not in section
    assert "state-quiet" in section  # honest empty state, never the raw-English card


def test_all_english_ai_items_have_no_level_a_lead_at_all():
    """Never fabricate a Korean lead -- if genuinely nothing in the round
    is Korean-ready, there is simply no Level A card that round."""
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("First English Headline", translation_status="FAILED"),
        _news_item("Second English Headline", translation_status="FAILED"),
    ])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="news-card ed-card ed-card-lead' not in section


def test_music_industry_untouched_by_korean_lead_guard():
    """The Korean-readiness guard is DAILY-only (AI/ECONOMY/SOCIETY) --
    Music Industry keeps its existing behavior untouched, including for a
    real English-titled item at index 0."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("Top industry story", snippet="A real snippet fact.", reason="A real distinct reason.", source_count=2)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert 'class="news-card ed-card ed-card-lead' in section
    lead_start = section.index('class="news-card ed-card ed-card-lead')
    lead_end = section.index("</article>", lead_start)
    assert "Top industry story" in section[lead_start:lead_end]


def test_all_article_cta_links_use_unified_label():
    """FINAL TEXT POLISH: every visible article CTA -- Level C compact
    item-link, ed-cta, and the lead-story's own meta link -- uses the same
    "기사 보기 →" text everywhere; "원문 기사 보기 →" must never appear
    anywhere on either page."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True,
                      headline_item=_news_item("리드 헤드라인", source_url="https://example.com/lead")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조 헤드라인", source_url="https://example.com/secondary")),
    ]
    data["news"]["AI"] = _news("NORMAL", [
        _news_item("AI 헤드라인 1", source_url="https://example.com/ai1", snippet="실제 요약입니다."),
        _news_item("AI 헤드라인 2", source_url="https://example.com/ai2"),
        _news_item("AI 헤드라인 3", source_url="https://example.com/ai3"),
        _news_item("AI 헤드라인 4", source_url="https://example.com/ai4"),
        _news_item("AI 헤드라인 5", source_url="https://example.com/ai5"),
    ])
    html_out = render_dashboard_html_v2(data)
    assert "원문 기사 보기" not in html_out
    assert "기사 보기 →" in html_out


def test_economy_card_shows_source_and_cta_link():
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item(
        "단독 경제 뉴스", source_name="연합뉴스", source_url="https://example.com/real-article",
        published_at="2026-08-14T01:00:00+00:00",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert 'class="ed-cta"' in section
    assert 'href="https://example.com/real-article"' in section


def test_ai_item_shows_real_why_it_matters_when_available():
    data = _empty_dashboard()
    item = _news_item("실제 헤드라인", ai_intelligence_status="AVAILABLE", why_it_matters="실제 왜 중요한가 텍스트")
    data["news"]["AI"] = _news("NORMAL", [item])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "실제 왜 중요한가 텍스트" in section


def test_music_news_card_never_truncates_real_summary_text():
    """REFERENCE DESIGN: MUSIC's own editorial card summary is a real,
    fully readable paragraph (matching the reference image's full-sentence
    summary), never truncated/clamped -- the 1-2 sentence cap (below) is a
    DAILY-only (AI/ECONOMY/SOCIETY) awareness-feed constraint, never
    applied to MUSIC's own deeper editorial summaries."""
    data = _empty_dashboard()
    long_snippet = "매우 긴 실제 기사 본문입니다. " * 40
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("헤드라인", snippet=long_snippet, source_count=2)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert long_snippet.strip() in section  # real text never stripped/truncated
    assert 'class="ed-summary"' in section


def test_daily_summary_capped_to_two_sentences_and_max_length():
    """DAILY summary hygiene: AI/ECONOMY/SOCIETY summaries stay to at most
    2 sentences within a bounded length -- a real scanning-focused
    constraint, distinct from MUSIC's own uncapped summaries (see
    test_music_news_card_never_truncates_real_summary_text). Never
    fabricates replacement text, only truncates the real snippet."""
    data = _empty_dashboard()
    long_snippet = "매우 긴 실제 기사 문장입니다. " * 40
    data["news"]["AI"] = _news("NORMAL", [_news_item("헤드라인", snippet=long_snippet)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="ed-summary"' in section
    assert long_snippet.strip() not in section  # the full uncapped text never appears
    summary_start = section.index('class="ed-summary"')
    summary_html = section[summary_start:section.index("</p>", summary_start)]
    assert summary_html.count("문장입니다.") <= 2  # at most 2 real sentences kept
    assert len(summary_html) < len(long_snippet)


def test_daily_summary_strips_leading_wire_service_boilerplate():
    """Confirmed real defect: Newis/연합뉴스-style bylines like
    "[전남광주=뉴시스]이현행 기자 =" leaking into the visible DAILY summary
    -- stripped only from the exact leading "[dateline=agency]reporter
    title =" shape, never rewriting the real article text after it."""
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item(
        "헤드라인", snippet="[전남광주=뉴시스]이현행 기자 = 실제 기사 본문 내용입니다.",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert "뉴시스" not in section
    assert "이현행 기자" not in section
    assert "실제 기사 본문 내용입니다." in section


def test_daily_summary_inserts_space_after_sentence_end_before_next_word():
    """FINAL TEXT POLISH: a Korean sentence-ending mark immediately
    followed (no whitespace) by the next sentence gets exactly one space
    inserted -- "경신했다.16일" -> "경신했다. 16일" -- deterministic, no
    rewriting."""
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item(
        "헤드라인", snippet="역대 최고 기록을 경신했다.16일 발표된 자료에 따르면 그렇다.",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert "경신했다. 16일" in section
    assert "경신했다.16일" not in section


def test_daily_summary_never_touches_decimal_numbers():
    """A digit immediately before the sentence-ending mark (a real decimal
    number like 66.4) must never get a space inserted -- only a Hangul
    character immediately before the mark triggers the fix."""
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item(
        "헤드라인", snippet="이번 폭우로 66.4㎜의 강수량을 기록했다.",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert "66.4㎜" in section
    assert "66. 4㎜" not in section


# ---- Sources: neutral, compact, real source keys only ----


def test_sources_section_lists_real_active_sources_and_tiktok_unavailable():
    data = _empty_dashboard()
    data["intelligence"]["early_signal"] = {"apple_music": [], "spotify_chart": []}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SOURCES")
    assert "TikTok" in section and "미연동" in section
    assert "연동됨" in section


# ---- Overall document integrity ----


def test_render_produces_complete_html_document_with_correct_title_date():
    data = _empty_dashboard()
    data["report_date_kst"] = "2026-08-15"
    html_out = render_dashboard_html_v2(data)
    assert "<!DOCTYPE html>" in html_out
    assert "<title>SUPER NEWS V2 — 2026.08.15</title>" in html_out
    assert html_out.strip().endswith("</html>")


def test_horizontal_nav_contains_exact_publication_link_order():
    """NEWSLETTER MASTHEAD NAV REBUILD: the old sticky desktop left rail /
    mobile chip-strip split is gone -- one thin horizontal publication nav
    renders the same on every viewport, in the exact order
    MUSIC | CHARTS | INDUSTRY | SPOTIFY | RADAR | PRODUCER | AI | 경제 |
    사회, plus the load-bearing MUSIC INTELLIGENCE release-gate badge."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    nav = html_out[html_out.index('class="pub-nav"'):html_out.index("</nav>")]
    expected_order = (
        ("MUSIC", "today-intel"), ("CHARTS", "section-CHARTPULSE"), ("INDUSTRY", "section-INDUSTRY"),
        ("SPOTIFY", "section-SPOTIFY"), ("RADAR", "section-GENRE"), ("PRODUCER", "section-PRODUCER"),
        ("AI", "section-AI"), ("경제", "section-ECONOMY"), ("사회", "section-SOCIETY"),
    )
    positions = []
    for label, anchor in expected_order:
        link = f'<a class="pub-nav-link" href="#{anchor}">{label}</a>'
        assert link in nav
        positions.append(nav.index(link))
    assert positions == sorted(positions)  # exact left-to-right order, not just presence
    assert nav.count('class="pub-nav-link"') == len(expected_order)  # no extra/duplicate links
    assert ">MUSIC INTELLIGENCE<" in nav  # release-gate substring lives in the nav badge itself


def test_left_rail_and_shell_layout_are_fully_removed():
    """The old dashboard-style permanent left sidebar/rail and its
    wrapping two-column .shell layout must not exist anywhere in the
    rendered page -- replaced by the thin horizontal .pub-nav plus a
    single centered .main reading column."""
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert "railnav" not in html_out
    assert 'class="shell"' not in html_out
    assert "nav-link-music-mobile" not in html_out
    assert "nav-group-label" not in html_out
    assert 'class="main"' in html_out


def test_music_intelligence_marker_present_for_the_real_release_gate():
    """report/release_v2.py's real production release gate
    (verify_local_v2_dashboard/verify_external_v2_dashboard) scans every
    generated page for the literal ">MUSIC INTELLIGENCE<" substring as its
    own staleness signal -- a regression here would silently break the
    real daily release flow (PUBLISH_BLOCKED/EXTERNAL_VERIFICATION_FAILED
    on an otherwise perfectly valid page), not just cosmetics."""
    from report.release_v2 import has_music_intelligence_marker

    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert has_music_intelligence_marker(html_out)


def test_no_html_injection_from_untrusted_title_text():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item('<script>alert(1)</script>')])
    html_out = render_dashboard_html_v2(data)
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


# ---- NEWSLETTER x INTELLIGENCE HYBRID REDESIGN ----------------------------


def test_masthead_shows_daily_music_intelligence_tagline_and_real_reading_time():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    assert "Daily Music Intelligence" in html_out
    assert "MIN READ" in html_out


def test_reading_time_is_deterministic_for_the_same_real_content():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("A real headline", snippet="A real summary sentence.")])
    first = render_dashboard_html_v2(data)
    second = render_dashboard_html_v2(data)
    import re as _re
    first_minutes = _re.search(r"(\d+) MIN READ", first).group(1)
    second_minutes = _re.search(r"(\d+) MIN READ", second).group(1)
    assert first_minutes == second_minutes


def test_category_transition_marks_boundary_between_music_and_ai():
    html_out = render_dashboard_html_v2(_empty_dashboard())
    ai_transition_pos = html_out.index('class="category-transition transition-AI"')
    ai_section_pos = html_out.index('id="section-AI"')
    music_industry_pos = html_out.index('id="section-INDUSTRY"')
    assert music_industry_pos < ai_transition_pos < ai_section_pos


def test_music_industry_featured_item_gets_summary_and_key_point_bullets():
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item(
        "Top industry story", snippet="Real snippet fact here", reason="Real distinct reason here", source_count=2,
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert 'class="news-card ed-card ed-card-lead' in section
    assert 'class="ed-bullets"' in section
    assert "Real snippet fact here" in section
    assert "Real distinct reason here" in section


def test_music_industry_card_gets_semantic_story_type_pill():
    """SEMANTIC NEWS COLOR (EDITORIAL INTEGRITY FIX): a Music Industry
    card whose real text matches a named priority class gets a small
    restrained category pill showing that story type; an AI-section card
    with the exact same matching text must never get one --
    music_industry_priority_rank is a Music-specific classifier and this
    is scoped to Music Industry's own render call only (show_story_type)."""
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("Label signs new licensing deal", source_count=2)])
    data["news"]["AI"] = _news("NORMAL", [_news_item("Label signs new licensing deal", source_count=2)])
    html_out = render_dashboard_html_v2(data)
    industry_section = _section(html_out, "section-INDUSTRY")
    ai_section = _section(html_out, "section-AI")
    assert '<span class="ed-pill">RIGHTS/LICENSING</span>' in industry_section
    assert "RIGHTS/LICENSING" not in ai_section


def test_music_industry_card_without_matching_priority_class_gets_no_pill():
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("A real chart consumption shift story", source_count=2)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-INDUSTRY")
    assert 'class="ed-pill"' not in section


def test_chart_pulse_has_data_table_header_row():
    data = _empty_dashboard()
    entry = _spotify_entry(1, artist="A", title="T1", rank_delta=2)
    data["spotify_chart"] = {"state": "NORMAL", "top10": [entry], "new_entries": [], "trend": _trend([entry]), "is_first_observation": False}
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-CHARTPULSE")
    assert "<thead>" in section
    assert "순위" in section and "트랙" in section and "상태" in section


def test_production_radar_uses_distinct_label_from_genre_radar():
    data = _empty_dashboard()
    data["music_trend_intelligence"] = {
        "state": "NORMAL", "genre_signals": [], "producer_references": [], "kpop_ar_notes": [],
        "production_notes": [_trend_signal("실제 프로덕션 관찰", "실제 해석", confidence="MEDIUM")],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-PRODUCTION")
    assert "관측된 프로덕션 특성" in section
    assert "radar-card-production" in section


# ---- REFERENCE DESIGN: ECONOMY/SOCIETY editorial-card treatment ----
# (see test_economy_first_item_gets_full_editorial_card /
# test_society_first_item_gets_full_editorial_card /
# test_economy_card_shows_source_and_cta_link above -- ECONOMY/SOCIETY are
# now real DAILY-product sections with the same A/B/C tiering as AI, not
# a peripheral ultra-compact feed.)


def test_economy_row_never_fabricates_a_link_when_no_source_url():
    data = _empty_dashboard()
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item("실제 헤드라인", source_name="연합뉴스", source_url=None)])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-ECONOMY")
    assert "원문 기사 보기" not in section
    assert "연합뉴스" in section


# ---- REFERENCE DESIGN: SUPER NEWS MUSIC / SUPER NEWS DAILY product split ----


def test_music_page_contains_no_ai_economy_society_content():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item("고유 AI 헤드라인 마커")])
    data["news"]["ECONOMY"] = _news("NORMAL", [_news_item("고유 경제 헤드라인 마커")])
    data["news"]["SOCIETY"] = _news("NORMAL", [_news_item("고유 사회 헤드라인 마커")])
    html_out = render_music_page_html_v2(data)
    assert "고유 AI 헤드라인 마커" not in html_out
    assert "고유 경제 헤드라인 마커" not in html_out
    assert "고유 사회 헤드라인 마커" not in html_out
    assert 'id="section-AI"' not in html_out
    assert 'id="section-ECONOMY"' not in html_out
    assert 'id="section-SOCIETY"' not in html_out
    assert "SUPER NEWS MUSIC" in html_out


def test_music_page_contains_real_music_sections():
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("고유 업계 뉴스 마커", source_count=2)])
    html_out = render_music_page_html_v2(data)
    assert "고유 업계 뉴스 마커" in html_out
    assert 'id="section-INDUSTRY"' in html_out


def test_daily_page_contains_no_music_content():
    data = _empty_dashboard()
    data["news"]["SPOTIFY"] = _news("NORMAL", [_news_item("고유 업계 뉴스 마커", source_count=2)])
    data["news"]["AI"] = _news("NORMAL", [_news_item("고유 AI 헤드라인 마커")])
    html_out = render_daily_page_html_v2(data)
    assert "고유 업계 뉴스 마커" not in html_out
    assert 'id="section-INDUSTRY"' not in html_out
    assert 'id="today-intel"' not in html_out
    assert "고유 AI 헤드라인 마커" in html_out
    assert "SUPER NEWS DAILY" in html_out


def test_daily_page_contains_ai_economy_society_sections():
    html_out = render_daily_page_html_v2(_empty_dashboard())
    assert 'id="section-AI"' in html_out
    assert 'id="section-ECONOMY"' in html_out
    assert 'id="section-SOCIETY"' in html_out


def test_both_split_pages_produce_complete_html_documents():
    data = _empty_dashboard()
    data["report_date_kst"] = "2026-08-15"
    music_html = render_music_page_html_v2(data)
    daily_html = render_daily_page_html_v2(data)
    for html_out in (music_html, daily_html):
        assert "<!DOCTYPE html>" in html_out
        assert html_out.strip().endswith("</html>")
        assert "2026.08.15" in html_out


def test_daily_body_class_scopes_larger_images_music_untouched():
    """DAILY_ONLY image enlargement is scoped via <body class="page-daily">
    -- MUSIC's own <body class="page-music"> never carries the DAILY
    class, so the .page-daily-scoped rule can never apply there even
    though the CSS text is shared across pages via the same _STYLE
    constant."""
    music_html = render_music_page_html_v2(_empty_dashboard())
    daily_html = render_daily_page_html_v2(_empty_dashboard())
    assert '<body class="page-daily">' in daily_html
    assert '<body class="page-music">' in music_html
    assert '<body class="page-daily">' not in music_html
    assert '<body class="page-music">' not in daily_html
    assert '.page-daily .ed-card-media { flex: 0 0 48%; max-width: 480px; }' in daily_html
    # The image-fill mechanism itself (stretch + height:100% + no aspect-
    # ratio constraint on the img) is shared by every .ed-card-media --
    # only the DAILY column WIDTH is page-daily-scoped.
    assert '.ed-card-media img { display: block; width: 100%; height: 100%; object-fit: cover;' in daily_html
    assert 'align-items: stretch;' in daily_html


def test_ed_card_media_frame_fills_full_card_height_desktop():
    """FIX ARTICLE IMAGE FRAME PROPERLY: the media wrapper stretches to
    the card's full content height (align-items: stretch, align-self:
    stretch) and the image has no aspect-ratio constraint of its own --
    it fills the wrapper via width/height: 100% + object-fit: cover, so
    there is no blank rectangle beneath a smaller fixed 4:3 box."""
    music_html = render_music_page_html_v2(_empty_dashboard())
    assert '.ed-card { background: var(--surface); border: 1px solid var(--rule); border-radius: 14px;' in music_html
    assert 'align-items: stretch; }' in music_html
    assert 'align-self: stretch;\n  overflow: hidden;' in music_html
    assert 'aspect-ratio: 4 / 3' not in music_html.split("@media (max-width: 640px)")[0]  # no desktop aspect-ratio constraint on the img


def test_semantic_duplication_guard_suppresses_same_chart_fact_in_later_section():
    """SEMANTIC DUPLICATION GUARD: confirmed real defect -- the SAME real
    Spotify chart fact (Shakira - Dai Dai's rank change) independently
    resurfaced, reworded, across the hero's own secondary signal AND a
    separate Genre Radar entry. Only the FIRST (hero secondary, earlier
    in the fixed editorial walk order) may show it in detail; the later
    Genre Radar entry referencing the SAME real track must be suppressed,
    not just reworded."""
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [_spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2)],
        "new_entries": [], "trend": _trend([_spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2)]),
        "is_first_observation": False,
    }
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", fact_text="Shakira - Dai Dai — 오늘 가장 큰 검증된 상승폭 ▲2"),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "Shakira - Dai Dai 등 라틴팝 트랙이 강세를 보였다",
            "interpretation": "라틴 팝 장르가 오늘 차트에서 두각을 나타냈다.",
            "evidence": [],
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_music_page_html_v2(data)
    assert "Shakira - Dai Dai — 오늘 가장 큰 검증된 상승폭" in html_out  # hero secondary: kept
    genre_section = _section(html_out, "section-GENRE")
    assert "Shakira - Dai Dai 등 라틴팝 트랙이 강세를 보였다" not in genre_section  # later duplicate: suppressed


def test_semantic_duplication_guard_catches_naturally_rephrased_mention():
    """Confirmed real gap found during before/after verification: an
    LLM-synthesized ANALYSIS entry (e.g. a Producer Insight's combined
    'chart movements' narrative) naturally rephrases a track mention in
    Korean prose ("Shakira의 'Dai Dai'는 순위가 2계단 상승했다") rather
    than the canonical "Artist - Title" format a raw chart-fact
    candidate uses -- the guard must still catch this as the same real
    track, not just an exact "Artist - Title" substring match."""
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [_spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2)],
        "new_entries": [], "trend": _trend([_spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2)]),
        "is_first_observation": False,
    }
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", fact_text="Shakira - Dai Dai — 오늘 가장 큰 검증된 상승폭 ▲2"),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "Spotify chart에 신곡들이 대거 진입했다. Shakira의 'Dai Dai'는 순위가 2계단 상승했다.",
            "interpretation": "차트 지형이 재편되고 있다.",
            "evidence": [],
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_music_page_html_v2(data)
    genre_section = _section(html_out, "section-GENRE")
    assert "Shakira의 'Dai Dai'는 순위가 2계단 상승했다" not in genre_section  # naturally-rephrased duplicate: suppressed


def test_semantic_duplication_guard_keeps_unrelated_entries():
    """Entries about a genuinely DIFFERENT real track are never touched by
    the guard -- suppression is scoped to a real shared chart-entity
    identity, never a blanket per-section cap."""
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [
            _spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2),
            _spotify_entry(6, artist="Tame Impala", title="Loser", is_new=True),
        ],
        "new_entries": [], "trend": _trend([
            _spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2),
            _spotify_entry(6, artist="Tame Impala", title="Loser", is_new=True),
        ]),
        "is_first_observation": False,
    }
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", fact_text="Shakira - Dai Dai — 오늘 가장 큰 검증된 상승폭 ▲2"),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "Tame Impala - Loser가 톱10에 신규 진입했다",
            "interpretation": "인디 록 사운드가 메인스트림에 침투하고 있다.",
            "evidence": [],
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_music_page_html_v2(data)
    genre_section = _section(html_out, "section-GENRE")
    assert "Tame Impala - Loser가 톱10에 신규 진입했다" in genre_section  # unrelated real track: kept


def test_semantic_duplication_guard_never_forces_a_replacement_entry():
    """Suppression only ever removes -- a section legitimately ends up
    with zero real items rather than a fabricated replacement when every
    real candidate it had duplicates an already-shown chart entity."""
    data = _empty_dashboard()
    data["spotify_chart"] = {
        "state": "NORMAL",
        "top10": [_spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2)],
        "new_entries": [], "trend": _trend([_spotify_entry(1, artist="Shakira", title="Dai Dai", rank_delta=2)]),
        "is_first_observation": False,
    }
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", fact_text="Shakira - Dai Dai — 오늘 가장 큰 검증된 상승폭 ▲2"),
    ]
    data["music_trend_intelligence"] = {
        "state": "NORMAL",
        "genre_signals": [{
            "observed": "Shakira - Dai Dai가 오늘 가장 크게 상승했다",
            "interpretation": "라틴 팝이 강세다.",
            "evidence": [],
        }],
        "production_notes": [], "producer_references": [], "kpop_ar_notes": [],
    }
    html_out = render_music_page_html_v2(data)
    genre_section = _section(html_out, "section-GENRE")
    assert "state-message" in genre_section  # honest empty state, never a fabricated card


def test_music_full_section_order_chart_pulse_last_real_content():
    """Full MUSIC content order, verified via real DOM string indexes:
    lead -> industry -> today-secondary -> music-today -> spotify-watch ->
    genre -> production -> producer -> signals -> outlook -> chart-pulse
    -> sources. Chart Pulse is the last REAL content section, strictly
    before the technical Sources section."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조 헤드라인")),
    ]
    html_out = render_music_page_html_v2(data)
    lead_pos = html_out.index('class="lead-story"')
    industry_pos = html_out.index('id="section-INDUSTRY"')
    today_secondary_pos = html_out.index('id="today-in-music"')
    music_today_pos = html_out.index('id="section-MUSICTODAY"')
    spotify_pos = html_out.index('id="section-SPOTIFY"')
    genre_pos = html_out.index('id="section-GENRE"')
    production_pos = html_out.index('id="section-PRODUCTION"')
    producer_pos = html_out.index('id="section-PRODUCER"')
    signals_pos = html_out.index('id="section-SIGNALS"')
    outlook_pos = html_out.index('id="section-OUTLOOK"')
    chart_pulse_pos = html_out.index('id="section-CHARTPULSE"')
    sources_pos = html_out.index('id="section-SOURCES"')
    assert (
        lead_pos < industry_pos < today_secondary_pos < music_today_pos < spotify_pos
        < genre_pos < production_pos < producer_pos < signals_pos < outlook_pos
        < chart_pulse_pos < sources_pos
    )
    assert outlook_pos < chart_pulse_pos < sources_pos  # explicit key relationship
    # Chart Pulse is the last REAL content section (only Sources, the
    # technical status section, follows it).
    assert chart_pulse_pos == max(
        industry_pos, today_secondary_pos, music_today_pos, spotify_pos, genre_pos,
        production_pos, producer_pos, signals_pos, outlook_pos, chart_pulse_pos,
    )


def test_music_nav_chart_link_is_last_content_link():
    """MUSIC nav "차트" link moves to the last real-content nav position,
    matching Chart Pulse's new bottom position -- style/markup unchanged,
    order only."""
    html_out = render_music_page_html_v2(_empty_dashboard())
    nav_end = html_out.index("</nav>") if "</nav>" in html_out else html_out.index('<main class="main">')
    nav_html = html_out[html_out.index('class="pub-nav"'):nav_end]
    chart_pos = nav_html.index(">차트<")
    other_positions = [nav_html.index(f">{label}<") for label in ("음악", "음악 산업", "Spotify", "레이더", "프로듀서")]
    assert chart_pos > max(other_positions)


def test_music_industry_is_first_section_before_music_today_and_chart_pulse():
    """Required MUSIC top order: hero -> Music Industry -> Music Today ->
    Chart Pulse -- section-INDUSTRY must be the first section in <main>,
    before both section-MUSICTODAY and section-CHARTPULSE."""
    html_out = render_music_page_html_v2(_empty_dashboard())
    industry_pos = html_out.index('id="section-INDUSTRY"')
    music_today_pos = html_out.index('id="section-MUSICTODAY"')
    chart_pulse_pos = html_out.index('id="section-CHARTPULSE"')
    assert industry_pos < music_today_pos < chart_pulse_pos
    main_start = html_out.index('<main class="main">')
    first_section_start = html_out.index("<section", main_start)
    assert 'id="section-INDUSTRY"' in html_out[first_section_start:first_section_start + 200]


def test_music_exact_visible_order_lead_industry_today_secondary_musictoday_chartpulse():
    """EXACT VISIBLE ORDER FIX: 오늘의 음악 소식 (today-secondary) is no
    longer nested inside .today-intel -- it must render AFTER
    section-INDUSTRY and BEFORE section-MUSICTODAY. Required order: lead
    story -> 뮤직 인더스트리 -> 오늘의 음악 소식 -> 뮤직 투데이 -> 차트 펄스."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드 헤드라인")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조 헤드라인")),
    ]
    html_out = render_music_page_html_v2(data)
    lead_pos = html_out.index('class="lead-story"')
    industry_pos = html_out.index('id="section-INDUSTRY"')
    today_secondary_pos = html_out.index('id="today-in-music"')
    music_today_pos = html_out.index('id="section-MUSICTODAY"')
    chart_pulse_pos = html_out.index('id="section-CHARTPULSE"')
    assert lead_pos < industry_pos < today_secondary_pos < music_today_pos < chart_pulse_pos
    # 오늘의 음악 소식 no longer lives inside .today-intel.
    today_intel_html = html_out[html_out.index('id="today-intel"'):html_out.index('<main class="main">')]
    assert "오늘의 음악 소식" not in today_intel_html
    assert "리드 헤드라인" in today_intel_html
    # Its own card content (the secondary signal) still renders intact.
    assert "보조 헤드라인" in html_out[today_secondary_pos:music_today_pos]


def test_music_today_secondary_card_design_unchanged():
    """The relocated 오늘의 음악 소식 block keeps its exact original
    markup/classes -- only its parent container changed, not its own
    design."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조 헤드라인", image_url="https://cdn.example.com/s1.jpg")),
    ]
    html_out = render_music_page_html_v2(data)
    assert 'class="today-secondary" id="today-in-music"' in html_out
    assert '<h2 class="today-secondary-head">오늘의 음악 소식</h2>' in html_out
    assert 'class="today-secondary-list"' in html_out
    assert 'class="signal-card"' in html_out


def test_dashboard_combined_page_keeps_today_secondary_nested_unchanged():
    """render_dashboard_html_v2 (legacy combined page, untouched by this
    fix) keeps the original nested structure -- 오늘의 음악 소식 still
    lives inside the same .today-intel div as the lead, exactly as
    before."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [
        _today_signal("INDUSTRY_NEWS", is_strongest=True, headline_item=_news_item("리드")),
        _today_signal("VIRAL_HOT", headline_item=_news_item("보조 헤드라인")),
    ]
    html_out = render_dashboard_html_v2(data)
    today_intel_html = html_out[html_out.index('id="today-intel"'):html_out.index('<main class="main">')]
    assert "오늘의 음악 소식" in today_intel_html
    assert "보조 헤드라인" in today_intel_html


def test_daily_page_never_shows_source_status_section():
    """Remove from DAILY only: Apple Music/Spotify/TikTok source status --
    that's a MUSIC-product concept, meaningless on a pure AI/ECONOMY/
    SOCIETY page."""
    html_out = render_daily_page_html_v2(_empty_dashboard())
    assert 'id="section-SOURCES"' not in html_out
    assert "출처" not in html_out


def test_music_page_still_shows_source_status_section():
    """Regression guard: the DAILY-only sources removal must not
    accidentally remove it from MUSIC, where it's still meaningful."""
    html_out = render_music_page_html_v2(_empty_dashboard())
    assert 'id="section-SOURCES"' in html_out


def test_masthead_brand_and_subtitles_are_exact():
    """USER CHANGE #1: SUPER NEWS is enlarged and paired with the edition
    word, and the exact requested subtitles render on each page."""
    music_html = render_music_page_html_v2(_empty_dashboard())
    daily_html = render_daily_page_html_v2(_empty_dashboard())
    assert 'class="brand">SUPER NEWS <span class="brand-edition-music">MUSIC</span>' in music_html
    assert "작곡가·프로듀서를 위한 오늘의 음악 인텔리전스" in music_html
    assert 'class="brand">SUPER NEWS <span class="brand-edition-daily">DAILY</span>' in daily_html
    assert "AI · 경제 · 사회 핵심 브리핑" in daily_html


# ---- FINAL 90+ QUALITY CORRECTION PASS: summary/bullet near-duplicate guard ----


def test_ed_card_bullet_identical_to_summary_is_suppressed():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item(
        "고유 헤드라인", snippet="Cursor가 SpaceX의 일부가 되었다.",
        ai_intelligence_status="AVAILABLE", why_it_matters="Cursor가 SpaceX의 일부가 되었다.",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert 'class="ed-summary"' in section
    assert 'class="ed-bullets"' not in section  # the only candidate bullet duplicated the summary


def test_ed_card_bullet_genuinely_additive_is_kept():
    data = _empty_dashboard()
    data["news"]["AI"] = _news("NORMAL", [_news_item(
        "고유 헤드라인", snippet="Cursor가 SpaceX의 일부가 되었다.",
        ai_intelligence_status="AVAILABLE", why_it_matters="이는 AI 코딩 도구 시장의 대형 M&A 지형 변화를 시사한다.",
    )])
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-AI")
    assert "이는 AI 코딩 도구 시장의 대형 M&amp;A 지형 변화를 시사한다." in section


def test_lead_story_why_row_duplicate_of_summary_suppressed():
    data = _empty_dashboard()
    data["today_music_intelligence"] = [_today_signal(
        "INDUSTRY_NEWS", is_strongest=True,
        headline_item=_news_item("음악 헤드라인", snippet="아티스트 X의 신곡이 차트 1위에 올랐다."),
        why_it_matters="아티스트 X의 신곡이 차트 1위에 올랐다.",
    )]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "lead-why-row" not in section


def test_lead_story_suppresses_cached_newsletter_watch_next():
    """CURRENT-CACHE SAFETY: the hero lead story's 프로듀서 시사점 row
    must never show cached editorial-content-creation advice either."""
    data = _empty_dashboard()
    data["today_music_intelligence"] = [_today_signal(
        "INDUSTRY_NEWS", is_strongest=True,
        headline_item=_news_item("음악 헤드라인", snippet="차트 관련 실제 요약"),
        why_it_matters="실제 왜 중요한가 텍스트",
        watch_next="이번 주 차트 무브를 요약한 짧은 뉴스레터 섹션을 바로 만들 수 있다",
    )]
    html_out = render_dashboard_html_v2(data)
    section = _today_intel_section(html_out)
    assert "뉴스레터" not in section


def test_music_today_card_suppresses_cached_newsletter_implication():
    data = _empty_dashboard()
    data["music_today"] = [_music_candidate(
        "INDUSTRY_NEWS", mode="ANALYSIS", why_it_matters="실제 왜 중요한가",
        producer_implication="이번 주 차트 무브를 요약한 짧은 뉴스레터 섹션을 바로 만들 수 있다",
    )]
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-MUSICTODAY")
    assert "뉴스레터" not in section


def test_spotify_watch_suppresses_cached_newsletter_implication(monkeypatch):
    data = _empty_dashboard()
    data["spotify_watch_candidates"] = [
        _news_item("Spotify announces new licensing agreement", source_url="https://example.com/a", event_key="ev-1"),
    ]
    data["producer_intelligence"] = {
        "state": "NORMAL",
        "insights": [{
            **_producer_insight("실제 시그널"),
            "what_could_i_make_now": "이번 주 차트 무브를 요약한 짧은 뉴스레터 섹션을 바로 만들 수 있다",
            "evidence": [{"ref": "E1", "summary": "Spotify announces new licensing agreement"}],
        }],
    }
    html_out = render_dashboard_html_v2(data)
    section = _section(html_out, "section-SPOTIFY")
    assert "뉴스레터" not in section


# =============================================================================
# PERMANENT KOREAN-FIRST DELIVERY GATE (2026-08-22)
# =============================================================================


def test_korean_first_gate_accepts_real_korean_translation():
    item = _news_item("Vanessa Bosaen exits Virgin Music Group",
                       ko_title="바네사 보사엔, 버진 뮤직 그룹 떠나", translation_status="TRANSLATED")
    assert is_korean_first_ready(item) is True


def test_korean_first_gate_rejects_untranslated_english_sentence():
    # No ko_title/translation_status -- _display_title falls back to the
    # raw English sentence, which the gate must reject.
    item = _news_item("Los Lobos Settles Sony Music Lawsuit Over Soundtrack Royalties")
    assert is_korean_first_ready(item) is False


def test_korean_first_gate_allows_bare_proper_noun_english():
    for title in ("BTS", "HYBE", "Billboard", "Spotify", "Take Two"):
        item = _news_item(title)
        assert is_korean_first_ready(item) is True, title


def test_korean_first_gate_text_form_matches_item_form():
    assert is_korean_first_text_ready("이 문장은 한국어입니다") is True
    assert is_korean_first_text_ready("This is an ordinary English sentence") is False
    assert is_korean_first_text_ready("HYBE") is True
    assert is_korean_first_text_ready("") is True  # nothing to gate

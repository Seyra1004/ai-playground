"""Kakao V2 -- exactly ONE compact message per day, deterministic (no LLM
call, no new cost/reliability risk). Reuses report.web_data_v2.
build_dashboard_data_v2()'s structured data -- this module only formats
it, it never re-derives or re-selects what matters (same "presentation-
only" rule report/web_render.py already follows).

Hard requirement: output is always a single string <= kakao.client.
MAX_TEXT_LENGTH characters. Per-field truncation happens BEFORE assembly
so a long title can't silently push the message over budget; a final
safety-net truncation on the fully assembled string guarantees the hard
limit even if a per-field budget is ever changed carelessly.

Never includes: a full TOP10 list, complete article lists, multiple
source links, category navigation links, or the dashboard URL as literal
text -- the URL belongs in Kakao's native link/button_title fields
(kakao/client.py's send_memo() already supports both), which cost zero
characters of the 200-char text budget.
"""

from report.validation import is_low_value_gossip_takeaway

MAX_TEXT_LENGTH = 200
_FIELD_BUDGET = 28


def _clip(text, limit=_FIELD_BUDGET):
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_news_line(news_section):
    items = news_section.get("items") or []
    if not items:
        return None
    return _clip(items[0]["title"])


def _spotify_line(spotify_chart):
    if spotify_chart["state"] != "NORMAL" or not spotify_chart["top10"]:
        return "데이터 없음"
    top = spotify_chart["top10"][0]
    return _clip(f"{top['canonical_artist']} - {top['canonical_title']} (1위)")


def _tiktok_line(tiktok_chart):
    # Always honest -- TikTok has no data source yet; never substituted
    # with Apple Music or any other platform.
    return "데이터 소스 미가동"


def _signal_line(intelligence):
    for source_name in ("spotify_chart", "apple_music"):
        candidates = intelligence.get("early_signal", {}).get(source_name) or []
        if candidates:
            top = candidates[0]
            delta = int(top["rank_delta"])
            return _clip(f"{top['canonical_artist']} - {top['canonical_title']} (+{delta})")
    return "신호 없음"


_CTA_LINE = "전체 브리핑 →"


def render_kakao_digest(dashboard_data_v2):
    """dashboard_data_v2: the exact shape report.web_data_v2.
    build_dashboard_data_v2() returns. Returns a single string, always
    <= MAX_TEXT_LENGTH characters -- callers pass this straight to
    kakao.client.send_memo(text, link_url=..., button_title=...).

    The CTA line is budget-reserved, not appended-then-hoped-for: content
    is truncated to fit AROUND it, so a long real-content day can never
    silently clip the call-to-action off the end of the message (a real
    bug caught by this module's own tests -- a naive "assemble everything,
    truncate the whole string" approach truncated the CTA away under
    realistic content)."""
    y, m, d = dashboard_data_v2["report_date_kst"].split("-")
    news = dashboard_data_v2["news"]

    lines = [f"SUPER NEWS | {int(m)}월 {int(d)}일", ""]
    lines.append("🎵 MUSIC")
    lines.append(f"TikTok: {_tiktok_line(dashboard_data_v2['tiktok_chart'])}")
    lines.append(f"Spotify: {_spotify_line(dashboard_data_v2['spotify_chart'])}")
    lines.append(f"Signal: {_signal_line(dashboard_data_v2['intelligence'])}")
    lines.append("")

    for label, category in (("🤖 AI", "AI"), ("💰 ECONOMY", "ECONOMY"), ("🌐 SOCIETY", "SOCIETY")):
        line = _first_news_line(news[category])
        lines.append(label)
        lines.append(line if line else "오늘 보고할 뉴스 없음")
        lines.append("")

    body = "\n".join(lines).rstrip()

    cta_block = "\n\n" + _CTA_LINE
    body_budget = MAX_TEXT_LENGTH - len(cta_block)
    if len(body) > body_budget:
        body = body[: body_budget - 1].rstrip() + "…"

    text = body + cta_block
    assert len(text) <= MAX_TEXT_LENGTH  # invariant, not a runtime user-facing check
    return text


# =============================================================================
# Full multi-message digest (FINAL MUSIC INTEGRATION / KAKAO E2E phase)
# =============================================================================
#
# render_kakao_digest() above is deliberately a single <=200-char teaser.
# This function is a SEPARATE, additive renderer for the explicitly-
# approved first real E2E send: a fuller real digest that actually
# surfaces the completed Music Intelligence capability (Genre/Production/
# Producer Reference Radar, K-pop/A&R, Producer Intelligence) rather than
# just a one-line teaser -- Kakao's 200-char-per-message limit means this
# is assembled as ONE longer logical text and left for the caller to split
# via report.kakao_render.split_message() (the same generic, already-
# tested utility report_delivery.py already uses for V1's own multi-
# message sends) and send as multiple kakao.client.send_memo() calls, one
# per chunk. Every section distinguishes OBSERVED FACT ("관찰:") from AI
# INFERENCE ("추론:") with the same textual labels report/web_render_v2.py
# already uses for this in the HTML UI. Every section renders its own
# honest empty-state line when real evidence doesn't support it -- never
# padded, never fabricated. TikTok chart data source is always reported as
# unavailable, matching the UI. Never includes a full TOP10 list, complete
# article lists, or a raw internal evidence ref code.

_FULL_FIELD_BUDGET = 60

_TREND_EMPTY_LINES = {
    "genre_signals": "오늘은 근거 부족",
    "production_notes": "오늘은 근거 부족",
    "producer_references": "오늘은 근거 부족",
    "kpop_ar_notes": "오늘은 근거 부족",
}
_TREND_LABELS = {
    "genre_signals": "장르 레이더",
    "production_notes": "프로덕션 레이더",
    "producer_references": "프로듀서 레퍼런스",
    "kpop_ar_notes": "케이팝/A&R",
}


def _clip_full(text, limit=_FULL_FIELD_BUDGET):
    return _clip(text, limit=limit)


def _news_lead_line(news_section, label):
    line = _first_news_line(news_section)
    return f"{label}: {line}" if line else f"{label}: 오늘 보고할 뉴스 없음"


def _first_non_gossip_news_line(news_section):
    """Same as _first_news_line, but skips any item report.validation.
    is_low_value_gossip_takeaway would reject (a low-value fan/social-
    comment gossip story -- e.g. a deleted-comment fandom spat -- with no
    real songwriting/production/A&R/label-business/platform-policy/
    rights-copyright/royalty-licensing/market signal). Used ONLY for the
    MUSIC Industry line below -- report.web_render_v2's HTML Producer/A&R
    section already applies this same helper; this closes the gap where
    the Kakao digest's own separate Industry-line selection bypassed it.
    _first_news_line itself (used by DAILY's AI/ECONOMY/SOCIETY lines) is
    intentionally left unchanged."""
    items = news_section.get("items") or []
    for item in items:
        if not is_low_value_gossip_takeaway(item.get("title")):
            return _clip(item["title"])
    return None


def _music_industry_lines(news):
    # Same real definition report.music_trend_synthesis.build_evidence_
    # catalog already uses for "industry news": TikTok-category +
    # Spotify-category news items (both are real Music Industry news
    # buckets, distinct from the TikTok/Spotify CHART data sources below).
    lines = []
    for label, category in (("TikTok", "TIKTOK"), ("Spotify", "SPOTIFY")):
        line = _first_non_gossip_news_line(news[category])
        if line:
            lines.append(f"{label}: {line}")
    return lines if lines else ["오늘 보고할 뮤직 인더스트리 뉴스 없음"]


def _apple_signal_line(intelligence):
    candidates = intelligence.get("early_signal", {}).get("apple_music") or []
    if not candidates:
        return "Apple Music: 신호 없음"
    top = candidates[0]
    delta = int(top["rank_delta"])
    raw = f"{top['canonical_artist']} - {top['canonical_title']} (+{delta})"
    return f"Apple Music: {_clip_full(raw)}"


def _trend_section_lines(music_trend_intelligence, field):
    label = _TREND_LABELS[field]
    if music_trend_intelligence["state"] != "NORMAL":
        return [f"{label}: {_TREND_EMPTY_LINES[field]}"]
    items = music_trend_intelligence.get(field) or []
    if not items:
        return [f"{label}: {_TREND_EMPTY_LINES[field]}"]
    top = items[0]
    return [
        f"{label}:",
        f"  관찰: {_clip_full(top['observed'])}",
        f"  추론: {_clip_full(top['interpretation'])}",
    ]


def _producer_intelligence_lines(producer_intelligence):
    if producer_intelligence["state"] != "NORMAL" or not producer_intelligence.get("insights"):
        return ["프로듀서 인사이트: 오늘은 근거 부족"]
    top = producer_intelligence["insights"][0]
    return [
        "프로듀서 인사이트:",
        f"  관찰: {_clip_full(top['what_is_moving'])}",
        f"  실행 제안: {_clip_full(top['what_could_i_make_now'])}",
    ]


def _future_radar_line(intelligence):
    outlook = intelligence.get("outlook") or {}
    statuses = [v for v in outlook.values() if isinstance(v, dict)]
    if not statuses:
        return "퓨처 레이더: 데이터 없음"
    # Real, not estimated: report the MOST advanced real status among
    # active sources -- never averages or guesses across sources.
    best = max(statuses, key=lambda s: s.get("days_of_history", 0))
    if best.get("status") == "READY":
        return "퓨처 레이더: 예측 가능 (데이터 충분)"
    days = best.get("days_of_history", 0)
    required = best.get("min_required_days", 90)
    return f"퓨처 레이더: 데이터 부족 ({days}/{required}일)"


# =============================================================================
# SUPER NEWS MUSIC / SUPER NEWS DAILY -- independent single-message products
# (Kakao delivery split phase). render_kakao_digest() above stays as-is
# (legacy combined teaser, still used nowhere new); these two are the real
# production per-product messages report_delivery_v2.py sends independently,
# each idempotent under its own REPORT_TYPE.
# =============================================================================

_MUSIC_CTA_LINE = "MUSIC 전체 브리핑 →"
_DAILY_CTA_LINE = "DAILY 전체 브리핑 →"


def _assemble_with_cta(lines, cta_line):
    """Same budget-reserved CTA assembly as render_kakao_digest() -- content
    is truncated to fit AROUND the CTA, never the other way around."""
    body = "\n".join(lines).rstrip()
    cta_block = "\n\n" + cta_line
    body_budget = MAX_TEXT_LENGTH - len(cta_block)
    if len(body) > body_budget:
        body = body[: body_budget - 1].rstrip() + "…"
    text = body + cta_block
    assert len(text) <= MAX_TEXT_LENGTH  # invariant, not a runtime user-facing check
    return text


def render_music_kakao_digest(dashboard_data_v2):
    """SUPER NEWS MUSIC -- single compact Kakao message, MUSIC content ONLY
    (TikTok chart, Spotify chart, Early Signal, Music Industry news lead).
    Never includes AI/ECONOMY/SOCIETY content -- that is exclusively
    render_daily_kakao_digest()'s own product, sent/tracked independently."""
    y, m, d = dashboard_data_v2["report_date_kst"].split("-")
    news = dashboard_data_v2["news"]

    lines = [f"SUPER NEWS MUSIC | {int(m)}월 {int(d)}일", ""]
    lines.append(f"TikTok: {_tiktok_line(dashboard_data_v2['tiktok_chart'])}")
    lines.append(f"Spotify: {_spotify_line(dashboard_data_v2['spotify_chart'])}")
    lines.append(f"Signal: {_signal_line(dashboard_data_v2['intelligence'])}")
    lines.append(f"Industry: {_music_industry_lines(news)[0]}")

    return _assemble_with_cta(lines, _MUSIC_CTA_LINE)


def render_daily_kakao_digest(dashboard_data_v2):
    """SUPER NEWS DAILY -- single compact Kakao message, AI/ECONOMY/SOCIETY
    content ONLY. Never includes any Music content -- that is exclusively
    render_music_kakao_digest()'s own product, sent/tracked independently."""
    y, m, d = dashboard_data_v2["report_date_kst"].split("-")
    news = dashboard_data_v2["news"]

    lines = [f"SUPER NEWS DAILY | {int(m)}월 {int(d)}일", ""]
    for label, category in (("AI", "AI"), ("ECONOMY", "ECONOMY"), ("SOCIETY", "SOCIETY")):
        line = _first_news_line(news[category])
        lines.append(f"{label}: {line if line else '오늘 보고할 뉴스 없음'}")

    return _assemble_with_cta(lines, _DAILY_CTA_LINE)


def render_full_digest_text(dashboard_data_v2):
    """Returns ONE logical digest string (before splitting) covering: top
    news leads (AI/ECONOMY/SOCIETY), Music Industry news, Spotify/Apple
    Music chart signal, TikTok chart status, Genre/Production/Producer
    Reference Radar, K-pop/A&R relevance, Producer Intelligence (observed
    fact + actionable takeaway), and Future Radar status -- the real
    completed Music Intelligence capability, not just a teaser line.
    Callers MUST split this via report.kakao_render.split_message() before
    sending (Kakao's per-message limit is kakao.client.MAX_TEXT_LENGTH)."""
    y, m, d = dashboard_data_v2["report_date_kst"].split("-")
    news = dashboard_data_v2["news"]
    intelligence = dashboard_data_v2["intelligence"]
    music_trend = dashboard_data_v2["music_trend_intelligence"]
    producer = dashboard_data_v2["producer_intelligence"]

    lines = [f"SUPER NEWS — {y}.{m}.{d}", ""]

    lines.append("[TOP NEWS]")
    for label, category in (("AI", "AI"), ("ECONOMY", "ECONOMY"), ("SOCIETY", "SOCIETY")):
        lines.append(_news_lead_line(news[category], label))
    lines.append("")

    lines.append("[MUSIC INDUSTRY]")
    lines.extend(_music_industry_lines(news))
    lines.append("")

    lines.append("[SPOTIFY / APPLE MUSIC]")
    lines.append(f"Spotify: {_spotify_line(dashboard_data_v2['spotify_chart'])}")
    lines.append(_apple_signal_line(intelligence))
    lines.append("")

    lines.append("[TIKTOK]")
    lines.append(_tiktok_line(dashboard_data_v2["tiktok_chart"]))
    lines.append("")

    lines.append("[TREND RADAR]")
    for field in ("genre_signals", "production_notes", "producer_references", "kpop_ar_notes"):
        lines.extend(_trend_section_lines(music_trend, field))
    lines.append("")

    lines.append("[PRODUCER INTELLIGENCE]")
    lines.extend(_producer_intelligence_lines(producer))
    lines.append("")

    lines.append("[FUTURE RADAR]")
    lines.append(_future_radar_line(intelligence))

    return "\n".join(lines).rstrip()

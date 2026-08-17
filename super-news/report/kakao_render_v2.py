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

import re

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


# MUSIC KAKAO EDITORIAL QUALITY PASS (2026-08-18): budgets sized for a
# FEW complete, ungrounded-nowhere lines rather than many clipped ones --
# see render_music_kakao_digest's own docstring for the "2-3 strong items
# beat 4 weak ones" rationale. 70 chars comfortably fits a real Korean
# headline (the longest real title seen in production, Megan Thee
# Stallion's UMG/Interscope deal, is 34 real characters) without an
# ellipsis; the "why" budget is wider still since it's the actual payoff
# line a reader is here for.
_KAKAO_MUSIC_TITLE_BUDGET = 70
_KAKAO_MUSIC_WHY_BUDGET = 80
# Floor/step for the graceful why-line degradation ladder (see
# render_music_kakao_digest): a why-line may be shortened in these
# increments -- down to this floor -- before being dropped entirely.
# Never applied to a title (TITLE RULE): titles are clipped once, at
# _KAKAO_MUSIC_TITLE_BUDGET, and never touched again.
_KAKAO_MUSIC_WHY_MIN = 15
_KAKAO_MUSIC_WHY_STEP = 10
# A Producer/A&R (or Production Radar) line is genuinely optional (see
# module docstring's PRODUCER/A&R RULE) -- only included when the
# synthesis itself judged the insight HIGH confidence, never merely to
# fill a third slot. LOW/MEDIUM-confidence insights already read fine on
# the full web page (with their own confidence badge shown alongside),
# but a compact Kakao line has no room for that caveat, so an unqualified
# MEDIUM/LOW line would read as more certain than it really is.
_KAKAO_MUSIC_AR_MIN_CONFIDENCE = "HIGH"

# COPY QUALITY MICRO-FIX (2026-08-18): a real why_it_matters/reason
# sentence in this codebase overwhelmingly follows Korean's ordinary
# topic-comment structure ("X는/은 Y") -- X restates the SAME subject the
# title line one row above it already showed, so X is redundant in a
# compact digest; Y alone is normally both the real professional payoff
# AND short enough to show complete, with no ellipsis.
#
# DELIBERATELY NARROW MATCH (correctness over coverage -- see module's
# own "do not fabricate/mangle" contract): 은/는 also commonly ends an
# ordinary relative-clause VERB form in Korean (있는/하는/되는/지키는...),
# which is never a topic-clause boundary -- splitting there would produce
# a grammatically broken fragment, not a cleanly shortened sentence.
# Reliably telling a verb-ending 는 apart from a topic-marker 는 needs
# real morphological analysis this module deliberately doesn't have,
# so this only ever matches the ONE shape that's unambiguous without it:
# a topic marker directly closing a parenthetical aside (e.g. "계약(마스터권
# 유지)은", "SWIM(GLOBAL)은") -- a closing paren can never be part of a verb
# stem, so this can never misfire on a relative-clause verb ending. A
# real why-sentence without this exact shape is left completely
# untouched here (never guessed at) and falls through to the existing
# char-budget degradation ladder instead.
_KOREAN_TOPIC_CLAUSE_RE = re.compile(r"^(.{4,}?\)[은는])\s*(.+)$")
# The comment clause's own trailing meta-commentary verb ("...제시한다"/
# "...보여준다" etc. -- "presents"/"shows") is dropped too, for the same
# reason: it's the sentence's OWN framing verb, not new information: the
# noun phrase before it already carries the real professional payoff.
_KOREAN_TRAILING_META_VERB_RE = re.compile(r"(을|를)?\s*(제시한다|보여준다|시사한다|의미한다|나타낸다|반영한다)\.?\s*$")


def _compact_korean_display_text(text):
    """GENERAL Korean structural compaction, never headline-specific:
    drops a real why/reason sentence's redundant leading topic clause
    (see _KOREAN_TOPIC_CLAUSE_RE) and its own trailing meta-commentary
    verb (see _KOREAN_TRAILING_META_VERB_RE), keeping only the real
    remaining comment clause. Returns `text` completely unchanged
    whenever no topic-marker pattern is found (a short class-fallback
    phrase, an already-compact sentence, non-Korean text) or the
    resulting comment clause would be empty -- never fabricates a
    replacement, only ever removes an already-redundant prefix/suffix
    from the SAME real grounded sentence."""
    if not text:
        return text
    match = _KOREAN_TOPIC_CLAUSE_RE.match(text)
    if not match:
        return text
    remainder = match.group(2).strip()
    trimmed = _KOREAN_TRAILING_META_VERB_RE.sub("", remainder).strip().rstrip(".")
    return trimmed or remainder or text


def _kakao_signal_plain_title(signal):
    """Plain-text (never HTML-escaped -- Kakao is plain text, unlike the
    HTML renderer's own _signal_title_and_url) Korean-first title for a
    today_music_intelligence signal, reusing report.web_render_v2.
    _display_title's SAME Korean-first contract so the Kakao LEAD line
    can never show raw English while the web LEAD shows Korean."""
    from report.web_render_v2 import _display_title

    item = signal.get("headline_item")
    if item:
        return _display_title(item)
    return signal.get("fact_text") or ""


def _kakao_music_ar_block(dashboard_data_v2, lead_event_key, industry_items, title_to_event_key):
    """PRODUCER/A&R RULE: the strongest Producer/A&R (or Production
    Radar) takeaway, included ONLY when it is (a) NOT already the same
    real event as LEAD or the top Industry line -- reuses
    report.web_render_v2._synthesis_entry_event_identity, the SAME real
    event-identity resolution the web page's own cross-section dedup
    already applies -- AND (b) HIGH confidence, the synthesis's own
    real signal that this is genuinely actionable rather than a
    borderline/speculative note. Checked across the SAME three real
    pipelines report.web_render_v2._render_producer_section combines
    into the web page's own Producer/A&R section (Producer Intelligence's
    own insights, producer_references, kpop_ar_notes), in that order,
    falling back to Production Radar's production_notes only when none
    of the three has anything. Returns (headline, why) or None -- never a
    padded slot merely to reach a target item count ("2 excellent items
    are better than 3 mediocre items")."""
    from report.web_render_v2 import _synthesis_entry_event_identity

    industry_event_keys = {item.get("event_key") for item in industry_items if item.get("event_key")}

    def _qualifies(entry):
        if entry.get("confidence") != _KAKAO_MUSIC_AR_MIN_CONFIDENCE:
            return False
        event_key, _ = _synthesis_entry_event_identity(entry, title_to_event_key)
        return not (event_key and (event_key == lead_event_key or event_key in industry_event_keys))

    producer_intelligence = dashboard_data_v2.get("producer_intelligence") or {}
    if producer_intelligence.get("state") == "NORMAL":
        for insight in producer_intelligence.get("insights") or []:
            what = insight.get("what_is_moving")
            if what and _qualifies(insight):
                return what, insight.get("why_it_matters")

    trend = dashboard_data_v2.get("music_trend_intelligence") or {}
    if trend.get("state") == "NORMAL":
        for field in ("producer_references", "kpop_ar_notes", "production_notes"):
            for note in trend.get(field) or []:
                observed = note.get("observed")
                if observed and _qualifies(note):
                    return observed, note.get("interpretation")

    return None


# PROFESSIONAL-CLASS WHY FALLBACK: a deterministically backfilled
# Industry item (report.web_data_v2.professional_evidence_backfill) never
# carries a real NEWS_COMBINED selection `reason` -- rather than either
# fabricating one or leaving the item with no professional framing at
# all, this names the REAL priority CLASS report.web_data_v2.
# music_industry_priority_rank already, deterministically, assigned it
# (the same real classification the Industry ranking itself is built on)
# -- general across every item of that class, never a per-title/per-
# headline hack. Only classes 1-4 appear here because only those ever
# reach the backfill in the first place (report.web_data_v2.
# _PROFESSIONAL_BACKFILL_MAX_PRIORITY).
_KAKAO_MUSIC_INDUSTRY_CLASS_WHY = {
    1: "권리·저작권 구조 변화 신호",
    2: "플랫폼 정책 변화 신호",
    3: "AI 음악 유통 기준 변화 신호",
    4: "레이블·A&R 구조 변화 신호",
}


def _music_kakao_blocks(dashboard_data_v2):
    """Returns an ordered list of (label, title, raw_why) blocks -- LEAD,
    INDUSTRY, then A&R only if it qualifies (see _kakao_music_ar_block).
    `title` is already clipped to its own per-field budget (TITLE RULE:
    the only place a title is ever shortened, and only for a real title
    longer than the budget -- a real 2026-08-18-shaped candidate's real
    titles are comfortably under it). `raw_why` is the REAL, UNCLIPPED
    why-line text (or None) -- render_music_kakao_digest's own
    degradation ladder decides how much of it, if any, fits."""
    from report.web_data_v2 import music_industry_priority_rank
    from report.web_render_v2 import _display_title, resolve_music_lead_and_industry

    lead_signal, lead_event_key, lead_refs, industry_items, title_to_event_key = (
        resolve_music_lead_and_industry(dashboard_data_v2)
    )

    blocks = []
    if lead_signal:
        title = _clip(_kakao_signal_plain_title(lead_signal), limit=_KAKAO_MUSIC_TITLE_BUDGET)
        why = _compact_korean_display_text(lead_signal.get("why_it_matters"))
        blocks.append(("LEAD", title, why))
    else:
        blocks.append(("LEAD", "오늘 보고할 소식 없음", None))

    if industry_items:
        top = industry_items[0]
        title = _clip(_display_title(top), limit=_KAKAO_MUSIC_TITLE_BUDGET)
        why = top.get("reason") or _KAKAO_MUSIC_INDUSTRY_CLASS_WHY.get(music_industry_priority_rank(top))
        blocks.append(("INDUSTRY", title, _compact_korean_display_text(why)))
    else:
        blocks.append(("INDUSTRY", "오늘 보고할 뮤직 인더스트리 뉴스 없음", None))

    ar_block = _kakao_music_ar_block(dashboard_data_v2, lead_event_key, industry_items, title_to_event_key)
    if ar_block:
        headline, why = ar_block
        blocks.append(("A&R", _clip(headline, limit=_KAKAO_MUSIC_TITLE_BUDGET), _compact_korean_display_text(why)))

    return blocks


def _render_music_blocks(blocks, why_limits):
    """`why_limits`: parallel list, one per block -- an int caps how much
    of that block's real why-line is shown (via _clip, ellipsis only if
    genuinely needed); None drops the why-line entirely. Never touches a
    title (TITLE RULE: never clip mid-phrase) -- only the why-line, and
    only the LOWEST-priority remaining why-line, ever degrades (see
    render_music_kakao_digest's own ladder)."""
    parts = []
    for (label, title, raw_why), limit in zip(blocks, why_limits):
        block_lines = [f"{label}: {title}"]
        if raw_why and limit:
            block_lines.append(f"→ {_clip(raw_why, limit=limit)}")
        parts.append("\n".join(block_lines))
    return "\n\n".join(parts)


def render_music_kakao_digest(dashboard_data_v2):
    """SUPER NEWS MUSIC -- single compact Kakao message.

    CONTENT-IDENTITY CONSOLIDATION (2026-08-18, confirmed real defect):
    previously built its own Industry/TikTok/Spotify lines directly from
    raw dashboard_data_v2["news"]["TIKTOK"/"SPOTIFY"]["items"], completely
    bypassing the real Lead-exclusion / professional-priority-ranking /
    professional-evidence-backfill / quality-floor pipeline
    render_music_page_html_v2 already applies. Now calls the SAME real
    report.web_render_v2.resolve_music_lead_and_industry() the web page
    itself calls, so Kakao is a compact SUMMARY of the identical accepted
    editorial result, never an independently-selected second product.

    EDITORIAL QUALITY PASS (2026-08-18): 2-3 complete, unclipped items
    beat 4 mangled ones. Every included item pairs its real (never
    fabricated) headline with a real "why it matters"/"interpretation"
    field already computed elsewhere in the pipeline -- LEAD's own
    why_it_matters, a NEWS_COMBINED-selected Industry item's own
    selection `reason` (omitted, never invented, for a deterministically
    backfilled item that was never given one -- see report.web_data_v2.
    professional_evidence_backfill), and the A&R block's own real
    why_it_matters/interpretation (see _kakao_music_ar_block). No
    TikTok/Spotify chart-status line, no cross-platform Signal line --
    both are structurally low-value filler that would crowd out real
    professional content on almost every real day; a reader wanting
    chart detail already has the 'MUSIC 전체 브리핑' web link.

    TITLE RULE / real Kakao limit (kakao.client.MAX_TEXT_LENGTH -- the
    documented real limit for Kakao's 기본 텍스트 템플릿 `text` field, not
    an arbitrary internal choice): a title never degrades -- it is
    clipped once, at _KAKAO_MUSIC_TITLE_BUDGET, and never touched again.
    If the fully-assembled message (every block's why-line at its full
    real length) would exceed the real limit, ONLY why-lines degrade --
    greedily, the SINGLE why-line currently rendering the MOST characters
    is shortened first (never mid-word-brutal -- the same ellipsis-at-a-
    clean-cut _clip already uses everywhere else), in small steps down to
    a minimum floor before being dropped entirely, then the next-longest
    remaining why is degraded the same way. This is deliberately NOT
    strict priority order: a genuinely short real why-line (e.g. this
    module's own class-fallback framing for a backfilled Industry item)
    is never sacrificed just because a longer, higher-priority why hasn't
    been trimmed down yet -- keeping SOME professional framing on every
    included item beats fully erasing one item's framing to preserve
    every last character of another's. Only once every block's why is
    already fully dropped does a whole A&R block get removed entirely.
    LEAD's and INDUSTRY's own headlines are never dropped or truncated
    here."""
    y, m, d = dashboard_data_v2["report_date_kst"].split("-")
    header = f"SUPER NEWS MUSIC | {int(m)}월 {int(d)}일"
    blocks = _music_kakao_blocks(dashboard_data_v2)

    cta_block = "\n\n" + _MUSIC_CTA_LINE
    budget = MAX_TEXT_LENGTH - len(header) - len("\n\n") - len(cta_block)

    why_limits = [_KAKAO_MUSIC_WHY_BUDGET] * len(blocks)
    active_blocks = list(blocks)
    while len(_render_music_blocks(active_blocks, why_limits)) > budget:
        candidates = [
            (i, len(_clip(active_blocks[i][2], limit=why_limits[i])))
            for i in range(len(active_blocks))
            if active_blocks[i][2] and why_limits[i]
        ]
        if candidates:
            i, _ = max(candidates, key=lambda pair: pair[1])
            if why_limits[i] > _KAKAO_MUSIC_WHY_MIN:
                why_limits[i] = max(_KAKAO_MUSIC_WHY_MIN, why_limits[i] - _KAKAO_MUSIC_WHY_STEP)
            else:
                why_limits[i] = None
            continue
        if len(active_blocks) > 2 and active_blocks[-1][0] == "A&R":
            del active_blocks[-1]
            del why_limits[-1]
            continue
        break  # both headlines alone still don't fit -- nothing safe left to drop

    body = f"{header}\n\n" + _render_music_blocks(active_blocks, why_limits)
    text = body + cta_block
    assert len(text) <= MAX_TEXT_LENGTH  # invariant, not a runtime user-facing check
    return text


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

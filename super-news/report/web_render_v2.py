"""Editorial Intelligence Dashboard renderer for Report V2.1 -- CATEGORY-
CONTIGUOUS PROFESSIONAL IA REFINEMENT phase (builds on the earlier MAJOR
INFORMATION ARCHITECTURE + UI/UX REBUILD).

Consumes report.web_data_v2.build_dashboard_data_v2()'s structured facts,
INCLUDING its two cross-cutting curation surfaces (today_music_
intelligence, music_today) which are themselves real selections over
already-computed data (see that module's own docstring) -- this module
still never invents a fact, a score, or a piece of analysis text; it only
decides how much of an already-real, already-ordered list to show by
default (progressive disclosure via native <details>, zero JS) and how to
lay it out.

Additive alongside report/web_render.py (V1) -- does not modify or replace
it, and is not wired into production generation in this pass.

PRIMARY UX RULE (category-contiguous): a reader must never be forced to
switch category and then return to the previous one -- every category is
one contiguous vertical block, MUSIC first and dominant (target visible-
product emphasis: MUSIC 65-70%, AI ~15%, ECONOMY/SOCIETY ~7-10% each):

  TODAY'S MUSIC INTELLIGENCE (hero, <=5 signals, MUSIC ONLY -- AI/ECONOMY/
    SOCIETY are never mixed into the top area; that would itself violate
    the category-contiguous rule at the very top of the page)
  -> MUSIC TODAY (<=6 real cross-cutting observations)
  -> CHART PULSE (Spotify TOP10 + Viral Hot/New merged into one compact
     table -- never the same chart repeated three times; TikTok folded
     into one quiet status line, never its own top-level empty section)
  -> MUSIC INDUSTRY (edited Korean briefing, <=10 primary + progressive
     disclosure for the rest)
  -> GENRE RADAR / PRODUCTION RADAR (real synthesis, honestly labeled
     "오늘 관측" (today's observation) rather than a fabricated trend
     direction this pipeline has no day-over-day basis to claim)
  -> PRODUCER / A&R TAKEAWAYS (real Producer Intelligence insights +
     K-pop/A&R notes, actionable TRY/WATCH framing)
  -> CROSS-PLATFORM SIGNALS (cross-platform + early signal + catalog
     revival, compressed to one quiet status line when none of the three
     found anything real today -- never a giant empty section)
  -> 3-6 MONTH OUTLOOK (one honest compact line while real observation
     history remains insufficient; this pipeline has never fabricated a
     forecast and still doesn't)
  -> AI (<=8 primary + progressive disclosure)
  -> ECONOMY (<=5 primary, HARD cap, NO archive -- real overflow beyond
     the cap is never rendered here at all, not even collapsed)
  -> SOCIETY (<=5 primary, same hard-cap-no-archive contract)
  -> SOURCES (quietest, last)

Category color system (used ONLY for section labels, thin divider rules,
small badges, metrics, subtle left accents, and chart movement indicators
-- never as a full section background/fill): MUSIC deep emerald, AI
professional cobalt blue, ECONOMY muted gold/amber, SOCIETY muted
burgundy, SOURCES slate gray.

Internal pipeline status (a missing LLM run, a translation outage) is
never primary user content -- the old per-section "AI 해석 대기" notice is
gone; a fallback/UNINTERPRETED item still renders as a normal real story,
it just never editorializes about why it got there.
"""

import html
import re
from datetime import datetime, timedelta, timezone

from report.source_metadata import source_display_name
from report.validation import is_content_creation_advice, is_low_value_gossip_takeaway, is_no_evidence_meta_statement
from report.web_data_v2 import (
    _MUSIC_INDUSTRY_DOWNRANKED_PRIORITY,
    _MUSIC_INDUSTRY_UNRANKED_PRIORITY,
    known_chart_entity_keys,
    music_industry_priority_rank,
    rank_music_industry_items,
    resolve_producer_enrichment,
    select_viral_hot,
    select_viral_new,
)

_KST = timezone(timedelta(hours=9))

_STATE_UNAVAILABLE = "UNAVAILABLE"
_QUIET_MESSAGE = "오늘 선별된 주요 이슈가 없습니다."
_DEGRADED_MESSAGE = "현재 데이터 수집 문제로 이 섹션의 브리핑이 제한됩니다."
_TIKTOK_UNAVAILABLE_LINE = "TikTok 차트 · 데이터 소스 미연동"
_SPOTIFY_UNAVAILABLE_MESSAGE = "Spotify 차트 데이터가 아직 수집되지 않았습니다."
_PRODUCER_EMPTY_MESSAGE = "오늘은 근거가 충분하지 않아 프로듀서 인사이트를 생성하지 않았습니다."
_MUSIC_TREND_UNAVAILABLE_MESSAGE = "오늘은 근거가 충분하지 않아 장르·프로덕션 레이더를 생성하지 않았습니다."
_MUSIC_TREND_EMPTY_MESSAGES = {
    "genre_signals": "오늘 검증 가능한 장르 변화 신호 없음",
    "production_notes": "오늘 실제 원문에 프로덕션 특성에 대한 구체적 언급이 없습니다.",
    "kpop_ar_notes": "오늘 근거 중 케이팝/A&R과 명확히 연관된 시그널이 없습니다.",
}
_OUTLOOK_INSUFFICIENT_LINE = "장기 관측 데이터 축적 중 — 오늘은 단기 관측만 제공합니다."
_MUSIC_TODAY_EMPTY_MESSAGE = "오늘은 근거가 충분한 음악 시그널이 없습니다."

CONFIDENCE_LABELS = {"LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음"}

# MUSIC TODAY / TODAY'S INTELLIGENCE candidate `type` -> Korean kicker
# label + FACT/ANALYSIS mode badge text.
_MUSIC_CANDIDATE_LABELS = {
    "INDUSTRY_NEWS": "업계 뉴스", "VIRAL_HOT": "바이럴 급상승", "VIRAL_NEW": "신규 데뷔",
    "CROSS_PLATFORM": "교차 플랫폼", "CATALOG_REVIVAL": "카탈로그 리바이벌",
    "GENRE_SIGNAL": "장르 시그널", "PRODUCTION_SIGNAL": "프로덕션 시그널",
    "KPOP_AR": "K-pop / A&R", "PRODUCER_INSIGHT": "프로듀서 인사이트",
}
_MODE_LABELS = {"FACT": "사실", "ANALYSIS": "분석"}

# Display caps -- how much of an already-real, already-ordered list shows
# by default. Never a quality threshold: real overflow is never dropped,
# only folded into a native <details> "더 보기" disclosure (zero JS).
_AI_PRIMARY_CAP = 8
_ECON_SOCIETY_PRIMARY_CAP = 5
_MUSIC_INDUSTRY_PRIMARY_CAP = 10

# NEWSLETTER MASTHEAD NAV REBUILD: the old sticky left rail (desktop) /
# horizontally-scrolling chip strip (mobile) is gone -- a single thin
# horizontal publication nav sits just under the masthead on every
# viewport, MUSIC-primary but flat (no nested "MUSIC INTELLIGENCE" group
# label wrapping 5 sub-links anymore): MUSIC | CHARTS | INDUSTRY | RADAR |
# PRODUCER | AI | 경제 | 사회. Cross-Platform/Outlook/Sources keep real
# section anchors, just without primary nav prominence -- a reader who
# scrolls the contiguous MUSIC block still reaches them naturally.
NAV_HORIZONTAL_LINKS = (
    ("MUSIC", "today-intel"),
    ("CHARTS", "section-CHARTPULSE"),
    ("INDUSTRY", "section-INDUSTRY"),
    ("SPOTIFY", "section-SPOTIFY"),
    ("RADAR", "section-GENRE"),
    ("PRODUCER", "section-PRODUCER"),
    ("AI", "section-AI"),
    ("경제", "section-ECONOMY"),
    ("사회", "section-SOCIETY"),
)
# The literal "MUSIC INTELLIGENCE" text here is load-bearing beyond just
# being a nav badge: report/release_v2.py's real production release gate
# (_MUSIC_INTELLIGENCE_MARKER = ">MUSIC INTELLIGENCE<") scans the
# generated page for this exact substring as its own real "is this really
# the MUSIC-primary redesigned page, not a stale pre-redesign one" signal
# -- verify_local_v2_dashboard/verify_external_v2_dashboard both fail
# closed without it. It must render as an element whose text content is
# EXACTLY "MUSIC INTELLIGENCE" (nothing else inside the tag). Do not
# rename/remove without also updating that gate's own marker (and
# re-verifying the real release flow).
NAV_MUSIC_INTELLIGENCE_BADGE = "MUSIC INTELLIGENCE"


def _e(text):
    return html.escape(text) if text else ""


def _source_label(source_name):
    return source_display_name(source_name)


def _region_label(region):
    if not region:
        return None
    return {"GLOBAL": "Global"}.get(region, region)


def _format_date_kst(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string).astimezone(_KST).strftime("%Y.%m.%d")
    except ValueError:
        return None


def _format_date_kst_korean(iso_string):
    """Korean long-form date (YYYY년 M월 D일, no zero-padding) for
    narrative prose -- distinct from _format_date_kst's compact numeric
    YYYY.MM.DD, which is used for data-label contexts."""
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string).astimezone(_KST)
    except ValueError:
        return None
    return f"{dt.year}년 {dt.month}월 {dt.day}일"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DETAILS_TAG_RE = re.compile(r"<details\b[^>]*>|</details\s*>", re.IGNORECASE)
# ~500 non-whitespace Korean characters/minute -- a common editorial rule
# of thumb for mixed Korean/English news prose read at a normal pace.
_READING_CHARS_PER_MINUTE = 500


def _strip_collapsed_details(html_fragment):
    """DENSITY TARGET: the default QUICK READ never includes text a
    reader has to click to reveal -- every real <details> block (evidence
    citations, "더 보기" overflow) is progressive disclosure by design,
    so its content (nesting-aware: a `<details>` can itself contain
    nested `<details>`, e.g. overflow cards carrying their own evidence
    disclosure) is excluded from the reading-time estimate entirely."""
    parts = []
    depth = 0
    last_end = 0
    for m in _DETAILS_TAG_RE.finditer(html_fragment):
        is_open = m.group(0)[1] != "/"
        if depth == 0 and is_open:
            parts.append(html_fragment[last_end:m.start()])
        if is_open:
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                last_end = m.end()
    parts.append(html_fragment[last_end:])
    return "".join(parts)


def _estimate_reading_minutes(*html_fragments):
    """NEW PRODUCT IDENTITY: "5 MIN READ" must never be a fake fixed
    number -- it is deterministically derived from the REAL visible
    editorial text this exact page renders by default (every section's
    real headline/summary/interpretation/bullet text that's on the page
    without clicking anything -- see _strip_collapsed_details -- tags
    stripped, HTML entities unescaped), every time the page is generated.
    Same real content in -> same real minute count out; never
    randomized, never hardcoded."""
    visible = _strip_collapsed_details("".join(html_fragments))
    text = _TAG_RE.sub(" ", visible)
    text = html.unescape(text)
    char_count = len(_WS_RE.sub("", text))
    return max(1, round(char_count / _READING_CHARS_PER_MINUTE))


def _link_html(source_url, label="기사 보기 →", css_class="item-link"):
    """MOBILE LINK FUNCTIONAL BUG FIX: every real external article link
    gets target="_blank" (opens independently of this page/any embedding
    iframe -- a real, confirmed cause of "links don't open" on mobile,
    where the link would otherwise navigate the current viewport away
    from SUPER NEWS with no way back) plus rel="noopener noreferrer"
    (already present) for safety. Never rendered at all when no real
    source_url exists -- never a fabricated link."""
    if not source_url:
        return ""
    safe_url = html.escape(source_url, quote=True)
    return f'<a class="{css_class}" href="{safe_url}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _headline_link_open(source_url):
    """MOBILE LINK FUNCTIONAL BUG FIX: the headline itself becomes a real
    tappable link (large mobile touch target) whenever a real source_url
    exists -- returns the opening `<a ...>` tag only; the caller supplies
    its own closing `</a>` right after the headline text. Never rendered
    when no real source_url exists (the headline just stays plain text)."""
    if not source_url:
        return ""
    safe_url = html.escape(source_url, quote=True)
    return f'<a class="headline-link" href="{safe_url}" target="_blank" rel="noopener noreferrer">'


def _linked_headline_html(tag, css_class, title_text, source_url):
    """MOBILE LINK FUNCTIONAL BUG FIX: wraps an already-escaped headline
    string in a real tappable link when a real source_url exists (see
    _headline_link_open) -- the plain <h3>/<h4> tag itself never changes,
    only whether its text is additionally wrapped in a real <a>."""
    open_a = _headline_link_open(source_url)
    close_a = "</a>" if open_a else ""
    return f'<{tag} class="{css_class}">{open_a}{title_text}{close_a}</{tag}>'


_DISPLAYABLE_TRANSLATION_STATUSES = ("TRANSLATED", "NOT_REQUIRED")

_HANGUL_RE = re.compile(r"[가-힣]")


def _display_title(item):
    """Korean-first UI contract: once a real translation succeeded, or the
    source was already sufficiently Korean, the reader-facing headline is
    the Korean text -- never the raw original in that case. Now applies to
    EVERY news category, Music Industry (TikTok/Spotify) included (MAJOR
    IA REBUILD phase) -- proper nouns (artist/track/company names) are
    never altered by the translation layer itself, see report/
    translation.py. item["title"] is still the fallback for a real
    UNAVAILABLE/FAILED translation outcome -- news is never hidden or
    replaced with a blank field just because translation didn't succeed."""
    if item.get("translation_status") in _DISPLAYABLE_TRANSLATION_STATUSES and item.get("ko_title"):
        return item["ko_title"]
    return item["title"]


def _has_korean_title(item):
    """DAILY KOREAN-READINESS GUARD (AI/ECONOMY/SOCIETY only -- see
    _render_compact_news_section's `korean_lead_guard` param): checks the
    ACTUAL text _display_title would render, not an internal status flag,
    so it stays correct regardless of how translation-status bookkeeping
    evolves. An untranslated raw-English headline must never become the
    Level A/B lead on a DAILY card -- a reader's first real impression of
    a section must never be raw English while a real Korean-ready
    alternative exists."""
    return bool(_HANGUL_RE.search(_display_title(item)))


# PERMANENT KOREAN-FIRST DELIVERY GATE (2026-08-22, production incident
# follow-up -- see report/translation_claude_cli.py's own permanent-cache-
# poisoning fix). This is the final, shared reader-facing checkpoint DAILY
# and MUSIC Kakao rendering both call immediately before a title is
# allowed into a real delivery: a normal English news sentence must never
# silently pass as "Korean-first" just because translation failed (any
# reason -- not just the specific cache-poisoning bug this pass fixed).
# Deterministic, no LLM call: a real Korean translation always contains at
# least one Hangul character (_HANGUL_RE); a raw, never-translated English
# proper noun (artist/company/album/song name -- "BTS", "HYBE", "Take
# Two") essentially never contains an ordinary English function word,
# while an untranslated ENGLISH SENTENCE/HEADLINE (what must be blocked)
# almost always does ("the", "a", "is", "in", "and", ...). This is a
# necessarily approximate heuristic, not a language-quality judgment --
# false positives (a legitimate multi-word title coincidentally read as a
# sentence) degrade to this item's own existing "no report today" empty
# state, never a crash, never a fabricated Korean string.
_ENGLISH_SENTENCE_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "of", "and", "or", "but", "with", "from", "by",
    "that", "this", "these", "those", "has", "have", "had", "will", "would",
    "its", "their", "his", "her", "he", "she", "they", "we", "you", "i",
    "not", "no", "as", "for", "after", "before", "over", "under", "into",
    "about", "than", "then", "when", "while", "who", "which", "what",
    "says", "said", "amid",
})

_ENGLISH_WORD_RE = re.compile(r"[A-Za-z']+")


def _is_untranslated_english_sentence(text):
    """True when `text` reads as an ordinary English sentence/headline
    (contains a common English function word) rather than a bare proper
    noun/title fragment. See is_korean_first_ready's own module comment
    for the full reasoning."""
    words = _ENGLISH_WORD_RE.findall(text.lower())
    return any(word in _ENGLISH_SENTENCE_STOPWORDS for word in words)


def is_korean_first_text_ready(text):
    """Text-level form of the gate, for a reader-facing string that isn't
    always backed by an item dict (e.g. a MUSIC signal's own `fact_text`,
    an LLM-synthesized sentence with no ko_title/translation_status at
    all). True when `text` is either real Korean, or acceptable English-
    only content (a proper noun/title fragment). See is_korean_first_ready
    for the item-dict form and the full reasoning."""
    if not text:
        return True  # nothing to gate -- caller's own empty-state applies
    if _HANGUL_RE.search(text):
        return True
    return not _is_untranslated_english_sentence(text)


def is_korean_first_ready(item):
    """The shared DAILY/MUSIC pre-delivery Korean-first gate: True when
    _display_title(item) is either real Korean text, or acceptable
    English-only content (a proper noun/title fragment -- BTS, HYBE,
    Spotify, Billboard, an album/song title, a company name). False for an
    ordinary untranslated English sentence/headline that should have been
    Korean -- callers must treat False the same as "no usable item here"
    (their own existing empty-state fallback), never render the raw text
    as if it were a successful Korean-first result."""
    return is_korean_first_text_ready(_display_title(item))


def _has_displayable_korean_content(item):
    """FINAL GOAL PASS (2026-08-17, confirmed real defect): the Korean-
    readiness guard above only ever checked the TITLE -- a real AI item
    whose title is an untranslated English headline (no free/non-paid
    title-translation path exists) but whose News Intelligence bullets
    (what_happened/why_it_matters/what_to_watch -- always real, LLM-
    synthesized KOREAN text, see report.web_render_v2._ed_bullets_html)
    were fully available still got forced to the bare compact tier,
    silently throwing away real, already-generated Korean content and
    leaving nothing but a raw English link -- worse than the guard's own
    intent (never a raw-English-only card), not better. An item counts as
    Korean-ready here when EITHER its title is Korean OR it has real
    AI-intelligence-sourced Korean bullets to show -- only an item with
    NEITHER is true raw fallback and stays compact-only."""
    return _has_korean_title(item) or item.get("ai_intelligence_status") == "AVAILABLE"


_RHETORICAL_QUESTION_ONLY_RE = re.compile(r"^(?:[^.!?]*[?？]\s*)+$")


def _is_rhetorical_question_only(text):
    """SUMMARY QUALITY (CURRENT-CACHE SAFETY, content-quality hardening
    pass, 2026-08-17): True when `text` is ENTIRELY composed of one or
    more question-ended sentences with no real declarative sentence
    anywhere in it -- a literal '~까요?' teaser-question translation
    (confirmed real example: "워터마킹은 실제로 어떻게 작동할까요? 편집으로
    숨길 수 있을까요?") reads awkwardly as a news summary line. See
    report.translation_anthropic's own system-prompt fix for the
    forward-looking half of this guard (future translations normalize
    this at the source, never producing this shape to begin with); this
    detector is the render-time half, for an ALREADY-cached translation.
    A text with even one real non-question sentence never matches."""
    if not text:
        return False
    return bool(_RHETORICAL_QUESTION_ONLY_RE.match(text.strip()))


def _display_snippet(item):
    if item.get("snippet_translation_status") in _DISPLAYABLE_TRANSLATION_STATUSES and item.get("ko_snippet"):
        snippet = item["ko_snippet"]
    else:
        snippet = item.get("snippet")
    # CURRENT-CACHE SAFETY: never shown when it's purely a rhetorical-
    # question-style translation -- suppressed, never rewritten/
    # fabricated, falling back to the card's own real declarative
    # bullets (see _is_rhetorical_question_only's own docstring).
    if snippet and _is_rhetorical_question_only(snippet):
        return None
    return snippet


def _item_byline(item):
    bits = []
    if item.get("source_name"):
        bits.append(_e(_source_label(item["source_name"])))
    published = _format_date_kst(item.get("published_at"))
    if published:
        bits.append(f'<span class="num">{published}</span>')
    if not bits:
        return ""
    return f'<p class="item-byline">{" · ".join(bits)}</p>'


def _news_state_message(state):
    if state == "DEGRADED":
        return _DEGRADED_MESSAGE, "state-degraded"
    if state == "QUIET":
        return _QUIET_MESSAGE, "state-quiet"
    return None, None


def _evidence_disclosure_html(evidence):
    """Real evidence chips, collapsed by default behind a native <details>
    toggle (zero JS) -- a reader can still open and verify every citation,
    it just never pushes the primary observed/interpretation text down
    the page by default."""
    if not evidence:
        return ""
    chips = "".join(f'<span class="evidence-chip">{_e(ev["summary"])}</span>' for ev in evidence)
    return f'<details class="evidence-disclosure"><summary>근거 보기 ({len(evidence)}개)</summary>{chips}</details>'


def _category_transition_html(css_key, label, subtitle=None):
    """A publication-style section break marking a real category boundary
    -- deliberately stronger than an ordinary .block-head so a reader
    scrolling quickly can tell "I've left MUSIC and entered {label}"
    without reading anything. Rendered ONCE per category group, never
    once per individual section within that group."""
    sub_html = f'<p class="transition-sub">{_e(subtitle)}</p>' if subtitle else ""
    return (
        f'<div class="category-transition transition-{css_key}">'
        f'<span class="transition-label">{_e(label)}</span>{sub_html}</div>'
    )


def _pulse_delta_cell(entry):
    """CHART PULSE MUST FEEL LIKE DATA: the Δ column is a real signed
    number derived from real rank history, or a plain "—" when no real
    prior rank exists to diff against (NEW/FIRST OBSERVATION) -- never a
    fabricated or inferred delta."""
    delta = entry.get("rank_delta")
    if delta and delta > 0:
        return f'<span class="pulse-delta pulse-delta-up num">▲{delta}</span>'
    if delta and delta < 0:
        return f'<span class="pulse-delta pulse-delta-down num">▼{-delta}</span>'
    return '<span class="pulse-delta num">—</span>'


def _pulse_status_badge(entry):
    if entry.get("status") == "FIRST_OBSERVED":
        return '<span class="badge badge-first">최초 관측</span>'
    if entry["is_new"]:
        return '<span class="badge badge-new">신규</span>'
    delta = entry.get("rank_delta")
    if delta and delta > 0:
        return '<span class="badge badge-up">상승</span>'
    if delta and delta < 0:
        return '<span class="badge badge-down">하락</span>'
    return '<span class="badge badge-flat">보합</span>'


_STYLE = """
:root {
  color-scheme: light;
  --bg: #f7f6f2;
  --surface: #ffffff;
  --ink: #17181c;
  --ink-soft: rgba(23,24,28,0.66);
  --ink-faint: rgba(23,24,28,0.44);
  --rule: rgba(23,24,28,0.12);
  --chip-bg: rgba(23,24,28,0.06);
  --masthead: #1f3a5f;
  --hue-music: #0f6e4f;
  --hue-music-tint: rgba(15,110,79,0.055);
  --hue-music-tint2: #0d7a72;
  --hue-ai: #2f5aa8;
  --hue-economy: #97730f;
  --hue-society: #8b3a4a;
  --hue-sources: #6b7280;
  --bad-down: #b91c1c;
  --good-up: #15803d;
  --new-badge: #1d4ed8;
}
/* REFERENCE DESIGN BRAND: SUPER NEWS MUSIC/DAILY are an intentional light
   editorial publication, not a system-theme-following app -- no automatic
   @media (prefers-color-scheme: dark) switch (a real defect found in a
   prior pass: it silently overrode the reference's light neutral
   background/dark-navy-on-white brand whenever the OS was in dark mode). */
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  background: var(--bg); color: var(--ink);
  margin: 0; padding: 0 0 64px; line-height: 1.7; font-size: 17px;
}
.num { font-family: Georgia, "Times New Roman", ui-serif, serif; font-variant-numeric: tabular-nums; }
a { color: inherit; }

.masthead { max-width: 1280px; margin: 0 auto; padding: 24px 20px 0; border-bottom: 1px solid var(--rule); padding-bottom: 16px; }
.brand-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
/* BRAND: SUPER NEWS is the publication's own name and must be the first
   thing the eye notices in the masthead -- substantially larger than the
   date below it, which stays clearly secondary. The edition word (MUSIC/
   DAILY) picks up a restrained accent color via .brand-edition-*; SUPER
   NEWS itself stays the single navy brand color. */
.brand { font-family: Georgia, "Times New Roman", ui-serif, serif; font-size: clamp(1.8rem, 2.6vw, 2.2rem);
  font-weight: 800; letter-spacing: -0.01em; line-height: 1.15; color: var(--masthead); }
.brand-edition-music { color: var(--hue-music); }
.brand-edition-daily { color: var(--hue-ai); }
.tagline { font-size: 0.86rem; letter-spacing: 0.01em; color: var(--ink-soft); margin-top: 2px; }
.meta-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
.date { font-family: Georgia, "Times New Roman", ui-serif, serif; font-size: clamp(1.05rem, 1.6vw, 1.3rem);
  font-weight: 700; margin: 0; letter-spacing: -0.01em; color: var(--ink-soft); }
.read-time { font-size: 0.76rem; color: var(--ink-faint); letter-spacing: 0.02em; white-space: nowrap; }

/* ---- thin horizontal publication nav, just under the masthead ---- */
.pub-nav { max-width: 1280px; margin: 0 auto; padding: 10px 20px; display: flex; align-items: center;
  gap: 18px; border-bottom: 1px solid var(--rule); overflow-x: auto; }
.pub-nav-badge { flex: 0 0 auto; font-size: 0.64rem; font-weight: 800; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--hue-music); white-space: nowrap; }
.pub-nav-links { display: flex; gap: 2px; flex-wrap: nowrap; white-space: nowrap; }
.pub-nav-link { display: inline-block; padding: 5px 11px; font-size: 0.8rem; font-weight: 600;
  color: var(--ink-soft); text-decoration: none; border-radius: 999px; }
.pub-nav-link:hover, .pub-nav-link:focus-visible { color: var(--ink); background: var(--chip-bg); }

/* ---- TODAY'S MUSIC INTELLIGENCE (Level 1, MUSIC ONLY) -- true newsletter
   lead structure: a single full-width LEAD STORY, then a stacked TODAY IN
   MUSIC list of at most 3 compact secondary signals underneath -- never a
   side-by-side grid (that's the old dashboard-hero layout this rebuild
   replaces, and the source of the large-blank-space defect it had). ---- */
.today-intel { max-width: 900px; margin: 24px auto 0; padding: 0 20px; }
.today-intel h1 { font-size: 0.78rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.12em;
  color: var(--hue-music); margin: 0 0 16px; }

/* ---- REFERENCE DESIGN: the lead story gets the same premium
   rounded-white-card treatment as the ed-card article cards below it
   (large radius, thin border, subtle shadow, generous padding) -- same
   markup/classes as before (zero test impact), restyled only. ---- */
.lead-story { background: var(--surface); border: 1px solid var(--rule); border-radius: 14px;
  box-shadow: 0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.05);
  padding: 28px; }
.lead-kicker { display: inline-block; font-size: 0.74rem; font-weight: 700; letter-spacing: 0.02em;
  color: var(--masthead); background: var(--chip-bg); padding: 4px 12px; border-radius: 999px; margin-bottom: 14px; }
.lead-title { font-weight: 800; color: var(--masthead);
  font-size: clamp(1.5rem, 3.4vw, 2.1rem); line-height: 1.3; margin: 0 0 14px; }
.lead-title a { text-decoration: none; }
.lead-title a:hover { text-decoration: underline; }
/* Editorial imagery is for importance signaling, not decoration: fixed
   aspect-ratio (no layout shift while it loads) and a hard max-height so
   it reinforces the headline instead of overpowering WHY IT MATTERS /
   PRODUCER IMPACT below it. */
.lead-image-wrap { margin: 4px 0 18px; border-radius: 10px; overflow: hidden; aspect-ratio: 16 / 9;
  max-height: 420px; background: var(--chip-bg); }
.lead-image { display: block; width: 100%; height: 100%; object-fit: cover; }
.lead-summary { font-size: 1.02rem; color: var(--ink-soft); line-height: 1.65; margin: 0 0 16px; max-width: 68ch; }
.lead-why-row { display: flex; gap: 12px; align-items: flex-start; margin: 0; padding: 9px 0;
  border-top: 1px dashed var(--rule); font-size: 0.95rem; }
.lead-why-row:first-of-type { border-top: 1px solid var(--rule); }
.lead-why-label { flex: 0 0 auto; width: 22px; height: 22px; margin-top: 1px; border-radius: 999px;
  background: var(--masthead); color: #fff; font-size: 0; text-transform: none; text-align: center; }
.lead-why-label::before { content: "✓"; font-size: 0.68rem; font-weight: 800; line-height: 22px; }
.lead-why-row p { margin: 0; }
.lead-meta { margin-top: 18px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.lead-meta .item-byline { margin: 0; }
.lead-meta .item-link { display: inline-flex; align-items: center; gap: 6px; background: var(--masthead);
  color: #fff; font-weight: 700; font-size: 0.88rem; padding: 11px 22px; border-radius: 999px;
  min-height: 0; line-height: 1.2; }
.lead-meta .item-link:hover { opacity: 0.88; text-decoration: none; }

.today-secondary { margin-top: 28px; border-top: 1px solid var(--rule); padding-top: 18px; }
.today-secondary-head { font-size: 0.7rem; font-weight: 800; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--ink-faint); margin: 0 0 10px; }
.today-secondary-list { display: flex; flex-direction: column; gap: 0; }
.signal-card { display: flex; gap: 12px; align-items: flex-start; padding: 14px 0; border-top: 1px solid var(--rule); }
.today-secondary-list .signal-card:first-child { border-top: none; padding-top: 0; }
.signal-thumb { flex: 0 0 auto; width: 64px; height: 64px; border-radius: 4px; object-fit: cover; background: var(--chip-bg); }
.signal-body { flex: 1 1 auto; min-width: 0; }
.signal-kicker { display: block; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--hue-music); margin-bottom: 4px; }
.signal-title { font-weight: 700; font-size: 1rem; line-height: 1.4; margin: 0; }
.signal-title a { text-decoration: none; }
.signal-title a:hover { text-decoration: underline; }
.signal-meaning { font-size: 0.88rem; color: var(--ink-soft); margin: 5px 0 0; }

.main { max-width: 900px; margin: 0 auto; padding: 0 20px; }
section.block { margin-bottom: 34px; padding-bottom: 24px; border-bottom: 1px solid var(--rule); }
section.block:last-of-type { border-bottom: none; }
.block-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px; }
/* REFERENCE DESIGN: a short accent bar beside every primary section
   heading (currentColor -- reuses whichever per-category h2 color already
   applies below, never a separate color system). */
.block-head h2 { font-size: 0.98rem; font-weight: 800; margin: 0; letter-spacing: 0.01em;
  display: inline-flex; align-items: center; gap: 8px; }
.block-head h2::before { content: ""; display: inline-block; width: 4px; height: 15px;
  background: currentColor; border-radius: 2px; }
.block-head .block-sub { font-size: 0.78rem; color: var(--ink-faint); }
.block-MUSICTODAY .block-head h2, .block-CHARTPULSE .block-head h2, .block-INDUSTRY .block-head h2,
.block-GENRE .block-head h2, .block-PRODUCTION .block-head h2, .block-PRODUCER .block-head h2,
.block-SIGNALS .block-head h2, .block-OUTLOOK .block-head h2 { color: var(--hue-music); }
.block-AI .block-head h2 { color: var(--hue-ai); }
.block-ECONOMY .block-head h2 { color: var(--hue-economy); }
.block-SOCIETY .block-head h2 { color: var(--hue-society); }
.block-SOURCES .block-head h2 { color: var(--hue-sources); }
/* MUSIC is the primary domain: its section headers stay Level-2/3 sized;
   AI drops one notch; ECONOMY/SOCIETY drop further still (Level 5) -- see
   this module's own docstring on the target visible-product ratio. */
.block-AI .block-head h2 { font-size: 0.86rem; }
.block-ECONOMY .block-head h2, .block-SOCIETY .block-head h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }
.block-SOURCES .block-head h2 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; }
.block-ECONOMY, .block-SOCIETY { margin-bottom: 24px; padding-bottom: 16px; }
.block-SOURCES { margin-bottom: 0; }

/* ---- category-transition divider: a reader scrolling out of MUSIC must
   immediately register "I have left MUSIC" -- this is deliberately a
   stronger visual break than an ordinary .block-head (bigger rule,
   uppercase eyebrow, more surrounding air), placed ONCE before each
   non-MUSIC category group, never repeated per-section within a group. */
.category-transition { margin: 8px 0 26px; padding-top: 22px; border-top: 3px solid var(--divider-color, var(--ink)); }
.category-transition .transition-label { display: block; font-size: 1.05rem; font-weight: 800; letter-spacing: 0.02em;
  color: var(--divider-color, var(--ink)); margin: 0; }
.category-transition .transition-sub { font-size: 0.8rem; color: var(--ink-faint); margin: 4px 0 0; }
.category-transition.transition-AI { --divider-color: var(--hue-ai); }
.category-transition.transition-ECONOMY { --divider-color: var(--hue-economy); }
.category-transition.transition-SOCIETY { --divider-color: var(--hue-society); }

/* ---- QUIET section variant: no real signal today, or a capability not
   yet connected at all -- recedes instead of competing with sections
   that carry real content. ---- */
section.block.block-quiet { margin-bottom: 20px; padding-bottom: 14px; border-bottom: none; }
section.block.block-quiet .block-head { margin-bottom: 6px; }
section.block.block-quiet .block-head h2 { font-size: 0.72rem; font-weight: 700; color: var(--ink-faint) !important; }
section.block.block-quiet .state-message { font-size: 0.86rem; }
.quiet-line { font-size: 0.86rem; color: var(--ink-faint); padding: 2px 0; margin: 0; }

/* ---- MUSIC TODAY ---- */
.music-today-list { display: flex; flex-direction: column; gap: 0; }
/* Editorial rule-separated treatment (not a filled box) -- "reduce
   repeated card styling," boxes are reserved for Producer/A&R Takeaways
   below, where a real actionable item benefits from stronger visual
   separation. */
.mt-card { padding: 14px 0 14px 16px; border-top: 1px solid var(--rule); border-left: 3px solid var(--hue-music); }
.music-today-list .mt-card:first-child { border-top: none; }
.mt-kicker-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mt-type { font-size: 0.68rem; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase; color: var(--hue-music); }
.mode-badge { font-size: 0.62rem; font-weight: 700; letter-spacing: 0.04em; padding: 1px 6px; border-radius: 3px; }
.mode-FACT { background: var(--chip-bg); color: var(--ink-faint); }
.mode-ANALYSIS { background: rgba(15,110,79,0.1); color: var(--hue-music); }
.mt-headline { font-size: 1.05rem; font-weight: 700; margin: 0 0 6px; line-height: 1.4; }
.mt-fact { font-size: 0.98rem; font-weight: 600; margin: 0 0 6px; line-height: 1.5; }
.mt-why, .mt-implication { font-size: 0.9rem; color: var(--ink-soft); margin: 6px 0 0; padding-left: 12px; border-left: 2px solid var(--rule); }
.mt-why b, .mt-implication b { color: var(--ink); font-weight: 700; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; display: block; margin-bottom: 2px; }

/* ---- CHART PULSE: a light data terminal, not another article list --
   monospace numerals, an explicit header row, generous letter-spacing on
   labels -- deliberately reads as DATA, distinct from the newsletter
   prose everywhere else on the page. ---- */
.pulse-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.pulse-table thead th { text-align: left; font-size: 0.64rem; font-weight: 800; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-faint); padding: 0 6px 8px; border-bottom: 1px solid var(--rule); }
.pulse-table thead th.pulse-rank, .pulse-table thead th.pulse-delta-head { text-align: right; }
.pulse-table thead th.pulse-status-head { text-align: right; }
.pulse-table tbody tr { border-top: 1px solid var(--rule); }
.pulse-table tbody tr:first-child { border-top: none; }
.pulse-table td { padding: 9px 6px; vertical-align: baseline; }
.pulse-rank { font-weight: 700; opacity: 0.55; font-size: 1rem; width: 2em; text-align: right; }
.pulse-track { font-weight: 600; }
.pulse-delta-cell { text-align: right; white-space: nowrap; }
.pulse-delta { font-size: 0.86rem; font-weight: 700; color: var(--ink-faint); }
.pulse-delta-up { color: var(--good-up); }
.pulse-delta-down { color: var(--bad-down); }
.pulse-badges { text-align: right; white-space: nowrap; }
.pulse-badges .badge { margin-left: 4px; }
.badge { font-size: 0.72rem; font-weight: 700; padding: 2px 7px; border-radius: 999px; white-space: nowrap; display: inline-block; }
.badge-new { background: rgba(29,78,216,0.12); color: var(--new-badge); }
.badge-first { background: var(--chip-bg); color: var(--ink-faint); }
.badge-up { background: rgba(21,128,61,0.12); color: var(--good-up); }
.badge-down { background: rgba(185,28,28,0.12); color: var(--bad-down); }
.badge-flat { background: var(--chip-bg); color: var(--ink-faint); }
.badge-cross { background: rgba(15,110,79,0.12); color: var(--hue-music); }
.pulse-narrative { font-size: 0.9rem; color: var(--ink-soft); margin: 10px 0 0; }
.pulse-status { font-size: 0.82rem; color: var(--ink-faint); margin: 8px 0 0; }

/* ---- compact news cards (AI / ECONOMY / SOCIETY / MUSIC INDUSTRY) ---- */
.news-list { display: flex; flex-direction: column; gap: 0; }
.news-card { padding: 14px 0; border-top: 1px solid var(--rule); }
.news-list .news-card:first-child { border-top: none; }
.news-list .ed-card:first-child { border-top: 1px solid var(--rule); }

/* ---- FINAL VISUAL PASS: premium bounded card, Music Industry ONLY --
   AI/Economy/Society intentionally keep the lighter rule-separated
   treatment above; this is a scoped addition, not a global redesign. ---- */
#section-INDUSTRY .news-list { gap: 12px; }
#section-INDUSTRY .news-card {
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 16px;
}
.news-corrob { display: inline-block; font-size: 0.72rem; font-weight: 600; color: var(--ink-faint); margin-bottom: 4px; }
.news-title { font-weight: 700; font-size: 0.98rem; line-height: 1.4; margin: 0 0 4px; }
.item-byline { font-size: 0.78rem; color: var(--ink-faint); margin: 2px 0 6px; }
.news-summary { font-size: 0.92rem; color: var(--ink-soft); margin: 0 0 4px; max-width: 68ch;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.news-why { font-size: 0.88rem; color: var(--ink-soft); margin: 4px 0 6px; max-width: 68ch;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.news-why b { color: var(--ink); font-weight: 700; }
.item-link { display: inline-block; font-size: 0.85rem; color: var(--masthead); text-decoration: none; min-height: 26px; line-height: 26px; }
.item-link:hover { text-decoration: underline; }

/* ---- Semantic story-type chip (Music Industry only, EDITORIAL INTEGRITY FIX) ---- */
.story-type-chip { display: inline-block; font-size: 0.62rem; font-weight: 800; letter-spacing: 0.06em;
  text-transform: uppercase; padding: 2px 7px; border-radius: 3px; background: var(--chip-bg);
  margin: 0 0 6px; }
.story-type-emerald { color: var(--hue-music); }
.story-type-teal { color: var(--hue-music-tint2, var(--hue-music)); }
.story-type-cobalt { color: var(--hue-ai); }
.story-type-amber { color: var(--hue-economy); }

/* ---- Level C: COMPACT BRIEF (lower-priority, no summary, no container) ---- */
.news-compact { padding: 9px 0; }
.news-title-compact { font-size: 0.9rem; font-weight: 600; line-height: 1.4; margin: 0 0 2px; }
.news-compact .item-byline { margin: 0 0 2px; }
.news-compact .item-link { font-size: 0.78rem; min-height: 0; line-height: 1.4; }

/* ---- Music Industry keeps compact (C) tier lighter than its A/B
   editorial cards above. ---- */
#section-INDUSTRY .news-compact { padding: 10px 14px; }

/* ---- FINAL DENSITY PASS: ECONOMY/SOCIETY ultra-compact awareness row --
   exactly two real lines (headline, then source · date · link), never a
   card/box -- a real single flex row on desktop where the headline is
   short enough to share space with its metadata, wrapping naturally
   (never forced) when it isn't; always two stacked lines on mobile (see
   the max-width:600px override below). Deliberately its own visual
   contract, distinct from Level A/B/C, since ECONOMY/SOCIETY are a
   peripheral awareness feed here, not a tiered newsletter section. ---- */
.news-row-compact { padding: 7px 0; border-top: 1px solid var(--rule); display: flex; flex-wrap: wrap;
  align-items: baseline; justify-content: space-between; column-gap: 16px; row-gap: 1px; }
.news-list .news-row-compact:first-child { border-top: none; }
.news-title-ultra { font-size: 0.92rem; font-weight: 600; line-height: 1.4; margin: 0; flex: 1 1 auto; min-width: 60%; }
.news-meta-line { font-size: 0.76rem; color: var(--ink-faint); margin: 0; flex: 0 0 auto; white-space: nowrap; }
.news-meta-line a { color: var(--masthead); text-decoration: none; }
.news-meta-line a:hover { text-decoration: underline; }
@media (max-width: 600px) {
  .news-row-compact { flex-direction: column; align-items: flex-start; row-gap: 2px; }
  .news-meta-line { white-space: normal; }
}

/* ---- REFERENCE EDITORIAL CARD (Level A/B: LEAD / IMPORTANT tiers) --
   image left (desktop) / top (mobile), category pill, large headline,
   summary, up to 3 real supported key points, prominent CTA button. See
   module docstring's REFERENCE DESIGN note. Reuses --masthead (already
   theme-aware light/dark) as the single navy accent -- no new color
   tokens needed. ---- */
/* DESKTOP: the media wrapper OWNS the image frame (align-items: stretch
   on .ed-card makes .ed-card-media match the card's full content height;
   the image itself has NO aspect-ratio constraint and simply fills that
   wrapper via width/height: 100% + object-fit: cover) -- so the photo
   fills the entire left-side slot vertically, never a smaller fixed-
   ratio box leaving blank space beneath it. Cropping stays proportional
   (object-fit: cover), never stretched/distorted. */
.ed-card { background: var(--surface); border: 1px solid var(--rule); border-radius: 14px;
  box-shadow: 0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.05);
  padding: 24px; margin: 0 0 20px; display: flex; gap: 28px; align-items: stretch; }
.ed-card-media { flex: 0 0 42%; max-width: 420px; align-self: stretch;
  overflow: hidden; border-radius: 10px; background: var(--chip-bg); }
.ed-card-media img { display: block; width: 100%; height: 100%; object-fit: cover;
  object-position: center; border-radius: 0; }
.ed-card-body { flex: 1 1 auto; min-width: 0; }
/* DAILY ONLY (page-daily): article photos get more visual presence --
   a wider image column (48%/480px vs MUSIC Industry's 42%/420px) --
   still fills its full frame via the same stretched-wrapper mechanism
   above; only the column WIDTH differs here. Scoped to .page-daily so
   MUSIC's Music Industry cards and hero (its own separate .lead-image/
   .lead-media rules) never inherit this. Mobile is unaffected -- the
   existing top-image/16:9 mobile rule below already applies identically
   to every page. */
.page-daily .ed-card-media { flex: 0 0 48%; max-width: 480px; }
.ed-pill { display: inline-block; font-size: 0.74rem; font-weight: 700; padding: 4px 12px;
  border-radius: 999px; background: var(--chip-bg); color: var(--masthead); margin: 0 0 12px; }
.ed-headline { font-weight: 800; line-height: 1.35; color: var(--masthead); margin: 0 0 10px; }
.ed-headline a { color: inherit; text-decoration: none; }
.ed-headline a:hover { text-decoration: underline; }
.ed-card-lead .ed-headline { font-size: clamp(1.35rem, 2.6vw, 1.85rem); }
.ed-card-standard .ed-headline { font-size: clamp(1.1rem, 1.9vw, 1.35rem); }
.ed-summary { font-size: 0.98rem; color: var(--ink-soft); line-height: 1.65; margin: 0 0 14px; max-width: 68ch; }
.ed-divider { border: none; border-top: 1px solid var(--rule); margin: 0 0 14px; }
.ed-bullets { list-style: none; margin: 0 0 18px; padding: 0; }
.ed-bullet { display: flex; align-items: flex-start; gap: 12px; padding: 9px 0;
  border-top: 1px dashed var(--rule); font-size: 0.92rem; color: var(--ink); line-height: 1.5; }
.ed-bullet:first-child { border-top: none; }
.ed-bullet-icon { flex: 0 0 auto; width: 22px; height: 22px; margin-top: 1px; border-radius: 999px;
  background: var(--masthead); color: #fff; font-size: 0.68rem; font-weight: 800;
  display: flex; align-items: center; justify-content: center; }
.ed-cta { display: inline-flex; align-items: center; gap: 6px; background: var(--masthead);
  color: #fff; font-weight: 700; font-size: 0.88rem; padding: 11px 22px; border-radius: 999px;
  text-decoration: none; }
.ed-cta:hover { opacity: 0.88; text-decoration: none; }
.ed-byline { font-size: 0.78rem; color: var(--ink-faint); margin: 0 0 14px; }
@media (max-width: 640px) {
  .ed-card { flex-direction: column; align-items: stretch; padding: 16px; gap: 14px; }
  .ed-card-media { flex: none; max-width: none; width: 100%; align-self: auto;
    aspect-ratio: 16 / 9; }
  .ed-card-media img { aspect-ratio: 16 / 9; }
  .ed-card-lead .ed-headline { font-size: 1.3rem; }
  .ed-card-standard .ed-headline { font-size: 1.1rem; }
  /* REFERENCE DESIGN: the reference's mobile CTA is a compact,
     left-aligned button (not a full-width bar) -- width: auto/fit-content
     keeps it that way while padding stays comfortable for a real tap
     target. */
  .ed-cta { width: auto; padding: 13px 24px; }
}

/* ---- progressive disclosure ---- */
details.more-disclosure { margin-top: 10px; }
details.more-disclosure > summary { cursor: pointer; font-size: 0.84rem; font-weight: 700; color: var(--masthead);
  list-style: none; padding: 6px 0; }
details.more-disclosure > summary::-webkit-details-marker { display: none; }
details.more-disclosure > summary::before { content: "+ "; }
details.more-disclosure[open] > summary::before { content: "− "; }
details.more-disclosure > .news-list { margin-top: 4px; padding-top: 4px; border-top: 1px dashed var(--rule); }

/* ---- Genre / Production Radar (editorial, rule-separated -- no box).
   GENRE uses a solid emerald left rule (matches the rest of MUSIC's
   intelligence modules); PRODUCTION uses a dashed teal-leaning rule +
   monospace observed line, so the two radars read as visually distinct
   real modules -- never by inventing a fake structured field grid
   neither pipeline's synthesis actually produces. ---- */
.radar-list { display: flex; flex-direction: column; gap: 0; }
.radar-card { padding: 14px 0 14px 16px; border-top: 1px solid var(--rule); border-left: 3px solid var(--hue-music); }
.radar-list .radar-card:first-child { border-top: none; }
.radar-observed-label { display: inline-block; font-size: 0.64rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: var(--hue-music); margin-bottom: 4px; margin-right: 8px; }
/* FACT/OBSERVATION/SIGNAL/TREND/INTELLIGENCE evidence-level badge (see
   report.web_render_v2._evidence_level_label) -- a small, restrained
   inline marker, never a rainbow card background; SIGNAL (2+ real
   independent citations) gets a faint teal tint, OBSERVATION (a single
   real citation) stays neutral/muted -- color signals real evidence
   strength, never decoration. */
.evidence-level-badge { display: inline-block; font-size: 0.6rem; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; padding: 1px 6px; border-radius: 3px; vertical-align: middle; }
.evidence-level-observation { color: var(--ink-faint); background: var(--chip-bg); }
.evidence-level-signal { color: var(--hue-music-tint2, var(--hue-music)); background: var(--hue-music-tint); }
.radar-observed { font-size: 0.98rem; font-weight: 600; margin: 0 0 8px; max-width: 68ch; }
.radar-interp { font-size: 0.9rem; color: var(--ink-soft); margin: 0 0 8px; max-width: 68ch; padding-left: 12px; border-left: 2px solid var(--rule); }
.radar-card-production { border-left-style: dashed; border-left-color: var(--hue-music-tint2, var(--hue-music)); }
.radar-card-production .radar-observed-label { color: var(--hue-music-tint2, var(--hue-music)); }
.radar-card-production .radar-observed { font-family: Georgia, "Times New Roman", ui-serif, serif; }

/* ---- Producer / A&R Takeaways (a real box: highest-value actionable
   items genuinely benefit from stronger separation -- "use boxes only
   when they add information hierarchy") ---- */
.takeaway-list { display: flex; flex-direction: column; gap: 10px; }
.takeaway-card { padding: 14px 16px; background: var(--surface); border: 1px solid var(--rule); border-left: 3px solid var(--hue-music); }
.takeaway-label { display: block; font-size: 0.64rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: var(--hue-music); margin-bottom: 4px; }

/* Evidence collapsed by default (native <details>, zero JS) -- a real
   citation a reader can open, never forced into view by default. */
details.evidence-disclosure { margin: 4px 0 8px; }
details.evidence-disclosure > summary { cursor: pointer; font-size: 0.76rem; font-weight: 600; color: var(--ink-faint);
  list-style: none; padding: 2px 0; }
details.evidence-disclosure > summary::-webkit-details-marker { display: none; }
details.evidence-disclosure > summary::before { content: "+ "; }
details.evidence-disclosure[open] > summary::before { content: "− "; }
.evidence-chip { display: block; font-size: 0.78rem; padding: 6px 9px; border-radius: 4px; background: var(--chip-bg); color: var(--ink-soft); line-height: 1.5; max-width: 68ch; margin: 4px 0 0; }
.evidence-chip::before { content: "— "; opacity: 0.5; }
.confidence-badge { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; }
.confidence-HIGH { color: var(--good-up); }
.confidence-MEDIUM { color: var(--hue-economy); }
.confidence-LOW { color: var(--ink-faint); }
.takeaway-action { font-size: 1rem; font-weight: 700; margin: 0 0 10px; }
.takeaway-row { font-size: 0.88rem; color: var(--ink-soft); margin: 0 0 6px; max-width: 68ch; }
.takeaway-row:last-of-type { margin-bottom: 8px; }
.takeaway-row b { color: var(--ink); font-weight: 600; }

/* ---- Spotify Watch (PREMIUM INTELLIGENCE UPGRADE PASS): one compact
   real card, DSP/platform teal accent -- never a giant new module, never
   a rainbow addition to the existing restrained palette. ---- */
.spotify-watch-card { padding: 14px 16px; background: var(--surface); border: 1px solid var(--rule);
  border-left: 3px solid var(--hue-music-tint2, var(--hue-music)); }
.spotify-watch-label { display: block; font-size: 0.64rem; font-weight: 800; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--hue-music-tint2, var(--hue-music)); margin-bottom: 6px; }
.spotify-watch-title { font-size: 1.05rem; font-weight: 700; margin: 0 0 6px; max-width: 68ch; }
.spotify-watch-row { font-size: 0.88rem; color: var(--ink-soft); margin: 0 0 6px; max-width: 68ch; }
.spotify-watch-row b { color: var(--ink); font-weight: 600; }

/* ---- Cross-Platform Signals (compact) ---- */
.signal-compact-list { list-style: none; margin: 0; padding: 0; }
.signal-compact-row { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px; padding: 8px 0; border-top: 1px solid var(--rule); font-size: 0.9rem; }
.signal-compact-row:first-child { border-top: none; }
.signal-compact-track { font-weight: 600; }
.signal-compact-meta { font-size: 0.8rem; color: var(--ink-faint); }
.signal-compact-detail { flex-basis: 100%; font-size: 0.82rem; color: var(--ink-soft); }

/* ---- Sources ---- */
.source-status-list { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.source-status-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 4px; background: var(--surface); border: 1px solid var(--rule); font-size: 0.8rem; color: var(--ink-soft); }
.status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 999px; }
.status-active .status-dot { background: var(--good-up); }
.status-unavailable .status-dot { background: var(--bad-down); }

.state-message { max-width: 68ch; padding: 12px 14px; border-radius: 4px; background: var(--chip-bg); font-size: 0.92rem; color: var(--ink-soft); }
.state-message.state-degraded, .state-message.state-unavailable { color: var(--bad-down); }

footer { max-width: 1280px; margin: 40px auto 0; padding: 0 20px; font-size: 0.72rem; color: var(--ink-faint); }

@media (max-width: 960px) {
  .main { max-width: 100%; }
}

@media (max-width: 600px) {
  body { font-size: 16px; }
  .masthead { padding: 18px 16px 14px; }
  /* MOBILE MASTHEAD CLIPPING FIX (confirmed real defect via true 390px
     headless-Chrome QA): .brand-row/.meta-row's own space-between +
     flex-wrap did not reliably wrap the tagline/read-time onto their own
     line at real narrow widths -- both were rendering clipped/invisible
     past the right edge instead. Forcing each onto its own full-width
     line removes any ambiguity -- neither is ever cut off, real content
     never lost, only re-flowed. */
  .brand-row { flex-wrap: wrap; }
  .brand { font-size: clamp(1.5rem, 5.5vw, 1.7rem); }
  .tagline { flex-basis: 100%; }
  .meta-row { flex-direction: column; align-items: flex-start; gap: 2px; }
  .date { font-size: clamp(0.95rem, 4vw, 1.15rem); }
  .pub-nav { padding: 8px 16px; gap: 12px; }
  .today-intel { padding: 0 16px; margin-top: 16px; }
  .lead-story { padding-top: 16px; }
  .lead-image-wrap { max-height: 260px; }
  .signal-thumb { width: 52px; height: 52px; }
  .main { padding: 0 16px; }
  section.block { margin-bottom: 26px; padding-bottom: 18px; }
  footer { padding: 0 16px; }
}
"""


# ---------------------------------------------------------------------
# TODAY'S MUSIC INTELLIGENCE (hero, MUSIC ONLY)
# ---------------------------------------------------------------------

_HERO_SECONDARY_MAX = 3


def _signal_title_and_url(signal):
    item = signal.get("headline_item")
    if item:
        return _e(_display_title(item)), item.get("source_url")
    return _e(signal.get("fact_text") or ""), None


def _valid_image_url(url):
    """MUSIC EDITORIAL IMAGERY -- IMAGE TRUST CONTRACT: only a real,
    well-formed http(s) URL string is ever treated as trustworthy -- never
    a relative path, a data: URI, a javascript: value, or any non-string.
    Defense-in-depth alongside the data layer's own equivalent check
    (report/web_data_v2._extract_trustworthy_image_url): this renderer is
    a pure function of whatever dict it's handed and must never assume its
    input was already validated upstream."""
    if not isinstance(url, str):
        return False
    url = url.strip()
    return url.startswith("http://") or url.startswith("https://")


def _lead_image_html(item, alt_text):
    """At most one prominent lead image, and ONLY when the real
    headline_item backing this signal carries a real, trustworthy image
    URL -- an analysis-only signal (item is None) or a text-only news item
    (no image_url) simply renders with no image, never a placeholder.
    Eager-loaded (not lazy): this is the first-screen lead image, so
    deferring it would hurt, not help, real first-screen rendering."""
    if not item or not _valid_image_url(item.get("image_url")):
        return ""
    safe_url = html.escape(item["image_url"], quote=True)
    return f'<div class="lead-image-wrap"><img class="lead-image" src="{safe_url}" alt="{alt_text}" loading="eager"></div>'


def _secondary_thumbnail_html(item, alt_text):
    """At most one compact thumbnail per TODAY IN MUSIC card (itself
    already capped at _HERO_SECONDARY_MAX, so at most 3 can ever appear on
    the page), lazy-loaded since these are never first-screen-critical the
    way the lead image is."""
    if not item or not _valid_image_url(item.get("image_url")):
        return ""
    safe_url = html.escape(item["image_url"], quote=True)
    return f'<img class="signal-thumb" src="{safe_url}" alt="{alt_text}" loading="lazy">'


def _render_lead_story(signal):
    """LEAD STORY: the single strongest real MUSIC signal, rendered as a
    true newsletter lead -- category kicker, a large headline that's a
    real tappable/clickable link whenever a real source_url exists (never
    fake-clickable for an analysis-only fact_text signal with no real
    item), a real prominent image when the item carries a real trustworthy
    one, a real short summary when the headline item carries one, then
    WHY IT MATTERS and PRODUCER IMPACT/WATCH as labeled rows, and finally
    a source/date/original-link line when a real item backs it."""
    item = signal.get("headline_item")
    type_label = _MUSIC_CANDIDATE_LABELS.get(signal.get("type"), "")
    kicker_html = f'<span class="lead-kicker">{_e(type_label)}</span>' if type_label else ""
    title_text, source_url = _signal_title_and_url(signal)
    headline_html = _linked_headline_html("h2", "lead-title", title_text, source_url)
    image_html = _lead_image_html(item, title_text)

    summary_html = ""
    snippet = None
    if item:
        snippet = _display_snippet(item)
        if snippet:
            summary_html = f'<p class="lead-summary">{_e(snippet)}</p>'

    # SUMMARY/BULLET DEDUP (FINAL 90+ QUALITY CORRECTION PASS): never
    # render a why-row that's a near-duplicate of the summary or of an
    # already-kept row -- see _ed_bullets_html's own docstring for the
    # same rule applied to the ed-card bullets.
    why_rows = ""
    kept_rows = []
    for label, text in (("왜 중요한가", signal.get("why_it_matters")), ("프로듀서 시사점", signal.get("watch_next"))):
        if not text:
            continue
        # CURRENT-CACHE SAFETY: watch_next can carry a cached Producer
        # Intelligence "make now" suggestion -- never shown when it
        # recommends editorial content creation instead of a real
        # music-making/A&R action (same guard as _render_producer_
        # takeaway_card's own action_row).
        if label == "프로듀서 시사점" and is_content_creation_advice(text):
            continue
        if _is_near_duplicate_text(text, snippet):
            continue
        if any(_is_near_duplicate_text(text, kept) for kept in kept_rows):
            continue
        kept_rows.append(text)
        why_rows += f'<div class="lead-why-row"><span class="lead-why-label">{_e(label)}</span><p>{_e(text)}</p></div>'

    meta_html = ""
    if item:
        byline_html = _item_byline(item)
        link_html = _link_html(item.get("source_url"), label="기사 보기 →")
        if byline_html or link_html:
            meta_html = f'<div class="lead-meta">{byline_html}{link_html}</div>'

    return f'<article class="lead-story">{kicker_html}{headline_html}{image_html}{summary_html}{why_rows}{meta_html}</article>'


def _render_secondary_signal_card(signal):
    item = signal.get("headline_item")
    type_label = _MUSIC_CANDIDATE_LABELS.get(signal.get("type"), "")
    kicker_html = f'<span class="signal-kicker">{_e(type_label)}</span>' if type_label else ""
    title_text, source_url = _signal_title_and_url(signal)
    headline_html = _linked_headline_html("h3", "signal-title", title_text, source_url)
    # NEVER render duplicated headline/meaning: `meaning` is only shown
    # when it says something genuinely different from the headline text
    # itself.
    meaning = signal.get("meaning")
    meaning_html = ""
    if meaning and _e(meaning) != title_text:
        meaning_html = f'<p class="signal-meaning">{_e(meaning)}</p>'
    thumb_html = _secondary_thumbnail_html(item, title_text)
    body_html = f'<div class="signal-body">{kicker_html}{headline_html}{meaning_html}</div>'
    return f'<div class="signal-card">{thumb_html}{body_html}</div>'


def _lead_signal(signals):
    """The real signal that becomes the LEAD STORY -- shared by the hero's
    own render and by MUSIC EVENT EXPOSURE BUDGET's cross-section
    suppression (render_dashboard_html_v2), so both always agree on
    exactly which real signal is "the lead" from the SAME single real
    rule. LEAD FALLBACK: is_strongest is a real upstream curation signal,
    not a guarantee -- if no signal has it set, the first real signal
    safely becomes the lead instead of an empty lead."""
    if not signals:
        return None
    strongest = next((s for s in signals if s.get("is_strongest")), None)
    return strongest if strongest is not None else signals[0]


# ---------------------------------------------------------------------
# MUSIC EVENT EXPOSURE BUDGET (TRUE event-level identity)
#
# CORRECTIVE PASS: the previous same-evidence-ref-overlap-only suppression
# left a real gap -- the SAME real underlying event, reported by DIFFERENT
# real outlets (different evidence refs, since report.music_trend_
# synthesis/report.producer_synthesis's own evidence catalogs assign a
# fresh ref per real article), could survive as an unlimited number of
# independent exposures across Genre Radar/Production Radar/Producer, since
# "different evidence_refs" was incorrectly being treated as proof of a
# different real event. This section resolves TRUE event identity
# (evidence ref -> its real article -> that article's real event_key,
# EXACTLY the same real news items already carry event_key on) wherever
# structured data allows it, and enforces a real hard cap of 2 total
# visible exposures (the Lead itself + at most 1 further genuinely
# distinct real interpretation) for one real underlying event.
#
# DOCUMENTED LIMITATION: a chart-fact evidence citation (SPOTIFY_CHART_
# RANK/TIKTOK_CHART_RANK -- see report.music_trend_synthesis.
# build_evidence_catalog) has no corresponding real article at all, so it
# can never resolve to a real event_key -- the strongest remaining real
# structured identity for that case is the real evidence-ref set itself
# (still deterministic, never a fabricated event_key, never fuzzy title
# similarity).
# ---------------------------------------------------------------------

def _news_title_to_event_key_map(news):
    """Deterministic real title -> real event_key lookup, built from the
    SAME real SPOTIFY/TIKTOK news items report.music_trend_synthesis./
    report.producer_synthesis's own evidence catalogs draw their
    MUSIC_INDUSTRY_NEWS entries from -- used by _resolve_entry_event_key
    to resolve a synthesis entry's own real evidence citation back to the
    real article's real event_key. A title seen on more than one real item
    keeps whichever event_key it saw first (deterministic; in practice a
    catalog summary is built from exactly one specific real item's title,
    so this never actually has to arbitrate a genuine conflict)."""
    mapping = {}
    for category in ("SPOTIFY", "TIKTOK"):
        for item in news[category]["items"]:
            title, event_key = item.get("title"), item.get("event_key")
            if title and event_key and title not in mapping:
                mapping[title] = event_key
    return mapping


def _resolve_entry_event_key(entry, title_to_event_key):
    """TRUE EVENT-LEVEL IDENTITY, in priority order (SECOND CORRECTIVE
    PASS -- closes the real gap the first pass left: text-matching was
    the PRIMARY bridge, so an evidence summary paraphrased differently
    from its source article's title could silently fail to resolve):

    1. An explicit real event_key already carried DIRECTLY on one of
       `entry`'s own real evidence citations. report.music_trend_
       synthesis.build_evidence_catalog / report.producer_synthesis.
       build_evidence_catalog propagate this straight from the
       originating real news item at catalog-build time (see those
       modules' own MUSIC EVENT-LEVEL IDENTITY docstrings) -- TRUE
       deterministic lineage, no text comparison involved at all. This is
       now the primary path for every normal, article-backed evidence
       citation persisted after this corrective pass.

    2. ONLY as a last, backward-compatible fallback -- a legacy/older
       persisted row's catalog entry that predates this propagation (no
       `event_key` key on its evidence citation at all): exact/prefix
       real-title matching against a real news item's own title (see
       _news_title_to_event_key_map), reusing the SAME real-title-match
       mechanic report.web_data_v2._evidence_refs_for_title already
       established, in reverse.

    Returns None when neither resolves -- a real non-article evidence
    type (chart/cross-platform facts, or a legacy row whose text simply
    doesn't match) has no real event_key, honestly, never fabricated."""
    evidence = entry.get("evidence") or []
    for ev in evidence:
        event_key = ev.get("event_key")
        if event_key:
            return event_key
    for ev in evidence:
        summary = ev.get("summary") or ""
        for title, event_key in title_to_event_key.items():
            if summary == title or summary.startswith(title + " — "):
                return event_key
    return None


def _signal_event_identity(signal, title_to_event_key):
    """Real (event_key, evidence_refs) pair for a Hero/Today-in-Music
    signal or raw music-signal candidate. event_key comes directly from a
    real headline_item when present (INDUSTRY_NEWS -- already a real,
    directly-known event_key, no resolution needed); otherwise a direct
    top-level `event_key` field when present (a deterministic MUSIC TODAY
    backfill candidate -- see report.web_data_v2._build_music_today --
    deliberately carries headline_item=None so its visible sentence is
    never the bare headline Industry already shows, but still needs its
    real event identity recognized here); otherwise resolved from the
    signal's own real `_evidence` citations (GENRE_SIGNAL/PRODUCTION_
    SIGNAL/KPOP_AR/PRODUCER_INSIGHT -- see report.web_data_v2.
    _collect_music_signal_candidates). evidence_refs is always the real
    ref-label set (report.web_data_v2's own `_evidence_refs`), used for
    the TIER 1 "cites the lead's own literal source" check below."""
    item = signal.get("headline_item")
    if item:
        event_key = item.get("event_key")
    elif signal.get("event_key"):
        event_key = signal.get("event_key")
    else:
        event_key = _resolve_entry_event_key({"evidence": signal.get("_evidence") or []}, title_to_event_key)
    return event_key, (signal.get("_evidence_refs") or set())


def _synthesis_entry_event_identity(entry, title_to_event_key):
    """Same real (event_key, evidence_refs) pair, for a raw Genre Radar/
    Production Radar/Producer synthesis entry (these already carry a real
    `evidence` list -- ref + real summary TEXT -- directly on themselves,
    unlike a Hero signal which needs the `_evidence`-prefixed internal
    copy; see report.web_data_v2._music_trend_intelligence_section /
    _producer_intelligence_section)."""
    return _resolve_entry_event_key(entry, title_to_event_key), {ev["ref"] for ev in entry.get("evidence") or []}


def _shares_lead_evidence(entry_refs, lead_refs):
    """TIER 1 -- literal same real source: `entry` cites at least one of
    the SAME real evidence ref(s) the lead itself cites, so it is citing
    the IDENTICAL real source material the lead already shows -- by
    definition adds no distinct real value, and is ALWAYS suppressed,
    never eligible for the one allowed distinct-interpretation slot.
    Overlap, not set equality: a lead citing {E1,E2} and an entry citing
    only {E1} still share real evidence."""
    return bool(entry_refs) and bool(lead_refs) and bool(entry_refs & lead_refs)


def _same_resolved_event(entry_event_key, lead_event_key):
    """TIER 2 -- the SAME real underlying event via a DIFFERENT real
    source: both resolve to the SAME real event_key (see
    _resolve_entry_event_key) while citing DIFFERENT real evidence than
    the lead. TRUE event-level identity -- "different evidence_refs" is
    NEVER, by itself, treated as proof of a different real event once a
    real event_key match exists (the confirmed real gap this corrective
    pass closes; SUPER_NEWS_SPEC.md section 9 / this task's own section
    3 "IMPORTANT DISTINCTION")."""
    return bool(entry_event_key) and bool(lead_event_key) and entry_event_key == lead_event_key


def _exclude_lead_event_from_today_in_music(music_today, lead_event_key, lead_refs, title_to_event_key,
                                             extra_excluded_event_keys=frozenset()):
    """TODAY IN MUSIC: ZERO tolerance for the lead's own real event, AND
    (FINAL EDITORIAL QUALITY PASS, 2026-08-18) for any real event already
    displayed in Music Industry (`extra_excluded_event_keys`, see
    _merge_music_industry_items) -- unlike Genre/Production/Producer
    below, an ordinary secondary signal sitting immediately adjacent to
    the Lead or to Music Industry on the same screen never counts as the
    one allowed distinct interpretation merely because its real
    representation (translation, paraphrase, different real outlet)
    differs (SUPER_NEWS_SPEC.md section 9's own "if an event is used as
    the lead, ordinary duplicate news cards should be suppressed" rule,
    applied at the page's own most visually-adjacent position -- Music
    Today sits directly beneath Music Industry, so the same "already
    shown a moment ago" logic applies). Real candidates never dropped for
    any OTHER reason here -- see report.web_data_v2._build_music_today
    for the real "never padded" cap."""
    excluded_event_keys = {key for key in ({lead_event_key} | set(extra_excluded_event_keys)) if key}
    if not excluded_event_keys and not lead_refs:
        return music_today
    kept = []
    for candidate in music_today:
        event_key, refs = _signal_event_identity(candidate, title_to_event_key)
        if _shares_lead_evidence(refs, lead_refs) or (event_key and event_key in excluded_event_keys):
            continue
        kept.append(candidate)
    return kept


def _resolved_event_keys_for_entry(entry, title_to_event_key):
    """All distinct real event_keys `entry`'s own evidence citations
    resolve to (never just the first match, unlike _resolve_entry_event_
    key -- a multi-citation synthesis entry can genuinely span more than
    one real event, and telling "fully covered by an already-shown entry"
    apart from "also touches a shown event but adds a genuinely new one"
    requires the complete set). Each citation's own `event_key` is
    preferred when already propagated (see report.music_trend_synthesis./
    report.producer_synthesis.build_evidence_catalog); title-match
    fallback for a legacy citation, same rule as _resolve_entry_event_key.
    Real refs/summaries are NOT compared directly here -- producer_
    intelligence and music_trend_intelligence each build their OWN
    independently ref-labelled evidence catalog (an "E1" in one is
    unrelated to an "E1" in the other), so only the real, catalog-
    independent event_key is ever a safe cross-catalog identity."""
    keys = set()
    for ev in entry.get("evidence") or []:
        event_key = ev.get("event_key")
        if not event_key:
            summary = ev.get("summary") or ""
            for title, candidate_key in title_to_event_key.items():
                if summary == title or summary.startswith(title + " — "):
                    event_key = candidate_key
                    break
        if event_key:
            keys.add(event_key)
    return keys


def _dedupe_producer_section_exact_duplicates(producer_insights, reference_items, kpop_items, title_to_event_key):
    """PRODUCER/A&R FINAL QUALITY PASS (confirmed real defect from actual
    generated-report QA): the SAME real event can independently surface as
    a Producer insight AND a separate K-pop/A&R note (or producer
    reference) -- e.g. "TikTok Music on Stage returns" appearing once as a
    Producer insight and again as its own K-pop/A&R card citing the same 3
    real outlets, just via producer_intelligence's and music_trend_
    intelligence's own INDEPENDENTLY ref-labelled evidence catalogs (so a
    raw ref/summary comparison never catches it -- see
    _resolved_event_keys_for_entry). An entry is suppressed only when
    EVERY real event it resolves to is already covered by an earlier-kept
    entry (by this section's own real display order: insights ->
    references -> kpop) -- a literal duplicate, zero incremental value. A
    broader multi-story synthesis that also touches ONE already-shown
    event but introduces at least one genuinely NEW one is never
    suppressed (DISTINCT INTELLIGENCE EXCEPTION) -- only full subset
    coverage counts as a duplicate."""
    seen_event_keys = set()

    def _filter(entries):
        kept = []
        for entry in entries:
            keys = _resolved_event_keys_for_entry(entry, title_to_event_key)
            if keys and keys.issubset(seen_event_keys):
                continue
            kept.append(entry)
            seen_event_keys.update(keys)
        return kept

    return _filter(producer_insights), _filter(reference_items), _filter(kpop_items)


def _apply_music_event_exposure_budget(entry_lists, lead_event_key, lead_refs, title_to_event_key,
                                        extra_shown_event_keys=frozenset()):
    """GENRE RADAR -> PRODUCTION RADAR -> PRODUCER/A&R (insights,
    references, K-pop/A&R notes), in that real fixed editorial order --
    ONE single global decision governs every section, never an
    independent per-section guess. `entry_lists` is that ordered list of
    real entry lists; returns the same shape, filtered.

    TIER 1 entries (cite the lead's own literal evidence -- see
    _shares_lead_evidence) are ALWAYS suppressed, in every one of these
    lists, no budget consumed.

    TIER 2 entries (resolve to the SAME real event_key as the lead OR as
    any item already displayed in Music Industry -- `extra_shown_event_
    keys`, see _merge_music_industry_items -- via DIFFERENT real
    evidence) consume the ONE real allowed "distinct interpretation"
    slot: the FIRST such real entry, walked in this fixed order across
    ALL these lists, is kept; every subsequent real TIER 2 match, against
    EITHER the lead OR any already-shown Industry event, in ANY of these
    lists, is suppressed -- even when it cites real evidence distinct
    from the one already-kept exposure (FINAL EDITORIAL QUALITY PASS,
    2026-08-18, confirmed real defect: this budget previously only ever
    tracked the lead's own identity, so a non-lead Industry item -- e.g.
    a Spotify product-feature launch or a TikTok platform announcement --
    could reappear, re-worded, in Genre/Production/Producer with no
    limit at all; the reader experiences that as the same news repeated,
    "different prose does NOT automatically make repetition useful").
    "Cites different evidence" is never sufficient, by itself, to earn a
    THIRD (or later) real exposure of the same real event.

    Entries matching neither tier (a genuinely unrelated real event, or
    one whose real identity could not be resolved at all -- see
    _resolve_entry_event_key's own documented limitation) are always
    kept, completely unaffected."""
    shown_event_keys = {key for key in ({lead_event_key} | set(extra_shown_event_keys)) if key}
    if not shown_event_keys and not lead_refs:
        return entry_lists
    budget_claimed = False
    result = []
    for entries in entry_lists:
        kept = []
        for entry in entries:
            event_key, refs = _synthesis_entry_event_identity(entry, title_to_event_key)
            if _shares_lead_evidence(refs, lead_refs):
                continue
            if event_key and event_key in shown_event_keys:
                if budget_claimed:
                    continue
                budget_claimed = True
            kept.append(entry)
        result.append(kept)
    return result


# ---- SEMANTIC DUPLICATION GUARD (content-quality hardening pass,
# 2026-08-17) -- extends the MUSIC EVENT EXPOSURE BUDGET above, which
# only ever compares each section against the LEAD, never against its
# OWN sibling sections, and has no identity concept at all for a
# chart-fact-only signal (no backing article/event_key -- see
# _resolve_entry_event_key's own documented limitation). Confirmed real
# defect: the SAME underlying Spotify chart fact (an artist's rank
# change) independently resurfaced, reworded, across FIVE separate
# sections (오늘의 음악 소식/hero secondary, 뮤직 투데이, 장르 레이더,
# 프로듀서 A&R 테이크어웨이, 크로스플랫폼 시그널) purely because each
# section's own synthesis pass has no visibility into what its siblings
# already showed. ----

# Every real text field ANY of these entry shapes may carry -- a single
# generic scan, not one adapter per section shape (hero/music_today
# candidates: fact_text/why_it_matters/watch_next; genre/production
# radar + producer references/kpop notes: observed/interpretation;
# producer insights: what_is_moving/why_it_matters/what_to_watch/
# what_could_i_make_now).
_MUSIC_ENTITY_TEXT_FIELDS = (
    "fact_text", "why_it_matters", "watch_next", "producer_implication",
    "observed", "interpretation", "what_is_moving", "what_to_watch", "what_could_i_make_now",
)


def _entry_text_blob(entry):
    return " ".join(str(entry[field]) for field in _MUSIC_ENTITY_TEXT_FIELDS if entry.get(field))


def _entry_chart_entity_keys(entry, known_chart_keys):
    """Real chart-entity identity for one entry -- checks that BOTH the
    real artist name AND the real track title (known_chart_keys' values
    are "Artist - Title" labels; split into two independent substring
    checks) appear SOMEWHERE in the entry's own real text, never requiring
    the exact joined "Artist - Title" phrasing. Confirmed real gap the
    exact-joined-string version had: an LLM-synthesized ANALYSIS entry
    naturally rephrases a track mention in natural Korean prose (e.g.
    "Shakira의 'Dai Dai'는 순위가...") rather than the canonical
    "Artist - Title" format a raw chart-fact candidate uses -- an exact
    substring match on the joined label silently missed every such
    rephrasing. Still never fuzzy/invented: both parts must be real,
    closed-vocabulary strings drawn directly from that day's real
    spotify_chart top10. An entry can genuinely reference more than one
    real track (a combined 'chart movements' narrative), so this returns
    a set. A headline_item's own title/snippet is also scanned when
    present (INDUSTRY_NEWS-shaped hero/music_today candidates)."""
    item = entry.get("headline_item")
    text = _entry_text_blob(entry)
    if item:
        text = f'{text} {item.get("title") or ""} {item.get("snippet") or ""}'
    if not text:
        return set()
    found = set()
    for key, label in known_chart_keys.items():
        artist, _, title = label.partition(" - ")
        if artist and title and artist in text and title in text:
            found.add(key)
    return found


def _apply_full_music_cross_section_dedup(
    hero_secondary, music_today, genre_signals, production_notes,
    producer_insights, producer_references, kpop_ar_notes,
    lead_event_key, title_to_event_key, known_chart_keys,
    extra_seed_event_keys=frozenset(),
):
    """ONE combined identity per entry -- its real event_key (article-
    backed, via _synthesis_entry_event_identity/_signal_event_identity)
    UNION any real chart-entity keys its own text mentions (see
    _entry_chart_entity_keys) -- walked across ALL of these sections in
    ONE fixed editorial order (exactly the page's own top-to-bottom
    order: hero secondary -> music_today -> genre -> production ->
    producer insights -> producer references -> kpop/A&R notes). The
    FIRST entry carrying a given identity is kept; every later entry
    sharing ANY of that identity -- in any of these lists -- is
    suppressed: simple re-wording of an already-shown fact is never a
    second real exposure ("한 사건은 최대 1개 primary section"). The
    LEAD's own identity seeds the seen-set first, so an entry that only
    repeats what the lead already showed is caught too (belt-and-braces
    with the existing exposure-budget pass above, which already removes
    most of those).

    크로스플랫폼 시그널 (cross_platform) is NOT passed here -- see its
    own call site, which is deliberately exempt as the one designated
    short cross-reference slot ("+ 필요할 경우 1개 짧은 cross-reference").

    Never invents a replacement entry -- suppression only ever removes.
    A section can legitimately end up with fewer (or zero) real items on
    a day the real music news is genuinely thin; "정보 밀도" is preferred
    over forcing every section to stay visually non-empty."""
    # Seeded with the lead's own event_key when it has one (an
    # article-backed lead) -- a chart-fact-only lead resolves no
    # event_key at all (see _signal_event_identity), and TIER 1
    # (_shares_lead_evidence) in the exposure-budget pass above already
    # covers literal-source overlap for that case, so lead_refs itself
    # needs no separate handling here. ALSO seeded with
    # `extra_seed_event_keys` (FINAL EDITORIAL QUALITY PASS, 2026-08-18:
    # whichever real events are already displayed in Music Industry, see
    # _merge_music_industry_items) -- an event already shown as an
    # Industry headline is "already shown a moment ago" exactly like the
    # lead, so a later section repeating it in different prose is still
    # zero new information for the reader.
    seen = {key for key in ({lead_event_key} | set(extra_seed_event_keys)) if key}

    def _identity_for(entry, is_signal):
        if is_signal:
            event_key, _refs = _signal_event_identity(entry, title_to_event_key)
        else:
            event_key, _refs = _synthesis_entry_event_identity(entry, title_to_event_key)
        keys = _entry_chart_entity_keys(entry, known_chart_keys)
        if event_key:
            keys = keys | {event_key}
        return keys

    def _filter(entries, is_signal):
        kept = []
        for entry in entries:
            identity = _identity_for(entry, is_signal)
            if identity and identity & seen:
                continue
            kept.append(entry)
            seen.update(identity)
        return kept

    return (
        _filter(hero_secondary, True),
        _filter(music_today, True),
        _filter(genre_signals, False),
        _filter(production_notes, False),
        _filter(producer_insights, False),
        _filter(producer_references, False),
        _filter(kpop_ar_notes, False),
    )


def _today_intel_parts(signals, secondary_override=None):
    """Returns (today_intel_lead_html, today_secondary_html) as two
    SEPARATE fragments so a caller can position them independently -- see
    render_music_page_html_v2, which places section-INDUSTRY between them
    (SUPER NEWS MUSIC visible-order fix: 업계 뉴스 lead -> 뮤직 인더스트리
    -> 오늘의 음악 소식). today_secondary_html carries no width/padding of
    its own (see .today-secondary CSS) -- it relies on whichever parent
    renders it for its 900px publication width, exactly like every other
    section inside <main class="main"> (also max-width: 900px; margin: 0
    auto; padding: 0 20px) -- so relocating it needs no new CSS.
    _render_today_music_intelligence (below) recombines both fragments in
    their original adjacent nested order, byte-identical to before this
    split, for render_dashboard_html_v2's own unchanged legacy page.

    `secondary_override`, when given, REPLACES the internally-computed
    secondary list (e.g. render_music_page_html_v2 passes its own
    already cross-section-deduped list -- see
    _apply_full_music_cross_section_dedup) -- None (the default)
    preserves the original internal computation exactly."""
    strongest = _lead_signal(signals)
    if strongest is None:
        return "", ""
    secondary = (
        secondary_override if secondary_override is not None
        else [s for s in signals if s is not strongest][:_HERO_SECONDARY_MAX]
    )

    lead_html = _render_lead_story(strongest)
    today_intel_lead_html = (
        '<div class="today-intel" id="today-intel"><h1>오늘의 뮤직 인텔리전스</h1>'
        f'{lead_html}</div>'
    )

    today_secondary_html = ""
    if secondary:
        cards = "".join(_render_secondary_signal_card(s) for s in secondary)
        today_secondary_html = (
            '<div class="today-secondary" id="today-in-music">'
            '<h2 class="today-secondary-head">오늘의 음악 소식</h2>'
            f'<div class="today-secondary-list">{cards}</div></div>'
        )
    return today_intel_lead_html, today_secondary_html


def _render_today_music_intelligence(signals):
    """CATEGORY-CONTIGUOUS IA REFINEMENT: the hero is MUSIC ONLY -- never
    mixes in AI/ECONOMY/SOCIETY.

    NEWSLETTER LEAD REDESIGN: a true newsletter lead structure, not a
    dashboard hero -- a single full-width LEAD STORY at full editorial
    weight, followed by a TODAY IN MUSIC list of at most
    _HERO_SECONDARY_MAX compact secondary signals stacked underneath
    (never a side-by-side grid, which is what produced the old large-
    blank-space defect when the two columns had uneven real content).

    Used as-is (lead + secondary nested in one .today-intel div, original
    markup) by render_dashboard_html_v2's legacy combined page; see
    _today_intel_parts for the split version render_music_page_html_v2
    uses to relocate 오늘의 음악 소식 after Music Industry."""
    lead_only_html, secondary_html = _today_intel_parts(signals)
    if not lead_only_html:
        return ""
    # Re-nest secondary_html INSIDE the same .today-intel div it always
    # rendered in here -- closing </div> moved from lead_only_html to
    # after secondary_html, exactly reproducing the original single nested
    # structure.
    return lead_only_html[:-len("</div>")] + secondary_html + "</div>"


# ---------------------------------------------------------------------
# MUSIC TODAY
# ---------------------------------------------------------------------

def _render_music_today_card(candidate):
    type_label = _MUSIC_CANDIDATE_LABELS.get(candidate["type"], candidate["type"])
    mode = candidate.get("mode", "FACT")
    mode_html = f'<span class="mode-badge mode-{mode}">{_MODE_LABELS.get(mode, mode)}</span>'
    kicker = f'<div class="mt-kicker-row"><span class="mt-type">{_e(type_label)}</span>{mode_html}</div>'

    item = candidate.get("headline_item")
    if item:
        headline_html = f'<h3 class="mt-headline">{_e(_display_title(item))}</h3>'
        byline_html = _item_byline(item)
        link_html = _link_html(item.get("source_url"))
    else:
        headline_html = f'<p class="mt-fact">{_e(candidate.get("fact_text") or "")}</p>'
        byline_html = ""
        link_html = ""

    why_html = ""
    if candidate.get("why_it_matters"):
        why_html = f'<p class="mt-why"><b>왜 중요한가</b>{_e(candidate["why_it_matters"])}</p>'
    implication_html = ""
    # CURRENT-CACHE SAFETY: same guard as the hero lead story's watch_next
    # row and _render_producer_takeaway_card's action_row -- a cached
    # producer_implication can recommend editorial content creation
    # instead of a real music-making/A&R action.
    implication = candidate.get("producer_implication")
    if implication and not is_content_creation_advice(implication):
        implication_html = f'<p class="mt-implication"><b>프로듀서 시사점</b>{_e(implication)}</p>'

    return (
        f'<article class="mt-card">{kicker}{headline_html}{byline_html}'
        f'{why_html}{implication_html}{link_html}</article>'
    )


def _render_music_today_section(music_today):
    if not music_today:
        body = f'<p class="state-message">{_e(_MUSIC_TODAY_EMPTY_MESSAGE)}</p>'
        quiet = " block-quiet"
    else:
        body = '<div class="music-today-list">' + "".join(_render_music_today_card(c) for c in music_today) + "</div>"
        quiet = ""
    return (
        f'<section class="block block-MUSICTODAY{quiet}" id="section-MUSICTODAY">'
        f'<div class="block-head"><h2>뮤직 투데이</h2></div>{body}</section>'
    )


# ---------------------------------------------------------------------
# CHART PULSE (Spotify TOP10 + Viral Hot/New merged, TikTok folded in)
# ---------------------------------------------------------------------

_PULSE_HEAD_HTML = (
    '<thead><tr><th class="pulse-rank">순위</th><th>트랙</th>'
    '<th class="pulse-delta-head">Δ</th><th class="pulse-status-head">상태</th></tr></thead>'
)


def _pulse_row(entry, cross_platform_ids):
    status_badge = _pulse_status_badge(entry)
    if entry.get("music_entity_id") in cross_platform_ids:
        status_badge += '<span class="badge badge-cross">교차 플랫폼</span>'
    return (
        f'<tr><td class="pulse-rank num">{entry["rank"]}</td>'
        f'<td class="pulse-track">{_e(entry["canonical_artist"])} - {_e(entry["canonical_title"])}</td>'
        f'<td class="pulse-delta-cell">{_pulse_delta_cell(entry)}</td>'
        f'<td class="pulse-badges">{status_badge}</td></tr>'
    )


_CHART_DATE_UNAVAILABLE_LABEL = "기준일 확인 필요"


def _chart_date_label(chart_date_iso):
    """CHART PULSE REAL-DATE CONTRACT: the SUPER NEWS report/publication
    date and the Spotify chart's own observation date are NOT the same
    thing (collector lag can put the real chart date a day or more before
    the report date) -- this renders the REAL chart_date the data layer
    already resolved (see report/web_data_v2._spotify_chart_section), and
    an explicit honest "기준일 확인 필요" state when no reliable chart_date
    exists. Never falls back to report_date_kst; never fabricates a
    date."""
    formatted = _format_date_kst(chart_date_iso)
    return f"{formatted} 기준" if formatted else _CHART_DATE_UNAVAILABLE_LABEL


def _first_observation_narrative(chart_date_iso):
    """Uses the REAL chart_date dynamically when available; never inserts
    a fabricated date into this sentence when chart_date is unavailable."""
    date_kr = _format_date_kst_korean(chart_date_iso)
    intro = (
        f'{date_kr} Spotify Global Daily Chart 첫 관측입니다.' if date_kr
        else f'Spotify Global Daily Chart 첫 관측입니다 ({_CHART_DATE_UNAVAILABLE_LABEL}).'
    )
    return (
        f'<p class="pulse-narrative">{intro} '
        '비교 가능한 이전 관측 데이터가 없어 이날을 기준선으로 설정합니다. '
        '다음 관측부터 순위 변동(Δ)을 표시합니다.</p>'
    )


def _render_chart_pulse_section(spotify_chart, cross_platform, viral_hot_top, viral_new_top):
    if spotify_chart["state"] == _STATE_UNAVAILABLE:
        body = f'<p class="state-message state-unavailable">{_e(_SPOTIFY_UNAVAILABLE_MESSAGE)}</p>'
        body += f'<p class="pulse-status">{_e(_TIKTOK_UNAVAILABLE_LINE)}</p>'
        return (
            '<section class="block block-CHARTPULSE" id="section-CHARTPULSE">'
            '<div class="block-head"><h2>차트 펄스</h2></div>' + body + "</section>"
        )

    top10 = spotify_chart["top10"]
    cross_platform_ids = {e["music_entity_id"] for e in cross_platform}
    rows = "\n".join(_pulse_row(e, cross_platform_ids) for e in top10)
    chart_date_iso = spotify_chart.get("chart_date")
    date_label = _chart_date_label(chart_date_iso)
    if spotify_chart.get("is_first_observation"):
        summary = f'<p class="block-sub-label">Spotify Global TOP {len(top10)} · {date_label} · 첫 관측 (기준선 생성)</p>'
        narrative = _first_observation_narrative(chart_date_iso)
    else:
        new_count = len(spotify_chart["new_entries"])
        summary = f'<p class="block-sub-label">Spotify Global TOP {len(top10)} · {date_label} · 신규 진입 {new_count}</p>'
        trend = spotify_chart.get("trend") or {}
        volatility_label = {"HIGH": "높음", "MEDIUM": "보통", "LOW": "낮음"}.get(trend.get("volatility"), "")
        narrative = (
            f'<p class="pulse-narrative">신규 {trend.get("new_count", 0)} · 상승 {trend.get("up_count", 0)} · '
            f'하락 {trend.get("down_count", 0)} — 변동성 {volatility_label}</p>'
        )
    hot_note = ""
    if viral_hot_top:
        hot_note = (
            f'<p class="pulse-narrative"><b>바이럴 급상승</b> {_e(viral_hot_top["canonical_artist"])} - '
            f'{_e(viral_hot_top["canonical_title"])} ▲{viral_hot_top["rank_delta"]}</p>'
        )
    new_note = ""
    if viral_new_top:
        new_note = (
            f'<p class="pulse-narrative"><b>주목할 신규 데뷔</b> {_e(viral_new_top["canonical_artist"])} - '
            f'{_e(viral_new_top["canonical_title"])} · TOP10 {viral_new_top["rank"]}위</p>'
        )
    status_line = f'<p class="pulse-status">{_e(_TIKTOK_UNAVAILABLE_LINE)}</p>'

    body = f'{summary}<table class="pulse-table">{_PULSE_HEAD_HTML}<tbody>{rows}</tbody></table>{narrative}{hot_note}{new_note}{status_line}'
    return (
        '<section class="block block-CHARTPULSE" id="section-CHARTPULSE">'
        '<div class="block-head"><h2>차트 펄스</h2></div>' + body + "</section>"
    )


# ---------------------------------------------------------------------
# Compact news cards (AI / ECONOMY / SOCIETY / MUSIC INDUSTRY)
# ---------------------------------------------------------------------

# SEMANTIC NEWS COLOR (EDITORIAL INTEGRITY FIX): a small, restrained
# story-type marker for Music Industry cards only -- reuses the existing
# theme-aware --hue-music/--hue-music-tint2/--hue-ai/--hue-economy
# variables (no new colors invented) and the existing --chip-bg neutral
# badge background, never a large colored card background. Only the 4
# real priority classes named in the editorial brief get a marker;
# revenue/chart/touring/release-strategy items intentionally show none
# rather than inventing further colors beyond the requested palette.
_MUSIC_STORY_TYPE_CHIPS = {
    1: ("RIGHTS/LICENSING", "emerald"),
    2: ("DSP/PLATFORM", "teal"),
    3: ("AI MUSIC", "cobalt"),
    4: ("A&R", "amber"),
}


def _music_story_type_chip_html(item):
    label = _MUSIC_STORY_TYPE_CHIPS.get(music_industry_priority_rank(item))
    if not label:
        return ""
    text, css_variant = label
    return f'<span class="story-type-chip story-type-{css_variant}">{_e(text)}</span>'


def _ed_cta_html(source_url):
    if not source_url:
        return ""
    safe_url = html.escape(source_url, quote=True)
    return f'<a class="ed-cta" href="{safe_url}" target="_blank" rel="noopener noreferrer">기사 보기 <span aria-hidden="true">&rarr;</span></a>'


_DEDUP_STRIP_RE = re.compile(r"[\s.,!?~·\-–—:;'\"()\[\]“”‘’]+")


def _normalize_for_dedup(text):
    """Deterministic near-duplicate normalization (FINAL 90+ QUALITY
    CORRECTION PASS): lowercased, whitespace/punctuation collapsed -- good
    enough to catch "same fact, trivial formatting difference" without any
    semantic/LLM comparison."""
    return _DEDUP_STRIP_RE.sub("", text.lower()) if text else ""


def _is_near_duplicate_text(a, b):
    na, nb = _normalize_for_dedup(a), _normalize_for_dedup(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _ed_bullets_html(item):
    """Same real-field selection _feature_bullets_html already uses
    (핵심/영향/지켜볼 점 -> up to 3 real key points) -- never invents a
    bullet; renders fewer than 3 (including zero) when fewer real fields
    exist, matching the evidence-integrity rule in the module docstring.

    SUMMARY/BULLET DEDUP (FINAL 90+ QUALITY CORRECTION PASS, confirmed real
    defect: several cards repeated the summary almost verbatim as bullet
    #1): every candidate bullet is rejected if it's a near-duplicate of the
    summary/snippet, or of an already-kept bullet -- never replaced with an
    invented substitute. Showing fewer than 3 (including zero) bullets is
    correct when fewer genuinely additive points survive."""
    snippet = _display_snippet(item)
    candidates = []
    if item.get("ai_intelligence_status") == "AVAILABLE":
        if item.get("what_happened"):
            candidates.append(item["what_happened"])
        if item.get("why_it_matters"):
            candidates.append(item["why_it_matters"])
        if item.get("what_to_watch"):
            candidates.append(item["what_to_watch"])
    else:
        if snippet:
            candidates.append(snippet)
        reason = item.get("reason")
        if reason and reason != snippet:
            candidates.append(reason)

    bullets = []
    for candidate in candidates:
        if _is_near_duplicate_text(candidate, snippet):
            continue
        if any(_is_near_duplicate_text(candidate, kept) for kept in bullets):
            continue
        bullets.append(candidate)
        if len(bullets) == 3:
            break
    if not bullets:
        return ""
    rows = "".join(
        f'<li class="ed-bullet"><span class="ed-bullet-icon" aria-hidden="true">&#10003;</span><span>{_e(text)}</span></li>'
        for text in bullets
    )
    return f'<ul class="ed-bullets">{rows}</ul>'


_DAILY_SUMMARY_MAX_SENTENCES = 2
_DAILY_SUMMARY_MAX_CHARS = 160

# WIRE-SERVICE BOILERPLATE (confirmed real defect: Newis/연합뉴스-style
# bylines like "[전남광주=뉴시스]이현행 기자 =" leaking into the visible
# DAILY summary). Only ever strips a real leading "[datelineーagency]
# reporter title =" prefix -- never rewrites or invents replacement text,
# and a summary that doesn't match this exact shape is returned untouched.
_WIRE_BOILERPLATE_RE = re.compile(r"^\[[^\]]{1,30}\]\s*[^=\[\]]{0,20}=\s*")

# SENTENCE SPACING NORMALIZATION (DAILY visible summaries only, confirmed
# real defect: source snippets like "경신했다.16일" render with no space
# after the sentence-ending mark). Only triggers when a Hangul character
# sits immediately BEFORE the mark -- a digit before it (as in a real
# decimal number like "66.4") never matches, so decimals are never
# touched. Deterministic, no rewriting -- inserts exactly one space.
_DAILY_SENTENCE_SPACING_RE = re.compile(r"(?<=[가-힣])([.!?])(?=[가-힣0-9])")


def _cap_daily_summary(text):
    """DAILY (AI/ECONOMY/SOCIETY) is a scanning-focused awareness feed --
    its summaries stay to at most 2 sentences within a bounded length,
    never MUSIC's own deeper editorial summaries. Strips real leading wire
    -service boilerplate and normalizes missing sentence-end spacing
    first, then truncates -- never fabricates replacement text; a summary
    already within the cap is returned with only boilerplate/spacing
    fixed."""
    if not text:
        return text
    text = _WIRE_BOILERPLATE_RE.sub("", text.strip(), count=1).strip()
    text = _DAILY_SENTENCE_SPACING_RE.sub(r"\1 ", text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    capped = " ".join(sentences[:_DAILY_SUMMARY_MAX_SENTENCES]).strip()
    if len(capped) > _DAILY_SUMMARY_MAX_CHARS:
        capped = capped[:_DAILY_SUMMARY_MAX_CHARS].rstrip()
        if " " in capped:
            capped = capped.rsplit(" ", 1)[0]
        capped = capped.rstrip(".,!?") + "…"
    return capped


def _editorial_article_card(item, tag="h2", size="standard", show_story_type=False, cap_summary=False):
    """REFERENCE DESIGN CARD (see module docstring): real article image
    left (desktop) / top (mobile) via CSS, category/source pill, large
    Korean headline, Korean summary, up to 3 real supported key points,
    prominent "기사 보기 ->" CTA button. Renders an elegant image-free
    card (never a placeholder/fake photo) when the item has no
    trustworthy image_url -- see _valid_image_url."""
    source_url = item.get("source_url")
    title_text = _e(_display_title(item))
    headline_html = _linked_headline_html(tag, "ed-headline", title_text, source_url)
    pill_label = None
    if show_story_type:
        chip = _MUSIC_STORY_TYPE_CHIPS.get(music_industry_priority_rank(item))
        pill_label = chip[0] if chip else None
    if not pill_label and item.get("source_name"):
        pill_label = _source_label(item["source_name"])
    pill_html = f'<span class="ed-pill">{_e(pill_label)}</span>' if pill_label else ""
    snippet = _display_snippet(item)
    if cap_summary:
        snippet = _cap_daily_summary(snippet)
    summary_html = f'<p class="ed-summary">{_e(snippet)}</p>' if snippet else ""
    bullets_html = _ed_bullets_html(item)
    cta_html = _ed_cta_html(source_url)
    byline_html = _item_byline(item).replace('class="item-byline"', 'class="item-byline ed-byline"')
    image_html = ""
    if _valid_image_url(item.get("image_url")):
        safe_url = html.escape(item["image_url"], quote=True)
        image_html = f'<div class="ed-card-media"><img src="{safe_url}" alt="{title_text}" loading="lazy"></div>'
    body_html = (
        f'<div class="ed-card-body">{pill_html}{headline_html}{summary_html}<hr class="ed-divider">'
        f'{bullets_html}{byline_html}{cta_html}</div>'
    )
    size_class = "ed-card-lead" if size == "lead" else "ed-card-standard"
    # "news-card" stays first in the class list: it's the generic
    # cross-section "this is one article" marker several existing
    # helpers/tests key off (see .news-list .news-card:first-child
    # below), kept alongside the new ed-card/{size_class} classes that
    # actually drive this card's visual design.
    return f'<article class="news-card ed-card {size_class}">{image_html}{body_html}</article>'


def _compact_news_card(item, level="B", feature_label=None, show_story_type=False, cap_summary=False):
    """NEWSLETTER ARTICLE SYSTEM: three visual levels for one real news
    item, never a mechanically identical box repeated for every story --
    Level A (featured, very sparing -- see _render_compact_news_section's
    own tiering) gets a category eyebrow + structured 핵심/영향/지켜볼점
    bullets; Level B (standard) keeps the existing headline+summary+
    optional why-line+byline+link newsletter row; Level C (compact brief)
    drops the summary/why entirely -- headline + byline + link only, for
    lower-priority stories that shouldn't visually compete with A/B.

    `show_story_type` (EDITORIAL INTEGRITY FIX) is only ever passed True
    from Music Industry's own call chain -- music_industry_priority_rank
    is a Music-specific classifier, so AI/ECONOMY/SOCIETY cards never
    render a chip even if their real text incidentally contains a
    music-priority keyword."""
    if level == "A":
        return _editorial_article_card(item, tag="h2", size="lead", show_story_type=show_story_type,
                                        cap_summary=cap_summary)

    if level == "B":
        return _editorial_article_card(item, tag="h3", size="standard", show_story_type=show_story_type,
                                        cap_summary=cap_summary)

    # level == "C": compact brief -- headline + byline + link only,
    # deliberately lighter than the A/B editorial card so it never
    # visually competes with LEAD/IMPORTANT stories.
    byline_html = _item_byline(item)
    source_url = item.get("source_url")
    link_html = _link_html(source_url)
    chip_html = _music_story_type_chip_html(item) if show_story_type else ""
    title_text = _e(_display_title(item))
    headline_html = _linked_headline_html("h4", "news-title news-title-compact", title_text, source_url)
    return (
        f'<article class="news-card news-compact">{chip_html}'
        f'{headline_html}'
        f'{byline_html}{link_html}</article>'
    )


def _ultra_compact_meta_html(item):
    """FINAL DENSITY PASS: source · date · 원문 보기 -> all on one real
    metadata line -- the link is only ever rendered from a real
    item["source_url"]; never fabricated (see _link_html)."""
    bits = []
    if item.get("source_name"):
        bits.append(_e(_source_label(item["source_name"])))
    published = _format_date_kst(item.get("published_at"))
    if published:
        bits.append(f'<span class="num">{published}</span>')
    link_html = _link_html(item.get("source_url"))
    if link_html:
        bits.append(link_html)
    if not bits:
        return ""
    return f'<p class="news-meta-line">{" · ".join(bits)}</p>'


def _render_ultra_compact_row(item):
    """FINAL DENSITY PASS: ECONOMY/SOCIETY are peripheral AWARENESS feeds,
    not deep-read newsletter categories -- every real field this codebase
    already computes for the item (snippet, why_it_matters, reason,
    what_to_watch) still exists on `item` untouched, simply never
    rendered here. Exactly two real lines: headline, then source · date ·
    원문 보기 -> A reader who wants the analysis opens the real source
    article via the real link."""
    title_text = _e(_display_title(item))
    meta_html = _ultra_compact_meta_html(item)
    headline_html = _linked_headline_html("h4", "news-title-ultra", title_text, item.get("source_url"))
    return f'<article class="news-row-compact">{headline_html}{meta_html}</article>'


def _render_compact_news_section(block_class, section_id, label, data, primary_cap, featured=True, secondary_end=4,
                                  show_overflow=True, feature_label=None, ultra_compact=False,
                                  show_story_type=False, cap_summary=False, korean_lead_guard=False):
    state = data["state"]
    message, css_class = _news_state_message(state)
    if message:
        return (
            f'<section class="block {block_class} block-quiet" id="{section_id}">'
            f'<div class="block-head"><h2>{_e(label)}</h2></div>'
            f'<p class="state-message {css_class}">{_e(message)}</p></section>'
        )
    items = data["items"]
    primary, overflow = items[:primary_cap], items[primary_cap:]
    if ultra_compact:
        # FINAL DENSITY PASS: ECONOMY/SOCIETY are peripheral awareness
        # feeds -- every real primary item (including the first) renders
        # as the same two-line compact row, deliberately never a
        # Level A/B/C tiering (that tiering exists to signal "this story
        # is more important," which is the opposite of this category's
        # own real editorial role on this page).
        cards = [_render_ultra_compact_row(item) for item in primary]
    else:
        # NEWSLETTER ARTICLE SYSTEM: TOP STORY (Level A, at most 1, only
        # when `featured` is True for this category) -> SECONDARY
        # (Level B, up to `secondary_end` real items) -> BRIEFING
        # (Level C, the rest of the real primary list) -- never a flat
        # list of equal-weight cards.
        #
        # DAILY KOREAN-READINESS GUARD (korean_lead_guard, AI/ECONOMY/
        # SOCIETY only): Korean-ready items sort before genuinely raw
        # (no Korean title AND no Korean AI-intelligence bullets) items
        # for DISPLAY ONLY -- data["items"]/the real DB ordering is never
        # mutated, only this local rendering-order list. A genuinely raw
        # item can never be Level A or B, only ever the compact Level C
        # brief (headline + byline + link, no summary) -- see
        # _has_displayable_korean_content's own docstring for why an
        # English TITLE alone no longer forces this (a real Korean
        # AI-intelligence summary/bullets must never be thrown away just
        # because the headline itself wasn't translated). Python's sort
        # is stable, so relative order within each group is preserved.
        ordered = (
            sorted(primary, key=lambda item: not _has_displayable_korean_content(item))
            if korean_lead_guard else primary
        )
        cards = []
        for i, item in enumerate(ordered):
            if korean_lead_guard and not _has_displayable_korean_content(item):
                level = "C"
            elif featured and i == 0:
                level = "A"
            elif i < secondary_end:
                level = "B"
            else:
                level = "C"
            cards.append(_compact_news_card(item, level=level, feature_label=feature_label,
                                             show_story_type=show_story_type, cap_summary=cap_summary))
    body = f'<div class="news-list">{"".join(cards)}</div>'
    # CATEGORY-CONTIGUOUS IA REFINEMENT: ECONOMY/SOCIETY get an exact hard
    # cap with NO "more" archive at all (show_overflow=False) -- real
    # overflow beyond the cap is never rendered here, not even collapsed.
    # High-volume collection remains real and internal (unaffected by
    # this display decision); AI/Music Industry still offer a real,
    # collapsed archive.
    if overflow and show_overflow:
        overflow_html = "".join(
            _compact_news_card(item, level="C", show_story_type=show_story_type) for item in overflow
        )
        body += (
            f'<details class="more-disclosure"><summary>더 보기 ({len(overflow)})</summary>'
            f'<div class="news-list">{overflow_html}</div></details>'
        )
    return (
        f'<section class="block {block_class}" id="{section_id}">'
        f'<div class="block-head"><h2>{_e(label)}</h2></div>{body}</section>'
    )


def _merge_music_industry_items(news, exclude_event_key=None, extra_items=()):
    """PROFESSIONAL EDITORIAL QUALITY PASS: re-ranked by real USER
    (songwriter/producer) IMPACT -- see report.web_data_v2.
    rank_music_industry_items's own real priority-class keyword system --
    never by celebrity-name recognition alone.

    MUSIC EVENT EXPOSURE BUDGET: `exclude_event_key` (the real event_key
    of whichever real item became today's LEAD STORY, see
    render_dashboard_html_v2/_lead_signal) is filtered out here -- the
    SAME real event/article must not ALSO occupy Music Industry's own
    top slot as an ordinary, zero-new-information duplicate of the lead
    that was already shown at full editorial weight moments earlier.
    Real event_key identity only -- never a text/title heuristic, so a
    translated or paraphrased headline of the exact same real event is
    still correctly caught.

    MUSIC INDUSTRY AGGRESSIVE NOISE CUT / QUALITY FLOOR (PREMIUM
    INTELLIGENCE UPGRADE PASS): a real DOWNRANKED item (celebrity
    lifestyle/gossip/health/estate-dispute/minor-crime -- see report.
    web_data_v2._MUSIC_INDUSTRY_DOWNRANK_KEYWORDS) is filtered out of
    Music Industry ENTIRELY, not merely sorted to the bottom -- "More"
    must still have a quality floor, never hide garbage inside it. A real
    day with only 3 genuinely qualifying stories shows 3, never padded
    with tabloid filler to look fuller.

    STRICTER QUALITY FLOOR (EDITORIAL INTEGRITY PASS): a real UNRANKED
    item (matches none of the 8 real priority classes -- e.g. an
    ambassador-campaign PR post, routine event photos, ordinary
    promotion) additionally needs real MULTI-outlet corroboration
    (source_count >= 2 -- an already-real, already-computed signal, never
    a new heuristic) to survive; a single-source unranked item is
    filtered out entirely. A real story multiple independent outlets
    found newsworthy enough to cover, even one this keyword system can't
    classify into one of the 8 named classes, still has a real signal
    behind it (e.g. a widely-covered rights/takedown story) and is never
    swept out just because it lacks an exact keyword match -- only a
    single-source promotional item is.

    `extra_items` (PROFESSIONAL EVIDENCE SELECTION RECOVERY, 2026-08-18):
    real items report.web_data_v2.professional_evidence_backfill found --
    genuine professional-class evidence NEWS_COMBINED's own generic
    selection missed, never a fabricated item. Defaults to empty so any
    caller that doesn't pass it (report.web_render_v2.
    render_dashboard_html_v2/DAILY) is provably unaffected; only
    render_music_page_html_v2 passes real backfilled items."""
    spotify_items = news["SPOTIFY"]["items"]
    tiktok_items = news["TIKTOK"]["items"]
    merged = rank_music_industry_items(spotify_items + tiktok_items + list(extra_items))

    def _passes_quality_floor(item):
        # CANDIDATE-SUPPLY BOTTLENECK FIX (2026-08-18, confirmed real
        # defect): the source_count>=2 corroboration requirement for an
        # UNRANKED item silently discarded genuinely useful P1-P3
        # evidence (a TikTok platform-strategy announcement, a real
        # Billboard-200 chart record, a Spotify product-feature launch)
        # -- this pipeline's real MUSIC_INDUSTRY_NEWS sources (Billboard/
        # NME/Rolling Stone/etc, one RSS feed each) essentially never
        # independently cover the exact same story, so this floor was
        # never actually reachable in practice and emptied Industry
        # entirely on a real day with real evidence. The DOWNRANKED
        # keyword filter below (gossip/lifestyle/PR/political -- already
        # proven working) remains the real quality gate.
        priority = music_industry_priority_rank(item)
        return priority != _MUSIC_INDUSTRY_DOWNRANKED_PRIORITY

    merged = [item for item in merged if _passes_quality_floor(item)]
    if exclude_event_key:
        merged = [item for item in merged if item.get("event_key") != exclude_event_key]
    return merged


def _music_industry_state(news):
    spotify_state, tiktok_state = news["SPOTIFY"]["state"], news["TIKTOK"]["state"]
    if news["SPOTIFY"]["items"] or news["TIKTOK"]["items"]:
        return "NORMAL" if (spotify_state != "DEGRADED" or tiktok_state != "DEGRADED") else "DEGRADED"
    if spotify_state == "DEGRADED" and tiktok_state == "DEGRADED":
        return "DEGRADED"
    return "QUIET"


def _render_industry_section(news, exclude_event_key=None, extra_items=()):
    # `_music_industry_state` reads the real, UNFILTERED news lists --
    # the lead-suppression above only ever changes which real items are
    # DISPLAYED, never whether the category honestly had real coverage
    # today (a day whose only industry item became the lead must still
    # read as real NORMAL coverage, never a false-negative "no news").
    merged = {
        "state": _music_industry_state(news),
        "items": _merge_music_industry_items(news, exclude_event_key, extra_items),
    }
    return _render_compact_news_section(
        "block-INDUSTRY", "section-INDUSTRY", "뮤직 인더스트리", merged, _MUSIC_INDUSTRY_PRIMARY_CAP,
        secondary_end=4, feature_label="업계 뉴스", show_story_type=True,
    )


# ---------------------------------------------------------------------
# Spotify Watch (PREMIUM INTELLIGENCE UPGRADE PASS: a permanent required
# watch layer, never a giant new section, never a fixed keyword quota)
# ---------------------------------------------------------------------

_SPOTIFY_WATCH_EMPTY_MESSAGE = "오늘 확인된 중대한 Spotify 정책·비즈니스 변화 없음."
# EDITORIAL INTEGRITY FIX (confirmed real defect: on a real day whose
# strongest Spotify move genuinely IS the Lead Story, the section
# previously said "no major Spotify change" -- semantically false; the
# real move exists, it's just already shown elsewhere, on the same real
# page, moments earlier).
_SPOTIFY_WATCH_ALREADY_LEAD_MESSAGE = "오늘의 주요 Spotify 변화는 Lead Story에서 다룹니다."


def _render_spotify_watch_section(candidates, lead_event_key, producer_intelligence):
    """A compact, single-item intelligence module -- picks the first real
    candidate (already ranked by the SAME real editorial priority scale
    Music Industry uses) that is BOTH a real classified priority class
    (never ordinary promotion/unranked -- see report.web_data_v2.
    _MUSIC_INDUSTRY_UNRANKED_PRIORITY) AND not already shown as today's
    Lead Story (both draw from the same real candidate pool, so an
    unfiltered pick would almost always be a literal duplicate of the
    lead). Two DIFFERENT honest empty states, never conflated: genuinely
    nothing qualifying today vs. today's real qualifying move already
    being the Lead -- the second is never presented as "no change"."""
    top = None
    qualifying_already_lead = False
    for item in candidates:
        if music_industry_priority_rank(item) >= _MUSIC_INDUSTRY_UNRANKED_PRIORITY:
            break  # already real-priority-ranked: nothing further can qualify either
        if lead_event_key and item.get("event_key") == lead_event_key:
            qualifying_already_lead = True
            continue  # already the Lead -- not a second genuinely distinct move
        top = item
        break
    if top is None:
        message = _SPOTIFY_WATCH_ALREADY_LEAD_MESSAGE if qualifying_already_lead else _SPOTIFY_WATCH_EMPTY_MESSAGE
        return (
            '<section class="block block-SPOTIFY block-quiet" id="section-SPOTIFY">'
            '<div class="block-head"><h2>스포티파이 워치</h2></div>'
            f'<p class="state-message">{_e(message)}</p></section>'
        )
    why_it_matters, producer_implication, _extra_refs = resolve_producer_enrichment(top, producer_intelligence)
    title_text = _e(_display_title(top))
    headline_html = _linked_headline_html("h3", "spotify-watch-title", title_text, top.get("source_url"))
    byline_html = _item_byline(top)
    rows = ""
    if why_it_matters:
        rows += f'<p class="spotify-watch-row"><b>왜 중요한가</b> {_e(why_it_matters)}</p>'
    # CURRENT-CACHE SAFETY: same content-creation-advice guard as every
    # other producer_implication/what_could_i_make_now render path above.
    if producer_implication and not is_content_creation_advice(producer_implication):
        rows += f'<p class="spotify-watch-row"><b>프로듀서 시사점</b> {_e(producer_implication)}</p>'
    return (
        '<section class="block block-SPOTIFY" id="section-SPOTIFY">'
        '<div class="block-head"><h2>스포티파이 워치</h2></div>'
        '<div class="spotify-watch-card">'
        '<span class="spotify-watch-label">KEY MOVE</span>'
        f'{headline_html}{byline_html}{rows}'
        '</div></section>'
    )


# ---------------------------------------------------------------------
# Genre Radar / Production Radar
# ---------------------------------------------------------------------

def _evidence_level_label(entry):
    """FACT / OBSERVATION / SIGNAL / TREND / INTELLIGENCE evidence
    discipline (PREMIUM INTELLIGENCE UPGRADE PASS): a real, deterministic
    distinction based on how many independent real evidence citations
    actually support this entry -- never a semantic judgment call, never
    fabricated. A single real citation is an OBSERVATION (one concrete
    example -- e.g. one song's production choice) and is NEVER
    automatically promoted to a market-wide claim; 2+ independent real
    citations is a SIGNAL (multiple real pieces of evidence pointing the
    same way). TREND (repeated movement over TIME) is deliberately never
    returned here -- a single-day synthesis has no real temporal/
    historical accumulation to honestly support that claim."""
    return "SIGNAL" if len(entry.get("evidence") or []) >= 2 else "OBSERVATION"


# Korean display text for _evidence_level_label's internal SIGNAL/OBSERVATION
# values -- the internal value still drives the evidence-level-{...} CSS
# class name (an implementation detail, not user-visible text), only the
# rendered label text changes here (Korean-first UI requirement).
_EVIDENCE_LEVEL_LABELS_KO = {"SIGNAL": "시그널", "OBSERVATION": "관측"}


def _render_radar_items(items, empty_message, variant="genre"):
    """GENRE RADAR / PRODUCTION RADAR must clearly look different from
    each other (real intelligence modules with distinct scope), never
    fabricating a shared visual identity just because both are rule-
    separated radar cards -- the observed-label wording and left-accent
    style differ by `variant`, but neither invents a structured field
    (BPM/HARMONY/etc. as its own labeled slot) the underlying synthesis
    doesn't actually produce; both stay honest free-text observed/
    interpretation prose, never a fabricated schema."""
    if not items:
        return f'<p class="signal-empty quiet-line">{_e(empty_message)}</p>'
    label_text = "오늘 관측" if variant == "genre" else "관측된 프로덕션 특성"
    cards = []
    for item in items:
        evidence_html = _evidence_disclosure_html(item.get("evidence", []))
        confidence = item.get("confidence", "LOW")
        evidence_level = _evidence_level_label(item)
        cards.append(
            f'<div class="radar-card radar-card-{variant}">'
            f'<span class="radar-observed-label">{label_text}</span>'
            f'<span class="evidence-level-badge evidence-level-{evidence_level.lower()}">'
            f'{_EVIDENCE_LEVEL_LABELS_KO.get(evidence_level, evidence_level)}</span>'
            f'<p class="radar-observed">{_e(item["observed"])}</p>'
            f'<p class="radar-interp">{_e(item["interpretation"])}</p>'
            f'{evidence_html}'
            f'<span class="confidence-badge confidence-{confidence}">신뢰도 {CONFIDENCE_LABELS.get(confidence, confidence)}</span>'
            '</div>'
        )
    return f'<div class="radar-list radar-list-{variant}">{"".join(cards)}</div>'


def _render_genre_radar_section(trend):
    # MUSIC EVENT EXPOSURE BUDGET: genre_signals arrives HERE already
    # filtered by render_dashboard_html_v2's own real event-level budget
    # pass (see _apply_music_event_exposure_budget) -- this function stays
    # a pure, budget-agnostic renderer of whatever real list it's given.
    signals = trend.get("genre_signals") or []
    if trend["state"] != "NORMAL" or not signals:
        body = f'<p class="state-message">{_e(_MUSIC_TREND_EMPTY_MESSAGES["genre_signals"] if trend["state"] == "NORMAL" else _MUSIC_TREND_UNAVAILABLE_MESSAGE)}</p>'
        quiet = " block-quiet"
    else:
        body = _render_radar_items(signals, _MUSIC_TREND_EMPTY_MESSAGES["genre_signals"], variant="genre")
        quiet = ""
    return (
        f'<section class="block block-GENRE{quiet}" id="section-GENRE">'
        f'<div class="block-head"><h2>장르 레이더</h2></div>{body}</section>'
    )


def _render_production_radar_section(trend):
    notes = trend.get("production_notes") or []
    if trend["state"] != "NORMAL" or not notes:
        body = f'<p class="state-message">{_e(_MUSIC_TREND_EMPTY_MESSAGES["production_notes"] if trend["state"] == "NORMAL" else _MUSIC_TREND_UNAVAILABLE_MESSAGE)}</p>'
        quiet = " block-quiet"
    else:
        body = _render_radar_items(notes, _MUSIC_TREND_EMPTY_MESSAGES["production_notes"], variant="production")
        quiet = ""
    return (
        f'<section class="block block-PRODUCTION{quiet}" id="section-PRODUCTION">'
        f'<div class="block-head"><h2>프로덕션 레이더</h2></div>{body}</section>'
    )


# ---------------------------------------------------------------------
# Producer / A&R Takeaways (Producer Intelligence insights + K-pop/A&R)
# ---------------------------------------------------------------------

def _render_producer_takeaway_card(insight):
    """PROFESSIONAL EDITORIAL QUALITY PASS: exactly 3 real rows -- SIGNAL
    (observed fact) / SO WHAT (why it matters) / TRY-OR-WATCH.

    PRODUCER/A&R INFERENCE-DISTANCE CONTROL (PREMIUM INTELLIGENCE UPGRADE
    PASS): a real LOW-confidence insight never gets a prescriptive TRY/
    ACTION row -- only its own real what_to_watch, labeled WATCH. MEDIUM/
    HIGH confidence keep the existing combined TRY/WATCH row (both real
    fields, condensed onto one compact row rather than two separate ones
    -- neither real field dropped, only the visual footprint reduced,
    matching "highly actionable, no essay-length cards"). This never
    invents text -- it only controls which of the insight's own already-
    validated real fields are shown, based on the SAME real confidence
    the synthesis itself assigned."""
    evidence_html = _evidence_disclosure_html(insight.get("evidence", []))
    confidence = insight.get("confidence", "LOW")
    # CURRENT-CACHE SAFETY (FINAL 90+ QUALITY CORRECTION PASS): some
    # already-cached insights (generated before report.producer_synthesis's
    # own prompt fix) recommend writing a newsletter/article/explainer
    # instead of a real music-making action -- never shown as producer
    # advice, regardless of confidence. Falls back to WATCH-only exactly
    # like the LOW-confidence path when the action is suppressed.
    show_action = not is_content_creation_advice(insight["what_could_i_make_now"])
    if confidence == "LOW" or not show_action:
        action_row = f'<p class="takeaway-row"><b>지켜볼 점</b> {_e(insight["what_to_watch"])}</p>'
    else:
        action_row = (
            f'<p class="takeaway-row"><b>시도 · 지켜볼 점</b> '
            f'{_e(insight["what_could_i_make_now"])} · {_e(insight["what_to_watch"])}</p>'
        )
    return (
        '<div class="takeaway-card">'
        '<span class="takeaway-label">시그널</span>'
        f'<p class="takeaway-action">{_e(insight["what_is_moving"])}</p>'
        f'<p class="takeaway-row"><b>왜 중요한가</b> {_e(insight["why_it_matters"])}</p>'
        f'{action_row}'
        f'{evidence_html}'
        f'<span class="confidence-badge confidence-{confidence}">신뢰도 {CONFIDENCE_LABELS.get(confidence, confidence)}</span>'
        '</div>'
    )


def _render_labeled_trend_card(label, item):
    """Shared card for the two music_trend_intelligence lists that belong
    in Producer / A&R Takeaways rather than their own top-level section:
    producer_references (real producer/songwriter/collaborator credits
    explicitly stated in the catalog text) and kpop_ar_notes (K-pop/A&R
    relevance) -- both real, already-validated LLM synthesis, never
    generated here."""
    evidence_html = _evidence_disclosure_html(item.get("evidence", []))
    confidence = item.get("confidence", "LOW")
    return (
        '<div class="takeaway-card">'
        f'<span class="takeaway-label">{_e(label)}</span>'
        f'<p class="takeaway-action">{_e(item["observed"])}</p>'
        f'<p class="takeaway-row">{_e(item["interpretation"])}</p>'
        f'{evidence_html}'
        f'<span class="confidence-badge confidence-{confidence}">신뢰도 {CONFIDENCE_LABELS.get(confidence, confidence)}</span>'
        '</div>'
    )


# PROFESSIONAL EDITORIAL QUALITY PASS: "MAX 3 primary takeaways" -- a
# real hard display cap on the COMBINED list (Producer Intelligence
# insights + producer_references + K-pop/A&R notes can together exceed 3
# before capping, since each is independently bounded upstream at its own,
# larger max). Real overflow folds into progressive disclosure, never
# dropped.
_TAKEAWAY_PRIMARY_CAP = 3


_TAKEAWAY_RENDERERS = {
    "producer": _render_producer_takeaway_card,
    "reference": lambda i: _render_labeled_trend_card("프로듀서 레퍼런스", i),
    "kpop": lambda i: _render_labeled_trend_card("K-pop / A&R", i),
}


def _render_producer_section(producer_intelligence, trend):
    # MUSIC EVENT EXPOSURE BUDGET: producer_intelligence["insights"] and
    # trend["kpop_ar_notes"]/["producer_references"] all arrive HERE
    # already filtered by render_dashboard_html_v2's own real event-level
    # budget pass (see _apply_music_event_exposure_budget) -- this
    # function stays a pure, budget-agnostic renderer.
    reference_items = trend.get("producer_references") or [] if trend.get("state") == "NORMAL" else []
    kpop_items = trend.get("kpop_ar_notes") or [] if trend.get("state") == "NORMAL" else []
    producer_insights = producer_intelligence["insights"] if producer_intelligence["state"] == "NORMAL" else []
    # PRODUCER/A&R GOSSIP GUARD (2026-08-17, confirmed real defect): this
    # section draws from THREE separate real pipelines (Producer
    # Intelligence's own insights, plus music_trend_intelligence's
    # producer_references/kpop_ar_notes) -- none of which is filtered by
    # the INDUSTRY-section/hero gossip downrank keywords in report.
    # web_data_v2, so a pure fan/social-comment gossip item could still
    # reach a Producer/A&R card even though the identical item is already
    # downranked out of INDUSTRY/hero. Applied here, before the quality
    # cap below, so a rejected gossip item never counts as "the only
    # insight" that cap would otherwise fall back to keeping.
    producer_insights = [
        i for i in producer_insights
        if not is_low_value_gossip_takeaway(i.get("what_is_moving"))
        and not is_no_evidence_meta_statement(i.get("what_is_moving"))
    ]
    reference_items = [
        i for i in reference_items
        if not is_low_value_gossip_takeaway(i.get("observed"))
        and not is_no_evidence_meta_statement(i.get("observed"))
    ]
    kpop_items = [
        i for i in kpop_items
        if not is_low_value_gossip_takeaway(i.get("observed"))
        and not is_no_evidence_meta_statement(i.get("observed"))
    ]
    # PRODUCER/A&R QUALITY CAP (EDITORIAL INTEGRITY PASS): a quality
    # ceiling, not a quota -- a real LOW-confidence insight must not
    # survive merely to pad the count when genuinely stronger (MEDIUM/
    # HIGH) real insights already exist that day. Only falls back to
    # keeping the real LOW-confidence insight(s) when NOTHING stronger
    # exists at all -- never a forced empty section when real content,
    # even weak, is all there is.
    stronger_insights = [i for i in producer_insights if i.get("confidence") != "LOW"]
    if stronger_insights:
        producer_insights = stronger_insights
    has_producer = bool(producer_insights)
    if not has_producer and not reference_items and not kpop_items:
        body = f'<p class="state-message">{_e(_PRODUCER_EMPTY_MESSAGE)}</p>'
        quiet = " block-quiet"
    else:
        all_cards = [("producer", i) for i in (producer_insights if has_producer else [])]
        all_cards += [("reference", i) for i in reference_items]
        all_cards += [("kpop", i) for i in kpop_items]
        primary, overflow = all_cards[:_TAKEAWAY_PRIMARY_CAP], all_cards[_TAKEAWAY_PRIMARY_CAP:]

        def render(kind, insight):
            return _TAKEAWAY_RENDERERS[kind](insight)

        cards = [render(kind, insight) for kind, insight in primary]
        body = f'<div class="takeaway-list">{"".join(cards)}</div>'
        if overflow:
            overflow_html = "".join(render(kind, insight) for kind, insight in overflow)
            body += (
                f'<details class="more-disclosure"><summary>더 보기 ({len(overflow)})</summary>'
                f'<div class="takeaway-list">{overflow_html}</div></details>'
            )
        quiet = ""
    return (
        f'<section class="block block-PRODUCER{quiet}" id="section-PRODUCER">'
        f'<div class="block-head"><h2>프로듀서 / A&amp;R 테이크어웨이</h2></div>{body}</section>'
    )


# ---------------------------------------------------------------------
# Cross-Platform Signals (cross-platform + early signal + catalog revival)
# ---------------------------------------------------------------------

def _render_signals_section(intelligence):
    cross_platform = intelligence.get("cross_platform") or []
    early_signal = intelligence.get("early_signal") or {}
    catalog_revival = intelligence.get("catalog_revival") or {}
    any_early = any(candidates for candidates in early_signal.values())
    any_revival = any(candidates for candidates in catalog_revival.values())

    if not cross_platform and not any_early and not any_revival:
        outlook = intelligence.get("outlook", {})
        lines = "".join(
            f'<p class="quiet-line">{_e(_source_label(name))} 관측 {info["days_of_history"]}/{info["min_required_days"]}일</p>'
            for name, info in sorted(outlook.items())
        )
        cross_platform_state = intelligence.get("cross_platform_state")
        cp_message = {
            "INSUFFICIENT_SOURCES": "2개 이상 플랫폼 데이터 부족",
            "INSUFFICIENT_HISTORY": "일부 플랫폼 관측 이력 부족",
            "NO_SIGNAL": "동시 신호 없음",
        }.get(cross_platform_state, "동시 신호 없음")
        body = f'<p class="quiet-line">Cross-Platform · {_e(cp_message)}</p>{lines}'
        quiet = " block-quiet"
    else:
        rows = []
        for entry in cross_platform:
            sources = " · ".join(_e(_source_label(s)) for s in entry["sources"])
            rows.append(
                f'<li class="signal-compact-row"><span class="signal-compact-track">{_e(entry["canonical_artist"])} - {_e(entry["canonical_title"])}</span>'
                f'<span class="signal-compact-meta">{len(entry["sources"])}개 소스 동시 확인</span>'
                f'<span class="signal-compact-detail">{sources}</span></li>'
            )
        for source_name in sorted(early_signal.keys()):
            for c in early_signal[source_name]:
                rows.append(
                    f'<li class="signal-compact-row"><span class="signal-compact-track">{_e(c["canonical_artist"])} - {_e(c["canonical_title"])}</span>'
                    f'<span class="signal-compact-meta">{_e(_source_label(source_name))} Early Signal ▲{int(c["rank_delta"])}</span></li>'
                )
        for source_name in sorted(catalog_revival.keys()):
            for c in catalog_revival[source_name]:
                rows.append(
                    f'<li class="signal-compact-row"><span class="signal-compact-track">{_e(c["canonical_artist"])} - {_e(c["canonical_title"])}</span>'
                    f'<span class="signal-compact-meta">{_e(_source_label(source_name))} Catalog Revival · {c["gap_days"]}일 공백</span></li>'
                )
        body = f'<ul class="signal-compact-list">{"".join(rows)}</ul>'
        quiet = ""
    return (
        f'<section class="block block-SIGNALS{quiet}" id="section-SIGNALS">'
        f'<div class="block-head"><h2>크로스플랫폼 시그널</h2></div>{body}</section>'
    )


# ---------------------------------------------------------------------
# 3-6 Month Outlook
# ---------------------------------------------------------------------

def _render_outlook_section(intelligence):
    outlook = intelligence.get("outlook", {})
    any_ready = any(info["status"] == "READY" for info in outlook.values())
    if not any_ready:
        body = f'<p class="quiet-line">{_e(_OUTLOOK_INSUFFICIENT_LINE)}</p>'
        quiet = " block-quiet"
    else:
        rows = []
        for name in sorted(outlook.keys()):
            info = outlook[name]
            status_text = "예측 가능" if info["status"] == "READY" else "데이터 부족"
            rows.append(f'<p class="quiet-line">{_e(_source_label(name))} · {status_text} · {info["days_of_history"]}일 관측</p>')
        body = "".join(rows)
        quiet = ""
    return (
        f'<section class="block block-OUTLOOK{quiet}" id="section-OUTLOOK">'
        f'<div class="block-head"><h2>3~6개월 전망</h2></div>{body}</section>'
    )


# ---------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------

def _render_sources_section(intelligence):
    chips = []
    for source_name in sorted(intelligence["early_signal"].keys()):
        chips.append(
            f'<li class="source-status-chip status-active"><span class="status-dot"></span>{_e(_source_label(source_name))} 연동됨</li>'
        )
    chips.append('<li class="source-status-chip status-unavailable"><span class="status-dot"></span>TikTok 미연동</li>')
    body = '<ul class="source-status-list">' + "".join(chips) + "</ul>"
    return (
        '<section class="block block-SOURCES" id="section-SOURCES">'
        '<div class="block-head"><h2>출처</h2></div>' + body + "</section>"
    )


def _render_nav():
    """Thin horizontal publication nav, same on every viewport (no more
    desktop-rail/mobile-chip-strip split): MUSIC | CHARTS | INDUSTRY |
    RADAR | PRODUCER | AI | 경제 | 사회, plus the load-bearing MUSIC
    INTELLIGENCE badge (see NAV_MUSIC_INTELLIGENCE_BADGE)."""
    links_html = "".join(
        f'<a class="pub-nav-link" href="#{anchor}">{_e(label)}</a>' for label, anchor in NAV_HORIZONTAL_LINKS
    )
    return (
        '<nav class="pub-nav" aria-label="Section navigation">'
        f'<span class="pub-nav-badge">{_e(NAV_MUSIC_INTELLIGENCE_BADGE)}</span>'
        f'<div class="pub-nav-links">{links_html}</div></nav>'
    )


def render_dashboard_html_v2(dashboard_data):
    """dashboard_data: the exact shape report.web_data_v2.
    build_dashboard_data_v2() returns. Returns a complete, self-contained
    HTML document string."""
    report_date_kst = dashboard_data["report_date_kst"]
    news = dashboard_data["news"]
    intelligence = dashboard_data["intelligence"]
    spotify_chart = dashboard_data["spotify_chart"]
    y, m, d = report_date_kst.split("-")

    today_intel_html = _render_today_music_intelligence(dashboard_data["today_music_intelligence"])

    # MUSIC EVENT EXPOSURE BUDGET (TRUE event-level identity -- see the
    # dedicated section above _render_today_music_intelligence): whichever
    # real signal became today's LEAD STORY (same real fallback rule
    # _render_today_music_intelligence itself uses, via the shared
    # _lead_signal helper) gets a real hard cap of 2 total visible
    # exposures across the whole MUSIC block -- itself, plus at most 1
    # further real, genuinely distinct interpretation. Deterministic
    # identity only (real event_key / real evidence refs) -- never a text
    # heuristic, never an LLM call.
    lead_signal = _lead_signal(dashboard_data["today_music_intelligence"])
    title_to_event_key = _news_title_to_event_key_map(news)
    lead_event_key, lead_refs = (None, set())
    if lead_signal:
        lead_event_key, lead_refs = _signal_event_identity(lead_signal, title_to_event_key)

    music_today = _exclude_lead_event_from_today_in_music(
        dashboard_data["music_today"], lead_event_key, lead_refs, title_to_event_key,
    )

    trend = dashboard_data["music_trend_intelligence"]
    producer_intelligence = dashboard_data["producer_intelligence"]
    genre_signals, production_notes, producer_insights, producer_references, kpop_ar_notes = (
        _apply_music_event_exposure_budget(
            [
                trend.get("genre_signals") or [], trend.get("production_notes") or [],
                producer_intelligence.get("insights") or [], trend.get("producer_references") or [],
                trend.get("kpop_ar_notes") or [],
            ],
            lead_event_key, lead_refs, title_to_event_key,
        )
    )
    producer_insights, producer_references, kpop_ar_notes = _dedupe_producer_section_exact_duplicates(
        producer_insights, producer_references, kpop_ar_notes, title_to_event_key,
    )
    trend_for_render = {
        **trend, "genre_signals": genre_signals, "production_notes": production_notes,
        "producer_references": producer_references, "kpop_ar_notes": kpop_ar_notes,
    }
    producer_intelligence_for_render = {**producer_intelligence, "insights": producer_insights}

    cross_platform = intelligence.get("cross_platform") or []
    viral_hot_top = None
    viral_new_top = None
    if spotify_chart["state"] == "NORMAL":
        hot = select_viral_hot(spotify_chart["top10"])
        viral_hot_top = hot[0] if hot else None
        new_notable = select_viral_new(spotify_chart["new_entries"])
        viral_new_top = new_notable[0] if new_notable else None

    music_sections_html = "".join([
        _render_music_today_section(music_today),
        _render_chart_pulse_section(spotify_chart, cross_platform, viral_hot_top, viral_new_top),
        # MUSIC INDUSTRY: ZERO tolerance too -- an ordinary second real
        # article about the lead's own real event from a different real
        # outlet is never a genuinely distinct interpretation (only Genre/
        # Production/Producer's real ANALYTICAL synthesis is eligible for
        # the one allowed slot above).
        _render_industry_section(news, exclude_event_key=lead_event_key),
        _render_spotify_watch_section(
            dashboard_data.get("spotify_watch_candidates") or [], lead_event_key, producer_intelligence,
        ),
        _render_genre_radar_section(trend_for_render),
        _render_production_radar_section(trend_for_render),
        _render_producer_section(producer_intelligence_for_render, trend_for_render),
        _render_signals_section(intelligence),
        _render_outlook_section(intelligence),
    ])

    # NEWSLETTER x INTELLIGENCE HYBRID REDESIGN: a real, stronger-than-
    # ordinary section break marks each category boundary ONCE (never
    # per-section within MUSIC's own multi-section block, since that
    # block is itself already contiguous) -- so a reader always knows
    # exactly when they've left MUSIC.
    sections_html = "".join([
        music_sections_html,
        _category_transition_html("AI", "AI INTELLIGENCE"),
        _render_compact_news_section("block-AI", "section-AI", "AI", news["AI"], _AI_PRIMARY_CAP, secondary_end=4, cap_summary=True, korean_lead_guard=True),
        _category_transition_html("ECONOMY", "ECONOMY"),
        _render_compact_news_section("block-ECONOMY", "section-ECONOMY", "경제", news["ECONOMY"], _ECON_SOCIETY_PRIMARY_CAP, secondary_end=2, show_overflow=False, cap_summary=True, korean_lead_guard=True),
        _category_transition_html("SOCIETY", "SOCIETY"),
        _render_compact_news_section("block-SOCIETY", "section-SOCIETY", "사회", news["SOCIETY"], _ECON_SOCIETY_PRIMARY_CAP, secondary_end=2, show_overflow=False, cap_summary=True, korean_lead_guard=True),
        _render_sources_section(intelligence),
    ])

    reading_minutes = _estimate_reading_minutes(today_intel_html, sections_html)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SUPER NEWS V2 — {y}.{m}.{d}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="masthead">
<div class="brand-row"><span class="brand">SUPER NEWS</span><span class="tagline">Daily Music Intelligence</span></div>
<div class="meta-row"><div class="date num">{y}.{m}.{d}</div><span class="read-time num">{reading_minutes} MIN READ</span></div>
</div>
{_render_nav()}
{today_intel_html}
<main class="main">
{sections_html}
</main>
<footer>이 페이지는 매일 자동으로 갱신됩니다.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------
# SUPER NEWS MUSIC / SUPER NEWS DAILY -- two genuinely separate product
# pages (REFERENCE DESIGN split, see module docstring). Neither page
# duplicates any selection/synthesis/dedup logic above -- both call the
# exact same internal section-render helpers render_dashboard_html_v2
# itself uses; render_dashboard_html_v2 is left untouched for its own
# existing callers/tests/release gate (report/release_v2.py's
# NAV_MUSIC_INTELLIGENCE_BADGE marker check on docs/v2/index.html).
# ---------------------------------------------------------------------

_NAV_MUSIC_LINKS = (
    ("음악", "today-intel"), ("음악 산업", "section-INDUSTRY"),
    ("Spotify", "section-SPOTIFY"), ("레이더", "section-GENRE"), ("프로듀서", "section-PRODUCER"),
    ("차트", "section-CHARTPULSE"),
)
_NAV_DAILY_LINKS = (
    ("AI", "section-AI"), ("경제", "section-ECONOMY"), ("사회", "section-SOCIETY"),
)


def _render_product_nav(links, badge=None):
    links_html = "".join(
        f'<a class="pub-nav-link" href="#{anchor}">{_e(label)}</a>' for label, anchor in links
    )
    badge_html = f'<span class="pub-nav-badge">{_e(badge)}</span>' if badge else ""
    return (
        '<nav class="pub-nav" aria-label="Section navigation">'
        f'{badge_html}<div class="pub-nav-links">{links_html}</div></nav>'
    )


def resolve_music_lead_and_industry(dashboard_data):
    """KAKAO/WEB CONTENT-IDENTITY CONSOLIDATION (2026-08-18, confirmed real
    defect: report.kakao_render_v2.render_music_kakao_digest independently
    re-derived its own Industry line straight from raw dashboard_data
    ["news"]["TIKTOK"/"SPOTIFY"]["items"], bypassing the SAME real Lead-
    exclusion / priority-ranking / professional-evidence-backfill /
    quality-floor pipeline render_music_page_html_v2 already applies below
    -- so Kakao could show a lower-priority or even lead-duplicate story
    while the web page correctly showed a stronger one). The ONE real
    computation of "today's Lead" and "today's final, deduped, backfilled
    Industry pool" -- every MUSIC consumer (the web page below AND
    report.kakao_render_v2.render_music_kakao_digest) calls this SAME
    function so they can never independently diverge again. Returns
    (lead_signal, lead_event_key, lead_refs, industry_items,
    title_to_event_key)."""
    news = dashboard_data["news"]
    # .get(...) or [] -- not direct indexing: build_dashboard_data_v2
    # always sets this key in production, but a minimal hand-built test
    # dashboard (or a future caller assembling a partial dict) must not
    # hard-crash just because it omitted a key this function itself
    # never writes to.
    lead_signal = _lead_signal(dashboard_data.get("today_music_intelligence") or [])
    title_to_event_key = _news_title_to_event_key_map(news)
    lead_event_key, lead_refs = (None, set())
    if lead_signal:
        lead_event_key, lead_refs = _signal_event_identity(lead_signal, title_to_event_key)

    professional_backfill = dashboard_data.get("music_professional_backfill") or {}
    industry_extra_items = (
        list(professional_backfill.get("SPOTIFY") or []) + list(professional_backfill.get("TIKTOK") or [])
    )
    industry_items = _merge_music_industry_items(
        news, exclude_event_key=lead_event_key, extra_items=industry_extra_items,
    )
    return lead_signal, lead_event_key, lead_refs, industry_items, title_to_event_key


def render_music_page_html_v2(dashboard_data):
    """SUPER NEWS MUSIC -- a standalone product page: today's music
    intelligence, chart pulse, music industry news, genre/production
    radar, producer/A&R takeaways, cross-platform signals, outlook.
    NEVER includes AI/ECONOMY/SOCIETY -- see module docstring's
    product-separation rule and render_daily_page_html_v2's docstring
    for the DAILY counterpart."""
    report_date_kst = dashboard_data["report_date_kst"]
    news = dashboard_data["news"]
    intelligence = dashboard_data["intelligence"]
    spotify_chart = dashboard_data["spotify_chart"]
    y, m, d = report_date_kst.split("-")

    lead_signal, lead_event_key, lead_refs, industry_items_for_dedup, title_to_event_key = (
        resolve_music_lead_and_industry(dashboard_data)
    )

    hero_secondary_raw = [
        s for s in dashboard_data["today_music_intelligence"] if s is not lead_signal
    ][:_HERO_SECONDARY_MAX]

    # FINAL EDITORIAL QUALITY PASS (2026-08-18, confirmed real defect):
    # Music Industry is rendered near the TOP of this page (see
    # sections_html below), but every downstream dedup pass here
    # previously only ever tracked the LEAD's own identity -- a non-lead
    # Industry item (e.g. a Spotify product-feature launch, a TikTok
    # platform announcement) could reappear, reworded, in Today-in-Music/
    # Genre/Production/Producer with no limit. Computed once, from the
    # SAME real `_merge_music_industry_items` pool Industry itself
    # renders from, so "already shown" always means the literal set of
    # events the reader just saw a moment ago -- never a second guess.
    # PROFESSIONAL EVIDENCE SELECTION RECOVERY (2026-08-18): real backfilled
    # items report.web_data_v2.professional_evidence_backfill already
    # computed (build_dashboard_data_v2 time, MUSIC-page-only key -- see
    # its own docstring), already folded into industry_items_for_dedup by
    # resolve_music_lead_and_industry above -- re-derived here only for
    # _render_industry_section's own extra_items argument below (same
    # values, not a second computation of the merge itself).
    professional_backfill = dashboard_data.get("music_professional_backfill") or {}
    industry_extra_items = list(professional_backfill.get("SPOTIFY") or []) + list(professional_backfill.get("TIKTOK") or [])
    industry_event_keys = {item.get("event_key") for item in industry_items_for_dedup if item.get("event_key")}

    music_today = _exclude_lead_event_from_today_in_music(
        dashboard_data["music_today"], lead_event_key, lead_refs, title_to_event_key,
        extra_excluded_event_keys=industry_event_keys,
    )

    trend = dashboard_data["music_trend_intelligence"]
    producer_intelligence = dashboard_data["producer_intelligence"]
    genre_signals, production_notes, producer_insights, producer_references, kpop_ar_notes = (
        _apply_music_event_exposure_budget(
            [
                trend.get("genre_signals") or [], trend.get("production_notes") or [],
                producer_intelligence.get("insights") or [], trend.get("producer_references") or [],
                trend.get("kpop_ar_notes") or [],
            ],
            lead_event_key, lead_refs, title_to_event_key,
            extra_shown_event_keys=industry_event_keys,
        )
    )
    # SEMANTIC DUPLICATION GUARD (second pass, cross-section vs EACH
    # OTHER -- not just vs the lead like the exposure-budget pass above):
    # see _apply_full_music_cross_section_dedup's own docstring for the
    # confirmed real defect this closes.
    known_chart_keys = known_chart_entity_keys(spotify_chart)
    (
        hero_secondary, music_today, genre_signals, production_notes,
        producer_insights, producer_references, kpop_ar_notes,
    ) = _apply_full_music_cross_section_dedup(
        hero_secondary_raw, music_today, genre_signals, production_notes,
        producer_insights, producer_references, kpop_ar_notes,
        lead_event_key, title_to_event_key, known_chart_keys,
        extra_seed_event_keys=industry_event_keys,
    )
    producer_insights, producer_references, kpop_ar_notes = _dedupe_producer_section_exact_duplicates(
        producer_insights, producer_references, kpop_ar_notes, title_to_event_key,
    )

    # VISIBLE ORDER FIX: 오늘의 음악 소식 (today-secondary) moves out of
    # .today-intel and renders AFTER section-INDUSTRY instead -- required
    # visible order is 업계 뉴스 lead -> 뮤직 인더스트리 -> 오늘의 음악 소식
    # -> 뮤직 투데이 -> 차트 펄스. today_intel_html here is the LEAD-ONLY
    # fragment (heading + lead story); today_secondary_html is inserted
    # into sections_html below, right after Music Industry. Uses the
    # already cross-section-deduped hero_secondary list computed above.
    today_intel_html, today_secondary_html = _today_intel_parts(
        dashboard_data["today_music_intelligence"], secondary_override=hero_secondary,
    )
    trend_for_render = {
        **trend, "genre_signals": genre_signals, "production_notes": production_notes,
        "producer_references": producer_references, "kpop_ar_notes": kpop_ar_notes,
    }
    producer_intelligence_for_render = {**producer_intelligence, "insights": producer_insights}

    cross_platform = intelligence.get("cross_platform") or []
    viral_hot_top = None
    viral_new_top = None
    if spotify_chart["state"] == "NORMAL":
        hot = select_viral_hot(spotify_chart["top10"])
        viral_hot_top = hot[0] if hot else None
        new_notable = select_viral_new(spotify_chart["new_entries"])
        viral_new_top = new_notable[0] if new_notable else None

    sections_html = "".join([
        # SECTION ORDER: Music Industry groups the industry-reading flow
        # together immediately after the lead, followed by 오늘의 음악
        # 소식 (relocated here from .today-intel, see above), then Music
        # Today and the chart-heavy sections -- internal design of these
        # sections is unchanged, this is a position-only change.
        _render_industry_section(news, exclude_event_key=lead_event_key, extra_items=industry_extra_items),
        today_secondary_html,
        _render_music_today_section(music_today),
        _render_spotify_watch_section(
            dashboard_data.get("spotify_watch_candidates") or [], lead_event_key, producer_intelligence,
        ),
        _render_genre_radar_section(trend_for_render),
        _render_production_radar_section(trend_for_render),
        _render_producer_section(producer_intelligence_for_render, trend_for_render),
        _render_signals_section(intelligence),
        _render_outlook_section(intelligence),
        # CHART PULSE MOVED TO THE BOTTOM (position-only change, design/
        # data untouched): the last REAL content section, still before the
        # technical source-status section.
        _render_chart_pulse_section(spotify_chart, cross_platform, viral_hot_top, viral_new_top),
        _render_sources_section(intelligence),
    ])

    reading_minutes = _estimate_reading_minutes(today_intel_html, sections_html)
    nav_html = _render_product_nav(_NAV_MUSIC_LINKS, badge=NAV_MUSIC_INTELLIGENCE_BADGE)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SUPER NEWS MUSIC — {y}.{m}.{d}</title>
<style>{_STYLE}</style>
</head>
<body class="page-music">
<div class="masthead">
<div class="brand-row"><span class="brand">SUPER NEWS <span class="brand-edition-music">MUSIC</span></span><span class="tagline">작곡가·프로듀서를 위한 오늘의 음악 인텔리전스</span></div>
<div class="meta-row"><div class="date num">{y}.{m}.{d}</div><span class="read-time num">읽는 시간 {reading_minutes}분</span></div>
</div>
{nav_html}
{today_intel_html}
<main class="main">
{sections_html}
</main>
<footer>이 페이지는 매일 자동으로 갱신됩니다.</footer>
</body>
</html>
"""


def render_daily_page_html_v2(dashboard_data):
    """SUPER NEWS DAILY -- a standalone product page: AI/ECONOMY/SOCIETY
    general news only. NEVER includes music industry content -- see
    render_music_page_html_v2's docstring for the MUSIC counterpart."""
    report_date_kst = dashboard_data["report_date_kst"]
    news = dashboard_data["news"]
    y, m, d = report_date_kst.split("-")

    # DAILY drops the Apple Music/Spotify/TikTok source-status section
    # entirely -- that status is a MUSIC-product concept (see
    # render_music_page_html_v2, which still shows it), meaningless on a
    # pure AI/ECONOMY/SOCIETY page.
    sections_html = "".join([
        _render_compact_news_section("block-AI", "section-AI", "AI", news["AI"], _AI_PRIMARY_CAP, secondary_end=4, cap_summary=True, korean_lead_guard=True),
        _render_compact_news_section("block-ECONOMY", "section-ECONOMY", "경제", news["ECONOMY"], _ECON_SOCIETY_PRIMARY_CAP, secondary_end=2, show_overflow=False, cap_summary=True, korean_lead_guard=True),
        _render_compact_news_section("block-SOCIETY", "section-SOCIETY", "사회", news["SOCIETY"], _ECON_SOCIETY_PRIMARY_CAP, secondary_end=2, show_overflow=False, cap_summary=True, korean_lead_guard=True),
    ])

    reading_minutes = _estimate_reading_minutes(sections_html)
    nav_html = _render_product_nav(_NAV_DAILY_LINKS, badge="SUPER NEWS DAILY")

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SUPER NEWS DAILY — {y}.{m}.{d}</title>
<style>{_STYLE}</style>
</head>
<body class="page-daily">
<div class="masthead">
<div class="brand-row"><span class="brand">SUPER NEWS <span class="brand-edition-daily">DAILY</span></span><span class="tagline">AI · 경제 · 사회 핵심 브리핑</span></div>
<div class="meta-row"><div class="date num">{y}.{m}.{d}</div><span class="read-time num">읽는 시간 {reading_minutes}분</span></div>
</div>
{nav_html}
<main class="main">
{sections_html}
</main>
<footer>이 페이지는 매일 자동으로 갱신됩니다.</footer>
</body>
</html>
"""

"""Presentation-only editorial Intelligence Dashboard renderer for Report
V2.1.

Consumes ONLY the structured facts report.web_data_v2.build_dashboard_
data_v2() already reads/computes from persisted data. This module makes
zero independent judgment about what matters: no new scores, rankings,
selections, or forecasts -- it only lays out already-computed facts
visually, exactly like report/web_render.py (V1) does for its own data.
The one exception is Producer Intelligence, whose actual synthesis
(action/why/evidence/confidence) already happened upstream in report/
producer_synthesis.py -- this module still only lays that out, it never
generates or edits a word of it.

Additive alongside report/web_render.py (V1) -- does not modify or replace
it, and is not wired into production generation in this pass.

Layout: a sticky category rail + a wider editorial canvas (desktop is NOT
capped at the old ~800px document width), collapsing to a horizontally
scrollable chip strip above a natural single-column flow on narrow/mobile
viewports (including Kakao's in-app browser, which gets no special-cased
markup -- it's a standard mobile WebView). No JS anywhere.

Section order: TODAY IN 30 SECONDS / TIKTOK / SPOTIFY / VIRAL & TRENDS /
INTELLIGENCE / MUSIC INDUSTRY / AI / ECONOMY / SOCIETY / PRODUCER
INTELLIGENCE / SOURCES.

- TIKTOK and SPOTIFY show only real chart facts. TikTok has no data source
  yet -- always an honest "not yet integrated" state, never substituted
  with Apple Music or fabricated.
- VIRAL & TRENDS (Viral Hot / Viral·New / Daily Music Trend) reads the
  SAME spotify_chart top10/new_entries SPOTIFY already showed, but each
  subsection applies a distinct lens (movement magnitude with peak-rank/
  days-on-chart context; debut framing; aggregate up/down/new counts) so
  no fact is repeated verbatim -- see report/web_data_v2.py's own
  docstring on why source_count/peak_rank/tier exist.
- INTELLIGENCE presents Early Signal, Catalog Revival, Cross-Platform
  Movement, and Future Radar (forecast-readiness) per already-active
  source, in whatever honest state they compute to.
- MUSIC INDUSTRY groups the curated editorial news items for the TIKTOK
  and SPOTIFY news categories -- journalism about the platforms, kept
  separate from chart data above. Uses the same LEAD/STANDARD/BRIEF tier
  rendering as AI/ECONOMY/SOCIETY.
- News items (AI/ECONOMY/SOCIETY/MUSIC INDUSTRY) render at LEAD/STANDARD/
  BRIEF depth per report/web_data_v2.py's `tier` field (driven by the
  LLM's own selection order, source_count shown only as a secondary
  corroboration chip -- never tier-determining, per the locked V2.1
  direction). A distinct, non-redundant `snippet` becomes the LEAD's
  intro paragraph; a redundant one was already dropped upstream.
- PRODUCER INTELLIGENCE renders the already-synthesized, already-
  validated insights (action/why/evidence/confidence) exactly as given --
  evidence chips show the REAL catalog summary text, never a bare ref
  code, and NEVER a fabricated fallback recommendation when the day's
  evidence was too thin (honest empty state instead).
- SOURCES lists exactly the source keys the input data itself names, plus
  TikTok's honest not-yet-integrated status -- never a hardcoded or
  aspirational source list.
"""

import html
from datetime import datetime, timedelta, timezone

from music.early_signal import MIN_RANK_DELTA
from report.source_metadata import source_display_name

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

# A NEW entry debuting this high on a 10-slot chart is a structural fact
# (its rank position, already real) worth calling out in Viral · New --
# NOT an invented judgment about the song. Chosen because CHART_LIMIT is
# 10 (music/spotify_chart.py): top-3 is the top third of the chart.
VIRAL_NEW_NOTABLE_RANK = 3

_STATE_UNAVAILABLE = "UNAVAILABLE"
_QUIET_MESSAGE = "오늘 선별된 주요 이슈가 없습니다."
_DEGRADED_MESSAGE = "현재 데이터 수집 문제로 이 섹션의 브리핑이 제한됩니다."
_TIKTOK_UNAVAILABLE_MESSAGE = "TikTok 차트 데이터 소스가 아직 연동되지 않았습니다."
_SPOTIFY_UNAVAILABLE_MESSAGE = "Spotify 차트 데이터가 아직 수집되지 않았습니다."
_PRODUCER_EMPTY_MESSAGE = "오늘은 근거가 충분하지 않아 프로듀서 인사이트를 생성하지 않았습니다."
_MUSIC_TREND_UNAVAILABLE_MESSAGE = "오늘은 근거가 충분하지 않아 트렌드 레이더를 생성하지 않았습니다."
_MUSIC_TREND_EMPTY_MESSAGES = {
    "genre_signals": "오늘은 근거가 충분한 장르 시그널이 없습니다.",
    "production_notes": "오늘 실제 원문에 프로덕션 특성에 대한 구체적 언급이 없습니다.",
    "producer_references": "오늘 실제 원문에 명시된 프로듀서/협업자 크레딧이 없습니다.",
    "kpop_ar_notes": "오늘 근거 중 케이팝/A&R과 명확히 연관된 시그널이 없습니다.",
}
_UNINTERPRETED_NOTICE = "AI 해석 대기 — 실제 수집된 원문을 매체 수 기준으로 정렬해 표시합니다."

NEWS_LABELS = {"AI": "AI", "ECONOMY": "경제", "SOCIETY": "사회", "TIKTOK": "TikTok", "SPOTIFY": "Spotify"}

CONFIDENCE_LABELS = {"LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음"}

NAV_SECTIONS = (
    ("TODAY", "오늘의 브리핑", "brief"),
    ("MUSIC", "Music Industry", "section-INDUSTRY"),
    ("MUSIC", "TikTok", "section-TIKTOK"),
    ("MUSIC", "Spotify", "section-SPOTIFY"),
    ("MUSIC", "Viral & Trends", "section-VIRAL"),
    ("MUSIC", "Intelligence", "section-INTELLIGENCE"),
    ("MUSIC", "Trend Radar", "section-TRENDS"),
    ("MUSIC", "Producer Intelligence", "section-PRODUCER"),
    ("NEWS", "AI", "section-AI"),
    ("NEWS", "경제", "section-ECONOMY"),
    ("NEWS", "사회", "section-SOCIETY"),
    ("INFO", "Sources", "section-SOURCES"),
)
# MUSIC is SUPER NEWS's primary intelligence domain (FINAL PREMIUM UI
# phase) -- its own consolidated nav group, positioned right after
# TODAY and ahead of generic NEWS, replacing the previous split across
# a "TIKTOK"-keyed chart group, music-industry news filed under the
# generic "NEWS" group, and Producer Intelligence under its own
# separate "INSIGHT" group.
NAV_GROUP_ORDER = ("TODAY", "MUSIC", "NEWS", "INFO")
NAV_GROUP_LABELS = {"TODAY": "TODAY", "MUSIC": "MUSIC INTELLIGENCE", "NEWS": "NEWS", "INFO": "INFO"}


def _source_label(source_name):
    """display_name now lives in ONE place (sources.yaml / music.registry,
    merged by report.source_metadata) -- this module no longer keeps a
    second hardcoded copy. An unmapped source still falls back to its raw
    internal name (visible-but-ugly, never silently hidden)."""
    return source_display_name(source_name)


def _region_label(region):
    """region is read verbatim from music_observations.region (see
    report/web_data_v2.py's _enrich_chart_entry) -- never invented here.
    Only casing/display polish, never a different value than what was
    actually persisted."""
    if not region:
        return None
    return {"GLOBAL": "Global"}.get(region, region)


def _format_date_kst(iso_string):
    """None-safe: a missing published_at/observed_at is omitted by the
    caller, never rendered as a fabricated date."""
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string).astimezone(_KST).strftime("%Y.%m.%d")
    except ValueError:
        return None


_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f7f6f2;
  --surface: #ffffff;
  --ink: #17181c;
  --ink-soft: rgba(23,24,28,0.66);
  --ink-faint: rgba(23,24,28,0.44);
  --rule: rgba(23,24,28,0.12);
  --chip-bg: rgba(23,24,28,0.06);
  --masthead: #1f3a5f;
  --hue-tiktok: #16181c;
  --hue-spotify: #158a3f;
  --hue-industry: #b3275f;
  --hue-intel: #6b46a3;
  --hue-ai: #2f5aa8;
  --hue-economy: #3f7d5c;
  --hue-society: #a86a2a;
  --hue-producer: #a3651f;
  --hue-sources: #6b7280;
  --hue-music: #0f6e4f;
  --bad-down: #b91c1c;
  --good-up: #15803d;
  --new-badge: #1d4ed8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #121317;
    --surface: #191b20;
    --ink: #eef0f3;
    --ink-soft: rgba(238,240,243,0.72);
    --ink-faint: rgba(238,240,243,0.5);
    --rule: rgba(238,240,243,0.14);
    --chip-bg: rgba(238,240,243,0.08);
    --masthead: #7ea1d6;
    --hue-tiktok: #eef0f3;
    --hue-spotify: #3ddc74;
    --hue-industry: #ec6ba0;
    --hue-intel: #a98af0;
    --hue-ai: #7fa6e6;
    --hue-economy: #6fbf93;
    --hue-society: #e0a75e;
    --hue-producer: #e0a75e;
    --hue-sources: #9aa0aa;
    --hue-music: #3ddc9a;
    --bad-down: #f87171;
    --good-up: #4ade80;
    --new-badge: #93c5fd;
  }
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  background: var(--bg); color: var(--ink);
  margin: 0; padding: 0 0 64px; line-height: 1.7; font-size: 17px;
}
.num { font-family: Georgia, "Times New Roman", ui-serif, serif; font-variant-numeric: tabular-nums; }

.masthead { max-width: 1180px; margin: 0 auto; padding: 28px 20px 20px; }
.brand { font-family: Georgia, "Times New Roman", ui-serif, serif; font-size: 0.82rem; font-weight: 700;
  letter-spacing: 0.22em; color: var(--masthead); }
.tagline { font-size: 0.75rem; letter-spacing: 0.1em; opacity: 0.5; margin-top: 2px; text-transform: uppercase; }
.date { font-family: Georgia, "Times New Roman", ui-serif, serif; font-size: clamp(2.1rem, 4vw, 3.1rem);
  font-weight: 700; margin: 6px 0 18px; letter-spacing: -0.01em; }

ul.key-points { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0; border-top: 1px solid var(--rule); }
li.key-point { padding: 14px 18px 14px 0; border-bottom: 1px solid var(--rule); }
li.key-point-dominant { grid-column: 1 / -1; padding: 20px 0 22px; border-bottom: 1px solid var(--rule); }
li.key-point-dominant .key-label { color: var(--masthead); }
li.key-point-dominant .key-title { font-size: clamp(1.3rem, 2.6vw, 1.7rem); font-weight: 800; line-height: 1.3; text-wrap: balance; }
li.key-point-dominant .key-sub { font-size: 0.92rem; max-width: 68ch; }
/* MUSIC is SUPER NEWS's primary intelligence domain (FINAL PREMIUM UI
   phase) -- its TODAY entry gets real, distinct visual weight, not the
   plain-chip treatment every other secondary key-point gets. Never as
   large as the single true dominant headline (that stays freshness-
   driven, never overridden), but never mistaken for a minor category
   either. */
li.key-point-music { grid-column: span 2; border-left: 2px solid var(--hue-music); padding-left: 14px; }
li.key-point-music .key-label { color: var(--hue-music); }
li.key-point-music .key-title { font-size: 1.08rem; font-weight: 800; }
.key-label { display: block; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 4px; }
.key-title { font-weight: 700; font-size: 1rem; line-height: 1.4; }
.key-sub { display: block; font-size: 0.8rem; color: var(--ink-soft); margin-top: 3px; }

/* ---- MUSIC INTELLIGENCE domain umbrella (FINAL PREMIUM UI phase) ---- */
.music-domain-header { display: flex; align-items: baseline; gap: 12px; margin: 0 0 22px; padding-bottom: 10px; border-bottom: 2px solid var(--hue-music); }
.music-domain-header h2 { font-size: 1.1rem; font-weight: 800; margin: 0; letter-spacing: 0.02em; color: var(--hue-music); }
.music-domain-header .music-domain-sub { font-size: 0.82rem; color: var(--ink-faint); }
.music-domain { margin-bottom: 12px; }
.music-domain section.block { margin-bottom: 30px; padding-bottom: 22px; }

.shell { max-width: 1180px; margin: 0 auto; display: flex; gap: 44px; align-items: flex-start; padding: 0 20px; }
.railnav { position: sticky; top: 20px; width: 220px; flex: 0 0 220px; display: flex; flex-direction: column; gap: 18px; }
.nav-group-label { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.1em; color: var(--ink-faint); margin-bottom: 6px; text-transform: uppercase; }
.nav-links { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.nav-link { display: block; padding: 5px 0; font-size: 0.88rem; color: var(--ink-soft); text-decoration: none; border-left: 2px solid transparent; padding-left: 10px; }
.nav-link:hover { color: var(--ink); border-left-color: var(--rule); }

.main { flex: 1; min-width: 0; max-width: 860px; }
section.block { margin-bottom: 40px; padding-bottom: 28px; border-bottom: 1px solid var(--rule); }
section.block:last-of-type { border-bottom: none; }
.block-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 16px; }
.block-head h2 { font-size: 0.82rem; font-weight: 800; margin: 0; text-transform: uppercase; letter-spacing: 0.1em; }
.block-head .block-sub { font-size: 0.8rem; color: var(--ink-faint); }
.block-TIKTOK .block-head h2, .kicker-TIKTOK { color: var(--hue-tiktok); }
.block-SPOTIFY .block-head h2, .kicker-SPOTIFY { color: var(--hue-spotify); }
.block-VIRAL .block-head h2 { color: var(--hue-spotify); }
.block-INDUSTRY .block-head h2 { color: var(--hue-industry); }
.block-INTELLIGENCE .block-head h2 { color: var(--hue-intel); }
.block-AI .block-head h2, .kicker-AI { color: var(--hue-ai); }
.block-ECONOMY .block-head h2, .kicker-ECONOMY { color: var(--hue-economy); }
.block-SOCIETY .block-head h2, .kicker-SOCIETY { color: var(--hue-society); }
.block-PRODUCER .block-head h2 { color: var(--hue-producer); }
.block-TRENDS .block-head h2 { color: var(--hue-music); }
.block-SOURCES .block-head h2 { color: var(--hue-sources); }

/* ---- tiered news items (LEAD / STANDARD / BRIEF) ---- */
.tier-group { display: flex; flex-direction: column; gap: 30px; }
.item-kicker { display: flex; align-items: center; gap: 6px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 6px; }
.item-kicker .source-count-chip:not(:first-child)::before { content: "· "; }
.source-count-chip { font-weight: 600; color: var(--ink-faint); letter-spacing: 0; text-transform: none; }
.item-lead .item-title { font-size: clamp(1.4rem, 2.6vw, 1.9rem); font-weight: 800; line-height: 1.3; margin: 0 0 10px; text-wrap: balance; }
.item-lead .item-lede { font-size: 1.04rem; color: var(--ink-soft); margin: 0 0 14px; max-width: 68ch; }
.item-why { display: flex; gap: 12px; padding: 0 0 0 14px; border-left: 2px solid var(--rule); margin: 0 0 14px; max-width: 68ch; }
.item-why-label { flex: 0 0 auto; width: 6.5em; font-size: 0.68rem; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; color: var(--ink-faint); padding-top: 2px; }
.item-why p { margin: 0; font-size: 0.95rem; }
.item-standard .item-title { font-size: 1.15rem; font-weight: 700; margin: 0 0 6px; }
.item-standard .item-body { font-size: 0.96rem; color: var(--ink-soft); margin: 0 0 4px; max-width: 68ch; }
.item-standard .item-context { font-size: 0.86rem; color: var(--ink-faint); margin: 0 0 8px; max-width: 68ch; }
.item-byline { font-size: 0.8rem; color: var(--ink-faint); margin: 0 0 8px; }
.uninterpreted-notice { font-size: 0.78rem; color: var(--hue-society); margin: 0 0 14px; display: inline-block; }
.item-link { display: inline-block; font-size: 0.88rem; color: var(--masthead); text-decoration: none; min-height: 30px; line-height: 30px; }
.item-link:hover { text-decoration: underline; }

.brief-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.brief-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 10px 0; border-top: 1px solid var(--rule); font-size: 0.92rem; }
.brief-row:first-child { border-top: none; }
.brief-title { font-weight: 600; }
.brief-reason { color: var(--ink-faint); font-size: 0.85rem; }
.brief-meta { color: var(--ink-faint); font-size: 0.78rem; flex-basis: 100%; }
.brief-row a { color: inherit; text-decoration: none; }
.brief-row a:hover .brief-title { text-decoration: underline; }

/* ---- chart / music rows ---- */
.chart-list { list-style: none; margin: 0; padding: 0; }
.chart-row { display: grid; grid-template-columns: 2.2em 1fr auto; align-items: baseline; gap: 12px; padding: 10px 0; border-top: 1px solid var(--rule); }
.chart-row:first-child { border-top: none; }
.chart-rank { font-weight: 700; opacity: 0.55; font-size: 1.05rem; }
.chart-track { font-weight: 600; }
.chart-meta { display: block; font-size: 0.78rem; color: var(--ink-faint); margin-top: 2px; }
.badge { font-size: 0.74rem; font-weight: 700; padding: 2px 7px; border-radius: 999px; white-space: nowrap; }
.badge-new { background: rgba(29,78,216,0.12); color: var(--new-badge); }
.badge-first { background: var(--chip-bg); color: var(--ink-faint); }
.badge-up { background: rgba(21,128,61,0.12); color: var(--good-up); }
.badge-down { background: rgba(185,28,28,0.12); color: var(--bad-down); }

.block-sub-label { font-size: 0.78rem; font-weight: 700; color: var(--ink-faint); margin: 0 0 10px; text-transform: uppercase; letter-spacing: 0.06em; }
.sub-section { margin-bottom: 28px; }
.sub-section:last-child { margin-bottom: 0; }

.trend-narrative { font-size: 1rem; margin: 0 0 12px; max-width: 68ch; }
.trend-stats { display: flex; flex-wrap: wrap; gap: 8px; }
.trend-stat { padding: 6px 12px; border-radius: 999px; background: var(--chip-bg); font-size: 0.85rem; font-weight: 600; }
.volatility-badge { font-weight: 700; }
.volatility-HIGH { color: var(--bad-down); }
.volatility-MEDIUM { color: var(--hue-society); }
.volatility-LOW { color: var(--ink-faint); }

/* ---- intelligence groups ---- */
.signal-list { list-style: none; margin: 0; padding: 0; }
.signal-row { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; padding: 8px 0; border-top: 1px solid var(--rule); }
.signal-row:first-child { border-top: none; }
.signal-track { font-weight: 600; }
.signal-meta { font-size: 0.82rem; color: var(--ink-faint); }
.signal-empty { font-size: 0.9rem; color: var(--ink-faint); padding: 4px 0; }

.movement-group-label { font-size: 0.72rem; font-weight: 800; letter-spacing: 0.06em; color: var(--ink-faint); margin: 14px 0 4px; }
.movement-group-label:first-child { margin-top: 0; }
.movement-list { list-style: none; margin: 0 0 4px; padding: 0; }
.movement-row { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 12px; padding: 7px 0; border-top: 1px solid var(--rule); }
.movement-row:first-child { border-top: none; }
.movement-detail { flex-basis: 100%; font-size: 0.85rem; color: var(--ink-soft); }
.cross-platform-note { margin-top: 10px; opacity: 0.7; }
.cross-platform-sources { list-style: none; margin: 4px 0 0; padding: 0; flex-basis: 100%; display: flex; flex-wrap: wrap; gap: 6px; }
.cross-platform-source { font-size: 0.78rem; padding: 3px 9px; border-radius: 999px; background: var(--chip-bg); color: var(--ink-soft); }
.cp-verified { color: var(--good-up); font-weight: 600; }
.cp-metric { color: var(--ink-soft); }
.cp-region { color: var(--ink-faint); }

.intelligence-status-card { padding: 14px 16px; border-radius: 6px; background: var(--surface); border: 1px solid var(--rule); }
.status-lines { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
.status-line { display: flex; justify-content: space-between; gap: 10px; padding: 6px 0; border-top: 1px solid var(--rule); font-size: 0.88rem; }
.status-line:first-child { border-top: none; }
.status-line-name { font-weight: 600; }
.status-line-detail { color: var(--ink-faint); }
.intelligence-status-card .signal-empty { margin: 0; padding-top: 4px; }

.outlook-row { padding: 10px 0; border-top: 1px solid var(--rule); }
.outlook-row:first-child { border-top: none; }
.outlook-label { display: flex; justify-content: space-between; font-size: 0.9rem; margin-bottom: 6px; }
.outlook-label .outlook-name { font-weight: 600; }
.outlook-label .outlook-status { color: var(--ink-faint); font-size: 0.82rem; }
.progress-track { height: 5px; border-radius: 999px; background: var(--chip-bg); overflow: hidden; }
.progress-fill { height: 100%; background: var(--hue-intel); border-radius: 999px; }

/* ---- producer intelligence ---- */
.producer-list { display: flex; flex-direction: column; gap: 16px; }
.producer-card { padding: 18px 20px; border-radius: 6px; background: var(--surface); border: 1px solid var(--rule); border-left: 3px solid var(--hue-producer); }
.producer-observed-label { display: block; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: var(--hue-producer); margin-bottom: 4px; }
.producer-action { font-size: 1.05rem; font-weight: 700; margin: 0 0 12px; }
.producer-inference { padding: 0 0 0 12px; border-left: 2px solid var(--rule); margin: 0 0 12px; }
.producer-inference-label { display: block; font-size: 0.64rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 4px; }
.producer-why { font-size: 0.9rem; color: var(--ink-soft); margin: 0 0 6px; max-width: 68ch; }
.producer-why:last-child { margin-bottom: 0; }
.producer-why b { color: var(--ink); font-weight: 600; }
.producer-evidence { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.evidence-chip { display: block; font-size: 0.8rem; padding: 7px 10px; border-radius: 4px; background: var(--chip-bg); color: var(--ink-soft); line-height: 1.5; max-width: 68ch; }
.evidence-chip::before { content: "— "; opacity: 0.5; }
.confidence-badge { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; }

/* ---- Trend Radar: Genre / Production / Producer Reference / K-pop-A&R --- */
.trend-signal-list { display: flex; flex-direction: column; gap: 12px; }
.trend-signal-card { padding: 14px 16px; border-radius: 6px; background: var(--surface); border: 1px solid var(--rule); border-left: 3px solid var(--hue-music); }
.trend-signal-observed { font-size: 0.98rem; font-weight: 600; margin: 0 0 10px; max-width: 68ch; }
.confidence-HIGH { color: var(--good-up); }
.confidence-MEDIUM { color: var(--hue-society); }
.confidence-LOW { color: var(--ink-faint); }

/* ---- sources ---- */
.source-status-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.source-status-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 14px; border-radius: 4px; background: var(--surface); border: 1px solid var(--rule); font-size: 0.9rem; }
.status-dot { display: inline-block; width: 7px; height: 7px; border-radius: 999px; margin-right: 8px; }
.status-active .status-dot { background: var(--good-up); }
.status-unavailable .status-dot { background: var(--bad-down); }
.status-active-label { color: var(--good-up); font-weight: 700; font-size: 0.82rem; }
.status-unavailable-label { color: var(--bad-down); font-weight: 700; font-size: 0.82rem; }

.state-message { max-width: 68ch; padding: 14px 16px; border-radius: 4px; background: var(--chip-bg); font-size: 0.95rem; color: var(--ink-soft); }
.state-message.state-degraded, .state-message.state-unavailable { color: var(--bad-down); }

footer { max-width: 1180px; margin: 48px auto 0; padding: 0 20px; font-size: 0.75rem; color: var(--ink-faint); }

@media (max-width: 960px) {
  .shell { flex-direction: column; gap: 24px; }
  .railnav { position: static; width: 100%; flex: none; flex-direction: row; overflow-x: auto; gap: 0; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
  .nav-group-label { display: none; }
  .nav-links { flex-direction: row; gap: 4px; flex-wrap: nowrap; }
  .nav-link { white-space: nowrap; border-left: none; border-bottom: 2px solid transparent; padding: 6px 10px; }
  .nav-link:hover { border-left-color: transparent; border-bottom-color: var(--rule); }
  .main { max-width: 100%; }
  .chart-row { grid-template-columns: 1.8em 1fr auto; }
}

/* ---- mobile: same hierarchy as desktop (TOP INTELLIGENCE -> SIGNALS ->
   CATEGORY), just re-flowed -- never a shrunk desktop layout. ---- */
@media (max-width: 600px) {
  body { font-size: 16px; }
  .masthead { padding: 20px 16px 14px; }
  .date { font-size: clamp(1.6rem, 7vw, 2.1rem); margin: 4px 0 14px; }
  ul.key-points { grid-template-columns: 1fr; }
  li.key-point { padding: 12px 0; }
  li.key-point-dominant { padding: 16px 0 18px; }
  li.key-point-dominant .key-title { font-size: clamp(1.15rem, 5.5vw, 1.4rem); }
  /* Real mobile defect found in QA: .key-point-music's base `grid-column:
     span 2` has no 2nd explicit column to span on this 1fr mobile grid,
     so the browser auto-creates an implicit column -- corrupting every
     sibling key-point's computed width (including the dominant
     headline, which is not itself spanned) and causing severe
     character-by-character text wrapping. Force every key-point to the
     single real column explicitly at this width. */
  li.key-point-music { grid-column: 1 / -1; padding-left: 12px; }
  li.key-point-music .key-title { font-size: 1rem; }
  .shell { padding: 0 16px; gap: 20px; }
  section.block { margin-bottom: 30px; padding-bottom: 20px; }
  .item-lead .item-title { font-size: clamp(1.15rem, 5.5vw, 1.4rem); }
  .item-why { padding-left: 12px; }
  .item-why-label { width: 5.2em; font-size: 0.63rem; }
  footer { padding: 0 16px; }
}
"""


def _e(text):
    return html.escape(text) if text else ""


def _news_state_message(state):
    if state == "DEGRADED":
        return _DEGRADED_MESSAGE, "state-degraded"
    if state == "QUIET":
        return _QUIET_MESSAGE, "state-quiet"
    return None, None


def _link_html(source_url):
    if not source_url:
        return ""
    safe_url = html.escape(source_url, quote=True)
    return f'<a class="item-link" href="{safe_url}" rel="noopener noreferrer">원문 보기 →</a>'


_DISPLAYABLE_TRANSLATION_STATUSES = ("TRANSLATED", "NOT_REQUIRED")


def _display_title(item):
    """Korean-first UI contract (Phase 3C production-pilot policy): once a
    real translation succeeded, or the source was already sufficiently
    Korean, the reader-facing headline is the Korean text -- never the raw
    original in that case. item["title"] (the real original -- report.
    translation never overwrites it) is still the fallback for every other
    case: no translation attempted (item["translation_status"] is None,
    e.g. TIKTOK/SPOTIFY news, which stay real/untranslated by design), or
    a real UNAVAILABLE/FAILED outcome -- news is never hidden or replaced
    with a blank field just because translation didn't succeed."""
    if item.get("translation_status") in _DISPLAYABLE_TRANSLATION_STATUSES and item.get("ko_title"):
        return item["ko_title"]
    return item["title"]


def _display_snippet(item):
    """Same Korean-first contract as _display_title, for the snippet/lede/
    context field. Returns None (never a fabricated empty string) when
    there is no real snippet text at all -- same as item["snippet"]
    itself, which this falls back to."""
    if item.get("snippet_translation_status") in _DISPLAYABLE_TRANSLATION_STATUSES and item.get("ko_snippet"):
        return item["ko_snippet"]
    return item.get("snippet")


def _item_byline(item):
    """SOURCE outlet + PUBLISHED date -- both real, already-persisted
    fields (raw_items.source_name/published_at); either half is simply
    omitted when not available, never fabricated."""
    bits = []
    if item.get("source_name"):
        bits.append(_e(_source_label(item["source_name"])))
    published = _format_date_kst(item.get("published_at"))
    if published:
        bits.append(f'<span class="num">{published}</span>')
    if not bits:
        return ""
    return f'<p class="item-byline">{" · ".join(bits)}</p>'


def _render_item(item, kicker_class=None, platform_label=None):
    tier = item.get("tier", "BRIEF")
    kicker_bits = []
    if platform_label:
        kicker_bits.append(_e(platform_label))
    if item.get("source_count") and item["source_count"] > 1:
        kicker_bits.append(f'<span class="source-count-chip">{item["source_count"]}개 매체 보도</span>')
    # Cluster-based coverage (report.web_data_v2._cluster_suppression): a
    # DIFFERENT real signal from source_count above -- this item is the
    # representative of >=2 independently-reported near-duplicate articles
    # about the same real event (different event_keys, merged by report.
    # story_clustering's own high-precision agreement, not the same-
    # event_key same-day corroboration source_count already measures).
    # Only rendered when a real cluster actually exists.
    if item.get("related_article_count") and item["related_article_count"] > 1:
        related_source_suffix = f" · {item['related_source_count']}개 매체" if item.get("related_source_count") else ""
        kicker_bits.append(
            f'<span class="source-count-chip">관련 보도 {item["related_article_count"]}건{related_source_suffix}</span>'
        )
    kicker_html = (
        f'<div class="item-kicker {kicker_class or ""}">' + " ".join(kicker_bits) + "</div>"
        if kicker_bits else ""
    )

    if tier == "LEAD":
        byline_html = _item_byline(item)
        display_snippet = _display_snippet(item)
        lede_html = f'<p class="item-lede">{_e(display_snippet)}</p>' if display_snippet else ""
        if item.get("ai_intelligence_status") == "AVAILABLE":
            # Reuses the existing .item-why/.item-why-label classes three
            # times (no new CSS -- the responsive foundation stays frozen)
            # for the full WHAT HAPPENED / WHY IT MATTERS / WHAT TO WATCH
            # structure once a real synthesis run has produced it.
            why_html = (
                f'<div class="item-why"><span class="item-why-label">무슨 일이 있었나</span><p>{_e(item["what_happened"])}</p></div>'
                f'<div class="item-why"><span class="item-why-label">왜 중요한가</span><p>{_e(item["why_it_matters"])}</p></div>'
                f'<div class="item-why"><span class="item-why-label">앞으로 지켜볼 점</span><p>{_e(item["what_to_watch"])}</p></div>'
            )
        else:
            why_html = (
                f'<div class="item-why"><span class="item-why-label">왜 중요한가</span><p>{_e(item["reason"])}</p></div>'
                if item.get("reason") else ""
            )
        return (
            f'<article class="item-lead">{kicker_html}'
            f'<h3 class="item-title">{_e(_display_title(item))}</h3>'
            f'{byline_html}{lede_html}{why_html}{_link_html(item.get("source_url"))}</article>'
        )
    if tier == "STANDARD":
        byline_html = _item_byline(item)
        body_html = f'<p class="item-body">{_e(item["reason"])}</p>' if item.get("reason") else ""
        display_snippet = _display_snippet(item)
        context_html = f'<p class="item-context">{_e(display_snippet)}</p>' if display_snippet else ""
        return (
            f'<article class="item-standard">{kicker_html}'
            f'<h4 class="item-title">{_e(_display_title(item))}</h4>'
            f'{byline_html}{body_html}{context_html}{_link_html(item.get("source_url"))}</article>'
        )
    # BRIEF -- stays compact, but source/date are now real, visible facts
    # here too (NEWS QUALITY pass): a BRIEF item with no date at all
    # previously gave the reader no way to tell a real week-old story
    # apart from a same-day one -- trust/freshness information must stay
    # accessible even at this compact tier, just subtle (see
    # .brief-meta's own CSS -- small, muted, never competing with the
    # headline).
    reason_html = f'<span class="brief-reason">{_e(item["reason"])}</span>' if item.get("reason") else ""
    meta_bits = []
    if item.get("source_name"):
        meta_bits.append(_e(_source_label(item["source_name"])))
    published = _format_date_kst(item.get("published_at"))
    if published:
        meta_bits.append(f'<span class="num">{published}</span>')
    meta_html = f'<span class="brief-meta">{" · ".join(meta_bits)}</span>' if meta_bits else ""
    inner = f'<span class="brief-title">{_e(_display_title(item))}</span> {reason_html}{meta_html}'
    if item.get("source_url"):
        safe_url = html.escape(item["source_url"], quote=True)
        return f'<li class="brief-row"><a href="{safe_url}" rel="noopener noreferrer">{inner}</a></li>'
    return f'<li class="brief-row">{inner}</li>'


def _render_item_group(items, kicker_class=None, platform_label=None):
    """Renders LEAD/STANDARD items as separate <article> blocks (they need
    room) and groups consecutive BRIEF items into one compact <ul> -- a
    BRIEF-tier item never gets a full-width headline treatment, which is
    the whole point of the tier."""
    if not items:
        return ""
    blocks = []
    brief_buffer = []

    def flush_brief():
        if brief_buffer:
            blocks.append('<ul class="brief-list">' + "".join(brief_buffer) + "</ul>")
            brief_buffer.clear()

    for item in items:
        if item.get("tier") == "BRIEF":
            brief_buffer.append(_render_item(item, kicker_class, platform_label))
        else:
            flush_brief()
            blocks.append(_render_item(item, kicker_class, platform_label))
    flush_brief()
    return f'<div class="tier-group">{"".join(blocks)}</div>'


def _render_cluster_evidence(clusters):
    """Real, non-LLM cross-event_key near-duplicate-coverage evidence
    (report.story_clustering) -- rendered only when a real multi-source
    cluster actually exists; an empty/None `clusters` renders nothing at
    all (never a manufactured "no clusters found" line, matching the
    module's own "no forced cluster" contract)."""
    if not clusters:
        return ""
    rows = "".join(
        f'<li class="signal-row"><span class="signal-track">{_e(c["representative_headline"])}</span>'
        f'<span class="signal-meta">관련 기사 {c["related_article_count"]}건 · {c["distinct_source_count"]}개 매체</span></li>'
        for c in clusters
    )
    return (
        '<div class="sub-section"><p class="block-sub-label">관련 사건 클러스터 (동일 사건, 다른 표제)</p>'
        f'<ul class="signal-list">{rows}</ul></div>'
    )


def _render_news_section(block_class, section_id, label, data):
    state = data["state"]
    message, css_class = _news_state_message(state)
    if message:
        body = f'<p class="state-message {css_class}">{_e(message)}</p>'
    else:
        notice = f'<p class="uninterpreted-notice">{_e(_UNINTERPRETED_NOTICE)}</p>' if state == "UNINTERPRETED" else ""
        body = (
            notice
            + _render_item_group(data["items"], kicker_class=f"kicker-{block_class.split('-')[-1]}")
            + _render_cluster_evidence(data.get("clusters"))
        )
    return (
        f'<section class="block {block_class}" id="{section_id}">'
        f'<div class="block-head"><h2>{_e(label)}</h2></div>{body}</section>'
    )


def _movement_badge(entry):
    """status (report/web_data_v2._enrich_chart_entry's V2-only
    normalization) is checked FIRST and is the single source of truth for
    "first observation vs genuine NEW" -- entry["is_new"] is now True only
    for status == "NEW" at that same boundary, so checking is_new first
    would never be wrong either, but checking status directly here avoids
    any dependency on that normalization having already run correctly."""
    delta = entry.get("rank_delta")
    if entry.get("status") == "FIRST_OBSERVED":
        return '<span class="badge badge-first">첫 관측</span>'
    if entry["is_new"]:
        return '<span class="badge badge-new">NEW</span>'
    if delta and delta > 0:
        return f'<span class="badge badge-up">▲{delta}</span>'
    if delta and delta < 0:
        return f'<span class="badge badge-down">▼{-delta}</span>'
    return ""


def _chart_row(entry):
    """TOP10's own row: current rank + identity + movement marker ONLY --
    owns "who is #N right now," nothing more. Detailed previous-> current
    movement and chart-history context belong to Daily Music Trend / Viral
    Hot respectively (fact-ownership rule) -- this row never repeats them."""
    return (
        f'<li class="chart-row"><span class="chart-rank num">{entry["rank"]}</span>'
        f'<span class="chart-track">{_e(entry["canonical_artist"])} - {_e(entry["canonical_title"])}</span>'
        f"{_movement_badge(entry)}</li>"
    )


def _render_tiktok_section(tiktok_chart):
    body = f'<p class="state-message state-unavailable">{_e(_TIKTOK_UNAVAILABLE_MESSAGE)}</p>'
    return (
        '<section class="block block-TIKTOK" id="section-TIKTOK">'
        '<div class="block-head"><h2>TikTok</h2></div>' + body + "</section>"
    )


def _render_spotify_section(spotify_chart):
    if spotify_chart["state"] == _STATE_UNAVAILABLE:
        body = f'<p class="state-message state-unavailable">{_e(_SPOTIFY_UNAVAILABLE_MESSAGE)}</p>'
    else:
        top10 = spotify_chart["top10"]
        if spotify_chart.get("is_first_observation"):
            summary = f'<p class="block-sub-label">TOP {len(top10)} · 첫 관측 (기준선 생성)</p>'
        else:
            new_count = len(spotify_chart["new_entries"])
            summary = f'<p class="block-sub-label">TOP {len(top10)} · 신규 진입 {new_count}</p>'
        rows = "\n".join(_chart_row(e) for e in top10)
        body = summary + f'<ul class="chart-list">{rows}</ul>'
    return (
        '<section class="block block-SPOTIFY" id="section-SPOTIFY">'
        '<div class="block-head"><h2>Spotify</h2></div>' + body + "</section>"
    )


def _select_viral_hot(top10):
    """Qualification is the SAME real threshold music.early_signal already
    uses to define an acceleration signal (MIN_RANK_DELTA) -- not an
    ad-hoc renderer cutoff. A track that merely moved up 1 spot is a real
    fact already shown in Daily Music Trend's RISERS group; it does not
    also qualify as "Viral Hot" just because it's positive."""
    movers = [e for e in top10 if e.get("status") == "UP" and (e.get("rank_delta") or 0) >= MIN_RANK_DELTA]
    return sorted(movers, key=lambda e: -e["rank_delta"])


def _render_viral_hot(spotify_chart):
    if spotify_chart["state"] != "NORMAL":
        return '<div class="sub-section"><p class="block-sub-label">Viral Hot</p><p class="signal-empty">데이터 없음</p></div>'
    hot = _select_viral_hot(spotify_chart["top10"])
    if not hot:
        body = f'<p class="signal-empty">오늘은 상승폭 +{MIN_RANK_DELTA} 이상인 검증된 급상승 곡이 없습니다.</p>'
    else:
        rows = []
        for e in hot:
            rows.append(
                f'<li class="signal-row"><span class="signal-track">{_e(e["canonical_artist"])} - {_e(e["canonical_title"])}</span>'
                f'<span class="signal-meta">오늘 가장 큰 검증된 상승폭 <span class="badge badge-up">▲{e["rank_delta"]}</span>'
                f' · 최고 {e["peak_rank"]}위 · {e["days_on_chart"]}일째 차트인</span></li>'
            )
        body = f'<ul class="signal-list">{"".join(rows)}</ul>'
    return f'<div class="sub-section"><p class="block-sub-label">Viral Hot</p>{body}</div>'


def _select_viral_new(new_entries):
    """A debut alone is already fully owned by Daily Music Trend's NEW
    ENTRIES group -- Viral · New only surfaces a debut that adds a
    genuinely distinct fact: entering unusually high (top
    VIRAL_NEW_NOTABLE_RANK of a 10-slot chart), a real, already-known
    structural fact, not an invented interpretation."""
    return [e for e in new_entries if e["rank"] <= VIRAL_NEW_NOTABLE_RANK]


def _render_viral_new(spotify_chart):
    if spotify_chart["state"] != "NORMAL":
        return '<div class="sub-section"><p class="block-sub-label">Viral · New</p><p class="signal-empty">데이터 없음</p></div>'
    notable = _select_viral_new(spotify_chart["new_entries"])
    if not notable:
        body = '<p class="signal-empty">오늘 신규 진입 중 이례적으로 높은 순위로 데뷔한 곡이 없습니다.</p>'
    else:
        rows = "".join(
            f'<li class="signal-row"><span class="signal-track">{_e(e["canonical_artist"])} - {_e(e["canonical_title"])}</span>'
            f'<span class="signal-meta">TOP10 {e["rank"]}위 데뷔 · 이례적 상위권 진입</span></li>'
            for e in notable
        )
        body = f'<ul class="signal-list">{rows}</ul>'
    return f'<div class="sub-section"><p class="block-sub-label">Viral · New</p>{body}</div>'


def _movement_row(e, extra_class=""):
    """Daily Music Trend's own row -- the ONE place the full previous ->
    current rank + delta breakdown is spelled out per track. previous_rank
    is None only for a genuinely NEW entry (report/web_data_v2.py never
    fabricates one)."""
    identity = f'{_e(e["canonical_artist"])} - {_e(e["canonical_title"])}'
    region = _region_label(e.get("region"))
    source_line = _e(_source_label("spotify_chart")) + (f" · {_e(region)}" if region else "")
    if e.get("status") == "FIRST_OBSERVED":
        movement = f'첫 관측 → 현재 <span class="num">#{e["rank"]}</span>'
    elif e["is_new"]:
        movement = f'NEW → 현재 <span class="num">#{e["rank"]}</span>'
    else:
        movement = (
            f'전일 <span class="num">#{e["previous_rank"]}</span> → 현재 <span class="num">#{e["rank"]}</span> '
            + _movement_badge(e)
        )
    return (
        f'<li class="movement-row {extra_class}"><span class="signal-track">{identity}</span>'
        f'<span class="signal-meta">{source_line}</span>'
        f'<span class="movement-detail">{movement}</span></li>'
    )


def _render_daily_trend(spotify_chart):
    trend = spotify_chart.get("trend")
    if not trend:
        return '<div class="sub-section"><p class="block-sub-label">Daily Music Trend</p><p class="signal-empty">데이터 없음</p></div>'

    top10 = spotify_chart["top10"]
    # status (report/web_data_v2._enrich_chart_entry's V2 normalization) is
    # the single source of truth here -- UP/DOWN are real movers with a
    # real previous rank; NEW/FIRST_OBSERVED both have none, and are always
    # grouped together in one "debut" section (music.signal_engine's own
    # is_first_observation is diff-level: on any given day EITHER every
    # entry is FIRST_OBSERVED, or none are, so they never mix within one
    # top10 -- checking the first debut entry's status is sufficient to
    # pick the group label).
    risers = [e for e in top10 if e.get("status") == "UP"]
    fallers = [e for e in top10 if e.get("status") == "DOWN"]
    debut_entries = [e for e in top10 if e.get("status") in ("NEW", "FIRST_OBSERVED")]
    risers.sort(key=lambda e: -e["rank_delta"])
    fallers.sort(key=lambda e: e["rank_delta"])
    debut_entries.sort(key=lambda e: e["rank"])

    groups_html = ""
    if risers:
        groups_html += (
            '<p class="movement-group-label">▲ RISERS</p><ul class="movement-list">'
            + "".join(_movement_row(e) for e in risers) + "</ul>"
        )
    if debut_entries:
        new_label = "첫 관측" if debut_entries[0].get("status") == "FIRST_OBSERVED" else "NEW"
        groups_html += (
            f'<p class="movement-group-label">{new_label}</p><ul class="movement-list">'
            + "".join(_movement_row(e) for e in debut_entries) + "</ul>"
        )
    if fallers:
        groups_html += (
            '<p class="movement-group-label">▼ FALLERS</p><ul class="movement-list">'
            + "".join(_movement_row(e) for e in fallers) + "</ul>"
        )
    if not groups_html:
        groups_html = '<p class="signal-empty">오늘 TOP10 내 변동이 없습니다.</p>'

    if spotify_chart.get("is_first_observation"):
        aggregate = (
            f'<p class="trend-narrative">오늘 TOP {len(top10)} 첫 관측 — 비교할 이전 데이터가 없어 '
            '기준선을 생성했습니다. 변동 추이는 다음 관측부터 표시됩니다.</p>'
        )
    else:
        volatility_label = {"HIGH": "높음", "MEDIUM": "보통", "LOW": "낮음"}[trend["volatility"]]
        stable_count = len(top10) - trend["new_count"] - trend["up_count"] - trend["down_count"]
        aggregate = (
            f'<p class="trend-narrative">오늘 TOP10 변화: 신규 {trend["new_count"]} · 상승 {trend["up_count"]} · '
            f'하락 {trend["down_count"]} · 유지 {stable_count} — 변동성 '
            f'<span class="volatility-badge volatility-{trend["volatility"]}">{volatility_label}</span></p>'
        )
    return (
        '<div class="sub-section"><p class="block-sub-label">Daily Music Trend</p>'
        f'{groups_html}{aggregate}</div>'
    )


def _render_viral_section(spotify_chart):
    body = _render_viral_hot(spotify_chart) + _render_viral_new(spotify_chart) + _render_daily_trend(spotify_chart)
    return (
        '<section class="block block-VIRAL" id="section-VIRAL">'
        '<div class="block-head"><h2>Viral &amp; Trends</h2></div>' + body + "</section>"
    )


def _render_industry_section(news):
    groups = []
    for category in ("TIKTOK", "SPOTIFY"):
        data = news[category]
        state = data["state"]
        message, css_class = _news_state_message(state)
        if message:
            groups.append(
                f'<div class="sub-section"><p class="block-sub-label">{_e(NEWS_LABELS[category])}</p>'
                f'<p class="state-message {css_class}">{_e(message)}</p></div>'
            )
            continue
        notice = f'<p class="uninterpreted-notice">{_e(_UNINTERPRETED_NOTICE)}</p>' if state == "UNINTERPRETED" else ""
        groups.append(
            f'<div class="sub-section"><p class="block-sub-label">{_e(NEWS_LABELS[category])}</p>'
            + notice
            + _render_item_group(data["items"], kicker_class="kicker-INDUSTRY")
            + "</div>"
        )
    body = "".join(groups)
    return (
        '<section class="block block-INDUSTRY" id="section-INDUSTRY">'
        '<div class="block-head"><h2>Industry News</h2></div>' + body + "</section>"
    )


def _render_early_signal_group(source_name, candidates):
    if not candidates:
        return (
            f'<div class="sub-section"><p class="block-sub-label">{_e(_source_label(source_name))} Early Signal</p>'
            f'<p class="signal-empty">신호 없음</p></div>'
        )
    rows = []
    for c in candidates:
        delta = int(c["rank_delta"])
        rows.append(
            f'<li class="signal-row"><span class="signal-track">{_e(c["canonical_artist"])} - {_e(c["canonical_title"])}</span>'
            f'<span class="signal-meta">▲{delta}</span></li>'
        )
    return (
        f'<div class="sub-section"><p class="block-sub-label">{_e(_source_label(source_name))} Early Signal</p>'
        f'<ul class="signal-list">' + "".join(rows) + "</ul></div>"
    )


def _render_catalog_revival_group(source_name, candidates):
    if not candidates:
        return (
            f'<div class="sub-section"><p class="block-sub-label">{_e(_source_label(source_name))} Catalog Revival</p>'
            f'<p class="signal-empty">해당 없음</p></div>'
        )
    rows = []
    for c in candidates:
        rows.append(
            f'<li class="signal-row"><span class="signal-track">{_e(c["canonical_artist"])} - {_e(c["canonical_title"])}</span>'
            f'<span class="signal-meta">{c["age_days"]}일 전 최초 관측 · {c["gap_days"]}일 공백 (approximate)</span></li>'
        )
    return (
        f'<div class="sub-section"><p class="block-sub-label">{_e(_source_label(source_name))} Catalog Revival</p>'
        f'<ul class="signal-list">' + "".join(rows) + "</ul></div>"
    )


_TIKTOK_NOT_AUTO_DETECTED_NOTE = "TikTok — 미연동 (교차 플랫폼 자동 감지 대상 아님)"

# Distinct wording per music.cross_platform.classify_cross_platform_state --
# an empty cross_platform list is never rendered with one flat "신호 없음"
# message regardless of WHY it's empty (see that function's own docstring
# on the real distinction between "not enough sources reporting yet",
# "an active source has no real history to compute velocity from yet", and
# "genuinely checked, found nothing"). Falls back to the NO_SIGNAL wording
# for an unrecognized/missing state -- never crashes on it.
_CROSS_PLATFORM_STATE_MESSAGES = {
    "NO_SIGNAL": "2개 이상 플랫폼에서 동시 확인된 시그널이 없습니다.",
    "INSUFFICIENT_SOURCES": "오늘 2개 이상의 플랫폼에서 실제 차트 데이터가 확인되지 않아 교차 플랫폼 비교를 수행할 수 없습니다.",
    "INSUFFICIENT_HISTORY": "일부 플랫폼의 관측 이력이 아직 짧아(첫 관측 포함) 교차 플랫폼 신호를 판단하기에 충분하지 않습니다.",
}


def _render_cross_platform_group(cross_platform, state=None):
    tiktok_note = f'<p class="signal-meta cross-platform-note">{_e(_TIKTOK_NOT_AUTO_DETECTED_NOTE)}</p>'
    if not cross_platform:
        message = _CROSS_PLATFORM_STATE_MESSAGES.get(state, _CROSS_PLATFORM_STATE_MESSAGES["NO_SIGNAL"])
        return (
            '<div class="sub-section"><p class="block-sub-label">Cross-Platform Movement</p>'
            f'<p class="signal-empty">{_e(message)}</p>'
            f'{tiktok_note}</div>'
        )
    rows = []
    for entry in cross_platform:
        # Every verified supporting source listed individually with its
        # OWN real metric -- never collapsed into "여러 플랫폼," never
        # forced into a shared rank semantic a source doesn't have. An
        # unavailable source (TikTok) is never counted among them (it
        # can't be: detect_cross_platform_signals only iterates
        # ACTIVE_MUSIC_SOURCES, which excludes it).
        detail_by_source = {d["source_name"]: d for d in entry.get("source_details", [])}
        source_rows = []
        for s in entry["sources"]:
            detail = detail_by_source.get(s)
            if detail is None:
                # Real fallback, never a fabricated number: the entity
                # qualified via derived_signals but today's chart-diff
                # lookup didn't resolve a row for it.
                metric_html = '<span class="cp-verified">검증된 신호</span>'
            elif detail.get("status") == "FIRST_OBSERVED":
                metric_html = f'<span class="cp-metric">첫 관측 → 현재 <span class="num">#{detail["rank"]}</span></span>'
            elif detail["is_new"]:
                metric_html = f'<span class="cp-metric">NEW → 현재 <span class="num">#{detail["rank"]}</span></span>'
            else:
                metric_html = (
                    f'<span class="cp-metric">전일 <span class="num">#{detail["previous_rank"]}</span> → '
                    f'현재 <span class="num">#{detail["rank"]}</span></span>'
                )
            region = _region_label(detail["region"]) if detail else None
            region_html = f' <span class="cp-region">{_e(region)}</span>' if region else ""
            source_rows.append(
                f'<li class="cross-platform-source">{_e(_source_label(s))}{region_html} {metric_html}</li>'
            )
        rows.append(
            f'<li class="signal-row"><span class="signal-track">{_e(entry["canonical_artist"])} - {_e(entry["canonical_title"])}</span>'
            f'<span class="signal-meta">{len(entry["sources"])}개 소스에서 동시 확인</span>'
            f'<ul class="cross-platform-sources">{"".join(source_rows)}</ul></li>'
        )
    return (
        '<div class="sub-section"><p class="block-sub-label">Cross-Platform Movement</p>'
        '<ul class="signal-list">' + "".join(rows) + f"</ul>{tiktok_note}</div>"
    )


def _render_outlook_group(outlook):
    rows = []
    for source_name in sorted(outlook.keys()):
        info = outlook[source_name]
        status_text = "예측 가능" if info["status"] == "READY" else "데이터 부족"
        detail = (
            f'{info["days_of_history"]}일 관측' if info["status"] == "READY"
            else f'관측 {info["days_of_history"]}일 / 최소 {info["min_required_days"]}일 필요'
        )
        pct = round(info.get("progress_ratio", 0) * 100)
        rows.append(
            f'<div class="outlook-row"><div class="outlook-label">'
            f'<span class="outlook-name">{_e(_source_label(source_name))}</span>'
            f'<span class="outlook-status">{status_text} · {_e(detail)}</span></div>'
            f'<div class="progress-track"><div class="progress-fill" style="width:{pct}%"></div></div></div>'
        )
    if not rows:
        return '<div class="sub-section"><p class="block-sub-label">Future Radar</p><p class="signal-empty">데이터 없음</p></div>'
    return '<div class="sub-section"><p class="block-sub-label">Future Radar</p>' + "".join(rows) + "</div>"


_CROSS_PLATFORM_STATUS_LINE_DETAIL = {
    "INSUFFICIENT_SOURCES": "2개 이상 플랫폼 데이터 부족",
    "INSUFFICIENT_HISTORY": "일부 플랫폼 관측 이력 부족",
    "NO_SIGNAL": "동시 신호 없음",
}


def _render_intelligence_empty_status_card(outlook, cross_platform_state=None):
    """ONE compact status card replacing the old layout of Early Signal /
    Catalog Revival / Cross-Platform / Future Radar each independently
    rendering their own "no signal" block -- for the common case (short
    observation history) that produced 4+ separate empty blocks and a lot
    of dead whitespace. Real counts only (outlook's days_of_history/
    min_required_days, already computed by music.forecast_gate) -- never
    fabricated. Only used when EVERY intelligence sub-signal is empty; any
    real signal still gets its own full section (see _render_intelligence_
    section)."""
    rows = []
    for source_name in sorted(outlook.keys()):
        info = outlook[source_name]
        rows.append(
            f'<div class="status-line"><span class="status-line-name">{_e(_source_label(source_name))}</span>'
            f'<span class="status-line-detail num">관측 {info["days_of_history"]}/{info["min_required_days"]}일</span></div>'
        )
    rows.append(
        '<div class="status-line"><span class="status-line-name">TikTok</span>'
        '<span class="status-line-detail">차트 데이터 미연동</span></div>'
    )
    cross_platform_detail = _CROSS_PLATFORM_STATUS_LINE_DETAIL.get(
        cross_platform_state, _CROSS_PLATFORM_STATUS_LINE_DETAIL["NO_SIGNAL"]
    )
    rows.append(
        '<div class="status-line"><span class="status-line-name">Cross-Platform</span>'
        f'<span class="status-line-detail">{_e(cross_platform_detail)}</span></div>'
    )
    return (
        '<div class="sub-section intelligence-status-card">'
        '<p class="block-sub-label">데이터 축적 현황</p>'
        f'<div class="status-lines">{"".join(rows)}</div>'
        '<p class="signal-empty">관측 기간이 쌓이면 Early Signal · Catalog Revival · Cross-Platform 신호가 '
        '이 자리에 표시됩니다.</p></div>'
    )


def _render_intelligence_section(intelligence):
    early = intelligence["early_signal"]
    revival = intelligence["catalog_revival"]
    cross_platform = intelligence["cross_platform"]
    cross_platform_state = intelligence.get("cross_platform_state")
    all_empty = (
        all(not candidates for candidates in early.values())
        and all(not candidates for candidates in revival.values())
        and not cross_platform
    )
    if all_empty:
        body = _render_intelligence_empty_status_card(intelligence["outlook"], cross_platform_state)
    else:
        parts = (
            [_render_early_signal_group(name, early[name]) for name in sorted(early.keys())]
            + [_render_catalog_revival_group(name, revival[name]) for name in sorted(revival.keys())]
            + [_render_cross_platform_group(cross_platform, cross_platform_state), _render_outlook_group(intelligence["outlook"])]
        )
        body = "".join(parts)
    return (
        '<section class="block block-INTELLIGENCE" id="section-INTELLIGENCE">'
        '<div class="block-head"><h2>Intelligence</h2></div>' + body + "</section>"
    )


def _render_trend_signal_items(items, empty_message):
    """Shared renderer for one Genre Radar / Production Radar / Producer
    Reference Radar / K-pop-A&R list -- each item shows the real OBSERVED
    evidence and the model's own INTERPRETATION as visually distinct
    lines (never merged into one undifferentiated sentence), plus
    resolved evidence chips and a confidence badge. An empty list is
    rendered as an explicit, honest message -- never silently omitted
    (a reader must be able to tell "no real signal today" apart from
    "this section doesn't exist")."""
    if not items:
        return f'<p class="signal-empty">{_e(empty_message)}</p>'
    cards = []
    for item in items:
        evidence_html = "".join(
            f'<span class="evidence-chip">{_e(ev["summary"])}</span>' for ev in item.get("evidence", [])
        )
        confidence = item.get("confidence", "LOW")
        cards.append(
            f'<div class="trend-signal-card">'
            f'<span class="producer-observed-label">관찰된 사실</span>'
            f'<p class="trend-signal-observed">{_e(item["observed"])}</p>'
            f'<div class="producer-inference">'
            f'<span class="producer-inference-label">AI 추론</span>'
            f'<p class="producer-why">{_e(item["interpretation"])}</p>'
            f'</div>'
            f'<div class="producer-evidence">{evidence_html}</div>'
            f'<span class="confidence-badge confidence-{confidence}">신뢰도 {CONFIDENCE_LABELS.get(confidence, confidence)}</span>'
            f'</div>'
        )
    return f'<div class="trend-signal-list">{"".join(cards)}</div>'


def _render_music_trend_section(music_trend_intelligence):
    """Genre Radar / Production Radar / Producer Reference Radar / K-pop-
    A&R relevance -- MUSIC INTELLIGENCE COMPLETION phase's new real
    capability, integrated as sub-sections of ONE block (matching the
    existing Intelligence section's own Early Signal/Catalog Revival/
    Cross-Platform/Future Radar sub-section pattern), never four
    separate top-level sections. UNAVAILABLE (no synthesis has run yet
    for today) renders one honest state message for the whole block;
    once a real run exists, each of the 4 categories is shown
    independently -- a category with no real evidence that day is its
    own honest empty message, never hidden and never padded."""
    if music_trend_intelligence["state"] != "NORMAL":
        body = f'<p class="state-message">{_e(_MUSIC_TREND_UNAVAILABLE_MESSAGE)}</p>'
    else:
        groups = [
            ("Genre Radar", "genre_signals"),
            ("Production Radar", "production_notes"),
            ("Producer Reference Radar", "producer_references"),
            ("K-pop / A&R Relevance", "kpop_ar_notes"),
        ]
        parts = []
        for label, field in groups:
            items = music_trend_intelligence.get(field, [])
            parts.append(
                f'<div class="sub-section"><p class="block-sub-label">{_e(label)}</p>'
                + _render_trend_signal_items(items, _MUSIC_TREND_EMPTY_MESSAGES[field])
                + "</div>"
            )
        body = "".join(parts)
    return (
        '<section class="block block-TRENDS" id="section-TRENDS">'
        '<div class="block-head"><h2>Trend Radar</h2></div>' + body + "</section>"
    )


def _render_producer_section(producer_intelligence):
    if producer_intelligence["state"] != "NORMAL" or not producer_intelligence["insights"]:
        body = f'<p class="state-message">{_e(_PRODUCER_EMPTY_MESSAGE)}</p>'
    else:
        cards = []
        for insight in producer_intelligence["insights"]:
            evidence_html = "".join(
                f'<span class="evidence-chip">{_e(ev["summary"])}</span>' for ev in insight.get("evidence", [])
            )
            confidence = insight.get("confidence", "LOW")
            # MUSIC INTELLIGENCE COMPLETION phase's 6-question contract:
            # what_is_moving is the OBSERVED FACT (grounded in
            # evidence_refs below it); the other three are explicitly
            # labeled AI 추론 (AI inference) -- never presented with the
            # same visual weight as the observed fact, so a reader can
            # never mistake the model's own interpretation for something
            # the evidence itself states.
            cards.append(
                f'<div class="producer-card">'
                f'<span class="producer-observed-label">관찰된 사실</span>'
                f'<p class="producer-action">{_e(insight["what_is_moving"])}</p>'
                f'<div class="producer-inference">'
                f'<span class="producer-inference-label">AI 추론</span>'
                f'<p class="producer-why"><b>왜 중요한가</b> {_e(insight["why_it_matters"])}</p>'
                f'<p class="producer-why"><b>지켜볼 점</b> {_e(insight["what_to_watch"])}</p>'
                f'<p class="producer-why"><b>지금 시도해볼 만한 것</b> {_e(insight["what_could_i_make_now"])}</p>'
                f'</div>'
                f'<div class="producer-evidence">{evidence_html}</div>'
                f'<span class="confidence-badge confidence-{confidence}">신뢰도 {CONFIDENCE_LABELS.get(confidence, confidence)}</span>'
                f"</div>"
            )
        body = f'<div class="producer-list">{"".join(cards)}</div>'
    return (
        '<section class="block block-PRODUCER" id="section-PRODUCER">'
        '<div class="block-head"><h2>Producer Intelligence</h2></div>' + body + "</section>"
    )


def _render_sources_section(intelligence):
    rows = []
    for source_name in sorted(intelligence["early_signal"].keys()):
        rows.append(
            f'<li class="source-status-row status-active"><span><span class="status-dot"></span>{_e(_source_label(source_name))}</span>'
            f'<span class="status-active-label">연동됨</span></li>'
        )
    rows.append(
        '<li class="source-status-row status-unavailable"><span><span class="status-dot"></span>TikTok</span>'
        '<span class="status-unavailable-label">미연동</span></li>'
    )
    body = '<ul class="source-status-list">' + "".join(rows) + "</ul>"
    return (
        '<section class="block block-SOURCES" id="section-SOURCES">'
        '<div class="block-head"><h2>Sources</h2></div>' + body + "</section>"
    )


def _key_point_html(label, title, sub=None, dominant=False, music=False):
    sub_html = f'<span class="key-sub">{_e(sub)}</span>' if sub else ""
    variant_class = " key-point-dominant" if dominant else (" key-point-music" if music else "")
    return (
        f'<li class="key-point{variant_class}"><span class="key-label">{_e(label)}</span>'
        f'<span class="key-title">{_e(title)}</span>{sub_html}</li>'
    )


def _render_today_in_30_seconds(dashboard_data):
    """TOP INTELLIGENCE / TODAY. Order (FINAL PREMIUM UI phase -- MUSIC is
    SUPER NEWS's primary intelligence domain): (1) the single freshest
    real AI/ECONOMY/SOCIETY LEAD -- by real published_at, never an
    invented cross-category importance score -- as the dominant headline
    (largest type, its own real WHY excerpt); (2) a real MUSIC entry
    right after it, with its own distinct elevated weight -- never the
    same plain-chip treatment a generic signal fact gets, so music never
    reads as just one more minor category; (3) the other real AI/ECONOMY/
    SOCIETY LEADs; (4) real chart/signal facts (TikTok/Spotify/Early
    Signal) last, as compact chips. The dominant slot itself is still
    decided purely by freshness -- never overridden to force MUSIC into
    it artificially."""
    news = dashboard_data["news"]
    spotify_chart = dashboard_data["spotify_chart"]
    tiktok_chart = dashboard_data["tiktok_chart"]
    intelligence = dashboard_data["intelligence"]

    # Real items exist in both NORMAL (LLM-selected) and UNINTERPRETED
    # (real-data fallback, see report.web_data_v2's own fallback
    # docstring) states -- gating this on NORMAL alone would silently drop
    # AI/ECONOMY/SOCIETY from the first-screen summary specifically
    # whenever the LLM is unavailable, even though each vertical's own
    # full section below still shows real news. The first screen must not
    # be the one place an LLM outage hides real, already-collected news.
    lead_candidates = [
        (category, news[category]["items"][0])
        for category in ("AI", "ECONOMY", "SOCIETY")
        if news[category]["items"]
    ]
    # Freshest real LEAD (by real published_at) becomes the dominant
    # headline -- a real, deterministic signal already computed upstream,
    # never a new judgment made here. A missing published_at sorts oldest
    # (empty string), never crashes, never wins the dominant slot over a
    # dated one.
    lead_candidates.sort(key=lambda pair: pair[1].get("published_at") or "", reverse=True)

    dominant_point = ""
    remaining_news_points = []
    for position, (category, item) in enumerate(lead_candidates):
        html = _key_point_html(
            NEWS_LABELS[category], _display_title(item), item.get("reason"), dominant=(position == 0),
        )
        if position == 0:
            dominant_point = html
        else:
            remaining_news_points.append(html)

    # Real MUSIC entry, elevated -- prefer a real Music Industry/Spotify
    # NEWS headline (already real editorial content: an actual story,
    # with its own real WHY) over a bare chart number; the real Spotify
    # chart leader is only the fallback when no music news item exists
    # that day. Either way, always a real fact, never fabricated -- an
    # unavailable state is simply omitted here, never given a slot.
    music_point = ""
    spotify_news_items = news["SPOTIFY"]["items"]
    used_spotify_news_headline = False
    if spotify_news_items:
        top_music_item = spotify_news_items[0]
        music_point = _key_point_html(
            "MUSIC", _display_title(top_music_item), top_music_item.get("reason"), music=True,
        )
        used_spotify_news_headline = True
    elif spotify_chart["top10"]:
        top = spotify_chart["top10"][0]
        rank_sub = "오늘의 Spotify 1위" if spotify_chart.get("is_first_observation") else "Spotify 1위"
        music_point = _key_point_html(
            "MUSIC", f"{top['canonical_artist']} - {top['canonical_title']}", rank_sub, music=True,
        )

    # A "not connected"/"no data" operational message is never given a
    # headline-card slot here -- it carries the same visual weight as a
    # real finding otherwise (see SUPER_NEWS_HANDOFF.md next-phase punch
    # list #9). Only real chart-leader facts become a signal chip; system
    # status belongs in the Sources section instead.
    signal_points = []

    if tiktok_chart["top10"]:
        top = tiktok_chart["top10"][0]
        signal_points.append(_key_point_html("TikTok", f"{top['canonical_artist']} - {top['canonical_title']}", "1위"))

    # Only add the Spotify chart chip when the MUSIC slot above used a
    # real news headline instead -- otherwise this would show the exact
    # same real chart-leader fact twice on the same first screen.
    if used_spotify_news_headline and spotify_chart["top10"]:
        top = spotify_chart["top10"][0]
        rank_sub = "첫 관측 1위" if spotify_chart.get("is_first_observation") else "1위"
        signal_points.append(_key_point_html("Spotify", f"{top['canonical_artist']} - {top['canonical_title']}", rank_sub))

    for source_name in sorted(intelligence["early_signal"].keys()):
        candidates = intelligence["early_signal"][source_name]
        if candidates:
            top = candidates[0]
            signal_points.append(
                _key_point_html(
                    f"{_source_label(source_name)} Signal",
                    f"{top['canonical_artist']} - {top['canonical_title']}",
                    f"+{int(top['rank_delta'])}",
                )
            )
            break

    points = [dominant_point, music_point] + remaining_news_points + signal_points
    points = [p for p in points if p]
    if not points:
        return ""
    return '<ul class="key-points">' + "".join(points) + "</ul>"


def _render_music_domain_header():
    """A single umbrella header preceding every music-related section
    (industry news, chart data, signals, intelligence, producer
    intelligence) -- these were previously scattered across disconnected
    nav groups (a "MUSIC" chart group, music-industry news filed under
    the generic "NEWS" group, Producer Intelligence under its own
    "INSIGHT" group); this header, plus the section reordering in
    render_dashboard_html_v2, presents them as ONE cohesive, visually
    dominant intelligence domain -- FINAL PREMIUM UI phase's explicit
    "MUSIC is SUPER NEWS's primary intelligence domain" requirement.
    Static text only, no data -- chart data and industry news remain
    distinct sub-sections underneath it, never merged into one."""
    return (
        '<div class="music-domain-header"><h2>MUSIC INTELLIGENCE</h2>'
        '<span class="music-domain-sub">뉴스 · 차트 · 시그널 · 프로듀서 인사이트</span></div>'
    )


def _render_nav():
    groups = {}
    for group_key, label, anchor in NAV_SECTIONS:
        groups.setdefault(group_key, []).append((label, anchor))
    blocks = []
    for group_key in NAV_GROUP_ORDER:
        links = "".join(f'<a class="nav-link" href="#{anchor}">{_e(label)}</a>' for label, anchor in groups[group_key])
        blocks.append(
            f'<div><div class="nav-group-label">{_e(NAV_GROUP_LABELS[group_key])}</div>'
            f'<nav class="nav-links">{links}</nav></div>'
        )
    return f'<nav class="railnav">{"".join(blocks)}</nav>'


def render_dashboard_html_v2(dashboard_data):
    """dashboard_data: the exact shape report.web_data_v2.
    build_dashboard_data_v2() returns. Returns a complete, self-contained
    HTML document string."""
    report_date_kst = dashboard_data["report_date_kst"]
    news = dashboard_data["news"]
    y, m, d = report_date_kst.split("-")

    key_points_html = _render_today_in_30_seconds(dashboard_data)

    # MUSIC INTELLIGENCE domain (FINAL PREMIUM UI phase): industry news
    # first (real editorial content), then chart data, then signals/
    # movement, then producer intelligence -- all under one umbrella
    # header, ahead of AI/ECONOMY/SOCIETY news. See section 5's required
    # flow: TOP INTELLIGENCE -> MUSIC INTELLIGENCE -> SIGNALS/WHAT'S
    # MOVING -> PRODUCER INTELLIGENCE -> AI -> ECONOMY -> SOCIETY ->
    # WATCH NEXT/SOURCES.
    music_domain_html = (
        _render_music_domain_header()
        + f'<div class="music-domain">'
        + _render_industry_section(news)
        + _render_tiktok_section(dashboard_data["tiktok_chart"])
        + _render_spotify_section(dashboard_data["spotify_chart"])
        + _render_viral_section(dashboard_data["spotify_chart"])
        + _render_intelligence_section(dashboard_data["intelligence"])
        + _render_music_trend_section(dashboard_data["music_trend_intelligence"])
        + _render_producer_section(dashboard_data["producer_intelligence"])
        + "</div>"
    )

    sections_html = "".join([
        music_domain_html,
        _render_news_section("block-AI", "section-AI", NEWS_LABELS["AI"], news["AI"]),
        _render_news_section("block-ECONOMY", "section-ECONOMY", NEWS_LABELS["ECONOMY"], news["ECONOMY"]),
        _render_news_section("block-SOCIETY", "section-SOCIETY", NEWS_LABELS["SOCIETY"], news["SOCIETY"]),
        _render_sources_section(dashboard_data["intelligence"]),
    ])

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
<div class="brand">SUPER NEWS</div>
<div class="tagline">Intelligence Dashboard</div>
<div class="date num">{y}.{m}.{d}</div>
<div id="brief">{key_points_html}</div>
</div>
<div class="shell">
{_render_nav()}
<main class="main">
{sections_html}
</main>
</div>
<footer>이 페이지는 매일 자동으로 갱신됩니다.</footer>
</body>
</html>
"""

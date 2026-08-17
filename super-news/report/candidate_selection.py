"""News candidate selection for Report V1: NORMALIZED FACT -> a deterministic,
bounded candidate list per news category, ready to hand to the LLM.

Deterministic by construction: candidates are grouped by event_key (multiple
sources covering the same story collapse into one candidate), ordered by
(-source_count, event_key) -- ties broken lexicographically, never by
insertion/dict order -- so calling this twice against the same DB state
always returns the identical list in the identical order.

Stale-exclusion window is intentionally narrow: only event_keys the LLM
already SELECTED in the immediately-previous day's report for that category
are excluded (not all history). This stops the same story from being
re-surfaced the very next day without needing a growing "seen forever" set.
"""

import math
import re
from datetime import datetime, timedelta, timezone

from report.source_metadata import source_quality_score as _source_quality_score

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST) -- avoids the zoneinfo/tzdata
# dependency this project doesn't otherwise need.
_KST = timezone(timedelta(hours=9))

# Report output categories (reports.category / run_category_status.category)
# are NOT the same strings as normalized_items.category. The collection
# layer's authoritative taxonomy is ingestion/registry.py's KNOWN_CATEGORIES,
# enforced at registry-load time and declared in sources.yaml -- normalize.py
# copies raw_items.category verbatim, so normalized_items.category is always
# one of those strings. This map is the single place that bridges the two
# vocabularies; report output category values are never changed to match.
#
# Each report category maps to a LIST of source categories (not a single
# value) -- SPOTIFY intentionally pools its own official news
# (SPOTIFY_NEWS) together with general trade-press coverage
# (MUSIC_INDUSTRY_NEWS: Billboard/MBW/Variety/Rolling Stone), since
# run_category_status.category's frozen CHECK constraint has no dedicated
# slot for a standalone "music industry" report category (only 'TIKTOK',
# 'SPOTIFY', 'AI', 'ECONOMY', 'SOCIETY', 'MUSIC', 'MONTHLY_FORECAST' are
# valid, and adding a new one requires a schema migration, not decided
# here). This is a deliberate, documented content-routing choice, not an
# accident -- see the V2 design research.
NEWS_CATEGORY_SOURCE_MAP = {
    "AI": ["AI_NEWS"],
    "ECONOMY": ["ECONOMY_NEWS"],
    "SOCIETY": ["SOCIETY_NEWS"],
    "TIKTOK": ["TIKTOK_NEWS"],
    "SPOTIFY": ["SPOTIFY_NEWS", "MUSIC_INDUSTRY_NEWS"],
}


def _source_categories(report_category):
    """Maps a report-output category to its list of normalized_items.category
    source values. Raises on an unrecognized report category rather than
    silently returning no rows -- a typo/new category here must fail
    loudly, not masquerade as an ordinary zero-candidate day."""
    try:
        return NEWS_CATEGORY_SOURCE_MAP[report_category]
    except KeyError:
        raise ValueError(
            f"Unknown report category {report_category!r}; expected one of {sorted(NEWS_CATEGORY_SOURCE_MAP)}"
        ) from None


def _kst_day_bounds_utc(report_date_kst):
    """Returns (start_utc_iso, end_utc_iso) -- the half-open UTC instant
    range covering one KST calendar day, in the same isoformat() shape
    raw_items.collected_at is stored in (so plain string comparison is
    valid)."""
    y, m, d = (int(part) for part in report_date_kst.split("-"))
    start_kst = datetime(y, m, d, tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()


def _previous_kst_date(report_date_kst):
    y, m, d = (int(part) for part in report_date_kst.split("-"))
    return (datetime(y, m, d) - timedelta(days=1)).strftime("%Y-%m-%d")


def _kst_date_minus_days(report_date_kst, days):
    y, m, d = (int(part) for part in report_date_kst.split("-"))
    return (datetime(y, m, d) - timedelta(days=days)).strftime("%Y-%m-%d")


def _resolve_as_of_utc(report_date_kst, as_of_utc=None):
    """The reference instant every age/freshness computation in this module
    is measured against. Deterministic-regeneration contract: for the
    CURRENT KST calendar date, "now" is real wall-clock time (today's
    report must stay genuinely real-time) -- but for any OTHER date
    (regenerating an archived/historical report later), the reference is
    pinned to the END of that KST calendar day, a pure function of
    report_date_kst with no dependency on when the regeneration actually
    runs. This is what makes re-running this module twice for the same
    past report_date_kst, at two different real times, produce byte-
    identical freshness_bucket/scores/ordering -- the exact determinism
    contract this function exists to satisfy. `as_of_utc` lets a caller
    (tests, or a future backfill tool) force a specific instant explicitly,
    bypassing this rule entirely."""
    if as_of_utc is not None:
        return as_of_utc
    now_utc = datetime.now(timezone.utc)
    today_kst = now_utc.astimezone(_KST).strftime("%Y-%m-%d")
    if report_date_kst == today_kst:
        return now_utc
    _, end_utc_iso = _kst_day_bounds_utc(report_date_kst)
    return datetime.fromisoformat(end_utc_iso)


# Freshness policy (locked contract, see SUPER_NEWS_HANDOFF.md phase-2
# spec): a story older than this is dropped from the daily candidate pool
# entirely -- it's not "old news ranked low," it's not real daily news
# anymore. Applies uniformly to every category; there is no BACKGROUND/
# CONTEXT section in V2.1 to demote it to instead, so exclusion is the only
# honest option (never silently keep it ranked at the bottom).
STALE_EXCLUSION_DAYS = 30

# Freshness buckets used for LEAD eligibility (report.web_data_v2 assigns
# tier from this): bucket 0 = real breaking-news window, bucket 1 = still
# a legitimate top story if nothing fresher exists that day, bucket 2 =
# old enough that it must never become a LEAD by default (no objective
# "why is this news again today" signal exists in this pipeline -- see
# module docstring for _freshness_bucket's caller).
_LEAD_ELIGIBLE_HOURS = 72
_STANDARD_ELIGIBLE_DAYS = 7


def _freshness_bucket(age_hours):
    """0 = within 72h (default LEAD-eligible), 1 = within 7 days (LEAD only
    if bucket 0 is empty that day -- see report.web_data_v2's tier
    assignment), 2 = older (7-30 days; never LEAD by default -- no
    current-event evidence signal exists in this pipeline to justify
    resurfacing it as the top story)."""
    if age_hours <= _LEAD_ELIGIBLE_HOURS:
        return 0
    if age_hours <= _STANDARD_ELIGIBLE_DAYS * 24:
        return 1
    return 2


def _age_hours(published_at_iso, now_utc):
    if not published_at_iso:
        return None
    try:
        published = datetime.fromisoformat(published_at_iso)
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return (now_utc - published).total_seconds() / 3600.0


# ---- Real, LLM-independent ranking signals (credential-independent
# architecture pass, 2026-08-14). Replaces plain -source_count as the
# within-bucket tie-break with a small weighted blend of continuous,
# already-real signals -- freshness_bucket remains the PRIMARY sort key
# (report.web_data_v2's LEAD-eligibility gate still reads it directly), but
# final_score now decides ordering both within a bucket and among
# same-bucket candidates instead of raw source_count alone. No signal here
# is invented: every input (age_hours, source quality tier, distinct-source
# count, recent-selection history) is already computed or already
# persisted elsewhere in this pipeline.
_FRESHNESS_HALFLIFE_HOURS = 48.0
_FRESHNESS_WEIGHT = 0.45
_SOURCE_QUALITY_WEIGHT = 0.25
_CORROBORATION_WEIGHT = 0.20
_NOVELTY_WEIGHT = 0.10

NOVELTY_LOOKBACK_DAYS = 3
_NOVELTY_REPEAT_SCORE = 0.3
_NOVELTY_FRESH_SCORE = 1.0

# Low-value content filter (SOURCE EXPANSION + CONTENT QUALITY HARDENING
# phase, 2026-08-15): a real top-20-by-score audit of production SOCIETY
# candidates found a daily horoscope column, a bare copyright-notice
# boilerplate item, and a private-individual obituary notice ranking
# inside the top 20 (would have landed in the visible STANDARD/BRIEF
# tier). Korean wire services conventionally bracket-tag these
# non-editorial content GENRES the same way across outlets -- this is a
# cross-source structural signal (any source using the same wire-service
# convention benefits), not a single-source regex hack. A match is never
# hard-dropped -- silently vanishing a real story on a false-positive
# genre match would be worse than a low-value one occasionally slipping
# through -- final_score is instead multiplied down sharply so it sinks
# far below the display cutoff through the exact same ranking mechanism
# every other signal here already uses. Deliberately narrow: a personnel/
# appointment notice like "[인사] 공정거래위원회" is genuine regulatory/
# institutional news and is NOT matched here.
_BOILERPLATE_BRACKET_GENRE_PATTERN = re.compile(r"\[[^\]]*(오늘의\s*운세|부고|오늘\s*날씨)[^\]]*\]")
# A bare "저작권" (copyright) keyword is NOT matched on its own -- a real
# story about copyright law/litigation legitimately uses that word. Only
# the outlet's own "[알림]" (site notice) bracket tag combined with
# "저작권" anywhere in the title is the routine copyright-notice pattern
# (confirmed real example: "[알림]뉴시스 콘텐츠 저작권 고지", where
# "저작권" itself falls outside the bracket).
_BOILERPLATE_NOTICE_TAG_PATTERN = re.compile(r"\[알림\]")
_BOILERPLATE_SCORE_MULTIPLIER = 0.15


def _is_boilerplate_genre(title):
    if not title:
        return False
    if _BOILERPLATE_BRACKET_GENRE_PATTERN.search(title):
        return True
    return bool(_BOILERPLATE_NOTICE_TAG_PATTERN.search(title)) and "저작권" in title


# ---- DAILY STALENESS POLICY (content-quality hardening pass, 2026-08-17)
# -- AI/ECONOMY/SOCIETY only. Confirmed real defect: a 3-day-old MIT
# Technology Review story (real published_at, late-collected) sat in the
# AI candidate pool with no real distinction from same-day coverage
# beyond freshness_bucket=1's own "still eligible if bucket 0 is empty"
# rule -- far too lenient for a "오늘의 브리핑" (today's briefing)
# product. SPOTIFY/TIKTOK (MUSIC) deliberately keep the existing, more
# lenient STALE_EXCLUSION_DAYS/_freshness_bucket policy untouched --
# chart/trade-press coverage has a genuinely slower, more forgiving news
# cycle than a daily general-news briefing, and this pass's own content
# review found no MUSIC lead-selection defect to justify tightening it. ----
_DAILY_STRICT_CATEGORIES = frozenset({"AI", "ECONOMY", "SOCIETY"})
# 0-48h: always eligible (no gate below applies). Within this window the
# existing continuous _freshness_score (halflife 48h) already prefers a
# ~24h-old item over a ~47h-old one -- no separate "prefer last 24h" gate
# is needed on top of it.
_DAILY_DEFAULT_EXCLUDE_AGE_HOURS = 48.0
# 72h+: ALWAYS excluded from the daily candidate pool, no exception --
# "오늘 뉴스 섹션에서 제외" is unconditional past this point.
_DAILY_HARD_EXCLUDE_AGE_HOURS = 72.0
# 48-72h: excluded UNLESS the item clears an objective, already-real
# "important follow-up/analysis" bar -- real multi-source corroboration
# or a real top-tier source quality score. Never a per-article name/
# keyword hardcode; the same two signals _corroboration_score/
# _source_quality_score_for_group already compute for every candidate.
_DAILY_IMPORTANT_EXCEPTION_MIN_SOURCE_COUNT = 3
_DAILY_IMPORTANT_EXCEPTION_MIN_QUALITY_SCORE = 0.85

# URL date-slug fallback (priority 4 of the published_at resolution
# chain: real published_at > RSS/API timestamp > structured metadata >
# URL date slug > collected_at) -- used ONLY when a group has no real
# published_at at all, or to detect a DATE_CONFLICT against one that
# does exist. Matches a YYYY-MM-DD-shaped run of digits anywhere in the
# URL (e.g. newsis.com's /NISX20260814_.../, or a generic /2026/08/14/
# path segment) -- never used to override a real published_at, only to
# fall back when one is missing or to flag reduced trust when the two
# disagree by more than a day.
_URL_DATE_SLUG_RE = re.compile(r"(20\d{2})[-/]?(0[1-9]|1[0-2])[-/]?(0[1-9]|[12]\d|3[01])")
_DATE_CONFLICT_THRESHOLD_HOURS = 24.0


def _extract_url_date_iso(url):
    """Best-effort fallback publish-date from a URL's own date slug --
    returns an ISO datetime (midnight UTC on that date) or None if no
    plausible YYYYMMDD-shaped run of digits is found. A coincidental
    numeric match (e.g. an article id that happens to look date-like) is
    an accepted, bounded risk of a pure string heuristic -- this value is
    NEVER trusted over a real published_at, only used as a fallback/
    cross-check (see _resolve_published_at_and_conflict)."""
    if not url:
        return None
    m = _URL_DATE_SLUG_RE.search(url)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _resolve_published_at_and_conflict(published_at_iso, source_urls):
    """Returns (effective_published_at_iso, conservative_published_at_iso,
    date_conflict).

    Priority: a real published_at (already RSS/API-sourced -- this
    pipeline's ingestion adapters populate it from the feed's own pubDate,
    i.e. priorities 1-3 of the requested chain in practice) is ALWAYS
    used as the DISPLAYED/effective value when present -- a URL date slug
    never overrides it. When published_at is missing, the first
    extractable URL date slug among the group's real source_urls is used
    as the priority-4 fallback; priority 5 (collected_at) is handled by
    the caller already treating a fully-unresolved age as "unknown," not
    here.

    date_conflict=True when a real published_at EXISTS and a URL date
    slug is ALSO extractable and the two disagree by more than
    _DATE_CONFLICT_THRESHOLD_HOURS -- a signal of reduced trust in the
    metadata (e.g. a republished/updated article). When that happens,
    conservative_published_at_iso is the OLDER of the two candidate
    timestamps -- used for STALENESS/AGE COMPUTATIONS ONLY ("낮은
    신뢰도로 처리" = never trust the newer-looking of two disagreeing
    timestamps for ranking purposes); it never changes what's shown to
    the reader (effective_published_at_iso). Equal to
    effective_published_at_iso whenever there's no conflict."""
    url_date = None
    for url in source_urls:
        url_date = _extract_url_date_iso(url)
        if url_date:
            break

    if published_at_iso:
        if url_date is None:
            return published_at_iso, published_at_iso, False
        published_dt = datetime.fromisoformat(published_at_iso)
        if published_dt.tzinfo is None:
            published_dt = published_dt.replace(tzinfo=timezone.utc)
        conflict = abs((published_dt - url_date).total_seconds()) > _DATE_CONFLICT_THRESHOLD_HOURS * 3600
        if conflict:
            conservative_dt = min(published_dt, url_date)
            return published_at_iso, conservative_dt.isoformat(), True
        return published_at_iso, published_at_iso, False

    if url_date is not None:
        return url_date.isoformat(), url_date.isoformat(), False
    return None, None, False


# ---- LEAD RANKING IMPROVEMENT (content-quality hardening pass,
# 2026-08-17) -- AI/ECONOMY/SOCIETY only, same reasoning as the staleness
# policy above. Deterministic, GENERALIZABLE keyword-pattern signals --
# the exact same established mechanism as _is_boilerplate_genre above,
# never a per-article name/title hardcode. Confirmed real defect: an
# uncorroborated (source_count==1) personal-harm allegation story
# outranked a well-covered industry M&A story for the AI section's LEAD
# slot, purely because the existing scoring model has no signal at all
# for "industry-structural significance" or "this is an individual-harm
# allegation that hasn't been corroborated yet." ----
_INDUSTRY_SIGNIFICANCE_RE = re.compile(
    r"(인수합병|인수|합병|M&A|acquisition|merger|"
    r"규제안|규제|법안|정책|regulation|regulatory|policy|"
    r"출시|launch|상장|IPO|투자유치|funding round|"
    r"반독점|antitrust|소송|lawsuit|"
    r"제휴|파트너십|partnership)",
    re.IGNORECASE,
)
_INDUSTRY_SIGNIFICANCE_BONUS = 0.12

# Deliberately narrow to real crime/abuse-victim-specific terms -- a
# generic reporting verb like "주장했다" (claimed) is far too common
# across ordinary legitimate hard news (political statements, corporate
# denials, etc.) to use as a signal on its own, and is NOT matched here.
_PERSONAL_HARM_ALLEGATION_RE = re.compile(
    r"(아동\s*성적|성적\s*학대|성적으로\s*노골적|의붓아버지|새아버지|계부|"
    r"성폭행|아동학대|폭행당|살해당|자살했|피해자가\s|"
    r"child sexual|sexually explicit|stepfather|stepmother|molest(ed|ation)?|"
    r"sexual assault|rape victim)",
    re.IGNORECASE,
)
# Never a hard exclusion -- a real, well-corroborated personal-harm story
# is still real, important news and can still become LEAD; this only
# down-weights the specific case of a SINGLE uncorroborated source,
# mirroring _BOILERPLATE_SCORE_MULTIPLIER's own "multiply down, never
# drop" philosophy.
_PERSONAL_HARM_UNCORROBORATED_MULTIPLIER = 0.6


def _freshness_score(age_hours):
    """Continuous exponential decay (halflife _FRESHNESS_HALFLIFE_HOURS),
    NOT the coarse 3-bucket step function _freshness_bucket already
    provides for LEAD-eligibility gating -- this is the finer-grained
    signal that lets two same-bucket candidates be told apart by real age
    instead of falling back to lexical event_key. Unknown age (no
    published_at) gets a neutral 0.5 -- never assumed fresh or stale,
    matching _freshness_bucket's own unknown-age handling."""
    if age_hours is None:
        return 0.5
    return math.exp(-age_hours / _FRESHNESS_HALFLIFE_HOURS)


def _corroboration_score(source_count):
    """Normalizes source_count (uncapped, real distinct-outlet count) onto
    a 0..1 signal: 1 source = 0.0 (no corroboration yet), 4+ sources = 1.0
    (saturates -- a 10-source story isn't "more true" than a 4-source one
    for ranking purposes, it's just well-corroborated either way)."""
    return min(1.0, (source_count - 1) / 3.0)


def _source_quality_score_for_group(source_names):
    """Best (max) quality score among the group's own real contributing
    sources -- a story picked up by even one primary/official outlet is a
    real, meaningful corroboration signal on its own, not diluted by also
    having lower-tier pickups. Falls back to the neutral
    report.source_metadata.DEFAULT_QUALITY_SCORE for any source with no
    recorded tier (never assumed best or worst)."""
    return max((_source_quality_score(name) for name in source_names), default=0.5)


def _recently_selected_event_keys(conn, category, report_date_kst, lookback_days):
    """event_keys the LLM selected for this category on ANY of the last
    `lookback_days` KST days (not just yesterday -- see _excluded_event_keys
    for the separate, narrower HARD 1-day exclusion this function does not
    replace). Used only for the soft `novelty_score` ranking signal: a
    story that keeps reappearing across recent reports (without being an
    exact yesterday-repeat, which is already hard-excluded) is real, but
    less novel than one appearing for the first time -- deprioritized, not
    dropped."""
    keys = set()
    for offset in range(1, lookback_days + 1):
        date = _kst_date_minus_days(report_date_kst, offset)
        keys |= _excluded_event_keys(conn, category, date)
    return keys


def _novelty_score(event_key, recently_selected_keys):
    return _NOVELTY_REPEAT_SCORE if event_key in recently_selected_keys else _NOVELTY_FRESH_SCORE


def _excluded_event_keys(conn, category, previous_date):
    """event_keys the LLM already selected (interpretation_items) for this
    category's most recent report on `previous_date`. Empty set if no report
    was generated for that category on that date -- never an error."""
    report_row = conn.execute(
        """SELECT run_id FROM reports
           WHERE report_date = ? AND category = ?
           ORDER BY generated_at DESC LIMIT 1""",
        (previous_date, category),
    ).fetchone()
    if report_row is None:
        return set()

    source_categories = _source_categories(category)
    placeholders = ",".join("?" for _ in source_categories)
    rows = conn.execute(
        f"""SELECT DISTINCT ni.event_key
           FROM interpretation_items ii
           JOIN llm_interpretations li ON li.id = ii.interpretation_id
           JOIN normalized_items ni ON ni.id = ii.normalized_item_id
           WHERE li.run_id = ? AND ni.category IN ({placeholders})""",
        (report_row["run_id"], *source_categories),
    ).fetchall()
    return {row["event_key"] for row in rows}


def select_news_candidates(conn, categories, report_date_kst, as_of_utc=None):
    """Returns dict category -> list[candidate dict], each list sorted
    deterministically. A category with zero eligible candidates gets an
    empty list -- never omitted from the returned dict.

    `as_of_utc`: see _resolve_as_of_utc -- None (the default) resolves to
    real wall-clock time for TODAY's report_date_kst, or a pinned
    end-of-KST-day instant for any other (archive/historical) date, so
    regenerating the same past report_date_kst later always reproduces the
    identical freshness_bucket/scores/ordering."""
    start_utc, end_utc = _kst_day_bounds_utc(report_date_kst)
    previous_date = _previous_kst_date(report_date_kst)

    now_utc = _resolve_as_of_utc(report_date_kst, as_of_utc)
    result = {}
    for category in categories:
        source_categories = _source_categories(category)
        excluded = _excluded_event_keys(conn, category, previous_date)
        recently_selected = _recently_selected_event_keys(conn, category, report_date_kst, NOVELTY_LOOKBACK_DAYS)

        placeholders = ",".join("?" for _ in source_categories)
        rows = conn.execute(
            f"""SELECT ni.id, ni.event_key, ni.entity_type, ni.entity_name,
                      ni.normalized_title, ri.source_name, ri.published_at, ri.source_url
               FROM normalized_items ni
               JOIN raw_items ri ON ri.id = ni.raw_item_id
               WHERE ni.category IN ({placeholders}) AND ri.collected_at >= ? AND ri.collected_at < ?
               ORDER BY ni.id ASC""",
            (*source_categories, start_utc, end_utc),
        ).fetchall()

        groups = {}
        for row in rows:
            if row["event_key"] in excluded:
                continue
            group = groups.setdefault(
                row["event_key"],
                {
                    "event_key": row["event_key"],
                    "id": row["id"],
                    "entity_type": row["entity_type"],
                    "entity_name": row["entity_name"],
                    "normalized_title": row["normalized_title"],
                    "item_ids": [],
                    "source_names": set(),
                    "published_at_values": [],
                    "source_urls": [],
                },
            )
            group["item_ids"].append(row["id"])
            group["source_names"].add(row["source_name"])
            if row["published_at"]:
                group["published_at_values"].append(row["published_at"])
            if row["source_url"]:
                group["source_urls"].append(row["source_url"])

        candidates = []
        for group in groups.values():
            # The freshest real published_at among the group's own items --
            # multiple outlets covering the same event_key can each report
            # at a different instant; the story's actual age is how recent
            # the most recent real coverage of it is, not the first.
            # PUBLISHED_AT RESOLUTION CHAIN (content-quality hardening
            # pass): effective_published_at is what's shown/stored as this
            # candidate's real published_at (never overridden by a URL
            # slug guess when a real timestamp exists); conservative_
            # published_at is the OLDER of the two when a DATE_CONFLICT is
            # detected (see _resolve_published_at_and_conflict) and is
            # what every age/staleness/freshness computation below
            # actually uses -- "낮은 신뢰도로 처리" means never trusting
            # the newer-looking of two disagreeing timestamps for ranking
            # purposes. Existing candidates with no URL date-slug match
            # (the overwhelming majority) see zero behavior change here.
            latest_published_at = max(group["published_at_values"], default=None)
            effective_published_at, conservative_published_at, date_conflict = (
                _resolve_published_at_and_conflict(latest_published_at, group["source_urls"])
            )
            age_hours = _age_hours(conservative_published_at, now_utc)
            # Unknown age (no published_at on any item in the group) is
            # never silently treated as fresh (bucket 0) -- but it's not
            # treated as confirmed-old either, since missing metadata is
            # not evidence the story is actually 7-30 days old. Bucket 1:
            # LEAD-eligible only as the same graceful fallback bucket 1
            # already is when nothing in bucket 0 exists that day, never
            # hard-excluded.
            if age_hours is None:
                stale = False
                bucket = 1
            else:
                stale = age_hours > STALE_EXCLUSION_DAYS * 24
                bucket = _freshness_bucket(age_hours)
            if stale:
                continue
            source_count = len(group["source_names"])
            source_quality_score = _source_quality_score_for_group(group["source_names"])
            # DAILY STALENESS POLICY (AI/ECONOMY/SOCIETY only -- see the
            # module-level constants' own docstring): a real, KNOWN age
            # (age_hours is not None) past 72h is unconditionally excluded
            # from the daily candidate pool; past 48h it survives only via
            # the same real corroboration/quality signals computed for
            # every candidate, never a per-article hardcode. An unknown
            # age is never penalized here -- same "not evidence of
            # staleness" principle as the bucket/stale logic above.
            if category in _DAILY_STRICT_CATEGORIES and age_hours is not None:
                if age_hours > _DAILY_HARD_EXCLUDE_AGE_HOURS:
                    continue
                if age_hours > _DAILY_DEFAULT_EXCLUDE_AGE_HOURS:
                    is_important_followup = (
                        source_count >= _DAILY_IMPORTANT_EXCEPTION_MIN_SOURCE_COUNT
                        or source_quality_score >= _DAILY_IMPORTANT_EXCEPTION_MIN_QUALITY_SCORE
                    )
                    if not is_important_followup:
                        continue
            freshness_score = _freshness_score(age_hours)
            corroboration_score = _corroboration_score(source_count)
            novelty_score = _novelty_score(group["event_key"], recently_selected)
            is_boilerplate_genre = _is_boilerplate_genre(group["normalized_title"])
            final_score = (
                _FRESHNESS_WEIGHT * freshness_score
                + _SOURCE_QUALITY_WEIGHT * source_quality_score
                + _CORROBORATION_WEIGHT * corroboration_score
                + _NOVELTY_WEIGHT * novelty_score
            )
            if is_boilerplate_genre:
                final_score *= _BOILERPLATE_SCORE_MULTIPLIER
            # LEAD RANKING IMPROVEMENT (AI/ECONOMY/SOCIETY only -- see the
            # module-level constants' own docstring): real, deterministic,
            # keyword-pattern signals, never a per-article hardcode.
            is_industry_significant = False
            is_personal_harm_uncorroborated = False
            if category in _DAILY_STRICT_CATEGORIES:
                title_text = group["normalized_title"] or ""
                if _INDUSTRY_SIGNIFICANCE_RE.search(title_text):
                    is_industry_significant = True
                    final_score += _INDUSTRY_SIGNIFICANCE_BONUS
                if _PERSONAL_HARM_ALLEGATION_RE.search(title_text) and source_count <= 1:
                    is_personal_harm_uncorroborated = True
                    final_score *= _PERSONAL_HARM_UNCORROBORATED_MULTIPLIER
            candidates.append(
                {
                    "id": group["id"],
                    "category": category,
                    "event_key": group["event_key"],
                    "entity_type": group["entity_type"],
                    "entity_name": group["entity_name"],
                    "normalized_title": group["normalized_title"],
                    "date_conflict": date_conflict,
                    "is_industry_significant": is_industry_significant,
                    "is_personal_harm_uncorroborated": is_personal_harm_uncorroborated,
                    "source_count": source_count,
                    "source_names": sorted(group["source_names"]),
                    "item_ids": sorted(group["item_ids"]),
                    "published_at": effective_published_at,
                    "age_hours": age_hours,
                    "freshness_bucket": bucket,
                    # Real, LLM-independent ranking evidence (all inputs
                    # already computed above or already persisted -- see the
                    # module-level comment on this block). event_key is
                    # never anything but the final deterministic tie-break
                    # below; it does not itself drive ranking.
                    "freshness_score": round(freshness_score, 4),
                    "source_quality_score": round(source_quality_score, 4),
                    "corroboration_score": round(corroboration_score, 4),
                    "novelty_score": round(novelty_score, 4),
                    "is_boilerplate_genre": is_boilerplate_genre,
                    "final_score": round(final_score, 4),
                }
            )
        # Freshness bucket is the PRIMARY sort key -- a 7+ day old story can
        # no longer outrank a same-day story purely on higher source_count
        # (the original bug this fixed: an old high-source-count item
        # sorting above fresh same-day coverage). final_score (a weighted
        # blend of continuous freshness/source-quality/corroboration/
        # novelty -- see the block above) is the ranking signal WITHIN a
        # freshness bucket, replacing plain -source_count so within-bucket
        # ordering is no longer decided by corroboration count alone.
        # event_key remains the final deterministic tie-break (real ties on
        # final_score are rare but possible, e.g. a single-source group with
        # no published_at) -- it is never itself a ranking signal.
        candidates.sort(key=lambda c: (c["freshness_bucket"], -c["final_score"], c["event_key"]))
        result[category] = candidates

    return result

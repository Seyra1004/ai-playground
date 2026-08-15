"""V2.1 structured, read-only data reader for the Intelligence Dashboard
hierarchy (TODAY IN 30 SECONDS / TIKTOK / SPOTIFY / MUSIC INDUSTRY /
INTELLIGENCE / AI / ECONOMY / SOCIETY / PRODUCER INTELLIGENCE / SOURCES).

Additive alongside report/web_data.py (V1) -- does not modify or replace
it. V1 remains the currently-deployed dashboard's data source; this module
is not wired into production generation/deployment in this pass.

Never fabricates: every section that has no real underlying data (TikTok
chart/trend data, cross-platform labels, forecasts, producer insights)
renders an honest UNAVAILABLE/INSUFFICIENT_HISTORY/empty state rather than
filler. Reuses V1's news classification (report.web_data._classify_state)
and the same find_latest_report_run_id() resolution so V1, V2, and Kakao
all describe the same run.

V2.1 depth additions (editorial redesign, all read-only re-derivations of
already-persisted/already-computed facts -- no new LLM call except
Producer Intelligence, which is its own separately-run, separately-gated
daily synthesis read here, never generated at render time):
- news items carry snippet (raw_items.snippet, dropped if redundant with
  the LLM's `reason`), source_count (re-derived the same way report/
  candidate_selection.py originally computed it, scoped to the same KST
  day window), and a LEAD/STANDARD/BRIEF `tier` -- tier follows the LLM's
  own selection ORDER (the existing relevance signal: item 0 is already
  "the" pick V1's key-points feature relies on) as the PRIMARY signal;
  source_count is corroboration/context only and never determines tier by
  itself, per the locked V2.1 direction.
- spotify_chart top10 entries carry peak_rank/days_on_chart (from real
  music_observations history), and the section carries a `trend` block
  (mechanical up/down/new counts + a threshold-bucketed volatility label
  -- a categorization of real numbers, not an invented judgment).
- producer_intelligence reads the latest (if any) already-validated
  report.producer_synthesis output for this date's dedicated run --
  honestly empty/unavailable if none was generated or the evidence was
  too thin that day; this module never runs synthesis itself.
"""

import json
from datetime import datetime, timedelta, timezone

from music.catalog_revival import detect_catalog_revival_candidates
from music.cross_platform import classify_cross_platform_state, detect_cross_platform_signals
from music.early_signal import select_early_signal_candidates
from music.forecast_gate import check_forecast_readiness
from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff
from report.candidate_selection import _kst_day_bounds_utc, select_news_candidates
from report.news_intelligence_synthesis import validate_news_intelligence
from report.story_clustering import cluster_candidates
from report.translation import NullTranslationProvider, build_translation_provider, translate_and_cache
from report.web_data import _classify_state
from report_delivery import find_latest_report_run_id

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

NEWS_CATEGORIES = ("AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY")

# Phase 3C production-pilot policy: real translation is scoped to these
# three categories only -- TIKTOK/SPOTIFY news items keep displaying their
# real, untranslated original text (NullTranslationProvider -> the
# existing, already-verified zero-network/zero-DB-write TRANSLATION_
# UNAVAILABLE path, same honest-fallback contract as any other unconfigured
# provider). Deliberately the SAME three categories News Intelligence
# already scopes itself to (see _NEWS_INTELLIGENCE_CATEGORIES below, and
# report.news_intelligence_orchestrator's own docstring for why TIKTOK/
# SPOTIFY are Music Industry's own evidence, not this pass's concern) --
# one real constant, not two independently-maintained category lists that
# could silently drift apart.
_TRANSLATION_ELIGIBLE_CATEGORIES = ("AI", "ECONOMY", "SOCIETY")

# Which registered chart source powers the SPOTIFY section's TOP10/trend/
# viral/new-song data. Apple Music remains registered and IS still surfaced
# in the INTELLIGENCE > Early Signal section (labeled by its own source
# name, never hidden) -- but per the locked V2 requirement "Apple Music
# must not dominate the MUSIC briefing," it does not get its own top-level
# section the way TikTok/Spotify do.
SPOTIFY_CHART_SOURCE = "spotify_chart"

STATE_UNAVAILABLE = "UNAVAILABLE"

# A news category whose LLM selection is unavailable (missing/failed LLM
# provider -- see report.llm_interface's provider abstraction, unchanged
# here) but that DOES have real, already-ingested candidates for today.
# Distinct from DEGRADED (which means no usable data exists at all) --
# an LLM outage must never hide real collected news, only the AI-authored
# "why it matters" layer on top of it. See _raw_fallback_items.
STATE_UNINTERPRETED = "UNINTERPRETED"

# Deliberately separate from report.validation.MAX_SELECTIONS_PER_CATEGORY
# (5) -- that constant bounds how many items an LLM may CURATE into an
# editorial selection, an output-quality constraint on the AI-authored
# path. The raw fallback below is the opposite case: real, already-
# deduplicated (by event_key) candidates shown BECAUSE no LLM curation
# ran. Capping it at the LLM's editorial limit would shrink real news
# specifically to hide an LLM outage, which is the one failure mode this
# fallback exists to prevent. 12 is a display cap only, not a quality
# threshold -- select_news_candidates already returns every real
# same-day candidate, deterministically ordered.
_FALLBACK_DISPLAY_LIMIT = 12

PRODUCER_INTELLIGENCE_CATEGORY = "MUSIC_PRODUCER_INTELLIGENCE"
MUSIC_TREND_INTELLIGENCE_CATEGORY = "MUSIC_TREND_INTELLIGENCE"

# Real counts (never invented) bucketed into a categorical label for
# display -- purely a classification of already-real numbers, same idea as
# any dashboard's "high/medium/low" severity tier driven by a threshold.
_TREND_HIGH_THRESHOLD = 6
_TREND_MEDIUM_THRESHOLD = 3


def _tier_for(index, freshness_bucket):
    """LEAD is reserved for index 0 AND a freshness bucket of 0 or 1 (<=7
    days old -- report.candidate_selection._freshness_bucket) -- a story
    older than 7 days can never become a LEAD by default, since this
    pipeline has no objective "why is this news again today" signal to
    justify it (see candidate_selection's own docstring). If the very top
    candidate is bucket 2, no item gets LEAD that category/day -- an
    honest "no fresh top story" rather than forcing an old one into the
    lead slot."""
    if index == 0 and freshness_bucket is not None and freshness_bucket <= 1:
        return "LEAD"
    if index <= 1:
        return "STANDARD"
    return "BRIEF"


def _freshness_bucket_from_published_at(published_at, report_date_kst, now_utc=None):
    """Same bucketing rule as report.candidate_selection._freshness_bucket,
    re-derived here from a single item's own published_at (used on the
    LLM-selected path, where the persisted selection doesn't carry the
    bucket candidate_selection already computed for the raw pool). Missing
    published_at is never treated as fresh -- returns bucket 2 (never
    LEAD by default), matching candidate_selection's own unknown-age
    handling.

    `now_utc` defaults through report.candidate_selection._resolve_as_of_utc
    (report_date_kst-pinned for a historical date, real wall-clock for
    today) -- the SAME deterministic-regeneration contract
    select_news_candidates uses for the raw candidate pool, so an
    LLM-selected item's LEAD/STANDARD/BRIEF tier is just as reproducible on
    archive regeneration as a fallback item's."""
    from report.candidate_selection import _age_hours, _freshness_bucket, _resolve_as_of_utc

    now_utc = now_utc or _resolve_as_of_utc(report_date_kst)
    age_hours = _age_hours(published_at, now_utc)
    if age_hours is None:
        return 1
    return _freshness_bucket(age_hours)


def _attach_translation(conn, provider, item):
    """Additive original_title/ko_title/translation_status AND original_
    snippet/ko_snippet/snippet_translation_status fields -- item["title"]/
    item["snippet"] (the real original text) are NEVER overwritten, so a
    TRANSLATION_UNAVAILABLE outcome (the only real outcome in this
    environment, no credential configured -- see report/translation.py)
    still leaves both real original strings fully displayed exactly as
    before this change. ko_title/ko_snippet are None whenever their own
    status != TRANSLATED -- never a fabricated translation string.

    title and snippet are cached/translated as two INDEPENDENT calls
    (report.translation.translate_and_cache is keyed on the real text
    content itself) -- a category whose fallback path drops a redundant
    snippet (see _is_redundant elsewhere in this module) simply has no
    snippet text here at all, so no snippet-translation call happens for
    it; that's correct, not a gap, since there is no real second fact to
    translate in that case."""
    # NOT_REQUIRED (already-sufficiently-Korean source text, see
    # report.translation._is_already_korean) is treated the same as
    # TRANSLATED for display purposes -- both mean "ko_title/ko_snippet is
    # real, displayable text," just via a different (free, deterministic)
    # path instead of a provider call.
    _DISPLAYABLE = ("TRANSLATED", "NOT_REQUIRED")

    title_result = translate_and_cache(conn, provider, item.get("title"))
    item["original_title"] = item.get("title")
    item["ko_title"] = title_result["translated_text"] if title_result["status"] in _DISPLAYABLE else None
    item["translation_status"] = title_result["status"]

    snippet_text = item.get("snippet")
    item["original_snippet"] = snippet_text
    if snippet_text:
        snippet_result = translate_and_cache(conn, provider, snippet_text)
        item["ko_snippet"] = snippet_result["translated_text"] if snippet_result["status"] in _DISPLAYABLE else None
        item["snippet_translation_status"] = snippet_result["status"]
    else:
        # No real snippet text exists for this item -- never a fabricated
        # "unavailable" outcome for a translation that was never attempted
        # because there was nothing real to translate.
        item["ko_snippet"] = None
        item["snippet_translation_status"] = None
    return item


def _is_redundant(candidate, reference):
    if not candidate:
        return True
    if not reference:
        return False
    c, r = candidate.strip().lower(), reference.strip().lower()
    if not c:
        return True
    return c in r or r in c


def _lookup_item_detail(conn, normalized_item_id):
    row = conn.execute(
        """SELECT ni.normalized_title AS title, ni.event_key AS event_key,
                  ri.source_url AS source_url, ri.snippet AS snippet, ri.source_name AS source_name,
                  ri.published_at AS published_at
           FROM normalized_items ni
           JOIN raw_items ri ON ri.id = ni.raw_item_id
           WHERE ni.id = ?""",
        (normalized_item_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "title": row["title"], "source_url": row["source_url"], "snippet": row["snippet"],
        "source_name": row["source_name"], "event_key": row["event_key"], "published_at": row["published_at"],
    }


def _source_count_for_event(conn, event_key, report_date_kst):
    """Re-derives EXACTLY what report/candidate_selection.py computed as
    source_count at selection time: distinct outlets covering this
    event_key within the same KST day window. Never recomputed over all
    history -- an event_key can legitimately recur on a later day for a
    genuinely new development in the same story (see candidate_selection's
    own stale-exclusion docstring), so counting outside the day window
    would overstate corroboration for an old story resurfacing."""
    start_utc, end_utc = _kst_day_bounds_utc(report_date_kst)
    row = conn.execute(
        """SELECT COUNT(DISTINCT ri.source_name) AS cnt
           FROM normalized_items ni JOIN raw_items ri ON ri.id = ni.raw_item_id
           WHERE ni.event_key = ? AND ri.collected_at >= ? AND ri.collected_at < ?""",
        (event_key, start_utc, end_utc),
    ).fetchone()
    return row["cnt"] if row and row["cnt"] else 1


# Safety ceiling on how many of the real, already-ranked same-day
# candidates get a detail lookup for diversity bucketing -- NOT a
# diversity-search depth tuned against today's data (a category topping
# out around 1-2k same-day candidates is normal and cheap for indexed
# single-row lookups on a local SQLite connection in a once-daily batch
# script; this exists only to bound pathological growth, not to trade
# off against finding every real distinct source).
_FALLBACK_CANDIDATE_LOOKUP_CEILING = 5000


def _diversify_by_source(detailed_candidates, limit):
    """Round-robins the real candidate pool across distinct source_names so
    one prolific source (e.g. a provider blog that publishes far more
    items per day than any news outlet) cannot silently crowd out an
    entire fallback display just by volume. Bucket order = each source's
    first appearance in the existing -source_count/event_key ranking, and
    the ranking WITHIN a source's own bucket is untouched -- this only
    interleaves across sources, it never re-scores individual stories.
    Falls back to draining whichever buckets remain once others are
    exhausted, so the display is never shorter than the real data
    actually supports (never pads to hit a diversity target)."""
    buckets = {}
    source_order = []
    for pair in detailed_candidates:
        source_name = pair[1]["source_name"]
        if source_name not in buckets:
            buckets[source_name] = []
            source_order.append(source_name)
        buckets[source_name].append(pair)

    result = []
    round_index = 0
    while len(result) < limit:
        added_this_round = False
        for source_name in source_order:
            bucket = buckets[source_name]
            if round_index < len(bucket):
                result.append(bucket[round_index])
                added_this_round = True
                if len(result) == limit:
                    break
        if not added_this_round:
            break
        round_index += 1
    return result


def _cluster_suppression(candidates):
    """NEWS QUALITY pass: applies report.story_clustering's real,
    high-precision near-duplicate-event detection to the DISPLAYED list
    itself, not merely as additive footnote evidence (see
    _story_clusters_for_category below, which still separately renders the
    full evidence block unchanged) -- multiple independent outlets
    covering the identical real event (e.g. three publishers all covering
    the same product announcement) must read as ONE top-level story, not
    three. Returns (suppressed_event_keys, representative_counts):
    `suppressed_event_keys` are every NON-representative member of a real
    >=2-member cluster (never the representative itself, and never a
    candidate with no real near-duplicate -- cluster_candidates only
    returns real, high-precision-agreed groups, see that module's own
    "recall sacrificed for precision" docstring); `representative_counts`
    maps a representative's own event_key to its real (related_article_
    count, distinct_source_count) for an honest "N개 매체 관련 보도" chip --
    never a fabricated number. Callers must filter suppressed candidates
    OUT of the top-level list entirely -- their real coverage is preserved
    as cluster evidence (report.web_render_v2._render_cluster_evidence),
    never as a second independent-looking top story."""
    clusters = cluster_candidates(candidates)
    suppressed_event_keys = set()
    representative_counts = {}
    for cluster in clusters:
        representative_key = cluster["representative_event_key"]
        representative_counts[representative_key] = (
            cluster["related_article_count"], cluster["distinct_source_count"],
        )
        for member_key in cluster["member_event_keys"]:
            if member_key != representative_key:
                suppressed_event_keys.add(member_key)
    return suppressed_event_keys, representative_counts


def _raw_fallback_items(conn, category, report_date_kst, limit=_FALLBACK_DISPLAY_LIMIT):
    """Real, already-ingested candidates for this category+date, used ONLY
    when no LLM selection exists (missing/failed LLM provider, or no
    report run attempted yet) -- see report.llm_interface.build_llm for
    the provider-swap abstraction this module never touches; an LLM
    outage is not fixed here, only worked around so it can never hide
    real news. Ranked by report.candidate_selection's own existing sort
    (-source_count, event_key) -- the SAME ordering already computed as
    the LLM's own input, re-run here read-only -- with real near-duplicate
    same-event coverage merged into one representative story
    (_cluster_suppression) BEFORE _diversify_by_source interleaves across
    distinct real sources so the fallback page itself doesn't read as
    single-source when the real data has more breadth than that. Never an
    LLM call. `reason` is always None (never a fabricated "why it
    matters" -- the renderer already omits that line entirely when reason
    is absent, exactly the same as any other item lacking one)."""
    candidates = select_news_candidates(conn, [category], report_date_kst)[category]
    pool = candidates[:_FALLBACK_CANDIDATE_LOOKUP_CEILING]

    suppressed_event_keys, representative_counts = _cluster_suppression(pool)
    pool = [c for c in pool if c["event_key"] not in suppressed_event_keys]

    detailed = []
    for candidate in pool:
        detail = _lookup_item_detail(conn, candidate["id"])
        if detail is None:
            continue
        detailed.append((candidate, detail))

    provider = build_translation_provider() if category in _TRANSLATION_ELIGIBLE_CATEGORIES else NullTranslationProvider()
    items = []
    for index, (candidate, detail) in enumerate(_diversify_by_source(detailed, limit)):
        # Same redundancy guard _news_section applies to the LLM-selected
        # path: a snippet that just restates the headline (e.g. a feed
        # whose description is only the title wrapped in a link, Google
        # News' search RSS being the known real case) isn't a real second
        # fact and must not render as one.
        snippet = detail["snippet"]
        if _is_redundant(snippet, detail["title"]):
            snippet = None
        item = {
            "id": candidate["id"],
            "title": detail["title"],
            "reason": None,
            "snippet": snippet,
            "source_url": detail["source_url"],
            "source_name": detail["source_name"],
            "published_at": detail["published_at"],
            "source_count": candidate["source_count"],
            "tier": _tier_for(index, candidate.get("freshness_bucket")),
        }
        related = representative_counts.get(candidate["event_key"])
        if related:
            item["related_article_count"], item["related_source_count"] = related
        items.append(_attach_translation(conn, provider, item))
    return items


def _story_clusters_for_category(conn, category, report_date_kst):
    """Real, non-LLM near-duplicate-event evidence (report.story_clustering)
    computed from the SAME candidate pool select_news_candidates already
    produces -- independent of whether this category ends up on the
    LLM-selected or the raw-fallback display path, since a real cluster is
    evidence about the underlying coverage, not about which items happen
    to be shown today. Never raises for zero candidates (cluster_candidates
    itself returns [] for an empty/singleton pool)."""
    candidates = select_news_candidates(conn, [category], report_date_kst)[category]
    return cluster_candidates(candidates)


NEWS_INTELLIGENCE_CATEGORY = "NEWS_INTELLIGENCE_V2"

# Only the categories/tiers report.news_intelligence_orchestrator actually
# synthesizes for -- see that module's own _ELIGIBLE_CATEGORIES docstring
# for why TIKTOK/SPOTIFY are excluded (their news items are Music
# Industry's own evidence, already cited elsewhere). Same set as
# _TRANSLATION_ELIGIBLE_CATEGORIES above, by design -- aliased, not
# independently maintained.
_NEWS_INTELLIGENCE_CATEGORIES = _TRANSLATION_ELIGIBLE_CATEGORIES


def _attach_news_intelligence(conn, report_date_kst, items):
    """Additive what_happened/why_it_matters/what_to_watch +
    ai_intelligence_status fields, read back from the separately-run,
    separately-gated report.news_intelligence_orchestrator daily synthesis
    -- never generated at render time (same contract as
    _producer_intelligence_section below). Re-validates on every read
    (report.news_intelligence_synthesis.validate_news_intelligence), same
    "validate on every read, reused or not" rule report.producer_
    orchestrator already documents. An item missing from the validated
    result (nothing persisted yet, a malformed row, or one invalid field)
    simply gets ai_intelligence_status=UNAVAILABLE -- the real title/
    source/snippet fields on `item` are untouched either way; this never
    hides or replaces them."""
    row = conn.execute(
        """SELECT li.output_text FROM llm_interpretations li
           JOIN runs r ON r.id = li.run_id
           WHERE li.category = ? AND r.run_date = ?
           ORDER BY li.id DESC LIMIT 1""",
        (NEWS_INTELLIGENCE_CATEGORY, report_date_kst),
    ).fetchone()
    validated = {}
    if row is not None:
        try:
            parsed = json.loads(row["output_text"])
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            items_by_id = {item["id"]: item for item in items if item.get("id") is not None}
            validated = validate_news_intelligence(parsed, items_by_id)

    for item in items:
        fields = validated.get(item.get("id"))
        if fields:
            item["what_happened"] = fields["what_happened"]
            item["why_it_matters"] = fields["why_it_matters"]
            item["what_to_watch"] = fields["what_to_watch"]
            item["ai_intelligence_status"] = "AVAILABLE"
        else:
            item["what_happened"] = None
            item["why_it_matters"] = None
            item["what_to_watch"] = None
            item["ai_intelligence_status"] = "UNAVAILABLE"
    return items


def _news_section(conn, status_by_category, selections_by_category, category, report_date_kst):
    state = _classify_state(status_by_category.get(category))
    provider = build_translation_provider() if category in _TRANSLATION_ELIGIBLE_CATEGORIES else NullTranslationProvider()
    items = []
    for index, selection in enumerate(selections_by_category.get(category) or []):
        if not isinstance(selection, dict) or "id" not in selection:
            continue
        detail = _lookup_item_detail(conn, selection["id"])
        if detail is None:
            continue
        reason = selection.get("reason")
        # Dropped if it repeats EITHER the headline or the reason -- a
        # snippet that just restates the title (not merely the LLM's
        # reason) is exactly the duplication bug this guards against.
        snippet = detail["snippet"]
        if _is_redundant(snippet, reason) or _is_redundant(snippet, detail["title"]):
            snippet = None
        item = {
            "id": selection["id"],
            "title": detail["title"],
            "reason": reason,
            "snippet": snippet,
            "source_url": detail["source_url"],
            "source_name": detail["source_name"],
            "published_at": detail["published_at"],
            "source_count": _source_count_for_event(conn, detail["event_key"], report_date_kst),
            "tier": _tier_for(index, _freshness_bucket_from_published_at(detail["published_at"], report_date_kst)),
        }
        items.append(_attach_translation(conn, provider, item))
    clusters = _story_clusters_for_category(conn, category, report_date_kst)
    if items:
        if category in _NEWS_INTELLIGENCE_CATEGORIES:
            items = _attach_news_intelligence(conn, report_date_kst, items)
        return {"state": state, "items": items, "clusters": clusters}

    fallback_items = _raw_fallback_items(conn, category, report_date_kst)
    if fallback_items:
        if category in _NEWS_INTELLIGENCE_CATEGORIES:
            fallback_items = _attach_news_intelligence(conn, report_date_kst, fallback_items)
        return {"state": STATE_UNINTERPRETED, "items": fallback_items, "clusters": clusters}
    return {"state": state, "items": items, "clusters": clusters}


def _distinct_kst_days(observed_at_values):
    """True days-on-chart count: distinct KST CALENDAR DATES among the
    given observed_at instants -- NOT a count of observation rows. Two
    observations recorded on the same KST calendar day (e.g. a manual
    rerun) must not inflate this; this is what makes it honest to label
    as "일" (days) rather than a raw observation count."""
    days = {datetime.fromisoformat(v).astimezone(_KST).strftime("%Y-%m-%d") for v in observed_at_values}
    return len(days)


def _enrich_chart_entry(conn, entry, source_name, observed_at):
    """Adds fields the fact-ownership design assigns to TOP10/Viral
    context, all real/derived, none invented:
    - previous_rank: derived from rank_delta (prev_rank - current_rank,
      per music.signal_engine.compute_chart_diff), NEVER computed for a
      NEW entry, which has no real previous rank to show.
    - observed_at: the shared diff-level snapshot instant this whole
      TOP10 belongs to, propagated onto each entry for display.
    - region: the ACTUAL music_observations.region value for this entity's
      most recent observation -- read from the DB, never hardcoded here,
      so a future change to a collector's region/market constant can never
      silently desync from what's displayed.
    - peak_rank / days_on_chart: from real music_observations history;
      days_on_chart is a genuine distinct-KST-calendar-day count (see
      _distinct_kst_days), not a raw observation-row count."""
    history_row = conn.execute(
        "SELECT MIN(metric_value) AS peak_rank FROM music_observations WHERE music_entity_id = ? AND source_name = ?",
        (entry["music_entity_id"], source_name),
    ).fetchone()
    observed_at_rows = conn.execute(
        "SELECT DISTINCT observed_at FROM music_observations WHERE music_entity_id = ? AND source_name = ?",
        (entry["music_entity_id"], source_name),
    ).fetchall()
    region_row = conn.execute(
        """SELECT region FROM music_observations
           WHERE music_entity_id = ? AND source_name = ? AND observed_at = ? LIMIT 1""",
        (entry["music_entity_id"], source_name, observed_at),
    ).fetchone()

    enriched = dict(entry)
    enriched["observed_at"] = observed_at
    enriched["region"] = region_row["region"] if region_row else None
    # previous_rank is real only when a real prior rank exists -- true for
    # neither a genuine NEW re-entry nor a FIRST_OBSERVED baseline entry
    # (both have rank_delta=None from music.signal_engine), so this reads
    # rank_delta directly rather than the (pre-normalization) is_new flag.
    enriched["previous_rank"] = None if entry["rank_delta"] is None else entry["rank"] + entry["rank_delta"]
    # V2 data-boundary contract (FIRST_OBSERVED/NEW audit fix): is_new must
    # be True ONLY when status == "NEW" (a genuine re-entry with real
    # absence-then-presence history) -- NEVER for status == "FIRST_OBSERVED"
    # (a baseline being established, with no real prior data to compare
    # against at all). music.signal_engine.compute_chart_diff's raw is_new
    # (True for BOTH cases, V1-compatible -- report/music_diff.py, the V1/
    # Kakao consumer, reads that raw dict directly and is intentionally left
    # unchanged, see SUPER_NEWS_HANDOFF.md's LEGACY_KNOWN_ISSUE entry) is
    # corrected HERE, at the V2-only normalization boundary, so every V2
    # consumer of `is_new` downstream of _enrich_chart_entry gets the
    # correct value structurally -- it can never misread a FIRST_OBSERVED
    # baseline entry as a real NEW entry by reading is_new alone, without
    # separately having to remember to also check `status`.
    enriched["is_new"] = entry.get("status") == "NEW"
    enriched["peak_rank"] = int(history_row["peak_rank"]) if history_row and history_row["peak_rank"] is not None else entry["rank"]
    enriched["days_on_chart"] = _distinct_kst_days([r["observed_at"] for r in observed_at_rows]) or 1
    return enriched


def _trend_summary(top10, is_first_observation):
    """Mechanical counts from already-present is_new/rank_delta fields
    (same math as report/web_render.py's V1 _music_change_counts), plus a
    threshold-bucketed volatility label -- a categorization of the real
    total-movers count, never an invented forecast.

    On a first-observation day (no prior snapshot exists at all -- see
    music.signal_engine.compute_chart_diff), every entry mechanically has
    is_new=True, but that is a baseline being established, not 10 real
    same-day chart entries -- new_count is reported as 0 and the real
    count moves to first_observation_count instead, so the rendered
    narrative never reads as "10 new entries today."""
    if is_first_observation:
        return {
            "new_count": 0, "up_count": 0, "down_count": 0,
            "first_observation_count": len(top10), "volatility": "LOW",
        }
    new_count = sum(1 for e in top10 if e["is_new"])
    up_count = sum(1 for e in top10 if not e["is_new"] and (e.get("rank_delta") or 0) > 0)
    down_count = sum(1 for e in top10 if not e["is_new"] and (e.get("rank_delta") or 0) < 0)
    total_movers = new_count + up_count + down_count
    if total_movers >= _TREND_HIGH_THRESHOLD:
        volatility = "HIGH"
    elif total_movers >= _TREND_MEDIUM_THRESHOLD:
        volatility = "MEDIUM"
    else:
        volatility = "LOW"
    return {
        "new_count": new_count, "up_count": up_count, "down_count": down_count,
        "first_observation_count": 0, "volatility": volatility,
    }


def _spotify_chart_section(conn, report_date_kst):
    """TOP10 + new-entry list from the real spotify_chart source. Returns
    state=UNAVAILABLE only if the chart source has never produced a
    snapshot at all -- an empty-but-attempted snapshot is a NORMAL empty
    result, not unavailable (distinguishes 'no data source' from 'source
    ran, found nothing new today')."""
    metric_name = ACTIVE_MUSIC_SOURCES[SPOTIFY_CHART_SOURCE]["metric_name"]
    diff = compute_chart_diff(conn, report_date_kst, SPOTIFY_CHART_SOURCE, metric_name)
    if diff["observed_at"] is None:
        return {"state": STATE_UNAVAILABLE, "top10": [], "new_entries": [], "trend": None, "is_first_observation": False}
    is_first_observation = diff.get("is_first_observation", False)
    top10 = [_enrich_chart_entry(conn, e, SPOTIFY_CHART_SOURCE, diff["observed_at"]) for e in diff["entries"]]
    for entry in top10:
        entry["is_first_observation"] = is_first_observation
    # On a first-observation day there is no real prior chart to have
    # debuted onto -- new_entries (used by Viral/first-screen "신규 진입"
    # framing) must be empty, not every single entry.
    new_entries = [] if is_first_observation else [e for e in top10 if e["is_new"]]
    return {
        "state": "NORMAL", "top10": top10, "new_entries": new_entries,
        "trend": _trend_summary(top10, is_first_observation),
        "is_first_observation": is_first_observation,
    }


def _tiktok_chart_section():
    """No TikTok chart/viral data source exists yet (blocked pending the
    source-access decision) -- always honestly UNAVAILABLE, never
    substituted with Apple Music or fabricated."""
    return {"state": STATE_UNAVAILABLE, "top10": [], "new_entries": [], "trend": None}


def _cross_platform_source_detail(conn, report_date_kst, source_name, music_entity_id):
    """Real per-source metric for one entity already known to have
    positive VELOCITY on this source today (see music.cross_platform's
    own query -- the entity is guaranteed to be in this source's current
    diff entries, since that diff is exactly what produced the VELOCITY
    row). Re-derives rank/previous_rank/region the same way _enrich_
    chart_entry does for Spotify's own TOP10 -- never a second source of
    truth. Returns None (never a fabricated placeholder) in the
    unreachable case the entity isn't found in today's diff."""
    metric_name = ACTIVE_MUSIC_SOURCES[source_name]["metric_name"]
    diff = compute_chart_diff(conn, report_date_kst, source_name, metric_name)
    entry = next((e for e in diff["entries"] if e["music_entity_id"] == music_entity_id), None)
    if entry is None:
        return None
    enriched = _enrich_chart_entry(conn, entry, source_name, diff["observed_at"])
    return {
        "source_name": source_name, "rank": enriched["rank"], "previous_rank": enriched["previous_rank"],
        "rank_delta": enriched["rank_delta"], "is_new": enriched["is_new"], "region": enriched["region"],
        # status (FIRST_OBSERVED/NEW/UP/DOWN/FLAT) -- see music.signal_engine's
        # own docstring on why a Cross-Platform detail must check this instead
        # of is_new alone: without it, a first-observation day would render
        # every cross-platform entity as a misleading "NEW" debut here, the
        # one V2 rendering path this specific fix targets (Spotify's own
        # TOP10/Viral/Daily-Trend sections were already fixed the prior
        # session -- this detail card was the one place still missed).
        "status": enriched["status"],
    }


def _intelligence_section(conn, report_date_kst):
    early_signal = {}
    catalog_revival = {}
    for source_name in ACTIVE_MUSIC_SOURCES:
        early_signal[source_name] = select_early_signal_candidates(conn, report_date_kst, source_name)
        catalog_revival[source_name] = detect_catalog_revival_candidates(conn, report_date_kst, source_name)

    cross_platform = detect_cross_platform_signals(conn, report_date_kst)
    for entry in cross_platform:
        entry["source_details"] = [
            detail for detail in (
                _cross_platform_source_detail(conn, report_date_kst, s, entry["music_entity_id"])
                for s in entry["sources"]
            )
            if detail is not None
        ]

    outlook = {}
    for source_name in ACTIVE_MUSIC_SOURCES:
        readiness = dict(check_forecast_readiness(conn, source_name))
        readiness["progress_ratio"] = min(1.0, readiness["days_of_history"] / readiness["min_required_days"])
        outlook[source_name] = readiness

    return {
        "early_signal": early_signal,
        "catalog_revival": catalog_revival,
        "cross_platform": cross_platform,
        "cross_platform_state": classify_cross_platform_state(conn, report_date_kst, cross_platform),
        "outlook": outlook,
    }


def _safe_parse_producer_intelligence(output_text):
    """Defensive shape check at read time -- report.producer_orchestrator
    is responsible for validating (report.validation.
    validate_producer_insights) BEFORE persisting, so a row reaching this
    table is already trustworthy; this is a second, cheap guard so a
    malformed row degrades to an honest empty state instead of crashing
    the whole dashboard.

    Returns (insights, evidence_by_ref). report.producer_orchestrator
    persists `{"insights": [...], "catalog": [...]}` (see its own
    docstring for why: the catalog's human-readable summaries are what
    let the renderer show what an insight's evidence_refs actually MEAN,
    not just opaque codes like "E1, E3"). evidence_by_ref is {} (never a
    crash) if `catalog` is missing/malformed -- an older or defensive-path
    row still renders its insights, just without resolvable evidence
    text; a ref that isn't in the map falls back to showing the bare ref
    at render time, never a fabricated explanation."""
    try:
        parsed = json.loads(output_text)
    except (ValueError, TypeError):
        return [], {}
    if not isinstance(parsed, dict):
        return [], {}
    insights = parsed.get("insights")
    if not isinstance(insights, list):
        return [], {}
    valid = []
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        if not all(
            k in insight
            for k in ("what_is_moving", "why_it_matters", "what_to_watch", "what_could_i_make_now",
                      "evidence_refs", "confidence")
        ):
            continue
        valid.append(insight)

    evidence_by_ref = {}
    catalog = parsed.get("catalog")
    if isinstance(catalog, list):
        for entry in catalog:
            if isinstance(entry, dict) and "ref" in entry and "summary" in entry:
                evidence_by_ref[entry["ref"]] = entry["summary"]

    return valid, evidence_by_ref


def _producer_intelligence_section(conn, report_date_kst):
    row = conn.execute(
        """SELECT li.output_text FROM llm_interpretations li
           JOIN runs r ON r.id = li.run_id
           WHERE li.category = ? AND r.run_date = ?
           ORDER BY li.id DESC LIMIT 1""",
        (PRODUCER_INTELLIGENCE_CATEGORY, report_date_kst),
    ).fetchone()
    if row is None:
        return {"state": "UNAVAILABLE", "insights": []}
    insights, evidence_by_ref = _safe_parse_producer_intelligence(row["output_text"])
    if not insights:
        return {"state": "UNAVAILABLE", "insights": []}
    resolved = []
    for insight in insights:
        enriched = dict(insight)
        enriched["evidence"] = [
            {"ref": ref, "summary": evidence_by_ref.get(ref, ref)} for ref in insight["evidence_refs"]
        ]
        resolved.append(enriched)
    return {"state": "NORMAL", "insights": resolved}


_MUSIC_TREND_LIST_FIELDS = ("genre_signals", "production_notes", "producer_references", "kpop_ar_notes")
_MUSIC_TREND_ITEM_REQUIRED_FIELDS = ("observed", "interpretation", "evidence_refs", "confidence")


def _safe_parse_music_trend_intelligence(output_text):
    """Same defensive-read contract as _safe_parse_producer_intelligence
    directly above -- report.music_trend_orchestrator is responsible for
    validating (report.validation.validate_music_trend_signals) BEFORE
    persisting, so a row reaching this table is already trustworthy; this
    is a second, cheap guard so a malformed row degrades to an honest
    empty state instead of crashing the whole dashboard. Returns
    (lists_by_field, evidence_by_ref) -- lists_by_field always has all 4
    keys (each an empty list if missing/malformed), never a KeyError for
    a caller that reads any of the 4 categories independently."""
    try:
        parsed = json.loads(output_text)
    except (ValueError, TypeError):
        parsed = None
    lists_by_field = {field: [] for field in _MUSIC_TREND_LIST_FIELDS}
    if isinstance(parsed, dict):
        for field in _MUSIC_TREND_LIST_FIELDS:
            items = parsed.get(field)
            if not isinstance(items, list):
                continue
            valid = [
                item for item in items
                if isinstance(item, dict) and all(k in item for k in _MUSIC_TREND_ITEM_REQUIRED_FIELDS)
            ]
            lists_by_field[field] = valid

    evidence_by_ref = {}
    catalog = parsed.get("catalog") if isinstance(parsed, dict) else None
    if isinstance(catalog, list):
        for entry in catalog:
            if isinstance(entry, dict) and "ref" in entry and "summary" in entry:
                evidence_by_ref[entry["ref"]] = entry["summary"]

    return lists_by_field, evidence_by_ref


def _music_trend_intelligence_section(conn, report_date_kst):
    """Genre Radar / Production Radar / Producer Reference Radar / K-pop-
    A&R relevance -- the MUSIC INTELLIGENCE COMPLETION phase's new real
    capability. Each of the 4 categories is independently honest: a
    category with no real evidence that day is an empty list, never
    padded -- state is only "UNAVAILABLE" when NO row exists at all for
    today (no synthesis has run yet), never when a real run legitimately
    found nothing in one or more categories."""
    row = conn.execute(
        """SELECT li.output_text FROM llm_interpretations li
           JOIN runs r ON r.id = li.run_id
           WHERE li.category = ? AND r.run_date = ?
           ORDER BY li.id DESC LIMIT 1""",
        (MUSIC_TREND_INTELLIGENCE_CATEGORY, report_date_kst),
    ).fetchone()
    empty = {field: [] for field in _MUSIC_TREND_LIST_FIELDS}
    if row is None:
        return {"state": "UNAVAILABLE", **empty}

    lists_by_field, evidence_by_ref = _safe_parse_music_trend_intelligence(row["output_text"])

    def _resolve(items):
        resolved = []
        for item in items:
            enriched = dict(item)
            enriched["evidence"] = [
                {"ref": ref, "summary": evidence_by_ref.get(ref, ref)} for ref in item["evidence_refs"]
            ]
            resolved.append(enriched)
        return resolved

    resolved_by_field = {field: _resolve(lists_by_field[field]) for field in _MUSIC_TREND_LIST_FIELDS}
    if not any(resolved_by_field.values()):
        return {"state": "UNAVAILABLE", **empty}
    return {"state": "NORMAL", **resolved_by_field}


def build_dashboard_data_v2(conn, report_date_kst):
    """Returns the full V2.1 structured dict. Never raises for "nothing
    persisted yet" -- news categories fall back to DEGRADED/empty exactly
    like V1; music-intelligence sections fall back to their own honest
    empty/UNAVAILABLE states, computed fresh from whatever real
    observations exist regardless of whether a news run has happened."""
    run_row_id = find_latest_report_run_id(conn, report_date_kst)

    status_by_category = {}
    selections_by_category = {}
    if run_row_id is not None:
        status_rows = conn.execute(
            "SELECT category, status, items_collected, items_selected FROM run_category_status WHERE run_id = ?",
            (run_row_id,),
        ).fetchall()
        status_by_category = {row["category"]: row for row in status_rows}

        interp_row = conn.execute(
            "SELECT output_text FROM llm_interpretations WHERE run_id = ?", (run_row_id,)
        ).fetchone()
        if interp_row is not None:
            try:
                parsed = json.loads(interp_row["output_text"])
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                selections_by_category = parsed

    news = {
        category: _news_section(conn, status_by_category, selections_by_category, category, report_date_kst)
        for category in NEWS_CATEGORIES
    }

    return {
        "report_date_kst": report_date_kst,
        "news": news,
        "tiktok_chart": _tiktok_chart_section(),
        "spotify_chart": _spotify_chart_section(conn, report_date_kst),
        "intelligence": _intelligence_section(conn, report_date_kst),
        "producer_intelligence": _producer_intelligence_section(conn, report_date_kst),
        "music_trend_intelligence": _music_trend_intelligence_section(conn, report_date_kst),
    }

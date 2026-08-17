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
import re
from datetime import datetime, timedelta, timezone

from music.catalog_revival import detect_catalog_revival_candidates
from music.cross_platform import classify_cross_platform_state, detect_cross_platform_signals
from music.early_signal import MIN_RANK_DELTA, select_early_signal_candidates
from music.forecast_gate import check_forecast_readiness
from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff
from report.candidate_selection import _kst_day_bounds_utc, select_news_candidates
from report.news_intelligence_synthesis import validate_news_intelligence
from report.source_metadata import source_quality_score as _source_quality_score
from report.story_clustering import cluster_candidates
from report.text_quality import is_malformed_synthesis_text
from report.translation import NullTranslationProvider, build_translation_provider, translate_and_cache
from report.web_data import _classify_state
from report_delivery import find_latest_report_run_id

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

NEWS_CATEGORIES = ("AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY")

# MAJOR IA REBUILD (music-primary product phase): Music Industry news must
# read as an edited Korean briefing, not raw English RSS -- so real
# translation is now scoped to every news category, TIKTOK/SPOTIFY
# included. This is deliberately a WIDER set than _NEWS_INTELLIGENCE_
# CATEGORIES below (the separately-run what_happened/why_it_matters/
# what_to_watch synthesis remains AI/ECONOMY/SOCIETY-only -- report.
# news_intelligence_orchestrator was never built/run for Music Industry
# evidence, and this pass does not expand that orchestrator) -- translation
# and news-intelligence eligibility are two REAL, independent concerns that
# used to be (and no longer are) aliased to the same tuple. Proper nouns
# (artist/track/company/platform names) are never altered by translation --
# see report/translation.py's own contract.
_TRANSLATION_ELIGIBLE_CATEGORIES = ("AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY")

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


# SOURCE TRUST GATE (quality-hardening phase): the structural LEAD-
# eligibility floor -- promoted from report.source_metadata.QUALITY_TIER_
# SCORE's existing TIER_1/TIER_2 ranking weight (report.candidate_
# selection's own final_score already uses this as a 25% ranking signal,
# never a hard floor). A single-source item can become LEAD only when its
# own best contributing source clears this score (TIER_1=1.0/TIER_2=0.8;
# TIER_3=0.6/TIER_4=0.4/unknown=0.5 do not) -- a low-trust single-source
# claim must never become the top story merely because its freshness/
# novelty signals are strong. Real corroboration (>=2 independent
# outlets) is an independent, always-sufficient path to LEAD regardless of
# any single source's own tier -- multiple outlets confirming the same
# event is real evidence a claim is established, not a rumor, even if
# none of them individually clears the high-trust floor alone. This never
# removes a reputable (TIER_1/TIER_2) source's own existing eligibility,
# and never demotes a candidate below STANDARD -- it only closes the one
# real path ("strong ranking signals alone") by which a weak,
# uncorroborated single source could reach the most prominent display
# slot.
_LEAD_TRUST_SCORE_FLOOR = 0.8
_LEAD_TRUST_MIN_CORROBORATION = 2


def _is_lead_eligible_by_trust(source_names, source_count):
    if source_count >= _LEAD_TRUST_MIN_CORROBORATION:
        return True
    best_score = max((_source_quality_score(name) for name in source_names), default=0.5)
    return best_score >= _LEAD_TRUST_SCORE_FLOOR


def _tier_for(index, freshness_bucket, lead_eligible_by_trust=True):
    """LEAD is reserved for index 0 AND a freshness bucket of 0 or 1 (<=7
    days old -- report.candidate_selection._freshness_bucket) AND real
    source-trust eligibility (see _is_lead_eligible_by_trust directly
    above) -- a story older than 7 days can never become a LEAD by
    default, since this pipeline has no objective "why is this news again
    today" signal to justify it (see candidate_selection's own
    docstring), and a low-trust uncorroborated single source can never
    become a LEAD merely because other ranking signals are strong. If the
    very top candidate fails either gate, no item gets LEAD that
    category/day -- an honest "no fresh, sufficiently-trusted top story"
    rather than forcing a stale or unsupported one into the lead slot;
    it's still shown, just at STANDARD, never hidden."""
    if index == 0 and freshness_bucket is not None and freshness_bucket <= 1 and lead_eligible_by_trust:
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


def _extract_trustworthy_image_url(extra_json):
    """MUSIC EDITORIAL IMAGERY: the only source of a news item's image is
    the SAME extra_json blob ingestion/adapters/rss.py already populated
    at ingestion time from real feed-provided image metadata (media:
    thumbnail / media:content / an image-typed <enclosure>) -- never
    fetched, scraped, or guessed here or at render time. Returns None for
    anything that isn't a real, well-formed http(s) URL string, which is
    exactly what "no image" looks like downstream -- never a placeholder.

    IMAGE QUALITY GATE (EDITORIAL QUALITY PASS, 2026-08-18, confirmed
    real defect): a Google-News-aggregated feed can carry a feed-provided
    image_url that is itself hosted on Google's own unreliable image
    infrastructure (see report.image_enrichment.is_unreliable_image_url's
    own docstring for the live-verified HTTP 400 this catches) -- a
    well-formed http(s) URL string is necessary but not sufficient; this
    is the SAME real reject rule the article-page-enrichment fallback
    already applies, reused (not duplicated) so both image paths agree."""
    if not extra_json:
        return None
    try:
        extra = json.loads(extra_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(extra, dict):
        return None
    image_url = extra.get("image_url")
    if not isinstance(image_url, str):
        return None
    image_url = image_url.strip()
    if not (image_url.startswith("http://") or image_url.startswith("https://")):
        return None
    from report.image_enrichment import is_unreliable_image_url

    if is_unreliable_image_url(image_url):
        return None
    return image_url


# MULTI-PUBLISHER CLUSTERING GAP / SOURCE PRESENTATION FIX (EDITORIAL
# INTEGRITY PASS, confirmed real defect from actual generated-report QA):
# a real Google-News-aggregator ingestion feed (e.g. `tiktok_music_news_
# google`) is ONE raw source_name representing MANY different real
# underlying publishers -- this made report.story_clustering.
# cluster_candidates's own real "source independence" gate incorrectly
# treat DIFFERENT real outlets' independent coverage of the SAME real
# event as "the same non-independent source" (they all shared that one
# raw source_name), so a real 17-article cluster (Taylor Swift/Trump/
# TikTok) never collapsed. Google News RSS's own real, deterministic
# convention appends " - <RealPublisherName>" to every aggregated
# headline -- extracted here, never fabricated (returns (None, title)
# unchanged whenever the pattern doesn't match, or the source isn't a
# real known aggregator). This also fixes SOURCE PRESENTATION ("Google
# 뉴스" as a visible byline) and a second real defect: the raw publisher
# suffix text was polluting title-similarity comparisons as if it were
# part of the real story content.
_TRAILING_PUBLISHER_RE = re.compile(r"\s[-–]\s([A-Za-z][A-Za-z0-9.&' ]{1,40})$")


def _extract_real_publisher(title, source_name):
    """Real, deterministic, narrow: only ever applies to a known real
    aggregator feed (source_name containing "google" -- a direct RSS feed
    like billboard_rss/rollingstone_music_rss is NEVER touched, so a
    legitimate real headline that happens to end in " - some words" is
    never altered). Returns (real_publisher_or_None, title_with_suffix_
    stripped_or_unchanged)."""
    if not title or "google" not in (source_name or "").lower():
        return None, title
    match = _TRAILING_PUBLISHER_RE.search(title)
    if not match:
        return None, title
    cleaned_title = title[: match.start()].strip()
    if not cleaned_title:
        return None, title
    return match.group(1).strip(), cleaned_title


def _image_enrichment_already_attempted(extra_json):
    """True when a prior call already tried the article-page og:image
    fallback for this item (success OR failure) -- see
    _enrich_image_from_article_page's own docstring for why this must be
    checked before ever attempting a second fetch."""
    if not extra_json:
        return False
    try:
        extra = json.loads(extra_json)
    except (ValueError, TypeError):
        return False
    return isinstance(extra, dict) and bool(extra.get("image_enrichment_attempted"))


def _enrich_image_from_article_page(conn, raw_item_id, source_url, extra_json):
    """ARTICLE-PAGE IMAGE FALLBACK (FIX ONLY: missing article images
    pass, 2026-08-18): only ever reached from _lookup_item_detail below,
    i.e. only for an item that is ACTUALLY selected for the final DAILY/
    MUSIC report -- never for the full raw candidate pool -- and only
    when report.web_data_v2._extract_trustworthy_image_url already found
    no real feed-provided image. A real, bounded (one GET) fetch of the
    article's OWN page via report.image_enrichment.fetch_article_image_
    url (og:image first, twitter:image fallback, never any other
    heuristic, never fabricated). The outcome -- a real image_url, or
    None when the page has no usable meta tag or the fetch itself failed
    -- is cached back into raw_items.extra_json (the SAME field
    _extract_trustworthy_image_url already reads) alongside an
    image_enrichment_attempted flag, so this fetch happens AT MOST ONCE
    per article ever, across every future render, whether it succeeded
    or not. Never raises -- a failure here must never break report
    generation, exactly like a feed that simply never had an image."""
    from report.image_enrichment import fetch_article_image_url

    try:
        image_url = fetch_article_image_url(source_url)
    except Exception:
        image_url = None

    try:
        extra = json.loads(extra_json) if extra_json else {}
        if not isinstance(extra, dict):
            extra = {}
    except (ValueError, TypeError):
        extra = {}
    extra["image_url"] = image_url
    extra["image_enrichment_attempted"] = True
    conn.execute("UPDATE raw_items SET extra_json = ? WHERE id = ?", (json.dumps(extra), raw_item_id))
    conn.commit()
    return image_url


def _lookup_item_detail(conn, normalized_item_id):
    row = conn.execute(
        """SELECT ni.normalized_title AS title, ni.event_key AS event_key,
                  ri.id AS raw_item_id, ri.source_url AS source_url, ri.snippet AS snippet,
                  ri.source_name AS source_name, ri.published_at AS published_at, ri.extra_json AS extra_json
           FROM normalized_items ni
           JOIN raw_items ri ON ri.id = ni.raw_item_id
           WHERE ni.id = ?""",
        (normalized_item_id,),
    ).fetchone()
    if row is None:
        return None
    real_publisher, cleaned_title = _extract_real_publisher(row["title"], row["source_name"])
    image_url = _extract_trustworthy_image_url(row["extra_json"])
    if image_url is None and not _image_enrichment_already_attempted(row["extra_json"]):
        image_url = _enrich_image_from_article_page(
            conn, row["raw_item_id"], row["source_url"], row["extra_json"]
        )
    return {
        "title": cleaned_title, "source_url": row["source_url"], "snippet": row["snippet"],
        "source_name": real_publisher or row["source_name"], "event_key": row["event_key"],
        "published_at": row["published_at"],
        "image_url": image_url,
    }


def _source_names_for_event(conn, event_key, report_date_kst):
    """Re-derives EXACTLY what report/candidate_selection.py computed as
    the real distinct-outlet set at selection time, scoped to this
    event_key within the same KST day window. Never recomputed over all
    history -- an event_key can legitimately recur on a later day for a
    genuinely new development in the same story (see candidate_selection's
    own stale-exclusion docstring), so counting outside the day window
    would overstate corroboration for an old story resurfacing. Feeds both
    the display source_count and the source-trust LEAD-eligibility gate
    (_is_lead_eligible_by_trust) from the SAME real query -- never two
    independently-maintained notions of "how many/how reputable are this
    event's real sources"."""
    start_utc, end_utc = _kst_day_bounds_utc(report_date_kst)
    rows = conn.execute(
        """SELECT DISTINCT ri.source_name
           FROM normalized_items ni JOIN raw_items ri ON ri.id = ni.raw_item_id
           WHERE ni.event_key = ? AND ri.collected_at >= ? AND ri.collected_at < ?""",
        (event_key, start_utc, end_utc),
    ).fetchall()
    return {row["source_name"] for row in rows}


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


def _prepare_for_clustering(candidate):
    """Real, additive-only pre-processing for report.story_clustering.
    cluster_candidates -- see _extract_real_publisher/_cluster_
    suppression's own docstrings. A shallow copy with `normalized_title`/
    `source_names` corrected to the real extracted publisher/cleaned
    title ONLY when a real Google-News-aggregator match exists; every
    other real candidate (the vast majority -- direct RSS feeds) is
    returned completely unchanged, so this never alters clustering
    behavior for anything this fix doesn't target."""
    source_names = candidate.get("source_names") or []
    source_name = source_names[0] if len(source_names) == 1 else None
    real_publisher, cleaned_title = _extract_real_publisher(candidate.get("normalized_title"), source_name)
    if real_publisher is None:
        return candidate
    prepared = dict(candidate)
    prepared["normalized_title"] = cleaned_title
    prepared["source_names"] = [real_publisher]
    return prepared


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
    never as a second independent-looking top story.

    MULTI-PUBLISHER CLUSTERING GAP FIX: `cluster_candidates` itself is
    untouched (never rebuilt) -- only its REAL input data quality is
    corrected here first, via `_prepare_for_clustering` (see
    _extract_real_publisher), so a real Google-News-aggregator feed's
    single raw source_name no longer masquerades as "the same non-
    independent source" for every one of the many different real outlets
    it actually aggregates."""
    prepared = [_prepare_for_clustering(c) for c in candidates]
    clusters = cluster_candidates(prepared)
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


def _suppress_duplicate_selections(items, item_event_keys, clusters):
    """DUPLICATE GATE, PRIMARY (LLM-selected) PATH: the deterministic
    near-duplicate defense (report.story_clustering, already applied to
    the no-LLM fallback path via _cluster_suppression above) must never
    apply ONLY to the fallback -- an LLM's own editorial judgment must
    never be the sole duplicate-defense layer. Unlike _cluster_suppression
    (which filters a whole day's CANDIDATE POOL), this only ever suppresses
    a SELECTED item when ANOTHER SELECTED item in this SAME result set is
    a real near-duplicate of it (both are members of the same
    story_clustering cluster) -- never because some unselected candidate
    elsewhere in the day's pool happens to be similar. This is what keeps
    "genuinely different developments" from being reduced: a cluster whose
    second member was never selected by the LLM in the first place is
    left completely untouched here.

    `items`/`item_event_keys` are parallel lists (same order, same
    length -- item_event_keys[i] is items[i]'s own real event_key).
    Keeps the cluster's representative event_key when the LLM itself
    selected it; otherwise keeps whichever selected member appears
    EARLIEST in the LLM's own selection order (index order is itself the
    LLM's own relevance signal -- see _tier_for). A real, non-representative
    duplicate that's suppressed carries its cluster's own honest
    (related_article_count, related_source_count) forward onto the kept
    item, mirroring the fallback path's own "N개 매체 관련 보도" evidence
    contract -- never a fabricated number, and the suppressed article's
    real coverage is still visible via the unchanged `clusters` evidence
    block, never silently lost."""
    if not clusters:
        return items
    event_key_to_index = {}
    for i, event_key in enumerate(item_event_keys):
        event_key_to_index.setdefault(event_key, i)

    suppress_indices = set()
    kept_related_counts = {}
    for cluster in clusters:
        member_keys = cluster["member_event_keys"]
        selected_indices = sorted({event_key_to_index[k] for k in member_keys if k in event_key_to_index})
        if len(selected_indices) < 2:
            continue  # need >=2 SELECTED members actually present to suppress anything
        representative_key = cluster["representative_event_key"]
        keep_index = event_key_to_index.get(representative_key)
        if keep_index is None or keep_index not in selected_indices:
            keep_index = selected_indices[0]
        kept_related_counts[keep_index] = (cluster["related_article_count"], cluster["distinct_source_count"])
        for idx in selected_indices:
            if idx != keep_index:
                suppress_indices.add(idx)

    if not suppress_indices:
        return items
    kept = []
    for i, item in enumerate(items):
        if i in suppress_indices:
            continue
        related = kept_related_counts.get(i)
        if related:
            item = dict(item)
            item["related_article_count"], item["related_source_count"] = related
        kept.append(item)
    return kept


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
        detail["title"] = _fix_known_truncated_publisher_suffix(detail["title"])
        detail["snippet"] = _fix_known_truncated_publisher_suffix(detail["snippet"])
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
            "image_url": detail["image_url"],
            "event_key": candidate["event_key"],
            "source_count": candidate["source_count"],
            "tier": _tier_for(
                index, candidate.get("freshness_bucket"),
                _is_lead_eligible_by_trust(candidate.get("source_names") or [], candidate["source_count"]),
            ),
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
# Industry's own evidence, already cited elsewhere, and that orchestrator
# was never built/run for them). Deliberately its OWN tuple now (MAJOR IA
# REBUILD phase) -- no longer aliased to _TRANSLATION_ELIGIBLE_CATEGORIES,
# which now also covers TIKTOK/SPOTIFY for Korean-headline purposes only.
_NEWS_INTELLIGENCE_CATEGORIES = ("AI", "ECONOMY", "SOCIETY")


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
    item_event_keys = []
    for index, selection in enumerate(selections_by_category.get(category) or []):
        if not isinstance(selection, dict) or "id" not in selection:
            continue
        detail = _lookup_item_detail(conn, selection["id"])
        if detail is None:
            continue
        detail["title"] = _fix_known_truncated_publisher_suffix(detail["title"])
        detail["snippet"] = _fix_known_truncated_publisher_suffix(detail["snippet"])
        reason = selection.get("reason")
        # Dropped if it repeats EITHER the headline or the reason -- a
        # snippet that just restates the title (not merely the LLM's
        # reason) is exactly the duplication bug this guards against.
        snippet = detail["snippet"]
        if _is_redundant(snippet, reason) or _is_redundant(snippet, detail["title"]):
            snippet = None
        source_names = _source_names_for_event(conn, detail["event_key"], report_date_kst)
        source_count = len(source_names) or 1
        item = {
            "id": selection["id"],
            "title": detail["title"],
            "reason": reason,
            "snippet": snippet,
            "source_url": detail["source_url"],
            "source_name": detail["source_name"],
            "published_at": detail["published_at"],
            "image_url": detail["image_url"],
            "event_key": detail["event_key"],
            "source_count": source_count,
            "tier": _tier_for(
                index, _freshness_bucket_from_published_at(detail["published_at"], report_date_kst),
                _is_lead_eligible_by_trust(source_names, source_count),
            ),
        }
        items.append(_attach_translation(conn, provider, item))
        item_event_keys.append(detail["event_key"])
    clusters = _story_clusters_for_category(conn, category, report_date_kst)
    if items:
        # DUPLICATE GATE: never rely on the LLM's own judgment alone (see
        # _suppress_duplicate_selections' own docstring) -- applied BEFORE
        # the news-intelligence attach so a suppressed duplicate's id never
        # gets its own additive intelligence layer computed for nothing.
        items = _suppress_duplicate_selections(items, item_event_keys, clusters)
        if category in _NEWS_INTELLIGENCE_CATEGORIES:
            items = _attach_news_intelligence(conn, report_date_kst, items)
        if category in ("ECONOMY", "SOCIETY"):
            items = rank_economy_society_items(items)
        return {"state": state, "items": items, "clusters": clusters}

    fallback_items = _raw_fallback_items(conn, category, report_date_kst)
    if fallback_items:
        if category in _NEWS_INTELLIGENCE_CATEGORIES:
            fallback_items = _attach_news_intelligence(conn, report_date_kst, fallback_items)
        if category in ("ECONOMY", "SOCIETY"):
            fallback_items = rank_economy_society_items(fallback_items)
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
        return {
            "state": STATE_UNAVAILABLE, "top10": [], "new_entries": [], "trend": None,
            "is_first_observation": False, "chart_date": None,
        }
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
        # REAL CHART DATE CONTRACT: the actual source snapshot instant
        # (_latest_snapshot_on_or_before's real observed_at, already used
        # to enrich every top10 entry above) -- NOT report_date_kst, which
        # is only an upper bound the query is allowed to look back from.
        # Collector lag means these two dates genuinely differ (e.g. a
        # chart observed 2026-08-15 shown in a 2026-08-16 report); this
        # field must never be silently backfilled from report_date_kst.
        "chart_date": diff["observed_at"],
    }


# A NEW entry debuting this high on a 10-slot chart is a structural fact
# (its rank position, already real) worth calling out as a notable debut --
# NOT an invented judgment about the song. Chosen because CHART_LIMIT is
# 10 (music/spotify_chart.py): top-3 is the top third of the chart. Moved
# here (MAJOR IA REBUILD phase) from report/web_render_v2.py -- selecting
# WHICH real chart facts qualify as "viral" is a real ranking/selection
# decision, not a layout one, and both Chart Pulse and MUSIC TODAY now
# need the same real selection.
VIRAL_NEW_NOTABLE_RANK = 3


def select_viral_hot(top10):
    """Qualification is the SAME real threshold music.early_signal already
    uses to define an acceleration signal (MIN_RANK_DELTA) -- not an
    ad-hoc cutoff. A track that merely moved up 1 spot is a real fact
    already shown as a plain riser; it does not also qualify as "viral
    hot" just because it's positive."""
    movers = [e for e in top10 if e.get("status") == "UP" and (e.get("rank_delta") or 0) >= MIN_RANK_DELTA]
    return sorted(movers, key=lambda e: -e["rank_delta"])


def select_viral_new(new_entries):
    """A debut alone is already a real, already-shown fact -- this only
    surfaces a debut that adds a genuinely distinct fact: entering
    unusually high (top VIRAL_NEW_NOTABLE_RANK of a 10-slot chart), a
    real, already-known structural fact, not an invented interpretation."""
    return [e for e in new_entries if e["rank"] <= VIRAL_NEW_NOTABLE_RANK]


# PROFESSIONAL EDITORIAL QUALITY PASS: Music Industry must rank by real
# USER (songwriter/producer) IMPACT, not celebrity-name popularity -- a
# real, deterministic priority-class keyword ranking, never a fabricated
# score. Lower number = higher priority. Checked against every real text
# field an item carries (original + Korean-translated title/snippet, so
# classification works regardless of translation outcome) -- an item
# matching multiple classes takes its single best (lowest-numbered)
# match; an item matching none of the 8 real priority classes keeps its
# existing relative order but sinks below every classified item, and one
# that also matches a real down-rank signal (lifestyle/promo/trivia/
# gossip) sinks lowest of all. This never removes a real story -- it only
# re-orders the same real, already-collected set before the existing
# display cap applies.
_MUSIC_INDUSTRY_PRIORITY_KEYWORDS = (
    # MUSIC EDITORIAL RANKING UPGRADE: priority 1 is "rights / copyright /
    # publishing / royalties / licensing" as ONE combined class (SUPER_
    # NEWS_SPEC.md section 8) -- licensing previously sat in its own
    # class alongside unrelated DSP-platform-policy keywords, which
    # incorrectly ranked a pure rights/licensing story one class below
    # copyright/publishing/royalty stories that are equally "highest
    # editorial value."
    (1, ("저작권", "판권", "퍼블리싱", "copyright", "publishing deal", "publishing", "royalt", "로열티", "rights holder",
         "저작권료", "퍼블리셔", "publisher", "라이선스", "license", "licensing")),
    (2, ("플랫폼 정책", "platform policy", "streaming policy", "정책 변경", "약관 변경",
         "이용약관", "계약 체결", "제휴", "파트너십", "inks agreement", "signs deal", "partnership", "connected app",
         "통합 출시", "platform integration", "연동 앱")),
    (3, ("ai 음악", "ai music", "생성형 ai", "generative ai", "creator tool", "크리에이터 도구", "ai 도구", "ai 툴",
         "ai-generated", "ai-powered", "ai 기반", "생성형 음악", "generative music", "스튜디오 2.0", "studio 2.0",
         "챗 바", "chat bar", "미디 지원", "midi support")),
    (4, ("레이블", "record label", " label ", "배급", "distribution deal", "a&r", "signing", "발굴 프로그램", "artist discovery")),
    (5, ("매출", "revenue", "market share", "시장 점유율", "streaming economics", "스트리밍 경제", "수익", "재무")),
    # priority 6/7 ORDER (confirmed real defect vs SUPER_NEWS_SPEC.md
    # section 8): consumption/chart/audience-behavior shifts rank ABOVE
    # touring/ticketing/live-business economics -- these two classes were
    # previously swapped.
    (6, ("차트", "chart", "consumption", "소비 행태", "streams", "스트리밍 수", "재생 수")),
    (7, ("투어", "tour", "concert", "콘서트", "아레나", "arena", "stadium", "스타디움", "티켓", "ticket")),
    (8, ("발매 전략", "release strategy", "신보", "정규 앨범", "album release", "single release", "싱글 발매")),
)
_MUSIC_INDUSTRY_DOWNRANK_KEYWORDS = (
    "라이프스타일", "lifestyle", "영화 예고편", "movie trailer", "영화 홍보", "film promotion",
    "가십", "gossip", "루머", "rumor", "cameo", "카메오", "레드카펫", "red carpet",
    # PROFESSIONAL EDITORIAL QUALITY PASS (confirmed real defect): a
    # celebrity personal-health/legal/relationship story that happens to
    # mention an unrelated priority-class word in passing (e.g. "투어" in
    # "reveals heart condition, one tour date remains") must never be
    # promoted by that incidental word -- these are checked BEFORE the 8
    # priority classes below, not after, so a real personal-life/health
    # story is downranked regardless of what else it happens to mention.
    "심장", "질환", "질병", "투병", "건강 이상", "health condition", "diagnosed with",
    "hospitalized", "병원 이송", "사망", "별세", "passed away", "dies at",
    "이혼", "divorce", "결별", "breakup", "열애", "dating rumor", "임신", "pregnant",
    "체포", "arrested", "구속", "기소", "indicted", "법정 공방", "lawsuit against",
    # MUSIC INDUSTRY AGGRESSIVE NOISE CUT (PREMIUM INTELLIGENCE UPGRADE
    # PASS, confirmed real defect from actual generated-report QA): estate
    # disputes and minor-crime stories about a musician (not a real
    # rights/business matter) read as tabloid filler in a premium
    # publication -- real examples seen: an estate-arbitration story, a
    # murder-for-hire trial. Still exempt from this bucket whenever the
    # LEGAL/RIGHTS EXCEPTION above already classified the story as real
    # rights/copyright/publishing/royalty/licensing business.
    "유산 분쟁", "estate dispute", "상속 분쟁", "유산 재단", "estate foundation",
    "살인", "murder", "인신매매", "trafficking", "폭행", "assault", "성범죄", "sexual assault",
    # MASTER PRODUCT COMPLETION PASS (confirmed real defect, real generated
    # music.html QA): a pure event-recap/fan-highlight-reel story (real
    # example seen: a "best moments from [festival]" wrap-up) carries no
    # rights/business/platform/production signal a composer could act on --
    # never confused with a real tour/booking ECONOMICS story (e.g. a real
    # revenue/pricing/route report), which stays eligible for its own real
    # priority class above.
    "최고의 순간", "베스트 모먼트", "best moments from", "하이라이트 모음", "명장면 모음",
    # FINAL 90+ QUALITY CORRECTION PASS (confirmed real defect): an
    # artist's music being used/removed on a POLITICAL FIGURE's social
    # media is a celebrity-politics conflict story, not a real music-
    # business/rights consequence, UNLESS it actually uses real
    # rights/licensing language (in which case the LEGAL/RIGHTS EXCEPTION
    # above already exempts it before this check ever runs) -- general
    # political-conflict terms, never one artist's name, so this applies
    # to any celebrity-vs-politician story, not just one real example.
    "백악관", "white house", "대통령 후보", "presidential campaign", "정치 캠페인", "political campaign",
    "선거 캠페인", "election campaign", "정당 지지", "trump", "트럼프",
    # COMPOSER/PRODUCER EDITORIAL PRIORITY PASS (2026-08-17): a pure
    # idol-gossip controversy (no rights/business/production signal a
    # composer could act on) is noise for this reader, same as
    # 가십/열애 above -- still exempt whenever the LEGAL/RIGHTS EXCEPTION
    # already classified the story as real rights/copyright/publishing/
    # royalty/licensing business (e.g. a real "저작권 논란" lawsuit),
    # since that check runs first.
    "논란", "controversy",
    # SUPER NEWS FINAL ROLLBACK-RESTORE PASS (2026-08-17): a fan/pundit
    # social-media comment spat (deleted tweet/comment, fan-vs-artist or
    # fan-vs-fan feud) carries no songwriting/production/A&R/business
    # signal -- real example seen: an artist's deleted TikTok-comment reply
    # to a fan. Still exempt whenever the LEGAL/RIGHTS EXCEPTION above
    # already classified the story as real rights/copyright/publishing/
    # royalty/licensing business, since that check runs first.
    "삭제된 댓글", "deleted comment", "deleted tiktok comment", "deleted tweet",
    "팬덤 갈등", "fan feud", "trolled", "claps back at",
    # EMERGENCY MUSIC QUALITY RECOVERY PASS (2026-08-18, confirmed real
    # defect): a concert-cameo/surprise-guest-appearance story ("X brings
    # out Y", "watch X join Y to perform", "watch X introduce Y on
    # stage") is fan-facing concert coverage, not a real songwriting/
    # production/A&R/business signal a composer could act on -- real
    # examples seen the same day genuinely stronger P1-P6 stories (an AI-
    # album Pitchfork-review backlash, a real Billboard chart record)
    # existed but were outranked by this class of story. Still exempt
    # whenever the LEGAL/RIGHTS EXCEPTION above already classified the
    # story as real rights/copyright/publishing/royalty/licensing
    # business, since that check runs first.
    "brings out", "surprise live debut", "surprise appearance", "surprise guest",
    "깜짝 라이브", "깜짝 등장", "무대에 함께 등장",
    # FINAL MUSIC RECOVERY PASS (2026-08-18, confirmed real defect): a
    # pure corporate-social-responsibility/charity-donation story (a
    # label/company donating to disaster relief) is legitimate news but
    # carries no songwriting/production/A&R/repertoire/rights/platform-
    # strategy signal. Still exempt whenever the LEGAL/RIGHTS EXCEPTION
    # above already classified the story as real rights/copyright/
    # publishing/royalty/licensing business, since that check runs first.
    "기부", "donation", "구호 성금", "재해 복구 성금",
)
_MUSIC_INDUSTRY_UNRANKED_PRIORITY = 9
_MUSIC_INDUSTRY_DOWNRANKED_PRIORITY = 10


def music_industry_priority_rank(item):
    """Returns an int priority (lower = shown first). Real text only --
    every original/translated title/snippet field the item actually
    carries, never a fabricated summary. Check order: priority-1 rights/
    copyright/publishing/royalty/licensing keywords FIRST (the LEGAL/
    RIGHTS EXCEPTION below), then the down-rank signals, then priority
    classes 2-8. A real celebrity personal-life/health story must never
    be promoted just because it incidentally mentions an unrelated
    priority-class word -- that's still true for classes 2-8, checked
    after the down-rank gate; only class 1 is exempt from that gate, and
    only because SUPER_NEWS_SPEC.md section 8 explicitly requires real
    rights/business litigation to never be down-ranked for merely
    containing legal language."""
    texts = [item.get(k) for k in ("title", "ko_title", "snippet", "ko_snippet")]
    combined = " ".join(t for t in texts if t).lower()
    if not combined:
        return _MUSIC_INDUSTRY_UNRANKED_PRIORITY
    # LEGAL/RIGHTS EXCEPTION (MUSIC EDITORIAL RANKING UPGRADE, confirmed
    # real defect vs SUPER_NEWS_SPEC.md section 8's explicit "do NOT
    # down-rank legal stories merely because they contain court/lawsuit/
    # legal language" rule): a real rights/copyright/publishing/royalty/
    # licensing story checked here FIRST, before the generic downrank
    # keywords -- a real copyright/licensing lawsuit ("법정 공방", "lawsuit
    # against") is business litigation, the highest-value editorial class,
    # not a personal-life/gossip story that merely mentions legal words.
    rights_priority, rights_keywords = _MUSIC_INDUSTRY_PRIORITY_KEYWORDS[0]
    if any(keyword in combined for keyword in rights_keywords):
        return rights_priority
    if any(keyword in combined for keyword in _MUSIC_INDUSTRY_DOWNRANK_KEYWORDS):
        return _MUSIC_INDUSTRY_DOWNRANKED_PRIORITY
    for priority, keywords in _MUSIC_INDUSTRY_PRIORITY_KEYWORDS[1:]:
        if any(keyword in combined for keyword in keywords):
            return priority
    return _MUSIC_INDUSTRY_UNRANKED_PRIORITY


def rank_music_industry_items(items):
    """Stable sort by real priority class only -- items within the same
    class (including two unranked/down-ranked items) keep their existing
    real relative order (freshness/tier/source-diversity, already decided
    upstream), never re-scored a second time."""
    return sorted(items, key=music_industry_priority_rank)


# PROFESSIONAL EVIDENCE SELECTION RECOVERY (2026-08-18, confirmed real
# defect via a read-only pipeline trace): report.ai_synthesis's
# NEWS_COMBINED call is ONE generic "select the most important stories"
# LLM prompt shared across AI/ECONOMY/SOCIETY/TIKTOK/SPOTIFY, capped at
# MAX_SELECTIONS_PER_CATEGORY per category, with zero music-professional-
# value awareness. Confirmed real example: a real Beatport AI-generated-
# song-ban story (a genuine rights/AI-music-class story, priority class 1
# under this module's own music_industry_priority_rank) was present in
# the SAME real deterministic candidate pool report.candidate_selection.
# select_news_candidates already computes (ranked #14 of 22 real SPOTIFY-
# pooled candidates that day) but the LLM picked 5 more artist-prominent
# stories instead -- never selected, so never translated, so never seen
# by ANY downstream Music section (Industry/Music Today/Producer
# Intelligence all read from this same narrow selected set). This is a
# GENERAL class of problem (any real professional-class story can lose
# this way on any day), not specific to Beatport -- backfilled by real
# priority class only, never by title.
#
# MUSIC-page-ONLY, never touches DAILY (see callers: only
# report.web_render_v2.render_music_page_html_v2 invokes this; DAILY's
# render_dashboard_html_v2 is untouched and never calls it). Never a new
# LLM call, never a new ingestion source, never new architecture: reuses
# the SAME real deterministic candidate pool, the SAME real priority-
# class ranking, and the SAME real report.translation pathway every
# other displayed item already goes through.
_PROFESSIONAL_BACKFILL_MAX_PRIORITY = 4
_PROFESSIONAL_BACKFILL_LIMIT = 2


def professional_evidence_backfill(conn, report_date_kst, existing_items, category):
    """Returns a short list of additional, real, translated items for
    `category` that NEWS_COMBINED did not select but that carry genuine
    professional-business/rights/platform/AI-music value (real priority
    class 1-4 -- see music_industry_priority_rank; classes 5-8/unranked/
    downranked are ordinary or low-value and never backfilled). Empty
    list on a day with nothing qualifying -- never pads with a generic
    item to fill a quota, and never re-adds an id already present in
    `existing_items`."""
    existing_ids = {item.get("id") for item in existing_items}
    candidates = select_news_candidates(conn, [category], report_date_kst)[category]
    scored = []
    for candidate in candidates:
        if candidate["id"] in existing_ids:
            continue
        detail = _lookup_item_detail(conn, candidate["id"])
        if detail is None:
            continue
        priority = music_industry_priority_rank({"title": detail["title"], "snippet": detail["snippet"]})
        if priority > _PROFESSIONAL_BACKFILL_MAX_PRIORITY:
            continue
        scored.append((priority, candidate, detail))
    scored.sort(key=lambda row: (row[0], -row[1]["source_count"], row[1]["event_key"]))
    scored = scored[:_PROFESSIONAL_BACKFILL_LIMIT]
    if not scored:
        return []

    provider = build_translation_provider()
    backfilled = []
    for index, (priority, candidate, detail) in enumerate(scored):
        title = _fix_known_truncated_publisher_suffix(detail["title"])
        snippet = _fix_known_truncated_publisher_suffix(detail["snippet"])
        if _is_redundant(snippet, title):
            snippet = None
        item = {
            "id": candidate["id"],
            "title": title,
            "reason": None,
            "snippet": snippet,
            "source_url": detail["source_url"],
            "source_name": detail["source_name"],
            "published_at": detail["published_at"],
            "image_url": detail["image_url"],
            "event_key": candidate["event_key"],
            "source_count": candidate["source_count"],
            "tier": _tier_for(
                index, candidate.get("freshness_bucket"),
                _is_lead_eligible_by_trust(candidate.get("source_names") or [], candidate["source_count"]),
            ),
        }
        backfilled.append(_attach_translation(conn, provider, item))
    return backfilled


# PRODUCTION RADAR / PRODUCER-A&R ROUTING FIX (2026-08-18, confirmed real
# defect via a read-only trace): report.music_trend_orchestrator.
# run_daily_music_trend_intelligence and report.producer_orchestrator.
# run_daily_producer_intelligence both build their own `industry_news`
# list EXCLUSIVELY from dashboard_data["news"]["TIKTOK"/"SPOTIFY"]["items"]
# -- the already-persisted NEWS_COMBINED selection -- never from
# professional_evidence_backfill's own pool (rights/platform-policy/
# AI-music/label-A&R, already deterministic, already excludes anything
# NEWS_COMBINED selected) and never from any craft-class evidence at all,
# since music_industry_priority_rank's 8 classes are entirely business/
# rights-oriented -- there has never been a craft (songwriting/production/
# arrangement/recording/mixing/mastering/sound-design) class. So even a
# genuinely craft-relevant article (confirmed real example: Attack
# Magazine production/sound-design tutorials) could never reach Production
# Radar's evidence catalog, no matter how strong the underlying source
# supply became.
#
# Real gossip/lifestyle keywords a craft term might incidentally appear
# inside (see _MUSIC_INDUSTRY_DOWNRANK_KEYWORDS) are excluded here too --
# the SAME real quality gate _merge_music_industry_items already applies,
# never a second, weaker one.
_CRAFT_EVIDENCE_KEYWORDS = (
    "songwrit", "co-writ", "co writ", "composition", "composer", "arrange", "orchestrat",
    "produced by", "music producer", "producer,", "producer.", "beatmaker", "beat maker",
    "production credit", "recording session", "mixed by", "mixing", "mastered by",
    "mastering", "sound design", "instrumentation", "studio session", "sound engineer",
    "mix engineer", "master engineer", "engineered by",
)
_CRAFT_EVIDENCE_LIMIT = 3


def craft_evidence_candidates(conn, report_date_kst, exclude_ids):
    """Deterministic CRAFT-class filter (see module comment above) over
    the SAME real candidate pool report.candidate_selection.
    select_news_candidates already computes for SPOTIFY+TIKTOK (which
    already pools MUSIC_INDUSTRY_NEWS -- see candidate_selection's own
    CATEGORY_SOURCES) -- never a new source, never an LLM call at this
    stage; only a small already-filtered set is ever handed to synthesis.
    `exclude_ids` are real candidate ids already reaching Production/
    Producer via another path (NEWS_COMBINED selection, professional_
    evidence_backfill) -- never re-added here as a duplicate. Returns
    plain {"title","snippet","event_key"} dicts, the exact shape report.
    music_trend_synthesis.build_evidence_catalog / report.
    producer_synthesis.build_evidence_catalog already expect from an
    industry_news entry. Empty list on a day with no real craft evidence
    -- never padded."""
    candidates = select_news_candidates(conn, ["SPOTIFY", "TIKTOK"], report_date_kst)
    pool = candidates["SPOTIFY"] + candidates["TIKTOK"]
    matched = []
    for candidate in pool:
        if candidate["id"] in exclude_ids:
            continue
        detail = _lookup_item_detail(conn, candidate["id"])
        if detail is None:
            continue
        priority = music_industry_priority_rank({"title": detail["title"], "snippet": detail["snippet"]})
        if priority == _MUSIC_INDUSTRY_DOWNRANKED_PRIORITY:
            continue
        combined = f"{detail['title']} {detail.get('snippet') or ''}".lower()
        if not any(keyword in combined for keyword in _CRAFT_EVIDENCE_KEYWORDS):
            continue
        matched.append((candidate, detail))
    matched.sort(key=lambda pair: (-pair[0]["source_count"], pair[0]["event_key"]))
    return [
        {"title": detail["title"], "snippet": detail["snippet"], "event_key": candidate["event_key"]}
        for candidate, detail in matched[:_CRAFT_EVIDENCE_LIMIT]
    ]


def synthesis_extra_industry_news(dashboard_data, conn, report_date_kst):
    """Additional real industry_news-shaped items for report.
    music_trend_orchestrator/report.producer_orchestrator to fold into
    their own industry_news list, so Production Radar and Producer/A&R
    can draw on evidence beyond NEWS_COMBINED's own narrow selection --
    the SAME already-computed professional_evidence_backfill pool
    (rights/platform-policy/AI-music/label-A&R -- directly relevant to
    Producer/A&R's repertoire/rights/label-strategy ownership) PLUS
    craft_evidence_candidates' new craft-class pool (directly relevant to
    Production Radar's songwriting/production/arrangement/recording/
    mixing/mastering ownership). Both deterministic, both already
    excluding anything NEWS_COMBINED already selected or each other --
    never a duplicate entry, never a new LLM call at this stage."""
    existing_ids = {
        item.get("id") for item in
        dashboard_data["news"]["TIKTOK"]["items"] + dashboard_data["news"]["SPOTIFY"]["items"]
    }
    professional_backfill = dashboard_data.get("music_professional_backfill") or {}
    extra = list(professional_backfill.get("SPOTIFY") or []) + list(professional_backfill.get("TIKTOK") or [])
    extra_ids = existing_ids | {item.get("id") for item in extra if item.get("id") is not None}
    extra += craft_evidence_candidates(conn, report_date_kst, extra_ids)
    return extra


def _find_producer_insight_for_title(producer_intelligence, title):
    """LEAD/SPOTIFY WATCH INTELLIGENCE GAP (PREMIUM INTELLIGENCE UPGRADE
    PASS, confirmed real defect: a no-LLM-fallback INDUSTRY_NEWS item --
    the common real case, since this dev DB's `reports` marker table is
    empty -- has no real `reason`, so a lead/watch item rendered as
    headline+summary+link only). Finds a REAL, already-computed Producer
    Intelligence insight citing this SAME real article as evidence (same
    real title-match mechanic report.web_data_v2._collect_music_signal_
    candidates's own `_evidence_refs_for_title` already established) --
    never a new LLM call, never invented text."""
    if not title:
        return None
    for insight in (producer_intelligence or {}).get("insights") or []:
        for ev in insight.get("evidence", []):
            summary = ev.get("summary", "")
            if summary == title or summary.startswith(title + " — "):
                return insight
    return None


def resolve_producer_enrichment(item, producer_intelligence):
    """Shared by the Lead's own INDUSTRY_NEWS candidate and SPOTIFY WATCH
    (see report.web_render_v2._render_spotify_watch_section): returns
    (why_it_matters, producer_implication, extra_evidence_refs) -- real
    `item.get("reason")` (an actual LLM selection reason) wins when
    present; otherwise a matching real Producer Intelligence insight's own
    `why_it_matters` is reused. PRODUCER/A&R INFERENCE-DISTANCE CONTROL: a
    LOW-confidence real insight never becomes a prescriptive TRY/ACTION --
    only its own real `what_to_watch`, same rule the Producer/A&R section
    itself applies. `extra_evidence_refs` lets the caller fold the
    matching insight's own real evidence into its own MUSIC EVENT
    EXPOSURE BUDGET identity, so that insight is correctly suppressed from
    ALSO independently re-appearing as its own separate Producer/A&R
    card."""
    why_it_matters = item.get("reason")
    matching_insight = _find_producer_insight_for_title(producer_intelligence, item.get("title"))
    if not matching_insight:
        return why_it_matters, None, set()
    if not why_it_matters:
        why_it_matters = matching_insight.get("why_it_matters")
    if matching_insight.get("confidence") == "LOW":
        producer_implication = matching_insight.get("what_to_watch")
    else:
        producer_implication = matching_insight.get("what_could_i_make_now") or matching_insight.get("what_to_watch")
    extra_refs = {ev["ref"] for ev in matching_insight.get("evidence", [])}
    return why_it_matters, producer_implication, extra_refs


# SPOTIFY WATCH (PREMIUM INTELLIGENCE UPGRADE PASS): Spotify is a
# permanent required watch layer, not a fixed quota or a keyword count --
# a real, deterministic filter over the SAME already-collected SPOTIFY/
# TIKTOK news pool Music Industry already uses, narrowed to items
# genuinely ABOUT Spotify (a real title/snippet mention) rather than
# every item merely pooled under the "SPOTIFY" display category (which
# also legitimately includes general trade-press MUSIC_INDUSTRY_NEWS with
# no real Spotify connection at all -- see report.candidate_selection's
# own CATEGORY_SOURCES docstring).
def _is_spotify_specific(item):
    texts = [item.get(k) for k in ("title", "ko_title", "snippet", "ko_snippet")]
    combined = " ".join(t for t in texts if t).lower()
    return "spotify" in combined


def spotify_watch_candidates(data):
    """Real Spotify-specific items, ranked by the SAME real
    music_industry_priority_rank editorial scale (licensing/royalties/
    publishing/policy/AI-rights rank highest; ordinary promotion ranks
    low/unranked) -- the renderer (report.web_render_v2.
    _render_spotify_watch_section) picks the first one not already shown
    as today's Lead Story, and only when its real priority is one of the
    8 real classified classes (never a promotional/unranked item), so a
    day with nothing genuinely important honestly shows the restrained
    empty state instead of filler."""
    spotify_items = [
        item for item in data["news"]["SPOTIFY"]["items"] + data["news"]["TIKTOK"]["items"]
        if _is_spotify_specific(item)
    ]
    return rank_music_industry_items(spotify_items)


# PROFESSIONAL EDITORIAL QUALITY PASS: ECONOMY/SOCIETY already have a hard
# ≤5-primary, no-archive cap (see report.web_render_v2's own
# _ECON_SOCIETY_PRIMARY_CAP) -- but which 5 of the category's real
# selected items land in those slots was still purely V1's own selection
# order (real, but not re-checked for real-world importance at this V2
# read layer). A minor local/promotional/recruitment/routine-
# administrative story landing at position 1-5 crowds out a genuinely
# important one at position 6+ that then never gets shown at all (no
# archive to fall back into). This is a deliberately ONE-DIRECTIONAL
# down-rank, not a positive priority-class system like Music Industry's
# own (ECONOMY/SOCIETY have no equivalent notion of "8 priority
# classes") -- a real down-ranked item still keeps its own real relative
# order among other down-ranked items, and every non-flagged item keeps
# its existing real V1 order untouched (stable sort).
_ECONOMY_SOCIETY_DOWNRANK_KEYWORDS = (
    "채용공고", "채용 공고", "구인공고", "구인 공고", "인턴 모집", "모집 공고",
    "공개채용", "신입 채용", "경력 채용", "정기 채용", "채용 시작", "인턴 채용", "채용 돌입",
    "job fair", "hiring event", "recruitment notice", "now hiring",
    "공모전", "이벤트 안내", "행사 안내", "축제 안내", "체험단 모집",
    "협찬", "광고", "sponsored content", "advertisement", "advertorial",
    "부고", "동정", "인사말", "축사", "지역 행사 소식", "administrative notice",
)


def _is_minor_administrative_story(item):
    texts = [item.get(k) for k in ("title", "ko_title", "snippet", "ko_snippet")]
    combined = " ".join(t for t in texts if t).lower()
    return any(keyword in combined for keyword in _ECONOMY_SOCIETY_DOWNRANK_KEYWORDS)


def rank_economy_society_items(items):
    """Stable sort: every genuinely minor/promotional/administrative real
    item moves after every other real item, but never reordered relative
    to each other or dropped -- "show fewer/less-prominent," never
    fabricate or delete."""
    return sorted(items, key=lambda item: 1 if _is_minor_administrative_story(item) else 0)


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


def _resolved_evidence_entry(ref, evidence_by_ref):
    """Builds ONE real {"ref", "summary", "event_key"} evidence entry --
    shared by both _producer_intelligence_section and
    _music_trend_intelligence_section so a signal's evidence always has
    the SAME real shape regardless of which synthesis produced it.
    `evidence_by_ref` values are {"summary", "event_key"} dicts (see
    _safe_parse_producer_intelligence/_safe_parse_music_trend_
    intelligence); a ref missing from the map (a malformed/legacy row)
    falls back to showing the bare ref as its own summary with no
    resolvable event_key -- never a crash, never a fabricated identity."""
    found = evidence_by_ref.get(ref)
    if found is None:
        return {"ref": ref, "summary": ref, "event_key": None}
    return {
        "ref": ref, "summary": _fix_known_truncated_publisher_suffix(found["summary"]),
        "event_key": found.get("event_key"),
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
    at render time, never a fabricated explanation.

    Each value is {"summary": str, "event_key": str|None} -- MUSIC EVENT-
    LEVEL IDENTITY: `event_key` is the real event_key report.producer_
    synthesis.build_evidence_catalog already propagated directly from the
    originating real news item at catalog-build time (see that module's
    own docstring) -- None for a legacy/older persisted row's catalog
    entry (no `event_key` key at all) or for a real non-article evidence
    type (chart/cross-platform facts), never fabricated."""
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
                evidence_by_ref[entry["ref"]] = {"summary": entry["summary"], "event_key": entry.get("event_key")}

    return valid, evidence_by_ref


# EDITORIAL QUALITY (music-primary product standard): report.producer_
# synthesis / report.music_trend_synthesis both prompt the LLM in English
# against an English evidence catalog (real article titles/snippets, most
# from English-language music trade press), so their real what_is_moving/
# why_it_matters/what_to_watch/what_could_i_make_now/observed/
# interpretation text comes back in English -- correct and expected
# upstream, but "primary explanatory language: natural Korean" is a real
# product requirement these fields were never held to. Translated HERE at
# read time via the SAME trusted, already-cached report.translation
# infrastructure every news headline already uses (translate_and_cache is
# a no-op UNAVAILABLE/NOT_REQUIRED result, never a crash, when no provider
# is configured or the text is already Korean) -- never re-prompts the
# LLM, never touches the persisted English original, never invents a
# translation. Real evidence-chip quotes are deliberately left untranslated
# (they're citations of the real source text, not primary explanatory
# prose -- translating a direct quote would blur fact vs. paraphrase).
def _translate_synthesis_field(conn, provider, text):
    if not text:
        return text
    result = translate_and_cache(conn, provider, text)
    if result["status"] in ("TRANSLATED", "NOT_REQUIRED") and result["translated_text"]:
        return result["translated_text"]
    return text


# PROFESSIONAL EDITORIAL QUALITY PASS: a confirmed real V1/ingestion-layer
# data-quality defect (raw_items.title/snippet themselves -- upstream of
# any V2 code, and never modified here per this project's "V1 수정 금지"
# constraint) leaves a real publisher name mid-word-truncated on some
# Google-News-sourced items (e.g. "Music Wee" for the real trade
# publication "Music Week"). This is a narrow, explicit, deterministic
# correction table for CONFIRMED real truncations only -- never a general
# heuristic (which risks silently mangling a legitimately short real
# publisher name like "Vox" or "BBC") -- matched only against the exact
# known-bad trailing suffix, applied at V2 read time everywhere this text
# is displayed.
_KNOWN_TRUNCATED_PUBLISHER_SUFFIXES = {
    " - Music Wee": " - Music Week",
    " Music Wee": " Music Week",
}

# PROFESSIONAL NEWSLETTER x INTELLIGENCE HYBRID REDESIGN: a confirmed real
# ingestion-layer artifact -- some source feeds (e.g. Music Business
# Worldwide) append a literal bare "Source" link-label as the very last
# word of raw_items.snippet itself (never modified here per "V1 수정
# 금지" -- upstream of any V2 code). Left in place, it renders as a
# jarring untranslated English word tacked onto an otherwise-Korean
# sentence (a real "mixed Korean/English fragment" defect). Narrow and
# deterministic: only strips a literal trailing " Source" token, never a
# legitimate sentence that happens to end in some other word.
_TRAILING_FEED_ARTIFACT_SUFFIX = " Source"


def _fix_known_truncated_publisher_suffix(text):
    if not text:
        return text
    for bad, good in _KNOWN_TRUNCATED_PUBLISHER_SUFFIXES.items():
        if text.endswith(bad):
            text = text[: -len(bad)] + good
            break
    stripped = text.rstrip()
    if stripped.endswith(_TRAILING_FEED_ARTIFACT_SUFFIX):
        text = stripped[: -len(_TRAILING_FEED_ARTIFACT_SUFFIX)].rstrip()
    return text


# FINAL EDITORIAL TEXT-QUALITY GATE (PROFESSIONAL EDITORIAL QUALITY PASS):
# report.validation already rejects malformed synthesis text (refusal
# markers, non-Korean, internal-ref-label leaks -- see report.text_
# quality.is_malformed_synthesis_text) BEFORE persisting, but that gate
# was never re-applied at READ time, so a row persisted before a gate was
# strengthened (or one whose defect class wasn't caught yet) could still
# reach the page. This closes that gap: every real user-facing text field
# is re-checked HERE, immediately before rendering, on every read,
# regardless of when the row was persisted. Deliberately REJECTS the
# whole item rather than surgically rewriting it -- a real day with too
# little clean evidence simply shows fewer items, never a silently
# mangled sentence. Never touches the persisted row itself.
def _passes_editorial_gate(*texts):
    return not any(is_malformed_synthesis_text(t) for t in texts if t)


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
    provider = build_translation_provider()
    resolved = []
    for insight in insights:
        enriched = dict(insight)
        for field in ("what_is_moving", "why_it_matters", "what_to_watch", "what_could_i_make_now"):
            enriched[field] = _translate_synthesis_field(conn, provider, insight.get(field))
        if not _passes_editorial_gate(*(enriched[f] for f in
                                         ("what_is_moving", "why_it_matters", "what_to_watch", "what_could_i_make_now"))):
            continue
        enriched["evidence"] = [_resolved_evidence_entry(ref, evidence_by_ref) for ref in insight["evidence_refs"]]
        # PRODUCER/A&R FINAL QUALITY PASS: an insight whose own real
        # what_is_moving text is (near-)verbatim redundant with one of its
        # own cited real evidence summaries adds no real synthesis --
        # report.producer_synthesis's own prompt already instructs against
        # this, but a real deterministic gate here catches it regardless
        # of prompt compliance, exactly like _is_redundant already gates
        # a news snippet that merely restates its own headline.
        if any(_is_redundant(enriched["what_is_moving"], ev["summary"]) for ev in enriched["evidence"]):
            continue
        resolved.append(enriched)
    if not resolved:
        return {"state": "UNAVAILABLE", "insights": []}
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
    a caller that reads any of the 4 categories independently.
    evidence_by_ref values are {"summary", "event_key"} dicts -- see
    _resolved_evidence_entry / report.music_trend_synthesis.
    build_evidence_catalog's own MUSIC EVENT-LEVEL IDENTITY docstring."""
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
                evidence_by_ref[entry["ref"]] = {"summary": entry["summary"], "event_key": entry.get("event_key")}

    return lists_by_field, evidence_by_ref


# PROFESSIONAL EDITORIAL QUALITY PASS: Genre Radar must actually identify
# genre/style movement (a real genre or subgenre name), never an artist-
# discovery programme, a single tour/stadium booking, or an AI-tool launch
# mislabeled as a "genre trend" -- and Production Radar must actually
# describe production/composition characteristics (tempo, groove, sound
# palette, arrangement, ...), never licensing/business news. Deliberately
# a real, deterministic, narrow keyword check -- not a semantic judgment
# call this module is meant to avoid making; a genuine genre/production
# signal upstream (report/music_trend_synthesis.py's own prompt already
# instructs the LLM to name a real genre/production characteristic
# explicitly) will always surface at least one of these real terms, so a
# real day whose evidence can't support that specific kind of conclusion
# simply shows fewer items -- never relabeled, never forced to fit.
_GENRE_KEYWORDS = (
    "k-pop", "케이팝", "j-pop", "제이팝", "r&b", "알앤비", "hip-hop", "hip hop", "힙합", "랩",
    "dance-pop", "댄스팝", "electropop", "일렉트로팝", "synth-pop", "신스팝", "edm",
    "house", "하우스", "techno", "테크노", "garage", "개러지", "2-step", "투스텝",
    "jersey club", "저지 클럽", "afrobeats", "아프로비트", "drum and bass", "드럼앤베이스", "d&b",
    "trap", "트랩", "disco", "디스코", "lo-fi", "로파이", "country", "컨트리",
    "indie", "인디", "alt-r&b", "얼터너티브 r&b", "reggaeton", "레게톤", "dancehall", "댄스홀",
    "amapiano", "아마피아노", "hyperpop", "하이퍼팝", "city pop", "시티팝", "bedroom pop",
    "trot", "트로트", "drill", "드릴", "afro-pop", "afropop", "bossa nova", "보사노바",
    "jazz", "재즈", "soul", "소울", "gospel", "가스펠", "latin pop", "라틴팝",
    "regional mexican", "corrido", "발라드", "ballad", "punk", "펑크(음악)", "metal", "메탈",
    "subgenre", "하위 장르", "장르 트렌드", "genre trend",
    "bedroom-pop", "hip house", "힙 하우스",
)
# NEWSLETTER x MUSIC INTELLIGENCE PRODUCT UPGRADE (confirmed real defect,
# reversing a prior pass's call): "tiktok pop" was previously treated as
# a valid genre/format label -- but the real evidence behind it (a
# platform-run artist-discovery program, a viral-TikTok-sample credit, an
# artist profiled as a "TikTok icon") is platform marketing and virality,
# NOT an actual observed genre/subgenre/hybrid/rhythm/sonic-movement
# signal. Deliberately excluded from _GENRE_KEYWORDS now -- an item whose
# only "genre" evidence is TikTok-platform framing correctly fails this
# gate and is never shown as a genre signal.
_PRODUCTION_KEYWORDS = (
    "bpm", "템포", "tempo", "groove", "그루브", "drum pattern", "드럼 패턴", "드럼",
    "bass line", "베이스라인", "harmonic", "화성", "chord progression", "코드 진행",
    "sound palette", "사운드 팔레트", "vocal production", "보컬 프로덕션", "보컬 처리",
    "arrangement", "편곡", "intro length", "인트로", "hook", "훅", "song structure",
    "곡 구조", "dynamics", "다이내믹", "mix character", "믹싱", "믹스",
    "sample", "샘플링", "샘플 사용", "synth", "신스 사운드", "instrumentation", "악기 편성",
    "rhythm", "리듬", "mastering", "마스터링", "reverb", "리버브", "melody line", "멜로디 라인",
)


def _text_contains_keyword(text, keywords):
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in keywords)


# STRICT GENRE/PRODUCTION RADAR (FAST COMPLETION product pass, confirmed
# real gap): the positive keyword gate above requires a real genre/
# production TERM somewhere in the text, but a real term can still appear
# purely as platform-marketing/virality framing or an ordinary product-
# feature/release announcement (e.g. "새 템포 조절 기능을 출시" mentions
# "템포" as a UI control label, not an observed sonic characteristic; a
# "틱톡에서 화제" story can literally name a genre while being pure
# virality, not a real stylistic-movement signal). Checked FIRST, same
# real "down-rank/reject before the positive class check" precedent
# report.web_data_v2._MUSIC_INDUSTRY_DOWNRANK_KEYWORDS already
# establishes for Music Industry ranking -- never removes a genuinely
# distinct real signal, only rejects the specific marketing/popularity/
# announcement framing this section must never surface as if it were
# real musical-movement intelligence.
_MUSIC_TREND_REJECT_KEYWORDS = (
    "틱톡에서", "on tiktok", "바이럴 챌린지", "viral challenge", "화제가 되고 있다",
    "trending on", "조회수를 기록", "조회수가", "인기가 급상승",
    "기능을 출시", "신기능을 공개", "launches a new feature", "unveiled a new tool",
    "새 도구를 공개", "앱을 출시", "기능을 추가했다", "업데이트를 출시",
    "컴백을 예고", "발매를 예고", "선공개했다", "티저를 공개", "발매 소식을 전했다",
    "팔로워 수", "구독자 수",
    # PRODUCTION RADAR DOMAIN PURITY (EDITORIAL INTEGRITY PASS, confirmed
    # real defect): a creator-tool/product VERSION launch (e.g. "Suno
    # Studio 2.0을 출시했다") is a business/creator-workflow event, never
    # an observed musical/sonic/arrangement characteristic of an actual
    # song -- even when its own real interpretation text speculates about
    # downstream workflow consequences using real production vocabulary
    # ("편곡", "믹싱"), that speculation is not itself an observed
    # production trait. Real tool-news value still surfaces via Music
    # Industry's own real AI-music priority class -- never lost, only
    # kept out of Genre/Production Radar specifically.
    "studio 2.0", "스튜디오 2.0", "출시했다고 보도", "챗 바를 추가", "챗 바를 넣",
)


def _is_marketing_or_popularity_framing(observed, interpretation):
    combined = f"{observed or ''} {interpretation or ''}"
    return _text_contains_keyword(combined, _MUSIC_TREND_REJECT_KEYWORDS)


def _is_genre_signal(observed, interpretation):
    if _is_marketing_or_popularity_framing(observed, interpretation):
        return False
    return _text_contains_keyword(observed, _GENRE_KEYWORDS) or _text_contains_keyword(interpretation, _GENRE_KEYWORDS)


def _is_production_signal(observed, interpretation):
    if _is_marketing_or_popularity_framing(observed, interpretation):
        return False
    return _text_contains_keyword(observed, _PRODUCTION_KEYWORDS) or _text_contains_keyword(interpretation, _PRODUCTION_KEYWORDS)


_MUSIC_TREND_SEMANTIC_CHECKS = {
    "genre_signals": _is_genre_signal,
    "production_notes": _is_production_signal,
}


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
    provider = build_translation_provider()

    def _resolve(items, semantic_check=None):
        resolved = []
        for item in items:
            enriched = dict(item)
            enriched["observed"] = _translate_synthesis_field(conn, provider, item.get("observed"))
            enriched["interpretation"] = _translate_synthesis_field(conn, provider, item.get("interpretation"))
            if not _passes_editorial_gate(enriched["observed"], enriched["interpretation"]):
                continue
            # PROFESSIONAL EDITORIAL QUALITY PASS: Genre Radar must
            # actually be about genre movement, Production Radar must
            # actually be about production/composition characteristics --
            # see _is_genre_signal/_is_production_signal. A real day whose
            # evidence can't support a genuine conclusion in that specific
            # sense simply shows fewer items here; it is never relabeled
            # or forced to fit.
            if semantic_check is not None and not semantic_check(enriched["observed"], enriched["interpretation"]):
                continue
            enriched["evidence"] = [_resolved_evidence_entry(ref, evidence_by_ref) for ref in item["evidence_refs"]]
            resolved.append(enriched)
        return resolved

    resolved_by_field = {
        field: _resolve(lists_by_field[field], semantic_check=_MUSIC_TREND_SEMANTIC_CHECKS.get(field))
        for field in _MUSIC_TREND_LIST_FIELDS
    }
    if not any(resolved_by_field.values()):
        return {"state": "UNAVAILABLE", **empty}
    return {"state": "NORMAL", **resolved_by_field}


# MAJOR IA REBUILD (music-primary product phase): MUSIC is the product's
# primary intelligence domain -- these two constants bound the two new
# cross-cutting curation surfaces (MUSIC TODAY, TODAY'S MUSIC INTELLIGENCE)
# built below. Display caps only, never a quality threshold -- a thin real
# day legitimately returns fewer items than these maximums; nothing is
# ever padded to reach them.
_MUSIC_TODAY_MAX_ITEMS = 6
# CATEGORY-CONTIGUOUS IA REFINEMENT: the hero is now MUSIC-only (AI/
# ECONOMY/SOCIETY are never mixed into it -- a reader must never be
# pulled out of MUSIC and back in at the very top of the page either).
_TODAY_MUSIC_INTELLIGENCE_MAX_SIGNALS = 5


def _music_track_label(entry):
    return f'{entry["canonical_artist"]} - {entry["canonical_title"]}'


def _chart_entity_key(canonical_artist, canonical_title):
    return f"{(canonical_artist or '').strip().lower()}::{(canonical_title or '').strip().lower()}"


def known_chart_entity_keys(spotify_chart):
    """SEMANTIC DUPLICATION GUARD (content-quality hardening pass,
    2026-08-17): the real, CLOSED vocabulary of today's chart track
    identities -- normalized "artist::title" key -> real "Artist - Title"
    display label -- built directly from spotify_chart's own real top10
    entries, never invented or fuzzy-matched. Used by report.web_render_v2
    to detect which real chart entity a signal/synthesis entry's own text
    is actually ABOUT, for cross-section semantic-duplication suppression
    (see _apply_full_music_cross_section_dedup). Empty when the chart
    itself isn't NORMAL that day -- never a fabricated entity."""
    if spotify_chart.get("state") != "NORMAL":
        return {}
    mapping = {}
    for entry in spotify_chart.get("top10") or []:
        artist, title = entry.get("canonical_artist"), entry.get("canonical_title")
        if artist and title:
            mapping[_chart_entity_key(artist, title)] = _music_track_label(entry)
    return mapping


def _collect_music_signal_candidates(data):
    """Real, already-computed music facts/analyses, in real editorial
    priority order (real editorial news first, then real mechanical chart
    facts, then real already-validated AI analysis/insight text) -- feeds
    BOTH MUSIC TODAY and TODAY'S INTELLIGENCE's music slots from the SAME
    single real selection, never two independently-maintained notions of
    "what's the top music signal today." Never fabricates: a candidate
    exists only when its own real underlying data exists; an empty/
    unavailable capability contributes zero candidates, never a
    placeholder.

    Each candidate: {"type": str, "mode": "FACT"|"ANALYSIS", "headline_
    item": dict|None (a real news item -- the renderer's own Korean-first
    display-title logic applies to it; INDUSTRY_NEWS only), "fact_text":
    str|None (a real, mechanical, non-judgmental description built only
    from already-computed numbers/labels -- set whenever headline_item is
    None), "why_it_matters": str|None (real, already-validated text, never
    invented here), "producer_implication": str|None (real, already-
    validated text, only when the SAME real evidence directly supports
    it), "source_url": str|None}."""
    candidates = []

    # NEWSLETTER x MUSIC INTELLIGENCE PRODUCT UPGRADE (confirmed real
    # cross-section event-exposure defect): report.music_trend_synthesis.
    # build_evidence_catalog cites a real news item's own real `title`
    # text verbatim as an evidence-catalog summary -- so a real news item
    # and a genre/production/kpop_ar_notes evidence entry describing the
    # SAME real article share the exact same real title text. That exact
    # (never fuzzy) match is what lets an INDUSTRY_NEWS candidate here
    # register its own real evidence-ref fingerprint too, so the
    # overlap-dedup below can catch the case a raw earlier version of
    # this pass missed: the same real event surfacing once as the raw
    # INDUSTRY_NEWS pick AND AGAIN as a Genre/Production/K-pop synthesis
    # card in this same Hero/Music Today pool.
    trend_for_refs = data.get("music_trend_intelligence") or {}

    def _evidence_refs_for_title(title):
        if not title:
            return set()
        refs = set()
        for field in ("genre_signals", "production_notes", "producer_references", "kpop_ar_notes"):
            for entry in trend_for_refs.get(field) or []:
                for ev in entry.get("evidence", []):
                    summary = ev.get("summary", "")
                    if summary == title or summary.startswith(title + " — "):
                        refs.add(ev["ref"])
        return refs

    for category in ("SPOTIFY", "TIKTOK"):
        # PROFESSIONAL EDITORIAL QUALITY PASS: the hero/Music Today's own
        # "top" industry-news pick must be the highest real USER-IMPACT
        # story, not whichever one happened to sort first upstream (which
        # could be pure celebrity lifestyle/health news) -- same real
        # ranking Music Industry's own section already applies.
        items = rank_music_industry_items(data["news"][category]["items"])
        if items:
            top = items[0]
            evidence_refs = _evidence_refs_for_title(top.get("title"))
            why_it_matters, producer_implication, extra_refs = resolve_producer_enrichment(
                top, data.get("producer_intelligence")
            )
            # Absorbing a real matching insight's own content into the
            # Lead means MUSIC EVENT EXPOSURE BUDGET must also see them as
            # the SAME real evidence, so the insight is correctly
            # suppressed from independently re-appearing as its own
            # separate Producer/A&R card (never the same real
            # interpretation shown twice).
            evidence_refs = evidence_refs | extra_refs
            candidates.append({
                "type": "INDUSTRY_NEWS", "mode": "ANALYSIS" if why_it_matters else "FACT",
                "headline_item": top, "fact_text": None,
                "why_it_matters": why_it_matters, "producer_implication": producer_implication,
                "source_url": top.get("source_url"),
                "_evidence_refs": evidence_refs,
            })

    spotify_chart = data["spotify_chart"]
    if spotify_chart["state"] == "NORMAL":
        hot = select_viral_hot(spotify_chart["top10"])
        if hot:
            top = hot[0]
            candidates.append({
                "type": "VIRAL_HOT", "mode": "FACT", "headline_item": None,
                "fact_text": (
                    f'{_music_track_label(top)} — 오늘 가장 큰 검증된 상승폭 ▲{top["rank_delta"]} '
                    f'(최고 {top["peak_rank"]}위 · {top["days_on_chart"]}일째 차트인)'
                ),
                "why_it_matters": None, "producer_implication": None, "source_url": None,
            })
        notable_new = select_viral_new(spotify_chart["new_entries"])
        if notable_new:
            top = notable_new[0]
            candidates.append({
                "type": "VIRAL_NEW", "mode": "FACT", "headline_item": None,
                "fact_text": f'{_music_track_label(top)} — TOP10 {top["rank"]}위로 이례적 데뷔',
                "why_it_matters": None, "producer_implication": None, "source_url": None,
            })

    intelligence = data["intelligence"]
    cross_platform = intelligence.get("cross_platform") or []
    if cross_platform:
        top = cross_platform[0]
        candidates.append({
            "type": "CROSS_PLATFORM", "mode": "FACT", "headline_item": None,
            "fact_text": f'{_music_track_label(top)} — {len(top["sources"])}개 플랫폼에서 동시 상승 확인',
            "why_it_matters": None, "producer_implication": None, "source_url": None,
        })
    for source_name in sorted(intelligence.get("catalog_revival", {}).keys()):
        revival = intelligence["catalog_revival"][source_name]
        if revival:
            top = revival[0]
            candidates.append({
                "type": "CATALOG_REVIVAL", "mode": "FACT", "headline_item": None,
                "fact_text": f'{_music_track_label(top)} — {top["gap_days"]}일 공백 후 재부상 (최초 관측 {top["age_days"]}일 전)',
                "why_it_matters": None, "producer_implication": None, "source_url": None,
            })
            break

    # PROFESSIONAL EDITORIAL QUALITY PASS (semantic deduplication): the
    # SAME real underlying event can legitimately get its own distinct
    # analytical lens in Genre Radar vs Production Radar vs Producer
    # Intelligence further down the page (see this module's own "distinct
    # lens" contract) -- but it must not ALSO monopolize the Hero/Music
    # Today highlights reel two or three times over. Real evidence_refs
    # (the same "E1"/"E11" labels the underlying evidence catalog assigns)
    # are the real, deterministic event fingerprint: a candidate whose
    # refs overlap an already-added candidate's refs is skipped here --
    # never dropped from its own real section further down, only kept out
    # of this shared cross-cutting pool a second time.
    used_evidence_refs = set()
    for candidate in candidates:
        if candidate.get("_evidence_refs"):
            used_evidence_refs |= candidate["_evidence_refs"]

    trend = data["music_trend_intelligence"]
    if trend["state"] == "NORMAL":
        for field, ctype in (
            ("genre_signals", "GENRE_SIGNAL"), ("production_notes", "PRODUCTION_SIGNAL"),
            ("kpop_ar_notes", "KPOP_AR"),
        ):
            for top in trend.get(field) or []:
                refs = {ev["ref"] for ev in top.get("evidence", [])}
                if refs and refs & used_evidence_refs:
                    continue
                candidates.append({
                    "type": ctype, "mode": "ANALYSIS", "headline_item": None,
                    "fact_text": top["observed"], "why_it_matters": top["interpretation"],
                    "producer_implication": None, "source_url": None, "_evidence_refs": refs,
                    # MUSIC EVENT EXPOSURE BUDGET: the entry's own real
                    # evidence citations (ref + real summary TEXT, not
                    # just the ref label) -- needed only when THIS
                    # candidate becomes the Lead, to resolve its real
                    # event_key via report.web_render_v2._resolve_entry_
                    # event_key (title-matches the summary against a real
                    # news item, the SAME real article an INDUSTRY_NEWS
                    # candidate would already carry event_key on
                    # directly).
                    "_evidence": top.get("evidence", []),
                })
                used_evidence_refs |= refs
                break  # still at most one candidate per field, just the first NON-duplicate one

    producer = data["producer_intelligence"]
    if producer["state"] == "NORMAL" and producer["insights"]:
        for top in producer["insights"]:
            refs = {ev["ref"] for ev in top.get("evidence", [])}
            if refs and refs & used_evidence_refs:
                continue
            candidates.append({
                "type": "PRODUCER_INSIGHT", "mode": "ANALYSIS", "headline_item": None,
                "fact_text": top["what_is_moving"], "why_it_matters": top["why_it_matters"],
                "producer_implication": top.get("what_could_i_make_now"), "source_url": None, "_evidence_refs": refs,
                "_evidence": top.get("evidence", []),
            })
            used_evidence_refs |= refs
            break

    # NOTE: `_evidence_refs` (present on INDUSTRY_NEWS/GENRE_SIGNAL/
    # PRODUCTION_SIGNAL/KPOP_AR/PRODUCER_INSIGHT candidates) and
    # `_evidence` (the same real evidence citations in FULL -- ref +
    # real summary TEXT, present on the GENRE_SIGNAL/PRODUCTION_SIGNAL/
    # KPOP_AR/PRODUCER_INSIGHT candidates that don't already carry a real
    # event_key via a real headline_item) are intentionally NOT stripped
    # here -- MUSIC EVENT EXPOSURE BUDGET (see report.web_render_v2's own
    # _resolve_entry_event_key/render_dashboard_html_v2) reuses them, when
    # the candidate becomes the Lead, to resolve its real TRUE event-level
    # identity (event_key when resolvable, else the real evidence-ref set)
    # and suppress an ordinary duplicate of that SAME real event from
    # independently resurfacing in Today in Music/Music Industry/Genre
    # Radar/Production Radar/Producer sections. Neither is ever rendered
    # directly (internal keys, ignored by every renderer that only reads
    # known display fields).
    return candidates


def _candidate_key(candidate):
    """Stable identity for a music-signal candidate, used only to avoid
    showing the EXACT same real item back-to-back in TODAY'S INTELLIGENCE
    and then again as MUSIC TODAY's own first card (a real usability
    issue: a reader scrolling past the teaser strip straight into the
    next section saw the identical headline twice in one screen, with no
    new information in between). For a real news item, its own real `id`
    (or title, if a fallback item has none) is unique; every other
    candidate type contributes at most ONE real candidate per
    _collect_music_signal_candidates call, so (type, fact_text) is
    already unique for those."""
    item = candidate.get("headline_item")
    if item is not None:
        return ("INDUSTRY_NEWS", item.get("id") or item.get("title"))
    return (candidate["type"], candidate.get("fact_text"))


def _build_music_today(data, exclude_keys=None):
    """MUSIC TODAY: up to _MUSIC_TODAY_MAX_ITEMS highest-value real
    observations NOT already shown in TODAY'S MUSIC INTELLIGENCE's own
    Lead + secondary cards, in the same real editorial-priority order
    _collect_music_signal_candidates already establishes -- never padded,
    never invented; a thin real day simply returns fewer items, honestly
    down to zero (never a fallback to re-showing an already-displayed
    card just to fill the section -- confirmed real defect from actual
    generated-report QA: on a thin real day this previously re-rendered
    the SAME 3 cards the hero just showed, immediately below it, reading
    as literal duplicate content -- "max 3/6 means maximum, not
    mandatory", never padding, per SUPER_NEWS_SPEC.md's own product
    principle)."""
    candidates = _collect_music_signal_candidates(data)
    fresh = candidates if not exclude_keys else [c for c in candidates if _candidate_key(c) not in exclude_keys]
    fresh = fresh[:_MUSIC_TODAY_MAX_ITEMS]
    if len(fresh) >= _MUSIC_TODAY_MAX_ITEMS:
        return fresh

    # FINAL MUSIC RECOVERY PASS (2026-08-18): DETERMINISTIC backfill, no
    # second LLM call. On a thin real day the hero already consumes every
    # candidate _collect_music_signal_candidates has, leaving Music Today
    # empty even when real SPOTIFY/TIKTOK industry evidence still exists
    # (the SAME real items Music Industry shows). Reusing the SAME real
    # event here is allowed -- editorial DUPLICATION is about repeated
    # PROSE, not repeated events (see this function's own module
    # docstring for "what changed today" vs Industry's "what happened").
    # Each backfilled candidate is rendered with headline_item=None and
    # its own real `reason` text as fact_text -- never the bare headline
    # Industry/Lead already show, so the visible sentence is always
    # structurally distinct, never a copy-paste of another section.
    # rank_music_industry_items is the SAME deterministic (non-LLM)
    # ordering Industry itself uses, so the same input always yields the
    # same backfill order.
    already_fresh_keys = {_candidate_key(c) for c in fresh}
    lead_event_key = None
    if exclude_keys:
        # Inlined copy of report.web_render_v2._lead_signal's own real
        # rule (never imported here -- web_render_v2 already imports FROM
        # this module, so importing back would be circular): the
        # strongest signal, or the first one if none is marked strongest.
        today_signals = data.get("today_music_intelligence") or []
        lead_signal = next((s for s in today_signals if s.get("is_strongest")), None)
        if lead_signal is None and today_signals:
            lead_signal = today_signals[0]
        if lead_signal and lead_signal.get("headline_item"):
            lead_event_key = lead_signal["headline_item"].get("event_key")
    ranked_industry = rank_music_industry_items(
        (data["news"].get("SPOTIFY", {}).get("items") or []) + (data["news"].get("TIKTOK", {}).get("items") or [])
    )
    backfill_cap = min(_MUSIC_TODAY_MAX_ITEMS, len(fresh) + 3)
    for item in ranked_industry:
        if len(fresh) >= backfill_cap:
            break
        if item.get("event_key") == lead_event_key:
            continue
        if music_industry_priority_rank(item) == _MUSIC_INDUSTRY_DOWNRANKED_PRIORITY:
            continue
        reason = item.get("reason")
        if not reason:
            continue
        backfill_candidate = {
            "type": "INDUSTRY_NEWS", "mode": "FACT",
            "headline_item": None, "fact_text": reason,
            "why_it_matters": None, "producer_implication": None,
            "source_url": item.get("source_url"),
            # FINAL EDITORIAL QUALITY PASS (2026-08-18, confirmed real
            # defect): headline_item is deliberately None here (see this
            # function's own docstring -- the visible sentence must never
            # be the bare headline Industry already shows), but that also
            # stripped the real event_key report.web_render_v2._signal_
            # event_identity needs to recognize "this backfilled item is
            # the SAME real event Industry is already displaying" -- a
            # plain top-level `event_key` field (never a headline_item)
            # carries that real identity forward without reintroducing
            # the bare headline.
            "event_key": item.get("event_key"),
        }
        key = _candidate_key(backfill_candidate)
        if key in already_fresh_keys:
            continue
        already_fresh_keys.add(key)
        fresh.append(backfill_candidate)
    return fresh


def _build_today_music_intelligence(data):
    """TODAY'S MUSIC INTELLIGENCE hero: up to
    _TODAY_MUSIC_INTELLIGENCE_MAX_SIGNALS real MUSIC signals, MUSIC ONLY --
    CATEGORY-CONTIGUOUS IA REFINEMENT: AI/ECONOMY/SOCIETY are never mixed
    into the hero (a reader must never be forced to switch category and
    then return -- the whole page is grouped into contiguous per-category
    blocks, and the hero is itself part of the MUSIC block, not a cross-
    category summary anymore). Never padded: a data-sparse real day
    simply returns fewer than 5, never forced to a minimum.

    The single top candidate gets elevated treatment and may additionally
    carry its own real deeper analysis (why_it_matters/producer_
    implication) -- but ONLY when that is genuinely distinct information
    from its own one-line `meaning` (an ANALYSIS-mode candidate's real
    `observed` fact becomes the meaning line; its own real, separate
    `interpretation`/implication becomes the expanded why/watch box --
    never the same real text shown twice). Every other candidate type
    (FACT-mode chart movements, INDUSTRY_NEWS with no separate analysis
    field) simply has no expanded box, which is correct, not a gap.

    Returns (signals, used_keys) -- used_keys lets _build_music_today
    deprioritize (never drop) whichever real candidates already appeared
    here, so a reader scrolling straight from this hero into MUSIC TODAY
    sees new real information first instead of the identical top headline
    twice in a row."""
    candidates = _collect_music_signal_candidates(data)
    chosen = candidates[:_TODAY_MUSIC_INTELLIGENCE_MAX_SIGNALS]

    signals = []
    used_keys = set()
    for index, candidate in enumerate(chosen):
        is_top = index == 0
        # NEWSLETTER x MUSIC INTELLIGENCE PRODUCT UPGRADE (confirmed real
        # defect): the headline itself is ALWAYS candidate["fact_text"]
        # whenever there's no real headline_item (see report.web_render_v2.
        # _signal_headline_html) -- `meaning` must never repeat that same
        # fact_text as a second line right under it. The real, separate
        # interpretation (`why_it_matters` -- a genuine reason/analysis
        # text, distinct from the observed fact) is the only honest
        # candidate for a one-line "meaning" subtitle; when no real
        # interpretation exists (FACT-mode chart candidates), there is
        # nothing real left to show, so meaning is simply absent rather
        # than falling back to repeating the headline.
        meaning = candidate["why_it_matters"] if not is_top else None
        why_it_matters = None
        watch_next = None
        if is_top:
            # The lead story's own real interpretation appears exactly
            # ONCE, as its own labeled WHY IT MATTERS row (never also
            # duplicated into `meaning` above).
            why_it_matters = candidate["why_it_matters"]
            watch_next = candidate.get("producer_implication")
        signals.append({
            "type": candidate["type"], "is_strongest": is_top,
            "headline_item": candidate["headline_item"], "fact_text": candidate["fact_text"],
            "meaning": meaning, "why_it_matters": why_it_matters, "watch_next": watch_next,
            # MUSIC EVENT EXPOSURE BUDGET: carried through so the renderer
            # can resolve this signal's real TRUE event-level identity
            # (event_key when resolvable via `_evidence`'s real citation
            # text, else the real `_evidence_refs` set) and suppress an
            # ordinary duplicate of that SAME real event from independently
            # resurfacing elsewhere when this signal becomes the Lead --
            # neither is ever rendered directly.
            "_evidence_refs": candidate.get("_evidence_refs"),
            "_evidence": candidate.get("_evidence"),
        })
        used_keys.add(_candidate_key(candidate))

    return signals, used_keys


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

    data = {
        "report_date_kst": report_date_kst,
        "news": news,
        "tiktok_chart": _tiktok_chart_section(),
        "spotify_chart": _spotify_chart_section(conn, report_date_kst),
        "intelligence": _intelligence_section(conn, report_date_kst),
        "producer_intelligence": _producer_intelligence_section(conn, report_date_kst),
        "music_trend_intelligence": _music_trend_intelligence_section(conn, report_date_kst),
    }
    # MAJOR IA REBUILD (music-primary product phase): both curated purely
    # by re-selecting from the dict already assembled above -- neither
    # reads the DB again or calls an LLM.
    data["today_music_intelligence"], used_music_keys = _build_today_music_intelligence(data)
    data["music_today"] = _build_music_today(data, exclude_keys=used_music_keys)
    data["spotify_watch_candidates"] = spotify_watch_candidates(data)
    # PROFESSIONAL EVIDENCE SELECTION RECOVERY (2026-08-18): a NEW,
    # separate dict key -- never merged into data["news"] itself -- so
    # report.web_render_v2.render_dashboard_html_v2 (DAILY), which only
    # ever reads data["news"], is provably unaffected; only
    # render_music_page_html_v2 reads this key. See
    # professional_evidence_backfill's own docstring for what/why.
    data["music_professional_backfill"] = {
        category: professional_evidence_backfill(conn, report_date_kst, news[category]["items"], category)
        for category in ("SPOTIFY", "TIKTOK")
    }
    return data

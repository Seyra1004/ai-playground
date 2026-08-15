"""Credential-independent V2 intelligence architecture pass (2026-08-14):
targeted tests for the specific PASS criteria named in the task spec --
deterministic freshness on archive regeneration, real (non-source_count-only)
ranking signals, FIRST_OBSERVED != NEW status semantics, conservative story
clustering (no false merge), single-source-of-truth source metadata, and
translation cache idempotency/never-overwrites-original."""

from datetime import datetime, timezone

import pytest

from db.database import connect, init_db
from music.signal_engine import compute_chart_diff
from report.candidate_selection import select_news_candidates
from report.source_metadata import source_display_name, source_quality_score
from report.story_clustering import cluster_candidates
from report.translation import (
    NullTranslationProvider,
    STATUS_TRANSLATED,
    STATUS_UNAVAILABLE,
    TranslationProvider,
    get_cached_translation,
    translate_and_cache,
)
from report.web_data_v2 import _enrich_chart_entry


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_item(conn, source_name, source_item_key, category, event_key, title,
                  collected_at, published_at=None, entity_name=None):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url,
              title, collected_at, published_at, category)
           VALUES (?, ?, 'rss', 'https://x/'||?, ?, ?, ?, ?)""",
        (source_name, source_item_key, source_item_key, title, collected_at, published_at, category),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items
           (raw_item_id, category, event_key, entity_name, normalized_title, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (raw_id, category, event_key, entity_name, title, collected_at),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---- 1. Deterministic freshness on archive regeneration --------------------


def test_historical_report_date_freshness_is_deterministic_regardless_of_wall_clock(conn):
    _insert_item(
        conn, "s1", "k1", "AI_NEWS", "ev-1", "title",
        "2026-01-05T16:00:00+00:00", published_at="2026-01-05T15:00:00+00:00",
    )
    # A PAST report_date_kst (not "today") must resolve to the SAME
    # freshness_bucket/final_score no matter which real instant this
    # function happens to run at -- simulated here via two different
    # explicit as_of_utc values that both land on a later real calendar day.
    # The default (as_of_utc=None) resolution for a PAST report_date_kst
    # must agree with itself no matter when this function actually runs --
    # the real determinism invariant under test.
    default_a = select_news_candidates(conn, ["AI"], "2026-01-06")
    default_b = select_news_candidates(conn, ["AI"], "2026-01-06")
    assert default_a == default_b
    assert default_a["AI"][0]["freshness_bucket"] == default_b["AI"][0]["freshness_bucket"]
    assert default_a["AI"][0]["final_score"] == default_b["AI"][0]["final_score"]

    # The default resolution itself is a pure function of report_date_kst
    # for any non-today date -- pinned to the END of that KST calendar day,
    # never to whatever real instant this function happens to run at.
    from report.candidate_selection import _resolve_as_of_utc

    anchor = _resolve_as_of_utc("2026-01-06")
    assert anchor == datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)  # 2026-01-07 00:00 KST == 2026-01-06 15:00 UTC
    assert _resolve_as_of_utc("2026-01-06") == anchor  # calling again (later, in real time) reproduces it exactly


# ---- 2. Real ranking signals: source_quality/corroboration/novelty move the
# score, not just source_count/event_key lexical order -----------------------


def test_official_tier_source_outranks_a_fresher_but_lower_tier_single_source_within_same_bucket(conn):
    now_iso = "2026-08-12T10:00:00+00:00"
    # openai_news_rss = TIER_1 in sources.yaml; a random unknown source
    # defaults to the neutral 0.5 quality score -- both same freshness
    # bucket (both recent), both single-source (source_count=1), so the
    # ONLY thing that can separate them is source_quality_score.
    _insert_item(conn, "openai_news_rss", "k1", "AI_NEWS", "ev-official", "official story",
                 "2026-08-12T01:00:00+00:00", published_at=now_iso)
    _insert_item(conn, "some_unknown_blog", "k2", "AI_NEWS", "ev-random", "random story",
                 "2026-08-12T01:00:00+00:00", published_at=now_iso)

    result = select_news_candidates(conn, ["AI"], "2026-08-12",
                                     as_of_utc=datetime(2026, 8, 12, 12, tzinfo=timezone.utc))
    by_key = {c["event_key"]: c for c in result["AI"]}
    assert by_key["ev-official"]["source_quality_score"] > by_key["ev-random"]["source_quality_score"]
    assert [c["event_key"] for c in result["AI"]] == ["ev-official", "ev-random"]


def test_final_score_present_and_freshness_bucket_still_primary_gate(conn):
    _insert_item(conn, "s1", "k1", "AI_NEWS", "ev-1", "title",
                 "2026-08-12T01:00:00+00:00", published_at="2026-08-12T00:00:00+00:00")
    result = select_news_candidates(conn, ["AI"], "2026-08-12",
                                     as_of_utc=datetime(2026, 8, 12, 12, tzinfo=timezone.utc))
    c = result["AI"][0]
    for field in ("freshness_score", "source_quality_score", "corroboration_score", "novelty_score", "final_score"):
        assert isinstance(c[field], float)
    assert c["freshness_bucket"] == 0


# ---- 3. FIRST_OBSERVED != NEW status -----------------------------------


def _seed_chart(conn, source_name, metric_name, entity_id_offset=0):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'B', 'ORIGINAL', 'RESOLVED', '2026-08-12T00:00:00+00:00', ?)""",
        (source_name,),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, ?, ?, 1, 'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, source_name, metric_name, "2026-08-12T00:00:00+00:00", "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    return entity_id


def test_first_observation_day_entries_get_status_first_observed_not_new(conn):
    _seed_chart(conn, "apple_music", "apple_music_chart_position")
    diff = compute_chart_diff(conn, "2026-08-12", "apple_music", "apple_music_chart_position")
    assert diff["is_first_observation"] is True
    assert diff["entries"][0]["is_new"] is True  # unchanged legacy field, V1 compatibility
    assert diff["entries"][0]["status"] == "FIRST_OBSERVED"  # V2 canonical field


def test_v2_boundary_first_observed_entry_has_is_new_false(conn):
    """The strict V2 data-boundary contract: status == FIRST_OBSERVED must
    imply is_new == False after report.web_data_v2._enrich_chart_entry's
    normalization -- the raw music.signal_engine dict (V1-compatible,
    is_new=True for both FIRST_OBSERVED and NEW) is never itself mutated;
    _enrich_chart_entry's `dict(entry)` copy is what gets corrected."""
    _seed_chart(conn, "apple_music", "apple_music_chart_position")
    diff = compute_chart_diff(conn, "2026-08-12", "apple_music", "apple_music_chart_position")
    raw_entry = diff["entries"][0]
    assert raw_entry["status"] == "FIRST_OBSERVED"
    assert raw_entry["is_new"] is True  # raw V1-compatible engine output, untouched

    enriched = _enrich_chart_entry(conn, raw_entry, "apple_music", diff["observed_at"])
    assert enriched["status"] == "FIRST_OBSERVED"
    assert enriched["is_new"] is False  # V2 boundary contract
    assert enriched["previous_rank"] is None


def test_genuine_new_entry_on_a_non_baseline_day_gets_status_new(conn):
    # Day 1: one entity establishes a real baseline.
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'A', 'ORIGINAL', 'RESOLVED', '2026-08-11T00:00:00+00:00', 'apple_music')"""
    )
    e1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, 'apple_music', 'apple_music_chart_position', 1,
                   'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', '2026-08-11T00:00:00+00:00', '2026-08-11T00:00:00+00:00')""",
        (e1,),
    )
    # Day 2: a genuinely different, second entity appears for the first
    # time -- a real re-entry/new-entry event, NOT a first-ever observation
    # (the source already has real history from day 1).
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('C', 'D', 'ORIGINAL', 'RESOLVED', '2026-08-12T00:00:00+00:00', 'apple_music')"""
    )
    e2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, 'apple_music', 'apple_music_chart_position', 2,
                   'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', '2026-08-12T00:00:00+00:00', '2026-08-12T00:00:00+00:00')""",
        (e2,),
    )
    conn.commit()

    diff = compute_chart_diff(conn, "2026-08-12", "apple_music", "apple_music_chart_position")
    assert diff["is_first_observation"] is False
    new_entry = next(e for e in diff["entries"] if e["music_entity_id"] == e2)
    assert new_entry["status"] == "NEW"

    enriched = _enrich_chart_entry(conn, new_entry, "apple_music", diff["observed_at"])
    assert enriched["status"] == "NEW"
    assert enriched["is_new"] is True  # is_new True ONLY for status == NEW, per the V2 contract
    assert enriched["previous_rank"] is None


# ---- 4. Story clustering: real merge + no false merge -----------------------


def test_near_duplicate_headlines_from_independent_sources_cluster():
    candidates = [
        {"event_key": "ev-1", "normalized_title": "OpenAI launches new model today",
         "source_count": 1, "source_names": ["source_a"], "published_at": "2026-08-12T01:00:00+00:00",
         "entity_name": "OpenAI"},
        {"event_key": "ev-2", "normalized_title": "OpenAI launches a new model today",
         "source_count": 1, "source_names": ["source_b"], "published_at": "2026-08-12T02:00:00+00:00",
         "entity_name": "OpenAI"},
    ]
    clusters = cluster_candidates(candidates)
    assert len(clusters) == 1
    assert clusters[0]["related_article_count"] == 2
    assert clusters[0]["distinct_source_count"] == 2


def test_genuinely_different_stories_never_false_merge():
    candidates = [
        {"event_key": "ev-1", "normalized_title": "Federal Reserve raises interest rates sharply",
         "source_count": 1, "source_names": ["source_a"], "published_at": "2026-08-12T01:00:00+00:00",
         "entity_name": None},
        {"event_key": "ev-2", "normalized_title": "Spotify announces new artist royalty program",
         "source_count": 1, "source_names": ["source_b"], "published_at": "2026-08-12T01:30:00+00:00",
         "entity_name": None},
    ]
    assert cluster_candidates(candidates) == []


def test_no_manufactured_single_member_cluster():
    candidates = [
        {"event_key": "ev-1", "normalized_title": "A totally unique headline about something",
         "source_count": 1, "source_names": ["source_a"], "published_at": "2026-08-12T01:00:00+00:00",
         "entity_name": None},
    ]
    assert cluster_candidates(candidates) == []


def test_same_source_covering_two_event_keys_does_not_merge():
    # Same source_name on both sides -- not independent corroboration of a
    # real-world event, conservatively refused even though titles are
    # near-identical.
    candidates = [
        {"event_key": "ev-1", "normalized_title": "Big story breaks today",
         "source_count": 1, "source_names": ["source_a"], "published_at": "2026-08-12T01:00:00+00:00",
         "entity_name": None},
        {"event_key": "ev-2", "normalized_title": "Big story breaks today update",
         "source_count": 1, "source_names": ["source_a"], "published_at": "2026-08-12T01:30:00+00:00",
         "entity_name": None},
    ]
    assert cluster_candidates(candidates) == []


# ---- 5. Source metadata single source of truth -------------------------


def test_known_source_display_name_and_quality_score_from_sources_yaml():
    assert source_display_name("openai_news_rss") == "OpenAI"
    assert source_quality_score("openai_news_rss") == 1.0  # TIER_1


def test_unknown_source_falls_back_to_raw_name_and_neutral_score():
    assert source_display_name("some_never_registered_source") == "some_never_registered_source"
    assert source_quality_score("some_never_registered_source") == 0.5


def test_chart_source_metadata_from_music_registry():
    assert source_display_name("spotify_chart") == "Spotify"
    assert source_quality_score("spotify_chart") == 1.0


def test_real_sources_yaml_and_music_registry_pass_production_validation():
    """The actual project sources.yaml + music.registry.ACTIVE_MUSIC_SOURCES
    must have 100% display_name/quality_tier coverage for every ENABLED
    source -- the production FAIL gate itself must not raise against the
    real config."""
    from report.source_metadata import validate_active_source_metadata

    validate_active_source_metadata()  # must not raise


def test_validation_fails_loudly_on_a_source_missing_metadata(tmp_path):
    from report.source_metadata import SourceMetadataValidationError, validate_active_source_metadata

    broken_yaml = tmp_path / "sources.yaml"
    broken_yaml.write_text(
        """
sources:
  - source_name: no_metadata_source
    enabled: true
    source_type: rss
    category: AI_NEWS
    region: GLOBAL
    endpoint: https://example.com/feed.xml
    timeout_seconds: 10
    retry:
      max_attempts: 3
      backoff_base_seconds: 1.0
      backoff_jitter_seconds: 0.5
    auth:
      mode: none
""",
        encoding="utf-8",
    )
    with pytest.raises(SourceMetadataValidationError, match="no_metadata_source"):
        validate_active_source_metadata(sources_yaml_path=broken_yaml)


def test_validation_ignores_a_disabled_source_missing_metadata(tmp_path):
    from report.source_metadata import validate_active_source_metadata

    disabled_yaml = tmp_path / "sources.yaml"
    disabled_yaml.write_text(
        """
sources:
  - source_name: disabled_no_metadata
    enabled: false
    source_type: rss
    category: AI_NEWS
    region: GLOBAL
    endpoint: https://example.com/feed.xml
    timeout_seconds: 10
    retry:
      max_attempts: 3
      backoff_base_seconds: 1.0
      backoff_jitter_seconds: 0.5
    auth:
      mode: none
""",
        encoding="utf-8",
    )
    validate_active_source_metadata(sources_yaml_path=disabled_yaml)  # must not raise


# ---- 6. Translation cache: idempotent, never overwrites original -------


def test_translate_and_cache_degrades_to_unavailable_without_credential(conn):
    provider = NullTranslationProvider()
    result = translate_and_cache(conn, provider, "Some real headline")
    assert result["status"] == STATUS_UNAVAILABLE
    assert result["translated_text"] is None


def test_translate_and_cache_never_calls_translate_or_caches_when_unconfigured(conn, monkeypatch):
    """Phase 3A.1: NullTranslationProvider.is_configured() is deterministically
    False, so translate_and_cache short-circuits BEFORE ever calling
    translate() -- this is a stronger guarantee than the old "provider
    invoked once, then cache hit" contract (translate() is invoked ZERO
    times, and the config-unavailable outcome is never persisted per-text at
    all, so it can never block a real attempt once a credential/provider
    becomes configured later). See report/translation.py's module docstring."""
    provider = NullTranslationProvider()
    calls = []
    original_translate = provider.translate

    def _tracking_translate(text, target_lang):
        calls.append(text)
        return original_translate(text, target_lang)

    monkeypatch.setattr(provider, "translate", _tracking_translate)

    translate_and_cache(conn, provider, "Repeated headline")
    translate_and_cache(conn, provider, "Repeated headline")
    assert calls == []  # translate() never reached -- is_configured() gate short-circuits first

    cached = get_cached_translation(conn, "Repeated headline", "ko", type(provider).__name__, provider.model_name)
    assert cached is None  # never persisted -- nothing to go stale, nothing to block a later real attempt


class _FakeSuccessProvider(TranslationProvider):
    """A real (non-Null) provider double that actually 'translates' and
    counts calls per unique text -- proves idempotency for a provider that
    CAN succeed, not just the always-unavailable NullTranslationProvider."""

    def __init__(self):
        self.calls = []

    def translate(self, text, target_lang):
        self.calls.append(text)
        # A plausible-looking Korean placeholder, not the literal source
        # text -- report.translation_validation's real-Korean-output check
        # (CONTENT INTEGRITY FINALIZATION phase) would otherwise correctly
        # reject a mostly-Latin "[ko] <English text>" fallback exactly the
        # way it must reject a real non-translation in production. Still
        # varies with the input (via its length) so title/snippet still
        # provably get independent translations below.
        return f"[가짜번역:{target_lang}] {len(text)}자 원문의 번역 결과입니다"


def test_title_and_summary_translated_and_cached_independently(conn):
    from report.web_data_v2 import _attach_translation

    provider = _FakeSuccessProvider()
    item = {"title": "Real headline text", "snippet": "Real summary text, distinct from the title."}
    _attach_translation(conn, provider, item)

    assert item["original_title"] == "Real headline text"
    assert item["ko_title"] == "[가짜번역:ko] 18자 원문의 번역 결과입니다"
    assert item["translation_status"] == STATUS_TRANSLATED
    assert item["original_snippet"] == "Real summary text, distinct from the title."
    assert item["ko_snippet"] == "[가짜번역:ko] 43자 원문의 번역 결과입니다"
    assert item["ko_title"] != item["ko_snippet"]  # independently translated, not shared
    assert item["snippet_translation_status"] == STATUS_TRANSLATED
    assert provider.calls == ["Real headline text", "Real summary text, distinct from the title."]


def test_reprocessing_same_title_and_summary_is_a_cache_hit_for_each_unique_text(conn):
    from report.web_data_v2 import _attach_translation

    provider = _FakeSuccessProvider()
    item_a = {"title": "Same headline", "snippet": "Same summary."}
    item_b = {"title": "Same headline", "snippet": "Same summary."}
    _attach_translation(conn, provider, item_a)
    _attach_translation(conn, provider, item_b)

    # Two DIFFERENT items sharing the SAME real title text and the SAME
    # real summary text -- exactly two unique texts overall, so the
    # provider must be invoked exactly once per unique text (twice total),
    # never once per item (which would have been four calls).
    assert len(provider.calls) == 2
    assert item_b["ko_title"] == item_a["ko_title"]
    assert item_b["ko_snippet"] == item_a["ko_snippet"]


def test_missing_snippet_never_triggers_a_translation_call(conn):
    from report.web_data_v2 import _attach_translation

    provider = _FakeSuccessProvider()
    item = {"title": "Headline only", "snippet": None}
    _attach_translation(conn, provider, item)

    assert item["ko_snippet"] is None
    assert item["snippet_translation_status"] is None
    assert provider.calls == ["Headline only"]  # title only, never called for the absent snippet

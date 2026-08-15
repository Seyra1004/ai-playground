"""report.story_clustering: near-duplicate-event detection across
event_keys, driven by real observed production examples (SOURCE EXPANSION
+ CONTENT QUALITY HARDENING phase, 2026-08-15 labeled-sample audit -- see
SUPER_NEWS_HANDOFF.md for the full 41-pair precision/recall evaluation).

Every title pair below is copied verbatim from real select_news_candidates
output, not invented -- this is deliberate: a synthetic "Article A" / "Article
B" pair can't exercise the real vocabulary/wording gap between two
independent newsrooms describing the same event the way a real pair can."""

from report.story_clustering import cluster_candidates


def _cand(event_key, title, source, published_at=None, entity_name=None):
    return {
        "event_key": event_key,
        "normalized_title": title,
        "source_count": 1,
        "source_names": [source],
        "published_at": published_at,
        "entity_name": entity_name,
    }


# ---- Real false negative, now fixed: the OpenAI "Ultrafast" pair ------------


def test_real_ultrafast_pair_now_clusters_via_distinctive_tokens():
    """Real production pair (2026-08-14, openai_news_rss id=2 vs
    techcrunch_ai_rss id=1322): ordinary title-Jaccard similarity is only
    0.43 (below the 0.55 main threshold) because the two outlets' wording
    differs a lot around the shared facts -- but both titles share
    several rare, specific terms ("Ultrafast", "Sol", "14X"/"14x") that
    are strong evidence of the same real announcement. This was a
    confirmed false negative before the distinctive-token path was added;
    it must cluster now."""
    candidates = [
        _cand("ev-1", "Previewing Ultrafast mode: GPT-5.6 Sol at up to 14X the speed",
              "openai_news_rss", "2026-08-13T10:00:00+00:00"),
        _cand("ev-2", "OpenAI introduces 'Ultrafast,' a new mode that makes GPT-5.6 Sol work at 14x the speed",
              "techcrunch_ai_rss", "2026-08-13T19:22:00+00:00"),
    ]
    clusters = cluster_candidates(candidates)
    assert len(clusters) == 1
    assert clusters[0]["related_article_count"] == 2
    assert clusters[0]["distinct_source_count"] == 2


def test_real_low_similarity_wire_pair_clusters_smr_bill_gates():
    """Real production pair (2026-08-15, mk_economy_rss vs yonhap_economy_rss):
    both report the Korea Eximbank's SMR financing talks with Bill Gates,
    but wording differs enough that plain title similarity is only 0.44 --
    the distinctive tokens ("소형모듈원자로"/"빌", "게이츠") carry the real
    signal that plain Jaccard alone underweights."""
    candidates = [
        _cand("ev-1", "수은, 빌 게이츠와 차세대 소형모듈원자로 금융 협력 논의",
              "mk_economy_rss", "2026-08-14T20:00:00+00:00"),
        _cand("ev-2", "수출입은행, 빌 게이츠와 소형모듈원자로 상용화 협력 논의",
              "yonhap_economy_rss", "2026-08-14T21:00:00+00:00"),
    ]
    clusters = cluster_candidates(candidates)
    assert len(clusters) == 1
    assert clusters[0]["related_article_count"] == 2


# ---- Real precision guard: same entity, genuinely different real event -----


def test_same_entity_different_real_event_never_merges():
    """Real production example (2026-08-14, ECONOMY_NEWS): a photo caption
    of Bill Gates arriving in Korea and a separate, later report of his
    SMR-cooperation meetings both mention Bill Gates, but are different
    real events on the editorial calendar -- section 7's explicit
    contract ('different events involving same company/person -> never
    merge merely due to entity overlap') must hold even with the new
    distinctive-token path active."""
    candidates = [
        _cand("ev-1", "[포토] 빌 게이츠 이사장 방한", "etnews_economy_rss", "2026-08-13T05:00:00+00:00"),
        _cand("ev-2", "빌 게이츠, 정재계 연쇄회동…'AI 전력난 해법' SMR 협력 본격화",
              "mk_economy_rss", "2026-08-13T20:00:00+00:00"),
    ]
    assert cluster_candidates(candidates) == []


def test_shared_generic_word_alone_never_merges():
    """Real production example (2026-08-13, AI_NEWS): two genuinely
    different stories that both happen to mention "GPT-5.6" -- a generic,
    extremely common term across that day's entire AI news cycle -- must
    not merge on that shared token alone. Distinctive-token evidence
    requires RARE terms, not just any digit-bearing token; "gpt" and
    "5" numerically fragment and are common enough this day that they
    must not, by themselves, cross the _MIN_DISTINCTIVE_SHARED_TOKENS
    bar with nothing else in common."""
    candidates = [
        _cand("ev-1", "Writer introduces new AI model and upgraded harness to contain token costs",
              "techcrunch_ai_rss", "2026-08-13T21:13:00+00:00"),
        _cand("ev-2", "Databricks wanted to raise $1B, investors wanted $15B. It settled on $5B at a $190B valuation.",
              "techcrunch_ai_rss", "2026-08-13T20:14:00+00:00"),
    ]
    assert cluster_candidates(candidates) == []

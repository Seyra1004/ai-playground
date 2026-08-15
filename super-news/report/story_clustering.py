"""V1 non-LLM story clustering: conservative near-duplicate-event detection
ACROSS event_keys.

The existing EXACT event_key dedup in report.candidate_selection is
untouched by this module -- that mechanism is the pipeline's real,
tested, working story-deduplication layer and this file does not rebuild
or replace it (see candidate_selection's own module docstring: "this is
the existing, working story-deduplication mechanism; do not rebuild it").
This module is an ADDITIVE analysis pass on top of its already-deduped
output: near-duplicate coverage of the same real-world event that
happened to get two different event_keys (a different normalize.py
event_key derivation per outlet, a slightly different headline wording,
...) is surfaced as cluster EVIDENCE, not merged into the displayed item
list itself.

Recall is deliberately sacrificed for precision: a FALSE MERGE (treating
two genuinely different stories as one) is worse than a missed real
near-duplicate, so every available signal (headline token-set similarity,
entity-name agreement, temporal proximity, source independence) must
agree before two candidates are clustered -- a missing signal (e.g. no
published_at on either side) never counts as agreement on its own, it
just drops out of the vote. No LLM/embedding call anywhere in this
module -- purely token-set arithmetic and real timestamp deltas.
"""

import re
from datetime import datetime, timezone

_TOKEN_RE = re.compile(r"[a-z0-9가-힣]+")

# Deliberately small, high-precision stopword set -- removing more common
# words trades recall for precision risk (a coincidentally shared common
# word must never be read as evidence of the same story); this only strips
# words with essentially zero topical content.
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "is", "are", "with", "at",
    "이", "가", "은", "는", "을", "를", "에", "의", "와", "과", "도", "그리고",
}

TITLE_SIMILARITY_THRESHOLD = 0.55
TEMPORAL_PROXIMITY_HOURS = 48

# Multi-signal recall improvement (SOURCE EXPANSION + CONTENT QUALITY
# HARDENING phase, 2026-08-15): a real labeled sample of 41 production
# pairs (see SUPER_NEWS_HANDOFF.md) showed the title-Jaccard-only
# threshold already at 96.3% precision/recall, with exactly one real
# false negative -- the known OpenAI "Ultrafast" pair, whose headlines
# differ enough in ordinary wording (0.43 similarity) to miss the 0.55
# gate even though they share several highly specific, rare terms
# ("Ultrafast", "Sol", "14x") that a human reader would immediately
# recognize as the same announcement. Rather than lowering the global
# threshold (which the sample shows would also let through a genuine
# borderline false positive at 0.625), a second path is added: below the
# main threshold, a real DISTINCTIVE-token overlap -- specific, rare
# evidence, not generic shared vocabulary -- can still justify a merge.
# This never loosens the >=0.55 path's own behavior at all.
_DISTINCTIVE_SIMILARITY_FLOOR = 0.30
_MIN_DISTINCTIVE_SHARED_TOKENS = 2
_RARE_DOC_FREQ_MAX = 2


def _tokenize(title):
    if not title:
        return set()
    tokens = _TOKEN_RE.findall(title.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _title_similarity(a, b):
    """Jaccard similarity over token sets -- 0.0 (never divide-by-zero, and
    never "identical") if either title has no real tokens at all."""
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


def _hours_apart(published_a, published_b):
    """None (not 0.0) if either side is missing -- a missing timestamp must
    never silently read as "same instant"."""
    if not published_a or not published_b:
        return None
    try:
        a = datetime.fromisoformat(published_a)
        b = datetime.fromisoformat(published_b)
    except ValueError:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((a - b).total_seconds()) / 3600.0


def _sources_independent(candidate_a, candidate_b):
    sources_a, sources_b = set(candidate_a.get("source_names") or []), set(candidate_b.get("source_names") or [])
    return bool(sources_a) and bool(sources_b) and sources_a.isdisjoint(sources_b)


def _distinctive_tokens(tokens, doc_freq):
    """A token counts as real evidence of a shared specific event -- not
    just incidental shared vocabulary -- when it contains a digit (a
    specific number/version/amount: "14x", "9440억") or is rare across the
    candidate pool being clustered (doc_freq <= _RARE_DOC_FREQ_MAX). A
    generic word repeated across many of that day's headlines (e.g. "gpt"
    on a day full of GPT stories, or "발표"/"기소") never counts alone, no
    matter how many titles happen to share it."""
    return {t for t in tokens if any(ch.isdigit() for ch in t) or doc_freq.get(t, 0) <= _RARE_DOC_FREQ_MAX}


def _should_merge(candidate_a, candidate_b, doc_freq):
    """Every available signal must agree; returns (bool, similarity) so a
    caller can record real confidence evidence even for a rejected pair if
    it ever wants to (cluster_candidates only uses it for accepted pairs
    today). `doc_freq` is a token -> pool document-count map (see
    cluster_candidates), used only by the distinctive-token fallback path
    below -- it never affects the main >=TITLE_SIMILARITY_THRESHOLD path."""
    title_a, title_b = candidate_a["normalized_title"], candidate_b["normalized_title"]
    similarity = _title_similarity(title_a, title_b)
    if similarity < TITLE_SIMILARITY_THRESHOLD:
        if similarity < _DISTINCTIVE_SIMILARITY_FLOOR:
            return False, similarity
        distinctive_shared = _distinctive_tokens(_tokenize(title_a), doc_freq) & _distinctive_tokens(
            _tokenize(title_b), doc_freq
        )
        if len(distinctive_shared) < _MIN_DISTINCTIVE_SHARED_TOKENS:
            return False, similarity
        # Falls through to the same temporal/entity/source-independence
        # gates below as the main path -- distinctive-token overlap is
        # never sufficient on its own.

    hours_apart = _hours_apart(candidate_a.get("published_at"), candidate_b.get("published_at"))
    if hours_apart is not None and hours_apart > TEMPORAL_PROXIMITY_HOURS:
        return False, similarity

    entity_a, entity_b = candidate_a.get("entity_name"), candidate_b.get("entity_name")
    if entity_a and entity_b and entity_a.strip().lower() != entity_b.strip().lower():
        return False, similarity

    if not _sources_independent(candidate_a, candidate_b):
        # The exact same source(s) already covering both event_keys is more
        # likely a normalization/event_key-assignment quirk than two
        # independent outlets confirming the same real-world event --
        # conservatively refuse to treat that as cluster evidence.
        return False, similarity

    return True, similarity


def cluster_candidates(candidates):
    """candidates: report.candidate_selection's per-category candidate
    list (already exact-event_key deduped; each dict must carry
    normalized_title/event_key/source_count/source_names/published_at/
    entity_name -- exactly what select_news_candidates already returns).

    Returns a list of cluster dicts, ONE PER GROUP OF >=2 MERGED
    candidates only -- a candidate with no real near-duplicate is never
    wrapped in a manufactured single-member cluster (no forced cluster
    creation when none is warranted)."""
    n = len(candidates)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    # Corpus-wide token document-frequency, computed once over this same
    # candidate pool -- what makes a token "distinctive" is relative to
    # what else is being talked about that day (see _distinctive_tokens),
    # never a fixed global list.
    doc_freq = {}
    for candidate in candidates:
        for token in set(_tokenize(candidate["normalized_title"])):
            doc_freq[token] = doc_freq.get(token, 0) + 1

    pair_confidence = {}
    for i in range(n):
        for j in range(i + 1, n):
            merge, similarity = _should_merge(candidates[i], candidates[j], doc_freq)
            if merge:
                union(i, j)
                pair_confidence[(i, j)] = similarity

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_candidates = [candidates[i] for i in members]
        # Representative headline: the member with the highest source_count
        # (the existing, already-real corroboration signal), tie-broken by
        # event_key for determinism -- never an invented "best" title.
        representative = min(member_candidates, key=lambda c: (-c["source_count"], c["event_key"]))
        all_sources = set()
        for c in member_candidates:
            all_sources |= set(c.get("source_names") or [])
        relevant_confidences = [
            sim for (i, j), sim in pair_confidence.items() if i in members and j in members
        ]
        clusters.append({
            "representative_headline": representative["normalized_title"],
            "representative_event_key": representative["event_key"],
            "member_event_keys": sorted(c["event_key"] for c in member_candidates),
            "related_article_count": len(member_candidates),
            "distinct_source_count": len(all_sources),
            "source_list": sorted(all_sources),
            "cluster_confidence": round(min(relevant_confidences), 4) if relevant_confidences else None,
        })
    clusters.sort(key=lambda c: (-c["related_article_count"], c["representative_event_key"]))
    return clusters

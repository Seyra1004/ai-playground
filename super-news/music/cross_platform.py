"""Source-independent Cross-Platform Movement framework.

Implements the >=2-real-sources evidence gate the V2 architecture design
requires: NO cross-platform label is ever produced unless a SINGLE
music_entity has a positive VELOCITY signal from at least
MIN_SOURCES_FOR_LABEL distinct registered sources.

Schema note (important, discovered while building this): music_entity_
aliases has UNIQUE(alias_type, alias_value) -- two different entities can
never share the same ISRC (or any) alias value. This means cross-platform
matching cannot work by grouping separate entities that happen to share an
ISRC (the schema physically prevents that duplication). The correct model
is that identity RESOLUTION happens at collection time: when a second
source's collector recognizes an already-known track (e.g. by ISRC), it
must attach its observations to the SAME existing music_entity_id, not
create a second entity. That entity-resolution/merge step is NOT built
yet -- today, every collector (music/apple_music.py, music/spotify_chart.py)
creates its own entity keyed to its own platform ID, so no entity is ever
observed by two different sources. This module's query is written to
correctly recognize multi-source evidence for a single entity the moment
that resolution step exists; until then it provably returns nothing in
production, which is the correct behavior, not a workaround.

V1 scope: only the CROSS_PLATFORM_HIT label (>=2 sources agreeing). The
fuller taxonomy (TikTok-only viral / Streaming-led / Catalog Revival within
a cross-platform context) needs lag/timing analysis beyond a same-day
snapshot comparison -- not implemented here, not fabricated.
"""

from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff

MIN_SOURCES_FOR_LABEL = 2
LABEL_CROSS_PLATFORM_HIT = "CROSS_PLATFORM_HIT"

# Intelligence state semantics (credential-independent architecture pass,
# 2026-08-14): an empty `labels` list from detect_cross_platform_signals can
# mean three real, DIFFERENT things -- collapsing them into one "동일 신호
# 없음" message risks a reader mistaking "not enough sources are even
# reporting yet" for "we checked thoroughly and found nothing." This
# classification never changes detect_cross_platform_signals' own return
# shape (existing callers/tests keep reading a plain list) -- it's a
# separate, additive read of the same real diff data.
STATE_NORMAL = "NORMAL"  # >=2 sources evaluated AND >=1 real cross-platform hit
STATE_NO_SIGNAL = "NO_SIGNAL"  # >=2 sources fully evaluated, genuinely nothing crossed the threshold
STATE_INSUFFICIENT_SOURCES = "INSUFFICIENT_SOURCES"  # fewer than MIN_SOURCES_FOR_LABEL sources produced a diff today at all
STATE_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"  # an active source's diff today is itself a first observation (no real prior snapshot to compute velocity from)


def _entities_with_positive_velocity_by_source(conn, report_date_kst):
    """Returns {source_name: set(music_entity_id)} for entities with a
    positive VELOCITY signal in today's active diff for that source."""
    result = {}
    for source_name, config in ACTIVE_MUSIC_SOURCES.items():
        diff = compute_chart_diff(conn, report_date_kst, source_name, config["metric_name"])
        if diff["observed_at"] is None or not diff["entries"]:
            continue
        entity_ids = [e["music_entity_id"] for e in diff["entries"]]
        placeholders = ",".join("?" for _ in entity_ids)
        rows = conn.execute(
            f"""SELECT DISTINCT music_entity_id FROM derived_signals
               WHERE signal_type = 'VELOCITY' AND period_end = ? AND value > 0
                 AND music_entity_id IN ({placeholders})""",
            (diff["observed_at"], *entity_ids),
        ).fetchall()
        if rows:
            result[source_name] = {row["music_entity_id"] for row in rows}
    return result


def detect_cross_platform_signals(conn, report_date_kst):
    """Returns a list of {"music_entity_id", "canonical_artist",
    "canonical_title", "sources", "label"} dicts -- one per entity with
    positive VELOCITY from >= MIN_SOURCES_FOR_LABEL distinct sources today.
    Computes nothing new itself (reads only already-persisted
    derived_signals rows) -- call music.derived_signals.compute_velocity_
    signals for each active source first. Returns [] (never raises, never
    fabricates) when no entity has multi-source evidence -- exactly the
    current production state, since no entity-resolution/merge step exists
    yet (see module docstring)."""
    per_source = _entities_with_positive_velocity_by_source(conn, report_date_kst)

    entity_to_sources = {}
    for source_name, entity_ids in per_source.items():
        for entity_id in entity_ids:
            entity_to_sources.setdefault(entity_id, set()).add(source_name)

    labels = []
    for entity_id, sources in sorted(entity_to_sources.items()):
        if len(sources) >= MIN_SOURCES_FOR_LABEL:
            entity_row = conn.execute(
                "SELECT canonical_artist, canonical_title FROM music_entities WHERE id = ?", (entity_id,)
            ).fetchone()
            labels.append({
                "music_entity_id": entity_id,
                "canonical_artist": entity_row["canonical_artist"] if entity_row else None,
                "canonical_title": entity_row["canonical_title"] if entity_row else None,
                "sources": sorted(sources),
                "label": LABEL_CROSS_PLATFORM_HIT,
            })
    return labels


def classify_cross_platform_state(conn, report_date_kst, labels=None):
    """Returns one of STATE_NORMAL/STATE_NO_SIGNAL/STATE_INSUFFICIENT_
    SOURCES/STATE_INSUFFICIENT_HISTORY -- a real classification of WHY
    `labels` is empty (or isn't), never a second computation of the labels
    themselves. `labels`, if already computed by the caller, is reused
    rather than re-queried; otherwise this calls detect_cross_platform_
    signals itself. Real signals only: a source counts as "reporting today"
    only if it produced an actual diff (compute_chart_diff's own
    observed_at is non-null), and "insufficient history" is read directly
    off compute_chart_diff's own is_first_observation flag -- the same real
    fact music.signal_engine already computes, never re-derived here."""
    if labels is None:
        labels = detect_cross_platform_signals(conn, report_date_kst)
    if labels:
        return STATE_NORMAL

    sources_with_diff_today = 0
    any_insufficient_history = False
    for source_name, config in ACTIVE_MUSIC_SOURCES.items():
        diff = compute_chart_diff(conn, report_date_kst, source_name, config["metric_name"])
        if diff["observed_at"] is None:
            continue
        sources_with_diff_today += 1
        if diff.get("is_first_observation"):
            any_insufficient_history = True

    if sources_with_diff_today < MIN_SOURCES_FOR_LABEL:
        return STATE_INSUFFICIENT_SOURCES
    if any_insufficient_history:
        return STATE_INSUFFICIENT_HISTORY
    return STATE_NO_SIGNAL

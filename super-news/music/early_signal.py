"""Per-source Early Signal candidate selection -- built on top of
music.derived_signals' VELOCITY rows. Deterministic, code-only (Level 0),
no LLM call, no cross-platform merging.

Each result is explicitly tagged with its source_name (e.g. rendered as
"SPOTIFY_EARLY_SIGNAL") -- this module NEVER produces a generic, unlabeled,
or cross-platform-sounding signal. A true CROSS_PLATFORM_EARLY_SIGNAL
requires >=2 real sources agreeing (see music/cross_platform.py) and is
never emitted here.

"Prioritize small-but-fast-growing over already-large hits, no fake
precision when data is weak" (V2 requirement): enforced via
MIN_RANK_DELTA -- an entity must have moved at least this many chart
positions to qualify at all; a smaller move is real but not confidently
"early signal," so it's excluded rather than shown with invented
confidence.
"""

from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff

MIN_RANK_DELTA = 2
DEFAULT_LIMIT = 5


def select_early_signal_candidates(conn, report_date_kst, source_name, limit=DEFAULT_LIMIT):
    """Returns a list of candidate dicts, largest rank_delta first, each
    tagged source_name=source_name. Recomputes today's chart diff for this
    source (cheap, local -- no network call) to know exactly which
    entities belong to it and at which observed_at instant, then reads
    only already-persisted derived_signals VELOCITY rows for those
    entities at that instant (computes nothing new itself -- call
    music.derived_signals.compute_velocity_signals first for today's
    VELOCITY rows to exist). Never merges across sources: an entity that
    also happens to have a VELOCITY row from a different source is not
    considered here unless it's also in THIS source's own current diff."""
    if source_name not in ACTIVE_MUSIC_SOURCES:
        raise ValueError(f"{source_name!r} is not in music.registry.ACTIVE_MUSIC_SOURCES.")
    metric_name = ACTIVE_MUSIC_SOURCES[source_name]["metric_name"]
    diff = compute_chart_diff(conn, report_date_kst, source_name, metric_name)
    if diff["observed_at"] is None or not diff["entries"]:
        return []

    entity_ids = [e["music_entity_id"] for e in diff["entries"]]
    placeholders = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"""SELECT ds.music_entity_id, ds.value AS rank_delta, me.canonical_artist, me.canonical_title
           FROM derived_signals ds
           JOIN music_entities me ON me.id = ds.music_entity_id
           WHERE ds.signal_type = 'VELOCITY' AND ds.period_end = ? AND ds.value >= ?
             AND ds.music_entity_id IN ({placeholders})
           ORDER BY ds.value DESC, ds.music_entity_id ASC
           LIMIT ?""",
        (diff["observed_at"], MIN_RANK_DELTA, *entity_ids, limit),
    ).fetchall()
    return [
        {
            "source_name": source_name,
            "music_entity_id": row["music_entity_id"],
            "canonical_artist": row["canonical_artist"],
            "canonical_title": row["canonical_title"],
            "rank_delta": row["rank_delta"],
        }
        for row in rows
    ]

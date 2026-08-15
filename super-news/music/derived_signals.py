"""Source-agnostic derived-signal computation, writing to the already-
approved `derived_signals` table (db/schema.sql's frozen v5 architecture:
"OBSERVATION -> DERIVED SIGNAL -> TREND SIGNAL -> ..."). Zero rows existed
before this module -- this is the first writer for that table.

V1 scope: VELOCITY only, computed per currently-active registered source
(music.registry.ACTIVE_MUSIC_SOURCES) from the most recent chart-diff
snapshot pair (music.signal_engine.compute_chart_diff) -- works identically
regardless of which source is passed in, no source-specific code.
ACCELERATION/PERSISTENCE/CROSS_PLATFORM_CONFIRMATION/etc. are already valid
signal_type values in the schema's CHECK constraint but are NOT computed
here -- not needed for the Early Signal V1 slice this feeds, and adding
them speculatively isn't justified until a real consumer needs them.

Known simplification (documented, not hidden): music.signal_engine's
compute_chart_diff() doesn't currently return the PRIOR snapshot's
observed_at (only entries + today's observed_at), so period_start is set
equal to period_end here rather than a true two-instant window. This is a
valid zero-width window under the schema's CHECK(period_start <= period_end)
constraint, and the VELOCITY *value* (rank_delta) itself is unaffected --
only the window bookkeeping is approximate. Extending the engine's return
contract to carry the true prior timestamp is a small, additive follow-up,
not attempted here to avoid touching Stage 2's already-accepted, tested
compute_chart_diff() return shape.
"""

from datetime import datetime, timezone

from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff

METHOD_VERSION = "v1"
SIGNAL_TYPE_VELOCITY = "VELOCITY"


def compute_velocity_signals(conn, report_date_kst, source_name):
    """For one active registered source, computes today's chart diff and
    writes one VELOCITY derived_signals row per entity that has a non-null
    rank_delta (a brand-new entry has no velocity to compute yet -- that's
    a NEW-entry event, not a velocity event, and is skipped here). Positive
    value = moved up (matches compute_chart_diff's own sign convention).
    Idempotent: the existing ux_derived_signal UNIQUE constraint (entity,
    signal_type, period_start, period_end, method_version) makes a retry a
    no-op via INSERT OR IGNORE. Returns the number of rows actually
    inserted (excludes idempotent no-ops)."""
    if source_name not in ACTIVE_MUSIC_SOURCES:
        raise ValueError(f"{source_name!r} is not in music.registry.ACTIVE_MUSIC_SOURCES.")
    metric_name = ACTIVE_MUSIC_SOURCES[source_name]["metric_name"]
    diff = compute_chart_diff(conn, report_date_kst, source_name, metric_name)
    if diff["observed_at"] is None:
        return 0

    computed_at = datetime.now(timezone.utc).isoformat()
    period_start = period_end = diff["observed_at"]

    written = 0
    for entry in diff["entries"]:
        if entry["is_new"] or entry["rank_delta"] is None:
            continue
        cursor = conn.execute(
            """INSERT OR IGNORE INTO derived_signals
               (music_entity_id, signal_type, period_start, period_end, value,
                unit, computed_at, method_version)
               VALUES (?, ?, ?, ?, ?, 'rank_delta', ?, ?)""",
            (
                entry["music_entity_id"], SIGNAL_TYPE_VELOCITY, period_start, period_end,
                float(entry["rank_delta"]), computed_at, METHOD_VERSION,
            ),
        )
        if cursor.rowcount:
            written += 1
    conn.commit()
    return written

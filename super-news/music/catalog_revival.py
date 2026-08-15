"""Source-agnostic Catalog Revival detection -- gap-based approximation.

V1 scope, honestly limited: a true "catalog revival" claim (an OLD RELEASE
resurging long after its original release date) needs
music_entities.release_date, which does not exist in the schema yet -- a
known, still-unapproved gap from the V2 architecture design research. This
module implements only the NON-schema-dependent approximation: an entity
that (a) was first observed by SUPER NEWS at least MIN_AGE_DAYS ago, and
(b) had an observation GAP of at least MIN_GAP_DAYS immediately before
reappearing in today's chart, is flagged as a catalog-revival CANDIDATE
(precision="approximate"). This is a real, honest signal -- a previously-
tracked entity disappearing and coming back -- but is explicitly NOT the
same claim as "this is a genuinely old release resurging"; the more
precise, release-date-based version is isolated as BLOCKED_SCHEMA_APPROVAL
below and not implemented.
"""

from datetime import datetime

from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff

MIN_AGE_DAYS = 90
MIN_GAP_DAYS = 30

# The precise, release-date-based refinement requires a schema change
# (music_entities.release_date) that has not been approved -- isolated
# here so callers/reports can surface this status explicitly rather than
# silently pretending the approximate signal is the precise one.
RELEASE_DATE_PRECISION_STATUS = "BLOCKED_SCHEMA_APPROVAL"


def detect_catalog_revival_candidates(conn, report_date_kst, source_name,
                                       min_age_days=MIN_AGE_DAYS, min_gap_days=MIN_GAP_DAYS):
    """Returns a list of candidate dicts for entities in today's active
    chart diff for this source that show the age+gap revival pattern.
    Every result carries precision="approximate" -- never claimed as a
    verified release-date-based revival. Computes nothing about sources it
    isn't given; never merges across sources."""
    if source_name not in ACTIVE_MUSIC_SOURCES:
        raise ValueError(f"{source_name!r} is not in music.registry.ACTIVE_MUSIC_SOURCES.")
    metric_name = ACTIVE_MUSIC_SOURCES[source_name]["metric_name"]
    diff = compute_chart_diff(conn, report_date_kst, source_name, metric_name)
    if diff["observed_at"] is None:
        return []

    candidates = []
    for entry in diff["entries"]:
        entity_id = entry["music_entity_id"]
        entity_row = conn.execute(
            "SELECT first_seen_at FROM music_entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if entity_row is None:
            continue

        obs_rows = conn.execute(
            """SELECT DISTINCT observed_at FROM music_observations
               WHERE music_entity_id = ? AND source_name = ? ORDER BY observed_at ASC""",
            (entity_id, source_name),
        ).fetchall()
        if len(obs_rows) < 2:
            continue

        observed_ats = [datetime.fromisoformat(r["observed_at"]) for r in obs_rows]
        first_seen_at = datetime.fromisoformat(entity_row["first_seen_at"])
        age_days = (observed_ats[-1] - first_seen_at).days
        gap_days = (observed_ats[-1] - observed_ats[-2]).days

        if age_days >= min_age_days and gap_days >= min_gap_days:
            candidates.append({
                "source_name": source_name,
                "music_entity_id": entity_id,
                "canonical_artist": entry["canonical_artist"],
                "canonical_title": entry["canonical_title"],
                "age_days": age_days,
                "gap_days": gap_days,
                "precision": "approximate",
            })
    return candidates

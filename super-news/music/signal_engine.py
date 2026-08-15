"""Source-agnostic chart-position snapshot diff engine.

Computes a deterministic rank/delta diff between the most recent and the
immediately-previous observation snapshot for a given (source_name,
metric_name) pair in `music_observations`. This module has no knowledge of
any specific platform -- it never imports from `music.apple_music` or any
other collector module. The caller supplies which source/metric to diff
(see `music/registry.py` for which sources are currently active).

This is the "core engine" referenced by the V2 architecture design: adding
a new source does not require editing this file, only adding a collector
module (writing into music_observations with its own source_name) and one
entry in music/registry.py.
"""

from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))

# V2 canonical movement status (additive, alongside the pre-existing
# is_new/rank_delta fields -- report/music_diff.py, this engine's V1/Kakao
# consumer, reads only is_new/rank_delta and is completely unaffected by
# this addition; see SUPER_NEWS_HANDOFF.md's LEGACY_KNOWN_ISSUE entry for
# why V1 is left as-is). FIRST_OBSERVED is distinct from NEW: a NEW entry
# has a real absence-then-presence history (a genuine re-entry); a
# FIRST_OBSERVED entry has no real prior snapshot to compare against AT
# ALL (a baseline being established), so every V2 downstream consumer must
# check `status`, never `is_new` alone, to tell the two apart.
STATUS_FIRST_OBSERVED = "FIRST_OBSERVED"
STATUS_NEW = "NEW"
STATUS_UP = "UP"
STATUS_DOWN = "DOWN"
STATUS_FLAT = "FLAT"


def _kst_date_of(observed_at_iso):
    dt = datetime.fromisoformat(observed_at_iso).astimezone(_KST)
    return dt.strftime("%Y-%m-%d")


def _latest_snapshot_on_or_before(conn, report_date_kst, source_name, metric_name):
    """Returns (observed_at, [rows]) for the most recent snapshot of this
    (source_name, metric_name) whose KST calendar date is <=
    report_date_kst, or (None, []) if none exists."""
    rows = conn.execute(
        """SELECT DISTINCT observed_at FROM music_observations
           WHERE source_name = ? AND metric_name = ?
           ORDER BY observed_at DESC""",
        (source_name, metric_name),
    ).fetchall()
    for row in rows:
        if _kst_date_of(row["observed_at"]) <= report_date_kst:
            observed_at = row["observed_at"]
            snapshot_rows = conn.execute(
                """SELECT mo.music_entity_id, mo.metric_value AS rank, me.canonical_artist, me.canonical_title
                   FROM music_observations mo
                   JOIN music_entities me ON me.id = mo.music_entity_id
                   WHERE mo.source_name = ? AND mo.metric_name = ? AND mo.observed_at = ?""",
                (source_name, metric_name, observed_at),
            ).fetchall()
            return observed_at, list(snapshot_rows)
    return None, []


def _previous_snapshot_before(conn, observed_at, source_name, metric_name):
    if observed_at is None:
        return None, []
    row = conn.execute(
        """SELECT DISTINCT observed_at FROM music_observations
           WHERE source_name = ? AND metric_name = ? AND observed_at < ?
           ORDER BY observed_at DESC LIMIT 1""",
        (source_name, metric_name, observed_at),
    ).fetchone()
    if row is None:
        return None, []
    prev_observed_at = row["observed_at"]
    snapshot_rows = conn.execute(
        """SELECT mo.music_entity_id, mo.metric_value AS rank
           FROM music_observations mo
           WHERE mo.source_name = ? AND mo.metric_name = ? AND mo.observed_at = ?""",
        (source_name, metric_name, prev_observed_at),
    ).fetchall()
    return prev_observed_at, list(snapshot_rows)


def compute_chart_diff(conn, report_date_kst, source_name, metric_name):
    """Returns a dict: {"observed_at": str|None, "entries": [...]}. Each
    entry has rank, canonical_artist, canonical_title, and either
    rank_delta (int, positive == moved up) or is_new=True when the entity
    wasn't present in the prior snapshot. Entries are ordered by today's
    rank ascending. If there is no snapshot at all for
    (report_date_kst, source_name, metric_name), returns
    {"observed_at": None, "entries": []} -- never raises.

    Source-agnostic: identical algorithm regardless of which source_name/
    metric_name is passed in -- callers are responsible for choosing a
    metric whose values are directly rank-comparable (lower = better), the
    same assumption the original Apple-Music-only version made."""
    today_observed_at, today_rows = _latest_snapshot_on_or_before(
        conn, report_date_kst, source_name, metric_name
    )
    if today_observed_at is None:
        return {"observed_at": None, "entries": [], "is_first_observation": False}

    prev_observed_at, prev_rows = _previous_snapshot_before(
        conn, today_observed_at, source_name, metric_name
    )
    prev_rank_by_entity = {row["music_entity_id"]: row["rank"] for row in prev_rows}

    # True only when NO snapshot exists at all before today's -- i.e. this
    # is the very first observation ever for this (source, metric). Every
    # entry necessarily has no real prior rank on a day like this, which is
    # a baseline being established, not 10 simultaneous "NEW" chart entries
    # (that label is reserved for a genuine re-entry: present before,
    # absent from the immediately-previous snapshot, present again today --
    # see is_new below, unchanged for that case).
    is_first_observation = prev_observed_at is None

    entries = []
    for row in sorted(today_rows, key=lambda r: r["rank"]):
        entity_id = row["music_entity_id"]
        entry = {
            "music_entity_id": entity_id,
            "rank": int(row["rank"]),
            "canonical_artist": row["canonical_artist"],
            "canonical_title": row["canonical_title"],
        }
        if entity_id in prev_rank_by_entity:
            entry["is_new"] = False
            entry["rank_delta"] = int(prev_rank_by_entity[entity_id] - row["rank"])
        else:
            entry["is_new"] = True
            entry["rank_delta"] = None

        if is_first_observation:
            entry["status"] = STATUS_FIRST_OBSERVED
        elif entry["is_new"]:
            entry["status"] = STATUS_NEW
        elif entry["rank_delta"] > 0:
            entry["status"] = STATUS_UP
        elif entry["rank_delta"] < 0:
            entry["status"] = STATUS_DOWN
        else:
            entry["status"] = STATUS_FLAT
        entries.append(entry)

    return {"observed_at": today_observed_at, "entries": entries, "is_first_observation": is_first_observation}

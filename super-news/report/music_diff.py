"""Deterministic Apple KR most-played chart diff for the MUSIC report.

No AI involved -- this is a pure comparison of two chart_position
observation snapshots (today's vs. the most recent one strictly before it).
Deliberately excluded from LLM synthesis per the Report V1 design: rank
deltas are an exact, fully-explainable computation, and running them through
an LLM would only add cost and hallucination risk for zero benefit.
"""

from datetime import datetime, timedelta, timezone

from music.apple_music import METRIC_NAME, SOURCE_NAME

_KST = timezone(timedelta(hours=9))


def _kst_date_of(observed_at_iso):
    dt = datetime.fromisoformat(observed_at_iso).astimezone(_KST)
    return dt.strftime("%Y-%m-%d")


def _latest_snapshot_on_or_before(conn, report_date_kst):
    """Returns (observed_at, [rows]) for the most recent chart snapshot whose
    KST calendar date is <= report_date_kst, or (None, []) if none exists."""
    rows = conn.execute(
        """SELECT DISTINCT observed_at FROM music_observations
           WHERE source_name = ? AND metric_name = ?
           ORDER BY observed_at DESC""",
        (SOURCE_NAME, METRIC_NAME),
    ).fetchall()
    for row in rows:
        if _kst_date_of(row["observed_at"]) <= report_date_kst:
            observed_at = row["observed_at"]
            snapshot_rows = conn.execute(
                """SELECT mo.music_entity_id, mo.metric_value AS rank, me.canonical_artist, me.canonical_title
                   FROM music_observations mo
                   JOIN music_entities me ON me.id = mo.music_entity_id
                   WHERE mo.source_name = ? AND mo.metric_name = ? AND mo.observed_at = ?""",
                (SOURCE_NAME, METRIC_NAME, observed_at),
            ).fetchall()
            return observed_at, list(snapshot_rows)
    return None, []


def _previous_snapshot_before(conn, observed_at):
    if observed_at is None:
        return None, []
    rows = conn.execute(
        """SELECT DISTINCT observed_at FROM music_observations
           WHERE source_name = ? AND metric_name = ? AND observed_at < ?
           ORDER BY observed_at DESC LIMIT 1""",
        (SOURCE_NAME, METRIC_NAME, observed_at),
    ).fetchone()
    if rows is None:
        return None, []
    prev_observed_at = rows["observed_at"]
    snapshot_rows = conn.execute(
        """SELECT mo.music_entity_id, mo.metric_value AS rank
           FROM music_observations mo
           WHERE mo.source_name = ? AND mo.metric_name = ? AND mo.observed_at = ?""",
        (SOURCE_NAME, METRIC_NAME, prev_observed_at),
    ).fetchall()
    return prev_observed_at, list(snapshot_rows)


def compute_music_diff(conn, report_date_kst):
    """Returns a dict: {"observed_at": str|None, "entries": [...]}. Each
    entry has rank, canonical_artist, canonical_title, and either
    rank_delta (int, positive == moved up) or is_new=True when the entity
    wasn't present in the prior snapshot. Entries are ordered by today's
    rank ascending. If there is no snapshot at all for report_date_kst,
    returns {"observed_at": None, "entries": []} -- never raises."""
    today_observed_at, today_rows = _latest_snapshot_on_or_before(conn, report_date_kst)
    if today_observed_at is None:
        return {"observed_at": None, "entries": []}

    _prev_observed_at, prev_rows = _previous_snapshot_before(conn, today_observed_at)
    prev_rank_by_entity = {row["music_entity_id"]: row["rank"] for row in prev_rows}

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
        entries.append(entry)

    return {"observed_at": today_observed_at, "entries": entries}


def render_music_report(diff):
    """Deterministic Korean-language plain text rendering of compute_music_diff's
    output -- this is reports.content for category='MUSIC'."""
    if not diff["entries"]:
        return "오늘 Apple Music KR 차트 데이터가 없습니다."

    lines = ["Apple Music KR 최다 재생 차트"]
    for entry in diff["entries"]:
        if entry["is_new"]:
            marker = " (NEW)"
        elif entry["rank_delta"] > 0:
            marker = f" (▲{entry['rank_delta']})"
        elif entry["rank_delta"] < 0:
            marker = f" (▼{-entry['rank_delta']})"
        else:
            marker = ""
        lines.append(f"{entry['rank']}. {entry['canonical_artist']} - {entry['canonical_title']}{marker}")
    return "\n".join(lines)

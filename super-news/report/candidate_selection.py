"""News candidate selection for Report V1: NORMALIZED FACT -> a deterministic,
bounded candidate list per news category, ready to hand to the LLM.

Deterministic by construction: candidates are grouped by event_key (multiple
sources covering the same story collapse into one candidate), ordered by
(-source_count, event_key) -- ties broken lexicographically, never by
insertion/dict order -- so calling this twice against the same DB state
always returns the identical list in the identical order.

Stale-exclusion window is intentionally narrow: only event_keys the LLM
already SELECTED in the immediately-previous day's report for that category
are excluded (not all history). This stops the same story from being
re-surfaced the very next day without needing a growing "seen forever" set.
"""

from datetime import datetime, timedelta, timezone

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST) -- avoids the zoneinfo/tzdata
# dependency this project doesn't otherwise need.
_KST = timezone(timedelta(hours=9))


def _kst_day_bounds_utc(report_date_kst):
    """Returns (start_utc_iso, end_utc_iso) -- the half-open UTC instant
    range covering one KST calendar day, in the same isoformat() shape
    raw_items.collected_at is stored in (so plain string comparison is
    valid)."""
    y, m, d = (int(part) for part in report_date_kst.split("-"))
    start_kst = datetime(y, m, d, tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()


def _previous_kst_date(report_date_kst):
    y, m, d = (int(part) for part in report_date_kst.split("-"))
    return (datetime(y, m, d) - timedelta(days=1)).strftime("%Y-%m-%d")


def _excluded_event_keys(conn, category, previous_date):
    """event_keys the LLM already selected (interpretation_items) for this
    category's most recent report on `previous_date`. Empty set if no report
    was generated for that category on that date -- never an error."""
    report_row = conn.execute(
        """SELECT run_id FROM reports
           WHERE report_date = ? AND category = ?
           ORDER BY generated_at DESC LIMIT 1""",
        (previous_date, category),
    ).fetchone()
    if report_row is None:
        return set()

    rows = conn.execute(
        """SELECT DISTINCT ni.event_key
           FROM interpretation_items ii
           JOIN llm_interpretations li ON li.id = ii.interpretation_id
           JOIN normalized_items ni ON ni.id = ii.normalized_item_id
           WHERE li.run_id = ? AND ni.category = ?""",
        (report_row["run_id"], category),
    ).fetchall()
    return {row["event_key"] for row in rows}


def select_news_candidates(conn, categories, report_date_kst):
    """Returns dict category -> list[candidate dict], each list sorted
    deterministically. A category with zero eligible candidates gets an
    empty list -- never omitted from the returned dict."""
    start_utc, end_utc = _kst_day_bounds_utc(report_date_kst)
    previous_date = _previous_kst_date(report_date_kst)

    result = {}
    for category in categories:
        excluded = _excluded_event_keys(conn, category, previous_date)

        rows = conn.execute(
            """SELECT ni.id, ni.event_key, ni.entity_type, ni.entity_name,
                      ni.normalized_title, ri.source_name
               FROM normalized_items ni
               JOIN raw_items ri ON ri.id = ni.raw_item_id
               WHERE ni.category = ? AND ri.collected_at >= ? AND ri.collected_at < ?
               ORDER BY ni.id ASC""",
            (category, start_utc, end_utc),
        ).fetchall()

        groups = {}
        for row in rows:
            if row["event_key"] in excluded:
                continue
            group = groups.setdefault(
                row["event_key"],
                {
                    "event_key": row["event_key"],
                    "id": row["id"],
                    "entity_type": row["entity_type"],
                    "entity_name": row["entity_name"],
                    "normalized_title": row["normalized_title"],
                    "item_ids": [],
                    "source_names": set(),
                },
            )
            group["item_ids"].append(row["id"])
            group["source_names"].add(row["source_name"])

        candidates = []
        for group in groups.values():
            candidates.append(
                {
                    "id": group["id"],
                    "category": category,
                    "event_key": group["event_key"],
                    "entity_type": group["entity_type"],
                    "entity_name": group["entity_name"],
                    "normalized_title": group["normalized_title"],
                    "source_count": len(group["source_names"]),
                    "item_ids": sorted(group["item_ids"]),
                }
            )
        candidates.sort(key=lambda c: (-c["source_count"], c["event_key"]))
        result[category] = candidates

    return result

"""Structured, read-only data reader for the web dashboard renderer.

Reuses exactly the same persisted facts the Kakao renderer and delivery
layer already consume -- NOT a second intelligence/selection path. Reads:
- report_delivery.find_latest_report_run_id() for "the latest coherent run
  for this date" (the same resolution Kakao delivery uses, so the
  dashboard and the Kakao message always describe the same run).
- run_category_status for the real, already-persisted per-category
  pipeline state (status/items_collected/items_selected) -- this is what
  drives the NORMAL/QUIET/DEGRADED classification; nothing here computes
  a new judgment about data quality, it only reads what the pipeline
  already recorded about itself.
- llm_interpretations.output_text for the exact validated LLM selections
  ({id, reason} per news category) -- the same JSON report/persistence.py
  already parsed and validated; read again here in its structured form
  instead of after report/persistence.py flattens it into prose.
- normalized_items / raw_items for each selected item's title and
  (verbatim, never rewritten) source_url.
- report.music_diff.compute_music_diff() for music -- already fully
  structured, reused directly rather than parsed back out of its own
  rendered text.

This module never calls an LLM, never re-derives or re-ranks a selection,
and never invents a fact. It only reorganizes already-persisted data into
a shape report/web_render.py can lay out visually.
"""

import json

from report.music_diff import compute_music_diff
from report_delivery import find_latest_report_run_id

NEWS_CATEGORIES = ("AI", "ECONOMY", "SOCIETY")

_EMPTY_NEWS_STATE = "DEGRADED"


def _classify_state(status_row):
    """NORMAL / QUIET / DEGRADED, derived purely from already-persisted
    run_category_status columns -- see the module docstring. No status_row
    at all (shouldn't normally happen once a run exists, since
    persist_report_run always writes all 4 categories) is treated as
    DEGRADED defensively, never as NORMAL."""
    if status_row is None:
        return "DEGRADED"
    if status_row["status"] in ("REPORT_FAILED", "NOT_READY"):
        return "DEGRADED"
    items_collected = status_row["items_collected"] or 0
    items_selected = status_row["items_selected"] or 0
    if items_collected == 0:
        return "DEGRADED"
    if items_selected == 0:
        return "QUIET"
    return "NORMAL"


def _lookup_title_and_source_url(conn, normalized_item_id):
    row = conn.execute(
        """SELECT ni.normalized_title AS title, ri.source_url AS source_url
           FROM normalized_items ni
           JOIN raw_items ri ON ri.id = ni.raw_item_id
           WHERE ni.id = ?""",
        (normalized_item_id,),
    ).fetchone()
    if row is None:
        return None
    return {"title": row["title"], "source_url": row["source_url"]}


def _empty_categories():
    categories = {cat: {"state": _EMPTY_NEWS_STATE, "items": []} for cat in NEWS_CATEGORIES}
    categories["MUSIC"] = {"state": _EMPTY_NEWS_STATE, "entries": []}
    return categories


def build_dashboard_data(conn, report_date_kst):
    """Returns {"report_date_kst": ..., "categories": {category: {...}}}.

    Never raises for "nothing persisted yet" -- returns every category as
    DEGRADED with no items in that case, since an honestly-empty dashboard
    is a valid, renderable state (see Decision D), not an error condition
    the caller must special-case."""
    run_row_id = find_latest_report_run_id(conn, report_date_kst)
    if run_row_id is None:
        return {"report_date_kst": report_date_kst, "categories": _empty_categories()}

    status_rows = conn.execute(
        "SELECT category, status, items_collected, items_selected FROM run_category_status WHERE run_id = ?",
        (run_row_id,),
    ).fetchall()
    status_by_category = {row["category"]: row for row in status_rows}

    interp_row = conn.execute(
        "SELECT output_text FROM llm_interpretations WHERE run_id = ?", (run_row_id,)
    ).fetchone()
    selections_by_category = {}
    if interp_row is not None:
        try:
            parsed = json.loads(interp_row["output_text"])
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            selections_by_category = parsed

    categories = {}
    for category in NEWS_CATEGORIES:
        state = _classify_state(status_by_category.get(category))
        items = []
        for selection in selections_by_category.get(category) or []:
            if not isinstance(selection, dict) or "id" not in selection:
                continue
            lookup = _lookup_title_and_source_url(conn, selection["id"])
            if lookup is None:
                continue
            items.append({
                "title": lookup["title"],
                "reason": selection.get("reason"),
                "source_url": lookup["source_url"],
            })
        categories[category] = {"state": state, "items": items}

    music_state = _classify_state(status_by_category.get("MUSIC"))
    music_diff = compute_music_diff(conn, report_date_kst)
    categories["MUSIC"] = {"state": music_state, "entries": music_diff["entries"]}

    return {"report_date_kst": report_date_kst, "categories": categories}

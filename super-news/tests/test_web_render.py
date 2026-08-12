"""report.web_render: presentation-only dashboard rendering from the exact
structured shape report.web_data.build_dashboard_data() produces. No DB
access, no LLM call -- pure function of its input dict."""

import inspect

from report.web_render import (
    DEGRADED_MESSAGE,
    QUIET_MESSAGE,
    SECTION_LABELS,
    SECTION_ORDER,
    render_dashboard_html,
)


def _dashboard(categories):
    return {"report_date_kst": "2026-08-13", "categories": categories}


def _normal_ai(items=None):
    return {"state": "NORMAL", "items": items if items is not None else [
        {"title": "AI headline", "reason": "이유", "source_url": "https://example.com/a"}
    ]}


def _quiet():
    return {"state": "QUIET", "items": []}


def _degraded():
    return {"state": "DEGRADED", "items": []}


def _all_categories(ai=None, economy=None, society=None, music=None):
    return {
        "AI": ai or _degraded(),
        "ECONOMY": economy or _degraded(),
        "SOCIETY": society or _degraded(),
        "MUSIC": music or {"state": "DEGRADED", "entries": []},
    }


# ---- category order: AI -> MUSIC -> ECONOMY -> SOCIETY ---------------------


def test_section_order_constant_is_ai_music_economy_society():
    assert SECTION_ORDER == ("AI", "MUSIC", "ECONOMY", "SOCIETY")


def test_rendered_section_order_matches_constant():
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_normal_ai(), music={"state": "NORMAL", "entries": [
            {"rank": 1, "canonical_artist": "A", "canonical_title": "B", "is_new": True, "rank_delta": None}
        ]},
        economy=_normal_ai([{"title": "econ", "reason": None, "source_url": None}]),
        society=_normal_ai([{"title": "soc", "reason": None, "source_url": None}]),
    )))
    assert (
        html_out.index('id="section-AI"')
        < html_out.index('id="section-MUSIC"')
        < html_out.index('id="section-ECONOMY"')
        < html_out.index('id="section-SOCIETY"')
    )


# ---- three-state rendering: NORMAL / QUIET / DEGRADED never confused -------


def _extract_section(html_out, category):
    """Other categories default to DEGRADED in these tests (see
    _all_categories), so assertions must be scoped to the one section under
    test rather than the whole page -- otherwise a legitimately-degraded
    sibling section would cause false positives/negatives."""
    start = html_out.index(f'id="section-{category}"')
    end = html_out.index("</section>", start)
    return html_out[start:end]


def test_normal_state_renders_items_with_reason_and_link():
    html_out = render_dashboard_html(_dashboard(_all_categories(ai=_normal_ai())))
    section = _extract_section(html_out, "AI")
    assert "AI headline" in section
    assert "이유" in section
    assert 'href="https://example.com/a"' in section
    assert QUIET_MESSAGE not in section
    assert DEGRADED_MESSAGE not in section


def test_quiet_state_shows_quiet_message_not_degraded():
    html_out = render_dashboard_html(_dashboard(_all_categories(ai=_quiet())))
    section = _extract_section(html_out, "AI")
    assert QUIET_MESSAGE in section
    assert DEGRADED_MESSAGE not in section


def test_degraded_state_shows_degraded_message_not_quiet():
    html_out = render_dashboard_html(_dashboard(_all_categories(ai=_degraded())))
    section = _extract_section(html_out, "AI")
    assert DEGRADED_MESSAGE in section
    assert QUIET_MESSAGE not in section


def test_item_without_source_url_has_no_link():
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_normal_ai([{"title": "no link item", "reason": None, "source_url": None}])
    )))
    section = _extract_section(html_out, "AI")
    assert "no link item" in section
    # '.item-link' as a CSS class always exists in the stylesheet -- what
    # must be absent is an actual rendered link element.
    assert '<a class="item-link"' not in section


# ---- 오늘의 핵심: mechanical restatement, never fabricated -----------------


def test_key_points_uses_first_selected_item_only():
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_normal_ai([
            {"title": "first", "reason": "r1", "source_url": None},
            {"title": "second", "reason": "r2", "source_url": None},
        ])
    )))
    assert 'class="key-points"' in html_out
    key_points_start = html_out.index('class="key-points"')
    key_points_end = html_out.index("</ul>", key_points_start)
    key_points_block = html_out[key_points_start:key_points_end]
    assert "first" in key_points_block
    assert "second" not in key_points_block


def test_quiet_and_degraded_categories_omitted_from_key_points():
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_quiet(), economy=_degraded(), society=_degraded(),
    )))
    # No NORMAL category at all -> no key-points block should render.
    assert 'class="key-points"' not in html_out


def test_key_points_respects_section_order():
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_normal_ai([{"title": "AI item", "reason": None, "source_url": None}]),
        economy=_normal_ai([{"title": "ECON item", "reason": None, "source_url": None}]),
    )))
    key_points_start = html_out.index('class="key-points"')
    key_points_end = html_out.index("</ul>", key_points_start)
    key_points_block = html_out[key_points_start:key_points_end]
    assert key_points_block.index("AI item") < key_points_block.index("ECON item")


# ---- snapshot cards ----------------------------------------------------------


def test_snapshot_cards_present_for_all_four_categories():
    html_out = render_dashboard_html(_dashboard(_all_categories()))
    for category in SECTION_ORDER:
        assert f"card-{category}" in html_out


# ---- HTML escaping / security -----------------------------------------------


def test_html_escapes_dangerous_content():
    dangerous_title = "<script>alert(1)</script>"
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_normal_ai([{"title": dangerous_title, "reason": None, "source_url": None}])
    )))
    assert "<script>alert" not in html_out
    assert "&lt;script&gt;" in html_out


def test_dangerous_source_url_is_escaped_in_href():
    html_out = render_dashboard_html(_dashboard(_all_categories(
        ai=_normal_ai([{"title": "x", "reason": None, "source_url": 'https://x/"><script>alert(1)</script>'}])
    )))
    assert "<script>alert" not in html_out


def test_no_db_identifiers_possible_by_signature():
    # render_dashboard_html only accepts the dashboard_data dict -- no
    # separate run_id/report_id/content_hash parameter exists through
    # which an internal identifier could reach the output.
    sig = inspect.signature(render_dashboard_html)
    assert list(sig.parameters) == ["dashboard_data"]


# ---- responsive / no-framework requirements ---------------------------------


def test_mobile_viewport_present():
    html_out = render_dashboard_html(_dashboard(_all_categories()))
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html_out


def test_no_external_script_or_stylesheet():
    html_out = render_dashboard_html(_dashboard(_all_categories()))
    assert "<script" not in html_out
    assert '<link rel="stylesheet"' not in html_out


def test_desktop_frame_width_and_card_grid_present():
    html_out = render_dashboard_html(_dashboard(_all_categories()))
    assert "max-width: 800px" in html_out
    assert "grid-template-columns" in html_out
    assert "65ch" in html_out


def test_deterministic_across_calls():
    dashboard = _dashboard(_all_categories(ai=_normal_ai()))
    assert render_dashboard_html(dashboard) == render_dashboard_html(dashboard)


def test_all_section_order_categories_have_labels():
    for category in SECTION_ORDER:
        assert category in SECTION_LABELS


# ---- MUSIC detail: compact summary + notable subset + full-chart <details> --


def _music_entries_25():
    entries = []
    for rank in range(1, 26):
        entries.append({
            "music_entity_id": rank,
            "rank": rank,
            "canonical_artist": f"Artist{rank}",
            "canonical_title": f"Title{rank}",
            "is_new": False,
            "rank_delta": 0,
        })
    entries[0]["is_new"], entries[0]["rank_delta"] = True, None    # rank 1: NEW
    entries[1]["is_new"], entries[1]["rank_delta"] = True, None    # rank 2: NEW
    entries[2]["rank_delta"] = 20   # rank 3: biggest up
    entries[3]["rank_delta"] = -18  # rank 4: biggest down
    entries[4]["rank_delta"] = 5    # rank 5: smaller up
    return entries


def _normal_music(entries):
    return {"state": "NORMAL", "entries": entries}


def _music_section_and_details_split(html_out, entries):
    section = _extract_section(html_out, "MUSIC")
    details_start = section.index("<details")
    return section, section[:details_start], section[details_start:]


def test_music_full_chart_contains_all_25_entries_inside_details():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    _, _, details_block = _music_section_and_details_split(html_out, entries)
    for entry in entries:
        assert f"Title{entry['rank']}" in details_block


def test_music_only_compact_notable_subset_visible_outside_details():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    _, before_details, _ = _music_section_and_details_split(html_out, entries)
    for rank in (1, 2, 3, 4, 5):
        assert f"Title{rank}" in before_details
    for rank in range(6, 26):
        assert f"Title{rank}" not in before_details


def test_music_notable_ordering_is_new_first_then_largest_abs_delta():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    _, before_details, _ = _music_section_and_details_split(html_out, entries)
    order = [f"Title{r}" for r in (1, 2, 3, 4, 5)]
    positions = [before_details.index(title) for title in order]
    assert positions == sorted(positions)


def test_music_change_summary_counts_from_existing_fields():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    section = _extract_section(html_out, "MUSIC")
    assert "신규 진입 2" in section
    assert "상승 2" in section
    assert "하락 1" in section


def test_music_details_collapsed_by_default():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    section = _extract_section(html_out, "MUSIC")
    details_start = section.index("<details")
    details_tag_end = section.index(">", details_start)
    details_open_tag = section[details_start:details_tag_end + 1]
    assert "open" not in details_open_tag


def test_music_full_chart_has_no_javascript():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    assert "<script" not in html_out
    assert "onclick" not in html_out.lower()


def test_music_snapshot_card_unchanged_by_detail_refinement():
    entries = _music_entries_25()
    html_out = render_dashboard_html(_dashboard(_all_categories(music=_normal_music(entries))))
    assert 'class="card card-MUSIC state-normal"' in html_out
    assert "25건" in html_out

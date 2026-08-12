"""Presentation-only web dashboard renderer for Report V1.

Consumes ONLY the structured facts report.web_data.build_dashboard_data()
already reads from persisted data. This module makes zero independent
judgment about what matters: no new scores, rankings, summaries, or
selections -- it only lays out already-selected facts visually.

- "오늘의 핵심" (today's key points) is a mechanical restatement of the
  FIRST already-selected item per category (in SECTION_ORDER) -- never a
  new synthesis. A QUIET or DEGRADED category is simply omitted from it,
  never filled with a fabricated point.
- The three-state (NORMAL/QUIET/DEGRADED) rendering always shows the
  persisted reality honestly -- a DEGRADED category never looks like an
  ordinary empty section, and a QUIET one is never confused with DEGRADED.
- Source links use raw_items.source_url exactly as persisted -- never
  rewritten, shortened, or fabricated; an item without one simply has no
  link.

No JS, no external framework/fonts/scripts, single inline stylesheet. No
DB identifiers (run_id/report_id/content_hash/status enum names) are ever
passed into or referenced by this module -- its only inputs are category
labels, titles, reasons, source URLs, and music chart facts.
"""

import html

SECTION_ORDER = ("AI", "MUSIC", "ECONOMY", "SOCIETY")
SECTION_LABELS = {"AI": "AI", "MUSIC": "음악", "ECONOMY": "경제", "SOCIETY": "사회"}

QUIET_MESSAGE = "오늘 선별된 주요 이슈가 없습니다."
DEGRADED_MESSAGE = "현재 데이터 수집 문제로 이 섹션의 브리핑이 제한됩니다."

# Single inline stylesheet -- no external framework, no build step, no JS.
# Mobile: everything is a natural single block-flow column under ~800px
# viewports (no explicit mobile media query needed -- the grid and max-width
# rules below only matter once there's room for them). Desktop: an ~800px
# outer frame (not a full-bleed stretch) with a responsive auto-fit card
# grid for the Level-2 snapshot, and prose constrained to ~65ch so detail
# text stays comfortable to read regardless of viewport width.
_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  margin: 0; padding: 20px 16px 48px; line-height: 1.65; font-size: 17px;
}
header { max-width: 800px; margin: 0 auto 24px; }
.brand { font-size: 0.8rem; font-weight: 700; letter-spacing: 0.12em; opacity: 0.6; }
.date { font-size: 1.7rem; font-weight: 800; margin: 2px 0 16px; }

ul.key-points { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
li.key-point { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 10px 12px; border-radius: 10px; background: rgba(128,128,128,0.08); }
.key-label { font-size: 0.75rem; font-weight: 700; opacity: 0.6; min-width: 3.2em; }
.key-title { font-weight: 600; }
.key-sub { font-size: 0.85rem; opacity: 0.65; flex-basis: 100%; }

.snapshot-grid {
  max-width: 800px; margin: 0 auto 32px;
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px;
}
.card { display: block; padding: 14px; border-radius: 12px; text-decoration: none; color: inherit; background: rgba(128,128,128,0.08); min-height: 44px; }
.card-label { font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; }
.card-teaser { margin-top: 4px; font-size: 0.9rem; opacity: 0.75; }
.state-degraded .card-teaser { color: #b91c1c; opacity: 1; }
.state-quiet .card-teaser { opacity: 0.55; }

main { max-width: 800px; margin: 0 auto; }
section.category { margin-bottom: 34px; padding-bottom: 24px; border-bottom: 1px solid rgba(128,128,128,0.2); }
section.category:last-of-type { border-bottom: none; }
section.category h2 { font-size: 1rem; font-weight: 800; margin: 0 0 12px; text-transform: uppercase; letter-spacing: 0.06em; }
.category-AI h2 { color: #2563eb; }
.category-MUSIC h2 { color: #db2777; }
.category-ECONOMY h2 { color: #059669; }
.category-SOCIETY h2 { color: #d97706; }

.item-list, .music-list { list-style: none; margin: 0; padding: 0; }
.item { max-width: 65ch; margin-bottom: 18px; }
.item-title { font-weight: 700; margin: 0 0 4px; }
.item-reason { margin: 0 0 6px; opacity: 0.8; font-size: 0.95rem; max-width: 65ch; }
.item-link { display: inline-block; padding: 6px 0; font-size: 0.9rem; color: #2563eb; text-decoration: none; min-height: 44px; line-height: 32px; }
.item-link:hover, .item-link:active { text-decoration: underline; }

.music-row { display: flex; align-items: baseline; gap: 10px; padding: 8px 0; border-bottom: 1px solid rgba(128,128,128,0.12); }
.music-row:last-child { border-bottom: none; }
.music-rank { font-weight: 800; opacity: 0.5; min-width: 1.6em; }
.music-track { flex: 1; }
.badge { font-size: 0.75rem; font-weight: 700; padding: 2px 6px; border-radius: 6px; }
.badge-new { background: #dbeafe; color: #1d4ed8; }
.badge-up { background: #dcfce7; color: #15803d; }
.badge-down { background: #fee2e2; color: #b91c1c; }

.section-label { font-size: 0.8rem; font-weight: 700; opacity: 0.6; margin: 0 0 8px; }
.music-summary { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 14px; }
.music-summary-item { padding: 6px 10px; border-radius: 8px; background: rgba(128,128,128,0.08); font-size: 0.85rem; font-weight: 600; }
details.music-full-chart summary { cursor: pointer; padding: 12px 4px; min-height: 44px; line-height: 20px; font-weight: 700; font-size: 0.9rem; }
details.music-full-chart[open] summary { margin-bottom: 4px; }

.state-message { max-width: 65ch; padding: 12px 14px; border-radius: 10px; background: rgba(128,128,128,0.08); font-size: 0.95rem; }
.state-message.state-degraded { color: #b91c1c; }

footer { max-width: 800px; margin: 36px auto 0; font-size: 0.75rem; opacity: 0.5; }
"""


def _e(text):
    return html.escape(text) if text else ""


def _category_key_point(category, data):
    """Returns (title, sub) for one 오늘의 핵심 line, or None to omit this
    category -- QUIET/DEGRADED are always omitted, never faked."""
    if data["state"] != "NORMAL":
        return None
    if category == "MUSIC":
        entries = data.get("entries") or []
        if not entries:
            return None
        top = entries[0]
        sub = "NEW" if top["is_new"] else None
        return f"{top['canonical_artist']} - {top['canonical_title']}", sub
    items = data.get("items") or []
    if not items:
        return None
    first = items[0]
    return first["title"], first.get("reason")


def _render_key_points(categories):
    points = []
    for category in SECTION_ORDER:
        point = _category_key_point(category, categories[category])
        if point is None:
            continue
        title, sub = point
        label = _e(SECTION_LABELS[category])
        sub_html = f'<span class="key-sub">{_e(sub)}</span>' if sub else ""
        points.append(
            f'<li class="key-point"><span class="key-label">{label}</span>'
            f'<span class="key-title">{_e(title)}</span>{sub_html}</li>'
        )
    if not points:
        return ""
    return '<ul class="key-points">\n' + "\n".join(points) + "\n</ul>"


def _render_snapshot_cards(categories):
    cards = []
    for category in SECTION_ORDER:
        data = categories[category]
        state = data["state"]
        label = _e(SECTION_LABELS[category])
        if state == "DEGRADED":
            teaser = DEGRADED_MESSAGE
        elif state == "QUIET":
            teaser = QUIET_MESSAGE
        else:
            count = len(data["entries"]) if category == "MUSIC" else len(data["items"])
            teaser = f"{count}건"
        cards.append(
            f'<a class="card card-{category} state-{state.lower()}" href="#section-{category}">\n'
            f'<div class="card-label">{label}</div>\n'
            f'<div class="card-teaser">{_e(teaser)}</div>\n'
            f"</a>"
        )
    return '<div class="snapshot-grid">\n' + "\n".join(cards) + "\n</div>"


def _render_news_section(category, data):
    label = _e(SECTION_LABELS[category])
    state = data["state"]
    if state == "DEGRADED":
        body = f'<p class="state-message state-degraded">{_e(DEGRADED_MESSAGE)}</p>'
    elif state == "QUIET":
        body = f'<p class="state-message state-quiet">{_e(QUIET_MESSAGE)}</p>'
    else:
        rows = []
        for item in data["items"]:
            reason_html = f'<p class="item-reason">{_e(item["reason"])}</p>' if item.get("reason") else ""
            link_html = ""
            if item.get("source_url"):
                # Verbatim persisted value -- never rewritten/shortened/inferred.
                safe_url = html.escape(item["source_url"], quote=True)
                link_html = f'<a class="item-link" href="{safe_url}" rel="noopener noreferrer">원문 보기 →</a>'
            rows.append(
                f'<li class="item">\n<p class="item-title">{_e(item["title"])}</p>\n{reason_html}\n{link_html}\n</li>'
            )
        body = '<ul class="item-list">\n' + "\n".join(rows) + "\n</ul>"
    return f'<section class="category category-{category}" id="section-{category}">\n<h2>{label}</h2>\n{body}\n</section>'


def _music_change_counts(entries):
    """Counts derived purely from existing is_new/rank_delta fields -- no
    inference. Unchanged entries (rank_delta == 0) count toward neither."""
    new_count = sum(1 for e in entries if e["is_new"])
    up_count = sum(1 for e in entries if not e["is_new"] and (e.get("rank_delta") or 0) > 0)
    down_count = sum(1 for e in entries if not e["is_new"] and (e.get("rank_delta") or 0) < 0)
    return new_count, up_count, down_count


def _select_notable_music_entries(entries, limit=5):
    """Deterministic subset: NEW entries first, then largest absolute
    rank_delta, tie-broken by rank ascending. Purely mechanical from
    is_new/rank_delta/rank -- no invented importance."""
    def sort_key(entry):
        is_new = entry["is_new"]
        delta_magnitude = 0 if is_new else abs(entry.get("rank_delta") or 0)
        return (0 if is_new else 1, -delta_magnitude, entry["rank"])

    return sorted(entries, key=sort_key)[:limit]


def _render_music_row(entry):
    delta = entry.get("rank_delta")
    if entry["is_new"]:
        marker = '<span class="badge badge-new">NEW</span>'
    elif delta and delta > 0:
        marker = f'<span class="badge badge-up">▲{delta}</span>'
    elif delta and delta < 0:
        marker = f'<span class="badge badge-down">▼{-delta}</span>'
    else:
        marker = ""
    return (
        f'<li class="music-row"><span class="music-rank">{entry["rank"]}</span>'
        f'<span class="music-track">{_e(entry["canonical_artist"])} - {_e(entry["canonical_title"])}</span>'
        f"{marker}</li>"
    )


def _render_music_section(data):
    label = _e(SECTION_LABELS["MUSIC"])
    state = data["state"]
    if state != "NORMAL":
        message = DEGRADED_MESSAGE if state == "DEGRADED" else QUIET_MESSAGE
        body = f'<p class="state-message state-{state.lower()}">{_e(message)}</p>'
    else:
        entries = data["entries"]
        new_count, up_count, down_count = _music_change_counts(entries)
        summary_html = (
            '<div class="music-summary">'
            f'<span class="music-summary-item">신규 진입 {new_count}</span>'
            f'<span class="music-summary-item">상승 {up_count}</span>'
            f'<span class="music-summary-item">하락 {down_count}</span>'
            "</div>"
        )
        notable_html = (
            '<ul class="music-list">\n'
            + "\n".join(_render_music_row(e) for e in _select_notable_music_entries(entries))
            + "\n</ul>"
        )
        full_html = (
            '<ul class="music-list">\n'
            + "\n".join(_render_music_row(e) for e in entries)
            + "\n</ul>"
        )
        body = (
            f'<p class="section-label">오늘의 주요 변화</p>\n{summary_html}\n{notable_html}\n'
            f'<details class="music-full-chart">\n<summary>전체 차트 {len(entries)}곡 보기</summary>\n{full_html}\n</details>'
        )
    return f'<section class="category category-MUSIC" id="section-MUSIC">\n<h2>{label}</h2>\n{body}\n</section>'


def render_dashboard_html(dashboard_data):
    """dashboard_data: the exact shape report.web_data.build_dashboard_data()
    returns. Returns a complete, self-contained HTML document string."""
    report_date_kst = dashboard_data["report_date_kst"]
    categories = dashboard_data["categories"]
    y, m, d = report_date_kst.split("-")

    key_points_html = _render_key_points(categories)
    snapshot_html = _render_snapshot_cards(categories)

    sections = []
    for category in SECTION_ORDER:
        if category == "MUSIC":
            sections.append(_render_music_section(categories["MUSIC"]))
        else:
            sections.append(_render_news_section(category, categories[category]))
    sections_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SUPER NEWS — {y}.{m}.{d}</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
<div class="brand">SUPER NEWS</div>
<div class="date">{y}.{m}.{d}</div>
{key_points_html}
</header>
{snapshot_html}
<main>
{sections_html}
</main>
<footer>이 페이지는 매일 자동으로 갱신됩니다.</footer>
</body>
</html>
"""

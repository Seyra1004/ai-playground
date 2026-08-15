"""Deterministic MUSIC-report chart diff -- report-layer entry point.

No AI involved -- this is a pure comparison of two observation snapshots
(today's vs. the most recent one strictly before it). Deliberately excluded
from LLM synthesis per the Report V1 design: rank deltas are an exact,
fully-explainable computation, and running them through an LLM would only
add cost and hallucination risk for zero benefit.

V2 note: the actual diff algorithm is source-agnostic and lives in
music.signal_engine.compute_chart_diff -- this module only decides WHICH
source's diff currently becomes the report's MUSIC content. Today that's
the single entry in music.registry.ACTIVE_MUSIC_SOURCES (apple_music).
Whether/how to combine multiple sources into one report is an output-layer
product decision, not yet approved -- see the V2 architecture design doc --
so this module still returns exactly one source's diff, unchanged in shape
from before this refactor. Adding a second active source does not require
editing this function's signature or music.signal_engine.
"""

from music.registry import ACTIVE_MUSIC_SOURCES
from music.signal_engine import compute_chart_diff

# The report's current single music source. Not a product decision to
# combine multiple sources -- see module docstring.
_PRIMARY_SOURCE_NAME = "apple_music"


def compute_music_diff(conn, report_date_kst):
    """Returns a dict: {"observed_at": str|None, "entries": [...]}. Each
    entry has rank, canonical_artist, canonical_title, and either
    rank_delta (int, positive == moved up) or is_new=True when the entity
    wasn't present in the prior snapshot. Entries are ordered by today's
    rank ascending. If there is no snapshot at all for report_date_kst,
    returns {"observed_at": None, "entries": []} -- never raises."""
    metric_name = ACTIVE_MUSIC_SOURCES[_PRIMARY_SOURCE_NAME]["metric_name"]
    return compute_chart_diff(conn, report_date_kst, _PRIMARY_SOURCE_NAME, metric_name)


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

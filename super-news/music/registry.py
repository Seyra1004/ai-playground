"""Minimal registry of currently-active music sources for the chart-diff
engine (music/signal_engine.py).

Deliberately not YAML-driven like ingestion/registry.py: two active
sources today, and a bigger declarative config format isn't justified
until a third source actually exists and its metric semantics are known.
Adding a source later means: (1) a collector module following music/
apple_music.py's pattern, writing into music_observations with its own
source_name, and (2) one new entry here -- no change to music/
signal_engine.py.

`metric_name` here must be a rank-comparable metric (lower value = better),
the same assumption music.signal_engine.compute_chart_diff makes -- a
future source whose native metric isn't rank-shaped (e.g. a raw view
count) needs its own decision about what "rank" means for it before it can
be added; not resolved here.

YouTube Music -- deliberately NOT added (2026-08-14 research pass, see
super-news project history for the full source-acceptance matrix): No
sufficiently reliable official YouTube Music chart source is currently
available without either credentialed non-chart metrics or undocumented
scraping risk. Concretely: charts.youtube.com's real chart data is served
through YouTube's private/undocumented "Innertube" API (confirmed by
direct probe -- an embedded, session-gated INNERTUBE_API_KEY, not a
public JSON service the way Spotify's charts-spotify-com-service is) --
unofficial-scraping risk, explicitly excluded by this project's own
source-reliability rule. The one official, documented alternative
(YouTube Data API v3's `videos.list(chart=mostPopular)`) requires a new
Google API credential AND only exposes "trending videos" ordering, not a
curated chart rank or a previous-rank field -- it cannot honestly satisfy
this registry's rank-comparable `metric_name` contract without
mislabeling a trending signal as a chart rank. Revisit only with an
explicit decision to provision real credentials and model it as a
distinct (non-rank) metric type -- not decided here, and not a blocker
for Spotify + Apple Music cross-platform work in the meantime.
"""

ACTIVE_MUSIC_SOURCES = {
    "apple_music": {
        "metric_name": "apple_music_chart_position",
        "display_name": "Apple Music",
        "quality_tier": "TIER_1",
    },
    "spotify_chart": {
        "metric_name": "spotify_chart_rank",
        "display_name": "Spotify",
        "quality_tier": "TIER_1",
    },
}

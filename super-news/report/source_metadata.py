"""Single source of truth for source display metadata (display_name,
quality_tier), merging ingestion/sources.yaml (news adapters) with
music/registry.py (chart sources) into ONE lookup keyed by source_name.

Neither report/candidate_selection.py's ranking nor report/web_render_v2.py's
rendering keeps its own second copy of this mapping any more -- adding a new
source to sources.yaml (or a new chart collector to music/registry.py) is
enough for it to rank and render correctly with no renderer code change.

An unknown/unmapped source_name is never hidden: display_name falls back to
the raw source_name (visible-but-ugly beats silently hiding provenance, the
same rule report/web_render_v2.py's own docstring already committed to), and
quality_tier falls back to a neutral score (never assumed high or low).
"""

from functools import lru_cache
from pathlib import Path

import yaml

from ingestion.registry import load_source_registry
from music.registry import ACTIVE_MUSIC_SOURCES

SOURCES_YAML_PATH = Path(__file__).resolve().parent.parent / "sources.yaml"

QUALITY_TIER_SCORE = {"TIER_1": 1.0, "TIER_2": 0.8, "TIER_3": 0.6, "TIER_4": 0.4}
DEFAULT_QUALITY_SCORE = 0.5  # neutral -- an unknown/unmapped tier is never assumed best or worst.


class SourceMetadataValidationError(RuntimeError):
    """Raised by validate_active_source_metadata when an ENABLED source is
    missing display_name and/or quality_tier. ingestion/registry.py's
    SourceConfig deliberately keeps both fields OPTIONAL (defaulting
    display_name to the raw source_name) so a minimal dev/test fixture
    SourceConfig still constructs without them -- that leniency must never
    extend to a real production run, where a missing display_name means a
    raw internal adapter key would be exposed to a reader, and a missing
    quality_tier means the ranking signal silently falls back to a neutral
    default for a source that should have a real one. This is the
    production FAIL gate: called once, at generation time, never silently
    skipped."""


def validate_active_source_metadata(sources_yaml_path=None):
    """Raises SourceMetadataValidationError (listing every real gap) if any
    ENABLED sources.yaml source or any music.registry.ACTIVE_MUSIC_SOURCES
    chart source is missing display_name and/or a known quality_tier.
    Returns None (silently) when coverage is complete -- never raises for a
    DISABLED source (an inactive source can never expose anything to a
    real reader).

    Reads sources.yaml's raw parsed dict directly (not through
    ingestion.registry.SourceConfig, whose loader already applies the
    lenient default) -- this is what lets this function catch a source
    entry that never set display_name/quality_tier at all, which
    SourceConfig's own optional-field fallback would otherwise silently
    paper over."""
    path = sources_yaml_path or SOURCES_YAML_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw_doc = yaml.safe_load(f)

    missing = []
    for raw in (raw_doc or {}).get("sources", []) or []:
        if not raw.get("enabled"):
            continue
        name = raw.get("source_name", "<unknown>")
        if not raw.get("display_name"):
            missing.append(f"{name}: missing display_name (sources.yaml)")
        if raw.get("quality_tier") not in QUALITY_TIER_SCORE:
            missing.append(f"{name}: missing/invalid quality_tier (sources.yaml)")

    for name, cfg in ACTIVE_MUSIC_SOURCES.items():
        if not cfg.get("display_name"):
            missing.append(f"{name}: missing display_name (music.registry)")
        if cfg.get("quality_tier") not in QUALITY_TIER_SCORE:
            missing.append(f"{name}: missing/invalid quality_tier (music.registry)")

    if missing:
        raise SourceMetadataValidationError(
            "Active source metadata coverage incomplete:\n" + "\n".join(missing)
        )


@lru_cache(maxsize=1)
def _load(sources_yaml_path=None):
    """Cached (process-lifetime) merged lookup: source_name -> {display_name,
    quality_tier}. Cache key includes the path so tests overriding it via
    the public functions' `sources_yaml_path` argument never see another
    test's cached registry."""
    path = sources_yaml_path or SOURCES_YAML_PATH
    metadata = {}
    for name, cfg in load_source_registry(path).items():
        metadata[name] = {"display_name": cfg.display_name, "quality_tier": cfg.quality_tier}
    for name, cfg in ACTIVE_MUSIC_SOURCES.items():
        metadata[name] = {"display_name": cfg["display_name"], "quality_tier": cfg["quality_tier"]}
    return metadata


def source_metadata(source_name, sources_yaml_path=None):
    return _load(sources_yaml_path).get(source_name)


def source_display_name(source_name, sources_yaml_path=None):
    meta = source_metadata(source_name, sources_yaml_path)
    return meta["display_name"] if meta else source_name


def source_quality_tier(source_name, sources_yaml_path=None):
    meta = source_metadata(source_name, sources_yaml_path)
    return meta["quality_tier"] if meta else None


def source_quality_score(source_name, sources_yaml_path=None):
    """Real numeric ranking signal (report/candidate_selection.py) derived
    from quality_tier -- never invented for a source with no tier on
    record, which gets the neutral DEFAULT_QUALITY_SCORE instead of being
    assumed authoritative or unreliable."""
    tier = source_quality_tier(source_name, sources_yaml_path)
    return QUALITY_TIER_SCORE.get(tier, DEFAULT_QUALITY_SCORE)

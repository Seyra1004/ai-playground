"""Source registry: loads sources.yaml into validated SourceConfig objects.

Declarative only — this module knows WHAT a source is (name, type, category,
endpoint, timeout/retry policy, which env vars hold its credentials). It has
no knowledge of HOW to fetch from it; adapter resolution (source_type ->
adapter function) is a separate, explicit map in
ingestion/adapters/__init__.py. This module never reads or stores a secret
VALUE — only the env var NAME a source's credentials live under
(`credential_env`).

Validation happens entirely at load time (fail fast with SourceRegistryError)
so a broken config is caught before any adapter runs, not partway through a
run.
"""

from dataclasses import dataclass, field

import yaml

KNOWN_SOURCE_TYPES = {"rss", "naver_news_api"}
KNOWN_CATEGORIES = {
    "AI_NEWS", "ECONOMY_NEWS", "SOCIETY_NEWS",
    "TIKTOK_NEWS", "SPOTIFY_NEWS", "MUSIC_INDUSTRY_NEWS",
}
KNOWN_AUTH_MODES = {"none", "api_key_pair"}
# TIER_1 = primary/official newsroom or platform, TIER_2 = established major
# newsroom, TIER_3 = reputable specialist/trade press or an aggregator API,
# TIER_4 = secondary/aggregator (e.g. a search-RSS proxy). See sources.yaml's
# own header comment for the full rubric -- this is a ranking SIGNAL
# (report/candidate_selection.py), never a hard ordering rule by itself.
KNOWN_QUALITY_TIERS = {"TIER_1", "TIER_2", "TIER_3", "TIER_4"}


class SourceRegistryError(ValueError):
    """Raised for any malformed sources.yaml — missing/invalid fields,
    unknown source_type/category, or duplicate source_name. Never includes
    a secret value (the registry never holds one)."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_base_seconds: float
    backoff_jitter_seconds: float


@dataclass(frozen=True)
class SourceConfig:
    source_name: str
    enabled: bool
    source_type: str
    category: str
    region: str
    endpoint: str
    timeout_seconds: float
    retry: RetryPolicy
    auth_mode: str
    # Both optional with a None default -- existing callers that construct
    # SourceConfig directly (tests predating this field) are unaffected.
    # load_source_registry always resolves display_name to a real string
    # (defaulting to source_name) before constructing one; __post_init__
    # below applies that SAME fallback here too, so a directly-constructed
    # SourceConfig with display_name left at None never surprises a caller
    # that reads .display_name expecting a usable string.
    display_name: str = None
    quality_tier: str = None
    credential_env: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.display_name is None:
            object.__setattr__(self, "display_name", self.source_name)


def _require(mapping, key, source_label):
    if key not in mapping or mapping[key] is None:
        raise SourceRegistryError(f"{source_label}: missing required field '{key}'.")
    return mapping[key]


def _parse_retry(raw_retry, source_label):
    if not isinstance(raw_retry, dict):
        raise SourceRegistryError(f"{source_label}: 'retry' must be a mapping.")
    max_attempts = _require(raw_retry, "max_attempts", source_label)
    backoff_base = _require(raw_retry, "backoff_base_seconds", source_label)
    backoff_jitter = _require(raw_retry, "backoff_jitter_seconds", source_label)

    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise SourceRegistryError(
            f"{source_label}: retry.max_attempts must be an int >= 1, got {max_attempts!r}."
        )
    if not isinstance(backoff_base, (int, float)) or isinstance(backoff_base, bool) or backoff_base <= 0:
        raise SourceRegistryError(
            f"{source_label}: retry.backoff_base_seconds must be a positive number, got {backoff_base!r}."
        )
    if not isinstance(backoff_jitter, (int, float)) or isinstance(backoff_jitter, bool) or backoff_jitter < 0:
        raise SourceRegistryError(
            f"{source_label}: retry.backoff_jitter_seconds must be a non-negative number, got {backoff_jitter!r}."
        )
    return RetryPolicy(
        max_attempts=max_attempts,
        backoff_base_seconds=float(backoff_base),
        backoff_jitter_seconds=float(backoff_jitter),
    )


def _parse_auth(raw_auth, source_label):
    if not isinstance(raw_auth, dict):
        raise SourceRegistryError(f"{source_label}: 'auth' must be a mapping.")
    mode = _require(raw_auth, "mode", source_label)
    if mode not in KNOWN_AUTH_MODES:
        raise SourceRegistryError(
            f"{source_label}: unknown auth.mode {mode!r} (known: {sorted(KNOWN_AUTH_MODES)})."
        )
    credential_env = raw_auth.get("credential_env", {})
    if not isinstance(credential_env, dict):
        raise SourceRegistryError(f"{source_label}: auth.credential_env must be a mapping.")
    for k, v in credential_env.items():
        if not isinstance(v, str) or not v:
            raise SourceRegistryError(
                f"{source_label}: auth.credential_env.{k} must name a non-empty env var, got {v!r}."
            )
    if mode == "api_key_pair" and not credential_env:
        raise SourceRegistryError(
            f"{source_label}: auth.mode='api_key_pair' requires a non-empty credential_env mapping."
        )
    return mode, credential_env


def _parse_source(raw, index):
    label = f"sources[{index}]"
    source_name = _require(raw, "source_name", label)
    if not isinstance(source_name, str) or not source_name:
        raise SourceRegistryError(f"{label}: source_name must be a non-empty string.")
    if source_name != source_name.lower() or " " in source_name:
        raise SourceRegistryError(
            f"{label}: source_name {source_name!r} must be a stable lowercase machine-readable identifier."
        )
    label = f"source '{source_name}'"

    enabled = _require(raw, "enabled", label)
    if not isinstance(enabled, bool):
        raise SourceRegistryError(f"{label}: enabled must be a boolean, got {enabled!r}.")

    source_type = _require(raw, "source_type", label)
    if source_type not in KNOWN_SOURCE_TYPES:
        raise SourceRegistryError(
            f"{label}: unknown source_type {source_type!r} (known: {sorted(KNOWN_SOURCE_TYPES)})."
        )

    category = _require(raw, "category", label)
    if category not in KNOWN_CATEGORIES:
        raise SourceRegistryError(
            f"{label}: unknown category {category!r} (known: {sorted(KNOWN_CATEGORIES)})."
        )

    endpoint = _require(raw, "endpoint", label)
    if not isinstance(endpoint, str) or not endpoint:
        raise SourceRegistryError(f"{label}: endpoint must be a non-empty string.")

    timeout_seconds = _require(raw, "timeout_seconds", label)
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise SourceRegistryError(
            f"{label}: timeout_seconds must be a positive number, got {timeout_seconds!r}."
        )

    retry = _parse_retry(_require(raw, "retry", label), label)
    auth_mode, credential_env = _parse_auth(_require(raw, "auth", label), label)

    region = raw.get("region")
    if region is not None and not isinstance(region, str):
        raise SourceRegistryError(f"{label}: region must be a string if present.")

    # Both optional (default to the raw source_name / an unknown-tier
    # sentinel) so a minimal test fixture or an older sources.yaml entry
    # without these two fields still loads -- the "visible-but-ugly beats
    # silently hidden" fallback report/web_render_v2.py's own docstring
    # already committed to for an unmapped source label.
    display_name = raw.get("display_name", source_name)
    if not isinstance(display_name, str) or not display_name.strip():
        raise SourceRegistryError(f"{label}: display_name must be a non-empty string if present.")

    quality_tier = raw.get("quality_tier")
    if quality_tier is not None and quality_tier not in KNOWN_QUALITY_TIERS:
        raise SourceRegistryError(
            f"{label}: unknown quality_tier {quality_tier!r} (known: {sorted(KNOWN_QUALITY_TIERS)})."
        )

    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise SourceRegistryError(f"{label}: params must be a mapping if present.")

    return SourceConfig(
        source_name=source_name,
        enabled=enabled,
        source_type=source_type,
        category=category,
        region=region,
        endpoint=endpoint,
        timeout_seconds=float(timeout_seconds),
        retry=retry,
        auth_mode=auth_mode,
        display_name=display_name,
        quality_tier=quality_tier,
        credential_env=dict(credential_env),
        params=dict(params),
    )


def load_source_registry(path):
    """Parse and validate a sources.yaml file. Returns a dict keyed by
    source_name -> SourceConfig. Raises SourceRegistryError on any
    malformed entry, unknown enum value, or duplicate source_name."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_doc = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise SourceRegistryError(f"sources.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw_doc, dict) or "sources" not in raw_doc:
        raise SourceRegistryError("sources.yaml must contain a top-level 'sources' list.")

    raw_sources = raw_doc["sources"]
    if not isinstance(raw_sources, list):
        raise SourceRegistryError("'sources' must be a list.")

    registry = {}
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise SourceRegistryError(f"sources[{index}]: each source entry must be a mapping.")
        source_config = _parse_source(raw, index)
        if source_config.source_name in registry:
            raise SourceRegistryError(
                f"duplicate source_name {source_config.source_name!r} in sources.yaml."
            )
        registry[source_config.source_name] = source_config

    return registry

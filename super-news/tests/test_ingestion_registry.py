"""TEST A, B, C from the Phase 2A test matrix: source registry load,
validation, and duplicate-identity rejection."""

import pytest

from ingestion.registry import SourceRegistryError, load_source_registry

VALID_YAML = """
sources:
  - source_name: source_a
    enabled: true
    source_type: rss
    category: AI_NEWS
    region: GLOBAL
    endpoint: https://example.com/feed.xml
    timeout_seconds: 10
    retry:
      max_attempts: 3
      backoff_base_seconds: 1.0
      backoff_jitter_seconds: 0.5
    auth:
      mode: none
"""


def _write(tmp_path, content):
    path = tmp_path / "sources.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---- TEST A: valid registry load -> PASS ------------------------------------


def test_A_valid_registry_load(tmp_path):
    path = _write(tmp_path, VALID_YAML)
    registry = load_source_registry(path)
    assert set(registry.keys()) == {"source_a"}
    cfg = registry["source_a"]
    assert cfg.source_type == "rss"
    assert cfg.category == "AI_NEWS"
    assert cfg.retry.max_attempts == 3
    assert cfg.auth_mode == "none"


def test_real_sources_yaml_loads(tmp_path):
    """The actual project sources.yaml must itself be a valid registry."""
    from pathlib import Path

    real_path = Path(__file__).resolve().parent.parent / "sources.yaml"
    registry = load_source_registry(real_path)
    assert len(registry) >= 1
    for cfg in registry.values():
        assert cfg.source_type in ("rss", "naver_news_api")


# ---- TEST B: invalid registry -> clear config error -------------------------


def test_B_missing_required_field_raises_clear_error(tmp_path):
    broken = VALID_YAML.replace("endpoint: https://example.com/feed.xml", "")
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="endpoint"):
        load_source_registry(path)


def test_B_unknown_source_type_raises(tmp_path):
    broken = VALID_YAML.replace("source_type: rss", "source_type: telegram")
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="source_type"):
        load_source_registry(path)


def test_B_unknown_category_raises(tmp_path):
    broken = VALID_YAML.replace("category: AI_NEWS", "category: SPORTS_NEWS")
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="category"):
        load_source_registry(path)


def test_B_enabled_must_be_boolean(tmp_path):
    broken = VALID_YAML.replace("enabled: true", 'enabled: "yes"')
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="enabled"):
        load_source_registry(path)


def test_B_invalid_timeout_raises(tmp_path):
    broken = VALID_YAML.replace("timeout_seconds: 10", "timeout_seconds: -1")
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="timeout_seconds"):
        load_source_registry(path)


def test_B_invalid_retry_max_attempts_raises(tmp_path):
    broken = VALID_YAML.replace("max_attempts: 3", "max_attempts: 0")
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="max_attempts"):
        load_source_registry(path)


def test_B_api_key_pair_without_credential_env_raises(tmp_path):
    broken = VALID_YAML.replace("auth:\n      mode: none", "auth:\n      mode: api_key_pair")
    path = _write(tmp_path, broken)
    with pytest.raises(SourceRegistryError, match="credential_env"):
        load_source_registry(path)


def test_B_malformed_yaml_raises(tmp_path):
    path = _write(tmp_path, "sources: [this is not valid: yaml: [")
    with pytest.raises(SourceRegistryError):
        load_source_registry(path)


# ---- TEST C: duplicate source identity -> blocked ----------------------------


def test_C_duplicate_source_name_blocked(tmp_path):
    duplicated = VALID_YAML + VALID_YAML.replace("sources:\n", "")
    path = _write(tmp_path, duplicated)
    with pytest.raises(SourceRegistryError, match="duplicate"):
        load_source_registry(path)

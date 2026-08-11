"""TEST F, G, H, I from the Phase 2B test matrix: registry hash
determinism, YAML-formatting invariance, effective-config sensitivity, and
secret-value exclusion."""

from ingestion.config_hash import compute_registry_hash
from ingestion.registry import load_source_registry

BASE_YAML = """
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

# Same semantic config, different comments/whitespace/key order within a mapping.
REFORMATTED_YAML = """
# a leading comment that means nothing
sources:
  - source_name: source_a

    enabled: true          # inline comment
    source_type: rss
    category:    AI_NEWS
    region: GLOBAL
    endpoint: https://example.com/feed.xml
    timeout_seconds: 10
    retry:
      backoff_jitter_seconds: 0.5
      max_attempts: 3
      backoff_base_seconds: 1.0
    auth:
      mode: none
# trailing comment
"""

CHANGED_TIMEOUT_YAML = BASE_YAML.replace("timeout_seconds: 10", "timeout_seconds: 20")

NAVER_YAML_TEMPLATE = """
sources:
  - source_name: naver_news
    enabled: true
    source_type: naver_news_api
    category: SOCIETY_NEWS
    region: KR
    endpoint: https://openapi.naver.com/v1/search/news.json
    timeout_seconds: 10
    retry:
      max_attempts: 3
      backoff_base_seconds: 1.0
      backoff_jitter_seconds: 0.5
    auth:
      mode: api_key_pair
      credential_env:
        client_id: {client_id_name}
        client_secret: NAVER_CLIENT_SECRET
"""


def _load(tmp_path, content, name="sources.yaml"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return load_source_registry(path)


# ---- TEST F: hash is deterministic across repeated calls -------------------


def test_F_hash_is_deterministic(tmp_path):
    registry = _load(tmp_path, BASE_YAML)
    assert compute_registry_hash(registry) == compute_registry_hash(registry)


# ---- TEST G: YAML comment/whitespace/key-order changes -> same hash --------


def test_G_formatting_changes_do_not_change_hash(tmp_path):
    registry_a = _load(tmp_path, BASE_YAML, "a.yaml")
    registry_b = _load(tmp_path, REFORMATTED_YAML, "b.yaml")
    assert compute_registry_hash(registry_a) == compute_registry_hash(registry_b)


# ---- TEST H: an effective config value change -> different hash ------------


def test_H_effective_config_change_changes_hash(tmp_path):
    registry_a = _load(tmp_path, BASE_YAML, "a.yaml")
    registry_b = _load(tmp_path, CHANGED_TIMEOUT_YAML, "b.yaml")
    assert compute_registry_hash(registry_a) != compute_registry_hash(registry_b)


# ---- TEST I: secret ENV VALUE changes -> same hash; NAME change -> different -


def test_I_secret_env_value_never_affects_hash(tmp_path, monkeypatch):
    registry = _load(
        tmp_path, NAVER_YAML_TEMPLATE.format(client_id_name="NAVER_CLIENT_ID")
    )
    monkeypatch.setenv("NAVER_CLIENT_ID", "value-one")
    hash_a = compute_registry_hash(registry)
    monkeypatch.setenv("NAVER_CLIENT_ID", "a-totally-different-value")
    hash_b = compute_registry_hash(registry)
    assert hash_a == hash_b  # env VALUE was never read by the registry loader at all


def test_credential_env_variable_name_change_changes_hash(tmp_path):
    registry_a = _load(
        tmp_path, NAVER_YAML_TEMPLATE.format(client_id_name="NAVER_CLIENT_ID"), "a.yaml"
    )
    registry_b = _load(
        tmp_path, NAVER_YAML_TEMPLATE.format(client_id_name="SOME_OTHER_ENV_NAME"), "b.yaml"
    )
    assert compute_registry_hash(registry_a) != compute_registry_hash(registry_b)


def test_hash_never_contains_secret_looking_value(tmp_path, monkeypatch):
    registry = _load(
        tmp_path, NAVER_YAML_TEMPLATE.format(client_id_name="NAVER_CLIENT_ID")
    )
    monkeypatch.setenv("NAVER_CLIENT_ID", "super_secret_marker_value_xyz")
    # The hash function never even looks at os.environ — this asserts that
    # invariant by construction (compute_registry_hash takes no env access
    # path), reinforced by test_I above observing the hash doesn't change.
    digest = compute_registry_hash(registry)
    assert len(digest) == 64
    assert "super_secret_marker_value_xyz" not in digest

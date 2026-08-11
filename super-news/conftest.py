# Presence of this file at the super-news root makes pytest add this directory
# to sys.path (rootless import mode), so tests can `import config`, `import db.database`,
# `import kakao.auth`, etc. regardless of where pytest is invoked from.

import pytest

import config as config_module
import kakao.token_store as token_store


@pytest.fixture
def kakao_env(monkeypatch, tmp_path):
    """Isolate config/env and the Kakao token store from the real .env and
    the real data/kakao_token.json, for any test that exercises kakao.auth
    (directly or via kakao.client). No network calls happen here — this only
    controls where config reads secrets from and where token_store persists.
    """
    monkeypatch.setattr(config_module, "_dotenv_loaded", False)
    monkeypatch.setattr(config_module, "ENV_PATH", tmp_path / "nonexistent.env")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "test_rest_api_key")
    monkeypatch.setenv("KAKAO_CLIENT_SECRET", "test_client_secret_value")
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "http://localhost:3000/oauth")
    monkeypatch.setattr(token_store, "TOKEN_STORE_PATH", tmp_path / "kakao_token.json")
    yield tmp_path

import pytest

import config as config_module


@pytest.fixture(autouse=True)
def isolated_dotenv(monkeypatch, tmp_path):
    """Never let these tests load the real (eventually secret-bearing) .env,
    and reset the "already loaded" cache so each test controls its own env."""
    monkeypatch.setattr(config_module, "_dotenv_loaded", False)
    monkeypatch.setattr(config_module, "ENV_PATH", tmp_path / "nonexistent.env")
    yield


def test_get_required_env_returns_set_value(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_TEST_VAR", "value123")
    assert config_module.get_required_env("SUPER_NEWS_TEST_VAR") == "value123"


def test_get_required_env_missing_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPER_NEWS_TEST_MISSING_VAR", raising=False)
    with pytest.raises(config_module.MissingSecretError) as exc_info:
        config_module.get_required_env("SUPER_NEWS_TEST_MISSING_VAR")
    message = str(exc_info.value)
    assert "SUPER_NEWS_TEST_MISSING_VAR" in message


def test_get_optional_env_returns_default_when_missing(monkeypatch):
    monkeypatch.delenv("SUPER_NEWS_TEST_OPTIONAL_VAR", raising=False)
    assert (
        config_module.get_optional_env("SUPER_NEWS_TEST_OPTIONAL_VAR", "default_val")
        == "default_val"
    )


def test_get_optional_env_returns_set_value(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_TEST_OPTIONAL_VAR", "actual_val")
    assert (
        config_module.get_optional_env("SUPER_NEWS_TEST_OPTIONAL_VAR", "default_val")
        == "actual_val"
    )

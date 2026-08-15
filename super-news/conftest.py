# Presence of this file at the super-news root makes pytest add this directory
# to sys.path (rootless import mode), so tests can `import config`, `import db.database`,
# `import kakao.auth`, etc. regardless of where pytest is invoked from.

import pytest

import config as config_module
import kakao.token_store as token_store


@pytest.fixture(autouse=True)
def _no_real_anthropic_credential_by_default(monkeypatch):
    """Safe-by-default (Phase 3C): a real ANTHROPIC_API_KEY now genuinely
    exists in this environment's .env (Phase 3B.1 onward). Without this,
    ANY test that exercises report.translation.build_translation_provider()
    or report.llm_interface.build_llm() without its own explicit env
    isolation would silently make a REAL, uncounted network call --
    confirmed to actually happen this session
    (tests/test_cli_generate_daily_web_report_v2.py's "AI headline" fixture
    text was really sent to the real Anthropic API before this fixture
    existed). Deleting just the key is sufficient and minimal: report.
    translation_anthropic.AnthropicTranslationProvider.is_configured() and
    report.llm_anthropic's own get_required_env() both degrade to their
    already-proven-safe "not configured" paths (zero network, zero DB
    writes -- see report/translation.py's module docstring) purely from
    the key's absence, regardless of TRANSLATION_PROVIDER/LLM_PROVIDER's
    own value. A test that deliberately wants to exercise a real-credential
    code path (e.g. tests/test_translation.py's own credential-specific
    tests) sets/deletes the env var itself within that test, which simply
    overrides this default for its own duration -- no conflict.

    Also forces config._dotenv_loaded True (never False) for the SAME
    reason kakao_env repoints ENV_PATH: config.get_optional_env's lazy
    load_dotenv() only fires once per process, and a delenv issued BEFORE
    that first real load is silently undone the moment ANY test in the
    session triggers it -- confirmed to actually happen (a delenv'd
    ANTHROPIC_API_KEY reappeared mid-test the first time this fixture was
    written, in whichever test in the suite happens to read a
    get_optional_env-backed value first). Forcing the flag True up front
    means load_dotenv() never runs during any test at all, so nothing can
    repopulate the key later in the session."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "_dotenv_loaded", True)


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

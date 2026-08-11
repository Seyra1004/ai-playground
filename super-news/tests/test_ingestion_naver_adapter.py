"""TEST X, Y from the Phase 2A test matrix (Naver adapter canonical
records + HTML cleanup), plus TEST Q's Naver-specific angle (401/403
classified as credential failure with no secret leakage) and TEST 37
(security: fake credentials never appear in the raised exception)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ingestion.adapters import naver
from ingestion.http import HttpClientError
from ingestion.registry import RetryPolicy, SourceConfig

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "naver_response.json"


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _naver_source_config():
    return SourceConfig(
        source_name="naver_news",
        enabled=True,
        source_type="naver_news_api",
        category="SOCIETY_NEWS",
        region="KR",
        endpoint="https://openapi.naver.com/v1/search/news.json",
        timeout_seconds=10,
        retry=RetryPolicy(max_attempts=3, backoff_base_seconds=1.0, backoff_jitter_seconds=0.5),
        auth_mode="api_key_pair",
        credential_env={"client_id": "NAVER_CLIENT_ID", "client_secret": "NAVER_CLIENT_SECRET"},
        params={"query": "사회", "display": 10, "sort": "date"},
    )


@pytest.fixture
def naver_env(monkeypatch, tmp_path):
    import config as config_module

    monkeypatch.setattr(config_module, "_dotenv_loaded", False)
    monkeypatch.setattr(config_module, "ENV_PATH", tmp_path / "nonexistent.env")
    monkeypatch.setenv("NAVER_CLIENT_ID", "fake_client_id_value")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "fake_client_secret_value")


# ---- TEST X: Naver response fixture -> canonical ingestion records ---------


def test_X_fixture_produces_canonical_records(naver_env):
    body = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    with patch("ingestion.adapters.naver.request_with_retry", return_value=_FakeResponse(200, body)):
        outcome = naver.fetch_source(_naver_source_config())

    assert len(outcome.records) == 2
    assert outcome.parse_errors == 0

    first = outcome.records[0]
    assert first.source_url == "https://press.example.com/articles/100"  # originallink preferred
    second = outcome.records[1]
    assert second.source_url == "https://news.naver.com/main/read.naver?oid=1&aid=101"  # fallback to link


# ---- TEST Y: Naver HTML cleanup -> payload cleanup level, not full rewrite --


def test_Y_html_entities_and_highlight_tags_cleaned(naver_env):
    body = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    with patch("ingestion.adapters.naver.request_with_retry", return_value=_FakeResponse(200, body)):
        outcome = naver.fetch_source(_naver_source_config())

    first = outcome.records[0]
    assert first.title == 'AI "인공지능" 기술 발전'  # &quot; unescaped, <b> stripped
    assert "<b>" not in first.title and "</b>" not in first.title
    assert first.snippet == "인공지능 관련 & 최신 소식."  # &amp; unescaped, <b> stripped


def test_item_missing_required_fields_counts_as_parse_error(naver_env):
    body = {"items": [{"title": "", "originallink": "", "link": ""}]}
    with patch("ingestion.adapters.naver.request_with_retry", return_value=_FakeResponse(200, body)):
        outcome = naver.fetch_source(_naver_source_config())
    assert outcome.records == []
    assert outcome.parse_errors == 1


# ---- TEST Q (Naver angle) + TEST 37: 401/403 classified, no secret leak ----


def test_naver_401_raises_http_client_error_without_leaking_credentials(naver_env):
    with patch(
        "ingestion.adapters.naver.request_with_retry",
        side_effect=HttpClientError("failed with status 401", status_code=401),
    ):
        with pytest.raises(HttpClientError) as excinfo:
            naver.fetch_source(_naver_source_config())
    assert excinfo.value.status_code == 401
    assert "fake_client_id_value" not in str(excinfo.value)
    assert "fake_client_secret_value" not in str(excinfo.value)

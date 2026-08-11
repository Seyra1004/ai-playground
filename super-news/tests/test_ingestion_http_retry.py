"""TEST M, N, O, P from the Phase 2A test matrix: HTTP retry/backoff
policy behavior. `requests.request` is monkeypatched so no real network
call ever happens and no test actually sleeps (sleep is injected)."""

from unittest.mock import patch

import pytest
import requests

from ingestion.http import HttpClientError, HttpTransientError, request_with_retry
from ingestion.registry import RetryPolicy


def _policy(max_attempts=3, base=1.0, jitter=0.0):
    return RetryPolicy(max_attempts=max_attempts, backoff_base_seconds=base, backoff_jitter_seconds=jitter)


class _FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def _sleep_recorder():
    calls = []

    def sleep(delay):
        calls.append(delay)

    return sleep, calls


# ---- TEST M: HTTP timeout -> bounded retry then FAILED-equivalent error ----


def test_M_timeout_retries_then_raises_transient_after_exhaustion():
    sleep, calls = _sleep_recorder()
    with patch("requests.request", side_effect=requests.Timeout("timed out")) as mock_request:
        with pytest.raises(HttpTransientError):
            request_with_retry("GET", "https://example.com", _policy(max_attempts=3), 10, sleep=sleep)
    assert mock_request.call_count == 3
    assert len(calls) == 2  # slept between attempts 1->2 and 2->3, not after the last


def test_M_timeout_succeeds_after_transient_retries():
    sleep, calls = _sleep_recorder()
    responses = [requests.Timeout("t"), requests.Timeout("t"), _FakeResponse(200)]

    def side_effect(*args, **kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("requests.request", side_effect=side_effect) as mock_request:
        response = request_with_retry("GET", "https://example.com", _policy(max_attempts=3), 10, sleep=sleep)
    assert response.status_code == 200
    assert mock_request.call_count == 3
    assert len(calls) == 2


# ---- TEST N: HTTP 500 -> retry ------------------------------------------------


def test_N_http_500_retries_then_succeeds():
    sleep, calls = _sleep_recorder()
    responses = [_FakeResponse(500), _FakeResponse(200)]

    with patch("requests.request", side_effect=lambda *a, **k: responses.pop(0)) as mock_request:
        response = request_with_retry("GET", "https://example.com", _policy(max_attempts=3), 10, sleep=sleep)
    assert response.status_code == 200
    assert mock_request.call_count == 2
    assert len(calls) == 1


def test_N_http_500_exhausts_retries_and_raises():
    sleep, calls = _sleep_recorder()
    with patch("requests.request", return_value=_FakeResponse(503)) as mock_request:
        with pytest.raises(HttpTransientError):
            request_with_retry("GET", "https://example.com", _policy(max_attempts=2), 10, sleep=sleep)
    assert mock_request.call_count == 2


# ---- TEST O: HTTP 429 -> retry policy applied, Retry-After respected --------


def test_O_http_429_retries_and_respects_retry_after():
    sleep, calls = _sleep_recorder()
    responses = [_FakeResponse(429, headers={"Retry-After": "2.5"}), _FakeResponse(200)]

    with patch("requests.request", side_effect=lambda *a, **k: responses.pop(0)) as mock_request:
        response = request_with_retry("GET", "https://example.com", _policy(max_attempts=3), 10, sleep=sleep)
    assert response.status_code == 200
    assert mock_request.call_count == 2
    assert calls == [2.5]


# ---- TEST P: HTTP 400 -> no unnecessary retry --------------------------------


def test_P_http_400_raises_immediately_without_retry():
    sleep, calls = _sleep_recorder()
    with patch("requests.request", return_value=_FakeResponse(400)) as mock_request:
        with pytest.raises(HttpClientError) as excinfo:
            request_with_retry("GET", "https://example.com", _policy(max_attempts=3), 10, sleep=sleep)
    assert mock_request.call_count == 1
    assert len(calls) == 0
    assert excinfo.value.status_code == 400


def test_401_and_403_also_raise_immediately_without_retry():
    for status in (401, 403):
        sleep, calls = _sleep_recorder()
        with patch("requests.request", return_value=_FakeResponse(status)) as mock_request:
            with pytest.raises(HttpClientError) as excinfo:
                request_with_retry("GET", "https://example.com", _policy(max_attempts=3), 10, sleep=sleep)
        assert mock_request.call_count == 1
        assert excinfo.value.status_code == status


def test_timeout_seconds_always_passed_explicitly():
    with patch("requests.request", return_value=_FakeResponse(200)) as mock_request:
        request_with_retry("GET", "https://example.com", _policy(), 7.5, sleep=lambda d: None)
    assert mock_request.call_args.kwargs["timeout"] == 7.5

"""Bounded HTTP retry policy shared by all ingestion adapters.

Timeout is always explicit. Retry is limited to TRANSIENT failures —
connection errors, timeouts, HTTP 429, HTTP 5xx — up to
`retry_policy.max_attempts` total attempts, with exponential backoff plus
small jitter. Deterministic client failures (4xx other than 429) are never
retried. `sleep` is injectable so tests never actually wait.
"""

import logging
import random
import time

import requests

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class HttpTransientError(RuntimeError):
    """Raised once retries are exhausted for a transient failure
    (connection error, timeout, 429, or 5xx)."""


class HttpClientError(RuntimeError):
    """Raised immediately (no retry) for a deterministic 4xx status other
    than 429. `status_code` lets callers distinguish e.g. 401/403
    (credential/config problems) from 400/404."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def request_with_retry(method, url, retry_policy, timeout_seconds, sleep=None, **kwargs):
    """Issue one logical HTTP request under a bounded retry+backoff policy.

    Returns the `requests.Response` for any status code that isn't a
    transient-failure or deterministic-4xx-error code — callers decide
    success semantics (e.g. Naver's own error envelope) beyond that.
    Raises HttpTransientError if every attempt hit a transient failure, or
    HttpClientError immediately on a non-429 4xx (no retry attempted)."""
    sleep_fn = sleep if sleep is not None else time.sleep
    last_exc = None

    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            response = requests.request(method, url, timeout=timeout_seconds, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "%s %s attempt %d/%d failed: network error (%s)",
                method, url, attempt, retry_policy.max_attempts, type(exc).__name__,
            )
            if attempt >= retry_policy.max_attempts:
                raise HttpTransientError(
                    f"{method} {url} failed after {attempt} attempt(s): "
                    f"network error ({type(exc).__name__})."
                ) from exc
            _wait(retry_policy, attempt, sleep_fn)
            continue

        if response.status_code in TRANSIENT_STATUS_CODES:
            logger.warning(
                "%s %s attempt %d/%d failed: status=%d",
                method, url, attempt, retry_policy.max_attempts, response.status_code,
            )
            if attempt >= retry_policy.max_attempts:
                raise HttpTransientError(
                    f"{method} {url} failed after {attempt} attempt(s): "
                    f"status={response.status_code}."
                )
            retry_after = _parse_retry_after(response) if response.status_code == 429 else None
            _wait(retry_policy, attempt, sleep_fn, retry_after_override=retry_after)
            continue

        if 400 <= response.status_code < 500:
            raise HttpClientError(
                f"{method} {url} failed with status {response.status_code}.",
                status_code=response.status_code,
            )

        return response

    raise HttpTransientError(
        f"{method} {url} failed after {retry_policy.max_attempts} attempt(s)."
    ) from last_exc


def _parse_retry_after(response):
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _wait(retry_policy, attempt, sleep_fn, retry_after_override=None):
    if retry_after_override is not None:
        delay = retry_after_override
    else:
        delay = retry_policy.backoff_base_seconds * (2 ** (attempt - 1))
        delay += random.uniform(0, retry_policy.backoff_jitter_seconds)
    sleep_fn(delay)

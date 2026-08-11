import logging
from unittest.mock import MagicMock, patch

import pytest

import kakao.auth as auth
import kakao.token_store as token_store
import logging_setup


class _ListHandler(logging.Handler):
    """Captures formatted log output in memory — no real log file touched."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


@pytest.fixture
def captured_logs():
    root = logging.getLogger()
    previous_level = root.level
    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(logging_setup.SecretRedactionFilter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.fixture(autouse=True)
def clean_registered_secrets():
    logging_setup._registered_secrets.clear()
    yield
    logging_setup._registered_secrets.clear()


def _mock_response(status_code, body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def test_redaction_filter_redacts_registered_secret():
    logging_setup.register_secret("unit-test-secret-abc")
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="value=%s",
        args=("unit-test-secret-abc",),
        exc_info=None,
    )
    logging_setup.SecretRedactionFilter().filter(record)
    assert "unit-test-secret-abc" not in record.getMessage()
    assert logging_setup._REDACTED in record.getMessage()


def test_redaction_filter_leaves_unrelated_messages_untouched():
    logging_setup.register_secret("some-other-secret")
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="status=%s error=%s",
        args=(200, "invalid_grant"),
        exc_info=None,
    )
    result = logging_setup.SecretRedactionFilter().filter(record)
    assert result is True
    assert record.getMessage() == "status=200 error=invalid_grant"


def test_successful_refresh_flow_never_logs_secrets(kakao_env, captured_logs):
    token_store.save({"access_token": "old_at_value", "refresh_token": "old_rt_value"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200,
            {
                "access_token": "brand_new_access_token_value",
                "refresh_token": "brand_new_refresh_token_value",
                "expires_in": 21599,
            },
        )
        auth.refresh_access_token()

    all_output = "\n".join(captured_logs.records)
    for secret in (
        "old_at_value",
        "old_rt_value",
        "brand_new_access_token_value",
        "brand_new_refresh_token_value",
        "test_client_secret_value",
    ):
        assert secret not in all_output


def test_failed_refresh_never_logs_secrets_or_raw_body(kakao_env, captured_logs):
    token_store.save({"access_token": "old_at_value", "refresh_token": "dead_rt_value"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            400,
            {
                "error": "invalid_grant",
                "error_description": "should never appear in logs either",
            },
        )
        with pytest.raises(auth.ReauthRequiredError):
            auth.refresh_access_token()

    all_output = "\n".join(captured_logs.records)
    assert "dead_rt_value" not in all_output
    assert "test_client_secret_value" not in all_output
    assert "should never appear in logs either" not in all_output
    # The safe, non-secret error enum string IS expected to appear — that's
    # the whole point of only logging status/grant_type/error, not the body.
    assert "invalid_grant" in all_output

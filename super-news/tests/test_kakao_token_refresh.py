from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import kakao.auth as auth
import kakao.token_store as token_store


def _mock_response(status_code, body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def test_refresh_uses_stored_refresh_token_and_persists_new_access_token(kakao_env):
    token_store.save({"access_token": "old_at", "refresh_token": "rt1"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200, {"access_token": "new_at", "expires_in": 21599}
        )
        result = auth.refresh_access_token()

    assert result["access_token"] == "new_at"
    stored = token_store.load()
    assert stored["access_token"] == "new_at"
    assert stored["refresh_token"] == "rt1"  # not rotated by Kakao -> preserved
    assert stored["access_token_expires_at"] is not None


def test_refresh_token_rotation_when_kakao_returns_new_one(kakao_env):
    token_store.save({"access_token": "old_at", "refresh_token": "rt1"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200,
            {
                "access_token": "new_at",
                "refresh_token": "rt2",
                "expires_in": 21599,
                "refresh_token_expires_in": 5183999,
            },
        )
        auth.refresh_access_token()

    stored = token_store.load()
    assert stored["refresh_token"] == "rt2"
    assert stored["refresh_token_expires_at"] is not None


def test_missing_expires_in_stores_unknown_not_inherited(kakao_env):
    token_store.save(
        {
            "access_token": "old_at",
            "refresh_token": "rt1",
            "access_token_expires_at": "2099-01-01T00:00:00+00:00",
        }
    )

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"access_token": "new_at"})
        auth.refresh_access_token()

    stored = token_store.load()
    assert stored["access_token"] == "new_at"
    # Must be explicitly unknown for the NEW token, never the old token's expiry.
    assert stored["access_token_expires_at"] is None


def test_invalid_grant_on_refresh_raises_reauth_required(kakao_env):
    token_store.save({"access_token": "old_at", "refresh_token": "dead_rt"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(400, {"error": "invalid_grant"})
        with pytest.raises(auth.ReauthRequiredError):
            auth.refresh_access_token()


def test_invalid_grant_on_authorization_code_raises_authorization_code_error(kakao_env):
    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(400, {"error": "invalid_grant"})
        with pytest.raises(auth.AuthorizationCodeError):
            auth.exchange_authorization_code("some_expired_code")


def test_authorization_code_error_is_not_reauth_required(kakao_env):
    """An expired/used authorization code must never be misdiagnosed as a
    refresh_token problem — the two exception types must stay distinct."""
    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(400, {"error": "invalid_grant"})
        with pytest.raises(auth.AuthorizationCodeError):
            auth.exchange_authorization_code("some_expired_code")
    assert not issubclass(auth.AuthorizationCodeError, auth.ReauthRequiredError)


def test_no_refresh_token_stored_raises_reauth_required(kakao_env):
    with pytest.raises(auth.ReauthRequiredError):
        auth.refresh_access_token()


def test_non_200_non_invalid_grant_raises_generic_auth_error(kakao_env):
    token_store.save({"access_token": "old_at", "refresh_token": "rt1"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(500, {"error": "server_error"})
        with pytest.raises(auth.KakaoAuthError):
            auth.refresh_access_token()


def test_success_response_missing_access_token_raises(kakao_env):
    token_store.save({"access_token": "old_at", "refresh_token": "rt1"})

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"expires_in": 21599})
        with pytest.raises(auth.KakaoAuthError):
            auth.refresh_access_token()


def test_get_valid_access_token_refreshes_when_expired(kakao_env):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_store.save(
        {"access_token": "old_at", "refresh_token": "rt1", "access_token_expires_at": past}
    )

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200, {"access_token": "refreshed_at", "expires_in": 21599}
        )
        token = auth.get_valid_access_token()

    assert token == "refreshed_at"


def test_get_valid_access_token_reuses_when_still_valid(kakao_env):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_store.save(
        {"access_token": "still_good", "refresh_token": "rt1", "access_token_expires_at": future}
    )

    with patch("kakao.auth.requests.post") as mock_post:
        token = auth.get_valid_access_token()
        mock_post.assert_not_called()

    assert token == "still_good"


def test_get_valid_access_token_refreshes_when_expiry_unknown(kakao_env):
    token_store.save(
        {"access_token": "old_at", "refresh_token": "rt1", "access_token_expires_at": None}
    )

    with patch("kakao.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200, {"access_token": "refreshed_at", "expires_in": 21599}
        )
        token = auth.get_valid_access_token()

    mock_post.assert_called_once()
    assert token == "refreshed_at"

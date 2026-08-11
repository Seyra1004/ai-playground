"""Kakao OAuth: bootstrap exchange, access-token refresh, and expiry tracking.

Token lifecycle (see PHASE 1A plan):
- Every response from Kakao's /oauth/token endpoint is checked for
  expires_in / refresh_token_expires_in; when present they're converted to
  absolute UTC timestamps and stored. When absent, the corresponding expiry
  is explicitly set to unknown (None) for whatever token that response
  actually issued — never inherited from a previous, different token value,
  and never guessed from a hardcoded policy constant.
- access_token and its expiry are ALWAYS overwritten by a token response
  (the new access_token replaces the old one, so its expiry must too — even
  when that means overwriting a known expiry with "unknown").
- refresh_token (and its expiry) are only overwritten when Kakao's response
  actually included a new refresh_token — the existing one (and the expiry
  that describes THAT token) is otherwise left untouched, via
  token_store.merge_and_save's "omitted key = preserved" contract.
- invalid_grant is interpreted differently depending on which grant this
  request was: from a refresh_token grant it means the refresh token itself
  is dead (ReauthRequiredError). From an authorization_code grant it means
  the one-time code was invalid/expired/already used — a completely
  different, non-refresh-related problem (AuthorizationCodeError). Conflating
  the two would misdiagnose an expired bootstrap code as "the whole
  installation needs re-auth."
- Nothing here ever logs client_secret, access_token, refresh_token, the
  authorization code, or a raw response body — only status codes, the grant
  type, and Kakao's short `error` enum string (e.g. "invalid_grant") are
  logged on failure.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests

import logging_setup
from config import (
    ACCESS_TOKEN_REFRESH_MARGIN_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    get_required_env,
)
from kakao import token_store

logger = logging.getLogger(__name__)

TOKEN_URL = "https://kauth.kakao.com/oauth/token"


class KakaoAuthError(RuntimeError):
    """Base class for Kakao auth errors. Never includes secret values or raw
    response bodies in its message."""


class ReauthRequiredError(KakaoAuthError):
    """Raised when Kakao rejects the refresh_token as invalid/expired
    (invalid_grant on a refresh_token grant), or when no refresh_token is
    stored yet. Recovery requires re-running the one-time interactive
    bootstrap (scripts/bootstrap_auth.py)."""


class AuthorizationCodeError(KakaoAuthError):
    """Raised when the one-time authorization_code exchange fails (invalid,
    expired, or already-used code — Kakao codes are single-use and expire in
    minutes). This is NOT a refresh_token problem; it means a fresh
    authorization code is needed and scripts/bootstrap_auth.py must be
    re-run from the start."""


def _client_credentials():
    rest_api_key = get_required_env("KAKAO_REST_API_KEY")
    client_secret = get_required_env("KAKAO_CLIENT_SECRET")
    logging_setup.register_secret(rest_api_key)
    logging_setup.register_secret(client_secret)
    return rest_api_key, client_secret


def _absolute_expiry(seconds):
    if seconds is None:
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _post_token_request(data):
    grant_type = data.get("grant_type")

    try:
        response = requests.post(TOKEN_URL, data=data, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.error("Kakao token request failed: network error (%s)", type(exc).__name__)
        raise KakaoAuthError("Kakao token request failed due to a network error.") from exc

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code != 200:
        error_code = body.get("error") if isinstance(body, dict) else None
        logger.error(
            "Kakao token request failed: grant_type=%s status=%s error=%s",
            grant_type,
            response.status_code,
            error_code,
        )
        if grant_type == "refresh_token" and error_code == "invalid_grant":
            raise ReauthRequiredError(
                "Kakao refresh_token was rejected (invalid_grant) — re-run "
                "scripts/bootstrap_auth.py to re-authenticate."
            )
        if grant_type == "authorization_code":
            raise AuthorizationCodeError(
                "Kakao authorization_code exchange failed "
                f"(status={response.status_code}, error={error_code!r}). "
                "Authorization codes are single-use and expire in minutes — "
                "get a fresh one and re-run scripts/bootstrap_auth.py."
            )
        raise KakaoAuthError(
            f"Kakao token request failed with status {response.status_code} "
            f"(error={error_code!r})."
        )

    if not isinstance(body, dict):
        raise KakaoAuthError(
            "Kakao token response returned status 200 but the body was not a "
            "JSON object."
        )
    return body


def _store_token_response(body):
    access_token = body.get("access_token")
    if not access_token:
        raise KakaoAuthError("Kakao token response did not include an access_token.")

    refresh_token = body.get("refresh_token")  # absent on most refresh responses

    logging_setup.register_secret(access_token)
    if refresh_token:
        logging_setup.register_secret(refresh_token)

    # access_token (and its expiry) always replace the stored values: a new
    # access_token is a different credential than the old one, so an old
    # expiry must never be left attached to it.
    updates = {
        "access_token": access_token,
        "access_token_expires_at": _absolute_expiry(body.get("expires_in")),
    }

    if refresh_token:
        # A NEW refresh_token was issued. Its expiry (if Kakao provided one)
        # describes THIS token, not whatever the old one's expiry was. If
        # Kakao didn't provide an expiry for it, that's explicitly unknown —
        # not the old token's expiry.
        updates["refresh_token"] = refresh_token
        updates["refresh_token_expires_at"] = _absolute_expiry(
            body.get("refresh_token_expires_in")
        )
    # else: refresh_token / refresh_token_expires_at are omitted from
    # `updates` entirely, so token_store.merge_and_save leaves the existing
    # stored refresh_token (and the expiry that describes THAT still-valid
    # token) untouched.

    return token_store.merge_and_save(updates)


def exchange_authorization_code(auth_code):
    """One-time bootstrap: authorization code -> first access/refresh token.
    Called by scripts/bootstrap_auth.py after the user completes the Kakao
    login in a browser. Raises AuthorizationCodeError (not
    ReauthRequiredError) if the code itself is invalid/expired/used."""
    rest_api_key, client_secret = _client_credentials()
    redirect_uri = get_required_env("KAKAO_REDIRECT_URI")
    logging_setup.register_secret(auth_code)

    body = _post_token_request(
        {
            "grant_type": "authorization_code",
            "client_id": rest_api_key,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": auth_code,
        }
    )
    return _store_token_response(body)


def refresh_access_token():
    """Force a refresh using the stored refresh_token. Raises
    ReauthRequiredError if none is stored or Kakao rejects it."""
    stored = token_store.load()
    if not stored or not stored.get("refresh_token"):
        raise ReauthRequiredError(
            "No stored refresh_token — run scripts/bootstrap_auth.py to authenticate."
        )
    refresh_token = stored["refresh_token"]
    logging_setup.register_secret(refresh_token)
    rest_api_key, client_secret = _client_credentials()

    body = _post_token_request(
        {
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
    )
    return _store_token_response(body)


def get_valid_access_token():
    """Return a valid access_token, refreshing first if the stored one is
    expired, near-expiry, or its expiry is unknown. Single entry point
    callers (kakao/client.py) should use — never read token_store directly."""
    stored = token_store.load()
    if not stored or not stored.get("access_token"):
        return refresh_access_token()["access_token"]

    if token_store.is_expired_or_unknown(
        stored.get("access_token_expires_at"), ACCESS_TOKEN_REFRESH_MARGIN_SECONDS
    ):
        return refresh_access_token()["access_token"]

    logging_setup.register_secret(stored["access_token"])
    return stored["access_token"]
